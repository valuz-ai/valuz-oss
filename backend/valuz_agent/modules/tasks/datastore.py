"""Datastores for the Task, TaskEvent, and TaskSession tables.

Owner model: user-facing list/get reads take the caller's ``user_id`` first and
filter on it; writes stamp the owner explicitly. A few methods stay cross-owner
on purpose:

- ``TaskDatastore.list_active`` — startup recovery resumes every owner's active
  tasks (it threads each row's ``user_id`` downstream).
- ``TaskSessionDatastore.get_run`` / ``update_run_by_session`` /
  ``next_sequence`` — keyed on the globally-unique kernel ``session_id`` / run
  id / per-task sequence; used by the runner + kernel-event finalization, not
  user queries.

``append_event`` assigns a monotonic sequence per (project_id, task_id).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow

logger = logging.getLogger(__name__)

_LOCK_RETRY_ATTEMPTS = 12


async def _lock_backoff_sleep(attempt: int) -> None:
    await asyncio.sleep(min(0.05 * (2**attempt), 1.5) + random.uniform(0, 0.05))


class TaskDatastore:
    """CRUD for valuz_task rows."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- Queries --

    async def list_tasks(self, user_id: str, project_id: str) -> list[TaskRow]:
        return list(
            (
                await self._db.execute(
                    select(TaskRow)
                    .where(TaskRow.project_id == project_id, TaskRow.user_id == user_id)
                    .order_by(TaskRow.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def get_task(self, user_id: str, task_id: str) -> TaskRow | None:
        return (
            (
                await self._db.execute(
                    select(TaskRow).where(TaskRow.id == task_id, TaskRow.user_id == user_id)
                )
            )
            .scalars()
            .first()
        )

    async def get_task_by_project(
        self, user_id: str, project_id: str, task_id: str
    ) -> TaskRow | None:
        return (
            (
                await self._db.execute(
                    select(TaskRow).where(
                        TaskRow.project_id == project_id,
                        TaskRow.id == task_id,
                        TaskRow.user_id == user_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def list_all(self, user_id: str, limit: int | None = 50) -> list[TaskRow]:
        """The caller's tasks across all their projects, newest activity first.

        Powers the sidebar TASKS section + activity overview. ``limit=None``
        returns the unbounded set (activity builds a lookup map by task id).
        """
        stmt = select(TaskRow).where(TaskRow.user_id == user_id).order_by(TaskRow.updated_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_active(self) -> list[TaskRow]:
        """SYSTEM SWEEP (cross-owner). All ``active`` tasks across every owner —
        startup recovery (VALUZ-RESUME Layer 1) resumes each under its own owner
        (the caller threads ``row.user_id``)."""
        return list(
            (await self._db.execute(select(TaskRow).filter_by(status="active"))).scalars().all()
        )

    # -- Commands --

    async def create_task(self, user_id: str, row: TaskRow) -> TaskRow:
        row.user_id = user_id
        self._db.add(row)
        await async_commit_with_retry(self._db, where="TaskDatastore.create_task")
        return row

    async def update_task(self, row: TaskRow) -> TaskRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="TaskDatastore.update_task")
        return row

    async def update_task_status(self, user_id: str, task_id: str, status: str) -> bool:
        """Update task status. Returns True when the row was updated."""
        res = await self._db.execute(
            update(TaskRow)
            .where(TaskRow.id == task_id, TaskRow.user_id == user_id)
            .values(status=status, updated_at=now_ms())
        )
        await async_commit_with_retry(self._db, where="TaskDatastore.update_task_status")
        return bool(res.rowcount)


class TaskEventDatastore:
    """Append-only event log for tasks."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- Queries --

    async def list_events(self, user_id: str, project_id: str, task_id: str) -> list[TaskEventRow]:
        return list(
            (
                await self._db.execute(
                    select(TaskEventRow)
                    .where(
                        TaskEventRow.project_id == project_id,
                        TaskEventRow.task_id == task_id,
                        TaskEventRow.user_id == user_id,
                    )
                    .order_by(TaskEventRow.sequence)
                )
            )
            .scalars()
            .all()
        )

    async def list_events_after(
        self,
        user_id: str,
        project_id: str,
        task_id: str,
        after_seq: int,
    ) -> list[TaskEventRow]:
        """Events strictly newer than ``after_seq`` (SSE cursor)."""
        return list(
            (
                await self._db.execute(
                    select(TaskEventRow)
                    .where(
                        TaskEventRow.project_id == project_id,
                        TaskEventRow.task_id == task_id,
                        TaskEventRow.user_id == user_id,
                        TaskEventRow.sequence > after_seq,
                    )
                    .order_by(TaskEventRow.sequence)
                )
            )
            .scalars()
            .all()
        )

    async def get_event(self, user_id: str, event_id: str) -> TaskEventRow | None:
        return (
            (
                await self._db.execute(
                    select(TaskEventRow).where(
                        TaskEventRow.id == event_id, TaskEventRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def latest_event(self, user_id: str, task_id: str) -> TaskEventRow | None:
        """The most recent timeline event for one of the caller's tasks."""
        return (
            await self._db.execute(
                select(TaskEventRow)
                .where(TaskEventRow.task_id == task_id, TaskEventRow.user_id == user_id)
                .order_by(TaskEventRow.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    # -- Commands --

    async def append_event(
        self,
        user_id: str,
        project_id: str,
        task_id: str,
        type: str,
        actor: str,
        session_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TaskEventRow:
        """Append an event with a monotonic sequence per (project_id, task_id).

        Retries on the ``(project_id, task_id, sequence)`` unique-collision (a
        loser re-sequences) and on SQLite ``database is locked``.
        """
        last_exc: Exception | None = None
        for attempt in range(_LOCK_RETRY_ATTEMPTS):
            max_seq = (
                await self._db.execute(
                    select(func.max(TaskEventRow.sequence)).filter_by(
                        project_id=project_id, task_id=task_id
                    )
                )
            ).scalar()
            next_seq = (max_seq or 0) + 1
            row = TaskEventRow(
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
                sequence=next_seq,
                type=type,
                actor=actor,
                session_id=session_id,
                payload=payload or {},
            )
            self._db.add(row)
            try:
                await self._db.commit()
                return row
            except IntegrityError as exc:
                await self._db.rollback()
                last_exc = exc
            except OperationalError as exc:
                await self._db.rollback()
                last_exc = exc
                if "locked" not in str(exc).lower():
                    raise
                logger.warning(
                    "LOCKDIAG[append_event:%s] attempt=%d err=%s",
                    task_id,
                    attempt + 1,
                    str(exc)[:80],
                )
                await _lock_backoff_sleep(attempt)
        raise RuntimeError(
            f"append_event: could not commit event for task {task_id} "
            f"after {_LOCK_RETRY_ATTEMPTS} attempts"
        ) from last_exc


class TaskSessionDatastore:
    """CRUD for valuz_task_session (run index) rows."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- Queries --

    async def list_runs(self, user_id: str, task_id: str) -> list[TaskSessionRow]:
        return list(
            (
                await self._db.execute(
                    select(TaskSessionRow)
                    .where(TaskSessionRow.task_id == task_id, TaskSessionRow.user_id == user_id)
                    .order_by(TaskSessionRow.sequence)
                )
            )
            .scalars()
            .all()
        )

    async def list_all(self, user_id: str) -> list[TaskSessionRow]:
        """The caller's run-index rows across all tasks (activity overview)."""
        return list(
            (
                await self._db.execute(
                    select(TaskSessionRow).where(TaskSessionRow.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )

    async def get_run(self, session_id: str) -> TaskSessionRow | None:
        """SYSTEM lookup by the globally-unique kernel ``session_id`` (runner +
        kernel-event finalization). Not a user query — no owner filter."""
        return (
            (await self._db.execute(select(TaskSessionRow).filter_by(session_id=session_id)))
            .scalars()
            .first()
        )

    async def get_run_by_id(self, user_id: str, run_id: str) -> TaskSessionRow | None:
        return (
            (
                await self._db.execute(
                    select(TaskSessionRow).where(
                        TaskSessionRow.id == run_id, TaskSessionRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def next_sequence(self, task_id: str) -> int:
        """Next run sequence for *task_id* (per-task counter; no owner filter —
        ``task_id`` already scopes it and it returns a number, not rows)."""
        max_seq = (
            await self._db.execute(
                select(func.max(TaskSessionRow.sequence)).filter_by(task_id=task_id)
            )
        ).scalar()
        return (max_seq or 0) + 1

    # -- Commands --

    async def create_run(self, user_id: str, row: TaskSessionRow) -> TaskSessionRow:
        row.user_id = user_id
        self._db.add(row)
        await async_commit_with_retry(self._db, where="TaskSessionDatastore.create_run")
        return row

    async def update_run(self, row: TaskSessionRow) -> TaskSessionRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="TaskSessionDatastore.update_run")
        return row

    async def update_run_by_session(
        self,
        session_id: str,
        status: str,
        result_manifest: dict[str, Any] | None = None,
        ended_at: int | None = None,
    ) -> bool:
        """SYSTEM update by the globally-unique kernel ``session_id`` (kernel-
        event finalization path); no owner filter."""
        updates: dict[str, Any] = {"status": status}
        if result_manifest is not None:
            updates["result_manifest"] = result_manifest
        if ended_at is not None:
            updates["ended_at"] = ended_at

        res = await self._db.execute(
            update(TaskSessionRow).where(TaskSessionRow.session_id == session_id).values(**updates)
        )
        await async_commit_with_retry(self._db, where="TaskSessionDatastore.update_run_by_session")
        return bool(res.rowcount)

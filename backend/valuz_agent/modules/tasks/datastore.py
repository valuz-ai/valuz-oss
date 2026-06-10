"""Datastores for the Task, TaskEvent, and TaskSession tables.

Naming mirrors modules/schedules/datastore.py:
  list_*  → returns list
  get_*   → returns Optional[Row]
  create_*→ adds + commits, returns Row
  update_*→ merge + commit, returns Row

append_event() assigns a monotonic sequence per (project_id, task_id)
by selecting MAX(sequence) + 1 within the task scope.
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

# Lock-contention retry budget. Under real parallel dispatch the host competes
# with the kernel's high-frequency event-stream writes for SQLite's single
# write slot. Exponential backoff + jitter over more attempts widens the window
# AND de-correlates competing host writers so they stop losing in lock-step.
_LOCK_RETRY_ATTEMPTS = 12


async def _lock_backoff_sleep(attempt: int) -> None:
    await asyncio.sleep(min(0.05 * (2**attempt), 1.5) + random.uniform(0, 0.05))


class TaskDatastore:
    """CRUD for valuz_task rows."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- Queries --

    async def list_tasks(self, project_id: str) -> list[TaskRow]:
        return list(
            (
                await self._db.execute(
                    select(TaskRow)
                    .filter_by(project_id=project_id)
                    .order_by(TaskRow.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def get_task(self, task_id: str) -> TaskRow | None:
        return await self._db.get(TaskRow, task_id)

    async def get_task_by_project(self, project_id: str, task_id: str) -> TaskRow | None:
        return (
            (
                await self._db.execute(
                    select(TaskRow).filter_by(project_id=project_id, id=task_id)
                )
            )
            .scalars()
            .first()
        )

    async def list_all(self, limit: int | None = 50) -> list[TaskRow]:
        """All tasks across every project, newest activity first.

        Powers the sidebar TASKS section: a global, cross-project view of
        "what's running / recently touched". ``limit`` matches the recents
        rail cap (50) — older tasks stay reachable via per-project tabs.
        Pass ``limit=None`` for the unbounded set (e.g. the activity overview,
        which builds a lookup map keyed by task id and must resolve any task a
        live session references).
        """
        stmt = select(TaskRow).order_by(TaskRow.updated_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_active(self) -> list[TaskRow]:
        """All ``active`` tasks across every project (VALUZ-RESUME Layer 1).

        Startup recovery only resumes ``active`` tasks — ``paused``/``stopped``
        are intentional user stops, terminal states need no resume.
        """
        return list(
            (await self._db.execute(select(TaskRow).filter_by(status="active"))).scalars().all()
        )

    # -- Commands --

    async def create_task(self, row: TaskRow) -> TaskRow:
        self._db.add(row)
        await async_commit_with_retry(self._db, where="TaskDatastore.create_task")
        return row

    async def update_task(self, row: TaskRow) -> TaskRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="TaskDatastore.update_task")
        return row

    async def update_task_status(
        self,
        task_id: str,
        status: str,
    ) -> bool:
        """Update task status. Returns True when the row was updated."""
        res = await self._db.execute(
            update(TaskRow).where(TaskRow.id == task_id).values(status=status, updated_at=now_ms())
        )
        await async_commit_with_retry(self._db, where="TaskDatastore.update_task_status")
        return bool(res.rowcount)


class TaskEventDatastore:
    """Append-only event log for tasks."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- Queries --

    async def list_events(self, project_id: str, task_id: str) -> list[TaskEventRow]:
        return list(
            (
                await self._db.execute(
                    select(TaskEventRow)
                    .filter_by(project_id=project_id, task_id=task_id)
                    .order_by(TaskEventRow.sequence)
                )
            )
            .scalars()
            .all()
        )

    async def list_events_after(
        self,
        project_id: str,
        task_id: str,
        after_seq: int,
    ) -> list[TaskEventRow]:
        """Return events strictly newer than ``after_seq``, ordered ascending.

        Backing query for the ``GET /v1/tasks/{id}/events/stream`` SSE
        endpoint — the iterator polls this on a tick and emits any
        newly-arrived rows. Cursoring on ``sequence`` (monotonic per task)
        is exact, no time-window gaps.
        """
        return list(
            (
                await self._db.execute(
                    select(TaskEventRow)
                    .filter_by(project_id=project_id, task_id=task_id)
                    .where(TaskEventRow.sequence > after_seq)
                    .order_by(TaskEventRow.sequence)
                )
            )
            .scalars()
            .all()
        )

    async def get_event(self, event_id: str) -> TaskEventRow | None:
        return await self._db.get(TaskEventRow, event_id)

    async def latest_event(self, task_id: str) -> TaskEventRow | None:
        """The most recent timeline event for a task (by sequence), across any
        project. Powers the activity overview's per-task "last event" preview;
        keyed on ``task_id`` alone since the caller has only that.
        """
        return (
            await self._db.execute(
                select(TaskEventRow)
                .where(TaskEventRow.task_id == task_id)
                .order_by(TaskEventRow.sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    # -- Commands --

    async def append_event(
        self,
        project_id: str,
        task_id: str,
        type: str,
        actor: str,
        session_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TaskEventRow:
        """Append an event with a monotonic sequence number per (project_id, task_id).

        The sequence is MAX(sequence)+1 within the task scope. This read-then-
        write is NOT race-free: concurrent appends (e.g. a v2 lead firing two
        ``dispatch_async`` calls in one turn) can compute the same next_seq and
        collide on the ``(project_id, task_id, sequence)`` unique constraint.
        We retry on that collision, recomputing the sequence each time, so a
        loser re-sequences instead of failing (which previously orphaned the
        caller's half-written row, e.g. a spawned run with no spawn event).

        We ALSO retry on ``OperationalError: database is locked`` with a short
        backoff. SQLite serializes writers; the sync host engine and the async
        kernel engine share the file, and under concurrent dispatch a writer
        can still time out past ``busy_timeout``.
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
                # Sequence collision — recompute MAX(seq)+1 and retry.
                await self._db.rollback()
                last_exc = exc
            except OperationalError as exc:
                # Write contention ("database is locked") — back off and retry.
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

    async def list_runs(self, task_id: str) -> list[TaskSessionRow]:
        return list(
            (
                await self._db.execute(
                    select(TaskSessionRow)
                    .filter_by(task_id=task_id)
                    .order_by(TaskSessionRow.sequence)
                )
            )
            .scalars()
            .all()
        )

    async def list_all(self) -> list[TaskSessionRow]:
        """Every run index row across all tasks.

        Powers the activity overview, which keys runs by kernel ``session_id``
        to classify each live session (lead vs. subtask) without a per-task
        query.
        """
        return list((await self._db.execute(select(TaskSessionRow))).scalars().all())

    async def get_run(self, session_id: str) -> TaskSessionRow | None:
        """Look up a run by its kernel session id."""
        return (
            (await self._db.execute(select(TaskSessionRow).filter_by(session_id=session_id)))
            .scalars()
            .first()
        )

    async def get_run_by_id(self, run_id: str) -> TaskSessionRow | None:
        return await self._db.get(TaskSessionRow, run_id)

    async def next_sequence(self, task_id: str) -> int:
        """Return the next run sequence number for *task_id*."""
        max_seq = (
            await self._db.execute(
                select(func.max(TaskSessionRow.sequence)).filter_by(task_id=task_id)
            )
        ).scalar()
        return (max_seq or 0) + 1

    # -- Commands --

    async def create_run(self, row: TaskSessionRow) -> TaskSessionRow:
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
        """Update run status and optional manifest by kernel session id."""
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

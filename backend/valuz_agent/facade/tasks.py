"""Owner-scoped task commands for hosts and editions.

Task execution remains project-owned. This facade gives a caller a cross-project
view without exposing ORM rows or reimplementing lifecycle and recovery rules.
Each operation opens its own transaction and requires an explicit owner.
"""

from __future__ import annotations

import base64
import builtins
import json
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import and_, or_, select

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow
from valuz_agent.modules.tasks.service import TaskService
from valuz_agent.modules.tasks.task_state import TASK_STATUSES


@dataclass(frozen=True, slots=True)
class TaskRef:
    id: str
    project_id: str
    title: str
    goal: str
    status: str
    lead_agent_slug: str
    created_at: int
    updated_at: int
    originating_session_id: str | None
    trigger_type: str
    trigger_task_id: str | None
    trigger_automation_id: str | None


@dataclass(frozen=True, slots=True)
class TaskRunRef:
    session_id: str
    agent_slug: str
    kind: str
    status: str
    result_manifest: dict[str, Any] | None
    ended_at: int | None


@dataclass(frozen=True, slots=True)
class TaskDetailRef:
    task: TaskRef
    runs: tuple[TaskRunRef, ...]
    latest_summary: str
    event_sequence: int
    latest_attention: dict[str, Any] | None = None
    finalized_at: datetime | None = None  # Canonical terminal event time, never updated_at.


@dataclass(frozen=True, slots=True)
class TaskEventRef:
    id: str
    sequence: int
    type: str
    created_at: datetime  # ISO UTC on the wire, converted from host epoch-millisecond events.
    payload: dict[str, Any]
    session_id: str | None


@dataclass(frozen=True, slots=True)
class TaskEventPage:
    owner_user_id: str
    task_id: str
    items: tuple[TaskEventRef, ...]
    next_cursor: int | None


@dataclass(frozen=True, slots=True)
class TaskPage:
    items: tuple[TaskRef, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TaskCommandResult:
    ok: bool
    task: TaskRef | None = None
    delivered: bool | None = None
    reason: str | None = None
    lead_session_id: str | None = None


def _identity(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _ref(row: TaskRow) -> TaskRef:
    return TaskRef(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        goal=row.goal,
        status=row.status,
        lead_agent_slug=row.lead_agent_slug,
        created_at=row.created_at,
        updated_at=row.updated_at,
        originating_session_id=(row.metadata_ or {}).get("originating_session_id"),
        trigger_type=row.trigger_type,
        trigger_task_id=row.trigger_task_id,
        trigger_automation_id=row.trigger_automation_id,
    )


def _finalized_at(status: str, events: Sequence[TaskEventRow]) -> datetime | None:
    event_types = {
        "completed": {"task_completed"},
        "stopped": {"task_stopped", "stopped"},
        "abandoned": {"task_abandoned", "abandoned"},
    }.get(status, set())
    matching = [event for event in events if event.type in event_types]
    if not matching:
        return None
    last = max(matching, key=lambda event: event.sequence)
    return datetime.fromtimestamp(last.created_at / 1000, UTC)


def _cursor(row: TaskRow) -> str:
    return base64.urlsafe_b64encode(json.dumps([row.updated_at, row.id]).encode()).decode()


def _decode_cursor(cursor: str) -> tuple[int, str]:
    try:
        timestamp, task_id = json.loads(base64.urlsafe_b64decode(cursor))
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise ValueError
        _identity(task_id, "cursor task id")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("invalid task cursor") from exc
    return timestamp, task_id


async def _require_source(user_id: str, session_id: str) -> None:
    from valuz_agent.adapters.data_reader import data_reader

    _identity(session_id, "source session id")
    if await data_reader().get_session(user_id, session_id) is None:
        raise ValueError("source session not found")


class TaskLibrary:
    """A public task API with owner checks and the canonical project rules."""

    async def list(
        self,
        user_id: str,
        *,
        project_id: str | None = None,
        originating_session_id: str | None = None,
        statuses: tuple[str, ...] | list[str] | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> TaskPage:
        """Newest activity first, with a tie-safe (updated_at, id) cursor.

        Filter terminal/halted statuses and an origin to reconcile missed result
        notifications. A sweep should restart from the first page periodically:
        rows updated while paging can move ahead of its current cursor.
        """
        _identity(user_id, "user_id")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if statuses is not None and any(s not in (*TASK_STATUSES, "failed") for s in statuses):
            raise ValueError("invalid task status")
        stmt = select(TaskRow).where(TaskRow.user_id == user_id)
        if project_id is not None:
            _identity(project_id, "project_id")
            stmt = stmt.where(TaskRow.project_id == project_id)
        if originating_session_id is not None:
            _identity(originating_session_id, "originating_session_id")
            stmt = stmt.where(
                TaskRow.metadata_["originating_session_id"].as_string() == originating_session_id
            )
        if statuses is not None:
            stmt = stmt.where(TaskRow.status.in_(statuses))
        if cursor is not None:
            timestamp, task_id = _decode_cursor(cursor)
            stmt = stmt.where(
                or_(
                    TaskRow.updated_at < timestamp,
                    and_(TaskRow.updated_at == timestamp, TaskRow.id < task_id),
                )
            )
        async with async_unit_of_work(commit=False) as db:
            rows = list(
                (
                    await db.execute(
                        stmt.order_by(TaskRow.updated_at.desc(), TaskRow.id.desc()).limit(limit + 1)
                    )
                )
                .scalars()
                .all()
            )
            selected = rows[:limit]
            return TaskPage(
                items=tuple(_ref(row) for row in selected),
                next_cursor=_cursor(selected[-1]) if len(rows) > limit else None,
            )

    async def get(self, user_id: str, task_id: str) -> TaskDetailRef | None:
        _identity(user_id, "user_id")
        _identity(task_id, "task_id")
        async with async_unit_of_work(commit=False) as db:
            detail = await TaskService(db).get_detail(user_id, task_id)
            if detail is None:
                return None
            summaries = [
                str(e.payload["summary"]) for e in detail.events if e.payload.get("summary")
            ]
            attention = [
                e
                for e in detail.events
                if e.type in {"task_blocked", "blocked", "paused", "task_paused"}
            ]
            latest_attention = None
            if detail.task.status in {"blocked", "paused"} and attention:
                latest = max(attention, key=lambda e: e.sequence)
                latest_attention = {
                    "event_type": latest.type,
                    "sequence": latest.sequence,
                    "created_at": datetime.fromtimestamp(latest.created_at / 1000, UTC).isoformat(),
                    **{
                        key: deepcopy(latest.payload[key])
                        for key in ("reason", "category", "error", "summary")
                        if key in latest.payload
                    },
                }
            return TaskDetailRef(
                task=_ref(detail.task),
                runs=tuple(
                    TaskRunRef(
                        session_id=r.session_id,
                        agent_slug=r.agent_slug,
                        kind=r.kind,
                        status=r.status,
                        result_manifest=deepcopy(r.result_manifest),
                        ended_at=r.ended_at,
                    )
                    for r in detail.runs
                ),
                latest_summary=summaries[-1] if summaries else "",
                event_sequence=max((e.sequence for e in detail.events), default=0),
                latest_attention=latest_attention,
                finalized_at=_finalized_at(detail.task.status, detail.events),
            )

    async def event_page(
        self, user_id: str, task_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> TaskEventPage | None:
        """Exact append-only timeline pages, including superseded plan changes."""
        _identity(user_id, "user_id")
        _identity(task_id, "task_id")
        if isinstance(after_seq, bool) or after_seq < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid task event page")
        async with async_unit_of_work(commit=False) as db:
            service = TaskService(db)
            task = await service.get_owned_task(user_id, task_id)
            if task is None:
                return None
            events = await service.events_after(user_id, task.project_id, task_id, after_seq)
            selected = events[:limit]
            return TaskEventPage(
                owner_user_id=user_id,
                task_id=task_id,
                items=tuple(
                    TaskEventRef(
                        id=event.id,
                        sequence=event.sequence,
                        type=event.type,
                        created_at=datetime.fromtimestamp(event.created_at / 1000, UTC),
                        payload=deepcopy(event.payload),
                        session_id=event.session_id,
                    )
                    for event in selected
                ),
                next_cursor=selected[-1].sequence if len(events) > limit else None,
            )

    async def create(
        self,
        user_id: str,
        *,
        project_id: str,
        goal: str,
        lead_agent_slug: str,
        originating_session_id: str | None = None,
        title: str | None = None,
        refs: builtins.list[str] | None = None,
        worktree: bool = False,
    ) -> TaskRef:
        """Kick off in the target project, preserving its roster and execution rules.

        A source chat need not belong to the target project, but it must belong
        to this owner. Hidden chat projects are not task execution containers.
        Returned status is the actual accepted header, not proof of completion.
        """
        from valuz_agent.modules.tasks.orchestrator import task_orchestrator

        for value, name in (
            (user_id, "user_id"),
            (project_id, "project_id"),
            (goal, "goal"),
            (lead_agent_slug, "lead_agent_slug"),
        ):
            _identity(value, name)
        if originating_session_id is not None:
            await _require_source(user_id, originating_session_id)
        async with async_unit_of_work(commit=False) as db:
            project = await ProjectDatastore(db).get_by_id(user_id, project_id)
            if project is None:
                raise ValueError("project not found")
            if project.kind != "project":
                raise ValueError("tasks require a normal project")
        row = await task_orchestrator.lifecycle.kickoff(
            project_id=project_id,
            goal=goal,
            lead_agent_slug=lead_agent_slug,
            user_id=user_id,
            originating_session_id=originating_session_id,
            title=title,
            refs=refs,
            worktree=worktree,
            created_by="agent" if originating_session_id else "user",
        )
        return _ref(row)

    async def intervene(
        self,
        user_id: str,
        task_id: str,
        *,
        action: Literal["note", "revise_goal", "pause", "resume", "stop"],
        text: str | None = None,
        goal: str | None = None,
    ) -> TaskCommandResult:
        """Control an owned task. Resume can carry a new continuation instruction.

        Goal revision requires an active lead and is atomic with its durable
        mailbox delivery. A rejected operation never claims successful control.
        """
        from valuz_agent.modules.tasks.orchestrator import task_orchestrator

        detail = await self.get(user_id, task_id)
        if detail is None:
            return TaskCommandResult(False, reason="TASK_NOT_FOUND")
        task = detail.task
        if action in {"note", "revise_goal"}:
            _identity((goal if action == "revise_goal" else text) or "", action)
            async with async_unit_of_work() as db:
                service = TaskService(db)
                row = await service.get_owned_task(user_id, task_id)
                if row is None:
                    return TaskCommandResult(False, reason="TASK_NOT_FOUND")
                if action == "revise_goal":
                    if row.status != "active":
                        return TaskCommandResult(False, _ref(row), False, "TASK_NOT_ACTIVE")
                    if not await service.revise_goal(user_id, row, goal or ""):
                        await db.rollback()
                        return TaskCommandResult(False, task, False, "NO_LEAD")
                else:
                    await service.add_note(user_id, row, text or "")
        elif action in {"pause", "stop"}:
            applied = await task_orchestrator.recovery.stop_task(
                task_id,
                task.project_id,
                user_id=user_id,
                target_status="paused" if action == "pause" else "stopped",
            )
            if not applied:
                return TaskCommandResult(False, task, reason="INVALID_TASK_STATE")
        elif action == "resume":
            result = await task_orchestrator.recovery.resume_task(
                task_id,
                task.project_id,
                user_id=user_id,
                instruction=text,
            )
            if not result.get("ok"):
                current = await self.get(user_id, task_id)
                return TaskCommandResult(
                    False,
                    current.task if current else None,
                    reason=result.get("error") or "RESUME_FAILED",
                )
        else:
            raise ValueError("invalid intervention action")
        current = await self.get(user_id, task_id)
        return TaskCommandResult(
            current is not None,
            current.task if current else None,
            delivered=True if action == "revise_goal" else None,
            reason=None if current else "TASK_NOT_FOUND",
        )

    async def inject(
        self,
        user_id: str,
        task_id: str,
        *,
        text: str,
        from_session_id: str | None = None,
    ) -> TaskCommandResult:
        """Send an owned instruction, using the host's halted-task revival policy.

        An explicit session must be real and owned. ``None`` is a command from
        the verified owner without a local chat (e.g. an HTTP host controller),
        attributed to ``user`` rather than a forged cross-host session id.
        """
        from valuz_agent.modules.tasks.orchestrator import task_orchestrator

        _identity(user_id, "user_id")
        _identity(text, "text")
        if from_session_id is not None:
            await _require_source(user_id, from_session_id)
        detail = await self.get(user_id, task_id)
        if detail is None:
            return TaskCommandResult(False, reason="TASK_NOT_FOUND", delivered=False)
        result = await task_orchestrator.recovery.inject_or_revive(
            task_id=task_id,
            project_id=detail.task.project_id,
            text=text,
            from_session_id=from_session_id or "user",
            user_id=user_id,
        )
        current = await self.get(user_id, task_id)
        delivered = bool(result.get("delivered"))
        return TaskCommandResult(
            delivered,
            current.task if current else None,
            delivered,
            result.get("reason"),
            result.get("lead_session_id"),
        )


__all__ = [
    "TaskCommandResult",
    "TaskDetailRef",
    "TaskEventPage",
    "TaskEventRef",
    "TaskLibrary",
    "TaskPage",
    "TaskRef",
    "TaskRunRef",
]

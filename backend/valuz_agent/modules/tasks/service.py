"""TaskService — the HTTP layer's service tier (routes never touch datastores).

Owned reads (incl. the composite detail/timeline reads) + the two intervention
writes (``add_note`` / ``revise_goal`` — the latter must reach a RUNNING lead,
not just the row). Ownership is a parameter, never ambient; methods return
``None`` instead of raising HTTP errors so non-HTTP callers can reuse them.

NOT here: lifecycle orchestration (``task_orchestrator`` services) and plan
writes (``plan_commands``). Agent-facing MCP reads ARE here — the
module-level functions below return loose dicts on their own UoW.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import valuz_agent.boot.kernel  # noqa: F401 — puts kernel on sys.path
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.plan import TaskPlan
from valuz_agent.modules.tasks.resolution import task_session_resolver


@dataclass(frozen=True)
class TaskDetail:
    """A task plus everything the detail page renders in one round trip."""

    task: TaskRow
    runs: list[TaskSessionRow]
    events: list[TaskEventRow]


@dataclass(frozen=True)
class TaskEvents:
    """A task's events plus the task itself.

    The task comes back because the caller needs its ``project_id`` to scope
    the event read — returning it avoids the "load it twice" shape the routes
    had.
    """

    task: TaskRow
    events: list[TaskEventRow]


class TaskService:
    """Owner-scoped task reads + the small intervention writes."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._tasks = TaskDatastore(db)
        self._runs = TaskSessionDatastore(db)
        self._events = TaskEventDatastore(db)

    # -- single task ---------------------------------------------------

    async def get_owned_task(self, user_id: str, task_id: str) -> TaskRow | None:
        """The caller's task, or ``None``.

        The single most repeated read in the route layer — it precedes almost
        every task mutation as the ownership + existence check.
        """
        return await self._tasks.get_task(user_id, task_id)

    async def get_detail(self, user_id: str, task_id: str) -> TaskDetail | None:
        """Task + runs + timeline for the detail page. ``None`` if not owned."""
        task = await self._tasks.get_task(user_id, task_id)
        if task is None:
            return None
        return TaskDetail(
            task=task,
            runs=await self._runs.list_runs(user_id, task_id),
            events=await self._events.list_events(user_id, task.project_id, task_id),
        )

    async def get_events(self, user_id: str, task_id: str) -> TaskEvents | None:
        """A task's full timeline. ``None`` if the task isn't the caller's."""
        task = await self._tasks.get_task(user_id, task_id)
        if task is None:
            return None
        return TaskEvents(
            task=task,
            events=await self._events.list_events(user_id, task.project_id, task_id),
        )

    async def events_after(
        self, user_id: str, project_id: str, task_id: str, after_seq: int
    ) -> list[TaskEventRow]:
        """Timeline tail strictly newer than ``after_seq`` (the SSE cursor)."""
        return await self._events.list_events_after(user_id, project_id, task_id, after_seq)

    # -- lists ---------------------------------------------------------

    async def list_for_project(self, user_id: str, project_id: str) -> list[TaskRow]:
        return await self._tasks.list_tasks(user_id, project_id)

    async def list_all(self, user_id: str, *, limit: int = 50) -> list[TaskRow]:
        """Cross-project list, newest activity first (sidebar TASKS section)."""
        return await self._tasks.list_all(user_id, limit=limit)

    async def titles_by_ids(self, user_id: str, task_ids: list[str]) -> dict[str, str]:
        """id → title, for labelling trigger provenance without N+1 lookups."""
        return await self._tasks.get_titles_by_ids(user_id, task_ids)

    # -- intervention writes -------------------------------------------

    async def add_note(self, user_id: str, task: TaskRow, text: str) -> None:
        """Append a user note. Does not interrupt the lead."""
        await self._events.append_event(
            user_id,
            task.project_id,
            task.id,
            "user_note",
            actor="user",
            payload={"text": text},
        )

    async def revise_goal(self, user_id: str, task: TaskRow, goal: str) -> bool:
        """Update ``task.goal`` AND push the revision to a running lead.

        Both halves matter: the goal is baked into the lead session at spawn as
        its brief and its goal-mode loop condition, so a bare row update is
        pull-only — a running lead never re-reads it and would keep working
        toward the old goal. The revision is queued for the lead in the SAME
        transaction as the row update, so the two cannot disagree; it reaches
        the lead at its next turn boundary from whichever host process drives
        it. Previously delivery went through an in-process queue and was simply
        lost whenever this request landed elsewhere.

        Returns whether the task has a lead to receive it.
        """
        from pydantic import ValidationError

        from valuz_agent.modules.sessions.task_checks import CONFIG_KEY, fresh_config
        from valuz_agent.modules.tasks import messaging
        from valuz_agent.ports.capability_policy import TaskCheckConfig

        task.goal = goal
        # A revised goal is new intent, not a continuation of a layout-only
        # exemption. Keep execution lineage on TaskRow; only the optional-check
        # revision changes so every lead/member converges at its next boundary.
        try:
            previous_checks = TaskCheckConfig.model_validate(
                (task.metadata_ or {}).get(CONFIG_KEY) or {}
            )
        except ValidationError:
            previous_checks = TaskCheckConfig()
        task.metadata_ = {
            **(task.metadata_ or {}),
            CONFIG_KEY: fresh_config(TaskCheckConfig(
                origin="task", operation="task.execute", run_id=previous_checks.run_id or task.id,
            )).model_dump(mode="json"),
        }
        await self._tasks.update_task(task)
        # One transaction: the row, the timeline entry and the lead's copy of
        # the revision. Splitting them is how a task ends up looking redirected
        # while its lead still pursues the old objective.
        notified = await messaging.notify_lead_goal_revised(
            self._db,
            task_id=task.id,
            project_id=task.project_id,
            new_goal=goal,
            user_id=user_id,
        )
        delivered = bool(notified["delivered"])
        await self._events.append_event(
            user_id,
            task.project_id,
            task.id,
            "goal_revised",
            actor="user",
            payload={"goal": goal, "delivered_to_lead": delivered},
        )
        return delivered


# ---------------------------------------------------------------------------
# Agent-facing reads (the MCP tools + cross-module consumers).
#
# Module-level functions, each opening its OWN read-only unit of work — the
# tool context has no request-scoped session to join, unlike the TaskService
# class above, which runs on the caller's ``db`` and feeds Pydantic models.
# They return the loose dict summaries the tools expect.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskActivitySummary:
    id: str
    updated_at: int
    project_id: str
    trigger_automation_id: str | None
    title: str
    status: str


async def list_activity_tasks_page(
    user_id: str,
    *,
    project_id: str | None = None,
    before_ts: int | None = None,
    automation: bool | None = None,
    limit: int = 20,
) -> list[TaskActivitySummary]:
    """Return task rows projected for the unified activity feed."""
    async with async_unit_of_work(commit=False) as db:
        rows = await TaskDatastore(db).list_tasks_page(
            user_id,
            project_id=project_id,
            before_ts=before_ts,
            automation=automation,
            limit=limit,
        )
    return [
        TaskActivitySummary(
            id=row.id,
            updated_at=row.updated_at,
            project_id=row.project_id,
            trigger_automation_id=row.trigger_automation_id,
            title=row.title,
            status=row.status,
        )
        for row in rows
    ]


async def get_task_with_runs(user_id: str, task_id: str) -> tuple[Any | None, list[Any]]:
    """Return a task and its runs for cross-module read-only consumers."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(user_id, task_id)
        runs = await TaskSessionDatastore(db).list_runs(user_id, task_id) if task else []
    return task, runs


async def list_members(project_id: str, user_id: str) -> list[dict[str, Any]]:
    """Return member descriptors for dispatch tool list_members().

    Host membership knowledge lives behind the resolver seam
    (tasks/resolution.py) — this stays a thin read wrapper.
    """
    async with async_unit_of_work(commit=False) as db:
        return await task_session_resolver.list_member_descriptors(
            db, user_id=user_id, project_id=project_id
        )


async def list_tasks(
    project_id: str,
    *,
    status: str | None = None,
    mine_session_id: str | None = None,
    limit: int = 20,
    user_id: str,
) -> list[dict[str, Any]]:
    """Return task summaries for *project_id* (newest first).

    ``status`` filters by task status (active/completed/failed). When
    ``mine_session_id`` is given, only tasks launched by that conversation
    session (``metadata.originating_session_id``) are returned. Each item
    carries run counts so the caller can gauge progress without a second
    call.
    """
    async with async_unit_of_work(commit=False) as db:
        task_ds = TaskDatastore(db)
        run_ds = TaskSessionDatastore(db)
        rows = await task_ds.list_tasks(user_id, project_id)

        # Filter + cap FIRST, then fetch run counts for exactly the tasks we
        # are going to render — one grouped query instead of a ``list_runs``
        # per task inside the loop (which also materialised every run row just
        # to count it). ``count_runs_by_tasks`` returns (total, settled), where
        # settled = completed | rejected | archived: an errored or
        # user-stopped run is finished work, and omitting them made the
        # progress reported back to the agent read low forever.
        selected: list[Any] = []
        for row in rows:
            if status and row.status != status:
                continue
            originated_by = (row.metadata_ or {}).get("originating_session_id")
            if mine_session_id and originated_by != mine_session_id:
                continue
            selected.append(row)
            if len(selected) >= limit:
                break

        counts = await run_ds.count_runs_by_tasks(user_id, [r.id for r in selected])
        result: list[dict[str, Any]] = []
        for row in selected:
            meta = row.metadata_ or {}
            originated_by = meta.get("originating_session_id")
            total, done = counts.get(row.id, (0, 0))
            result.append(
                {
                    "task_id": row.id,
                    "title": row.title,
                    "status": row.status,
                    "lead_agent": row.lead_agent_slug,
                    "dispatch_mode": meta.get("dispatch_mode"),
                    "created_at": str(row.created_at) if row.created_at else None,
                    "runs": total,
                    "runs_done": done,
                    "originated_by_me": (
                        bool(mine_session_id) and originated_by == mine_session_id
                    ),
                }
            )
        return result


async def get_task(
    task_id: str,
    project_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Return one task's status + per-run states + latest summary.

    Scoped to *project_id* (cross-project lookups return ``None``).
    ``latest_summary`` is the most recent ``task_completed`` /
    ``subtask_*`` event summary so the caller can report progress.
    """
    async with async_unit_of_work(commit=False) as db:
        task_ds = TaskDatastore(db)
        run_ds = TaskSessionDatastore(db)
        event_ds = TaskEventDatastore(db)
        row = await task_ds.get_task_by_project(user_id, project_id, task_id)
        if row is None:
            return None
        runs = await run_ds.list_runs(user_id, task_id)
        latest_summary = ""
        for ev in reversed(
            await event_ds.list_events(user_id, project_id, task_id)
        ):
            summary = (ev.payload or {}).get("summary")
            if summary:
                latest_summary = str(summary)
                break
        plan = TaskPlan.from_dict(row.plan)
        return {
            "task_id": row.id,
            "title": row.title,
            "goal": row.goal,
            "status": row.status,
            "lead_agent": row.lead_agent_slug,
            "latest_summary": latest_summary,
            # Plan overview (VALUZ-TASK): the subtask DAG + which nodes are
            # dispatchable now, so the caller can report/decide next steps.
            "plan": plan.to_panel(),
            "ready": plan.ready_keys(),
            "runs": [
                {
                    "agent": r.agent_slug,
                    "kind": r.kind,
                    "status": r.status,
                    "session_id": r.session_id,
                    "subtask_key": r.subtask_key,
                }
                for r in runs
            ],
        }


__all__ = ["TaskDetail", "TaskEvents", "TaskService"]

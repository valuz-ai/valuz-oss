"""Task read-side queries — observability for the dispatch MCP tools.

Extracted verbatim from ``TaskOrchestrator`` (T1.1 god-object split): these
three reads hold no orchestrator state and call no sibling methods, so they
live as plain module functions. Each opens its own read-only unit of work —
the callers (the ``list_tasks`` / ``get_task`` / ``list_members`` dispatch
tools) run in the app-scoped MCP context, not a request scope.

The HTTP routes deliberately do **not** use these: ``routes/tasks.py`` reads
the same tables but projects them into Pydantic ``TaskResponse`` /
``TaskDetailResponse`` shapes, whereas these return the dict summaries the
agent-facing tools expect.
"""

from __future__ import annotations

from typing import Any

import valuz_agent.boot.kernel  # noqa: F401 — puts kernel on sys.path
from valuz_agent.adapters import kernel_store
from valuz_agent.adapters.agent_resolver import summarize_role
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.agents.datastore import ProjectMemberDatastore
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.plan import TaskPlan


async def list_members(project_id: str) -> list[dict[str, Any]]:
    """Return member descriptors for dispatch tool list_members()."""
    async with async_unit_of_work(commit=False) as db:
        member_ds = ProjectMemberDatastore(db)
        rows = await member_ds.list_by_project(project_id)
        result: list[dict[str, Any]] = []
        for row in rows:
            agent_cfg = await kernel_store.load_agent(row.kernel_agent_id)
            runtime = agent_cfg.runtime_provider if agent_cfg else "unknown"
            name = agent_cfg.name if agent_cfg else row.agent_slug
            role_summary = summarize_role(agent_cfg.instructions) if agent_cfg else ""
            result.append(
                {
                    "slug": row.agent_slug,
                    "name": name,
                    "runtime": runtime,
                    "source_agent_slug": row.source_agent_slug,
                    # Member role/capability summary so the lead can
                    # dispatch accurately (lead-dispatch-mvp §1.5).
                    "role_summary": role_summary,
                }
            )
        return result


async def list_tasks(
    project_id: str,
    *,
    status: str | None = None,
    mine_session_id: str | None = None,
    limit: int = 20,
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
        rows = await task_ds.list_tasks(project_id)
        result: list[dict[str, Any]] = []
        for row in rows:
            if status and row.status != status:
                continue
            meta = row.metadata_ or {}
            originated_by = meta.get("originating_session_id")
            if mine_session_id and originated_by != mine_session_id:
                continue
            runs = await run_ds.list_runs(row.id)
            done = sum(1 for r in runs if r.status in ("completed", "failed"))
            result.append(
                {
                    "task_id": row.id,
                    "title": row.title,
                    "status": row.status,
                    "lead_agent": row.lead_agent_slug,
                    "dispatch_mode": meta.get("dispatch_mode"),
                    "created_at": str(row.created_at) if row.created_at else None,
                    "runs": len(runs),
                    "runs_done": done,
                    "originated_by_me": (
                        bool(mine_session_id) and originated_by == mine_session_id
                    ),
                }
            )
            if len(result) >= limit:
                break
        return result


async def get_task(task_id: str, project_id: str) -> dict[str, Any] | None:
    """Return one task's status + per-run states + latest summary.

    Scoped to *project_id* (cross-project lookups return ``None``).
    ``latest_summary`` is the most recent ``task_completed`` /
    ``subtask_*`` event summary so the caller can report progress.
    """
    async with async_unit_of_work(commit=False) as db:
        task_ds = TaskDatastore(db)
        run_ds = TaskSessionDatastore(db)
        event_ds = TaskEventDatastore(db)
        row = await task_ds.get_task_by_project(project_id, task_id)
        if row is None:
            return None
        runs = await run_ds.list_runs(task_id)
        latest_summary = ""
        for ev in reversed(await event_ds.list_events(project_id, task_id)):
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

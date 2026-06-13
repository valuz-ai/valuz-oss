"""Activity overview — aggregates running (and recently finished) runs.

A "run" is a kernel session, classified by source:
- ``assistant``     — chat in the default (kind="chat") project
- ``project_chat``  — chat in a project
- ``task``          — a task's **lead** session (member subtasks never surface
  as standalone runs)

Sessions live in the kernel; the host reads them via the kernel async store and
enriches with host-owned project + task rows. Built directly off the kernel
``Session`` objects (which already carry ``todos`` / ``status`` / ``model``)
so the overview needs no per-session detail fetch.

Read-only, fully async (ADR-020): valuz tables via the request ``AsyncSession``,
kernel sessions/messages via the kernel's async ``StorePort``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.schemas import SessionData as KernelSession

import valuz_agent.boot.kernel  # noqa: F401 — puts kernel on sys.path
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.auth_context import require_current_user_id
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.models import TaskRow, TaskSessionRow

SourceKind = Literal["assistant", "project_chat", "task"]

# A task's own status is the stable "is it executing" signal — the lead
# session flickers to idle between turns (and while a member subtask is the one
# actually running), so keying off the session status would drop an actively
# executing task. Map the task status onto the run status the overview filters.
_TASK_STATUS_TO_RUN = {
    "active": "running",
    "paused": "paused",
    "completed": "completed",
    "stopped": "stopped",
    "blocked": "blocked",
}
# What the "running" overview surfaces: in-flight work the user can still act
# on. A ``paused`` task is in-flight (recoverable — it's resumable, not
# terminal) and still shows in the project task list, so dropping it from the
# Activity overview made it vanish there while lingering in the project. Keep
# it here, rendered with a "Paused" chip alongside the actively-running cards.
_RUNNING_RUN_STATUS = {"running", "paused"}
# What lands in the "finished" (history) tab: anything that has run and isn't
# in-flight. ``idle`` covers chat conversations that have completed a turn —
# without it, finished chats wouldn't show up at all. ``created`` (never run)
# and ``paused`` are excluded here — ``paused`` belongs to the running overview
# above, not history.
_FINISHED_RUN_STATUS = {"idle", "completed", "stopped", "blocked", "failed"}
_FINISHED_LIMIT = 50
_OUTPUT_CHARS = 200


def _truncate_output(text: str | None) -> str | None:
    """Collapse whitespace and clip the last round's output to a one-glance
    preview for the activity overview."""
    if not text:
        return None
    collapsed = " ".join(text.split())
    if len(collapsed) <= _OUTPUT_CHARS:
        return collapsed
    return collapsed[:_OUTPUT_CHARS].rstrip() + "…"


@dataclass
class TodoSnapshot:
    content: str
    status: str
    activeForm: str | None = None  # noqa: N815 — preserve SDK casing on the wire


@dataclass
class RunSummary:
    session_id: str
    source_kind: SourceKind
    project_id: str
    title: str
    status: str
    updated_at: int  # Unix epoch milliseconds (UTC)
    project_name: str | None = None
    task_id: str | None = None
    current_todo: TodoSnapshot | None = None
    last_message: str | None = None
    # Chats: last round's assistant output (truncated). Tasks use ``last_event``.
    last_output: str | None = None
    # Tasks: latest task timeline event ({type, payload}) — the frontend renders
    # it with the same logic as the task-detail timeline. None for chats.
    last_event: dict[str, Any] | None = None
    model: str | None = None
    runtime: str | None = None


def _map_status(kernel_status: str) -> str:
    """Kernel ``terminated`` → valuz ``failed`` (mirrors sessions service)."""
    return {"terminated": "failed"}.get(kernel_status, kernel_status)


def _pick_todo(todos: list[dict[str, Any]] | None) -> TodoSnapshot | None:
    """The most relevant TODO step: the in-progress one, else the first
    pending, else the last entry. ``None`` when there's no usable content."""
    if not todos:
        return None
    chosen: dict[str, Any] | None = None
    for todo in todos:
        if todo.get("status") == "in_progress":
            chosen = todo
            break
    if chosen is None:
        chosen = next((t for t in todos if t.get("status") == "pending"), todos[-1])
    content = str(chosen.get("content") or "")
    if not content:
        return None
    active_form = chosen.get("activeForm")
    return TodoSnapshot(
        content=content,
        status=str(chosen.get("status") or "pending"),
        activeForm=str(active_form) if active_form else None,
    )


class RunsService:
    def __init__(
        self,
        projects: ProjectDatastore,
        task_sessions: TaskSessionDatastore,
        tasks: TaskDatastore,
        task_events: TaskEventDatastore,
    ) -> None:
        self._projects = projects
        self._task_sessions = task_sessions
        self._tasks = tasks
        self._task_events = task_events

    async def list_runs(self, status: str = "running") -> list[RunSummary]:
        # Recent sessions come from the host project↔session index; the
        # kernel rows are bulk-fetched by id (the kernel itself is
        # project-agnostic).
        index_rows = await project_index.list_recent(limit=200)
        proj_by_session = {r.session_id: r.project_id for r in index_rows}
        sessions: list[KernelSession] = await kernel_client.list_sessions(
            require_current_user_id(), ids=[r.session_id for r in index_rows], limit=200
        )
        ws_map: dict[str, ProjectRow] = {
            str(r.id): r for r in await self._projects.list_projects(require_current_user_id())
        }
        ts_map: dict[str, TaskSessionRow] = {
            r.session_id: r for r in await self._task_sessions.list_all(require_current_user_id())
        }
        task_map: dict[str, TaskRow] = {
            str(r.id): r for r in await self._tasks.list_all(require_current_user_id(), limit=None)
        }

        out: list[RunSummary] = []
        for sess in sessions:
            task_session = ts_map.get(sess.id)
            # member subtask sessions never surface as standalone runs
            if task_session is not None and task_session.kind == "subtask":
                continue
            effective = self._effective_status(_map_status(sess.status), task_session, task_map)
            if status == "running":
                if effective not in _RUNNING_RUN_STATUS:
                    continue
            elif effective not in _FINISHED_RUN_STATUS:
                continue
            out.append(
                await self._build(
                    sess,
                    task_session,
                    ws_map,
                    task_map,
                    effective,
                    project_id=proj_by_session.get(sess.id, ""),
                )
            )

        out.sort(key=lambda r: r.updated_at, reverse=True)
        return out if status == "running" else out[:_FINISHED_LIMIT]

    @staticmethod
    def _effective_status(
        mapped_status: str,
        task_session: TaskSessionRow | None,
        task_map: dict[str, TaskRow],
    ) -> str:
        """Run status the overview filters on. Task leads follow their task's
        status (active → running); everything else uses the session status."""
        if task_session is not None and task_session.kind == "lead" and task_session.task_id:
            task = task_map.get(task_session.task_id)
            if task is not None:
                return _TASK_STATUS_TO_RUN.get(task.status, task.status)
        return mapped_status

    async def _build(
        self,
        sess: KernelSession,
        task_session: TaskSessionRow | None,
        ws_map: dict[str, ProjectRow],
        task_map: dict[str, TaskRow],
        effective_status: str,
        *,
        project_id: str,
    ) -> RunSummary:
        meta: dict[str, Any] = (sess.metadata or {}).get("valuz") or {}
        project = ws_map.get(project_id)
        title = meta.get("name") or meta.get("last_user_message_text") or "Untitled"
        source: SourceKind
        task_id: str | None = None
        last_output: str | None = None
        last_event: dict[str, Any] | None = None
        if task_session is not None and task_session.kind == "lead":
            source = "task"
            task_id = task_session.task_id
            task = task_map.get(task_id or "")
            if task is not None:
                title = task.title
            # Tasks are described by their latest timeline event — the frontend
            # renders it the same way the task-detail timeline does.
            last_event = await self._latest_task_event(task_id)
        else:
            source = (
                "project_chat" if project is not None and project.kind == "project" else "assistant"
            )
            last_output = _truncate_output(await self._latest_assistant_text(sess.id))
        return RunSummary(
            session_id=sess.id,
            source_kind=source,
            project_id=project_id,
            project_name=project.name if project is not None else None,
            task_id=task_id,
            title=str(title),
            status=effective_status,
            updated_at=sess.created_at,
            current_todo=_pick_todo(getattr(sess, "todos", None)),
            last_message=meta.get("last_user_message_text") or None,
            last_output=last_output,
            last_event=last_event,
            model=sess.model or None,
            runtime=getattr(sess, "runtime_provider", None) or None,
        )

    @staticmethod
    async def _latest_assistant_text(session_id: str) -> str | None:
        """Assistant output of the session's most recent run that produced any —
        the last round's content. Scans a few recent messages because the
        in-flight turn's message may not have its ``assistant_message`` set yet.
        """
        messages = await kernel_client.list_messages(require_current_user_id(), session_id, limit=3)
        for message in messages:  # most-recent first
            if message.assistant_message:
                return str(message.assistant_message)
        return None

    async def _latest_task_event(self, task_id: str | None) -> dict[str, Any] | None:
        if not task_id:
            return None
        row = await self._task_events.latest_event(require_current_user_id(), task_id)
        if row is None:
            return None
        return {"type": row.type, "payload": row.payload or {}}

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

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.schemas import SessionData as KernelSession
from app.schemas import TodoItem

import valuz_agent.boot.kernel  # noqa: F401 — puts kernel on sys.path
from valuz_agent.adapters import kernel_client
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.sessions import project_index
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow

logger = logging.getLogger(__name__)

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
# Index-pool size for the recency window. Generous so user activity stays in
# range as automation runs accrue; the per-group _FINISHED_LIMIT bounds output.
_INDEX_POOL = 500
_OUTPUT_CHARS = 200
# Per-session enrichment reads the shared kernel SQLite store.  Keep the
# request comfortably below its 5 + 10 overflow connection budget so the
# conversation, catalog, and stream endpoints retain headroom while a large
# finished-runs overview is being assembled.
_ENRICH_CONCURRENCY = 4


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
    origin: str = "user"
    current_todo: TodoSnapshot | None = None
    last_message: str | None = None
    # Chats: last round's assistant output (truncated). Tasks use ``last_event``.
    last_output: str | None = None
    # Tasks: latest task timeline event ({type, payload}) — the frontend renders
    # it with the same logic as the task-detail timeline. None for chats.
    last_event: dict[str, Any] | None = None
    model: str | None = None
    runtime: str | None = None
    # True when the session carries a live background task (run_in_background
    # shell command). Such sessions surface in the running view even while no
    # turn is streaming — the sidebar/Activity keep signalling in-flight work
    # after the user navigates away from the conversation.
    background: bool = False


def _map_status(kernel_status: str) -> str:
    """Kernel ``terminated`` → valuz ``failed`` (mirrors sessions service)."""
    return {"terminated": "failed"}.get(kernel_status, kernel_status)


def _pick_todo(todos: list[TodoItem] | None) -> TodoSnapshot | None:
    """The most relevant TODO step: the in-progress one, else the first
    pending, else the last entry. ``None`` when there's no usable content.

    ``todos`` are kernel ``TodoItem`` models (pydantic), not dicts — use
    attribute access, not ``.get``.
    """
    if not todos:
        return None
    chosen = next((t for t in todos if t.status == "in_progress"), None)
    if chosen is None:
        chosen = next((t for t in todos if t.status == "pending"), todos[-1])
    content = str(chosen.content or "")
    if not content:
        return None
    return TodoSnapshot(
        content=content,
        status=str(chosen.status or "pending"),
        activeForm=str(chosen.activeForm) if chosen.activeForm else None,
    )


class RunsService:
    def __init__(
        self,
        projects: ProjectDatastore,
        task_sessions: TaskSessionDatastore,
        tasks: TaskDatastore,
        task_events: TaskEventDatastore,
        automations: AutomationDatastore,
    ) -> None:
        self._projects = projects
        self._task_sessions = task_sessions
        self._tasks = tasks
        self._task_events = task_events
        self._automations = automations

    async def list_runs(
        self,
        user_id: str,
        status: str = "running",
        project_id: str | None = None,
        limit: int | None = None,
    ) -> list[RunSummary]:
        # Recent sessions come from the host project↔session index; the
        # kernel rows are bulk-fetched by id (the kernel itself is
        # project-agnostic). The pool is generous (automation runs accrue
        # fast); the per-group ``_FINISHED_LIMIT`` budget below is what bounds
        # the response, not this fetch.
        #
        # ``project_id`` scopes both the index window and the output budget to
        # one project — the sidebar accordion's path. Without it a project whose
        # conversations are older than the global window's tail can never appear
        # under its own row, however few sessions it has.
        index_rows = await project_index.list_recent(
            limit=_INDEX_POOL, user_id=user_id, project_id=project_id
        )
        proj_by_session = {r.session_id: r.project_id for r in index_rows}
        # Last-activity per session (index ``updated_at``, bumped each turn) — the
        # sort key that floats a chat with a new message to the top.
        activity_by_session = {r.session_id: r.updated_at for r in index_rows}
        sessions: list[KernelSession] = await kernel_client.list_sessions(
            user_id, ids=[r.session_id for r in index_rows], limit=_INDEX_POOL
        )
        ws_map: dict[str, ProjectRow] = {
            str(r.id): r for r in await self._projects.list_projects(user_id)
        }
        # Bounded to the index pool: the overview is polled, and unbounded
        # full-table maps here grow with install age (automations mint tasks
        # continuously) — thousands of ORM rows per tick for a ≤500-row need.
        ts_map: dict[str, TaskSessionRow] = {
            r.session_id: r
            for r in await self._task_sessions.list_by_session_ids(user_id, list(proj_by_session))
        }
        task_map: dict[str, TaskRow] = {
            str(r.id): r
            for r in await self._tasks.list_by_ids(
                user_id, sorted({r.task_id for r in ts_map.values() if r.task_id})
            )
        }
        # Session ids spawned by a scheduled automation run. A task created by
        # an automation has a lead session whose own origin stays "user" and
        # whose task row carries no automation marker — but the automation's
        # run record points at this session. Membership here is the reliable
        # "automation-triggered" signal for both chats and task leads.
        automation_session_ids = await self._automations.list_run_session_ids(user_id)

        # Sessions whose warm runtime carries a live background task
        # (run_in_background shell command). They surface in the running view
        # even while no turn is streaming, so the sidebar/Activity keep
        # signalling in-flight work after the user leaves the conversation.
        # Best-effort: the overview must not break if the kernel seam hiccups.
        bg_busy_ids: set[str] = set()
        if status == "running":
            try:
                bg_busy_ids = set(await kernel_client.bg_busy_session_ids())
            except Exception:  # noqa: BLE001
                logger.debug("runs overview: bg-busy probe failed", exc_info=True)

        candidates = []
        for sess in sessions:
            task_session = ts_map.get(sess.id)
            # member subtask sessions never surface as standalone runs
            if task_session is not None and task_session.kind == "subtask":
                continue
            effective = self._effective_status(_map_status(sess.status), task_session, task_map)
            background = sess.id in bg_busy_ids
            if status == "running":
                if effective not in _RUNNING_RUN_STATUS:
                    if not background:
                        continue
                    # Idle session, but background work in flight — surface it
                    # as running so every runs-derived indicator lights up.
                    effective = "running"
            elif effective not in _FINISHED_RUN_STATUS:
                continue
            candidates.append((sess, task_session, effective, background))

        # Task rows are described by their latest timeline event. Resolve them
        # for the whole batch HERE, before the fan-out: reading them inside
        # ``_build_one`` issued concurrent statements on the one request-scoped
        # AsyncSession (unsupported), and the per-row ``except`` below turned
        # the resulting InvalidRequestError into "skipping session …" — runs
        # silently vanishing from the overview.
        latest_task_events = await self._task_events.latest_events_by_task(
            user_id,
            sorted(
                {
                    ts.task_id
                    for _s, ts, _e, _b in candidates
                    if ts is not None and ts.kind == "lead" and ts.task_id
                }
            ),
        )

        # Per-run enrichment (`_build` fetches the latest message) is
        # independent per session — run it concurrently instead of one awaited
        # round-trip per run (the finished view can carry ~100 runs, which
        # made this loop the dominant cost of the polled overview).
        async def _build_one(
            sess: Any, task_session: Any, effective: str, background: bool
        ) -> RunSummary | None:
            async with enrich_slots:
                # Isolate per-session enrichment: a single malformed session
                # must not blank the entire overview. Skip it, keep the rest.
                try:
                    return await self._build(
                        user_id,
                        sess,
                        task_session,
                        ws_map,
                        task_map,
                        effective,
                        project_id=proj_by_session.get(sess.id, ""),
                        last_activity=activity_by_session.get(sess.id, sess.created_at),
                        automation_session_ids=automation_session_ids,
                        latest_task_events=latest_task_events,
                        background=background,
                    )
                except Exception:
                    logger.exception(
                        "runs overview: skipping session %s — failed to build summary",
                        sess.id,
                    )
                    return None

        enrich_slots = asyncio.Semaphore(_ENRICH_CONCURRENCY)
        built = await asyncio.gather(
            *(
                _build_one(sess, task_session, effective, background)
                for sess, task_session, effective, background in candidates
            )
        )
        out: list[RunSummary] = [summary for summary in built if summary is not None]

        out.sort(key=lambda r: r.updated_at, reverse=True)
        budget = limit if limit is not None else _FINISHED_LIMIT
        if status == "running":
            return out[:limit] if limit is not None else out
        # Separate budgets so a flood of automation runs can't crowd user
        # chats/tasks out of the recency-sorted window (and vice-versa). Each
        # group keeps its own budget of most-recent runs; the client splits them
        # across the 全部/对话/任务/自动化 tabs.
        user_runs = [r for r in out if r.origin != "automation"]
        automation_runs = [r for r in out if r.origin == "automation"]
        return user_runs[:budget] + automation_runs[:budget]

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
        user_id: str,
        sess: KernelSession,
        task_session: TaskSessionRow | None,
        ws_map: dict[str, ProjectRow],
        task_map: dict[str, TaskRow],
        effective_status: str,
        *,
        project_id: str,
        last_activity: int,
        automation_session_ids: set[str],
        latest_task_events: dict[str, TaskEventRow],
        background: bool = False,
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
            row = latest_task_events.get(task_id or "")
            last_event = (
                {"type": row.type, "payload": row.payload or {}} if row is not None else None
            )
        else:
            source = (
                "project_chat" if project is not None and project.kind == "project" else "assistant"
            )
            last_output = _truncate_output(await self._latest_assistant_text(user_id, sess.id))

        # ``origin`` = who triggered this run.
        # - Chat sessions: read the session's own metadata origin (the
        #   automation runner stamps "automation" at creation).
        # - Task leads: the lead session's own origin stays "user" (the task
        #   orchestrator doesn't propagate the trigger) and the task row carries
        #   no marker. The reliable signal is the automation *run* record: if
        #   this session id was produced by a scheduled run, it's automation.
        own_origin = str(meta.get("origin") or "user")
        if own_origin == "user" and sess.id in automation_session_ids:
            own_origin = "automation"

        return RunSummary(
            session_id=sess.id,
            source_kind=source,
            project_id=project_id,
            project_name=project.name if project is not None else None,
            task_id=task_id,
            origin=own_origin,
            title=str(title),
            status=effective_status,
            # Last-activity (host index ``updated_at``, bumped each turn by
            # ``project_index.touch_activity``) — NOT ``sess.created_at`` — so a
            # chat with a new message floats to the top of the sidebar RECENTS,
            # consistent with the activity feed. Also drives the running card's
            # elapsed = now − last_activity (current-turn runtime).
            updated_at=last_activity,
            current_todo=_pick_todo(getattr(sess, "todos", None)),
            last_message=meta.get("last_user_message_text") or None,
            last_output=last_output,
            last_event=last_event,
            model=sess.model or None,
            runtime=getattr(sess, "runtime_provider", None) or None,
            background=background,
        )

    @staticmethod
    async def _latest_assistant_text(user_id: str, session_id: str) -> str | None:
        """Assistant output of the session's most recent run that produced any —
        the last round's content. Scans a few recent messages because the
        in-flight turn's message may not have its ``assistant_message`` set yet.
        """
        messages = await kernel_client.list_messages(user_id, session_id, limit=3)
        for message in messages:  # most-recent first
            if message.assistant_message:
                return str(message.assistant_message)
        return None

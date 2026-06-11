"""HTTP routes for lead-dispatch Tasks (lead-dispatch-mvp §S* / H9/H14).

Endpoints:
  POST   /v1/projects/{id}/tasks            — kickoff a task (goal + lead agent)
  POST   /v1/projects/{id}/tasks:draft      — open a draft task (VALUZ-CHATPLAN S3)
  GET    /v1/projects/{id}/tasks            — list project tasks
  GET    /v1/tasks/{task_id}                  — task header + runs + events
  GET    /v1/tasks/{task_id}/events           — full event log (ACTIVITY)
  GET    /v1/tasks/{task_id}/events/stream    — SSE: live task events (cursor: ?after_seq=N)
  POST   /v1/tasks/{task_id}:intervene        — note / revise_goal / pause / resume / stop
  POST   /v1/tasks/{task_id}:commit           — draft → active (VALUZ-CHATPLAN S3)
  POST   /v1/tasks/{task_id}:abandon          — draft → abandoned (VALUZ-CHATPLAN S3)
  POST   /v1/tasks/{task_id}:inject           — push user instruction into lead mailbox (S4)
  POST   /v1/tasks/{task_id}/plan             — lay down the initial plan
  PATCH  /v1/tasks/{task_id}/plan             — modify the plan (CAS via expected_version)
  GET    /v1/tasks/{task_id}/plan             — read the plan snapshot
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from valuz_agent.infra.db import async_unit_of_work, get_async_session
from valuz_agent.infra.sse import shielded
from valuz_agent.modules.tasks import messaging, planning
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.models import TaskEventRow
from valuz_agent.modules.tasks.orchestrator import task_orchestrator

router = APIRouter(tags=["tasks"])


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class KickoffTaskRequest(BaseModel):
    goal: str
    lead_agent_slug: str
    refs: list[str] | None = None
    title: str | None = None
    created_by: str = "user"
    # Dispatch architecture (M10): "sync" (v1, lead drives one turn, dispatch
    # blocks) or "async" (v2, lead + members are persistent actors).
    dispatch_mode: Literal["sync", "async"] = "async"


class TaskResponse(BaseModel):
    id: str
    project_id: str
    title: str
    goal: str
    status: str
    created_by: str
    lead_agent_slug: str
    current_holder: str
    file_path: str
    # Surfaced so the sidebar TASKS section can sort/group by recency
    # ("active just now" vs "completed yesterday").
    created_at: int
    updated_at: int

    model_config = {"from_attributes": True}


class RunResponse(BaseModel):
    id: str
    session_id: str
    agent_slug: str
    sequence: int
    kind: str
    status: str
    label: str | None
    goal: str | None
    dispatched_by: str | None
    project_mode: str
    run_dir: str | None
    result_manifest: dict[str, Any] | None

    model_config = {"from_attributes": True}


class EventResponse(BaseModel):
    id: str
    sequence: int
    type: str
    actor: str
    session_id: str | None
    payload: dict[str, Any]
    created_at: int

    model_config = {"from_attributes": True}


class TaskDetailResponse(BaseModel):
    task: TaskResponse
    runs: list[RunResponse]
    events: list[EventResponse]


class InterveneRequest(BaseModel):
    action: Literal["note", "revise_goal", "pause", "resume", "stop"]
    text: str | None = None
    goal: str | None = None


# ---- VALUZ-CHATPLAN S3 schemas --------------------------------------------


class DraftTaskRequest(BaseModel):
    goal: str
    lead_agent_slug: str
    originating_session_id: str
    refs: list[str] | None = None
    title: str | None = None


class DraftTaskResponse(BaseModel):
    task_id: str
    status: str
    plan_version: int
    title: str
    lead_agent_slug: str


class CommitTaskRequest(BaseModel):
    caller_session_id: str
    lead_agent_slug: str | None = None


class AbandonTaskRequest(BaseModel):
    caller_session_id: str
    reason: str | None = None


class InjectTaskRequest(BaseModel):
    text: str
    from_session_id: str


class InjectTaskResponse(BaseModel):
    delivered: bool
    lead_session_id: str | None = None
    reason: str | None = None


class PlanTaskRequest(BaseModel):
    """Used by both POST (initial plan) and PATCH (modify). ``lead_session_id``
    is the caller's session — for draft-mode chat sessions, the originating
    chat; for active-mode lead callers, the lead session id."""

    lead_session_id: str
    subtasks: list[dict[str, Any]] | None = None  # POST: initial plan
    add: list[dict[str, Any]] | None = None  # PATCH: add nodes
    update: list[dict[str, Any]] | None = None  # PATCH: patch nodes
    expected_version: int | None = None  # PATCH: optimistic-lock token


class PlanResponse(BaseModel):
    subtasks: list[dict[str, Any]]
    ready: list[str]
    counts: dict[str, int] | None = None
    all_done: bool | None = None
    current_version: int


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.post("/v1/projects/{project_id}/tasks", status_code=201, response_model=TaskResponse)
async def kickoff_task(project_id: str, payload: KickoffTaskRequest) -> TaskResponse:
    """Create a task and start its lead session (lead self-dispatches sub-runs)."""
    try:
        row = await task_orchestrator.kickoff(
            project_id=project_id,
            goal=payload.goal,
            lead_agent_slug=payload.lead_agent_slug,
            refs=payload.refs,
            created_by=payload.created_by,
            title=payload.title,
            dispatch_mode=payload.dispatch_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskResponse.model_validate(row)


@router.get("/v1/projects/{project_id}/tasks", response_model=dict[str, list[TaskResponse]])
async def list_tasks(
    project_id: str, db: AsyncSession = Depends(get_async_session)
) -> dict[str, list[TaskResponse]]:
    rows = await TaskDatastore(db).list_tasks(project_id)
    return {"tasks": [TaskResponse.model_validate(r) for r in rows]}


@router.get("/v1/tasks", response_model=dict[str, list[TaskResponse]])
async def list_all_tasks(
    limit: int = 50, db: AsyncSession = Depends(get_async_session)
) -> dict[str, list[TaskResponse]]:
    """Global cross-project task list, newest activity first. Powers the
    sidebar TASKS section so users see what's running regardless of which
    project page they're on."""
    rows = await TaskDatastore(db).list_all(limit=limit)
    return {"tasks": [TaskResponse.model_validate(r) for r in rows]}


@router.get("/v1/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_task(
    task_id: str, db: AsyncSession = Depends(get_async_session)
) -> TaskDetailResponse:
    task = await TaskDatastore(db).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    runs = await TaskSessionDatastore(db).list_runs(task_id)
    events = await TaskEventDatastore(db).list_events(task.project_id, task_id)
    return TaskDetailResponse(
        task=TaskResponse.model_validate(task),
        runs=[RunResponse.model_validate(r) for r in runs],
        events=[EventResponse.model_validate(e) for e in events],
    )


@router.get("/v1/tasks/{task_id}/events", response_model=dict[str, list[EventResponse]])
async def list_task_events(
    task_id: str, db: AsyncSession = Depends(get_async_session)
) -> dict[str, list[EventResponse]]:
    task = await TaskDatastore(db).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    events = await TaskEventDatastore(db).list_events(task.project_id, task_id)
    return {"events": [EventResponse.model_validate(e) for e in events]}


# SSE polling cadence — tight enough that ``inject_into_task`` → plan
# change → chat reaction feels snappy, loose enough that idle tasks
# don't hammer the DB. 500ms matches the kernel-event SSE legacy poll
# default and the user-perceived latency budget for a "live" UI.
_TASK_EVENTS_POLL_INTERVAL_S = 0.5

# Heartbeat keep-alive — sse-starlette / browsers may otherwise close
# an idle connection. 15s matches the kernel session SSE.
_TASK_EVENTS_HEARTBEAT_S = 15.0


async def _iter_task_events_sse(
    task_id: str,
    project_id: str,
    after_seq: int,
    is_disconnected: callable | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Polling iterator for task-event SSE.

    Yields ``{event, data, id}`` dicts compatible with sse-starlette's
    ``EventSourceResponse``. Each event:
      - ``event`` = the task_event type (``task_planned`` / ``committed`` /
        ``task_plan_update`` / ``user_inject`` / etc.)
      - ``data`` = JSON-encoded EventResponse
      - ``id``   = the sequence number, so a reconnecting client can pass
        ``?after_seq=<id>`` to resume without gaps

    Sends a heartbeat ``{event: 'heartbeat'}`` every ``_TASK_EVENTS_HEARTBEAT_S``
    seconds of silence so intermediaries (nginx, browsers) don't close the
    connection.

    Task events don't have an in-memory broadcast subscriber (unlike kernel
    events). DB polling at 500ms is cheap (single indexed query per tick)
    and exact (sequence is monotonic per task — no gaps possible).
    """
    cursor = after_seq
    silent_for = 0.0
    while True:
        if is_disconnected is not None and is_disconnected():
            return

        # ``shielded``: client disconnect cancels this generator; landing the
        # cancellation inside the in-flight DB read would tear the pooled
        # connection down mid-checkin (see ``infra.sse.shielded``).
        async def _tick_read(after: int) -> list[TaskEventRow]:
            async with async_unit_of_work(commit=False) as db:
                return await TaskEventDatastore(db).list_events_after(project_id, task_id, after)

        rows = await shielded(_tick_read(cursor))
        if rows:
            for row in rows:
                event_payload = EventResponse.model_validate(row).model_dump(mode="json")
                yield {
                    "id": str(row.sequence),
                    "event": row.type,
                    "data": json.dumps(event_payload, ensure_ascii=False),
                }
                cursor = row.sequence
            silent_for = 0.0
        else:
            silent_for += _TASK_EVENTS_POLL_INTERVAL_S
            if silent_for >= _TASK_EVENTS_HEARTBEAT_S:
                yield {"event": "heartbeat", "data": ""}
                silent_for = 0.0
        await asyncio.sleep(_TASK_EVENTS_POLL_INTERVAL_S)


@router.get("/v1/tasks/{task_id}/events/stream")
async def stream_task_events(
    task_id: str,
    request: Request,
    after_seq: int = 0,
) -> EventSourceResponse:
    """SSE subscription for a task's event timeline.

    Reconnect protocol: client passes ``?after_seq=<last_seen_id>`` to
    resume from the cursor. The server replays everything newer
    (no gaps possible — sequence is strictly monotonic). The
    ``id:`` field on each emitted event is the sequence number the
    client should remember for the next reconnect.

    Polling cadence: 500ms (see ``_TASK_EVENTS_POLL_INTERVAL_S``). The
    DB write side (``_emit_plan_update`` and friends) doesn't currently
    publish to an in-memory broadcast queue; once that lands (Slice 6
    optimization candidate) this endpoint can switch to push.
    """
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    # ``request.is_disconnected`` is async; sse-starlette wraps the
    # generator with a cancel scope that fires on client drop, so we
    # don't need to pass a custom disconnect probe — ``None`` is fine.
    del request
    return EventSourceResponse(
        _iter_task_events_sse(
            task_id=task_id,
            project_id=task.project_id,
            after_seq=after_seq,
            is_disconnected=None,
        )
    )


@router.post("/v1/tasks/{task_id}:intervene", response_model=TaskResponse)
async def intervene(
    task_id: str, payload: InterveneRequest, db: AsyncSession = Depends(get_async_session)
) -> TaskResponse:
    """User intervention on a running task.

    note          — append user_note (does not interrupt the lead)
    revise_goal   — update task.goal + append goal_revised
    pause         — cascade-halt the lead + every in-flight member → ``paused``
                    (recoverable; app-restart skips it, user resumes explicitly)
    stop          — cascade-halt → ``stopped`` (UI-terminal: no resume button;
                    still revivable via chat/inject per design)
    resume        — reconcile + respawn members + re-drive lead
                    (paused/stopped/blocked → active)
    """
    task_ds = TaskDatastore(db)
    event_ds = TaskEventDatastore(db)
    task = await task_ds.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    ws = task.project_id

    if payload.action == "note":
        await event_ds.append_event(
            ws, task_id, "user_note", actor="user", payload={"text": payload.text or ""}
        )
    elif payload.action == "revise_goal":
        if not payload.goal:
            raise HTTPException(status_code=422, detail="goal is required for revise_goal")
        task.goal = payload.goal
        await task_ds.update_task(task)
        # Push the revision to the running lead so it actually re-orients —
        # task.goal alone is pull-only (the lead never re-reads it mid-run).
        # Best-effort: an offline/finished lead just isn't woken (DB goal still
        # updated above). The lead decides autonomously how to fold it in.
        notified = await messaging.notify_lead_goal_revised(
            task_id=task_id, project_id=ws, new_goal=payload.goal
        )
        await event_ds.append_event(
            ws,
            task_id,
            "goal_revised",
            actor="user",
            payload={"goal": payload.goal, "delivered_to_lead": notified["delivered"]},
        )
    elif payload.action in ("pause", "stop"):
        # Layer 2 cascade halt (orchestrator manages its own txn). ``pause`` →
        # ``paused`` (resumable, 恢复 button stays); ``stop`` → ``stopped``
        # (UI-terminal, no 恢复 button — still revivable via chat/inject).
        target = "paused" if payload.action == "pause" else "stopped"
        await task_orchestrator.stop_task(task_id, ws, target_status=target)
    elif payload.action == "resume":
        await task_orchestrator.resume_task(task_id, ws)

    db.expire_all()  # drop cached rows so we see the orchestrator's committed write
    refreshed = await task_ds.get_task(task_id)
    assert refreshed is not None
    return TaskResponse.model_validate(refreshed)


class StopMemberResponse(BaseModel):
    stopped: bool


# --------------------------------------------------------------------------
# VALUZ-CHATPLAN S3 — draft / commit / abandon / inject / plan REST routes
#
# These wrap the orchestrator methods that the MCP tool handlers also call
# (draft_task, commit_task, abandon_task, inject_into_task, plan_task,
# modify_plan, get_plan), so the frontend can drive the same state machine
# directly via HTTP without going through an agent turn.
# --------------------------------------------------------------------------


@router.post(
    "/v1/projects/{project_id}/tasks:draft",
    status_code=201,
    response_model=DraftTaskResponse,
)
async def draft_task(project_id: str, payload: DraftTaskRequest) -> DraftTaskResponse:
    """Open a draft task (status=draft, plan_version=0). No lead session is
    started — the originating chat session is recorded as the plan writer."""
    try:
        row = await task_orchestrator.draft_task(
            project_id=project_id,
            goal=payload.goal,
            lead_agent_slug=payload.lead_agent_slug,
            originating_session_id=payload.originating_session_id,
            refs=payload.refs,
            title=payload.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DraftTaskResponse(
        task_id=row.id,
        status=row.status,
        plan_version=row.plan_version or 0,
        title=row.title,
        lead_agent_slug=row.lead_agent_slug,
    )


@router.post("/v1/tasks/{task_id}:commit", response_model=dict[str, Any])
async def commit_task(task_id: str, payload: CommitTaskRequest) -> dict[str, Any]:
    """Promote a draft task to active by spawning its lead session."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    result = await task_orchestrator.commit_task(
        task_id=task_id,
        project_id=task.project_id,
        caller_session_id=payload.caller_session_id,
        lead_agent_slug_override=payload.lead_agent_slug,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/v1/tasks/{task_id}:abandon", response_model=dict[str, Any])
async def abandon_task(task_id: str, payload: AbandonTaskRequest) -> dict[str, Any]:
    """Discard a draft task. Terminal (cannot be resurrected)."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    result = await task_orchestrator.abandon_task(
        task_id=task_id,
        project_id=task.project_id,
        caller_session_id=payload.caller_session_id,
        reason=payload.reason or "",
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/v1/tasks/{task_id}:inject", response_model=InjectTaskResponse)
async def inject_into_task(task_id: str, payload: InjectTaskRequest) -> InjectTaskResponse:
    """Push a user instruction into the lead session's mailbox. Returns
    delivered=False with reason when the lead is offline / task not active."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if not payload.text or not payload.text.strip():
        raise HTTPException(status_code=422, detail="text is required")
    result = await messaging.inject_into_task(
        task_id=task_id,
        project_id=task.project_id,
        text=payload.text,
        from_session_id=payload.from_session_id,
    )
    return InjectTaskResponse(
        delivered=bool(result.get("delivered")),
        lead_session_id=result.get("lead_session_id"),
        reason=result.get("reason"),
    )


@router.post("/v1/tasks/{task_id}/plan", response_model=PlanResponse)
async def plan_task_route(task_id: str, payload: PlanTaskRequest) -> PlanResponse:
    """Lay down the initial plan (errors if a plan with progress already exists)."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if not payload.subtasks:
        raise HTTPException(status_code=422, detail="subtasks is required and must be non-empty")
    result = await planning.plan_task(
        task_id=task_id,
        project_id=task.project_id,
        lead_session_id=payload.lead_session_id,
        subtasks=payload.subtasks,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return PlanResponse(
        subtasks=result["subtasks"],
        ready=result["ready"],
        current_version=result["current_version"],
    )


@router.patch("/v1/tasks/{task_id}/plan", response_model=PlanResponse)
async def modify_plan_route(task_id: str, payload: PlanTaskRequest) -> PlanResponse:
    """Patch the plan: add nodes / update existing nodes. CAS via
    ``expected_version`` — returns 409 on conflict."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    result = await planning.modify_plan(
        task_id=task_id,
        project_id=task.project_id,
        lead_session_id=payload.lead_session_id,
        add=payload.add,
        update=payload.update,
        expected_version=payload.expected_version,
    )
    if result.get("error") == "PLAN_VERSION_CONFLICT":
        raise HTTPException(status_code=409, detail=result)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return PlanResponse(
        subtasks=result["subtasks"],
        ready=result["ready"],
        current_version=result["current_version"],
    )


@router.get("/v1/tasks/{task_id}/plan", response_model=PlanResponse)
async def get_plan_route(task_id: str) -> PlanResponse:
    """Read the plan snapshot + ready keys + counts + current_version."""
    async with async_unit_of_work(commit=False) as db:
        task = await TaskDatastore(db).get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    result = await planning.get_plan(
        task_id=task_id,
        project_id=task.project_id,
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return PlanResponse(
        subtasks=result["subtasks"],
        ready=result["ready"],
        counts=result.get("counts"),
        all_done=result.get("all_done"),
        current_version=result["current_version"],
    )


@router.post("/v1/runs/{session_id}:stop", response_model=StopMemberResponse)
async def stop_member(session_id: str) -> StopMemberResponse:
    """User-initiated single-member stop: interrupt one subtask, notify the lead
    (member_done cancelled), run→rejected, node→rework. Task stays active."""
    stopped = await task_orchestrator.stop_member(session_id)
    if not stopped:
        raise HTTPException(status_code=404, detail=f"Subtask run not found: {session_id}")
    return StopMemberResponse(stopped=True)

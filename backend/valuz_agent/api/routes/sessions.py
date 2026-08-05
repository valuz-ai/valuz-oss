import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from valuz_agent.adapters.data_reader import data_reader
from valuz_agent.adapters.event_sse_adapter import iter_events_sse
from valuz_agent.api.deps import get_current_user_id, get_session_service
from valuz_agent.infra.db import get_async_session
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore
from valuz_agent.modules.artifacts.models import ArtifactContentRow, ArtifactRevisionRow
from valuz_agent.modules.files.uri import build_valuz_file_uri
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.dto import (
    QueuedInputList,
    SessionDetail,
    SessionEventEnvelope,
    SessionListItem,
    SessionRunResponse,
)
from valuz_agent.modules.sessions.errors import BudgetExceeded
from valuz_agent.modules.sessions.models import SessionAttachmentRow
from valuz_agent.modules.sessions.schemas import (
    SessionEffortRequest,
    SessionModelSelection,
    SessionWorktreeSpec,
)
from valuz_agent.modules.sessions.service import SessionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


def _data_file_path(user_id: str, ref: str) -> Path:
    rel = Path(ref)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError("Invalid data file path")
    target = (fs_registry.data_dir(user_id) / rel).resolve()
    data_root = fs_registry.data_dir(user_id).resolve()
    if data_root != target and data_root not in target.parents:
        raise ValueError("Data file path escapes data dir")
    return target


class SessionCreateRequest(SessionModelSelection):
    """Body for ``POST /v1/sessions``.

    Inherits ``model_id`` / ``provider_id`` / ``runtime_id`` / ``effort``
    from ``SessionModelSelection`` so every session-creating entry point
    shares the same nullable shape and the same
    ``adapters.model_resolver`` precedence. ``model`` is frozen at
    creation per ADR-006; ``permission_mode`` and ``effort`` are
    live-reconcilable (kernel V5+bba3014) via dedicated PATCH routes.
    """

    project_id: str
    title: str | None = None
    # Slugs of MCP data sources to enable for this session (e.g. ["reportify"]).
    # The frontend's data-source picker provides them; ``adapters.mcp_resolver``
    # expands each slug into one or more kernel ``McpServerConfig`` rows.
    mcp_provider_slugs: list[str] | None = None
    # Approval mode for this session. Stamped at creation but can be
    # changed mid-session via ``PATCH /v1/sessions/{id}/permission-mode``
    # — kernel V5+bba3014 live-reconciles on next Send.
    # ``default`` parks every write/shell/MCP call on the host for user
    # approval; ``auto_review`` lets an LLM classifier auto-decide and
    # surfaces a review feed; ``full_access`` auto-approves everything.
    # ``None`` falls through to the kernel default of ``full_access``.
    permission_mode: str | None = None
    # Agent to bind this conversation to. When set, skills / connectors /
    # instructions come from the agent, and runtime / model / provider /
    # effort default to the agent's brain — but an explicit model/provider/
    # runtime/effort above OVERRIDES that default for this one session
    # (the agent row is never modified). ``None`` keeps the classic
    # model-picker path (quick chats).
    agent_slug: str | None = None
    # Opt-in worktree isolation: presence of the object (even empty) runs
    # the session in an isolated git worktree of the project repo on its
    # own branch. Requires the project cwd to be inside a git repository —
    # 422 otherwise (no silent fallback). See
    # docs/design/project-worktree-design.md.
    worktree: SessionWorktreeSpec | None = None


class SessionPermissionModeRequest(BaseModel):
    """Body for ``PATCH /v1/sessions/{id}/permission-mode``.

    Live-reconcile (kernel V5+bba3014): the new mode applies on the
    next Send. Each runtime picks it up its own way (Claude: live
    mutator + fork-on-bypass; Codex: turn_kwargs; DeepAgents: graph
    rebuild). A turn already in flight keeps the mode it started with.
    """

    permission_mode: str


class SessionActionRequest(BaseModel):
    """Body for ``POST /v1/sessions/{id}/actions``.

    Wire shape mirrors the kernel route under ``{KERNEL_API_PREFIX}/v1/...``
    1:1 (ADR-013; default ``/kernel`` for this host) so the host never has to
    translate between the two. See the kernel's ``SubmitActionRequest``
    schema for the field-level invariants.

    Decision verbs (V5+d008b53):
      - ``approve`` / ``reject`` — universal verbs (v1).
      - ``answer`` — clarifying_questions only; carries ``answers``.
      - ``approve_with_changes`` — A1; carries ``modified_input``.
      - ``approve_for_session`` — v2; commits a session-scoped rule.
        No client payload; the kernel reuses the staged pending's
        ``session_rule_preview`` so subsequent matching tool calls
        short-circuit with ``auto_approved``.

    ``auto_approved`` / ``expired`` / ``interrupted`` are kernel-emitted
    only and intentionally absent from the Literal so Pydantic rejects
    them as input.
    """

    pending_id: str
    decision: Literal[
        "approve",
        "approve_with_changes",
        "approve_for_session",
        "reject",
        "answer",
    ]
    message: str | None = None
    answers: dict[str, str | list[str]] | None = None
    # Replacement tool args for an ``approve_with_changes`` decision.
    # Same shape as the original tool input from the pending's payload —
    # the kernel maps it to ``PermissionResultAllow.updated_input`` on
    # Claude and ``EditDecision.edited_action.args`` on DeepAgents.
    # Pydantic rejects 422 when this field is present on any other
    # decision verb, and rejects 422 when ``approve_with_changes`` is
    # missing it — defense-in-depth so the orchestrator gets a clean
    # shape regardless of what the wire sent.
    modified_input: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _enforce_decision_payload_invariants(self) -> "SessionActionRequest":
        # ``answer`` ↔ ``answers``
        if self.decision == "answer" and self.answers is None:
            raise ValueError("decision='answer' requires the 'answers' field")
        if self.decision != "answer" and self.answers is not None:
            raise ValueError(
                f"'answers' is only valid with decision='answer'; got decision={self.decision!r}"
            )
        # ``approve_with_changes`` ↔ ``modified_input``
        if self.decision == "approve_with_changes" and self.modified_input is None:
            raise ValueError("decision='approve_with_changes' requires the 'modified_input' field")
        if self.decision != "approve_with_changes" and self.modified_input is not None:
            raise ValueError(
                f"'modified_input' is only valid with decision='approve_with_changes'; "
                f"got decision={self.decision!r}"
            )
        return self


class SessionActionResponse(BaseModel):
    session_id: str
    pending_id: str
    decision: str
    accepted_at: int  # Unix epoch milliseconds (UTC)
    idempotent: bool = False
    # Set when ``decision == "approve_for_session"`` — the kernel-assigned
    # UUID for the rule just attached to the session. The frontend uses
    # it to render the "Always for this session" badge on the resolved
    # card and to correlate future ``auto_approved`` events back to
    # their originating pending. ``None`` for every other decision verb.
    rule_id: str | None = None


class SessionMessageRequest(BaseModel):
    prompt: str
    # Hints carried over for backward compatibility with the existing chat UI.
    # The session's model is locked at creation; if these don't match the
    # frozen value the service ignores them rather than failing.
    provider_id: str | None = None
    model_id: str | None = None


class SessionEventsResponse(BaseModel):
    session_id: str
    items: list[SessionEventEnvelope]


class SessionEventWindowResponse(BaseModel):
    """Turn-aligned page of events for the conversation history scroller.

    ``items`` is ordered ASC by seq so the frontend can prepend it
    directly. ``has_more`` is true iff at least one ``user_message`` row
    exists strictly older than ``items[0].seq`` — the cursor for the
    next call. When false, the renderer can hide the scroll-up
    sentinel.
    """

    session_id: str
    items: list[SessionEventEnvelope]
    has_more: bool


@router.get("")
async def list_sessions(
    project_id: str | None = None,
    q: str | None = None,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> dict[str, list[SessionListItem]]:
    return {"sessions": await svc.list_sessions(project_id=project_id, query=q, user_id=user_id)}


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionDetail:
    return await svc.get_session(session_id, user_id=user_id)


@router.post("", status_code=201)
async def create_session(
    body: SessionCreateRequest,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionDetail:
    return await svc.create_session(
        body.project_id,
        title=body.title,
        model_id=body.model_id,
        provider_id=body.provider_id,
        runtime_id=body.runtime_id,
        mcp_provider_slugs=body.mcp_provider_slugs,
        permission_mode=body.permission_mode,
        effort=body.effort,
        agent_slug=body.agent_slug,
        worktree=body.worktree,
        user_id=user_id,
    )


@router.get("/{session_id}/events")
async def list_events(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    after_seq: int = 0,
    svc: SessionService = Depends(get_session_service),
) -> SessionEventsResponse:
    items = await svc.list_events(session_id, user_id=user_id, after_seq=after_seq)
    return SessionEventsResponse(session_id=session_id, items=items)


@router.get("/{session_id}/events/window")
async def list_events_window(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    before_seq: int | None = None,
    turn_limit: int = 20,
    svc: SessionService = Depends(get_session_service),
) -> SessionEventWindowResponse:
    """Turn-aligned page of historical events for the conversation scroller.

    Initial conversation load: client passes ``turn_limit=N`` only →
    server returns the most recent N turns' events.

    Scroll-up "load earlier turns": client passes
    ``before_seq=items[0].seq, turn_limit=N`` → server returns the next
    older N turns. Loop terminates when ``has_more=false``.
    """
    items, has_more = await svc.list_events_window(
        session_id,
        user_id=user_id,
        before_seq=before_seq,
        turn_limit=turn_limit,
    )
    return SessionEventWindowResponse(session_id=session_id, items=items, has_more=has_more)


@router.get("/{session_id}/events/stream")
async def subscribe_events(
    session_id: str,
    request: Request,
    after_seq: int = 0,
    user_id: str = Depends(get_current_user_id),
) -> EventSourceResponse:
    """Reconnectable SSE subscription for session events.

    Bridges the kernel V5 in-memory broadcast (token-level
    ``text_delta`` / ``thinking_delta`` deltas — never persisted to the
    DB) plus the DB replay path (everything else, available on
    reconnect via ``?after_seq=<last_seen>``). The two paths are unified
    by ``event_sse_adapter.iter_events_sse``: it drains DB events
    newer than the cursor first, subscribes to the live broadcast
    queue, and yields a heartbeat every 15s of silence so proxies
    don't time out.

    Replacing the previous DB-only polling implementation: that polled
    every 300ms which made deltas visibly batch up at the polling
    cadence and dropped ``text_delta`` entirely (the DB sink filters
    them out, so the live broadcast is the only path they reach the
    client).
    """
    # ``request.is_disconnected`` is async and ``iter_events_sse`` calls
    # the predicate synchronously — passing it would always-truthy the
    # coroutine object and abort the loop on the first iteration. We
    # rely on ``sse-starlette`` cancelling the generator when the
    # client drops, which fires the ``finally`` block in
    # ``iter_events_sse`` and unsubscribes cleanly.
    del request  # disconnect handled by EventSourceResponse cancel scope
    return EventSourceResponse(iter_events_sse(session_id, user_id=user_id, after_seq=after_seq))


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str,
    body: SessionMessageRequest,
    svc: SessionService = Depends(get_session_service),
    user_id: str = Depends(get_current_user_id),
) -> SessionDetail:
    """Start agent execution in background. Returns immediately with running status."""
    try:
        return await svc.send_message(
            session_id,
            body.prompt,
            provider_id=body.provider_id,
            model_id=body.model_id,
            user_id=user_id,
        )
    except BudgetExceeded as exc:
        # The session service runs a channel-aware wallet pre-check before the
        # turn. Surface its rejection as a 402 carrying the overlay's i18n
        # ``key`` (+ ``params``) for the client to render; ``message`` is the
        # no-translation fallback. (The global ValuzError handler returns 400
        # and drops ``message_key``, so map it explicitly here.)
        raise HTTPException(
            status_code=402,
            detail={
                "message_key": exc.message_key,
                "message_params": exc.message_params,
                "message": exc.message or "Budget exceeded",
            },
        ) from exc


@router.post("/{session_id}/messages/sync")
async def send_message_sync(
    session_id: str,
    body: SessionMessageRequest,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionRunResponse:
    """Synchronous variant — blocks until execution completes. For tests."""
    return await svc.send_message_sync(session_id, body.prompt, user_id=user_id)


@router.post("/{session_id}/interrupt")
async def interrupt(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionDetail:
    return await svc.interrupt(session_id, user_id=user_id)


class QueuedInputCreate(BaseModel):
    prompt: str
    provider_id: str | None = None
    model_id: str | None = None


class QueuedInputPatch(BaseModel):
    prompt: str


@router.get("/{session_id}/queue")
async def list_session_queue(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> QueuedInputList:
    """List a session's queued follow-up inputs (FIFO) + paused flag."""
    return await svc.list_queue(session_id, user_id=user_id)


@router.post("/{session_id}/queue")
async def enqueue_session_input(
    session_id: str,
    body: QueuedInputCreate,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> QueuedInputList:
    """Enqueue a follow-up input. Drains immediately if idle, else after the
    active turn (docs/design/session-input-queue.md)."""
    return await svc.enqueue(
        session_id,
        body.prompt,
        provider_id=body.provider_id,
        model_id=body.model_id,
        user_id=user_id,
    )


@router.patch("/{session_id}/queue/{queue_id}")
async def edit_session_queued_input(
    session_id: str,
    queue_id: str,
    body: QueuedInputPatch,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> QueuedInputList:
    return await svc.edit_queued(session_id, queue_id, body.prompt, user_id=user_id)


@router.delete("/{session_id}/queue/{queue_id}")
async def delete_session_queued_input(
    session_id: str,
    queue_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> QueuedInputList:
    return await svc.delete_queued(session_id, queue_id, user_id=user_id)


@router.post("/{session_id}/queue/resume")
async def resume_session_queue(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> QueuedInputList:
    """Resume draining a queue paused by an interrupt."""
    return await svc.resume_queue(session_id, user_id=user_id)


@router.post("/{session_id}/queue/{queue_id}/steer")
async def steer_session_queued_input(
    session_id: str,
    queue_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> QueuedInputList:
    """Steer — send a queued input now, silently interrupting the active turn."""
    return await svc.steer_queued(session_id, queue_id, user_id=user_id)


@router.post("/{session_id}/cancel")
async def cancel(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionDetail:
    return await svc.cancel(session_id, user_id=user_id)


@router.post("/{session_id}/regenerate")
async def regenerate(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionDetail:
    """Regenerate re-sends the last user message. Returns immediately."""
    return await svc.regenerate(session_id, user_id=user_id)


@router.patch("/{session_id}")
async def rename_session(
    session_id: str,
    name: str,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionDetail:
    return await svc.rename_session(session_id, name, user_id=user_id)


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> None:
    await svc.delete_session(session_id, user_id=user_id)


@router.patch("/{session_id}/permission-mode")
async def update_permission_mode(
    session_id: str,
    body: SessionPermissionModeRequest,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionDetail:
    """Change the approval mode for an existing session.

    Live-reconcile (kernel V5+bba3014): the new mode applies on the
    next Send. Claude uses ``client.set_permission_mode`` (and
    fork-on-rebuild for the bypass tier — G1/G2 CLI gotchas); Codex
    threads ``approval_policy`` / ``sandbox_policy`` per-turn through
    ``turn_kwargs``; DeepAgents drops its cached graph for a cold
    rebuild. A turn already in flight keeps the mode it started with.

    DeepAgents sessions reject ``auto_review`` (kernel constraint —
    only the Claude tier ships the LLM classifier today).
    """
    try:
        return await svc.set_permission_mode(session_id, body.permission_mode, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{session_id}/effort")
async def update_session_effort(
    session_id: str,
    body: SessionEffortRequest,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionDetail:
    """Change the reasoning-effort budget for an existing session.

    Live-reconcile (kernel V5+bba3014): the new effort applies on the
    next Send. Claude cold-reloads the SDK client (effort is a
    build-time option); Codex drops it into ``turn_kwargs
    .reasoning_effort`` (survives ``--resume``); DeepAgents drops its
    cached graph so the next turn rebuilds the langchain chat client
    with the new ``reasoning_effort`` / ``thinking_level``.

    Per-runtime mapping:
      * Claude: pass-through all 5 values (``low|medium|high|xhigh|max``).
      * Codex: ``max`` clamps to ``xhigh``.
      * DeepAgents openai_completion: ``max`` → ``xhigh``.
      * DeepAgents anthropic: pass-through.
      * DeepAgents gemini: ``xhigh|max`` → ``high``.

    ``effort=null`` resets to the SDK default.
    """
    try:
        return await svc.set_session_effort(session_id, body.effort, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{session_id}/actions")
async def submit_session_action(
    session_id: str,
    body: SessionActionRequest,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionActionResponse:
    """Resolve a pending ``requires_action`` with a user decision.

    Thin façade over the kernel orchestrator's ``submit_action``. The
    kernel also exposes this under ``POST {KERNEL_API_PREFIX}/v1/sessions/{id}/actions``
    (ADR-013; default ``/kernel`` for this host, kernel's own upstream
    default ``/api`` — see ``valuz_agent.boot.kernel.kernel_api_prefix``)
    — both routes reach the same orchestrator instance and same
    persistence path. Keeping the ``/v1/...`` shape means the frontend
    can talk to one prefix for everything session-related.

    Validation invariants:
      - ``answer`` ⇔ ``answers``  (enforced by ``SessionActionRequest.
        _enforce_decision_payload_invariants``)
      - ``approve_with_changes`` ⇔ ``modified_input``  (same)
      - subject ↔ decision compatibility (e.g. ``clarifying_questions``
        only accepts ``answer`` / ``reject``; ``approve_for_session``
        requires a runtime that advertised it) is enforced inside
        ``orchestrator.submit_action``; that error becomes a 400 here.
    """

    from valuz_agent.adapters.kernel_client import KernelClientError

    try:
        result = await svc.submit_action(
            session_id,
            pending_id=body.pending_id,
            decision=body.decision,
            message=body.message,
            answers=body.answers,
            modified_input=body.modified_input,
            user_id=user_id,
        )
    except KernelClientError as exc:
        # The kernel seam already shaped the error HTTP-wise (the kernel
        # route mapped its orchestrator exceptions); re-surface verbatim.
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    rule_id_raw = result.get("rule_id")
    return SessionActionResponse(
        session_id=session_id,
        pending_id=str(result["pending_id"]),
        decision=str(result["decision"]),
        accepted_at=int(result["accepted_at"]),  # type: ignore[call-overload]
        idempotent=bool(result["idempotent"]),
        rule_id=str(rule_id_raw) if isinstance(rule_id_raw, str) else None,
    )


class SessionExtraSkillsRequest(BaseModel):
    skill_ids: list[str]


class SessionExtraSkillsResponse(BaseModel):
    skill_ids: list[str]


@router.get("/{session_id}/skills")
async def get_session_extra_skills(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionExtraSkillsResponse:
    return SessionExtraSkillsResponse(
        skill_ids=await svc.get_extra_skills(session_id, user_id=user_id),
    )


@router.put("/{session_id}/skills")
async def set_session_extra_skills(
    session_id: str,
    body: SessionExtraSkillsRequest,
    user_id: str = Depends(get_current_user_id),
    svc: SessionService = Depends(get_session_service),
) -> SessionExtraSkillsResponse:
    """Replace the per-session list of attached skills.

    skill-creator is always active and does not need to be listed here.
    """
    await svc.set_extra_skills(session_id, body.skill_ids, user_id=user_id)
    return SessionExtraSkillsResponse(
        skill_ids=await svc.get_extra_skills(session_id, user_id=user_id),
    )


# ──────────────────────────────────────────────────────────────────────
# Attachments
# ──────────────────────────────────────────────────────────────────────


class AttachmentItem(BaseModel):
    id: str
    session_id: str
    filename: str
    stored_path: str  # absolute path the agent can Read directly
    parsed_path: str | None = None  # parsed markdown for agent attachment
    parse_status: str = "uploaded"
    size_bytes: int
    mime_type: str | None
    created_at: int
    # ``local`` (multipart upload) vs ``kb_doc`` (live reference to a
    # global KB document). The UI uses this to pick an icon and
    # decide whether to render a "from KB <name>" source label; the
    # backend uses it to route the delete path (unlink for local,
    # row-only for KB-sourced).
    source_kind: str = "local"
    source_kb_id: str | None = None
    source_kb_doc_id: str | None = None
    # Per-turn lifecycle marker. ``None`` = pending (staged for the
    # next turn); a timestamp = already shipped with a turn. The
    # panel's "uploaded files" section renders every row as session
    # history; the composer's staging chips + the upload-cap count
    # render only the pending (``consumed_at is None``) subset.
    consumed_at: int | None = None


class AttachmentListResponse(BaseModel):
    items: list[AttachmentItem]


class AddKbAttachmentsRequest(BaseModel):
    """Body of ``POST /v1/sessions/{id}/attachments/kb``.

    ``doc_ids`` is the set the user just confirmed in the picker;
    duplicates already attached to the session are silently dropped
    server-side so the panel doesn't sprout repeats when the user
    re-opens the picker.
    """

    doc_ids: list[str]


def _row_to_item(row: SessionAttachmentRow) -> AttachmentItem:
    return AttachmentItem(
        id=row.id,
        session_id=row.session_id,
        filename=row.filename,
        stored_path=row.stored_path,
        parsed_path=row.parsed_path,
        parse_status=row.parse_status,
        size_bytes=row.size_bytes,
        mime_type=row.mime_type,
        created_at=row.created_at,
        source_kind=row.source_kind,
        source_kb_id=row.source_kb_id,
        source_kb_doc_id=row.source_kb_doc_id,
        consumed_at=row.consumed_at,
    )


@router.get("/{session_id}/attachments")
async def list_attachments(
    session_id: str,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> AttachmentListResponse:
    """Return every attachment ever uploaded to ``session_id``.

    Includes consumed rows — the frontend panel renders the full
    session history of uploaded files, while the composer's staging
    chips + the upload cap filter to ``consumed_at is None`` (pending)
    client-side. The runtime path uses ``_load_pending_attachments``
    instead, which is pending-only.
    """
    if await data_reader().get_session(user_id, session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    rows = await SessionDatastore(db).list_attachments(user_id, session_id, include_consumed=True)
    return AttachmentListResponse(items=[_row_to_item(r) for r in rows])


@router.post("/{session_id}/attachments", status_code=201)
async def upload_attachment(
    session_id: str,
    file: UploadFile,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> AttachmentItem:
    """Stream-write *file* into the session's attachment dir and persist a row.

    The returned ``stored_path`` is an absolute filesystem path that becomes the
    kernel's ``UserMessage.attachments[].source_path`` (the original file the
    agent operates on); the parsed markdown extract, when ready, rides along as
    ``parsed_path`` so the agent can ``Read`` text cheaply. The kernel never
    copies bytes — valuz holds the canonical store and the kernel only
    references it.
    """
    if await data_reader().get_session(user_id, session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")

    # Session-wide attachment cap (local + KB-sourced counted together).
    from valuz_agent.infra.config import settings as _settings

    current_count = len(await SessionDatastore(db).list_attachments(user_id, session_id))
    if current_count >= _settings.max_session_attachments:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Session attachment limit reached "
                f"({_settings.max_session_attachments}); remove a file before "
                f"uploading another."
            ),
        )

    safe_name = (file.filename or "upload").replace("/", "_").replace("\\", "_")
    # Disambiguate if the same filename is uploaded twice. Don't try to be
    # clever about content hashing — the user can always rename later.
    name = safe_name
    key = f"attachments/{session_id}/{name}"
    if _data_file_path(user_id, key).is_file():
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        i = 1
        while _data_file_path(user_id, key).is_file():
            name = f"{stem}-{i}{suffix}"
            key = f"attachments/{session_id}/{name}"
            i += 1

    data = await file.read()
    size = len(data)
    target = _data_file_path(user_id, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    # Persist the row as ``parsing`` and kick the heavy parse off the event
    # loop in a background task. The parser (PyMuPDF / MarkItDown / RapidOCR)
    # is CPU/IO-heavy and fully synchronous — running it inline froze the
    # whole single-threaded server for the parse's duration (every other
    # request / SSE stream stalled). The upload now returns at once; the
    # frontend polls ``GET .../attachments`` until ``parse_status`` flips to
    # ``ready`` / ``failed``. A turn sent before parsing finishes falls back
    # to the raw ``stored_path`` (see ``_attachment_paths`` and the
    # additional-context builder).
    row = SessionAttachmentRow(
        session_id=session_id,
        filename=file.filename or name,
        stored_path=key,
        parsed_path=None,
        parse_status="parsing",
        size_bytes=size,
        mime_type=file.content_type,
        source_kind="local",
    )
    await SessionDatastore(db).create_attachment(user_id, row)
    await db.refresh(row)
    _spawn_attachment_parse(row.id, key, session_id, name, user_id)
    return _row_to_item(row)


def _write_parse_result(
    result: Any, session_id: str, base_name: str, user_id: str
) -> tuple[str | None, str, str | None, str | None]:
    """Write a ``ParseResult``'s markdown into ``dest_dir`` as
    ``{base_name}.parsed.md`` and classify the outcome.

    Returns ``(parsed_path, parse_status, engine, error_message)``:
    - ``("…/x.parsed.md", "ready", <plugin/engine>, None)`` when the parser
      produced real markdown and reported no error.
    - ``(None, "failed", <plugin/engine>, <reason>)`` when there is no markdown
      OR the result carries ``metadata["error"]`` (unsupported file, parser
      failure, or a fallback-disabled cloud failure). Callers fall back to the
      raw ``stored_path`` so the agent at least sees the original file.

    ``engine`` records WHICH parser ran (``metadata["plugin_id"]`` — e.g.
    ``mineru`` / ``paddleocr`` / ``light_local`` — falling back to the
    per-format ``engine`` label) for provenance on the attachment row.

    ``error_message`` carries the parser-reported reason on the failure path.
    Parsers signal failure by RETURNING a ``ParseResult`` with
    ``metadata["error"]`` (they don't raise — see ``ParserRouter`` /
    ``LightLocalParser``), so without surfacing it here the real cause — e.g.
    ``"model not found at …/magika/…"`` from a frozen build missing magika's
    model data — was discarded with the markdown, leaving the attachment row's
    ``error_message`` NULL and the failure undiagnosable from the DB.
    """
    meta = dict(getattr(result, "metadata", None) or {})
    engine = meta.get("plugin_id") or meta.get("engine")
    markdown = getattr(result, "markdown", "") or ""
    error = meta.get("error")
    if not markdown or error:
        reason = str(error) if error else "parser produced no content"
        return None, "failed", engine, reason[:2000]
    parsed_key = f"attachments/{session_id}/{base_name}.parsed.md"
    i = 1
    while _data_file_path(user_id, parsed_key).is_file():
        parsed_key = f"attachments/{session_id}/{base_name}-{i}.parsed.md"
        i += 1
    target = _data_file_path(user_id, parsed_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    return parsed_key, "ready", engine, None


async def _build_attachment_parser(db: Any, user_id: str) -> Any:
    """Build the configured ``ParserRouter`` for an attachment parse.

    Thin indirection over ``deps.build_parser_router`` so tests can monkeypatch
    a stub router without standing up the full settings/registry stack.
    """
    from valuz_agent.api.deps import build_parser_router

    return await build_parser_router(db, user_id)


# Strong refs to in-flight parse tasks. ``asyncio`` only holds weak refs to
# bare tasks, so without this set a fire-and-forget parse could be GC'd
# mid-run. Discarded automatically on completion.
_PARSE_TASKS: set[asyncio.Task[None]] = set()

# Cap concurrent *local* (SYNC) parses. ``to_thread`` keeps a single parse off
# the event loop, but a CPU-bound parser (pymupdf4llm / markitdown) holds the
# GIL in stretches, so N simultaneous parses (e.g. the user re-uploading the
# same heavy PDF several times) would still starve the loop. Bounding the
# worker threads to a small number keeps the loop getting fair time slices.
# ASYNC_POLL (cloud) parses are I/O-bound and intentionally NOT gated by this —
# they only await the network, and serializing them would needlessly stall a
# multi-file upload behind a minutes-long cloud job.
_LOCAL_PARSE_SEMAPHORE = asyncio.Semaphore(2)


# File types a vision-capable runtime can read directly. When the local parser
# can't text-extract one of these (no OCR / enhanced model authorized), that is
# NOT a failure — the raw file is handed to the runtime, which ingests it
# natively. The parse is reclassified ``failed`` → ``native`` so the UI shows a
# calm "model reads it" hint instead of a scary "Failed", and the turn still
# ships the source file (``_attachment_specs`` always carries ``source_path``).
_RUNTIME_NATIVE_EXTS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic", ".heif"}
)


def _is_runtime_native(path: str) -> bool:
    """True for files a vision runtime reads directly — a local-parse miss on
    these is a passthrough (status ``native``), not a failure."""
    return Path(path).suffix.lower() in _RUNTIME_NATIVE_EXTS


def _spawn_attachment_parse(
    attachment_id: str, source: str, session_id: str, base_name: str, user_id: str
) -> None:
    """Parse ``source_path`` through the CONFIGURED parser and persist it.

    Fire-and-forget: the upload route has already returned the row as
    ``parse_status="parsing"``. This runs on the MAIN event loop (created via
    ``asyncio.create_task``) and routes the file through the same
    ``ParserRouter`` KB/Docs ingestion uses, so a user who configured MinerU /
    PaddleOCR gets that engine for conversation attachments — not hardcoded
    light parsing.

    Off-loop dispatch is MODE-AWARE (the load-bearing detail):
    - ASYNC_POLL backends (MinerU / PaddleOCR) submit to the ``PollingScheduler``
      and await a future whose tick lives on THIS (main) loop, so we
      ``await router.parse(...)`` directly. Driving them via
      ``to_thread(parse_sync)`` would ``asyncio.run`` a loop disconnected from
      the scheduler and hang.
    - SYNC backends (LightLocal) do heavy in-process CPU/IO, so we push them off
      the loop via ``await asyncio.to_thread(router.parse_sync, ...)`` — keeping
      the single-threaded server responsive (the whole point of the async
      upload model).

    Every failure mode is contained so a parse crash can never take down the
    event loop or strand the row in ``parsing`` (the poller would spin forever).
    """

    async def _run() -> None:
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.ports.parser_plugin import ParserPluginMode

        parsed_path: str | None = None
        parse_status = "failed"
        error_message: str | None = None
        engine: str | None = None
        try:
            # Build the router in its OWN fresh session — the request's session
            # is closed by the time this background task runs.
            async with async_unit_of_work() as db:
                router = await _build_attachment_parser(db, user_id)
            from valuz_agent.modules.sessions.attachments import _resolve_file_key_path

            src_path = _resolve_file_key_path(user_id, source)
            if src_path is None:
                raise FileNotFoundError(f"attachment source not found: {source}")
            if router.plugin_mode_for(src_path) == ParserPluginMode.ASYNC_POLL:
                result = await router.parse(src_path)
            else:
                # Bound concurrent CPU-bound local parses (see semaphore note).
                async with _LOCAL_PARSE_SEMAPHORE:
                    result = await asyncio.to_thread(router.parse_sync, src_path)
            parsed_path, parse_status, engine, error_message = _write_parse_result(
                result, session_id, base_name, user_id
            )
        except Exception as exc:  # noqa: BLE001 — contain; never crash the loop
            logger.exception("Background parse failed for attachment %s", attachment_id)
            error_message = str(exc)
        # Image (and other vision-readable) files the local parser couldn't
        # text-extract are handed off to the runtime, which reads them
        # natively — not a failure. ``parsed_path`` stays None (no text
        # extract); only the original ships, which is exactly what the runtime
        # needs.
        if parse_status == "failed" and _is_runtime_native(base_name):
            parse_status = "native"
            error_message = None
        try:
            async with async_unit_of_work() as db:
                await SessionDatastore(db).update_attachment_parse(
                    attachment_id,
                    parsed_path=parsed_path,
                    parse_status=parse_status,
                    parse_mode=engine,
                    error_message=error_message,
                )
        except Exception:  # noqa: BLE001 — persistence best-effort
            logger.exception("Failed to persist parse result for attachment %s", attachment_id)

    task = asyncio.create_task(_run())
    _PARSE_TASKS.add(task)
    task.add_done_callback(_PARSE_TASKS.discard)


@router.post("/{session_id}/attachments/kb", status_code=201)
async def add_kb_attachments(
    session_id: str,
    body: AddKbAttachmentsRequest,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> AttachmentListResponse:
    """Attach one or more KB documents to the session.

    Each ``doc_id`` becomes a ``SessionAttachmentRow`` with
    ``source_kind="kb_doc"``. KB picks go through the **same** async
    parse pipeline as local uploads (``_spawn_attachment_parse``): the
    row is created ``parse_status="parsing"`` and a background task runs
    the KB document's ``source_path`` through the configured
    ``ParserRouter`` (MinerU / PaddleOCR / LightLocal), writing the
    markdown extract into the session's own attachment directory. So the
    agent reads a uniformly-parsed ``.parsed.md`` in the session dir —
    exactly like a local upload — rather than the KB's global
    ``docs/preview/`` artifact (which may have been produced by a
    different, KB-routed parser).

    ``stored_path`` still points at the KB's deterministic
    ``source_path`` — the raw file is never copied, only the parsed
    derivative lands in the session dir.

    Idempotent: a doc already attached to this session is skipped,
    matching the panel's "this doc is already attached" UX. Missing
    docs return 400 with the offending id so the picker can surface
    the conflict instead of silently dropping the selection.
    """
    if await data_reader().get_session(user_id, session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    if not body.doc_ids:
        # Empty list is a no-op (picker confirmed with nothing
        # selected) — return the current attachment list instead of
        # erroring so the frontend doesn't need to special-case.
        rows = await SessionDatastore(db).list_attachments(user_id, session_id)
        return AttachmentListResponse(items=[_row_to_item(r) for r in rows])

    ds = SessionDatastore(db)
    existing = await ds.list_attachments(user_id, session_id)
    already_attached = {r.source_kb_doc_id for r in existing if r.source_kind == "kb_doc"}

    from valuz_agent.infra.config import settings as _settings
    from valuz_agent.modules.docs.datastore import DocumentDatastore

    # Session-wide attachment cap (local + KB-sourced together). Count
    # only the ids that would actually create new rows — re-picking a
    # doc that's already attached is a no-op and shouldn't eat budget.
    net_new = [d for d in body.doc_ids if d not in already_attached]
    if len(existing) + len(net_new) > _settings.max_session_attachments:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Session attachment limit reached "
                f"({_settings.max_session_attachments}); the selection "
                f"would add {len(net_new)} to an existing {len(existing)}. "
                f"Remove some files first."
            ),
        )

    doc_ds = DocumentDatastore(db)
    for doc_id in body.doc_ids:
        if doc_id in already_attached:
            continue
        doc = await doc_ds.get_by_id(user_id, doc_id)
        if doc is None:
            raise HTTPException(
                status_code=400,
                detail=f"Knowledge-base document {doc_id!r} not found",
            )
        # Parse the KB document's source file the same way a local
        # upload is parsed — uniform ``LightLocalParser`` output
        # written into the session's own attachment dir. The raw
        # file is NOT copied: ``stored_path`` stays pointed at the
        # KB's deterministic ``source_path`` (also the fallback the
        # agent reads when the parse yields nothing).
        safe_name = (doc.source_filename or doc_id).replace("/", "_").replace("\\", "_")
        # Persist as ``parsing`` and parse in the background — see
        # ``upload_attachment`` for why parsing must never run inline (it
        # blocks the whole event loop). The poller flips the row to
        # ``ready`` / ``failed`` once the background task finishes.
        row = SessionAttachmentRow(
            session_id=session_id,
            filename=doc.source_filename,
            stored_path=doc.source_path,
            parsed_path=None,
            parse_status="parsing",
            size_bytes=doc.file_size_bytes or 0,
            mime_type=doc.mime_type,
            source_kind="kb_doc",
            source_kb_id=doc.kb_id,
            source_kb_doc_id=doc.id,
        )
        await ds.create_attachment(user_id, row)
        await db.refresh(row)
        _spawn_attachment_parse(row.id, doc.source_path, session_id, safe_name, user_id)

    rows = await ds.list_attachments(user_id, session_id)
    return AttachmentListResponse(items=[_row_to_item(r) for r in rows])


@router.delete("/{session_id}/attachments/{attachment_id}", status_code=204)
async def delete_attachment(
    session_id: str,
    attachment_id: str,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Remove a session attachment.

    Only ever unlinks files the **session** owns under
    ``attachment_dir(session_id)``:

    - ``source_kind="local"`` — both ``stored_path`` (the raw upload)
      and ``parsed_path`` (its ``.parsed.md`` sidecar) live in the
      session dir, so both are unlinked.
    - ``source_kind="kb_doc"`` — ``stored_path`` points at the KB's
      ``source_path`` (KB-owned, **never** touched); ``parsed_path``
      is the session-dir ``.parsed.md`` we produced from it, so that
      one *is* unlinked.

    Missing files are tolerated to keep delete idempotent across the
    "user manually rm'd the upload dir" case.
    """
    import os

    if await data_reader().get_session(user_id, session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    ds = SessionDatastore(db)
    row = await ds.get_attachment(user_id, attachment_id)
    if row is None or row.session_id != session_id:
        raise HTTPException(status_code=404, detail=f"Attachment {attachment_id!r} not found")
    # Local rows own both paths; KB rows own only the parsed
    # derivative — their ``stored_path`` is a KB-owned source file.

    owned_paths = (
        (row.stored_path, row.parsed_path) if row.source_kind == "local" else (row.parsed_path,)
    )
    for ref in owned_paths:
        if not ref:
            continue
        if os.path.isabs(ref):
            # Legacy absolute path (or a kb_doc's KB-owned source, which never
            # reaches here) — delete from the local filesystem as before.
            try:
                os.unlink(ref)
            except FileNotFoundError:
                pass
            except OSError:
                logger.exception("Failed to unlink attachment file %s", ref)
        else:
            try:
                _data_file_path(user_id, ref).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                logger.exception("Failed to unlink attachment file %s", ref)
    await ds.delete_attachment(user_id, attachment_id)
    return Response(status_code=204)


# ──────────────────────────────────────────────────────────────────────
# Artifacts (agent-delivered deliverables — the "生成文件" list)
# ──────────────────────────────────────────────────────────────────────


class ArtifactItem(BaseModel):
    """One delivered artifact, at the version this session produced.

    Field names are unchanged from when a delivery was a single mutable row, so
    existing clients keep working; ``file_path`` now points at the immutable
    snapshot rather than the agent's working copy, which is the same absolute
    path the model was handed and the same one ``ref`` wraps.
    """

    id: str  # revision id — a version, not a deliverable
    session_id: str
    file_path: str  # absolute path of THIS version's snapshot
    # Stable file identity — the client passes this to POST /v1/files/resolve to
    # get an access address (local path or signed URL). Derived from file_path;
    # not stored. See docs/design/file-address-resolution.md.
    ref: str
    file_name: str
    file_size: int
    mime_type: str | None = None
    created_at: int
    # Version coordinates. The panel shows "v3" and can offer the history.
    artifact_id: str
    version_no: int
    is_current: bool


class ArtifactListResponse(BaseModel):
    items: list[ArtifactItem]


def _artifact_item(
    revision: ArtifactRevisionRow,
    content: ArtifactContentRow | None,
    *,
    session_id: str,
    is_current: bool,
) -> ArtifactItem:
    path = revision.abs_path or ""
    return ArtifactItem(
        id=revision.id,
        session_id=session_id,
        file_path=path,
        ref=build_valuz_file_uri(path) if path else "",
        file_name=revision.file_name,
        file_size=content.byte_size if content else 0,
        mime_type=content.mime_type if content else None,
        created_at=revision.created_at,
        artifact_id=revision.artifact_id,
        version_no=revision.version_no,
        is_current=is_current,
    )


@router.get("/{session_id}/artifacts")
async def list_artifacts(
    session_id: str,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> ArtifactListResponse:
    """Return the deliverables this session produced, one row each.

    Session-scoped on purpose: the panel answers "what did this conversation
    produce", so a revision another session made to the same deliverable does
    not appear here. ``is_current`` distinguishes a version that is still the
    latest from one that has since been superseded — without it the panel would
    present a stale version as the deliverable.

    One row per *deliverable*, not per revision. A session that delivered the
    same file three times produced one thing, three times — listing it three
    times reads as three files, and now that the panel can expand a version
    history, it would also show that history once per row. The row carries the
    latest version THIS session produced.
    """
    if await data_reader().get_session(user_id, session_id) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id!r} not found")
    ds = ArtifactDatastore(db)
    revisions = await ds.list_session_revisions(user_id, session_id)
    # Oldest first from the datastore, so the last write per artifact wins and
    # each deliverable ends up on its newest version from this session.
    latest = {rev.artifact_id: rev for rev in revisions}
    ordered = sorted(latest.values(), key=lambda rev: rev.created_at)

    # Two queries for the whole list rather than two per row — the panel reloads
    # this on every turn end.
    heads = await ds.get_heads(user_id, [rev.artifact_id for rev in ordered])
    contents = await ds.get_contents(user_id, [rev.content_id for rev in ordered])

    return ArtifactListResponse(
        items=[
            _artifact_item(
                revision,
                contents.get(revision.content_id),
                session_id=session_id,
                is_current=(
                    (head := heads.get(revision.artifact_id)) is not None
                    and head.revision_id == revision.id
                ),
            )
            for revision in ordered
        ]
    )

"""Session CRUD routes — /api/v1/sessions."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app._validators import validate_mcp_servers, validate_registered_tools, validate_skills
from app.dependencies import get_orchestrator, get_store
from app.serializers import (
    agent_config_from_schema as _agent_config_from_schema,
)
from app.serializers import (
    event_to_data as _event_to_data,
)
from app.serializers import (
    session_to_data as _session_to_data,
)
from app.schemas import (
    AppendEventData,
    AppendEventResponse,
    CreateSessionRequest,
    EventPayload,
    FinalizeSessionRequest,
    DataResponse,
    EventListResponse,
    ModelProviderInputSchema,
    ModelProviderUpdateSchema,
    ModelSettingsSchema,
    SessionListResponse,
    SessionResponse,
    SetSessionModeRequest,
    SubmitActionData,
    SubmitActionRequest,
    SubmitActionResponse,
    UpdateSessionRequest,
)
from src.core import (
    Event,
    ModelProvider,
    ModelSettings,
    Session,
    StorePort,
)
from src.core.orchestrator import (
    ApprovalNotImplementedError,
    PendingActionConflictError,
    PendingActionDecisionMismatchError,
    PendingActionExpiredError,
    PendingActionNotFoundError,
    RuntimeUnavailableError,
    SessionNotFoundError,
    SessionOrchestrator,
)
from src.runtimes.factory import validate_api_protocol

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

StoreDep = Annotated[StorePort, Depends(get_store)]
OrchestratorDep = Annotated[SessionOrchestrator, Depends(get_orchestrator)]


@router.post("", status_code=201, response_model=SessionResponse)
async def create_session(
    body: CreateSessionRequest,
    store: StoreDep,
) -> dict[str, Any]:
    if not body.cwd.strip():
        raise HTTPException(status_code=400, detail="cwd is required and must be non-empty.")
    if not body.agent_config.name.strip():
        raise HTTPException(status_code=400, detail="agent_config.name must not be empty.")
    agent = _agent_config_from_schema(body.agent_config)
    validate_registered_tools(list(agent.tools))

    # DeepAgents needs an explicit langchain model client at runtime, so
    # both ``model`` and ``model_provider`` are required when chosen.
    # ClaudeAgent / Codex fall back to ambient SDK credentials and accept
    # both fields empty.
    if body.runtime_provider == "deepagents":
        if not body.model.strip():
            raise HTTPException(
                status_code=400,
                detail="model is required when runtime_provider is 'deepagents'.",
            )
        if body.model_provider is None:
            raise HTTPException(
                status_code=400,
                detail="model_provider is required when runtime_provider is 'deepagents'.",
            )

    # ``permission_mode`` is sunk to the session per D9: agent holds the
    # default; createSession prefills from agent when the request omits the
    # field; runtime reads ``session.permission_mode`` thereafter.
    # ``permission_mode`` is sunk to the session: the embedded snapshot
    # holds the default; the request value wins when provided.
    permission_mode = body.permission_mode or agent.permission_mode
    if body.runtime_provider == "deepagents" and permission_mode == "auto_review":
        raise HTTPException(
            status_code=400,
            detail="auto_review is not supported for deepagents; use default or full_access.",
        )

    provider = (
        _validate_model_provider_input(body.model_provider)
        if body.model_provider is not None
        else None
    )
    # Cross-check api_protocol against the chosen runtime — see
    # ``factory.ALLOWED_PROTOCOLS_BY_RUNTIME``. Surfaces mismatches like
    # ``runtime_provider=claude_agent`` + ``api_protocol=gemini`` at
    # session-create time instead of at first turn.
    if provider is not None:
        try:
            validate_api_protocol(body.runtime_provider, provider.api_protocol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings = _model_settings_from_schema(body.model_settings)
    validate_skills(body.skills)
    mcp_configs = validate_mcp_servers(body.mcp_servers)

    session = Session(
        id=body.id or str(uuid.uuid4()),
        agent_config=agent,
        runtime_provider=body.runtime_provider,
        cwd=body.cwd,
        model=body.model,
        model_provider=provider,
        model_settings=settings,
        instructions=body.instructions,
        skills=tuple(body.skills),
        mcp_servers=tuple(mcp_configs),
        permission_mode=permission_mode,
        mode=body.mode,
        metadata=body.metadata,
    )
    await store.save_session(session)
    return {"data": _session_to_data(session)}


def _normalize_base_url(raw: str | None) -> str | None:
    """Empty / whitespace-only ``base_url`` collapses to ``None`` so the
    "first-party fallback" branch in each runtime fires uniformly,
    regardless of whether the field was omitted or sent as an empty
    string by a UI that strips trimmed input on submit."""
    if raw is None:
        return None
    trimmed = raw.strip()
    return trimmed or None


def _validate_model_provider_input(p: ModelProviderInputSchema) -> ModelProvider:
    if not p.api_key.strip():
        raise HTTPException(status_code=400, detail="model_provider.api_key must not be empty.")
    return ModelProvider(
        base_url=_normalize_base_url(p.base_url),
        api_key=p.api_key,
        api_protocol=p.api_protocol,
    )


def _apply_model_provider_update(
    current: ModelProvider | None, patch: ModelProviderUpdateSchema
) -> ModelProvider:
    if patch.api_key is None:
        if current is None:
            raise HTTPException(
                status_code=400,
                detail="model_provider.api_key is required (no existing key to retain).",
            )
        api_key = current.api_key
    else:
        if not patch.api_key.strip():
            raise HTTPException(
                status_code=400,
                detail="model_provider.api_key must be omitted (to retain) or non-empty.",
            )
        api_key = patch.api_key
    return ModelProvider(
        base_url=_normalize_base_url(patch.base_url),
        api_key=api_key,
        api_protocol=patch.api_protocol,
    )


def _model_settings_from_schema(s: ModelSettingsSchema | None) -> ModelSettings | None:
    if s is None:
        return None
    return ModelSettings(temperature=s.temperature, max_tokens=s.max_tokens, effort=s.effort)


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    store: StoreDep,
    status: Annotated[str | None, Query()] = None,
    ids: Annotated[str | None, Query(description="comma-separated session id filter")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    id_list = [i for i in (ids.split(",") if ids else []) if i] if ids is not None else None
    sessions = await store.list_sessions(
        status=status,
        ids=id_list,
        limit=limit,
        offset=offset,
    )
    return {"data": [_session_to_data(s) for s in sessions]}


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    store: StoreDep,
) -> dict[str, Any]:
    session = await store.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"data": _session_to_data(session)}


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    store: StoreDep,
) -> dict[str, Any]:
    session = await store.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if body.instructions is not None:
        session.instructions = body.instructions
    if body.skills is not None:
        validate_skills(body.skills)
        session.skills = tuple(body.skills)
    if body.mcp_servers is not None:
        session.mcp_servers = tuple(validate_mcp_servers(body.mcp_servers))
    if body.model_provider is not None:
        new_provider = _apply_model_provider_update(session.model_provider, body.model_provider)
        # ``runtime_provider`` is immutable on session — re-check the
        # patched ``api_protocol`` against it (the create path enforces
        # the same invariant; mid-session PATCH must too, otherwise the
        # cached runtime would still work but the next cold-reload
        # would fail).
        try:
            validate_api_protocol(session.runtime_provider, new_provider.api_protocol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.model_provider = new_provider
    if body.model_settings is not None:
        session.model_settings = _model_settings_from_schema(body.model_settings)
    if body.permission_mode is not None:
        if session.runtime_provider == "deepagents" and body.permission_mode == "auto_review":
            raise HTTPException(
                status_code=400,
                detail="auto_review is not supported for deepagents; use default or full_access.",
            )
        session.permission_mode = body.permission_mode
    if body.cwd is not None:
        session.cwd = body.cwd
    if body.metadata is not None:
        session.metadata = body.metadata
    await store.save_session(session)
    return {"data": _session_to_data(session)}


@router.post("/{session_id}/mode", response_model=SessionResponse)
async def set_session_mode(
    session_id: str,
    body: SetSessionModeRequest,
    store: StoreDep,
    orchestrator: OrchestratorDep,
) -> dict[str, Any]:
    """Set the session's runtime mode (`default` / `plan` / `goal`).

    Endpoint validates + writes the kernel field, then emits a
    ``mode_changed`` event on the session bus (only on a real transition;
    idempotent same-mode re-sets are silent). Per-runtime side effects
    (slash dispatch on Claude/Codex, auto-exit detection) arrive in
    slices 5–6.

    Validation:

    * 400 — `deepagents` runtime: plan / goal have no native primitive.
    * 422 — `mode` not in `{"default", "plan", "goal"}` (Pydantic).

    Direct ``plan ↔ goal`` transitions are allowed. The runtime
    reconcile (Claude / Codex) is already composed of independent
    exit + entry branches, so leaving the prior mode (``/goal clear``
    on Claude, ``thread/goal/clear`` on Codex, ``set_permission_mode``
    restore on Claude plan) runs in the same reconcile pass as entry
    of the new mode. Same-mode re-set is idempotent (no event).
    """
    session = await store.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if body.mode != "default" and session.runtime_provider == "deepagents":
        raise HTTPException(
            status_code=400,
            detail=(
                f"mode={body.mode!r} is not supported on deepagents sessions "
                "(no native plan/goal primitive)."
            ),
        )

    transitioned = session.mode != body.mode
    session.mode = body.mode
    await store.save_session(session)
    if transitioned:
        await orchestrator.emit_session_event(
            session_id,
            Event(type="mode_changed", data={"mode": body.mode, "by": "user"}),
        )
    return {"data": _session_to_data(session)}


@router.delete("/{session_id}", response_model=DataResponse)
async def delete_session(
    session_id: str,
    store: StoreDep,
) -> dict[str, Any]:
    deleted = await store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"data": None}


@router.post("/{session_id}/events", status_code=201, response_model=AppendEventResponse)
async def append_session_event(
    session_id: str,
    body: EventPayload,
    store: StoreDep,
) -> dict[str, Any]:
    """Append an out-of-band event onto the session's latest message.

    For supervisors that aren't driving a turn (recovery, interrupt
    fallback, after-the-fact detectors). ``persisted=false`` when the
    session has no messages yet to anchor onto (the event is dropped).
    """
    session = await store.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await store.list_messages_for_session(session_id, limit=1)
    if not messages:
        return {"data": AppendEventData(persisted=False)}
    await store.append_event(
        session_id,
        messages[0].id,
        Event(type=body.type, data=body.data),  # type: ignore[arg-type]
    )
    return {"data": AppendEventData(persisted=True)}


@router.post("/{session_id}/finalize", response_model=SessionResponse)
async def finalize_session(
    session_id: str,
    body: FinalizeSessionRequest,
    store: StoreDep,
) -> dict[str, Any]:
    """Flip a session to ``idle``/``terminated`` from outside a turn.

    The supervisor-facing alternative to PATCH (which deliberately cannot
    touch ``status``): boot recovery clears crashed ``running`` rows, the
    interrupt fallback parks a session as idle. Appends ``error_event``
    after the flip when provided. Idempotent on the status flip.
    """
    session = await store.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    from src.core.types import Error as ErrorStop  # type: ignore[import-not-found]
    from src.core.types import UserInterrupt  # type: ignore[import-not-found]

    stop_reason = session.stop_reason
    if body.stop_reason_type == "user_interrupt":
        stop_reason = UserInterrupt()
    elif body.stop_reason_type == "error":
        stop_reason = ErrorStop(message=body.stop_reason_message or "")

    if (
        session.status != body.status
        or body.stop_reason_type is not None
        or body.metadata is not None
    ):
        session = dataclasses.replace(
            session,
            status=body.status,
            stop_reason=stop_reason,
            metadata=body.metadata if body.metadata is not None else session.metadata,
        )
        await store.save_session(session)

    if body.error_event is not None:
        messages = await store.list_messages_for_session(session_id, limit=1)
        if messages:
            await store.append_event(
                session_id,
                messages[0].id,
                Event(type=body.error_event.type, data=body.error_event.data),  # type: ignore[arg-type]
            )
    return {"data": _session_to_data(session)}


@router.get("/{session_id}/events", response_model=EventListResponse)
async def get_session_events(
    session_id: str,
    store: StoreDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    events = await store.get_events(session_id, limit=limit, offset=offset)
    return {"data": [_event_to_data(e) for e in events]}


@router.post("/{session_id}/actions", response_model=SubmitActionResponse)
async def submit_session_action(
    session_id: str,
    body: SubmitActionRequest,
    orchestrator: OrchestratorDep,
) -> dict[str, Any]:
    """Resolve a pending ``requires_action`` event with a decision.

    See OpenAPI / `docs/design/cross-runtime-approval-contract.md` §4.2.
    Idempotent on (pending_id, decision); conflicts (different decision)
    return 409; expired / interrupted pendings return 410; missing
    runtime returns 400; runtime that hasn't yet wired the bridge
    returns 501.
    """
    try:
        result = await orchestrator.submit_action(
            session_id,
            pending_id=body.pending_id,
            decision=body.decision,
            message=body.message,
            answers=body.answers,
            modified_input=body.modified_input,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except PendingActionNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Pending action {body.pending_id} not found"
        ) from exc
    except PendingActionDecisionMismatchError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Pending {exc.pending_id} subject={exc.subject!r} cannot accept "
                f"decision={exc.decision!r}"
            ),
        ) from exc
    except PendingActionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Pending {exc.pending_id} already resolved as "
                f"{exc.previous_decision}; cannot replace with "
                f"{exc.requested_decision}"
            ),
        ) from exc
    except PendingActionExpiredError as exc:
        raise HTTPException(
            status_code=410,
            detail=f"Pending {exc.pending_id} already {exc.reason}; cannot decide",
        ) from exc
    except RuntimeUnavailableError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No live runtime is awaiting decision for session {session_id}; "
                "the turn has ended or the host restarted."
            ),
        ) from exc
    except ApprovalNotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail=f"Runtime has not implemented the approval bridge: {exc}",
        ) from exc

    return {
        "data": SubmitActionData(
            session_id=session_id,
            pending_id=result.pending_id,
            decision=result.decision,
            accepted_at=result.accepted_at,
            idempotent=result.idempotent,
            rule_id=result.rule_id,
        )
    }

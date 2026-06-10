"""Session CRUD routes — /api/v1/sessions."""

from __future__ import annotations

import dataclasses
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app._validators import validate_mcp_servers, validate_registered_tools, validate_skills
from app.dependencies import get_orchestrator, get_store
from app.schemas import (
    AgentConfigSchema,
    CreateSessionRequest,
    DataResponse,
    EventData,
    EventListResponse,
    McpHttpServerConfigSchema,
    McpStdioServerConfigSchema,
    ModelProviderInputSchema,
    ModelProviderResponseSchema,
    ModelProviderUpdateSchema,
    ModelSettingsSchema,
    SessionData,
    SessionListResponse,
    SessionResponse,
    SetSessionModeRequest,
    StopReasonSchema,
    SubmitActionData,
    SubmitActionRequest,
    SubmitActionResponse,
    SubAgentDefSchema,
    TodoItem,
    ToolDefSchema,
    UpdateSessionRequest,
)
from src.core import (
    AgentConfig,
    Event,
    McpServerConfig,
    McpStdioServerConfig,
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


def _mcp_to_schema(
    cfg: McpServerConfig,
) -> McpHttpServerConfigSchema | McpStdioServerConfigSchema:
    if isinstance(cfg, McpStdioServerConfig):
        return McpStdioServerConfigSchema(
            name=cfg.name,
            command=cfg.command,
            args=list(cfg.args),
            env=dict(cfg.env),
            env_vars=list(cfg.env_vars),
        )
    return McpHttpServerConfigSchema(
        name=cfg.name,
        url=cfg.url,
        transport=cfg.transport,
        headers=dict(cfg.headers),
    )


def _agent_config_to_schema(cfg: Any) -> AgentConfigSchema:
    return AgentConfigSchema(
        id=cfg.id,
        name=cfg.name,
        model=cfg.model,
        runtime_provider=cfg.runtime_provider,
        instructions=cfg.instructions,
        permission_mode=cfg.permission_mode,
        max_turns=cfg.max_turns,
        max_cost_usd=cfg.max_cost_usd,
        tools=[
            ToolDefSchema(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
                read_only=t.read_only,
                permission=t.permission,
            )
            for t in cfg.tools
        ],
        callable_agents=[
            SubAgentDefSchema(
                name=a.name,
                description=a.description,
                prompt=a.prompt,
                tools=list(a.tools),
                model=a.model,
                skills=list(a.skills) if a.skills is not None else None,
                metadata=a.metadata,
            )
            for a in cfg.callable_agents
        ],
        skills=list(cfg.skills),
        mcp_servers=[_mcp_to_schema(c) for c in cfg.mcp_servers],
        effort=cfg.effort,
        thinking=cfg.thinking,
        metadata=cfg.metadata,
    )


def _agent_config_from_schema(schema: AgentConfigSchema) -> AgentConfig:
    from src.adapters.sqlalchemy_store.converters import dict_to_agent_config

    cfg = dict_to_agent_config(schema.model_dump())
    assert cfg is not None  # name is required on the schema
    return cfg


def _session_to_data(session: Session) -> SessionData:
    stop_reason = None
    if session.stop_reason is not None:
        sr_dict = dataclasses.asdict(session.stop_reason)
        stop_reason = StopReasonSchema(**sr_dict)
    return SessionData(
        id=session.id,
        agent_config=_agent_config_to_schema(session.agent_config),
        runtime_provider=session.runtime_provider,
        cwd=session.cwd,
        model=session.model,
        model_provider=(
            ModelProviderResponseSchema(
                base_url=session.model_provider.base_url,
                api_protocol=session.model_provider.api_protocol,
            )
            if session.model_provider is not None
            else None
        ),
        model_settings=(
            ModelSettingsSchema(
                temperature=session.model_settings.temperature,
                max_tokens=session.model_settings.max_tokens,
                effort=session.model_settings.effort,
            )
            if session.model_settings is not None
            else None
        ),
        instructions=session.instructions,
        skills=list(session.skills),
        mcp_servers=[_mcp_to_schema(cfg) for cfg in session.mcp_servers],
        permission_mode=session.permission_mode,
        mode=session.mode,
        status=session.status,
        stop_reason=stop_reason,
        created_at=session.created_at,
        metadata=session.metadata,
        runtime_session_id=session.runtime_session_id,
        todos=[TodoItem(**t) for t in session.todos] if session.todos is not None else None,
    )


def _event_to_data(event: Event) -> EventData:
    return EventData(type=event.type, data=event.data, timestamp=event.timestamp)


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
        id=str(uuid.uuid4()),
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
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    sessions = await store.list_sessions(
        status=status,
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

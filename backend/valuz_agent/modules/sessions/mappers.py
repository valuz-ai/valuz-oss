"""Pure mappers + coercers between kernel ``Session`` objects and valuz DTOs.

Stateless, no DB/IO — kernel domain object in, valuz view DTO (or a
validated scalar) out. Lives below ``service`` so the god module shrinks to
orchestration logic; ``service`` and the route layer both import from here.
"""

from __future__ import annotations

from src.core.types import (  # type: ignore[import-not-found]
    Session as KernelSession,
)

# Side-effect: puts the kernel on sys.path so ``src.core`` resolves.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.modules.sessions.dto import SessionDetail, SessionListItem, TodoItem
from valuz_agent.modules.sessions.errors import SessionNotFound


def _copy_session(session: KernelSession, /, **overrides: object) -> KernelSession:
    """Shallow-copy a kernel Session with optional field overrides.

    All fields from *session* are preserved unless overridden.  The kernel
    ``Session`` dataclass gains fields over time — centralising construction
    here prevents field-dropping bugs (e.g. dropping ``model_provider`` on
    status-only updates would re-break runtime dispatch). V5+messages
    drops ``total_turns`` / ``total_cost_usd`` (now on Message) and adds
    ``todos`` (latest TodoWrite snapshot) and ``runtime_session_id``.
    ADR-008 (V5+e8d6c87) adds ``instructions`` — the workspace system
    prompt is now session-level state, so dropping it on status copies
    would leave the runtime with an empty prompt mid-session.
    V5+1aae940 (approval contract slice 1) sinks ``permission_mode`` to
    the session — every shallow-copy path MUST forward it or a status
    update silently demotes the session back to ``full_access`` and the
    approval bridge for the next turn never wires.
    """
    from src.core.types import Session as KS  # type: ignore[import-not-found]

    fields: dict[str, object] = {
        "id": session.id,
        "project_id": session.project_id,
        "agent_id": session.agent_id,
        "runtime_provider": getattr(session, "runtime_provider", "claude_agent"),
        "model": session.model,
        "model_provider": session.model_provider,
        "model_settings": session.model_settings,
        "instructions": session.instructions,
        "skills": session.skills,
        "mcp_servers": session.mcp_servers,
        "permission_mode": getattr(session, "permission_mode", "full_access"),
        "status": session.status,
        "stop_reason": session.stop_reason,
        "created_at": session.created_at,
        "metadata": session.metadata,
        "runtime_session_id": getattr(session, "runtime_session_id", None),
        "todos": getattr(session, "todos", None),
    }
    fields.update(overrides)
    return KS(**fields)  # type: ignore[arg-type]


def _valuz_meta(session: KernelSession) -> dict[str, object]:
    return session.metadata.get("valuz") or {}  # type: ignore[return-value]


def _session_to_list_item(session: KernelSession) -> SessionListItem:
    meta = _valuz_meta(session)
    settings = getattr(session, "model_settings", None)
    effort = settings.effort if settings is not None else None
    raw_task_id = meta.get("task_id")
    return SessionListItem(
        id=session.id,
        workspace_id=str(session.project_id),
        name=meta.get("name") or None,  # type: ignore[arg-type]
        status=_map_kernel_status(session.status),
        origin=str(meta.get("origin") or "user"),
        last_user_message_text=meta.get("last_user_message_text") or None,  # type: ignore[arg-type]
        locked_model_id=session.model or None,
        locked_provider_id=meta.get("locked_provider_id") or None,  # type: ignore[arg-type]
        updated_at=session.created_at,
        runtime_provider=getattr(session, "runtime_provider", "deepagents") or "deepagents",
        permission_mode=getattr(session, "permission_mode", "full_access") or "full_access",
        effort=effort,
        task_id=str(raw_task_id) if raw_task_id else None,
    )


def _session_to_detail(session: KernelSession) -> SessionDetail:
    meta = _valuz_meta(session)
    raw_trigger = meta.get("trigger_meta")
    if isinstance(raw_trigger, dict):
        trigger_meta: dict[str, str] | None = {str(k): str(v) for k, v in raw_trigger.items()}
    else:
        trigger_meta = None
    # V5+messages: token/cost roll-up moved to Message rows. Aggregating
    # across messages is a UI concern; surface 0 here and let callers that
    # care fetch the messages list directly.
    raw_todos = getattr(session, "todos", None)
    todos = (
        [
            TodoItem(**{k: v for k, v in t.items() if k in ("content", "status", "activeForm")})
            for t in raw_todos
            if isinstance(t, dict) and "content" in t and "status" in t
        ]
        if isinstance(raw_todos, list)
        else None
    )
    settings = getattr(session, "model_settings", None)
    effort = settings.effort if settings is not None else None
    return SessionDetail(
        id=session.id,
        workspace_id=str(session.project_id),
        name=meta.get("name") or None,  # type: ignore[arg-type]
        status=_map_kernel_status(session.status),
        origin=str(meta.get("origin") or "user"),
        last_user_message_text=meta.get("last_user_message_text") or None,  # type: ignore[arg-type]
        locked_model_id=session.model or None,
        updated_at=session.created_at,
        locked_provider_id=meta.get("locked_provider_id") or None,  # type: ignore[arg-type]
        runtime_provider=getattr(session, "runtime_provider", "deepagents") or "deepagents",
        permission_mode=getattr(session, "permission_mode", "full_access") or "full_access",
        effort=effort,
        total_tokens=0,
        total_cost_usd=0.0,
        created_at=session.created_at,
        trigger_meta=trigger_meta,
        todos=todos,
        instructions=session.instructions or None,
        agent_slug=meta.get("agent_slug") or None,  # type: ignore[arg-type]
    )


def _map_kernel_status(kernel_status: str) -> str:
    """Map kernel session statuses to valuz session statuses.

    Kernel: created | idle | running | terminated
    Valuz:  created | idle | running | failed | cancelled | archived
    """
    return {
        "created": "created",
        "idle": "idle",
        "running": "running",
        "terminated": "failed",
    }.get(kernel_status, kernel_status)


def _kernel_session_not_found(session_id: str) -> SessionNotFound:
    return SessionNotFound(f"Session {session_id!r} not found")


# Kernel V5+1aae940 collapsed the permission-mode enum to a 3-value
# discriminator. Callers that pass in a legacy value (e.g. from a
# stamped-but-not-yet-rendered UI binding, a CLI alias, or a stale
# config file) get coerced to the closest equivalent: every
# bypass-everything spelling lands on ``full_access``; the rest fall
# through to the kernel-side default of ``full_access`` too, since the
# host has no opinion when the caller didn't pick one.
_VALID_SESSION_PERMISSION_MODES = ("default", "auto_review", "full_access")


def _coerce_session_permission_mode(value: str | None) -> str:
    if value in _VALID_SESSION_PERMISSION_MODES:
        return value  # type: ignore[return-value]
    return "full_access"


# Cross-runtime reasoning-budget lever (kernel ``ModelSettings.effort``).
# Mirrors ``src.core.types.EffortLevel``. ``None`` means "let the runtime
# pick its SDK default" — Claude's CLAUDE_CODE_EFFORT default, codex's
# ``model_reasoning_effort`` config default, langchain client defaults.
_VALID_SESSION_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _coerce_session_effort(value: str | None) -> str | None:
    """Validate an effort value against the kernel's 5-value enum.

    Returns the input unchanged on success or ``None``; raises
    ``ValueError`` on an unknown value so the route layer can 400. We
    deliberately don't silently coerce because effort is a user-driven
    knob; quietly downgrading "extreme" to ``None`` would surprise
    operators who set a specific budget.
    """
    if value is None or value == "":
        return None
    if value in _VALID_SESSION_EFFORTS:
        return value
    raise ValueError(
        f"unknown effort {value!r}; expected one of {list(_VALID_SESSION_EFFORTS)} or null"
    )

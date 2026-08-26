"""Pure mappers + coercers between kernel ``Session`` objects and valuz DTOs.

Stateless, no DB/IO — kernel domain object in, valuz view DTO (or a
validated scalar) out. Lives below ``service`` so the god module shrinks to
orchestration logic; ``service`` and the route layer both import from here.
"""

from __future__ import annotations

from src.core.types import (
    Session as KernelSession,
)

# Side-effect: puts the kernel on sys.path so ``src.core`` resolves.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.modules.sessions.dto import (
    SessionDetail,
    SessionListItem,
    TodoItem,
    WorktreeRef,
)
from valuz_agent.modules.sessions.errors import SessionNotFound


def _copy_session(session: KernelSession, /, **overrides: object) -> KernelSession:
    """Shallow-copy a kernel Session with optional field overrides.

    All fields from *session* are preserved unless overridden.  The kernel
    ``Session`` dataclass gains fields over time — centralising construction
    here prevents field-dropping bugs (e.g. dropping ``model_provider`` on
    status-only updates would re-break runtime dispatch). V5+messages
    drops ``total_turns`` / ``total_cost_usd`` (now on Message) and adds
    ``todos`` (latest TodoWrite snapshot) and ``runtime_session_id``.
    ADR-008 (V5+e8d6c87) adds ``instructions`` — the project system
    prompt is now session-level state, so dropping it on status copies
    would leave the runtime with an empty prompt mid-session.
    V5+1aae940 (approval contract slice 1) sinks ``permission_mode`` to
    the session — every shallow-copy path MUST forward it or a status
    update silently demotes the session back to ``full_access`` and the
    approval bridge for the next turn never wires.
    """
    fields: dict[str, object] = {
        "id": session.id,
        "agent_config": session.agent_config,
        "cwd": session.cwd,
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
    return KernelSession(**fields)  # type: ignore[arg-type]


def _valuz_meta(session: KernelSession) -> dict[str, object]:
    return session.metadata.get("valuz") or {}  # type: ignore[return-value]


def _worktree_ref(meta: dict[str, object]) -> WorktreeRef | None:
    raw = meta.get("worktree")
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    path = raw.get("path")
    if not name or not path:
        return None
    branch = raw.get("branch")
    return WorktreeRef(
        name=str(name),
        branch=str(branch) if branch else None,
        path=str(path),
    )


def _task_id(meta: dict[str, object]) -> str | None:
    """The task this session belongs to, or None for a standalone one.

    Shared by BOTH mappers on purpose. It used to be inlined in the list
    mapper only, so ``GET /v1/sessions/{id}`` answered ``task_id: null`` for
    every session including a task's own lead and members — ``SessionDetail``
    inherits the field from ``SessionListItem`` (contract and DTO both), and
    the default filled the gap silently. Anything reading the detail (the
    conversation page holds exactly one session, hydrated from it) therefore
    could not tell a task session apart from a normal one.
    """
    raw = meta.get("task_id")
    return str(raw) if raw else None


def _forked_from_session_id(session: KernelSession) -> str | None:
    """Kernel-stamped fork provenance — metadata TOP level (not ``valuz``:
    the kernel owns the stamp, callers cannot spoof it)."""
    forked_from = (getattr(session, "metadata", None) or {}).get("forked_from")
    if isinstance(forked_from, dict) and forked_from.get("session_id"):
        return str(forked_from["session_id"])
    return None


def _session_to_list_item(session: KernelSession) -> SessionListItem:
    meta = _valuz_meta(session)
    settings = getattr(session, "model_settings", None)
    effort = settings.effort if settings is not None else None
    return SessionListItem(
        id=session.id,
        project_id=str(meta.get("project_id") or ""),
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
        mode=getattr(session, "mode", "default") or "default",
        task_id=_task_id(meta),
        worktree=_worktree_ref(meta),
        forked_from_session_id=_forked_from_session_id(session),
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
    todos: list[TodoItem] | None
    if isinstance(raw_todos, list):
        # The kernel-client seam is wire-schema typed: ``session.todos`` items
        # arrive as kernel ``TodoItem`` pydantic models, not the domain dicts
        # this mapper was written against. The old ``isinstance(t, dict)``
        # filter silently dropped every model item, so the detail endpoint
        # returned ``todos: []`` for sessions whose DB row had todos — the
        # panel's "detail hydrate" then wiped a good window/live snapshot with
        # an empty list on warm re-opens. Accept both shapes.
        todos = []
        for t in raw_todos:
            item = (
                t if isinstance(t, dict) else (t.model_dump() if hasattr(t, "model_dump") else None)
            )
            if isinstance(item, dict) and "content" in item and "status" in item:
                todos.append(
                    TodoItem(
                        **{
                            k: v
                            for k, v in item.items()
                            if k in ("content", "status", "activeForm")
                        }
                    )
                )
    else:
        todos = None
    settings = getattr(session, "model_settings", None)
    effort = settings.effort if settings is not None else None
    return SessionDetail(
        id=session.id,
        project_id=str(meta.get("project_id") or ""),
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
        mode=getattr(session, "mode", "default") or "default",
        task_id=_task_id(meta),
        total_tokens=0,
        total_cost_usd=0.0,
        created_at=session.created_at,
        trigger_meta=trigger_meta,
        todos=todos,
        instructions=session.instructions or None,
        agent_slug=meta.get("agent_slug") or None,  # type: ignore[arg-type]
        worktree=_worktree_ref(meta),
        forked_from_session_id=_forked_from_session_id(session),
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

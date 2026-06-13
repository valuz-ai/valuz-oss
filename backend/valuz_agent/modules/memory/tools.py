"""memory_get / memory_write in-process MCP tools (memory-system-design §3.2).

Registered via the kernel ``register_tool`` mechanism (same pattern as
``modules/tasks/dispatch_mcp``). Both are runtime-agnostic (MCP tools work
across claude/codex/deepagents). The handlers resolve the scope's storage
location from the calling session's context:

- ``global``  : always available.
- ``project`` : needs the kernel project's cwd (ExecContext.project_id).
- ``task``    : needs project cwd + the session's task_id (metadata["valuz"]).

Scope visibility/writability is enforced by ScopeResolver: a chat session
(no project cwd) can only touch ``global``; a task session can touch all three.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.auth_context import require_current_user_id
from valuz_agent.modules.memory.models import MEM_TYPES, MemoryScope, Scope
from valuz_agent.modules.memory.service import MemoryError, memory_service

logger = logging.getLogger(__name__)

MEMORY_GET_TOOL_NAME = "memory_get"
MEMORY_WRITE_TOOL_NAME = "memory_write"


# --- context resolution (module-level so tests can monkeypatch) -------------


async def _resolve_project_cwd(session_id: str) -> str | None:
    """Project-root cwd for the session's project — resolved through the
    session's host-stamped ``metadata.valuz.project_id`` (the kernel knows
    no projects). NOT ``session.cwd``: task sub-runs execute in their own
    run_dir while memory scopes must anchor at the project root."""
    if not session_id:
        return None
    sess = await kernel_client.get_session(require_current_user_id(), session_id)
    if sess is None:
        return None
    project_id = ((sess.metadata or {}).get("valuz", {}) or {}).get("project_id") or ""
    if not project_id:
        return None
    from valuz_agent.modules.projects.service import project_cwd_by_id

    return await project_cwd_by_id(sess.user_id, str(project_id))


async def _resolve_task_id(session_id: str) -> str | None:
    if not session_id:
        return None
    sess = await kernel_client.get_session(require_current_user_id(), session_id)
    if sess is None:
        return None
    valuz = (sess.metadata or {}).get("valuz", {}) or {}
    return valuz.get("task_id") or None


class ScopeResolver:
    """Resolve a requested scope name into a concrete MemoryScope for a session,
    enforcing visibility (chat → global only; project/task sessions → all)."""

    async def resolve(self, scope: Scope, ctx: ExecContext) -> MemoryScope:
        if scope == "global":
            return MemoryScope("global")
        cwd = await _resolve_project_cwd(ctx.session_id)
        if not cwd:
            raise MemoryError(
                f"scope {scope!r} unavailable: this session has no project cwd "
                "(only 'global' memory is available here)"
            )
        if scope == "project":
            return MemoryScope("project", project_cwd=cwd)
        if scope == "task":
            task_id = await _resolve_task_id(ctx.session_id)
            if not task_id:
                raise MemoryError("scope 'task' unavailable: this session is not bound to a task")
            return MemoryScope("task", project_cwd=cwd, task_id=task_id)
        raise MemoryError(f"unknown scope: {scope!r}")


_scope_resolver = ScopeResolver()


# --- handlers ---------------------------------------------------------------


async def _memory_get_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    scope = args.get("scope")
    name = args.get("name")
    if scope not in ("global", "project", "task") or not name:
        return ToolResult(content="memory_get: 'scope' and 'name' are required", is_error=True)
    try:
        ms = await _scope_resolver.resolve(scope, ctx)
        body = memory_service.get(ms, name=str(name))
    except MemoryError as exc:
        return ToolResult(content=f"memory_get: {exc}", is_error=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("memory_get failed")
        return ToolResult(content=f"memory_get failed: {exc}", is_error=True)
    if body is None:
        return ToolResult(content=f"memory_get: no memory named {name!r} in {scope} scope")
    return ToolResult(content=body)


async def _memory_write_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    scope = args.get("scope")
    name = args.get("name")
    mtype = args.get("type")
    content = args.get("content")
    if scope not in ("global", "project", "task"):
        return ToolResult(content="memory_write: invalid 'scope'", is_error=True)
    if not name or mtype not in MEM_TYPES or not content:
        return ToolResult(
            content=(
                "memory_write: 'name', 'type'(user|feedback|project|reference), "
                "'content' are required"
            ),
            is_error=True,
        )
    try:
        ms = await _scope_resolver.resolve(scope, ctx)
        entry = memory_service.write(
            ms, name=str(name), type=mtype, content=str(content), source="agent"
        )
    except MemoryError as exc:
        return ToolResult(content=f"memory_write: {exc}", is_error=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("memory_write failed")
        return ToolResult(content=f"memory_write failed: {exc}", is_error=True)
    return ToolResult(content=f"saved [{entry.scope}] {entry.name} ({entry.type})")


# --- schemas ----------------------------------------------------------------

_SCOPE_PROP = {
    "type": "string",
    "enum": ["global", "project", "task"],
    "description": "global=cross-project user memory; project=this project; task=this task.",
}

_GET_PARAMS = {
    "type": "object",
    "properties": {"scope": _SCOPE_PROP, "name": {"type": "string"}},
    "required": ["scope", "name"],
}
_WRITE_PARAMS = {
    "type": "object",
    "properties": {
        "scope": _SCOPE_PROP,
        "name": {"type": "string", "description": "kebab-case slug, unique per scope."},
        "type": {"type": "string", "enum": list(MEM_TYPES)},
        "content": {"type": "string", "description": "The durable fact/preference/decision."},
    },
    "required": ["scope", "name", "type", "content"],
}

_GET_DESC = (
    "Load the full content of a stored memory by scope+name. The available "
    "memory names appear in the injected memory index."
)
_WRITE_DESC = (
    "Save a durable memory (fact/preference/decision NOT derivable from code/git). "
    "Same name overwrites (update). Types: user|feedback|project|reference."
)


# Declarations (handler=None) for AgentConfig.tools — the kernel only surfaces
# a tool to the model if it is declared on the agent's tools tuple; the real
# handlers are attached from the registry (register_memory_tools) at runtime.
MEMORY_TOOL_DECLARATIONS: tuple[ToolDef, ...] = (
    ToolDef(
        name=MEMORY_GET_TOOL_NAME,
        description=_GET_DESC,
        parameters=_GET_PARAMS,
        handler=None,
        read_only=True,
    ),
    ToolDef(
        name=MEMORY_WRITE_TOOL_NAME,
        description=_WRITE_DESC,
        parameters=_WRITE_PARAMS,
        handler=None,
        read_only=False,
    ),
)


def build_memory_tool_defs() -> tuple[ToolDef, ...]:
    """Build the memory_get / memory_write defs (live handlers) for the
    host toolkit MCP server."""
    _defs: list[ToolDef] = []

    def register_tool(td: ToolDef) -> None:
        _defs.append(td)

    register_tool(
        ToolDef(
            name=MEMORY_GET_TOOL_NAME,
            description=_GET_DESC,
            parameters=_GET_PARAMS,
            handler=_memory_get_handler,
            read_only=True,
        )
    )
    register_tool(
        ToolDef(
            name=MEMORY_WRITE_TOOL_NAME,
            description=_WRITE_DESC,
            parameters=_WRITE_PARAMS,
            handler=_memory_write_handler,
            read_only=False,
        )
    )
    logger.info("Built memory tool defs: %s, %s", MEMORY_GET_TOOL_NAME, MEMORY_WRITE_TOOL_NAME)
    return tuple(_defs)

"""memory in-process MCP tool (memory-system-design §5).

A single ``memory`` tool with actions add/replace/remove for the agent's own
notes, plus list/clear/settings — the Memory settings page's operations — so
the user can manage memory from the conversation (agent/UI parity).
Registered in the host toolkit MCP ``base``
toolset, so it is runtime-agnostic (claude/codex/deepagents). The handler
resolves the ``project`` target from the calling session's host-stamped
``metadata.valuz.project_id`` (the kernel knows no projects); ``user`` /
``global`` need no project. Both this tool and the background extractor (P1)
call the same ``MemoryStore`` pipeline — only the ``source`` tag differs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.modules.memory.models import TARGETS
from valuz_agent.modules.memory.prompts import TOOL_DESCRIPTION
from valuz_agent.modules.memory.service import MemoryError, memory_store

logger = logging.getLogger(__name__)

MEMORY_TOOL_NAME = "memory"
_ACTIONS = ("add", "replace", "remove", "list", "clear", "settings")

# The management half of the Memory settings page, so the agent can show,
# prune, clear and configure memory from the conversation (agent/UI parity).
_MANAGE_ADDENDUM = (
    "\n\nManagement actions (same as the Memory settings page):\n"
    "- action=list: return the stored entries per target (user, global, and "
    "project when this session has one) plus the memory settings. Use it "
    "before remove/replace so you quote an existing entry.\n"
    "- action=clear: delete EVERY entry of `target`. Irreversible — confirm "
    "with the user first.\n"
    "- action=settings: read the memory settings; pass any of `enabled`, "
    "`auto_extract`, `custom_instructions` to change them (omitted fields "
    "stay as they are; custom_instructions='' clears it)."
)


# --- context resolution (module-level so tests can monkeypatch) -------------


async def _resolve_project_id(user_id: str, session_id: str) -> str | None:
    """Project id for the session — read from the host-stamped
    ``metadata.valuz.project_id`` (the kernel knows no projects). Returns None
    for quick chats / agent-only sessions (only user+global are writable there)."""
    if not session_id:
        return None
    sess = await kernel_client.get_session(user_id, session_id)
    if sess is None:
        return None
    return ((sess.metadata or {}).get("valuz", {}) or {}).get("project_id") or None


# --- handler ----------------------------------------------------------------


async def _memory_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    user_id = ctx.user_id

    action = args.get("action")
    target = args.get("target")
    content = args.get("content")
    old_text = args.get("old_text")

    if action not in _ACTIONS:
        return ToolResult(
            content="memory: 'action' must be add|replace|remove|list|clear|settings",
            is_error=True,
        )
    if action == "settings":
        return await _settings(user_id, args)
    if action == "list":
        return await _list(user_id, ctx.session_id, target)
    if target not in TARGETS:
        return ToolResult(content="memory: 'target' must be user|global|project", is_error=True)
    if action == "add" and not content:
        return ToolResult(content="memory: 'content' is required for add", is_error=True)
    if action == "replace" and (not old_text or not content):
        return ToolResult(
            content="memory: 'old_text' and 'content' are required for replace", is_error=True
        )
    if action == "remove" and not old_text:
        return ToolResult(content="memory: 'old_text' is required for remove", is_error=True)

    project_id: str | None = None
    if target == "project":
        # MCP tool boundary: the toolkit server has published the caller's owner
        # into the auth context — resolve it once here and thread it explicitly.
        project_id = await _resolve_project_id(user_id, ctx.session_id)
        if not project_id:
            return ToolResult(
                content=(
                    "memory: 'project' target unavailable here (this session has no "
                    "project) — use 'user' or 'global'"
                ),
                is_error=True,
            )

    try:
        if action == "add":
            result = memory_store.add(
                user_id, target, str(content), project_id=project_id, source="agent"
            )
        elif action == "replace":
            result = memory_store.replace(
                user_id,
                target,
                str(old_text),
                str(content),
                project_id=project_id,
                source="agent",
            )
        elif action == "clear":
            memory_store.clear(user_id, target, project_id=project_id)
            result = {
                "success": True,
                "target": target,
                "entries": [],
                "entry_count": 0,
                "message": f"cleared every {target} memory entry",
            }
        else:  # remove
            result = memory_store.remove(
                user_id, target, str(old_text), project_id=project_id, source="agent"
            )
    except MemoryError as exc:
        return ToolResult(content=f"memory: {exc}", is_error=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("memory tool failed")
        return ToolResult(content=f"memory failed: {exc}", is_error=True)

    return ToolResult(
        content=json.dumps(result, ensure_ascii=False),
        is_error=not bool(result.get("success", True)),
    )


async def _read_settings(user_id: str) -> dict[str, Any]:
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.settings.preferences import (
        get_memory_auto_extract,
        get_memory_custom_instructions,
        get_memory_enabled,
    )

    async with async_unit_of_work(commit=False) as db:
        return {
            "enabled": await get_memory_enabled(db, user_id=user_id),
            "auto_extract": await get_memory_auto_extract(db, user_id=user_id),
            "custom_instructions": await get_memory_custom_instructions(db, user_id=user_id),
        }


async def _list(user_id: str, session_id: str, target: Any) -> ToolResult:
    """The Memory page's view: entries per scope + settings (GET /v1/memory)."""
    if target is not None and target not in TARGETS:
        return ToolResult(content="memory: 'target' must be user|global|project", is_error=True)
    try:
        project_id = await _resolve_project_id(user_id, session_id)
        targets = [target] if target else ["user", "global", *(["project"] if project_id else [])]
        entries: dict[str, list[str]] = {}
        for scope in targets:
            if scope == "project" and not project_id:
                return ToolResult(
                    content="memory: this session has no project — no project memory to list",
                    is_error=True,
                )
            entries[scope] = memory_store.read_entries(
                user_id, scope, project_id=project_id if scope == "project" else None
            )
        payload: dict[str, Any] = {
            "success": True,
            "entries": entries,
            "usage": {
                scope: memory_store.usage_for(items, scope) for scope, items in entries.items()
            },
            "settings": await _read_settings(user_id),
        }
    except MemoryError as exc:
        return ToolResult(content=f"memory: {exc}", is_error=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("memory list failed")
        return ToolResult(content=f"memory failed: {exc}", is_error=True)
    return ToolResult(content=json.dumps(payload, ensure_ascii=False))


async def _settings(user_id: str, args: dict[str, Any]) -> ToolResult:
    """Read or patch the memory settings (PATCH /v1/memory/settings)."""
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.settings.preferences import (
        set_memory_auto_extract,
        set_memory_custom_instructions,
        set_memory_enabled,
    )

    enabled = args.get("enabled")
    auto_extract = args.get("auto_extract")
    custom = args.get("custom_instructions")
    if enabled is not None and not isinstance(enabled, bool):
        return ToolResult(content="memory: 'enabled' must be a boolean", is_error=True)
    if auto_extract is not None and not isinstance(auto_extract, bool):
        return ToolResult(content="memory: 'auto_extract' must be a boolean", is_error=True)
    try:
        if enabled is not None or auto_extract is not None or custom is not None:
            async with async_unit_of_work() as db:
                if enabled is not None:
                    await set_memory_enabled(db, bool(enabled), user_id=user_id)
                if auto_extract is not None:
                    await set_memory_auto_extract(db, bool(auto_extract), user_id=user_id)
                if custom is not None:
                    await set_memory_custom_instructions(db, str(custom), user_id=user_id)
        settings = await _read_settings(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("memory settings failed")
        return ToolResult(content=f"memory failed: {exc}", is_error=True)
    return ToolResult(
        content=json.dumps({"success": True, "settings": settings}, ensure_ascii=False)
    )


# --- schema -----------------------------------------------------------------

_TARGET_PROP = {
    "type": "string",
    "enum": list(TARGETS),
    "description": (
        "user=who the user is (cross-project); global=cross-project notes/lessons; "
        "project=this project (project sessions only)."
    ),
}
_PARAMS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(_ACTIONS),
            "description": "add|replace|remove|list|clear|settings.",
        },
        "target": _TARGET_PROP,
        "content": {"type": "string", "description": "Entry text. Required for add and replace."},
        "old_text": {
            "type": "string",
            "description": "Unique substring identifying the entry to replace/remove.",
        },
        "enabled": {
            "type": "boolean",
            "description": "settings only: turn the memory feature on/off.",
        },
        "auto_extract": {
            "type": "boolean",
            "description": (
                "settings only: let the background extractor save memories automatically."
            ),
        },
        "custom_instructions": {
            "type": "string",
            "description": "settings only: what the extractor should focus on; '' clears.",
        },
    },
    "required": ["action"],
}


def build_memory_tool_defs() -> tuple[ToolDef, ...]:
    """Build the single ``memory`` tool def (live handler) for the host toolkit MCP server."""
    td = ToolDef(
        name=MEMORY_TOOL_NAME,
        description=TOOL_DESCRIPTION + _MANAGE_ADDENDUM,
        parameters=_PARAMS,
        handler=_memory_handler,
        read_only=False,
    )
    logger.info("Built memory tool def: %s", MEMORY_TOOL_NAME)
    return (td,)

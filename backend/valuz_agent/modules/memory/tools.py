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
from valuz_agent.facade.memory import MemoryLibrary
from valuz_agent.modules.memory.models import TARGETS, MemoryUnavailable, SourceRef, Target
from valuz_agent.modules.memory.prompts import TOOL_DESCRIPTION
from valuz_agent.modules.memory.service import MemoryError, MemoryStore

logger = logging.getLogger(__name__)

MEMORY_TOOL_NAME = "memory"
_ACTIONS = ("add", "replace", "remove", "list", "clear", "settings", "skill_preview")

# The management half of the Memory settings page, so the agent can show,
# prune, clear and configure memory from the conversation (agent/UI parity).
_MANAGE_ADDENDUM = (
    "\n\nskill_preview: propose a bounded Skill document from 2–8 current confirmed "
    "lesson/decision record_ids with independent owner evidence. Pass name (slug), purpose "
    "and at least two replay_cases. Returns a draft only: no library save, installation or "
    "automatic permission. Review, replay, then use skill-creator/submit_skill approval."
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
            content="memory: 'action' must be add|replace|remove|list|clear|settings|skill_preview",
            is_error=True,
        )
    if action == "skill_preview":
        return await _skill_preview(user_id, ctx.session_id, args)
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

    base_revision = args.get("base_revision")
    if base_revision is not None and (
        isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 0
    ):
        return ToolResult(
            content="memory: base_revision must be a nonnegative integer", is_error=True
        )
    if action in ("add", "replace"):
        try:
            if not (await _read_settings(user_id))["enabled"]:
                return ToolResult(content="memory: collection is disabled", is_error=True)
        except Exception:  # noqa: BLE001 — fail closed if the collection switch is unavailable
            return ToolResult(content="memory: collection settings are unavailable", is_error=True)
    source_refs = (
        (SourceRef(kind="session", source_id=ctx.session_id, origin="agent"),)
        if ctx.session_id
        else ()
    )
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
            result = await MemoryLibrary(user_id).add(
                target,
                str(content),
                project_id=project_id,
                source="agent",
                source_refs=source_refs,
                base_revision=base_revision,
            )
        elif action == "replace":
            result = await MemoryLibrary(user_id).replace(
                target,
                str(old_text),
                str(content),
                project_id=project_id,
                source="agent",
                source_refs=source_refs,
                base_revision=base_revision,
            )
        elif action == "clear":
            await MemoryLibrary(user_id).clear(
                target,
                project_id=project_id,
                source="agent",
                source_refs=source_refs,
                base_revision=base_revision,
            )
            result = {
                "success": True,
                "target": target,
                "entries": [],
                "entry_count": 0,
                "message": f"cleared every {target} memory entry",
            }
        else:  # remove
            result = await MemoryLibrary(user_id).remove(
                target,
                str(old_text),
                project_id=project_id,
                source="agent",
                source_refs=source_refs,
                base_revision=base_revision,
            )
    except MemoryUnavailable:
        return ToolResult(
            content="memory: authority unavailable; do not treat this as empty memory",
            is_error=True,
        )
    except MemoryError as exc:
        return ToolResult(content=f"memory: {exc}", is_error=True)
    except Exception:  # noqa: BLE001
        logger.exception("memory tool failed")
        return ToolResult(content="memory: operation failed; no confirmed result", is_error=True)

    return ToolResult(
        content=json.dumps(result, ensure_ascii=False),
        is_error=not bool(result.get("success", True)),
    )


async def _skill_preview(user_id: str, session_id: str, args: dict[str, Any]) -> ToolResult:
    from valuz_agent.modules.memory.learning import SkillLearningRequest, preview_skill_learning

    session = await kernel_client.get_session(user_id, session_id) if session_id else None
    if session is None or session.user_id != user_id:
        return ToolResult(content="memory: an owned session is required", is_error=True)
    try:
        request = SkillLearningRequest.model_validate(
            {
                "record_ids": args.get("record_ids"),
                "name": args.get("name"),
                "purpose": args.get("purpose"),
                "replay_cases": args.get("replay_cases"),
            }
        )
        result = await preview_skill_learning(
            user_id,
            request,
            project_id=await _resolve_project_id(user_id, session_id),
            library=MemoryLibrary(user_id),
        )
        return ToolResult(content=result.model_dump_json())
    except MemoryUnavailable:
        return ToolResult(
            content="memory: authority unavailable; no Skill draft prepared", is_error=True
        )
    except (MemoryError, ValueError) as exc:
        return ToolResult(content=f"memory: {exc}", is_error=True)


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
        targets: list[Target] = (
            [scope for scope in TARGETS if target == scope] if target else ["user", "global"]
        )
        if target is None and project_id:
            targets.append("project")
        snapshot = await MemoryLibrary(user_id).snapshot()
        entries: dict[Target, list[str]] = {}
        for scope in targets:
            if scope == "project" and not project_id:
                return ToolResult(
                    content="memory: this session has no project — no project memory to list",
                    is_error=True,
                )
            entries[scope] = [
                record.content
                for record in snapshot.records
                if record.target == scope
                and (scope != "project" or record.project_id == project_id)
            ]
        payload: dict[str, Any] = {
            "success": True,
            "revision": snapshot.revision,
            "records": [
                record.model_dump(mode="json")
                for record in snapshot.records
                if record.target in targets
                and (record.target != "project" or record.project_id == project_id)
            ],
            "entries": entries,
            "usage": {
                scope: MemoryStore.usage_for(items, scope) for scope, items in entries.items()
            },
            "settings": await _read_settings(user_id),
        }
    except MemoryUnavailable:
        return ToolResult(
            content="memory: authority unavailable; do not treat this as empty memory",
            is_error=True,
        )
    except MemoryError as exc:
        return ToolResult(content=f"memory: {exc}", is_error=True)
    except Exception:  # noqa: BLE001
        logger.exception("memory list failed")
        return ToolResult(content="memory: operation failed; no confirmed result", is_error=True)
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
    except Exception:  # noqa: BLE001
        logger.exception("memory settings failed")
        return ToolResult(content="memory: operation failed; no confirmed result", is_error=True)
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
            "description": "add|replace|remove|list|clear|settings|skill_preview.",
        },
        "record_ids": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 8},
        "name": {"type": "string", "description": "skill_preview only: proposed Skill slug."},
        "purpose": {"type": "string", "description": "skill_preview only: when to use the method."},
        "replay_cases": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 2,
            "maxItems": 8,
        },
        "target": _TARGET_PROP,
        "content": {"type": "string", "description": "Entry text. Required for add and replace."},
        "old_text": {
            "type": "string",
            "description": "Unique substring identifying the entry to replace/remove.",
        },
        "base_revision": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "Expected catalog revision from list; prevents overwriting a newer change."
            ),
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

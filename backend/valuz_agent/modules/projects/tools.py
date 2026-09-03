"""project-config in-process tool: set/append the project's instructions.

Mirrors the memory tool (``modules/memory/tools.py``): a single tool registered
in the host toolkit MCP ``base`` toolset (runtime-agnostic — claude/codex/
deepagents), self-gating to PROJECT sessions via the calling session's
host-stamped ``metadata.valuz.project_id`` (the kernel knows no projects).

Why instructions are NOT delivered like memory
-----------------------------------------------
Project memory rides per-turn ``additional_context`` (rebuilt every turn), so it
takes effect immediately and stays prompt-cache-safe. Project *instructions* are
the project's authoritative direction/framework — they live in
``project.instructions_md`` and flow into a session's SYSTEM PROMPT
(``session.instructions``), which is frozen at session creation (ADR-008). So an
edit here applies to the project's NEXT conversation, not the current turn —
exactly like Claude Code's ``CLAUDE.md`` (read once per conversation, re-read on
the next one). The tool description tells the agent this so it doesn't promise an
immediate effect.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.adapters import kernel_client
from valuz_agent.infra.errors import ValuzError
from valuz_agent.integrations.tools_entity_common import dump

logger = logging.getLogger(__name__)

PROJECT_INSTRUCTIONS_TOOL_NAME = "project_instructions"
_ACTIONS = ("get", "set", "append")

TOOL_DESCRIPTION = (
    "Read or configure THIS project's instructions (its 项目说明 / direction, "
    "analysis framework, output preferences). Project sessions only — unavailable "
    "in a quick chat or agent-only conversation. Edits the project's persistent "
    "instructions that seed every conversation's system prompt.\n"
    "- action=get: return the project's CURRENT full instructions text. Use this "
    "first when editing, so you can modify the full text deliberately.\n"
    "- action=set: replace the WHOLE instructions text with `content` (the "
    "read-then-edit-the-whole-thing flow: get → revise → set).\n"
    "- action=append: add `content` as a new paragraph below the existing text "
    "(shortcut for purely additive notes).\n"
    "Edits take effect for the project's NEXT conversation (the current "
    "conversation keeps the system prompt it started with) — say so if the user "
    "expects an immediate change. For facts/progress that should apply right "
    "away, use the `memory` tool with target=project instead."
)


async def _resolve_project_id(user_id: str, session_id: str) -> str | None:
    """Project id for the calling session — read from the host-stamped
    ``metadata.valuz.project_id``. Returns None for quick chats / agent-only
    sessions (no project), which gates the tool to project sessions."""
    if not session_id:
        return None
    sess = await kernel_client.get_session(user_id, session_id)
    if sess is None:
        return None
    return ((sess.metadata or {}).get("valuz", {}) or {}).get("project_id") or None


async def _handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    user_id = ctx.user_id

    action = args.get("action")
    content = args.get("content")

    if action not in _ACTIONS:
        return ToolResult(
            content="project_instructions: 'action' must be get|set|append", is_error=True
        )
    if action in ("set", "append") and (not content or not str(content).strip()):
        return ToolResult(
            content="project_instructions: 'content' is required for set/append", is_error=True
        )

    # MCP tool boundary: the toolkit server published the caller's owner into
    # the auth context — resolve it once here and thread it explicitly.
    project_id = await _resolve_project_id(user_id, ctx.session_id)
    if not project_id:
        return ToolResult(
            content=(
                "project_instructions: this session has no project — project "
                "instructions can only be read/configured inside a project"
            ),
            is_error=True,
        )

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.projects.service import ProjectService

    try:
        # Read path: return the current full instructions (the read half of a
        # deliberate read → revise → set edit).
        if action == "get":
            async with async_unit_of_work(commit=False) as db:
                row = await ProjectDatastore(db).get_by_id(user_id, project_id)
            if row is None:
                return ToolResult(content="project_instructions: project not found", is_error=True)
            return ToolResult(
                content=json.dumps({"instructions": row.instructions_md or ""}, ensure_ascii=False)
            )

        text = str(content).strip()
        async with async_unit_of_work(commit=True) as db:
            ds = ProjectDatastore(db)
            svc = ProjectService(ds, event_bus)
            if action == "append":
                row = await ds.get_by_id(user_id, project_id)
                if row is None:
                    return ToolResult(
                        content="project_instructions: project not found", is_error=True
                    )
                base = (row.instructions_md or "").strip()
                text = f"{base}\n\n{text}".strip() if base else text
            await svc.update_instructions(user_id, project_id, text)
    except KeyError:
        return ToolResult(content="project_instructions: project not found", is_error=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("project_instructions tool failed")
        return ToolResult(content=f"project_instructions failed: {exc}", is_error=True)

    return ToolResult(
        content=json.dumps(
            {
                "success": True,
                "action": action,
                "applies_to": "the project's next conversation (current one is unchanged)",
            },
            ensure_ascii=False,
        )
    )


_PARAMS = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(_ACTIONS), "description": "get|set|append."},
        "content": {
            "type": "string",
            "description": (
                "Instructions text. Omit for get. For set: the full revised text. "
                "For append: the paragraph to add."
            ),
        },
    },
    "required": ["action"],
}


def build_project_instructions_tool_defs() -> tuple[ToolDef, ...]:
    """Build the single ``project_instructions`` tool def for the toolkit MCP."""
    td = ToolDef(
        name=PROJECT_INSTRUCTIONS_TOOL_NAME,
        description=TOOL_DESCRIPTION,
        parameters=_PARAMS,
        handler=_handler,
        read_only=False,
    )
    logger.info("Built project tool def: %s", PROJECT_INSTRUCTIONS_TOOL_NAME)
    return (td,)


# ---------------------------------------------------------------------------
# ``project`` — the Projects page operations (create / rename / delete /
# default lead / export), so the agent can manage projects from the
# conversation (agent/UI parity). Read-only actions work anywhere; mutations
# default to THIS project and refuse chat/temp projects, like the routes.
# ---------------------------------------------------------------------------

PROJECT_TOOL_NAME = "project"
_PROJECT_ACTIONS = (
    "list",
    "get",
    "create",
    "rename",
    "delete_preview",
    "delete",
    "set_default_lead",
    "export",
)

PROJECT_TOOL_DESCRIPTION = (
    "Manage the user's projects — the same operations as the Projects page.\n"
    "- action=list: every project (id, name, kind, cwd).\n"
    "- action=get: one project's detail. `project_id` defaults to THIS project.\n"
    "- action=create: create a project from `name` (managed folder), or bind an "
    "existing folder with `root_path` (absolute). Returns the new project.\n"
    "- action=rename: set `name` on `project_id` (defaults to THIS project).\n"
    "- action=delete_preview: what deleting would remove (sessions, knowledge "
    "bindings, automations, project skills). Call it and show the user BEFORE "
    "action=delete.\n"
    "- action=delete: permanently delete the project and everything above. "
    "Irreversible — only after the user confirmed. Pass `project_id` explicitly.\n"
    "- action=set_default_lead: make `agent_slug` (a current project member "
    "handle, see list_project_members) the default lead; omit it to clear.\n"
    "- action=export: write the project as a .valuzpack archive into this "
    "session's workspace and return its path.\n"
    "Quick chats / temp conversations are not projects: get/rename/delete/"
    "export refuse them."
)

PROJECT_TOOL_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(_PROJECT_ACTIONS),
            "description": "list|get|create|rename|delete_preview|delete|set_default_lead|export",
        },
        "project_id": {
            "type": "string",
            "description": "Target project. Defaults to THIS session's project (not for create).",
        },
        "name": {"type": "string", "description": "create: new project name; rename: new name."},
        "root_path": {
            "type": "string",
            "description": (
                "create only: absolute path of an existing folder to bind as the project."
            ),
        },
        "agent_slug": {
            "type": "string",
            "description": (
                "set_default_lead only: member handle to make default lead; omit to clear."
            ),
        },
    },
    "required": ["action"],
}


async def _resolve_real_project_id(user_id: str, session_id: str) -> str | None:
    """THIS session's project id, but only for a real project (``kind ==
    "project"``): a quick chat binds to an ephemeral ``ProjectRow(kind="chat")``
    that must not be renamed, deleted or exported. Mirrors the agent tools'
    resolver (``tools_agent_proposal._resolve_project_id``)."""
    if not session_id:
        return None
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.sessions import project_index

    project_id = await project_index.project_of(session_id)
    if not project_id:
        return None
    async with async_unit_of_work(commit=False) as db:
        row = await ProjectDatastore(db).get_by_id(user_id, project_id)
    return project_id if (row is not None and row.kind == "project") else None


def _full_project_service(db: Any) -> Any:
    """``ProjectService`` with every collaborator the routes wire (``api/deps.
    get_project_service``): delete / preview / default lead need them."""
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.agents.datastore import ProjectMemberDatastore
    from valuz_agent.modules.automations.datastore import AutomationDatastore
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.docs.datastore import DocumentDatastore
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.projects.service import ProjectService
    from valuz_agent.modules.sessions.datastore import SessionDatastore
    from valuz_agent.modules.skills.datastore import SkillDatastore

    return ProjectService(
        datastore=ProjectDatastore(db),
        event_bus=event_bus,
        session_datastore=SessionDatastore(db),
        document_datastore=DocumentDatastore(db),
        automation_datastore=AutomationDatastore(db),
        skill_datastore=SkillDatastore(db),
        connector_datastore=ConnectorDatastore(db),
        member_datastore=ProjectMemberDatastore(db),
    )


async def _export_project(user_id: str, project_id: str) -> bytes:
    """Mirror ``api/deps.get_project_pack_service`` over one unit of work."""
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.agent_packs.service import AgentPackService
    from valuz_agent.modules.agents.service import AgentService
    from valuz_agent.modules.automations.datastore import AutomationDatastore
    from valuz_agent.modules.automations.service import AutomationService
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.modules.docs.datastore import DocumentDatastore
    from valuz_agent.modules.project_packs.service import ProjectPackService
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.projects.service import ProjectService
    from valuz_agent.modules.sessions.datastore import SessionDatastore
    from valuz_agent.modules.settings.preferences import (
        get_default_locale,
        get_effective_default_timezone,
    )
    from valuz_agent.modules.skills.datastore import SkillDatastore

    async with async_unit_of_work() as db:
        locale = await get_default_locale(db, user_id=user_id)
        default_timezone = await get_effective_default_timezone(db, user_id=user_id)
        project_svc = ProjectService(
            datastore=ProjectDatastore(db),
            event_bus=event_bus,
            automation_datastore=AutomationDatastore(db),
            skill_datastore=SkillDatastore(db),
            connector_datastore=ConnectorDatastore(db),
            session_datastore=SessionDatastore(db),
            document_datastore=DocumentDatastore(db),
        )
        connector_svc = ConnectorService(datastore=ConnectorDatastore(db))
        agent_svc = AgentService(db=db, connector_service=connector_svc)
        pack_svc = ProjectPackService(
            project_service=project_svc,
            agent_service=agent_svc,
            agent_pack_service=AgentPackService(agent_svc),
            automation_service=AutomationService(
                db=db,
                event_bus=event_bus,
                project_service=project_svc,
                agent_service=agent_svc,
                locale=locale,
                default_timezone=default_timezone,
            ),
        )
        return await pack_svc.export_project(user_id, project_id)


def _perr(message: str) -> ToolResult:
    return ToolResult(content=f"project: {message}", is_error=True)


async def _project_handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    user_id = ctx.user_id
    action = args.get("action")
    if action not in _PROJECT_ACTIONS:
        return _perr("'action' must be " + "|".join(_PROJECT_ACTIONS))

    from valuz_agent.infra.db import async_unit_of_work

    try:
        if action == "list":
            async with async_unit_of_work(commit=False) as db:
                items = await _full_project_service(db).list_projects(user_id)
            return ToolResult(
                content=json.dumps({"ok": True, "projects": dump(items)}, ensure_ascii=False)
            )

        if action == "create":
            name = str(args.get("name") or "").strip()
            if not name:
                return _perr("'name' is required for create")
            root_path = args.get("root_path")
            async with async_unit_of_work() as db:
                detail = await _full_project_service(db).create_project(
                    user_id, name, str(root_path) if root_path else None
                )
            return ToolResult(
                content=json.dumps(
                    {
                        "ok": True,
                        "project": dump(detail),
                        "next_step": "The project exists; deploy agents into it with deploy_agent.",
                    },
                    ensure_ascii=False,
                )
            )

        # Every other action targets one real project: explicit id, else THIS one.
        project_id = str(args.get("project_id") or "").strip() or None
        if project_id is None:
            project_id = await _resolve_real_project_id(user_id, ctx.session_id)
        if not project_id:
            return _perr(
                "no project — pass `project_id`, or run this inside a project conversation "
                "(quick chats are not projects)"
            )
        if action == "delete" and not str(args.get("project_id") or "").strip():
            return _perr("delete requires an explicit `project_id` (after showing delete_preview)")

        if action == "get":
            async with async_unit_of_work(commit=False) as db:
                detail = await _full_project_service(db).get_project(user_id, project_id)
            return ToolResult(
                content=json.dumps({"ok": True, "project": dump(detail)}, ensure_ascii=False)
            )
        if action == "rename":
            name = str(args.get("name") or "").strip()
            if not name:
                return _perr("'name' is required for rename")
            async with async_unit_of_work() as db:
                detail = await _full_project_service(db).rename_project(user_id, project_id, name)
            return ToolResult(
                content=json.dumps({"ok": True, "project": dump(detail)}, ensure_ascii=False)
            )
        if action == "delete_preview":
            async with async_unit_of_work(commit=False) as db:
                preview = await _full_project_service(db).preview_delete(user_id, project_id)
            return ToolResult(
                content=json.dumps(
                    {
                        "ok": True,
                        "project_id": project_id,
                        "would_remove": dump(preview),
                        "next_step": (
                            "Show these counts to the user; delete only after they confirm."
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        if action == "delete":
            async with async_unit_of_work() as db:
                await _full_project_service(db).delete_project(user_id, project_id)
            return ToolResult(
                content=json.dumps({"ok": True, "deleted": project_id}, ensure_ascii=False)
            )
        if action == "set_default_lead":
            slug = str(args.get("agent_slug") or "").strip() or None
            async with async_unit_of_work() as db:
                detail = await _full_project_service(db).set_default_lead(user_id, project_id, slug)
            return ToolResult(
                content=json.dumps({"ok": True, "project": dump(detail)}, ensure_ascii=False)
            )
        if action == "export":
            from pathlib import Path

            data = await _export_project(user_id, project_id)
            workspace = Path(ctx.workspace) if getattr(ctx, "workspace", None) else Path.cwd()
            target = workspace / f"{project_id}.valuzpack"
            target.write_bytes(data)
            return ToolResult(
                content=json.dumps(
                    {"ok": True, "path": str(target), "bytes": len(data)}, ensure_ascii=False
                )
            )
    except KeyError:
        return _perr("project not found")
    except ValueError as exc:
        return _perr(str(exc))
    except ValuzError as exc:
        # e.g. ProjectNotExportable (chat project) / ProjectPackNotFound
        return _perr(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("project tool failed")
        return _perr(f"failed: {exc}")
    return _perr("unhandled action")


def build_project_tool_defs() -> tuple[ToolDef, ...]:
    """The ``project`` management tool for the host toolkit MCP server."""
    return (
        ToolDef(
            name=PROJECT_TOOL_NAME,
            description=PROJECT_TOOL_DESCRIPTION,
            parameters=PROJECT_TOOL_PARAMETERS,
            handler=_project_handler,
            read_only=False,
        ),
    )

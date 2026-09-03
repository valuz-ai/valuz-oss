"""``skill_library`` toolkit tool — the Skills page operations for the agent.

Authoring a skill's *content* keeps its dedicated flow (``prepare_skill_edit``
→ edit in ./.skill-staging → ``submit_skill`` for the user's review). This
tool covers everything else the page can do: list / inspect / files /
versions, create from markdown, update metadata, enable / disable, delete
(preview then confirm), import from a GitHub or archive URL or a local
archive (preview then confirm), restore a version. Every action calls
``SkillLibraryService`` with the session owner from ``ctx.user_id`` and the
same rules as ``api/routes/skills.py``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.infra.errors import ValuzError
from valuz_agent.integrations.tools_entity_common import dump, run_with_skill_service

logger = logging.getLogger(__name__)

SKILL_LIBRARY_TOOL_NAME = "skill_library"
_ACTIONS = (
    "list",
    "get",
    "files",
    "read_file",
    "create",
    "update",
    "enable",
    "disable",
    "delete",
    "import_url",
    "import_archive",
    "import_confirm",
    "versions",
    "restore",
)
_SCOPES = ("user", "project")

SKILL_LIBRARY_DESCRIPTION = (
    "Manage the user's skill library — the same operations as the Skills page. "
    "Skill ids look like `user:<slug>`, `project:<slug>`, `official:<slug>`.\n"
    "- list: the catalog (optionally `library_enabled` true/false to filter).\n"
    "- get: one skill's detail. files / read_file (`path`): its package.\n"
    "- create: a new skill from `name` + `instructions_markdown` (its SKILL.md "
    "body) in `target_scope` user|project (project needs `project_id`; "
    "`add_to_project` enables it there). To improve an EXISTING skill's content "
    "use prepare_skill_edit → submit_skill instead.\n"
    "- update: metadata of `skill_id` — `name`, `description`, "
    "`instructions_markdown` (omitted fields keep their value).\n"
    "- enable / disable: the library switch (disable = 卸载 in the page; files "
    "stay, the skill just stops being offered).\n"
    "- delete: with `confirm`=false (default) returns which projects would be "
    "affected; with `confirm`=true removes the skill's files and bindings. "
    "Irreversible — confirm with the user first.\n"
    "- import_url: fetch a skill from `url` — a GitHub repository, a "
    "github.com/.../tree/<ref>/<dir> folder, a raw SKILL.md, or any zip URL — "
    "and return the import preview (one candidate per SKILL.md found, each "
    "with its own `preview_id`). Pass `confirm`=true to import every candidate "
    "immediately; otherwise call import_confirm per chosen `preview_id` "
    "within 10 minutes.\n"
    "- import_archive: same for a local zip/tar at `archive_path`.\n"
    "- import_confirm: finish an import — `preview_id`, `import_kind` url|archive, "
    "optional `name`, `target_scope`, `project_id`, `add_to_project`.\n"
    "- versions: the version history of `skill_id`; restore: bring back "
    "`revision_id` as the current version."
)

SKILL_LIBRARY_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(_ACTIONS), "description": "See above."},
        "skill_id": {"type": "string", "description": "Target skill id (get/files/update/...)."},
        "project_id": {
            "type": "string",
            "description": (
                "Project scope for list/get/files/update/delete, and for project skills."
            ),
        },
        "library_enabled": {"type": "boolean", "description": "list: filter by library switch."},
        "path": {"type": "string", "description": "read_file: file path inside the skill."},
        "name": {"type": "string", "description": "create/update/import_confirm: skill name."},
        "description": {"type": "string", "description": "create/update: one-line description."},
        "instructions_markdown": {
            "type": "string",
            "description": "create/update: the SKILL.md body (markdown).",
        },
        "target_scope": {
            "type": "string",
            "enum": list(_SCOPES),
            "description": "create/import: where the skill lives. Default user.",
        },
        "add_to_project": {
            "type": "boolean",
            "description": "create/import: also enable the skill in `project_id`.",
        },
        "confirm": {
            "type": "boolean",
            "description": (
                "delete: actually delete; import_url/import_archive: import all candidates now."
            ),
        },
        "url": {"type": "string", "description": "import_url: GitHub / raw / zip URL."},
        "archive_path": {"type": "string", "description": "import_archive: local archive path."},
        "preview_id": {"type": "string", "description": "import_confirm: candidate to import."},
        "import_kind": {
            "type": "string",
            "enum": ["url", "archive"],
            "description": "import_confirm: which preview kind the id came from (default url).",
        },
        "revision_id": {"type": "string", "description": "restore: version to restore."},
    },
    "required": ["action"],
}


def _err(message: str) -> ToolResult:
    return ToolResult(content=f"skill_library: {message}", is_error=True)


def _ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(content=json.dumps({"ok": True, **payload}, ensure_ascii=False))


def _candidates(preview: Any) -> list[Any]:
    """Every importable candidate of a preview (multi-skill archives carry
    them under ``skills``; a single-skill preview is its own candidate)."""
    skills = getattr(preview, "skills", None) or []
    return list(skills) if skills else [preview]


async def _handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    user_id = ctx.user_id
    action = args.get("action")
    if action not in _ACTIONS:
        return _err("'action' must be " + "|".join(_ACTIONS))

    skill_id = str(args.get("skill_id") or "").strip() or None
    project_id = str(args.get("project_id") or "").strip() or None
    scope = str(args.get("target_scope") or "user")
    if scope not in _SCOPES:
        return _err("'target_scope' must be user|project")
    if scope == "project" and not project_id and action in ("create", "import_confirm"):
        return _err("'project_id' is required for target_scope=project")
    needs_id = action in (
        "get",
        "files",
        "read_file",
        "update",
        "enable",
        "disable",
        "delete",
        "versions",
        "restore",
    )
    if needs_id and not skill_id:
        return _err(f"'skill_id' is required for {action}")

    from valuz_agent.modules.skills.models import (
        SkillCreateRequest,
        SkillUpdateRequest,
    )

    try:
        if action == "list":
            flag = args.get("library_enabled")

            async def _list(svc: Any) -> Any:
                catalog = await svc.list_catalog(user_id, project_id or "chat-default")
                skills = list(catalog.skills)
                if flag is not None:
                    skills = [
                        s for s in skills if getattr(s, "library_enabled", True) is bool(flag)
                    ]
                return {"project_id": catalog.project_id, "skills": dump(skills)}

            return _ok(await run_with_skill_service(user_id, _list))

        if action == "get":
            detail = await run_with_skill_service(
                user_id, lambda svc: svc.get_skill_detail(user_id, skill_id, project_id=project_id)
            )
            return _ok({"skill": dump(detail)})

        if action == "files":
            nodes = await run_with_skill_service(
                user_id, lambda svc: svc.list_skill_files(user_id, skill_id, project_id=project_id)
            )
            return _ok({"skill_id": skill_id, "files": dump(nodes)})

        if action == "read_file":
            path = str(args.get("path") or "").strip()
            if not path:
                return _err("'path' is required for read_file")
            content = await run_with_skill_service(
                user_id,
                lambda svc: svc.read_skill_file(user_id, skill_id, path, project_id=project_id),
            )
            return _ok({"skill_id": skill_id, "file": dump(content)})

        if action == "create":
            name = str(args.get("name") or "").strip()
            body = str(args.get("instructions_markdown") or "")
            if not name or not body.strip():
                return _err("'name' and 'instructions_markdown' are required for create")
            payload = SkillCreateRequest(
                name=name,
                description=str(args.get("description") or ""),
                target_scope=scope,
                project_id=project_id,
                instructions_markdown=body,
                add_to_project=bool(args.get("add_to_project", False)),
            )
            view = await run_with_skill_service(
                user_id, lambda svc: svc.create_skill(user_id, payload)
            )
            return _ok({"skill": dump(view)})

        if action == "update":
            fields = {
                k: args.get(k)
                for k in ("name", "description", "instructions_markdown")
                if args.get(k) is not None
            }
            if not fields:
                return _err("update needs at least one of name/description/instructions_markdown")
            payload = SkillUpdateRequest(**fields)
            view = await run_with_skill_service(
                user_id,
                lambda svc: svc.update_skill(user_id, skill_id, payload, project_id=project_id),
            )
            return _ok({"skill": dump(view)})

        if action in ("enable", "disable"):
            enabled = action == "enable"
            detail = await run_with_skill_service(
                user_id, lambda svc: svc.set_library_enabled(user_id, skill_id, enabled)
            )
            return _ok({"skill": dump(detail), "library_enabled": enabled})

        if action == "delete":
            confirm = bool(args.get("confirm", False))
            mode = "confirm" if confirm else "dry_run"
            result = await run_with_skill_service(
                user_id,
                lambda svc: svc.delete_skill(user_id, skill_id, project_id=project_id, mode=mode),
            )
            if confirm:
                return _ok({"deleted": skill_id})
            return _ok(
                {
                    "skill_id": skill_id,
                    "would_affect": dump(result),
                    "next_step": "Show this to the user; call delete again with confirm=true.",
                }
            )

        if action in ("import_url", "import_archive"):
            add = bool(args.get("add_to_project", False))
            if action == "import_url":
                url = str(args.get("url") or "").strip()
                if not url:
                    return _err("'url' is required for import_url")

                async def _preview(svc: Any) -> Any:
                    return await svc.import_url_preview(
                        user_id, url=url, target_scope=scope, project_id=project_id
                    )

                kind = "url"
            else:
                archive_path = str(args.get("archive_path") or "").strip()
                if not archive_path:
                    return _err("'archive_path' is required for import_archive")

                async def _preview(svc: Any) -> Any:
                    return await svc.import_archive_preview(
                        user_id, archive_path, scope, project_id=project_id
                    )

                kind = "archive"

            preview = await run_with_skill_service(user_id, _preview)
            candidates = _candidates(preview)
            if not bool(args.get("confirm", False)):
                return _ok(
                    {
                        "import_kind": kind,
                        "candidates": dump(candidates),
                        "preview": dump(preview),
                        "next_step": (
                            "Ask the user which candidates to import, then call import_confirm "
                            "with each preview_id (valid for 10 minutes), or re-run with "
                            "confirm=true to import all."
                        ),
                    }
                )
            imported = []
            for cand in candidates:
                imported.append(
                    await _confirm_import(
                        user_id,
                        kind,
                        preview_id=cand.preview_id,
                        name=None,
                        scope=scope,
                        project_id=project_id,
                        add_to_project=add,
                    )
                )
            return _ok({"import_kind": kind, "imported": dump(imported)})

        if action == "import_confirm":
            preview_id = str(args.get("preview_id") or "").strip()
            if not preview_id:
                return _err("'preview_id' is required for import_confirm")
            kind = str(args.get("import_kind") or "url")
            if kind not in ("url", "archive"):
                return _err("'import_kind' must be url|archive")
            view = await _confirm_import(
                user_id,
                kind,
                preview_id=preview_id,
                name=str(args.get("name") or "").strip() or None,
                scope=scope,
                project_id=project_id,
                add_to_project=bool(args.get("add_to_project", False)),
            )
            return _ok({"skill": dump(view)})

        if action == "versions":
            versions = await run_with_skill_service(
                user_id, lambda svc: svc.list_versions(user_id, skill_id)
            )
            return _ok({"skill_id": skill_id, "versions": dump(versions)})

        if action == "restore":
            revision_id = str(args.get("revision_id") or "").strip()
            if not revision_id:
                return _err("'revision_id' is required for restore")
            result = await run_with_skill_service(
                user_id, lambda svc: svc.restore_version(user_id, skill_id, revision_id)
            )
            return _ok({"restored": dump(result)})
    except KeyError:
        return _err(f"skill '{skill_id}' not found")
    except (ValueError, ValuzError) as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("skill_library tool failed")
        return _err(f"failed: {exc}")
    return _err("unhandled action")


async def _confirm_import(
    user_id: str,
    kind: str,
    *,
    preview_id: str,
    name: str | None,
    scope: str,
    project_id: str | None,
    add_to_project: bool,
) -> Any:
    from valuz_agent.modules.skills.models import (
        SkillImportArchiveConfirmRequest,
        SkillImportUrlConfirmRequest,
    )

    if kind == "url":
        payload = SkillImportUrlConfirmRequest(
            preview_id=preview_id,
            name=name,
            target_scope=scope,
            project_id=project_id,
            add_to_project=add_to_project,
        )
        return await run_with_skill_service(
            user_id, lambda svc: svc.confirm_url_import(user_id, payload)
        )
    payload = SkillImportArchiveConfirmRequest(
        preview_id=preview_id,
        name=name,
        target_scope=scope,
        project_id=project_id,
        add_to_project=add_to_project,
    )
    return await run_with_skill_service(
        user_id, lambda svc: svc.confirm_archive_import(user_id, payload)
    )


def build_skill_library_tool_defs() -> tuple[ToolDef, ...]:
    """The ``skill_library`` management tool for the host toolkit MCP server."""
    return (
        ToolDef(
            name=SKILL_LIBRARY_TOOL_NAME,
            description=SKILL_LIBRARY_DESCRIPTION,
            parameters=SKILL_LIBRARY_PARAMETERS,
            handler=_handler,
            read_only=False,
        ),
    )

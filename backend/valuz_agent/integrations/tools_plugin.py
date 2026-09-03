"""``plugin`` toolkit tool — the Plugins page operations for the agent.

Preview / install from a local archive or directory, an http(s) zip URL, a
marketplace item, or a GitHub address (repository root or a
``/tree/<ref>/<dir>`` folder — fetched with the skills importer's GitHub
downloader, packaged locally with ``zip_plugin_root``, then installed like
an uploaded archive); enable / disable / update / uninstall / export; list,
inspect, memberships. Same ``PluginService`` and rules as
``api/routes/plugins.py``; owner from ``ctx.user_id``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.infra.errors import ValuzError
from valuz_agent.integrations.tools_entity_common import dump, run_with_skill_service

logger = logging.getLogger(__name__)

PLUGIN_TOOL_NAME = "plugin"
_ACTIONS = (
    "list",
    "get",
    "preview",
    "install",
    "enable",
    "disable",
    "update",
    "uninstall",
    "export",
    "memberships",
)
_CONFLICTS = ("skip", "overwrite")

PLUGIN_TOOL_DESCRIPTION = (
    "Manage the user's plugins (bundles of skills + connectors) — the same "
    "operations as the Plugins page.\n"
    "- list / get: installed plugins (id, name, version, source, enabled, members).\n"
    "- preview: inspect a plugin BEFORE installing — its manifest, members, "
    "conflicts with existing skills/connectors, and whether a plugin of that "
    "name already exists. Same `source` parameters as install; no side effects.\n"
    "- install: install from exactly one source: `path` (a local .zip or an "
    "unpacked plugin directory), `url` (an http(s) zip, or a GitHub address: "
    "github.com/<owner>/<repo> or github.com/<owner>/<repo>/tree/<ref>/<dir> — "
    "the contents are downloaded, packaged locally and installed), or "
    "`market_item_id`. `on_conflict` skip|overwrite decides what happens to "
    "members that already exist (default skip). Run preview first when the "
    "user has not seen what the plugin contains.\n"
    "- enable / disable: the plugin switch (members follow).\n"
    "- update: re-fetch from the plugin's recorded source (url / path / market); "
    "a plugin installed from a one-off archive or a GitHub address has no "
    "re-fetchable source — reinstall it instead.\n"
    "- uninstall: remove the plugin and the members only it owns (built-in "
    "plugins can only be disabled). Irreversible — confirm with the user first.\n"
    "- export: write the plugin as a zip into this session's workspace and "
    "return the path (refused for plugins with protected members).\n"
    "- memberships: which plugins own the given `kind` skill|connector `slugs`."
)

PLUGIN_TOOL_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(_ACTIONS), "description": "See above."},
        "plugin_id": {"type": "string", "description": "Target plugin id (get/enable/...)."},
        "path": {
            "type": "string",
            "description": "preview/install: local zip file or plugin directory.",
        },
        "url": {
            "type": "string",
            "description": "preview/install: http(s) zip URL or GitHub address.",
        },
        "market_item_id": {
            "type": "string",
            "description": "preview/install: marketplace item id.",
        },
        "on_conflict": {
            "type": "string",
            "enum": list(_CONFLICTS),
            "description": "install/update: what to do with members that already exist.",
        },
        "kind": {
            "type": "string",
            "enum": ["skill", "connector"],
            "description": "memberships: member kind.",
        },
        "slugs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "memberships: member slugs to look up.",
        },
    },
    "required": ["action"],
}


def _err(message: str) -> ToolResult:
    return ToolResult(content=f"plugin: {message}", is_error=True)


def _ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(content=json.dumps({"ok": True, **payload}, ensure_ascii=False))


def _fetch_github_plugin_zip(skills: Any, url: str) -> bytes:
    """Download the GitHub address into a temp dir and package it as a plugin
    zip (blocking — run in a thread). The skill library service already knows
    how to turn a repository / tree URL into a directory (its importer's
    ``_fetch_github_tree``); the plugin manifest helpers then locate the plugin
    root inside it and zip exactly that."""
    from valuz_agent.modules.plugins.manifest import find_plugin_root, zip_plugin_root

    with tempfile.TemporaryDirectory(prefix="valuz-plugin-github-") as tmp:
        staging = Path(tmp) / "src"
        staging.mkdir()
        skills._fetch_github_tree(url, staging)
        root = find_plugin_root(staging) or staging
        return zip_plugin_root(root)


async def _resolve_source(args: dict[str, Any], skills: Any) -> tuple[dict[str, Any], str]:
    """``PluginService.preview/install`` keyword arguments for the requested
    source, plus a label for the reply. Exactly one source is accepted."""
    path = str(args.get("path") or "").strip() or None
    url = str(args.get("url") or "").strip() or None
    market_item_id = str(args.get("market_item_id") or "").strip() or None
    given = [k for k, v in (("path", path), ("url", url), ("market_item_id", market_item_id)) if v]
    if len(given) != 1:
        raise ValueError("pass exactly one of path / url / market_item_id")
    if path:
        return {"path": path}, f"path:{path}"
    if market_item_id:
        return {"market_item_id": market_item_id}, f"market:{market_item_id}"
    assert url is not None
    if skills._is_github_url(url):
        data = await asyncio.to_thread(_fetch_github_plugin_zip, skills, url)
        return {"zip_bytes": data}, f"github:{url}"
    return {"url": url}, f"url:{url}"


async def _with_plugin_service(user_id: str, fn: Any) -> Any:
    """``PluginService`` wired like ``api/routes/plugins._get_plugin_service``:
    the skill service from its own unit of work (see ``run_with_skill_service``),
    the plugin datastore / connectors / marketplace installs from another."""

    async def _inner(skills: Any) -> Any:
        from valuz_agent.api.routes.marketplace import _market_index_client
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.connectors.datastore import ConnectorDatastore
        from valuz_agent.modules.connectors.service import ConnectorService
        from valuz_agent.modules.marketplace.install_store import MarketplaceInstallStore
        from valuz_agent.modules.plugins.datastore import PluginDatastore
        from valuz_agent.modules.plugins.service import PluginService

        async with async_unit_of_work() as db:
            svc = PluginService(
                datastore=PluginDatastore(db),
                skill_service=skills,
                connector_service=ConnectorService(ConnectorDatastore(db)),
                market=_market_index_client(),
                installs=MarketplaceInstallStore(db),
            )
            return await fn(svc, skills)

    return await run_with_skill_service(user_id, _inner)


async def _handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    user_id = ctx.user_id
    action = args.get("action")
    if action not in _ACTIONS:
        return _err("'action' must be " + "|".join(_ACTIONS))
    plugin_id = str(args.get("plugin_id") or "").strip() or None
    if action in ("get", "enable", "disable", "update", "uninstall", "export") and not plugin_id:
        return _err(f"'plugin_id' is required for {action}")
    on_conflict = str(args.get("on_conflict") or "skip")
    if on_conflict not in _CONFLICTS:
        return _err("'on_conflict' must be skip|overwrite")

    try:
        if action == "list":
            views = await _with_plugin_service(user_id, lambda svc, _s: svc.list_plugins(user_id))
            return _ok({"plugins": dump(views)})
        if action == "get":
            view = await _with_plugin_service(
                user_id, lambda svc, _s: svc.get_plugin(user_id, plugin_id)
            )
            return _ok({"plugin": dump(view)})
        if action in ("preview", "install"):

            async def _preview_or_install(svc: Any, skills: Any) -> tuple[str, Any]:
                source, label = await _resolve_source(args, skills)
                if action == "preview":
                    return label, await svc.preview(user_id, **source)
                return label, await svc.install(user_id, on_conflict=on_conflict, **source)

            label, result = await _with_plugin_service(user_id, _preview_or_install)
            if action == "preview":
                return _ok(
                    {
                        "source": label,
                        "preview": dump(result),
                        "next_step": "Show members/conflicts to the user, then install.",
                    }
                )
            return _ok({"source": label, "result": dump(result)})
        if action in ("enable", "disable"):
            enabled = action == "enable"
            view = await _with_plugin_service(
                user_id, lambda svc, _s: svc.set_enabled(user_id, plugin_id, enabled)
            )
            return _ok({"plugin": dump(view)})
        if action == "update":
            result = await _with_plugin_service(
                user_id, lambda svc, _s: svc.update(user_id, plugin_id, on_conflict=on_conflict)
            )
            return _ok({"result": dump(result)})
        if action == "uninstall":
            result = await _with_plugin_service(
                user_id, lambda svc, _s: svc.uninstall(user_id, plugin_id)
            )
            return _ok({"uninstalled": plugin_id, "result": dump(result)})
        if action == "export":
            filename, data = await _with_plugin_service(
                user_id, lambda svc, _s: svc.export_zip(user_id, plugin_id)
            )
            workspace = Path(ctx.workspace) if getattr(ctx, "workspace", None) else Path.cwd()
            target = workspace / filename
            target.write_bytes(data)
            return _ok({"path": str(target), "bytes": len(data)})
        if action == "memberships":
            kind = str(args.get("kind") or "")
            slugs = [str(s) for s in (args.get("slugs") or []) if str(s).strip()]
            if kind not in ("skill", "connector") or not slugs:
                return _err("memberships needs `kind` skill|connector and non-empty `slugs`")
            refs = await _with_plugin_service(
                user_id, lambda svc, _s: svc.memberships(user_id, kind, slugs)
            )
            return _ok({"kind": kind, "memberships": dump(refs)})
    except (ValueError, ValuzError) as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("plugin tool failed")
        return _err(f"failed: {exc}")
    return _err("unhandled action")


def build_plugin_tool_defs() -> tuple[ToolDef, ...]:
    """The ``plugin`` management tool for the host toolkit MCP server."""
    return (
        ToolDef(
            name=PLUGIN_TOOL_NAME,
            description=PLUGIN_TOOL_DESCRIPTION,
            parameters=PLUGIN_TOOL_PARAMETERS,
            handler=_handler,
            read_only=False,
        ),
    )

"""``app_plugin_manager`` harness tool (R3; docs plugin-development/05).

One action-dispatch tool, like ``plugin`` (``tools_plugin.py``), through which an
agent builds, installs, debugs and publishes a Valuz App Plugin (the
``Plugins`` page and ``valuz plugin app`` do the same through the same
``app_plugin_service`` — there is no extra channel).

Read-only actions (``status`` / ``validate`` / ``pack`` / ``logs`` / ``reload`` /
``submissions``) act directly. ``dev_link`` / ``install`` / ``uninstall`` /
``publish`` never do: they PROPOSE an ``app_plugin.*`` operation
(``modules/app_plugins/operations``) and return the operation envelope the chat
card reads (same shape as ``submit_skill``), so the user's confirm — not the
agent — is what installs, links, removes or uploads code.

Rules enforced here:

* install-type actions (``dev_link`` / ``install`` / ``uninstall`` / ``reload``)
  only on a local deployment — a cloud session's workspace is not on the machine
  the plugin would run on; ``publish`` only uploads and works anywhere;
* ``dev_link`` only accepts a directory INSIDE the session workspace (symlinks
  resolved) — an agent cannot turn an arbitrary directory into a plugin;
* relative paths resolve against the session workspace;
* the owner is ``ExecContext.user_id``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

from src.core import ToolDef, ToolResult
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401  (sets kernel import path)
from valuz_agent.infra.errors import ValuzError
from valuz_agent.ports.workspace_sync import ensure_readable

logger = logging.getLogger(__name__)

APP_PLUGIN_MANAGER_TOOL_NAME = "app_plugin_manager"
_ACTIONS = (
    "status",
    "validate",
    "pack",
    "dev_link",
    "install",
    "uninstall",
    "reload",
    "logs",
    "publish",
    "submissions",
)
#: Actions that change what is installed on THIS machine.
_LOCAL_ONLY = frozenset({"dev_link", "install", "uninstall", "reload"})
_SCOPES = ("personal", "org", "global")
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.[a-z0-9][a-z0-9-]*$")
_LOG_LIMIT_DEFAULT = 100
_LOG_LIMIT_MAX = 500
_NOTES_MAX = 2000

APP_PLUGIN_MANAGER_DESCRIPTION = (
    "Build, install, debug and publish Valuz App Plugins — the same operations "
    "as the App Plugins section of the Plugins page.\n"
    "App Plugins extend the application interface and features; Agent Plugins bundle skills "
    "and MCP connectors and are managed separately through `valuz plugin agent`.\n"
    'Flow for "build me an app plugin": (1) load the `valuz-plugin-dev` skill '
    "and write the plugin "
    "in the workspace with the `valuz-plugin` command (create / build / test / validate); "
    "(2) `validate` the directory and fix every error; (3) `dev_link` {path} to try it live, "
    "or `pack` then `install` {source_path: the zip} for a fixed version — BOTH show the user "
    "a confirmation card and nothing happens until they confirm; stop and wait. A dev link "
    "is confirmed once: later rebuilds reload by themselves, without another card; "
    "(4) after they confirm, read `status` and `logs` {id} — you cannot see their screen, the "
    "log is where load and runtime errors show up, and the user judges how it looks; after "
    "every rebuild call `reload` {id} if the plugin is not showing the change; (5) to share "
    "it, `pack` then `publish` {path or id, scope} — also a card, needs a Valuz account.\n"
    "Actions:\n"
    "- status: every plugin (id, version, status enabled|disabled|incompatible|broken|"
    "requires-unmet|blocked + reason, source, permissions, revision, dev directory); pass "
    "`id` for one.\n"
    "- validate {path}: check a plugin directory (or zip) — errors / warnings / manifest. "
    "No side effects.\n"
    "- pack {path, out_dir?}: zip a plugin directory; returns the zip path, sha256 and size.\n"
    "- dev_link {path}: link a plugin directory for live development (confirmation card). "
    "`path` must be inside this session's workspace.\n"
    "- install {source_path | url}: install a zip or directory (confirmation card with the "
    "plugin's id, version, publisher, sha256, permissions — new permissions highlighted on "
    "an update — and anything it still requires). A higher version of an installed id "
    "updates it.\n"
    "- uninstall {id, purge_data?}: remove a plugin; `purge_data` also deletes its stored "
    "data (confirmation card, destructive).\n"
    "- reload {id}: reload a dev-linked plugin after a rebuild (no card).\n"
    "- logs {id, limit?}: the plugin's recent log lines (frontend / backend / audit).\n"
    "- publish {path | id, scope, distribution_ids?, notes?}: upload a packed plugin to "
    "scope personal (only the user), org (their organization, after review) or global "
    "(the marketplace of the given distributions, after platform review) — confirmation "
    "card. `path` is a plugin directory or a packed zip; `id` publishes an installed / "
    "dev-linked plugin.\n"
    "- submissions: the user's publish submissions and their review state.\n"
    "Install-type actions (dev_link, install, uninstall, reload) only work on a local "
    "deployment; publish works anywhere. Only call interfaces the plugin declared in its "
    "manifest `permissions`."
)

APP_PLUGIN_MANAGER_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(_ACTIONS), "description": "See above."},
        "id": {
            "type": "string",
            "description": "Plugin id <publisher>.<name> (status/uninstall/reload/logs/publish).",
        },
        "path": {
            "type": "string",
            "description": (
                "validate / pack / dev_link / publish: a plugin directory (publish and "
                "validate also take a zip). Relative paths resolve against the session workspace."
            ),
        },
        "out_dir": {"type": "string", "description": "pack: where to write the zip."},
        "source_path": {
            "type": "string",
            "description": "install: a plugin zip or directory (relative to the workspace).",
        },
        "url": {"type": "string", "description": "install: an http(s) URL of a plugin zip."},
        "purge_data": {
            "type": "boolean",
            "description": "uninstall: also delete the plugin's stored data (default false).",
        },
        "limit": {
            "type": "integer",
            "description": f"logs: how many recent lines (default {_LOG_LIMIT_DEFAULT}, "
            f"max {_LOG_LIMIT_MAX}).",
        },
        "scope": {
            "type": "string",
            "enum": list(_SCOPES),
            "description": "publish: personal | org | global.",
        },
        "distribution_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "publish, scope global: the distributions to list it in.",
        },
        "notes": {"type": "string", "description": "publish: a note for the reviewer."},
    },
    "required": ["action"],
}


class _ToolError(Exception):
    """A problem to report to the agent as a failed tool result."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _ok(action: str, **payload: Any) -> ToolResult:
    return ToolResult(content=_json({"ok": True, "action": action, **payload}))


def _fail(action: str, code: str, message: str, **extra: Any) -> ToolResult:
    """A failure still speaks the envelope (``ok: false`` + ``action``), so a card
    that reads it shows the failure instead of falling back to a blank state."""
    return ToolResult(
        content=_json(
            {"ok": False, "action": action, "error_code": code, "message": message, **extra}
        ),
        is_error=True,
    )


# ── Paths ────────────────────────────────────────────────────────────────


async def _session_workspace(ctx: ExecContext) -> Path | None:
    """The session's working directory: ``ExecContext.workspace``, else (the
    toolkit MCP path leaves it empty) the kernel session's ``cwd``."""
    raw: str = ctx.workspace or ""
    if not raw and ctx.session_id:
        try:
            from valuz_agent.adapters import kernel_client

            session = await kernel_client.get_session(ctx.user_id, ctx.session_id)
            raw = (getattr(session, "cwd", "") or "") if session is not None else ""
        except Exception:  # noqa: BLE001 — no workspace is reported by the callers
            logger.debug("app_plugin_manager: could not resolve the session cwd", exc_info=True)
            raw = ""
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        return None


def _resolve(raw: str, workspace: Path | None, what: str) -> Path:
    """``raw`` as an absolute, symlink-resolved path; relative → the workspace."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        if workspace is None:
            raise _ToolError(
                "no_workspace", f"'{what}' is relative but this session has no workspace directory"
            )
        path = workspace / path
    try:
        return path.resolve()
    except OSError as exc:
        raise _ToolError("invalid_path", f"cannot resolve '{what}': {exc}") from exc


def _required(args: dict[str, Any], key: str, action: str) -> str:
    value = str(args.get(key) or "").strip()
    if not value:
        raise _ToolError("missing_argument", f"'{key}' is required for {action}")
    return value


def _plugin_id(args: dict[str, Any], action: str) -> str:
    plugin_id = _required(args, "id", action)
    if not _PLUGIN_ID_RE.fullmatch(plugin_id):
        raise _ToolError("invalid_id", f"'{plugin_id}' is not a plugin id (<publisher>.<name>)")
    return plugin_id


# ── Shapes ───────────────────────────────────────────────────────────────


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "version",
        "name",
        "publisher",
        "source",
        "status",
        "status_reason",
        "enabled",
        "permissions",
        "requires",
        "unmet_requires",
        "revision",
        "dev_path",
        "sha256",
        "installed_at",
    )
    return {k: item.get(k) for k in keys if k in item}


def _operation(row: Any) -> dict[str, Any]:
    """The operation envelope the client's card reads (same shape the skill and
    playbook tools emit, so ``parseOperationToolOutput`` needs no new case)."""
    return {
        "id": row.id,
        "operation_type": row.operation_type,
        "operation_version": row.operation_version,
        "project_id": row.project_id,
        "actor_kind": row.actor_kind,
        "actor_id": row.actor_id,
        "origin_session_id": row.origin_session_id,
        "origin_tool_call_id": row.origin_tool_call_id,
        "origin_playbook_run_id": row.origin_playbook_run_id,
        "origin_automation_run_id": row.origin_automation_run_id,
        "target_refs": row.target_refs,
        "state": row.state,
        "risk_level": row.risk_level,
        "confirmation_policy": row.confirmation_policy,
        "proposal_hash": row.proposal_hash,
        "preview": row.preview,
        "input_payload": row.input_payload,
        "expected_revisions": row.expected_revisions,
        "canonical_result_refs": row.canonical_result_refs,
        "result_payload": row.result_payload,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _preview(action: str, info: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """The card's content from ``app_plugin_service.inspect`` of what is proposed."""
    manifest = info.get("manifest") or {}
    publisher = manifest.get("publisher")
    existing = info.get("existing") or {}
    return {
        "kind": "app_plugin",
        "action": action,
        "id": manifest.get("id"),
        "version": manifest.get("version"),
        "name": manifest.get("name"),
        "description": manifest.get("description"),
        "publisher": publisher.get("name") if isinstance(publisher, dict) else publisher,
        "source": source,
        "path": source.get("path"),
        "sha256": info.get("sha256"),
        "size": info.get("size"),
        "permissions": list(info.get("permissions") or manifest.get("permissions") or []),
        "added_permissions": list(info.get("added_permissions") or []),
        "requires": list(info.get("requires") or manifest.get("requires") or []),
        "unmet_requires": list(info.get("unmet_requires") or []),
        "has_backend": bool(info.get("has_backend")),
        # ``existing.version`` is what the card reads; the flat copy is for the tool message.
        "existing": {"version": existing["version"]}
        if isinstance(existing, dict) and existing.get("version")
        else None,
        "existing_version": existing.get("version") if isinstance(existing, dict) else None,
        "warnings": list(info.get("warnings") or []),
    }


def _manifest_id(info: dict[str, Any], what: str) -> str:
    plugin_id = str((info.get("manifest") or {}).get("id") or "")
    if not plugin_id:
        raise _ToolError("invalid_manifest", f"{what} has no readable plugin manifest")
    return plugin_id


def _check_inspected(info: dict[str, Any], what: str) -> None:
    errors = [str(e) for e in info.get("errors") or []]
    if errors:
        raise _ToolError(
            "invalid_manifest",
            f"{what} is not a valid plugin: {'; '.join(errors)}. Fix these, `validate` again "
            "and retry.",
            errors=errors,
        )


async def _propose(
    ctx: ExecContext,
    action: str,
    operation_type: str,
    *,
    plugin_id: str,
    input_payload: dict[str, Any],
    preview: dict[str, Any],
    message: str,
    expected_revisions: dict[str, Any] | None = None,
) -> ToolResult:
    session_id = (ctx.session_id or "").strip()
    if not session_id:
        raise _ToolError(
            "no_session",
            "session id is empty in ExecContext — cannot attach a confirmation card. "
            "Ask the user to retry the session.",
        )
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.app_plugins.operations import propose_app_plugin_operation

    async with async_unit_of_work() as db:
        row = await propose_app_plugin_operation(
            db,
            ctx.user_id,
            session_id,
            operation_type,
            plugin_id=plugin_id,
            input_payload=input_payload,
            preview=preview,
            expected_revisions=expected_revisions,
        )
        envelope = _operation(row)
    logger.info(
        "app_plugin_manager: proposed %s for %s (operation %s)", action, plugin_id, envelope["id"]
    )
    return ToolResult(
        content=_json({"ok": True, "action": action, "message": message, "operation": envelope})
    )


_WAIT_FOR_USER = (
    " The user is shown a confirmation card in the chat. Stop here and wait: do not call "
    "this action again; once they confirm, check `status` / `logs`."
)


def _require_local() -> None:
    from valuz_agent.infra.config import settings

    if settings.deployment_type != "local":
        raise _ToolError(
            "local_only",
            "installing, linking, removing and reloading plugins only works in a local "
            "session (the desktop app). You can still write, validate, pack and publish; "
            "ask the user to install on their desktop.",
        )


# ── Actions ──────────────────────────────────────────────────────────────


async def _status(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    from valuz_agent.modules.app_plugins.service import app_plugin_service

    listing = await app_plugin_service.list(ctx.user_id)
    plugins = [dict(p) for p in listing.get("plugins") or []]
    wanted = str(args.get("id") or "").strip()
    if wanted:
        plugins = [p for p in plugins if p.get("id") == wanted]
        if not plugins:
            raise _ToolError("not_found", f"no installed plugin with id '{wanted}'")
    return _ok(
        "status",
        api_version=listing.get("api_version"),
        safe_mode=bool(listing.get("safe_mode")),
        safe_mode_reason=listing.get("safe_mode_reason"),
        generation=listing.get("generation"),
        plugins=[_summary(p) for p in plugins],
    )


async def _validate(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    from valuz_agent.modules.app_plugins.service import app_plugin_service

    path = _resolve(_required(args, "path", "validate"), await _session_workspace(ctx), "path")
    await ensure_readable(ctx.user_id, [path])
    result = await asyncio.to_thread(app_plugin_service.validate, str(path))
    # The service's own ``ok`` (is the plugin valid) must not shadow the envelope's
    # ``ok`` (did this tool call work): it is reported as ``valid``.
    return _ok(
        "validate",
        path=str(path),
        valid=bool(result.get("ok", not result.get("errors"))),
        errors=list(result.get("errors") or []),
        warnings=list(result.get("warnings") or []),
        manifest=result.get("manifest"),
    )


async def _pack(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    from valuz_agent.modules.app_plugins.service import app_plugin_service

    workspace = await _session_workspace(ctx)
    path = _resolve(_required(args, "path", "pack"), workspace, "path")
    out_dir = (
        str(_resolve(str(args["out_dir"]), workspace, "out_dir"))
        if str(args.get("out_dir") or "").strip()
        else None
    )
    await ensure_readable(ctx.user_id, [path])
    result = await asyncio.to_thread(app_plugin_service.pack, str(path), out_dir)
    return _ok("pack", **result)


async def _reload(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    from valuz_agent.modules.app_plugins.service import app_plugin_service

    plugin_id = _plugin_id(args, "reload")
    result = await app_plugin_service.reload(plugin_id)
    plugin = result.get("plugin") or {}
    return _ok(
        "reload",
        message=(
            f"Reloaded {plugin_id} (revision {plugin.get('revision')}). The app reloads it "
            "in place; read `logs` to see whether it loaded."
        ),
        plugin=_summary(plugin),
    )


async def _logs(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    plugin_id = _plugin_id(args, "logs")
    try:
        limit = int(args.get("limit") or _LOG_LIMIT_DEFAULT)
    except (TypeError, ValueError):
        limit = _LOG_LIMIT_DEFAULT
    limit = max(1, min(limit, _LOG_LIMIT_MAX))
    from valuz_agent.modules.app_plugins.logs import read_log

    entries = await asyncio.to_thread(read_log, plugin_id, limit)
    return _ok("logs", id=plugin_id, entries=entries, count=len(entries))


async def _submissions(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    publisher = _publisher()
    return _ok("submissions", submissions=await publisher.submissions(ctx.user_id))


def _publisher() -> Any:
    from valuz_agent.ports.extensions import ext

    if ext.app_plugin_publisher is None:
        raise _ToolError(
            "publish_unavailable",
            "publishing needs a Valuz account: sign in to Valuz (this build has no plugin "
            "catalog to publish to).",
        )
    return ext.app_plugin_publisher


async def _propose_install(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    from valuz_agent.modules.app_plugins.operations import APP_PLUGIN_INSTALL_OPERATION
    from valuz_agent.modules.app_plugins.service import app_plugin_service

    source_path = str(args.get("source_path") or "").strip()
    url = str(args.get("url") or "").strip()
    if bool(source_path) == bool(url):
        raise _ToolError("missing_argument", "pass exactly one of 'source_path' or 'url'")
    source: dict[str, Any]
    shown: dict[str, Any]
    if source_path:
        path = _resolve(source_path, await _session_workspace(ctx), "source_path")
        await ensure_readable(ctx.user_id, [path])
        source, shown = {"source_path": str(path)}, {"kind": "file", "path": str(path)}
    else:
        if not url.lower().startswith(("http://", "https://")):
            raise _ToolError("invalid_url", "'url' must be an http(s) address")
        source, shown = {"url": url}, {"kind": "url", "url": url}

    info = await app_plugin_service.inspect(source)
    what = source_path or url
    _check_inspected(info, what)
    plugin_id = _manifest_id(info, what)
    preview = _preview("install", info, shown)
    sha256 = info.get("sha256")
    return await _propose(
        ctx,
        "install",
        APP_PLUGIN_INSTALL_OPERATION,
        plugin_id=plugin_id,
        input_payload={
            "id": plugin_id,
            "version": preview["version"],
            "source": source,
            # What the user reviews is what gets installed: re-checked at install.
            "expected_sha256": sha256,
        },
        preview=preview,
        expected_revisions={"sha256": sha256},
        message=(
            f"Proposed installing {plugin_id} {preview['version']}"
            + (
                f" (updating from {preview['existing_version']})"
                if preview["existing_version"]
                else ""
            )
            + "."
            + _WAIT_FOR_USER
        ),
    )


async def _propose_dev_link(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    from valuz_agent.modules.app_plugins.operations import APP_PLUGIN_DEV_LINK_OPERATION
    from valuz_agent.modules.app_plugins.service import app_plugin_service

    raw = _required(args, "path", "dev_link")
    workspace = await _session_workspace(ctx)
    if workspace is None:
        raise _ToolError(
            "no_workspace",
            "dev_link only accepts a directory inside this session's workspace, and this "
            "session has none",
        )
    path = _resolve(raw, workspace, "path")
    if not path.is_relative_to(workspace):
        raise _ToolError(
            "outside_workspace",
            f"dev_link only accepts a directory inside this session's workspace ({workspace}); "
            f"'{raw}' resolves to {path}, which is outside it. Build the plugin inside the "
            "workspace and link that directory.",
        )
    await ensure_readable(ctx.user_id, [path])
    if not path.is_dir():
        raise _ToolError("not_a_directory", f"{path} is not a directory")

    info = await app_plugin_service.inspect({"source_path": str(path)})
    _check_inspected(info, str(path))
    plugin_id = _manifest_id(info, str(path))
    preview = {
        **_preview("dev_link", info, {"kind": "dev", "path": str(path)}),
        # What the card has to say: the user confirms THIS DIRECTORY'S code, once.
        "live_reload": True,
        "workspace": str(workspace),
    }
    return await _propose(
        ctx,
        "dev_link",
        APP_PLUGIN_DEV_LINK_OPERATION,
        plugin_id=plugin_id,
        input_payload={
            "id": plugin_id,
            "version": preview["version"],
            "path": str(path),
            "workspace": str(workspace),
        },
        preview=preview,
        message=(
            f"Proposed linking {plugin_id} from {path} for development. Once confirmed, "
            "every rebuild of this directory reloads in the app without another card."
            + _WAIT_FOR_USER
        ),
    )


async def _propose_uninstall(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    from valuz_agent.modules.app_plugins.operations import APP_PLUGIN_UNINSTALL_OPERATION
    from valuz_agent.modules.app_plugins.service import app_plugin_service

    plugin_id = _plugin_id(args, "uninstall")
    listing = await app_plugin_service.list(ctx.user_id)
    item = next((p for p in listing.get("plugins") or [] if p.get("id") == plugin_id), None)
    if item is None:
        raise _ToolError("not_found", f"no installed plugin with id '{plugin_id}'")
    purge = bool(args.get("purge_data"))
    publisher = item.get("publisher")
    preview = {
        "kind": "app_plugin",
        "action": "uninstall",
        "id": plugin_id,
        "version": item.get("version"),
        "name": item.get("name"),
        "publisher": publisher.get("name") if isinstance(publisher, dict) else publisher,
        "source": item.get("source"),
        "dev_path": item.get("dev_path"),
        "path": item.get("dev_path"),
        "permissions": list(item.get("permissions") or []),
        "automations": [
            a.get("name") for a in item.get("automations") or [] if isinstance(a, dict)
        ],
        "purge_data": purge,
    }
    return await _propose(
        ctx,
        "uninstall",
        APP_PLUGIN_UNINSTALL_OPERATION,
        plugin_id=plugin_id,
        input_payload={"id": plugin_id, "purge_data": purge},
        preview=preview,
        message=(
            f"Proposed uninstalling {plugin_id}"
            + (" and deleting its stored data" if purge else "")
            + "."
            + _WAIT_FOR_USER
        ),
    )


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _publish_archive(args: dict[str, Any], ctx: ExecContext, workspace: Path | None) -> Path:
    """The zip to publish: the given zip, or a fresh pack of the given directory /
    of the installed or dev-linked plugin ``id``."""
    from valuz_agent.modules.app_plugins.service import app_plugin_service

    raw_path = str(args.get("path") or "").strip()
    raw_id = str(args.get("id") or "").strip()
    if bool(raw_path) == bool(raw_id):
        raise _ToolError("missing_argument", "pass exactly one of 'path' or 'id'")
    out_dir: str | None = None
    if str(args.get("out_dir") or "").strip():
        out_dir = str(_resolve(str(args["out_dir"]), workspace, "out_dir"))

    if raw_path:
        path = _resolve(raw_path, workspace, "path")
        await ensure_readable(ctx.user_id, [path])
        if path.is_file():
            return path
        if not path.is_dir():
            raise _ToolError("not_found", f"{path} does not exist")
        source_dir = path
    else:
        plugin_id = _plugin_id(args, "publish")
        listing = await app_plugin_service.list(ctx.user_id)
        item = next((p for p in listing.get("plugins") or [] if p.get("id") == plugin_id), None)
        if item is None:
            raise _ToolError("not_found", f"no installed plugin with id '{plugin_id}'")
        if item.get("dev_path"):
            source_dir = Path(str(item["dev_path"]))
        else:
            from valuz_agent.infra.fs_registry import fs_registry

            source_dir = fs_registry.app_plugin_version_dir(plugin_id, str(item.get("version")))
            # An installed package is read-only: pack it somewhere writable.
            out_dir = out_dir or tempfile.mkdtemp(prefix="valuz-publish-")
        if not source_dir.is_dir():
            raise _ToolError("not_found", f"the plugin directory {source_dir} is gone")

    checked = await asyncio.to_thread(app_plugin_service.validate, str(source_dir))
    if not checked.get("ok", not checked.get("errors")):
        errors = [str(e) for e in checked.get("errors") or []]
        raise _ToolError(
            "invalid_manifest",
            f"{source_dir} does not validate: {'; '.join(errors)}. Fix these and retry.",
            errors=errors,
        )
    packed = await asyncio.to_thread(app_plugin_service.pack, str(source_dir), out_dir)
    return Path(str(packed["path"]))


async def _propose_publish(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    from valuz_agent.modules.app_plugins.operations import APP_PLUGIN_PUBLISH_OPERATION
    from valuz_agent.modules.app_plugins.service import app_plugin_service

    _publisher()  # fail early and clearly, before packing anything
    scope = str(args.get("scope") or "").strip()
    if scope not in _SCOPES:
        raise _ToolError("invalid_scope", f"'scope' must be one of {', '.join(_SCOPES)}")
    raw_distributions = args.get("distribution_ids") or []
    if not isinstance(raw_distributions, list):
        raise _ToolError("invalid_argument", "'distribution_ids' must be a list of strings")
    distribution_ids = [str(d).strip() for d in raw_distributions if str(d).strip()]
    notes = str(args.get("notes") or "").strip()[:_NOTES_MAX]

    workspace = await _session_workspace(ctx)
    archive = await _publish_archive(args, ctx, workspace)
    info = await app_plugin_service.inspect({"source_path": str(archive)})
    _check_inspected(info, str(archive))
    plugin_id = _manifest_id(info, str(archive))
    sha256 = await asyncio.to_thread(_file_sha256, archive)
    preview = {
        **_preview("publish", info, {"kind": "file", "path": str(archive)}),
        "sha256": sha256,
        "scope": scope,
        "distribution_ids": distribution_ids,
        "notes": notes,
        "archive_path": str(archive),
    }
    return await _propose(
        ctx,
        "publish",
        APP_PLUGIN_PUBLISH_OPERATION,
        plugin_id=plugin_id,
        input_payload={
            "id": plugin_id,
            "version": preview["version"],
            "archive_path": str(archive),
            "sha256": sha256,
            "scope": scope,
            "distribution_ids": distribution_ids,
            "notes": notes,
        },
        preview=preview,
        expected_revisions={"sha256": sha256},
        message=(
            f"Proposed publishing {plugin_id} {preview['version']} to scope '{scope}'."
            + _WAIT_FOR_USER
            + " Afterwards `submissions` shows the review state."
        ),
    )


_DISPATCH = {
    "status": _status,
    "validate": _validate,
    "pack": _pack,
    "reload": _reload,
    "logs": _logs,
    "submissions": _submissions,
    "install": _propose_install,
    "dev_link": _propose_dev_link,
    "uninstall": _propose_uninstall,
    "publish": _propose_publish,
}


async def _handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
    action = str(args.get("action") or "")
    if action not in _DISPATCH:
        return _fail(
            action or "unknown", "invalid_action", "'action' must be " + "|".join(_ACTIONS)
        )
    try:
        if action in _LOCAL_ONLY:
            _require_local()
        return await _DISPATCH[action](args, ctx)
    except _ToolError as exc:
        return _fail(action, exc.code, exc.message, **exc.extra)
    except ValuzError as exc:
        extra: dict[str, Any] = {}
        errors = getattr(exc, "errors", None)
        if errors:
            extra["errors"] = [str(e) for e in errors]
        return _fail(action, str(getattr(exc, "code", "service_error")), str(exc), **extra)
    except ValueError as exc:
        return _fail(action, "invalid_request", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("app_plugin_manager %s failed", action)
        return _fail(action, "failed", f"{action} failed: {exc}")


def build_app_plugin_manager_tool_defs() -> tuple[ToolDef, ...]:
    """The ``app_plugin_manager`` tool for the host toolkit MCP server."""
    return (
        ToolDef(
            name=APP_PLUGIN_MANAGER_TOOL_NAME,
            description=APP_PLUGIN_MANAGER_DESCRIPTION,
            parameters=APP_PLUGIN_MANAGER_PARAMETERS,
            handler=_handler,
            read_only=False,
        ),
    )


# Deprecated import aliases; the toolkit only advertises app_plugin_manager.
EXTENSION_MANAGER_TOOL_NAME = "extension_manager"
EXTENSION_MANAGER_DESCRIPTION = APP_PLUGIN_MANAGER_DESCRIPTION
EXTENSION_MANAGER_PARAMETERS = APP_PLUGIN_MANAGER_PARAMETERS
build_extension_manager_tool_defs = build_app_plugin_manager_tool_defs

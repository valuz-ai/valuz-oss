"""Resolve a list of enabled MCP-provider slugs into kernel MCP wire schemas.

The capability resolver receives the slugs the caller chose for a session and
delegates to this module to materialise them. Each provider knows how to
acquire its credentials (OAuth account secret store, future API-key vaults,
etc.) and how to build its URL.

The resulting wire-schema list is handed to the kernel verbatim. The
kernel runtime registers them under their ``name`` so the agent's tool calls
land in the right server.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urlsplit

from app.schemas import (
    McpHttpServerConfigSchema as McpHttpServerConfig,
)
from app.schemas import (
    McpServerConfigSchema as McpServerConfig,
)
from app.schemas import (
    McpStdioServerConfigSchema as McpStdioServerConfig,
)

# Side-effect import — surfaces ``src.core...`` on sys.path.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.service import build_overrides, merge_params_into_url
from valuz_agent.ports.extensions import ext

logger = logging.getLogger(__name__)

_REPORTIFY_MCP_HOST = "mcp.reportify.cn"
_REPORTIFY_REQUEST_TIMEOUT_SECONDS = float(
    os.environ.get("VALUZ_REPORTIFY_REQUEST_TIMEOUT_SECONDS", "600")
)


def _connector_tool_timeout_sec(url: str) -> float | None:
    """Apply the shared Reportify request budget only to Reportify MCPs."""

    return (
        _REPORTIFY_REQUEST_TIMEOUT_SECONDS
        if urlsplit(url).hostname == _REPORTIFY_MCP_HOST
        else None
    )


def _trusted_builtin_server_instructions(row: Any) -> bool:
    """Trust only an immutable first-party catalog identity and endpoint.

    ``connector_type`` is part of the public create payload, so it is only a
    presentation/protection flag—not proof by itself.  The packaged/edition
    catalog is Host-controlled input; both its explicit builtin marker and its
    endpoint must match before the trust bit crosses into the kernel.
    """

    if getattr(row, "connector_type", None) != "builtin":
        return False
    slug = getattr(row, "slug", None)
    actual_url = getattr(row, "url", None)
    if not isinstance(slug, str) or not isinstance(actual_url, str):
        return False

    from valuz_agent.modules.connectors.catalog import load_catalog

    for entry in load_catalog():
        if not isinstance(entry, dict):
            continue
        members = entry.get("connectors")
        candidates = members if isinstance(members, list) else [entry]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if candidate.get("slug") != slug or candidate.get("builtin") is not True:
                continue
            expected_url = candidate.get("url")
            if not isinstance(expected_url, str):
                return False
            return actual_url.rstrip("/") == expected_url.rstrip("/")
    return False


async def _ensure_fresh_oauth_token(
    row: Any, connectors: ConnectorDatastore, token_json: str
) -> str:
    """Proactively refresh an OAuth connector's token if its stored expiry shows
    it has lapsed.

    The resolver builds the server config ahead of time, so it can't react to a
    runtime 401 — instead it refreshes *before* handing the token to the kernel.
    Returns the (possibly refreshed) token JSON; on any failure it returns the
    original blob so the caller still attempts the old token (and the runtime's
    own 401 surfaces normally).
    """
    try:
        return await ext.connector_oauth_refresh.ensure_fresh_token(
            row=row,
            connectors=connectors,
            token_json=token_json,
        )
    except Exception:  # noqa: BLE001
        logger.exception("mcp resolver: connector oauth refresh failed")
        return token_json


async def resolve_mcp_servers(
    *,
    enabled_slugs: list[str],
    connectors: ConnectorDatastore | None = None,
    user_id: str | None = None,
) -> list[McpServerConfig]:
    """Translate enabled MCP-provider slugs into kernel ``McpServerConfig`` rows.

    Connector secrets / OAuth tokens are read off each connector row, so no
    separate secret store is threaded in.
    """
    out: list[McpServerConfig] = []
    seen_names: set[str] = set()

    for slug in enabled_slugs:
        cfgs = await _resolve_connector_slug(slug, connectors, user_id=user_id)
        if cfgs is None:
            logger.info("mcp resolver: slug %s unknown or has no credentials — skipping", slug)
            continue
        for cfg in cfgs:
            if cfg.name in seen_names or cfg.name == "harness":
                continue
            seen_names.add(cfg.name)
            out.append(cfg)

    return out


async def _resolve_connector_slug(
    slug: str,
    connectors: ConnectorDatastore | None,
 user_id: str | None = None) -> list[McpServerConfig] | None:
    if user_id is None:
        raise ValueError("user_id is required")

    if connectors is None:
        return None
    row = await connectors.get_by_slug(user_id, slug)
    if row is None or not row.enabled:
        return None

    if row.transport == "stdio":
        return _build_stdio_config(row)

    return await _build_http_config(row, connectors)


async def _build_http_config(row, connectors: ConnectorDatastore) -> list[McpServerConfig] | None:
    # Single injection truth shared with the probe (Acceptance #8 — probe
    # and runtime must produce byte-identical headers/params).
    headers, params = build_overrides(row)

    if row.auth_type == "oauth":
        # OAuth layers on AFTER build_overrides — it needs a live token.
        token_json = row.oauth_token_json
        if not token_json:
            logger.info("mcp resolver: connector %s oauth token not found", row.slug)
            return None
        # Self-heal an expired token before the runtime ever makes a call.
        token_json = await _ensure_fresh_oauth_token(row, connectors, token_json)
        try:
            token_data = json.loads(token_json)
            access_token = token_data.get("access_token", "")
        except (json.JSONDecodeError, AttributeError):
            return None
        if not access_token:
            return None
        headers["Authorization"] = f"Bearer {access_token}"

    url = row.url or ""
    transport = row.transport if row.transport in ("http", "sse") else "http"

    if not url:
        return None

    tool_timeout_sec = _connector_tool_timeout_sec(url)
    server_instructions_trusted = _trusted_builtin_server_instructions(row)

    if "{module}" in url:
        modules: list[str] = []
        if row.args:
            try:
                parsed = json.loads(row.args)
                if isinstance(parsed, list):
                    modules = [str(m) for m in parsed]
            except json.JSONDecodeError:
                pass
        if not modules:
            return []
        return [
            McpHttpServerConfig(
                name=f"{row.slug}_{module}",
                url=merge_params_into_url(url.replace("{module}", module), params),
                transport=transport,  # type: ignore[arg-type]
                headers=dict(headers),
                tool_timeout_sec=tool_timeout_sec,
                server_instructions_trusted=server_instructions_trusted,
            )
            for module in modules
        ]

    return [
        McpHttpServerConfig(
            name=row.slug,
            url=merge_params_into_url(url, params),
            transport=transport,  # type: ignore[arg-type]
            headers=dict(headers),
            tool_timeout_sec=tool_timeout_sec,
            server_instructions_trusted=server_instructions_trusted,
        )
    ]


def _bundled_mcp_servers_dir() -> str:
    """Absolute path to the bundled MCP server tree
    (``valuz_agent/resources/mcp_servers``), as a POSIX string.

    Bundled stdio connectors reference their entry point with the
    ``{mcp_dir}`` placeholder so the catalog stays path-agnostic and the
    same JSON works in a dev checkout and a PyInstaller-frozen app (where
    ``resources/`` lands under ``_internal/valuz_agent/``).
    """
    from pathlib import Path

    return (Path(__file__).resolve().parent.parent / "resources" / "mcp_servers").as_posix()


def expand_mcp_dir(value: str) -> str:
    """Substitute the ``{mcp_dir}`` placeholder a bundled stdio connector uses
    for its entry point with the absolute bundled-server tree path.

    The single source of truth for this expansion — used both by the runtime
    resolver (``_build_stdio_config``) and the connector test probe, so a
    bundled connector that runs at session time also passes the UI's
    "test connection" probe.
    """
    return value.replace("{mcp_dir}", _bundled_mcp_servers_dir())


def _build_stdio_config(row) -> list[McpServerConfig] | None:
    import shlex
    import shutil

    if not row.command:
        logger.info("mcp resolver: stdio connector %s has no command", row.slug)
        return None

    def _expand(value: str) -> str:
        return expand_mcp_dir(value)

    raw_command = _expand(row.command)
    extra_args: tuple[str, ...] = ()
    if " " in raw_command:
        parts = shlex.split(raw_command)
        raw_command = parts[0]
        extra_args = tuple(parts[1:])

    # Pre-flight the executable. The stdio child is spawned kernel-side; with
    # the default in-process kernel that is THIS process, so a which() miss is
    # definitive — drop the server with an attributable log line instead of
    # every runtime failing (or silently degrading) at turn time with a bare
    # ``[Errno 2] No such file or directory``. A split kernel
    # (``VALUZ_KERNEL_MODE=http``) spawns against ITS own environment, so keep
    # the server there and leave availability to the runtime.
    from valuz_agent.infra.config import settings

    if not settings.is_http_kernel and shutil.which(raw_command) is None:
        logger.warning(
            "mcp resolver: stdio connector %s dropped — command %r not found on PATH",
            row.slug,
            raw_command,
        )
        return None

    args: tuple[str, ...] = extra_args
    if row.args:
        try:
            parsed = json.loads(row.args)
            if isinstance(parsed, list):
                args = extra_args + tuple(_expand(str(a)) for a in parsed)
        except json.JSONDecodeError:
            pass
    env: dict[str, str] = {}
    if row.env_json:
        try:
            parsed_env = json.loads(row.env_json)
            if isinstance(parsed_env, dict):
                env = {str(k): str(v) for k, v in parsed_env.items()}
        except json.JSONDecodeError:
            pass
    # Paid stdio servers (wind/ifind) read their key from the harness process
    # env (inherited by the child), not from connector credentials.
    return [
        McpStdioServerConfig(
            name=row.slug,
            command=raw_command,
            args=args,
            env=env,
        )
    ]


__all__ = ["resolve_mcp_servers"]

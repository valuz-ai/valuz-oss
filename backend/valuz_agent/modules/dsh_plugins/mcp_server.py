"""``valuz-dsh-plugins`` — dsh plugin tools as an always-on MCP server.

The user's dsh plugins load in the resident manager host; its Valuz bundle
plugin ``valuz-tool-bridge`` serves the tools they register over a loopback
JSON API (endpoint + token in ``$VALUZ_DSH_HOME/valuz/tool-bridge.json``).
This server fronts that API for every non-dsh runtime (Claude, Codex,
DeepAgents): ``tools/list`` mirrors the bridge, ``tools/call`` forwards. dsh
sessions load the same plugins natively, so the kernel leaves this server
out of their composition.

When the manager host is not running the server is simply empty — sessions
never wait on a dsh start. ``modules/dsh_plugins/manager`` starts the host
at boot when the profile carries user-installed bundles.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool

from valuz_agent.modules.dsh_plugins.manager import resolve_dsh_home

logger = logging.getLogger(__name__)

SERVER_NAME = "valuz-dsh-plugins"
MOUNT_PATH = "/_internal/mcp/dsh-plugins"
_LIST_TIMEOUT_SECONDS = 5.0
_CALL_TIMEOUT_SECONDS = 600.0

_server: Server | None = None
_manager: StreamableHTTPSessionManager | None = None


def bridge_state_path() -> Path:
    return resolve_dsh_home() / "valuz" / "tool-bridge.json"


def read_bridge() -> tuple[str, str] | None:
    """``(url, token)`` of the live tool bridge, or None."""
    try:
        state = json.loads(bridge_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    url, token = state.get("url"), state.get("token")
    if isinstance(url, str) and isinstance(token, str) and url and token:
        return url.rstrip("/"), token
    return None


async def list_bridge_tools() -> list[dict[str, Any]]:
    bridge = read_bridge()
    if bridge is None:
        return []
    url, token = bridge
    try:
        async with httpx.AsyncClient(timeout=_LIST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{url}/tools", headers={"authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
            tools = response.json()
    except (httpx.HTTPError, ValueError):
        # A stale state file from a host that died without unloading.
        logger.debug("dsh tool bridge unreachable", exc_info=True)
        return []
    return [tool for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str)]


async def call_bridge_tool(name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
    bridge = read_bridge()
    if bridge is None:
        return True, "the dsh plugin host is not running — open Settings → Extensions"
    url, token = bridge
    async with httpx.AsyncClient(timeout=_CALL_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{url}/call",
            headers={"authorization": f"Bearer {token}"},
            json={"name": name, "arguments": arguments},
        )
    if response.status_code != 200:
        return True, f"dsh tool bridge answered HTTP {response.status_code}: {response.text[:300]}"
    body = response.json()
    return bool(body.get("isError")), str(body.get("text") or "")


def _build_server() -> Server:
    server: Server = Server(SERVER_NAME)

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name=tool["name"],
                description=str(tool.get("description") or tool["name"]),
                inputSchema=tool.get("inputSchema") or {"type": "object", "properties": {}},
            )
            for tool in await list_bridge_tools()
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> list[TextContent]:
        is_error, text = await call_bridge_tool(tool_name, dict(arguments or {}))
        # Same convention as the built-in toolkit: an error is a text prefix,
        # never a wire-level failure (some runtimes drop the server for the turn).
        return [TextContent(type="text", text=f"ERROR: {text}" if is_error else text)]

    return server


def _ensure_manager() -> StreamableHTTPSessionManager:
    global _server, _manager
    if _manager is None:
        _server = _build_server()
        _manager = StreamableHTTPSessionManager(app=_server, stateless=True)
    return _manager


@asynccontextmanager
async def dsh_plugins_mcp_session_manager_run() -> Any:
    async with _ensure_manager().run():
        yield


def build_dsh_plugins_mcp_asgi() -> Any:
    """ASGI app for ``/_internal/mcp/dsh-plugins`` (behind the internal-credential gate)."""
    from valuz_agent.integrations._mcp_asgi import build_internal_mcp_asgi

    async def _handle(scope: Any, receive: Any, send: Any) -> None:
        await _ensure_manager().handle_request(scope, receive, send)

    return build_internal_mcp_asgi(_handle)

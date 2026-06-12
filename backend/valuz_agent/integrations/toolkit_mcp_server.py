"""In-process MCP server exposing the host's harness tools.

Why this exists
---------------
The dispatch / memory / submit_skill tool handlers are HOST code (task
orchestration, project memory, skill staging). They used to be pushed
into the kernel's in-process tool registry at boot and surfaced by each
runtime through a runtime-specific path (Claude: in-process SDK MCP
server; Codex: the kernel's ``/mcp/toolkit`` HTTP bridge — which the
host app never even mounted; DeepAgents: in-process callables). That
in-process coupling is the last declared bypass of the kernel seam: a
kernel running in another process has no registry to resolve handlers
from.

This module replaces all of that with the same pattern the docs /
automations / connectors tools already use: the host serves its tools as
an in-process **MCP-over-HTTP** server, and sessions reference it
through ``session.mcp_servers`` (``McpHttpServerConfig`` named
``harness`` — preserving the ``mcp__harness__*`` names Claude models
already see). Every runtime consumes it through its EXISTING MCP-client
path, in-process and remote alike.

Toolsets
--------
Two fixed toolsets mirror the per-session tool surfaces the old
declaration logic produced:

- ``base``  — orchestration launchers + memory + submit_skill; attached
  to every session (chat, project, task member).
- ``lead``  — everything in ``base`` plus the dispatch set
  (dispatch / await_members / review_subtask / finish_task / …);
  attached to task-lead sessions only.

The lead-only handlers also keep their internal run-kind gate — toolset
selection controls *visibility* (prompt surface); the gate stays the
enforcement point.

Wire shape
----------
    POST /internal/mcp/toolkit/{base|lead}
      headers:
        X-Valuz-Internal:    <per-process token>
        X-Valuz-Session-Id:  <kernel session id>

The session id rides a header (not the URL) so each call rebuilds the
handler's ``ExecContext`` server-side — the host's answer to "tool calls
must carry the caller's identity across the wire".
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any
from collections.abc import AsyncIterator

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool

from src.core.tools import ExecContext, ToolDef

logger = logging.getLogger(__name__)

TOOLSET_NAMES = ("base", "lead")

# Bound for the duration of one HTTP request by the ASGI wrapper. Tool
# handlers receive it via the rebuilt ExecContext.
_session_var: ContextVar[str | None] = ContextVar("valuz_toolkit_mcp_session_id", default=None)

# Installed by boot (``install_toolkit_toolsets``) once the tasks
# orchestrator exists; maps toolset name → tool defs.
_TOOLSETS: dict[str, tuple[ToolDef, ...]] = {}
_SERVERS: dict[str, Server] = {}
_MANAGERS: dict[str, StreamableHTTPSessionManager] = {}


def install_toolkit_toolsets(*, base: tuple[ToolDef, ...], lead: tuple[ToolDef, ...]) -> None:
    """Install the tool defs each toolset serves. Called once at boot,
    after the tasks orchestrator (whose services the handlers close over)
    has been constructed. Idempotent — re-installing replaces."""
    _TOOLSETS["base"] = base
    _TOOLSETS["lead"] = lead
    _SERVERS.clear()
    _MANAGERS.clear()
    logger.info(
        "toolkit MCP toolsets installed: base=%d tools, lead=%d tools", len(base), len(lead)
    )


def _current_session_id() -> str:
    sid = _session_var.get()
    if not sid:
        raise RuntimeError("toolkit MCP tool called outside of a session-scoped request")
    return sid


def _build_server(toolset: str) -> Server:
    """Wire the toolset's handlers into a fresh ``mcp.server.Server``.

    Mirrors the kernel's ``mcp_bridge.build_mcp_server_from_toolkit``:
    declarations (``handler is None``) are dropped; ``ToolResult.is_error``
    is surfaced as a text prefix rather than a wire-level failure (a wire
    failure makes some runtimes drop the server for the whole turn).
    """
    tool_defs = _TOOLSETS.get(toolset, ())
    eligible = [t for t in tool_defs if t.handler is not None]
    server: Server = Server(f"valuz-toolkit-{toolset}")
    cached_tools = [
        Tool(
            name=t.name,
            description=t.description or t.name,
            inputSchema=t.parameters or {"type": "object", "properties": {}},
        )
        for t in eligible
    ]
    by_name = {t.name: t for t in eligible}

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def _list_tools() -> list[Tool]:
        return cached_tools

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call_tool(tool_name: str, arguments: dict[str, Any]) -> list[TextContent]:
        tdef = by_name.get(tool_name)
        if tdef is None or tdef.handler is None:
            raise ValueError(f"unknown tool: {tool_name}")
        ctx = ExecContext(session_id=_current_session_id())
        result = await tdef.handler(dict(arguments), ctx)
        text = result.content if not result.is_error else f"ERROR: {result.content}"
        return [TextContent(type="text", text=text)]

    return server


def _ensure_manager(toolset: str) -> StreamableHTTPSessionManager:
    manager = _MANAGERS.get(toolset)
    if manager is None:
        _SERVERS[toolset] = _build_server(toolset)
        manager = StreamableHTTPSessionManager(app=_SERVERS[toolset], stateless=True)
        _MANAGERS[toolset] = manager
    return manager


@asynccontextmanager
async def toolkit_mcp_session_managers_run() -> AsyncIterator[None]:
    """Run both toolsets' session managers for the app's lifetime.

    Same contract as ``docs_mcp_session_manager_run`` — the host lifespan
    keeps this open so the streamable-HTTP background tasks exist.
    """
    base = _ensure_manager("base")
    lead = _ensure_manager("lead")
    async with base.run(), lead.run():
        yield


def build_toolkit_mcp_asgi(toolset: str) -> Any:
    """Return an ASGI app to mount at ``/internal/mcp/toolkit/{toolset}``.

    Each request: verify ``X-Valuz-Internal``, record
    ``X-Valuz-Session-Id`` into the ContextVar, delegate to the toolset's
    session manager.
    """
    from starlette.responses import PlainTextResponse

    if toolset not in TOOLSET_NAMES:
        raise ValueError(f"unknown toolkit toolset: {toolset}")

    async def _app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)
            return

        from valuz_agent.infra.config import settings as _settings

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []
        }
        if headers.get("x-valuz-internal") != _settings.internal_mcp_token:
            response = PlainTextResponse("Forbidden", status_code=403)
            await response(scope, receive, send)
            return

        session_id = headers.get("x-valuz-session-id") or ""
        if not session_id:
            response = PlainTextResponse("Missing X-Valuz-Session-Id header", status_code=400)
            await response(scope, receive, send)
            return

        ctx_token = _session_var.set(session_id)
        try:
            await _ensure_manager(toolset).handle_request(scope, receive, send)
        finally:
            _session_var.reset(ctx_token)

    return _app


def toolkit_mcp_url(*, base_url: str, toolset: str) -> str:
    """Compose the toolkit MCP endpoint a session's MCP client should call.

    The ``/mcp`` inner path keeps the request strictly inside the Starlette
    mount — a bare mount-root URL would draw a 307 redirect, which MCP
    clients don't reliably follow on POST. The stateless session manager
    itself is path-agnostic.
    """
    if toolset not in TOOLSET_NAMES:
        raise ValueError(f"unknown toolkit toolset: {toolset}")
    return f"{base_url.rstrip('/')}/internal/mcp/toolkit/{toolset}/mcp"


__all__ = [
    "TOOLSET_NAMES",
    "build_toolkit_mcp_asgi",
    "install_toolkit_toolsets",
    "toolkit_mcp_session_managers_run",
    "toolkit_mcp_url",
    "_session_var",  # exposed for tests
]

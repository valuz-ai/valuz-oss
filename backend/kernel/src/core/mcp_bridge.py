"""ToolKit -> ``mcp.server.Server`` bridge plus the per-session registry.

This module is the **only** place in the kernel that imports the ``mcp``
protocol library. It does two things:

1. ``build_mcp_server_from_toolkit`` — converts a harness ``ToolKit`` plus
   an ``ExecContext`` into a configured ``mcp.server.Server`` ready to be
   mounted on any MCP transport (Streamable HTTP, stdio, in-process).
2. A small process-global registry (``register_session_toolkit`` /
   ``unregister_session_toolkit`` / ``get_session_record``) that maps a
   session id to its toolkit + ExecContext. The transport layer
   (``app/mcp_toolkit_router.py``) reads this registry to look up the
   right toolkit for an incoming MCP request — so ``CodexRuntime`` only
   ever depends on ``src.core``.

Per ``docs/design/CODEX-CUSTOM-TOOLS-DESIGN.md`` §3.4, this bridge is the
counterpart to Claude Agent SDK's ``create_sdk_mcp_server`` — same
``Server.request_handlers`` shape, but driven from harness primitives so
``core/`` does not depend on ``claude-agent-sdk``.

Used by ``CodexRuntime`` (Streamable HTTP transport on the FastAPI app);
``ClaudeAgentRuntime`` keeps its own SDK-native path and does not call
this module.

**Auth note:** the toolkit endpoint is not authenticated. The harness
backend is expected to bind ``127.0.0.1`` (or a private network) so the
MCP URL is only reachable from the codex subprocess colocated with the
backend. Anyone with shell access to that host could already invoke the
underlying handlers; the URL adds no privilege beyond that.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from mcp.server import Server
from mcp.types import TextContent, Tool

from src.core.tools import ExecContext, ToolKit


@dataclass(frozen=True)
class SessionToolkitRecord:
    """A session's contribution to the global toolkit registry."""

    toolkit: ToolKit
    exec_context: ExecContext


_REGISTRY: dict[str, SessionToolkitRecord] = {}
_REGISTRY_LOCK = threading.Lock()


def register_session_toolkit(
    session_id: str,
    toolkit: ToolKit,
    exec_context: ExecContext,
) -> None:
    """Add (or replace) a session's toolkit + ExecContext entry."""
    with _REGISTRY_LOCK:
        _REGISTRY[session_id] = SessionToolkitRecord(toolkit=toolkit, exec_context=exec_context)


def unregister_session_toolkit(session_id: str) -> None:
    """Drop a session's entry. No-op if absent."""
    with _REGISTRY_LOCK:
        _REGISTRY.pop(session_id, None)


def get_session_record(session_id: str) -> SessionToolkitRecord | None:
    """Lookup helper used by the transport layer."""
    with _REGISTRY_LOCK:
        return _REGISTRY.get(session_id)


def reset_registry_for_tests() -> None:
    """Drop all registry entries — pytest cleanup hook only."""
    with _REGISTRY_LOCK:
        _REGISTRY.clear()


def build_mcp_server_from_toolkit(
    toolkit: ToolKit,
    *,
    name: str,
    exec_context: ExecContext,
    version: str = "1.0.0",
) -> Server:
    """Wire ``toolkit`` handlers into a fresh ``mcp.server.Server``.

    * Tools without a ``handler`` (declarations) are dropped — only callable
      tools are exposed over MCP.
    * Tools with ``permission == "deny"`` are dropped — they should not be
      reachable from any runtime.
    * ``ExecContext`` is captured by closure so the SDK-shaped MCP handler
      ``(name, arguments) -> CallToolResult`` can still invoke our native
      ``ToolHandler`` signature ``(args, ExecContext) -> ToolResult``.
    """
    server: Server = Server(name, version=version)
    eligible = [t for t in toolkit.list_tools() if t.handler is not None and t.permission != "deny"]
    cached_tools = [
        Tool(
            name=t.name,
            description=t.description or t.name,
            inputSchema=t.parameters or {"type": "object", "properties": {}},
        )
        for t in eligible
    ]
    by_name = {t.name: t for t in eligible}

    # ``mcp.server.Server`` decorators are themselves untyped; suppress
    # narrowly so the rest of the kernel stays under strict mode.
    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _list_tools() -> list[Tool]:
        return cached_tools

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call_tool(tool_name: str, arguments: dict[str, object]) -> list[TextContent]:
        tdef = by_name.get(tool_name)
        if tdef is None or tdef.handler is None:
            raise ValueError(f"unknown tool: {tool_name}")
        result = await tdef.handler(dict(arguments), exec_context)
        # Returning a list[TextContent] matches the lowlevel @call_tool
        # decorator contract. ToolResult.is_error carries non-fatal "the
        # tool ran but reported a problem" semantics, which we surface as a
        # TextContent prefix. We don't raise: an exception in the lowlevel
        # handler turns into a wire-level CallTool failure, which Codex
        # surfaces as a tool-call FAILURE — that's not what is_error means.
        prefix = "[error] " if result.is_error else ""
        return [TextContent(type="text", text=f"{prefix}{result.content}")]

    return server

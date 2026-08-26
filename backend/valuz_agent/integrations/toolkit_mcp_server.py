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
    POST /_internal/mcp/toolkit/{base|lead}
      (also served at the legacy ``/internal/mcp/toolkit/{base|lead}`` —
      ADR-013 dual-mount, see ``api/app.py::_mount_internal``)
      headers:
        X-Valuz-Internal:    <per-process token>
        X-Valuz-Session-Id:  <kernel session id>

The session id rides a header (not the URL) so each call rebuilds the
handler's ``ExecContext`` server-side — the host's answer to "tool calls
must carry the caller's identity across the wire".
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*

from __future__ import annotations

import itertools
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from collections.abc import AsyncIterator, Awaitable, Callable

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from valuz_agent.integrations._mcp_asgi import (
    build_internal_mcp_asgi,
    get_current_mcp_session_id,
    get_current_mcp_user_id,
)

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool

from src.core.tools import ExecContext, ToolDef

logger = logging.getLogger(__name__)


@dataclass
class HostExecContext(ExecContext):
    """``ExecContext`` enriched with the resolved session owner.

    The kernel's ``ExecContext`` stays owner-agnostic (owner scoping is a host
    concern). The host's built-in MCP toolkit hands its tool handlers this
    richer context — populated once at the request boundary (``_call_tool``) —
    so handlers read ``ctx.user_id`` instead of an ambient context var and stay
    pure. Tests construct it directly with the owner they seed.
    """

    user_id: str = ""
    # Heartbeat for a long, silent tool call. A client that sent a
    # ``progressToken`` gets an MCP ``notifications/progress`` frame. This
    # buys VISIBILITY, not time: measured, no client extends its deadline on a
    # beat — a tool that legitimately runs for minutes without partial output
    # (``generate_ui`` streams a whole document out of a model before it can
    # answer) survives on its server's declared ``tool_timeout_sec``, not on
    # its heartbeat. No token / no client support → this is a no-op the
    # handler can call unconditionally.
    report_progress: Callable[[str], Awaitable[None]] | None = None


TOOLSET_NAMES = ("base", "lead")

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
    sid = get_current_mcp_session_id()
    if not sid:
        raise RuntimeError("toolkit MCP tool called outside of a session-scoped request")
    return sid


def _current_user_id() -> str:
    uid = get_current_mcp_user_id()
    if not uid:
        raise RuntimeError("toolkit MCP tool called outside of a user-scoped request")
    return uid


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
        ctx = HostExecContext(
            session_id=_current_session_id(),
            user_id=_current_user_id(),
            report_progress=_progress_reporter(server),
        )
        result = await tdef.handler(dict(arguments), ctx)
        text = result.content if not result.is_error else f"ERROR: {result.content}"
        return [TextContent(type="text", text=text)]

    return server


def _progress_reporter(server: Server) -> Callable[[str], Awaitable[None]] | None:
    """Bind a progress sender to THIS call's request context, or ``None``.

    Read at call time because ``request_context`` is a ContextVar scoped to
    the in-flight request. Returns ``None`` when the client did not ask for
    progress (no ``progressToken``) so handlers can skip the work entirely.
    Sending is best-effort: a heartbeat that fails must never fail the tool.
    """
    try:
        request_context = server.request_context
    except LookupError:
        return None
    token = getattr(getattr(request_context, "meta", None), "progressToken", None)
    if token is None:
        return None
    session = request_context.session
    counter = itertools.count(1)

    async def _report(message: str) -> None:
        try:
            await session.send_progress_notification(
                progress_token=token,
                progress=float(next(counter)),
                message=message,
                # Streamable HTTP keys its per-request SSE streams by
                # ``str(request_id)``; relating the notification is what puts
                # it on the caller's own stream instead of the standalone GET
                # one, which a tool-calling client need not have open.
                related_request_id=str(request_context.request_id),
            )
        except Exception:  # noqa: BLE001 — a heartbeat is never load-bearing
            logger.debug("progress notification failed", exc_info=True)

    return _report


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
    """Return an ASGI app to mount at ``/_internal/mcp/toolkit/{toolset}``
    (and, dual-mounted for pre-ADR-013 session compatibility,
    ``/internal/mcp/toolkit/{toolset}`` — see ``api/app.py::_mount_internal``).

    Each request: verify ``X-Valuz-Internal``, record
    ``X-Valuz-Session-Id`` into the ContextVar, delegate to the toolset's
    session manager.
    """
    if toolset not in TOOLSET_NAMES:
        raise ValueError(f"unknown toolkit toolset: {toolset}")

    # Resolve the session manager LAZILY, per request — do NOT bind
    # ``_ensure_manager(toolset).handle_request`` at mount time. This app is
    # mounted at app-build, but ``install_toolkit_toolsets`` clears the manager
    # registry later (in the boot lifespan, once the tasks orchestrator exists),
    # and ``toolkit_mcp_session_managers_run`` then creates + ``run()``s a fresh
    # manager. A mount-time binding would capture the pre-install manager (empty
    # toolset, never ``run()``) and every request would 500 with "Task group is
    # not initialized". Resolving here always hits the started manager.
    async def _handle(scope: Any, receive: Any, send: Any) -> None:
        await _ensure_manager(toolset).handle_request(scope, receive, send)

    return build_internal_mcp_asgi(_handle)


def toolkit_mcp_url(*, base_url: str, toolset: str) -> str:
    """Compose the toolkit MCP endpoint a session's MCP client should call.

    The ``/mcp`` inner path keeps the request strictly inside the Starlette
    mount — a bare mount-root URL would draw a 307 redirect, which MCP
    clients don't reliably follow on POST. The stateless session manager
    itself is path-agnostic.

    ADR-013: newly created sessions get the ``/_internal/...`` path;
    ``/internal/...`` stays mounted so session snapshots that persisted the
    pre-rename URL keep working on restore (see ``api/app.py::_mount_internal``,
    removed the next OSS major version).
    """
    if toolset not in TOOLSET_NAMES:
        raise ValueError(f"unknown toolkit toolset: {toolset}")
    return f"{base_url.rstrip('/')}/_internal/mcp/toolkit/{toolset}/mcp"


__all__ = [
    "TOOLSET_NAMES",
    "build_toolkit_mcp_asgi",
    "install_toolkit_toolsets",
    "toolkit_mcp_session_managers_run",
    "toolkit_mcp_url",
]

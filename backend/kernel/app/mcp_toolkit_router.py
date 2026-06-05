"""Per-session MCP-over-HTTP endpoint for the Codex runtime.

Mounts a single ``mcp.server.streamable_http_manager.StreamableHTTPSessionManager``
on the FastAPI app at ``/mcp/toolkit/{session_id}``. The codex Rust subprocess
talks to this endpoint to discover and call harness ``ToolKit`` tools — see
``docs/design/CODEX-CUSTOM-TOOLS-DESIGN.md`` §3.4 for the architecture rationale
and the spike under ``docs/archive/codex-spike/spike_http_mcp_toolkit.py``
for the wire-level verification.

Design notes:

* **Single shared MCP Server, contextvar dispatch.** ``StreamableHTTPSessionManager``
  is bound to one ``Server`` at construction time, so we use a delegating
  ``Server`` whose ``list_tools`` / ``call_tool`` handlers look up the
  request's session via a ``ContextVar``. The ASGI middleware sets the
  contextvar after extracting the session id, then hands the request to
  the shared manager.
* **Registry lives in the kernel.** ``register_session_toolkit`` /
  ``unregister_session_toolkit`` / ``get_session_record`` are owned by
  ``src.core.mcp_bridge`` so ``CodexRuntime`` never has to import from
  ``app.*``. This module is purely the transport / ASGI mount layer.
* **No bearer-token auth.** The toolkit endpoint is an internal harness
  convention. The backend is expected to bind ``127.0.0.1`` (or a private
  network) so the URL is only reachable from a colocated codex subprocess;
  anyone with shell access to that host could already call the handlers
  through other means.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool

from src.core.mcp_bridge import get_session_record

logger = logging.getLogger(__name__)


MCP_ROUTER_MOUNT_PATH = "/mcp/toolkit"


_CURRENT_SESSION: contextvars.ContextVar[str] = contextvars.ContextVar("mcp_router_session_id")


def _build_router_server() -> Server:
    """Construct the single shared delegating ``Server`` instance.

    The handlers consult ``_CURRENT_SESSION`` to find the toolkit for the
    in-flight request — set by ``mcp_toolkit_asgi`` once the session id is
    extracted from the URL.
    """
    server: Server = Server("harness-toolkit")

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _list_tools() -> list[Tool]:
        sid = _CURRENT_SESSION.get()
        rec = get_session_record(sid)
        if rec is None:
            return []
        eligible = [
            t for t in rec.toolkit.list_tools() if t.handler is not None and t.permission != "deny"
        ]
        return [
            Tool(
                name=t.name,
                description=t.description or t.name,
                inputSchema=t.parameters or {"type": "object", "properties": {}},
            )
            for t in eligible
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        sid = _CURRENT_SESSION.get()
        rec = get_session_record(sid)
        if rec is None:
            raise ValueError(f"unknown session: {sid}")
        tdef = rec.toolkit.get(name)
        if tdef is None or tdef.handler is None:
            raise ValueError(f"unknown tool: {name}")
        result = await tdef.handler(dict(arguments), rec.exec_context)
        # is_error is content-side metadata, not a wire-level failure;
        # surface it as a prefix the way mcp_bridge does.
        prefix = "[error] " if result.is_error else ""
        return [TextContent(type="text", text=f"{prefix}{result.content}")]

    return server


_ROUTER_SERVER: Server | None = None
_SESSION_MANAGER: StreamableHTTPSessionManager | None = None


def _ensure_router() -> StreamableHTTPSessionManager:
    """Lazy-init the shared Server + session manager singletons."""
    global _ROUTER_SERVER, _SESSION_MANAGER  # noqa: PLW0603
    if _SESSION_MANAGER is None:
        _ROUTER_SERVER = _build_router_server()
        _SESSION_MANAGER = StreamableHTTPSessionManager(app=_ROUTER_SERVER, stateless=True)
    return _SESSION_MANAGER


@asynccontextmanager
async def mcp_router_lifespan() -> AsyncIterator[None]:
    """Start the shared MCP session manager. Mount inside the FastAPI lifespan."""
    manager = _ensure_router()
    async with manager.run():
        yield


async def _send_http(send: Any, status: int, body: bytes = b"") -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _path_after_mount(scope: dict[str, Any]) -> str:
    """Return the request path with the mount prefix stripped.

    Starlette/FastAPI's ``app.mount(path, asgi)`` does NOT remove the mount
    prefix from ``scope["path"]`` — the inner app sees the full URL path.
    Strip ``/mcp/toolkit`` ourselves so the session-id segment lands first.
    """
    raw_path = str(scope.get("path", ""))
    if raw_path.startswith(MCP_ROUTER_MOUNT_PATH):
        return raw_path[len(MCP_ROUTER_MOUNT_PATH) :] or "/"
    return raw_path


def _extract_session_id(scope: dict[str, Any]) -> str | None:
    inner = _path_after_mount(scope).lstrip("/")
    parts = inner.split("/", 1)
    if not parts or not parts[0]:
        return None
    return parts[0]


async def mcp_toolkit_asgi(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """ASGI entry point for the mounted ``/mcp/toolkit/...`` sub-app.

    Extracts the session id from the path, looks up the registered toolkit,
    sets the ``_CURRENT_SESSION`` contextvar, and delegates to the shared
    ``StreamableHTTPSessionManager``.
    """
    if scope["type"] != "http":
        await _send_http(send, 400, b"only http supported")
        return

    sid = _extract_session_id(scope)
    if not sid:
        await _send_http(send, 404, b"session id required")
        return

    if get_session_record(sid) is None:
        await _send_http(send, 404, b"unknown session")
        return

    # Strip the mount prefix + session-id segment so the inner manager sees
    # a path that begins with the actual MCP request path (``/`` for the
    # stateless POST entry point).
    after_mount = _path_after_mount(scope).lstrip("/")
    rest = after_mount.split("/", 1)[1] if "/" in after_mount else ""
    inner_path = "/" + rest if rest else "/"
    inner_scope = dict(scope)
    inner_scope["path"] = inner_path
    if isinstance(scope.get("raw_path"), bytes):
        # Best-effort raw_path adjustment — only used by HTTP routers; the
        # MCP transport itself reads ``path``.
        inner_scope["raw_path"] = inner_path.encode("latin-1")

    manager = _ensure_router()
    token_ctx = _CURRENT_SESSION.set(sid)
    try:
        await manager.handle_request(inner_scope, receive, send)
    finally:
        _CURRENT_SESSION.reset(token_ctx)


def mount_mcp_router(app: FastAPI) -> None:
    """Wire the ASGI sub-app and its lifespan onto an existing FastAPI app.

    Must be called *before* the FastAPI lifespan starts (typically at module
    import time, before ``uvicorn`` invokes the lifespan context).
    """
    # Starlette types ``mount`` against the strict ASGI3 protocol with
    # ``MutableMapping`` scope and the ASGI ``Send`` / ``Receive`` callables;
    # our handler uses the looser ``dict[str, Any]`` shape. The runtime
    # contract is identical.
    app.mount(MCP_ROUTER_MOUNT_PATH, mcp_toolkit_asgi)  # type: ignore[arg-type]


def reset_for_tests() -> None:
    """Reset both the toolkit registry and the shared manager — pytest hook.

    Tests that drive the router under their own lifespan need a clean
    slate between cases; production code never calls this.
    """
    from src.core.mcp_bridge import reset_registry_for_tests

    global _ROUTER_SERVER, _SESSION_MANAGER  # noqa: PLW0603
    reset_registry_for_tests()
    _ROUTER_SERVER = None
    _SESSION_MANAGER = None

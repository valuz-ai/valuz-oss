"""``/mcp/proxy/<session>/<server>/`` — the kernel's MCP proxy for CLI runtimes.

Codex and DSH reach this endpoint (instead of the MCP server itself) while a
``tool.call`` handler is registered; see :mod:`src.runtimes.mcp_proxy`. One
shared low-level MCP ``Server`` + stateless streamable-HTTP manager serves
every proxied server; the ASGI entry resolves ``(session, server)`` from the
path, checks the session's bearer token, and the handlers forward to that
server's kernel-owned upstream connection. Tool calls dispatch ``tool.call``;
resources and prompts pass through unchanged.

Mounted next to the kernel toolkit (``mount_mcp_router`` /
``mcp_router_lifespan`` in ``app.mcp_toolkit_router`` wire both).
"""

from __future__ import annotations

import base64
import contextvars
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import unquote

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import (
    BlobResourceContents,
    GetPromptResult,
    Prompt,
    Resource,
    ResourceTemplate,
    TextResourceContents,
    Tool,
)
from src.runtimes.mcp_proxy.dispatch import dispatch_mcp_call
from src.runtimes.mcp_proxy.registry import (
    MCP_PROXY_MOUNT_PATH,
    ProxiedSession,
    get_session_proxy,
)
from src.runtimes.mcp_proxy.upstream import McpUpstream

logger = logging.getLogger(__name__)

_CURRENT: contextvars.ContextVar[tuple[ProxiedSession, McpUpstream]] = contextvars.ContextVar(
    "mcp_proxy_target"
)


def _target() -> tuple[ProxiedSession, McpUpstream]:
    return _CURRENT.get()


def _build_server() -> Server:
    server: Server = Server("valuz-mcp-proxy")

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _list_tools() -> list[Tool]:
        _session, upstream = _target()
        return list(await upstream.list_tools())

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
        proxied, upstream = _target()

        async def call(args: dict[str, Any]) -> Any:
            return await upstream.call_tool(name, args)

        return await dispatch_mcp_call(
            proxied.hooks,
            upstream.name,
            name,
            arguments or {},
            call,
            # Codex and DSH only ever see the content; the citation
            # projection needs the source metadata alongside it.
            carry_source_metadata=True,
        )

    @server.list_resources()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _list_resources() -> list[Resource]:
        _session, upstream = _target()
        return list(await upstream.list_resources())

    @server.list_resource_templates()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _list_resource_templates() -> list[ResourceTemplate]:
        _session, upstream = _target()
        return list(await upstream.list_resource_templates())

    @server.read_resource()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _read_resource(uri: Any) -> Any:
        _session, upstream = _target()
        result = await upstream.read_resource(uri)
        return [_as_read_contents(item) for item in result.contents]

    @server.list_prompts()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _list_prompts() -> list[Prompt]:
        _session, upstream = _target()
        return list(await upstream.list_prompts())

    @server.get_prompt()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        _session, upstream = _target()
        result: GetPromptResult = await upstream.get_prompt(name, arguments)
        return result

    return server


def _as_read_contents(item: Any) -> ReadResourceContents:
    """Upstream resource contents → the low-level server's return shape."""
    meta = getattr(item, "meta", None)
    if isinstance(item, BlobResourceContents):
        return ReadResourceContents(
            content=base64.b64decode(item.blob), mime_type=item.mimeType, meta=meta
        )
    text = item.text if isinstance(item, TextResourceContents) else str(item)
    return ReadResourceContents(content=text, mime_type=getattr(item, "mimeType", None), meta=meta)


_SERVER: Server | None = None
_MANAGER: StreamableHTTPSessionManager | None = None


def _ensure_manager() -> StreamableHTTPSessionManager:
    global _SERVER, _MANAGER  # noqa: PLW0603
    if _MANAGER is None:
        _SERVER = _build_server()
        _MANAGER = StreamableHTTPSessionManager(app=_SERVER, stateless=True)
    return _MANAGER


@asynccontextmanager
async def mcp_proxy_lifespan() -> AsyncIterator[None]:
    async with _ensure_manager().run():
        yield


async def _respond(send: Any, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _split(scope: dict[str, Any]) -> tuple[str, str, str] | None:
    path = str(scope.get("path", ""))
    if path.startswith(MCP_PROXY_MOUNT_PATH):
        path = path[len(MCP_PROXY_MOUNT_PATH) :]
    parts = path.lstrip("/").split("/", 2)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    rest = parts[2] if len(parts) == 3 else ""
    return parts[0], unquote(parts[1]), "/" + rest if rest else "/"


def _header(scope: dict[str, Any], name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


async def mcp_proxy_asgi(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] != "http":
        await _respond(send, 400, b"only http supported")
        return
    split = _split(scope)
    if split is None:
        await _respond(send, 404, b"session and server required")
        return
    session_id, server_name, inner_path = split
    proxied = get_session_proxy(session_id)
    if proxied is None:
        await _respond(send, 404, b"unknown session")
        return
    if not proxied.authorized(_header(scope, b"authorization")):
        await _respond(send, 401, b"unauthorized")
        return
    upstream = proxied.upstreams.get(server_name)
    if upstream is None:
        await _respond(send, 404, b"unknown server")
        return
    inner_scope = dict(scope)
    inner_scope["path"] = inner_path
    inner_scope["raw_path"] = inner_path.encode("latin-1")
    token = _CURRENT.set((proxied, upstream))
    try:
        await _ensure_manager().handle_request(inner_scope, receive, send)
    finally:
        _CURRENT.reset(token)


def reset_for_tests() -> None:
    global _SERVER, _MANAGER  # noqa: PLW0603
    from src.runtimes.mcp_proxy.registry import reset_for_tests as reset_registry

    reset_registry()
    _SERVER = None
    _MANAGER = None


__all__ = ["MCP_PROXY_MOUNT_PATH", "mcp_proxy_asgi", "mcp_proxy_lifespan", "reset_for_tests"]

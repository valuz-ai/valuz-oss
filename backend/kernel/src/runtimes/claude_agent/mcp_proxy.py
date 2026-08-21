"""Transparent MCP bridge for Claude's content-only tool-result transport."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, cast

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.shared._httpx_utils import create_mcp_http_client
from src.core.mcp_source_metadata import wrap_mcp_result_metadata_in_content_for_transport
from src.core.types import McpServerConfig, McpStdioServerConfig
from src.runtimes.mcp_env import resolve_stdio_env

_DEFAULT_HTTP_TIMEOUT_SECONDS = 300.0
_MAX_TOOL_LIST_PAGES = 1_000


@dataclass
class _ProxyRequest:
    operation: Literal["list_tools", "call_tool", "close"]
    future: asyncio.Future[Any]
    tool_name: str | None = None
    arguments: dict[str, Any] | None = None


class ClaudeMcpSourceProxy:
    """Expose an upstream MCP server as an in-process Claude SDK server.

    The Claude CLI discards result-level ``_meta`` and ``structuredContent``
    before PostToolUse.  This bridge owns the upstream connection and appends a
    Valuz-private content sidecar only when the result carries the standard
    source descriptor.  No server name or tool name participates in routing.
    """

    def __init__(
        self,
        config: McpServerConfig,
        *,
        session_context_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.config = config
        self.server = Server(config.name, version="1.0.0")
        self._session_context_factory = session_context_factory or (
            lambda: _open_upstream_session(config)
        )
        self._start_lock = asyncio.Lock()
        self._queue: asyncio.Queue[_ProxyRequest] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._closed = False

        @self.server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
        async def list_tools() -> list[Any]:
            return await self.list_tools()

        @self.server.call_tool()  # type: ignore[untyped-decorator]
        async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
            return await self.call_tool(name, arguments)

    def sdk_config(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "type": "sdk",
            "name": self.config.name,
            "instance": self.server,
        }
        timeout_sec = getattr(self.config, "tool_timeout_sec", None)
        if isinstance(timeout_sec, (int, float)) and timeout_sec > 0:
            entry["timeout"] = int(float(timeout_sec) * 1_000)
        return entry

    async def list_tools(self) -> list[Any]:
        result = await self._submit(_ProxyRequest(operation="list_tools", future=_future()))
        return cast(list[Any], result)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._submit(
            _ProxyRequest(
                operation="call_tool",
                future=_future(),
                tool_name=name,
                arguments=arguments,
            )
        )

    async def close(self) -> None:
        async with self._start_lock:
            if self._closed:
                return
            self._closed = True
            worker = self._worker_task
            queue = self._queue
            if worker is None or queue is None or worker.done():
                return
            request = _ProxyRequest(operation="close", future=_future())
            queue.put_nowait(request)
        try:
            await request.future
        finally:
            await asyncio.gather(worker, return_exceptions=True)

    async def _submit(self, request: _ProxyRequest) -> Any:
        async with self._start_lock:
            if self._closed:
                raise RuntimeError(f"MCP proxy '{self.config.name}' is closed")
            if self._worker_task is None or self._worker_task.done():
                self._queue = asyncio.Queue()
                self._queue.put_nowait(request)
                self._worker_task = asyncio.create_task(self._serve(self._queue))
            else:
                assert self._queue is not None
                self._queue.put_nowait(request)
        return await request.future

    async def _serve(self, queue: asyncio.Queue[_ProxyRequest]) -> None:
        close_request: _ProxyRequest | None = None
        try:
            async with self._session_context_factory() as session:
                async with asyncio.TaskGroup() as tasks:
                    while True:
                        request = await queue.get()
                        if request.operation == "close":
                            close_request = request
                            break
                        tasks.create_task(self._execute(session, request))
            if close_request is not None and not close_request.future.done():
                close_request.future.set_result(None)
        except BaseException as exc:
            if close_request is not None and not close_request.future.done():
                close_request.future.set_exception(exc)
            while not queue.empty():
                request = queue.get_nowait()
                if not request.future.done():
                    request.future.set_exception(exc)

    async def _execute(self, session: Any, request: _ProxyRequest) -> None:
        try:
            if request.operation == "list_tools":
                result = await _list_all_tools(session)
            else:
                assert request.tool_name is not None
                timeout_sec = getattr(self.config, "tool_timeout_sec", None)
                read_timeout = (
                    timedelta(seconds=float(timeout_sec))
                    if isinstance(timeout_sec, (int, float)) and timeout_sec > 0
                    else None
                )
                kwargs = {"read_timeout_seconds": read_timeout} if read_timeout else {}
                result = await session.call_tool(
                    request.tool_name,
                    request.arguments or {},
                    **kwargs,
                )
                result = wrap_mcp_result_metadata_in_content_for_transport(
                    result,
                    server_name=self.config.name,
                )
        except BaseException as exc:
            if not request.future.done():
                request.future.set_exception(exc)
        else:
            if not request.future.done():
                request.future.set_result(result)


def _future() -> asyncio.Future[Any]:
    return asyncio.get_running_loop().create_future()


async def _list_all_tools(session: Any) -> list[Any]:
    tools: list[Any] = []
    cursor: str | None = None
    for _ in range(_MAX_TOOL_LIST_PAGES):
        result = await session.list_tools(cursor=cursor)
        tools.extend(result.tools or [])
        cursor = result.nextCursor
        if not cursor:
            return tools
    raise RuntimeError("MCP tools/list exceeded 1000 pages")


@asynccontextmanager
async def _open_upstream_session(config: McpServerConfig) -> AsyncIterator[ClientSession]:
    timeout_sec = getattr(config, "tool_timeout_sec", None)
    read_timeout = (
        timedelta(seconds=float(timeout_sec))
        if isinstance(timeout_sec, (int, float)) and timeout_sec > 0
        else None
    )
    async with AsyncExitStack() as stack:
        if isinstance(config, McpStdioServerConfig):
            params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env=resolve_stdio_env(config),
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        elif config.transport == "sse":
            transport_timeout = float(timeout_sec or _DEFAULT_HTTP_TIMEOUT_SECONDS)
            read, write = await stack.enter_async_context(
                sse_client(
                    config.url,
                    headers=dict(config.headers) or None,
                    timeout=min(30.0, transport_timeout),
                    sse_read_timeout=transport_timeout,
                )
            )
        else:
            transport_timeout = float(timeout_sec or _DEFAULT_HTTP_TIMEOUT_SECONDS)
            http_client = await stack.enter_async_context(
                create_mcp_http_client(
                    headers=dict(config.headers) or None,
                    timeout=httpx.Timeout(
                        transport_timeout,
                        connect=min(30.0, transport_timeout),
                    ),
                )
            )
            read, write, _ = await stack.enter_async_context(
                streamable_http_client(config.url, http_client=http_client)
            )
        session = await stack.enter_async_context(
            ClientSession(read, write, read_timeout_seconds=read_timeout)
        )
        await session.initialize()
        yield session


__all__ = ["ClaudeMcpSourceProxy"]

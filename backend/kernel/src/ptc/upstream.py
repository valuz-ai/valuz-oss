"""Per-execution upstream MCP client pool for PTC forwarded calls.

One ``execute_code`` run opens at most one upstream session per server and
reuses it across the program's calls (spawn/handshake once, like a warmed
client). The ``mcp`` SDK's anyio transports must be entered and exited on
the same task, while router requests arrive on arbitrary tasks — so each
server gets a dedicated worker task owning its session, fed through a queue
(the same shape ``ClaudeMcpSourceProxy`` uses, minus the sidecar wrapping:
PTC needs the raw ``CallToolResult`` to build its own trace).

Scope: HTTP / SSE servers only. PTC v1 targets HTTP data connectors
(``valuz-search`` / ``valuz-data``); stdio servers are rejected upstream of
this module by the executor's eligibility filter.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from src.core.types import McpServerConfig

_DEFAULT_HTTP_TIMEOUT_SECONDS = 300.0


@dataclass
class _CallRequest:
    tool: str
    arguments: dict[str, Any]
    future: asyncio.Future[Any]


@asynccontextmanager
async def _open_upstream_session(config: McpServerConfig) -> AsyncIterator[ClientSession]:
    """HTTP/SSE upstream session, honoring the server's declared timeout."""
    timeout_sec = getattr(config, "tool_timeout_sec", None)
    read_timeout = (
        timedelta(seconds=float(timeout_sec))
        if isinstance(timeout_sec, (int, float)) and timeout_sec > 0
        else None
    )
    transport_timeout = float(timeout_sec or _DEFAULT_HTTP_TIMEOUT_SECONDS)
    async with AsyncExitStack() as stack:
        if getattr(config, "transport", "http") == "sse":
            read, write = await stack.enter_async_context(
                sse_client(
                    config.url,
                    headers=dict(config.headers) or None,
                    timeout=min(30.0, transport_timeout),
                    sse_read_timeout=transport_timeout,
                )
            )
        else:
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


class _ServerWorker:
    """Owns one upstream session; serves call requests from a queue."""

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._queue: asyncio.Queue[_CallRequest | None] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        request = _CallRequest(
            tool=tool,
            arguments=arguments,
            future=asyncio.get_running_loop().create_future(),
        )
        async with self._lock:
            if self._closed:
                raise RuntimeError(f"upstream pool for {self._config.name!r} is closed")
            if self._task is None or self._task.done():
                self._queue = asyncio.Queue()
                self._task = asyncio.create_task(self._serve(self._queue))
            self._queue.put_nowait(request)
        return await request.future

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            task = self._task
            if task is None or task.done():
                return
            self._queue.put_nowait(None)
        await asyncio.gather(task, return_exceptions=True)

    async def _serve(self, queue: asyncio.Queue[_CallRequest | None]) -> None:
        try:
            async with _open_upstream_session(self._config) as session:
                while True:
                    request = await queue.get()
                    if request is None:
                        return
                    await self._execute(session, request)
        except BaseException as exc:
            # Fail every queued waiter; the next call opens a fresh session.
            while not queue.empty():
                pending = queue.get_nowait()
                if pending is not None and not pending.future.done():
                    pending.future.set_exception(_clone_exc(exc))
            if not isinstance(exc, Exception):
                raise

    async def _execute(self, session: ClientSession, request: _CallRequest) -> None:
        try:
            timeout_sec = getattr(self._config, "tool_timeout_sec", None)
            kwargs: dict[str, Any] = {}
            if isinstance(timeout_sec, (int, float)) and timeout_sec > 0:
                kwargs["read_timeout_seconds"] = timedelta(seconds=float(timeout_sec))
            result = await session.call_tool(request.tool, request.arguments, **kwargs)
        except BaseException as exc:
            if not request.future.done():
                request.future.set_exception(_clone_exc(exc))
            if not isinstance(exc, Exception):
                raise
        else:
            if not request.future.done():
                request.future.set_result(result)


def _clone_exc(exc: BaseException) -> Exception:
    if isinstance(exc, Exception):
        return exc
    return RuntimeError(f"upstream session aborted: {exc!r}")


class UpstreamPool:
    """Per-execution pool of server workers, closed when the run settles."""

    def __init__(self, servers: dict[str, McpServerConfig]) -> None:
        self._configs = dict(servers)
        self._workers: dict[str, _ServerWorker] = {}
        self._lock = asyncio.Lock()

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        config = self._configs.get(server)
        if config is None:
            raise KeyError(server)
        async with self._lock:
            worker = self._workers.get(server)
            if worker is None:
                worker = _ServerWorker(config)
                self._workers[server] = worker
        return await worker.call(tool, arguments)

    async def close(self) -> None:
        async with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            await worker.close()

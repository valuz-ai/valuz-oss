"""One kernel-owned connection to an upstream MCP server.

The ``mcp`` SDK's anyio transports must be entered and exited on the same
task while requests arrive on arbitrary tasks, so a dedicated worker task
owns the session and serves requests from a queue — the same shape as
:class:`src.runtimes.claude_agent.mcp_proxy.ClaudeMcpSourceProxy`, which
Claude uses in-process. This one serves the kernel's HTTP proxy for Codex and
DSH and forwards resources and prompts as well as tools.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from src.core.types import McpServerConfig
from src.runtimes.claude_agent.mcp_proxy import _list_all_tools, _open_upstream_session


@dataclass
class _Request:
    operation: str
    future: asyncio.Future[Any]
    args: tuple[Any, ...] = ()


class McpUpstream:
    def __init__(
        self,
        config: McpServerConfig,
        *,
        session_context_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ) -> None:
        self.config = config
        self._session_context_factory = session_context_factory or (
            lambda: _open_upstream_session(config)
        )
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue[_Request] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def name(self) -> str:
        return self.config.name

    async def list_tools(self) -> list[Any]:
        return await self._submit("list_tools")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._submit("call_tool", name, arguments)

    async def list_resources(self) -> list[Any]:
        return await self._submit("list_resources")

    async def list_resource_templates(self) -> list[Any]:
        return await self._submit("list_resource_templates")

    async def read_resource(self, uri: Any) -> Any:
        return await self._submit("read_resource", uri)

    async def list_prompts(self) -> list[Any]:
        return await self._submit("list_prompts")

    async def get_prompt(self, name: str, arguments: dict[str, str] | None) -> Any:
        return await self._submit("get_prompt", name, arguments)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            worker, queue = self._worker, self._queue
            if worker is None or queue is None or worker.done():
                return
            request = _Request(operation="close", future=_future())
            queue.put_nowait(request)
        try:
            await request.future
        finally:
            await asyncio.gather(worker, return_exceptions=True)

    async def _submit(self, operation: str, *args: Any) -> Any:
        request = _Request(operation=operation, future=_future(), args=args)
        async with self._lock:
            if self._closed:
                raise RuntimeError(f"MCP upstream '{self.config.name}' is closed")
            if self._worker is None or self._worker.done():
                self._queue = asyncio.Queue()
                self._queue.put_nowait(request)
                self._worker = asyncio.get_running_loop().create_task(
                    self._serve(self._queue), context=contextvars.Context()
                )
            else:
                assert self._queue is not None
                self._queue.put_nowait(request)
        return await request.future

    async def _serve(self, queue: asyncio.Queue[_Request]) -> None:
        close_request: _Request | None = None
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
                pending = queue.get_nowait()
                if not pending.future.done():
                    pending.future.set_exception(
                        exc if isinstance(exc, Exception) else RuntimeError(repr(exc))
                    )

    async def _execute(self, session: Any, request: _Request) -> None:
        try:
            result = await self._run(session, request)
        except BaseException as exc:
            if not request.future.done():
                request.future.set_exception(exc)
        else:
            if not request.future.done():
                request.future.set_result(result)

    async def _run(self, session: Any, request: _Request) -> Any:
        operation, args = request.operation, request.args
        if operation == "list_tools":
            return await _list_all_tools(session)
        if operation == "call_tool":
            name, arguments = args
            timeout_sec = getattr(self.config, "tool_timeout_sec", None)
            kwargs: dict[str, Any] = {}
            if isinstance(timeout_sec, (int, float)) and timeout_sec > 0:
                kwargs["read_timeout_seconds"] = timedelta(seconds=float(timeout_sec))
            return await session.call_tool(name, arguments or {}, **kwargs)
        if operation == "list_resources":
            if not _supports(session, "resources"):
                return []
            return list((await session.list_resources()).resources or [])
        if operation == "list_resource_templates":
            if not _supports(session, "resources"):
                return []
            return list((await session.list_resource_templates()).resourceTemplates or [])
        if operation == "read_resource":
            return await session.read_resource(args[0])
        if operation == "list_prompts":
            if not _supports(session, "prompts"):
                return []
            return list((await session.list_prompts()).prompts or [])
        if operation == "get_prompt":
            name, arguments = args
            return await session.get_prompt(name, arguments)
        raise ValueError(f"unknown MCP proxy operation {operation!r}")


def _supports(session: Any, capability: str) -> bool:
    """Whether the upstream advertised *capability* (unknown counts as yes)."""
    getter = getattr(session, "get_server_capabilities", None)
    capabilities = getter() if callable(getter) else None
    if capabilities is None:
        return True
    return getattr(capabilities, capability, None) is not None


def _future() -> asyncio.Future[Any]:
    return asyncio.get_running_loop().create_future()


__all__ = ["McpUpstream"]

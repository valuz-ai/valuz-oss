"""The hook bus for a runtime in another process (DSH today, mods host later).

The chain runs here, in the kernel; the runtime's own behaviour (the
``core``) runs over there. One dispatch is therefore a short conversation:

1. the remote side **starts** a dispatch with the event payload;
2. the kernel runs the chain; when it reaches ``core`` it answers
   ``{"op": "core", "data": …}`` — "run your behaviour on this payload";
3. the remote side runs it and **posts the core result**; the chain
   continues (a handler may call ``next`` again → another ``core`` step);
4. the kernel answers ``{"op": "done", "result": …}``.

Each HTTP request returns the next step, so nothing is held open while the
remote side runs its behaviour (a tool may run for minutes). A remote
session is identified by a random per-spawn token, which is the credential.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.core.hooks.events import (
    AGENT_SPAWN,
    SESSION_COMPACT,
    TOOL_CALL,
    TOOL_CHECK,
    CompactDecision,
    HookEvent,
    ToolDecision,
    ToolOutcome,
    ToolRef,
)
from src.core.hooks.freeze import thaw
from src.core.hooks.registry import SessionHooks
from src.core.hooks.tool_identity import mcp_tool_ref, native_tool_ref, toolkit_tool_ref

logger = logging.getLogger(__name__)

# Events a remote runtime may start, and how their results travel.
REMOTE_EVENTS = frozenset({TOOL_CALL, TOOL_CHECK, SESSION_COMPACT, AGENT_SPAWN})


class RemoteHookError(Exception):
    pass


def _result_from_wire(event: str, raw: Any) -> Any:
    data = raw if isinstance(raw, Mapping) else {}
    if event == TOOL_CALL:
        return ToolOutcome(
            content=data.get("content", ""),
            is_error=bool(data.get("is_error")),
            executed=bool(data.get("executed", True)),
        )
    if event == TOOL_CHECK:
        behavior = str(data.get("behavior") or "deny")
        if behavior not in ("allow", "deny", "ask"):
            behavior = "deny"
        return ToolDecision(behavior=behavior, reason=data.get("reason"))  # type: ignore[arg-type]
    if event == SESSION_COMPACT:
        return CompactDecision(proceed=bool(data.get("proceed", True)), reason=data.get("reason"))
    return None


def _result_to_wire(result: Any) -> Any:
    if isinstance(result, ToolOutcome):
        return {
            "content": thaw(result.content),
            "is_error": result.is_error,
            "executed": result.executed,
        }
    if isinstance(result, ToolDecision):
        return {"behavior": result.behavior, "reason": result.reason}
    if isinstance(result, CompactDecision):
        return {"proceed": result.proceed, "reason": result.reason}
    return None


@dataclass
class _Dispatch:
    event: str
    steps: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    core_results: asyncio.Queue[Any] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[Any] | None = None


class RemoteHookSession:
    """One remote runtime session's side of the conversation."""

    def __init__(
        self,
        hooks: SessionHooks,
        runtime_provider: str,
        *,
        toolkit_servers: tuple[str, ...] = (),
    ) -> None:
        self.hooks = hooks
        self.runtime_provider = runtime_provider
        # MCP server names under which the runtime sees the kernel toolkit
        # (dsh: ``harness_toolkit``): their tools are toolkit tools, as on
        # every other runtime.
        self.toolkit_servers = toolkit_servers
        self._dispatches: dict[str, _Dispatch] = {}

    def wants(self) -> list[str]:
        """The events worth a round trip right now."""
        return sorted(event for event in REMOTE_EVENTS if self.hooks.wants(event))

    def tool_ref(self, name: str, arguments: Mapping[str, Any]) -> ToolRef:
        """``mcp__<server>__<tool>`` is an MCP (or kernel toolkit) tool; any
        other name is one of the runtime's own."""
        if name.startswith("mcp__"):
            server, _, tool = name[len("mcp__") :].partition("__")
            if server and tool:
                if server in self.toolkit_servers:
                    return toolkit_tool_ref(tool)
                return mcp_tool_ref(server, tool)
        return native_tool_ref(self.runtime_provider, name, arguments)

    async def start(self, event: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if event not in REMOTE_EVENTS:
            raise RemoteHookError(f"event {event!r} cannot be dispatched remotely")
        data = dict(payload)
        if event in (TOOL_CALL, TOOL_CHECK):
            name = str(payload.get("name") or "")
            arguments = payload.get("input") if isinstance(payload.get("input"), Mapping) else {}
            data = {
                "tool": self.tool_ref(name, arguments).to_dict(),
                "input": dict(arguments),
                "tool_use_id": payload.get("tool_use_id"),
            }
        dispatch_id = secrets.token_hex(8)
        dispatch = _Dispatch(event=event)
        self._dispatches[dispatch_id] = dispatch

        async def core(hook_event: HookEvent) -> Any:
            await dispatch.steps.put({"op": "core", "data": thaw(hook_event.data)})
            raw = await dispatch.core_results.get()
            if isinstance(raw, BaseException):
                raise raw
            return _result_from_wire(event, raw)

        async def run() -> None:
            try:
                result = await self.hooks.dispatch(event, data, core)
                await dispatch.steps.put({"op": "done", "result": _result_to_wire(result)})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — reported to the remote side
                logger.warning("remote %s dispatch failed: %s", event, exc)
                await dispatch.steps.put({"op": "error", "message": str(exc)})

        dispatch.task = asyncio.create_task(run())
        return await self._next_step(dispatch_id, dispatch)

    async def post_core_result(self, dispatch_id: str, result: Any) -> dict[str, Any]:
        dispatch = self._dispatches.get(dispatch_id)
        if dispatch is None:
            raise RemoteHookError("unknown or finished dispatch")
        await dispatch.core_results.put(result)
        return await self._next_step(dispatch_id, dispatch)

    async def post_core_error(self, dispatch_id: str, message: str) -> dict[str, Any]:
        return await self.post_core_result(dispatch_id, RemoteHookError(message))

    async def _next_step(self, dispatch_id: str, dispatch: _Dispatch) -> dict[str, Any]:
        step = await dispatch.steps.get()
        if step["op"] != "core":
            self._dispatches.pop(dispatch_id, None)
        return {"id": dispatch_id, **step}

    async def close(self) -> None:
        dispatches = list(self._dispatches.values())
        self._dispatches.clear()
        for dispatch in dispatches:
            if dispatch.task is not None and not dispatch.task.done():
                dispatch.task.cancel()
        tasks = [d.task for d in dispatches if d.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


_SESSIONS: dict[str, RemoteHookSession] = {}
_LOCK = threading.Lock()


def register_remote_hooks(session: RemoteHookSession) -> str:
    token = secrets.token_hex(16)
    with _LOCK:
        _SESSIONS[token] = session
    return token


def get_remote_hooks(token: str) -> RemoteHookSession | None:
    with _LOCK:
        return _SESSIONS.get(token)


async def unregister_remote_hooks(token: str) -> None:
    with _LOCK:
        session = _SESSIONS.pop(token, None)
    if session is not None:
        await session.close()


__all__ = [
    "REMOTE_EVENTS",
    "RemoteHookError",
    "RemoteHookSession",
    "get_remote_hooks",
    "register_remote_hooks",
    "unregister_remote_hooks",
]

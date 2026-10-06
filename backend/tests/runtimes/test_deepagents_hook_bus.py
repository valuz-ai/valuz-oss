"""DeepAgents runtime on the Valuz hook bus.

Built-in tools: one middleware whose ``awrap_tool_call`` is the ``tool.call``
chain (rewrite / retry / take over all work). Approvals: every HITL action is
its own ``tool.check`` chain with Valuz's approval card as core.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from src.core.agent_config import AgentConfig
from src.core.hooks import (
    AGENT_SPAWN,
    TOOL_CALL,
    TOOL_CHECK,
    SessionRef,
    ToolDecision,
    ToolOutcome,
    hook_registry,
)
from src.core.tools import ExecContext, ToolDef, ToolKit, ToolResult
from src.runtimes.deepagents.runtime import DeepAgentsRuntime

OWNER = "test.deepagents-hook-bus"


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    hook_registry.unregister_owner(OWNER)


class _Sink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


def _runtime(tmp_path, permission_mode: str = "full_access") -> DeepAgentsRuntime:  # noqa: ANN001
    async def echo(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        return ToolResult(content=f"toolkit {args}")

    toolkit = ToolKit()
    toolkit.register(ToolDef(name="kernel_tool", description="", handler=echo))
    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        "model",
        _Sink(),
        workspace_root=str(tmp_path),
        toolkit=toolkit,
    )
    runtime._mcp_tool_names = {"search"}
    runtime._hook_session_ref = SessionRef(
        session_id="s1", runtime_provider="deepagents", permission_mode=permission_mode
    )
    return runtime


def _request(name: str, args: dict[str, Any], call_id: str = "c1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": call_id, "type": "tool_call"},
        tool=None,
        state={},
        runtime=None,  # type: ignore[arg-type]
    )


def _executor(log: list[dict[str, Any]], reply: str = "ok"):  # noqa: ANN202
    async def handler(request: ToolCallRequest) -> ToolMessage:
        log.append(dict(request.tool_call["args"]))
        return ToolMessage(
            content=f"{reply}:{request.tool_call['args'].get('command', '')}",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    return handler


def test_no_handlers_means_no_middleware(tmp_path) -> None:  # noqa: ANN001
    assert _runtime(tmp_path)._hooks_middlewares() == []


async def test_rewrite_input_and_result(tmp_path) -> None:  # noqa: ANN001
    async def handler(ctx, event, next_):  # noqa: ANN001
        result = await next_(event.with_data(input={"command": "ls -la"}))
        return ToolOutcome(content=f"{result.content} (checked)")

    hook_registry.register(TOOL_CALL, handler, owner=OWNER, matcher={"tool.kind": "shell"})
    runtime = _runtime(tmp_path)
    [middleware] = runtime._hooks_middlewares()
    calls: list[dict[str, Any]] = []
    result = await middleware.awrap_tool_call(
        _request("execute", {"command": "ls"}), _executor(calls)
    )
    assert calls == [{"command": "ls -la"}]
    assert isinstance(result, ToolMessage)
    assert result.content == "ok:ls -la (checked)"
    assert result.tool_call_id == "c1" and result.status == "success"


async def test_retry_runs_the_tool_again(tmp_path) -> None:  # noqa: ANN001
    async def retry(ctx, event, next_):  # noqa: ANN001
        await next_()
        return await next_()

    hook_registry.register(TOOL_CALL, retry, owner=OWNER)
    [middleware] = _runtime(tmp_path)._hooks_middlewares()
    calls: list[dict[str, Any]] = []
    await middleware.awrap_tool_call(_request("execute", {"command": "x"}), _executor(calls))
    assert len(calls) == 2


async def test_takeover_and_passthrough(tmp_path) -> None:  # noqa: ANN001
    async def answer(ctx, event, next_):  # noqa: ANN001
        if event.get("tool.kind") == "file_write":
            return ToolOutcome(content="writes are disabled", is_error=True, executed=False)
        return await next_()

    hook_registry.register(TOOL_CALL, answer, owner=OWNER)
    [middleware] = _runtime(tmp_path)._hooks_middlewares()
    calls: list[dict[str, Any]] = []
    denied = await middleware.awrap_tool_call(
        _request("write_file", {"file_path": "/a", "content": "x"}), _executor(calls)
    )
    assert calls == []
    assert denied.content == "writes are disabled" and denied.status == "error"
    original = _executor(calls)
    kept = await middleware.awrap_tool_call(_request("read_file", {"file_path": "/a"}), original)
    assert kept.content == "ok:" and len(calls) == 1


async def test_toolkit_and_mcp_tools_pass_through(tmp_path) -> None:  # noqa: ANN001
    seen: list[str] = []

    async def watch(ctx, event, next_):  # noqa: ANN001
        seen.append(event.get("tool.name"))
        return await next_()

    hook_registry.register(TOOL_CALL, watch, owner=OWNER)
    [middleware] = _runtime(tmp_path)._hooks_middlewares()
    calls: list[dict[str, Any]] = []
    for name in ("kernel_tool", "search"):
        await middleware.awrap_tool_call(_request(name, {}), _executor(calls))
    assert seen == [] and len(calls) == 2


async def test_task_tool_announces_agent_spawn(tmp_path) -> None:  # noqa: ANN001
    spawned: list[str] = []

    async def watch(ctx, event, next_):  # noqa: ANN001
        spawned.append(event.get("agent_type"))
        return await next_()

    hook_registry.register(AGENT_SPAWN, watch, owner=OWNER)
    [middleware] = _runtime(tmp_path)._hooks_middlewares()
    calls: list[dict[str, Any]] = []
    await middleware.awrap_tool_call(
        _request("task", {"subagent_type": "researcher", "description": "dig"}),
        _executor(calls),
    )
    assert spawned == ["researcher"] and len(calls) == 1


async def test_kernel_toolkit_dispatches_through_call_tooldef(tmp_path) -> None:  # noqa: ANN001
    async def wrap(ctx, event, next_):  # noqa: ANN001
        assert event.get("tool.source") == "toolkit"
        result = await next_(event.with_data(input={"n": 2}))
        return ToolOutcome(content=f"[{result.content}]")

    hook_registry.register(TOOL_CALL, wrap, owner=OWNER, matcher={"tool": "kernel_tool"})
    runtime = _runtime(tmp_path)
    tool = runtime._to_structured_tool(runtime.toolkit.get("kernel_tool"))
    assert await tool.ainvoke({"n": 1}) == "[toolkit {'n': 2}]"


async def test_tool_check_per_action(tmp_path) -> None:  # noqa: ANN001
    async def deny_writes(ctx, event, next_):  # noqa: ANN001
        if event.get("tool.kind") == "file_write":
            return ToolDecision(behavior="deny", reason="read-only today")
        return await next_()

    hook_registry.register(TOOL_CHECK, deny_writes, owner=OWNER)
    runtime = _runtime(tmp_path, permission_mode="default")
    sink = runtime.event_sink

    async def approve_cards() -> None:
        while True:
            await asyncio.sleep(0.01)
            cards = [e for e in sink.events if e.type == "requires_action"]
            if cards:
                pending_id = cards[0].data["pending_id"]
                runtime._pending_futures[pending_id].set_result(("approve", None, None))
                return

    approver = asyncio.create_task(approve_cards())
    decisions = await runtime._await_host_decisions(
        [
            {"name": "write_file", "args": {"file_path": "/a", "content": "x"}},
            {"name": "execute", "args": {"command": "ls"}},
        ]
    )
    await approver
    assert decisions == [{"type": "reject", "message": "read-only today"}, {"type": "approve"}]
    cards = [e for e in sink.events if e.type == "requires_action"]
    # Only the call the hook let through asked the user.
    assert len(cards) == 1 and cards[0].data["payload"]

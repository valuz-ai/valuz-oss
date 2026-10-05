"""Claude Agent runtime on the Valuz hook bus.

Built-in tools: PreToolUse starts the ``tool.call`` chain, PostToolUse ends
it. Permissions: ``can_use_tool`` is the ``tool.check`` event with Valuz's own
approval logic as core. With nothing registered the runtime behaves exactly
as before.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from src.core.agent_config import AgentConfig
from src.core.hooks import (
    AGENT_SPAWN,
    SESSION_COMPACT,
    TOOL_CALL,
    TOOL_CHECK,
    CompactDecision,
    ToolDecision,
    ToolOutcome,
    hook_registry,
)
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime

OWNER = "test.claude-hook-bus"


class _Sink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    yield
    hook_registry.unregister_owner(OWNER)


def _runtime(permission_mode: str = "full_access") -> ClaudeAgentRuntime:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _Sink())
    runtime._citation_compaction_enabled = False
    runtime._cached_permission_mode = permission_mode  # type: ignore[assignment]
    return runtime


def _hook(runtime: ClaudeAgentRuntime, name: str) -> Any:
    hooks = runtime._map_hooks()
    assert hooks is not None and name in hooks
    return hooks[name][0].hooks[0]


def test_nothing_registered_means_no_sdk_hooks() -> None:
    assert _runtime()._map_hooks() is None


async def test_rewrite_input_then_output_of_a_builtin_tool() -> None:
    async def handler(ctx, event, next_):  # noqa: ANN001
        result = await next_(event.with_data(input={"command": "ls -la"}))
        return ToolOutcome(content={**result.content, "stdout": "checked"})

    hook_registry.register(TOOL_CALL, handler, owner=OWNER, matcher={"tool.kind": "shell"})
    runtime = _runtime()
    pre = _hook(runtime, "PreToolUse")
    post = _hook(runtime, "PostToolUse")

    out = await pre({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "t1", None)
    assert out["hookSpecificOutput"]["updatedInput"] == {"command": "ls -la"}
    # No permissionDecision: the rewritten call still goes through permissions.
    assert "permissionDecision" not in out["hookSpecificOutput"]

    out = await post(
        {"tool_name": "Bash", "tool_response": {"stdout": "a b", "stderr": ""}},
        "t1",
        None,
    )
    assert out["hookSpecificOutput"]["updatedToolOutput"] == {"stdout": "checked", "stderr": ""}


async def test_observing_handler_changes_nothing() -> None:
    seen: list[str] = []

    async def handler(ctx, event, next_):  # noqa: ANN001
        seen.append(event.get("tool.name"))
        return await next_()

    hook_registry.register(TOOL_CALL, handler, owner=OWNER)
    runtime = _runtime()
    pre = _hook(runtime, "PreToolUse")
    post = _hook(runtime, "PostToolUse")
    assert await pre({"tool_name": "Read", "tool_input": {"file_path": "/a"}}, "t2", None) == {}
    assert await post({"tool_name": "Read", "tool_response": "text"}, "t2", None) == {}
    assert seen == ["Read"]
    assert len(runtime._tool_relay) == 0


async def test_takeover_becomes_a_soft_deny_with_the_answer() -> None:
    async def handler(ctx, event, next_):  # noqa: ANN001
        return ToolOutcome(content="use the safe wrapper instead", executed=False)

    hook_registry.register(TOOL_CALL, handler, owner=OWNER, matcher={"tool": "Bash"})
    pre = _hook(_runtime(), "PreToolUse")
    out = await pre({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}, "t3", None)
    specific = out["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert specific["permissionDecisionReason"] == "use the safe wrapper instead"
    assert "continue_" not in out


async def test_mcp_and_toolkit_tools_are_not_dispatched_here() -> None:
    calls: list[str] = []

    async def handler(ctx, event, next_):  # noqa: ANN001
        calls.append(event.get("tool.name"))
        return await next_()

    hook_registry.register(TOOL_CALL, handler, owner=OWNER)
    pre = _hook(_runtime(), "PreToolUse")
    for name in ("mcp__srv__search", "mcp__harness_toolkit__execute_code"):
        assert await pre({"tool_name": name, "tool_input": {}}, f"id-{name}", None) == {}
    assert calls == []


async def test_failed_tool_and_never_run_tool_finish_the_chain() -> None:
    outcomes: list[ToolOutcome] = []

    async def handler(ctx, event, next_):  # noqa: ANN001
        result = await next_()
        outcomes.append(result)
        return result

    hook_registry.register(TOOL_CALL, handler, owner=OWNER)
    runtime = _runtime()
    pre = _hook(runtime, "PreToolUse")
    failure = _hook(runtime, "PostToolUseFailure")
    await pre({"tool_name": "Bash", "tool_input": {"command": "x"}}, "f1", None)
    await failure({"tool_name": "Bash", "error": "exit 1"}, "f1", None)
    await pre({"tool_name": "Bash", "tool_input": {"command": "y"}}, "f2", None)
    await runtime._tool_relay.abort_all()
    assert outcomes[0] == ToolOutcome(content="exit 1", is_error=True)
    assert outcomes[1].executed is False
    assert len(runtime._tool_relay) == 0


async def test_tool_check_wraps_valuz_approval() -> None:
    async def deny_rm(ctx, event, next_):  # noqa: ANN001
        if "rm" in str(event.get("input.command")):
            return ToolDecision(behavior="deny", reason="no deletes")
        return await next_()

    hook_registry.register(TOOL_CHECK, deny_rm, owner=OWNER)
    runtime = _runtime()
    denied = await runtime._permission_handler("Bash", {"command": "rm x"}, None)
    assert isinstance(denied, PermissionResultDeny) and denied.message == "no deletes"
    allowed = await runtime._permission_handler("Bash", {"command": "ls"}, None)
    # Unchanged by the hook: the runtime's own SDK result comes back verbatim.
    assert isinstance(allowed, PermissionResultAllow)
    assert allowed.updated_input == {"command": "ls"}


async def test_user_hook_cannot_approve_in_default_mode() -> None:
    async def approve(ctx, event, next_):  # noqa: ANN001
        return ToolDecision(behavior="allow")

    hook_registry.register(TOOL_CHECK, approve, owner=OWNER)
    runtime = _runtime("default")
    runtime.APPROVAL_TIMEOUT_SECONDS = 0.05  # type: ignore[misc]
    result = await runtime._permission_handler("Bash", {"command": "ls"}, None)
    # The hook's "allow" was ignored; Valuz asked the user, who never answered.
    assert isinstance(result, PermissionResultDeny)
    assert result.message == "approval timed out"


async def test_interaction_tools_skip_tool_check() -> None:
    called: list[str] = []

    async def handler(ctx, event, next_):  # noqa: ANN001
        called.append(event.get("tool.name"))
        return await next_()

    hook_registry.register(TOOL_CHECK, handler, owner=OWNER)
    runtime = _runtime()

    async def decide(tool_name: str, input_data: dict[str, Any], context: Any) -> Any:
        return PermissionResultAllow(updated_input=input_data)

    runtime._permission_decision = decide  # type: ignore[method-assign]
    await runtime._permission_handler("AskUserQuestion", {"questions": []}, None)
    assert called == []


async def test_compaction_can_be_blocked_and_spawns_observed() -> None:
    spawned: list[str] = []

    async def block(ctx, event, next_):  # noqa: ANN001
        return CompactDecision(proceed=False, reason="keep the full transcript")

    async def watch(ctx, event, next_):  # noqa: ANN001
        spawned.append(event.get("agent_type"))
        return await next_()

    hook_registry.register(SESSION_COMPACT, block, owner=OWNER)
    hook_registry.register(AGENT_SPAWN, watch, owner=OWNER)
    runtime = _runtime()
    out = await _hook(runtime, "PreCompact")({"trigger": "auto"}, None, None)
    assert out == {"decision": "block", "reason": "keep the full transcript"}
    await _hook(runtime, "SubagentStart")({"agent_type": "researcher"}, None, None)
    assert spawned == ["researcher"]


async def test_parallel_tool_calls_keep_their_own_chains() -> None:
    async def tag(ctx, event, next_):  # noqa: ANN001
        result = await next_()
        return ToolOutcome(content=f"{event.get('input.command')}:{result.content}")

    hook_registry.register(TOOL_CALL, tag, owner=OWNER)
    runtime = _runtime()
    pre = _hook(runtime, "PreToolUse")
    post = _hook(runtime, "PostToolUse")
    await asyncio.gather(
        pre({"tool_name": "Bash", "tool_input": {"command": "a"}}, "p1", None),
        pre({"tool_name": "Bash", "tool_input": {"command": "b"}}, "p2", None),
    )
    out_b, out_a = await asyncio.gather(
        post({"tool_name": "Bash", "tool_response": "B"}, "p2", None),
        post({"tool_name": "Bash", "tool_response": "A"}, "p1", None),
    )
    assert out_a["hookSpecificOutput"]["updatedToolOutput"] == "a:A"
    assert out_b["hookSpecificOutput"]["updatedToolOutput"] == "b:B"

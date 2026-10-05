"""DeepAgents ↔ Valuz hook bus.

DeepAgents runs its tools inside our own LangGraph, so the adapter is a plain
middleware: ``awrap_tool_call`` is the ``tool.call`` chain with the tool's
own execution as core — rewrites, retries (``next`` again) and takeovers all
work. It handles DeepAgents' built-in tools (filesystem, ``execute``,
``task`` …); kernel toolkit tools dispatch in ``call_tooldef`` and MCP tools
in the MCP proxy, so they pass straight through here. A ``task`` call is also
announced as ``agent.spawn``.

Approvals (HITL interrupts) are wrapped per action in the runtime
(``DeepAgentsRuntime._await_host_decisions``) with the helpers below.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from src.core.hooks import (
    AGENT_SPAWN,
    TOOL_CALL,
    HookEvent,
    SessionHooks,
    ToolDecision,
    ToolOutcome,
    native_tool_ref,
    thaw,
)

logger = logging.getLogger(__name__)

PROVIDER = "deepagents"


class ValuzHooksMiddleware(AgentMiddleware):  # type: ignore[type-arg]
    """``tool.call`` / ``agent.spawn`` for DeepAgents' built-in tools."""

    def __init__(
        self,
        hooks: Callable[[], SessionHooks],
        dispatched_elsewhere: Callable[[str], bool],
    ) -> None:
        super().__init__()
        self._hooks = hooks
        self._dispatched_elsewhere = dispatched_elsewhere

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        tool_call = request.tool_call
        name = str(tool_call.get("name") or "")
        if self._dispatched_elsewhere(name):
            return await handler(request)
        args = tool_call.get("args") if isinstance(tool_call.get("args"), Mapping) else {}
        hooks = self._hooks()
        ref = native_tool_ref(PROVIDER, name, args)
        if ref.kind == "subagent" and hooks.wants(AGENT_SPAWN):
            await _announce_spawn(hooks, args)
        data = {"tool": ref.to_dict(), "input": dict(args), "tool_use_id": tool_call.get("id")}
        if not hooks.wants(TOOL_CALL, data):
            return await handler(request)

        produced: list[Any] = []

        async def core(event: HookEvent) -> ToolOutcome:
            new_args = thaw(event.get("input"))
            call_request = request
            if isinstance(new_args, dict) and new_args != dict(args):
                call_request = request.override(tool_call={**tool_call, "args": new_args})
            result = await handler(call_request)
            produced.append(result)
            if isinstance(result, ToolMessage):
                return ToolOutcome(
                    content=result.content,
                    is_error=getattr(result, "status", None) == "error",
                )
            # A Command (state update) — opaque to hooks; reported as-is.
            return ToolOutcome(content=str(result))

        outcome = await hooks.dispatch(TOOL_CALL, data, core)
        last = produced[-1] if produced else None
        if isinstance(outcome, ToolOutcome) and last is not None:
            if not isinstance(last, ToolMessage):
                return last
            unchanged = (
                outcome.executed
                and thaw(outcome.content) == thaw(_frozen(last.content))
                and outcome.is_error == (getattr(last, "status", None) == "error")
            )
            if unchanged:
                return last
        if not isinstance(outcome, ToolOutcome):
            return last if last is not None else await handler(request)
        content = thaw(outcome.content)
        return ToolMessage(
            content=content if isinstance(content, (str, list)) else str(content),
            tool_call_id=str(tool_call.get("id") or ""),
            name=name,
            status="error" if outcome.is_error else "success",
        )


def _frozen(value: Any) -> Any:
    from src.core.hooks import freeze

    return freeze(value)


async def _announce_spawn(hooks: SessionHooks, args: Mapping[str, Any]) -> None:
    async def observed(_event: HookEvent) -> None:
        return None

    try:
        await hooks.dispatch(
            AGENT_SPAWN,
            {
                "agent_type": str(args.get("subagent_type") or "general-purpose"),
                "description": str(args.get("description") or ""),
            },
            observed,
        )
    except Exception:  # noqa: BLE001 — a notification never breaks the call
        logger.warning("agent.spawn dispatch failed", exc_info=True)


# -- approvals (HITL) ----------------------------------------------------------


def decision_from_hitl(
    decision: Mapping[str, Any], original_args: Mapping[str, Any]
) -> ToolDecision:
    kind = decision.get("type")
    if kind == "approve":
        return ToolDecision(behavior="allow")
    if kind == "edit":
        action = decision.get("edited_action") or {}
        args = action.get("args") if isinstance(action, Mapping) else None
        return ToolDecision(
            behavior="allow",
            updated_input=dict(args) if isinstance(args, Mapping) else dict(original_args),
        )
    return ToolDecision(behavior="deny", reason=str(decision.get("message") or "") or None)


def hitl_from_decision(
    decision: ToolDecision, tool_name: str, original_args: Mapping[str, Any]
) -> dict[str, Any]:
    if decision.behavior == "deny":
        return {"type": "reject", "message": decision.reason or f"{tool_name} denied by a hook"}
    updated = thaw(decision.updated_input) if decision.updated_input is not None else None
    if isinstance(updated, dict) and updated != dict(original_args):
        return {"type": "edit", "edited_action": {"name": tool_name, "args": updated}}
    return {"type": "approve"}


__all__ = [
    "PROVIDER",
    "ValuzHooksMiddleware",
    "decision_from_hitl",
    "hitl_from_decision",
]

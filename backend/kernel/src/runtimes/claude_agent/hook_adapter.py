"""Claude Agent ↔ Valuz hook bus.

Claude's built-in tools run inside the CLI; the SDK lets us see a call
before it runs (PreToolUse) and after (PostToolUse / PostToolUseFailure).
:class:`ClaudeToolRelay` turns that pair into one ``tool.call`` chain whose
``core`` is the CLI running the tool:

- PreToolUse starts the chain. When it reaches ``core`` the (possibly
  rewritten) input goes back to the CLI as ``updatedInput``; when a handler
  answers without ``next`` the CLI is told to skip the tool (a soft deny
  whose reason is the handler's answer — the model reads it as the result).
- PostToolUse feeds the tool's output to the waiting ``core``; the chain
  unwinds and a changed outcome goes back as ``updatedToolOutput``.

The CLI runs a tool once per call, so ``next`` re-runs are not available
for Claude's built-in tools (MCP tools go through the MCP proxy, where they
are). MCP and kernel-toolkit tools are dispatched elsewhere and skipped here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.core.hooks import (
    TOOL_CALL,
    HookEvent,
    SessionHooks,
    ToolDecision,
    ToolOutcome,
    ToolRef,
    freeze,
    mcp_tool_ref,
    native_tool_ref,
    thaw,
    toolkit_tool_ref,
)

logger = logging.getLogger(__name__)

_TOOLKIT_PREFIXES = ("mcp__harness_toolkit__",)
# Interaction tools — answering questions / leaving plan mode are not
# permissions a hook should take over.
INTERACTION_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})


def claude_tool_ref(tool_name: str, tool_input: Any) -> ToolRef:
    for prefix in _TOOLKIT_PREFIXES:
        if tool_name.startswith(prefix):
            return toolkit_tool_ref(tool_name[len(prefix) :])
    if tool_name.startswith("mcp__"):
        server, _, tool = tool_name[len("mcp__") :].partition("__")
        if server and tool:
            return mcp_tool_ref(server, tool)
    return native_tool_ref("claude_agent", tool_name, tool_input)


def tool_event_data(tool_name: str, tool_input: Any, tool_use_id: str | None) -> dict[str, Any]:
    ref = claude_tool_ref(tool_name, tool_input)
    return {
        "tool": ref.to_dict(),
        "input": dict(tool_input) if isinstance(tool_input, Mapping) else {},
        "tool_use_id": tool_use_id,
    }


def outcome_text(content: Any) -> str:
    """Model-facing text of a hook's answer (for a soft deny reason)."""
    plain = thaw(content)
    if isinstance(plain, str):
        return plain
    if isinstance(plain, list):
        texts = [
            str(block.get("text"))
            for block in plain
            if isinstance(block, Mapping) and block.get("type") == "text"
        ]
        if texts:
            return "\n".join(texts)
    return json.dumps(plain, ensure_ascii=False, default=str)


_NOT_RUN = object()


def _future() -> asyncio.Future[Any]:
    return asyncio.get_running_loop().create_future()


@dataclass
class _Pending:
    original_input: Any
    started: asyncio.Future[Any] = field(default_factory=lambda: _future())
    output: asyncio.Future[Any] = field(default_factory=lambda: _future())
    task: asyncio.Task[Any] | None = None


@dataclass(frozen=True)
class PreDecision:
    """What PreToolUse should tell the CLI."""

    updated_input: dict[str, Any] | None = None
    deny_reason: str | None = None


class ClaudeToolRelay:
    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}

    def __len__(self) -> int:
        return len(self._pending)

    async def pre(
        self,
        hooks: SessionHooks,
        tool_name: str,
        tool_input: Any,
        tool_use_id: str | None,
    ) -> PreDecision | None:
        """Start the ``tool.call`` chain; ``None`` when nothing wants it."""
        if not tool_use_id:
            return None
        data = tool_event_data(tool_name, tool_input, tool_use_id)
        if data["tool"]["source"] != "native" or not hooks.wants(TOOL_CALL, data):
            return None
        pending = _Pending(original_input=data["input"])

        async def core(event: HookEvent) -> ToolOutcome:
            if pending.started.done():
                raise RuntimeError(
                    "Claude runs a built-in tool once per call; next() can be awaited once"
                )
            pending.started.set_result(thaw(event.get("input")))
            raw = await pending.output
            if raw is _NOT_RUN:
                return ToolOutcome(content="", is_error=True, executed=False)
            if isinstance(raw, _Failure):
                return ToolOutcome(content=raw.error, is_error=True)
            return ToolOutcome(content=raw)

        pending.task = asyncio.ensure_future(hooks.dispatch(TOOL_CALL, data, core))
        await asyncio.wait({pending.started, pending.task}, return_when=asyncio.FIRST_COMPLETED)
        if pending.started.done():
            self._pending[tool_use_id] = pending
            new_input = pending.started.result()
            if new_input != pending.original_input:
                return PreDecision(updated_input=new_input)
            return PreDecision()
        # The chain finished without running the tool: a hook answered.
        try:
            outcome = pending.task.result()
        except Exception as exc:  # noqa: BLE001 — dispatch contains handler failures
            logger.warning("tool.call chain failed before the tool ran: %s", exc)
            return None
        if isinstance(outcome, ToolOutcome):
            return PreDecision(deny_reason=outcome_text(outcome.content))
        return None

    async def post(self, tool_use_id: str | None, output: Any) -> ToolOutcome | None:
        """Finish the chain with the tool's output; the final outcome."""
        pending = self._pending.pop(tool_use_id or "", None)
        if pending is None or pending.task is None:
            return None
        if not pending.output.done():
            pending.output.set_result(output)
        try:
            outcome = await pending.task
        except Exception as exc:  # noqa: BLE001
            logger.warning("tool.call chain failed after the tool ran: %s", exc)
            return None
        return outcome if isinstance(outcome, ToolOutcome) else None

    async def failed(self, tool_use_id: str | None, error: str) -> None:
        await self.post(tool_use_id, _Failure(error))

    async def abort_all(self) -> None:
        """The turn ended: calls that never ran (denied, interrupted) finish now."""
        pending = list(self._pending.values())
        self._pending.clear()
        for item in pending:
            if not item.output.done():
                item.output.set_result(_NOT_RUN)
        tasks = [item.task for item in pending if item.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


@dataclass(frozen=True)
class _Failure:
    error: str


def outcome_changed(outcome: ToolOutcome, seen: Any) -> bool:
    return outcome.is_error or thaw(outcome.content) != thaw(freeze(seen))


def decision_from_permission(result: Any) -> ToolDecision:
    """Claude SDK permission result → bus decision."""
    from claude_agent_sdk import PermissionResultAllow

    if isinstance(result, PermissionResultAllow):
        updated = result.updated_input
        return ToolDecision(
            behavior="allow",
            updated_input=dict(updated) if isinstance(updated, Mapping) else None,
        )
    return ToolDecision(behavior="deny", reason=getattr(result, "message", None) or None)


__all__ = [
    "ClaudeToolRelay",
    "INTERACTION_TOOLS",
    "PreDecision",
    "claude_tool_ref",
    "decision_from_permission",
    "outcome_changed",
    "outcome_text",
    "tool_event_data",
]

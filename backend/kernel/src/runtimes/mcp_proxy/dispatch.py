"""``tool.call`` for MCP tools — one helper every MCP call path uses.

Each runtime reaches MCP servers its own way (Claude through the in-process
source proxy, DeepAgents through langchain-mcp-adapters, PTC through its
upstream pool, Codex and DSH through the kernel's HTTP proxy). Each of those
call sites hands the upstream call to :func:`dispatch_mcp_call`, so a handler
sees the same event — ``tool.name = "mcp__<server>__<tool>"``,
``source="mcp"`` — in every runtime, with the upstream call as core.

The event's ``content`` is the result's content blocks as plain JSON. When no
handler changes the outcome the original ``CallToolResult`` comes back
untouched (``structuredContent`` / ``_meta`` included).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from mcp.types import CallToolResult
from src.core.hooks import (
    TOOL_CALL,
    HookEvent,
    SessionHooks,
    ToolOutcome,
    mcp_tool_ref,
    thaw,
)
from src.core.mcp_source_metadata import (
    unwrap_mcp_source_content_transport,
    wrap_mcp_result_metadata_in_content_for_transport,
)

UpstreamCall = Callable[[dict[str, Any]], Awaitable[Any]]


def outcome_from_result(result: Any) -> ToolOutcome:
    if isinstance(result, CallToolResult):
        blocks = [block.model_dump(mode="json", exclude_none=True) for block in result.content]
        return ToolOutcome(content=blocks, is_error=bool(result.isError))
    return ToolOutcome(content=result)


def result_from_outcome(outcome: ToolOutcome, original: Any = None) -> CallToolResult:
    """A ``CallToolResult`` carrying a handler's content.

    Handlers rewrite the model-facing content blocks. When the upstream
    produced the result, its ``structuredContent`` and ``_meta`` are kept as
    the server sent them: a tool that declares an output schema must still
    return structured content (MCP clients validate it), and Valuz's source
    metadata rides ``_meta``.
    """
    content = thaw(outcome.content)
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    elif not isinstance(content, list):
        content = [{"type": "text", "text": str(content)}]
    payload: dict[str, Any] = {"content": content, "isError": outcome.is_error}
    if isinstance(original, CallToolResult) and outcome.executed:
        if original.structuredContent is not None:
            payload["structuredContent"] = original.structuredContent
        if original.meta is not None:
            payload["_meta"] = original.meta
    return CallToolResult.model_validate(payload)


def mcp_event_data(server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": mcp_tool_ref(server, tool).to_dict(),
        "input": dict(arguments),
        "tool_use_id": None,
    }


async def dispatch_mcp_call(
    hooks: SessionHooks | None,
    server: str,
    tool: str,
    arguments: dict[str, Any],
    call: UpstreamCall,
    *,
    carry_source_metadata: bool = False,
) -> Any:
    """Run one MCP tool call through ``tool.call``; *call* runs it upstream.

    ``carry_source_metadata`` packs the result's ``_meta`` source descriptor
    into the content the handlers see (Claude's in-process proxy does the
    same), for the citation projection of runtimes that only ever see the
    content (``core/hooks/builtin/citation_projection.py``). The marker never
    reaches the runtime: an untouched outcome returns the upstream result,
    and a leftover marker is removed from a rewritten one.
    """
    if hooks is None:
        return await call(arguments)
    data = mcp_event_data(server, tool, arguments)
    if not hooks.wants(TOOL_CALL, data):
        return await call(arguments)

    produced: list[tuple[ToolOutcome, Any]] = []

    async def core(event: HookEvent) -> ToolOutcome:
        args = thaw(event.get("input"))
        result = await call(args if isinstance(args, dict) else {})
        seen = (
            wrap_mcp_result_metadata_in_content_for_transport(result, server_name=server)
            if carry_source_metadata
            else result
        )
        outcome = outcome_from_result(seen)
        produced.append((outcome, result))
        return outcome

    outcome = await hooks.dispatch(TOOL_CALL, data, core)
    for core_outcome, raw in reversed(produced):
        if outcome is core_outcome:
            return raw
    if not isinstance(outcome, ToolOutcome):
        return produced[-1][1] if produced else await call(arguments)
    if carry_source_metadata:
        _descriptor, _structured, restored = unwrap_mcp_source_content_transport(
            thaw(outcome.content)
        )
        if restored is not None:
            outcome = replace(outcome, content=restored)
    return result_from_outcome(outcome, produced[-1][1] if produced else None)


__all__ = [
    "dispatch_mcp_call",
    "mcp_event_data",
    "outcome_from_result",
    "result_from_outcome",
]

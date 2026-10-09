"""The kernel toolkit's ``tool.call`` dispatch point.

Kernel ToolDefs (``execute_code`` and friends) run in the kernel whichever
runtime called them — Claude through its in-process SDK server, DeepAgents as
LangChain tools, Codex and DSH through the kernel's ``/mcp/toolkit`` HTTP
endpoint. Every one of those paths calls :func:`call_tooldef`, so a handler
sees the same ``tool.call`` (``source="toolkit"``) in every runtime and can
rewrite, retry or answer it.
"""

from __future__ import annotations

import json
from typing import Any

from src.core.hooks.events import TOOL_CALL, HookEvent, ToolOutcome
from src.core.hooks.freeze import thaw
from src.core.hooks.registry import SessionHooks
from src.core.hooks.tool_identity import toolkit_tool_ref
from src.core.tools import ExecContext, ToolDef, ToolResult


def _as_text(content: Any) -> str:
    plain = thaw(content)
    if isinstance(plain, str):
        return plain
    return json.dumps(plain, ensure_ascii=False, default=str)


async def call_tooldef(
    hooks: SessionHooks | None,
    tdef: ToolDef,
    args: dict[str, Any],
    context: ExecContext,
) -> ToolResult:
    """Run *tdef* through the ``tool.call`` chain (directly when nobody listens)."""
    handler = tdef.handler
    if handler is None:
        raise ValueError(f"tool {tdef.name} has no handler")
    if hooks is None:
        from src.core.hooks.registry import hook_registry

        if hook_registry.has_required(TOOL_CALL):
            return ToolResult(
                content="Required execution guard has no trusted session context", is_error=True
            )
        return await handler(args, context)
    data = {"tool": toolkit_tool_ref(tdef.name).to_dict(), "input": args, "tool_use_id": None}
    if not hooks.wants(TOOL_CALL, data):
        return await handler(args, context)

    async def core(event: HookEvent) -> ToolOutcome:
        call_args = thaw(event.get("input"))
        result = await handler(call_args if isinstance(call_args, dict) else {}, context)
        return ToolOutcome(content=result.content, is_error=result.is_error)

    outcome = await hooks.dispatch(TOOL_CALL, data, core)
    if not isinstance(outcome, ToolOutcome):
        return await handler(args, context)
    return ToolResult(content=_as_text(outcome.content), is_error=outcome.is_error)


__all__ = ["call_tooldef"]

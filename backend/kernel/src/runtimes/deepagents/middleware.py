"""Custom langchain middleware used by ``DeepAgentsRuntime``.

DeepAgents wires extra behavior into a graph by composing langchain
``AgentMiddleware`` subclasses. This module collects the harness-side
middleware so the runtime stays focused on graph wiring and event mapping.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

logger = logging.getLogger(__name__)


class ToolErrorTolerantMiddleware(AgentMiddleware):
    """Catch tool exceptions and feed them back to the model as a ToolMessage.

    DeepAgents (langchain) lets a tool raise propagate up the graph, which
    aborts the run. For transient/recoverable failures (HTTP 4xx/5xx, network
    blips, validation errors) we'd rather hand the error string to the model
    so it can read the message and try again on the next step. Permanent
    bugs still surface — the agent will see the same error repeatedly and
    eventually give up via max_turns.
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return await handler(request)
        except Exception as exc:
            tool_call = request.tool_call
            logger.warning(
                "Tool '%s' raised %s — returning error to model: %s",
                tool_call.get("name"),
                type(exc).__name__,
                exc,
            )
            return ToolMessage(
                content=f"Error calling tool '{tool_call.get('name')}': {exc}",
                tool_call_id=tool_call["id"],
                name=tool_call.get("name"),
                status="error",
            )

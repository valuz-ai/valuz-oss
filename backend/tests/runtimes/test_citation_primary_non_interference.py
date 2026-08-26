"""Citation infrastructure must not steer the Primary Agent's research."""

from __future__ import annotations

import json
from typing import Any, cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from src.core.agent_config import AgentConfig
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime
from src.runtimes.deepagents.middleware import (
    CitationEvidenceCompactionMiddleware,
    ToolErrorTolerantMiddleware,
)


class _RecordingSink:
    async def emit(self, _event: Any) -> None:
        return None


def _discovery_payload() -> dict[str, Any]:
    return {
        "docs": [
            {
                "doc_id": f"doc-{index}",
                "title": f"Provider result {index}",
                "summary": f"Provider summary {index}",
                "metadata": {
                    "fiscal_year": "FY2026",
                    "fiscal_quarter": f"Q{(index % 4) + 1}",
                },
                "companies": [
                    {"stocks": [{"symbol": "MSFT" if index != 1 else "AMZN"}]}
                ],
            }
            for index in range(6)
        ]
    }


async def test_deepagents_citation_adapter_preserves_discovery_rows_and_order() -> None:
    """Evidence capture may annotate a result but cannot select candidates."""

    payload = _discovery_payload()
    original = ToolMessage(
        content=json.dumps(payload),
        tool_call_id="call-discovery",
        name="conferences_search",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        ToolCallRequest,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "call-discovery",
                    "name": "conferences_search",
                    "args": {"symbols": ["MSFT"]},
                }
            },
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        request,
        handler,
    )

    assert isinstance(result, ToolMessage)
    assert json.loads(cast(str, result.content))["docs"] == payload["docs"]


async def test_claude_citation_adapter_preserves_discovery_rows_and_order() -> None:
    payload = _discovery_payload()
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]

    result = await hook(
        {
            "tool_name": "mcp__reportify__conferences_search",
            "tool_input": {"symbols": ["MSFT"]},
            "tool_response": payload,
        },
        "call-discovery",
        None,  # type: ignore[arg-type]
    )

    hook_output = result.get("hookSpecificOutput", {})
    visible = hook_output.get("updatedMCPToolOutput", payload)
    if isinstance(visible, str):
        visible = json.loads(visible)
    assert visible["docs"] == payload["docs"]


async def test_tool_error_adapter_never_short_circuits_agent_retries() -> None:
    """A generic error adapter may report failures, not set a retrieval policy."""

    middleware = ToolErrorTolerantMiddleware()
    calls = 0

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        nonlocal calls
        calls += 1
        raise RuntimeError(f"provider 404 attempt {calls}")

    request = cast(
        ToolCallRequest,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "call-fetch",
                    "name": "document_fetch",
                    "args": {"doc_id": "doc-1"},
                }
            },
        )(),
    )

    results = [
        await middleware.awrap_tool_call(request, handler)
        for _ in range(3)
    ]

    assert calls == 3
    assert all("provider 404 attempt" in str(result.content) for result in results)

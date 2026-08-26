"""Anthropic history compatibility for the DeepAgents runtime."""

# ruff: noqa: I001
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401

from langchain_core.messages import AIMessage, ToolMessage
from src.core.agent_config import AgentConfig
from src.core.types import ModelProvider, ModelSettings, Session
from src.runtimes.deepagents.runtime import DeepAgentsRuntime


def test_anthropic_client_drops_signature_only_thinking_history_block() -> None:
    """Streaming gateways may persist a terminal signature delta as a
    standalone thinking block. Anthropic rejects that block on the next model
    round because it has no required ``thinking`` field; keep the tool call but
    drop only the invalid history block before transport.
    """

    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        model="valuz-lite-anthropic",
        event_sink=object(),  # type: ignore[arg-type]
        model_provider=ModelProvider(
            api_key="test-key",
            base_url="https://gateway.invalid",
            api_protocol="anthropic",
        ),
    )
    session = Session(
        id="session-1",
        agent_config=runtime.config,
        cwd="/tmp/session-1",
        runtime_provider="deepagents",
        model="valuz-lite-anthropic",
        model_provider=runtime.model_provider,
        model_settings=ModelSettings(effort="high"),
    )
    client = runtime._build_model_client(session)
    messages = [
        AIMessage(
            content=[
                {
                    "type": "thinking",
                    "signature": "signature-only",
                    "index": 0,
                },
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "search",
                    "input": {"query": "MLCC"},
                    "index": 1,
                },
            ]
        ),
        ToolMessage(content="result", tool_call_id="call-1"),
    ]

    payload = client._get_request_payload(messages)  # noqa: SLF001

    assert payload["messages"][0]["content"] == [
        {
            "type": "tool_use",
            "id": "call-1",
            "name": "search",
            "input": {"query": "MLCC"},
        }
    ]


def test_anthropic_client_preserves_complete_thinking_history_block() -> None:
    runtime = DeepAgentsRuntime(
        AgentConfig(id="agent-1", name="tester"),
        model="valuz-lite-anthropic",
        event_sink=object(),  # type: ignore[arg-type]
        model_provider=ModelProvider(api_key="test-key", api_protocol="anthropic"),
    )
    session = Session(
        id="session-1",
        agent_config=runtime.config,
        cwd="/tmp/session-1",
        runtime_provider="deepagents",
        model="valuz-lite-anthropic",
        model_provider=runtime.model_provider,
    )
    client = runtime._build_model_client(session)
    messages = [
        AIMessage(
            content=[
                {
                    "type": "thinking",
                    "thinking": "valid reasoning",
                    "signature": "signed",
                    "index": 0,
                },
                {"type": "text", "text": "done", "index": 1},
            ]
        )
    ]

    payload = client._get_request_payload(messages)  # noqa: SLF001

    assert payload["messages"][0]["content"][0] == {
        "type": "thinking",
        "thinking": "valid reasoning",
        "signature": "signed",
    }

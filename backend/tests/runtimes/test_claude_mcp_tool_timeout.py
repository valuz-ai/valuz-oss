"""Claude MCP proxy preserves timeout and source-metadata transport semantics."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

from mcp.types import CallToolResult, TextContent
from src.core.agent_config import AgentConfig
from src.core.mcp_source_metadata import (
    MCP_SOURCE_CONTENT_TRANSPORT_PREFIX,
    MCP_SOURCE_METADATA_KEY,
)
from src.core.types import McpHttpServerConfig, McpStdioServerConfig, Session
from src.runtimes.claude_agent.mcp_proxy import ClaudeMcpSourceProxy
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime


def test_http_proxy_carries_the_declared_timeout_in_ms() -> None:
    proxy = ClaudeMcpSourceProxy(
        McpHttpServerConfig(
            name="harness",
            url="http://127.0.0.1:8000/_internal/mcp/toolkit/base/mcp",
            tool_timeout_sec=720.0,
        )
    )

    entry = proxy.sdk_config()

    assert entry["type"] == "sdk"
    assert entry["timeout"] == 720_000


def test_proxy_without_a_declared_timeout_stays_on_the_cli_default() -> None:
    proxy = ClaudeMcpSourceProxy(
        McpHttpServerConfig(name="external", url="https://example.test/mcp")
    )

    assert "timeout" not in proxy.sdk_config()


def test_stdio_proxy_has_no_timeout_to_declare() -> None:
    proxy = ClaudeMcpSourceProxy(
        McpStdioServerConfig(name="local", command="node", args=("server.js",))
    )

    assert "timeout" not in proxy.sdk_config()


def test_runtime_routes_every_external_mcp_server_through_the_sdk_proxy() -> None:
    config = McpHttpServerConfig(name="external", url="https://example.test/mcp")
    agent = AgentConfig(id="a", name="a")
    runtime = ClaudeAgentRuntime(agent, "", SimpleNamespace())  # type: ignore[arg-type]
    session = Session(
        id="s",
        agent_config=agent,
        cwd="/tmp",
        mcp_servers=(config,),
    )

    options = runtime._build_options(session)

    assert isinstance(options.mcp_servers, dict)
    assert options.mcp_servers["external"]["type"] == "sdk"
    assert len(runtime._mcp_source_proxies) == 1


async def test_proxy_preserves_source_metadata_for_an_arbitrary_tool_name() -> None:
    payload = {"data": [{"symbol": "TEST", "value": 42}]}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    descriptor = {
        "version": 1,
        "provider": {"id": "generic-provider"},
        "operation": {"toolName": "future_generic_tool"},
        "result": {
            "target": "structuredContent",
            "hash": {"algorithm": "sha256", "value": digest},
        },
        "resources": [],
    }

    class FakeSession:
        async def list_tools(self, cursor=None):  # noqa: ANN001, ANN202
            return SimpleNamespace(tools=[], nextCursor=None)

        async def call_tool(self, name, arguments, **kwargs):  # noqa: ANN001, ANN202
            assert name == "future_generic_tool"
            assert arguments == {"symbol": "TEST"}
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(payload))],
                structuredContent=payload,
                _meta={MCP_SOURCE_METADATA_KEY: descriptor},
            )

    @asynccontextmanager
    async def session_context():  # noqa: ANN202
        yield FakeSession()

    proxy = ClaudeMcpSourceProxy(
        McpHttpServerConfig(name="any-server", url="https://example.test/mcp"),
        session_context_factory=session_context,
    )
    try:
        result = await proxy.call_tool("future_generic_tool", {"symbol": "TEST"})
    finally:
        await proxy.close()

    assert result.content[0].text == json.dumps(payload)
    assert result.content[-1].text.startswith(MCP_SOURCE_CONTENT_TRANSPORT_PREFIX)

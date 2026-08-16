"""deepagents MCP loading tolerates broken servers instead of failing the turn.

The aggregate ``MultiServerMCPClient.get_tools()`` call fails the WHOLE turn
when any single configured server is unreachable — a stdio connector whose
command isn't installed on the machine surfaced as a bare
``FileNotFoundError: [Errno 2]`` killing every deepagents conversation, while
the CLI runtimes (claude_agent / codex) degrade to "server unavailable, tools
absent". ``_build_mcp_tools`` now pre-flights stdio commands with ``which()``
and loads tools per server, skipping the broken ones with an attributable log.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import sys
from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

import pytest

from src.core.types import McpHttpServerConfig, McpStdioServerConfig
from src.runtimes.deepagents.runtime import (
    DeepAgentsRuntime,
    _preserve_mcp_source_metadata,
)


class _FakeClient:
    """Stands in for ``MultiServerMCPClient``; per-server canned behavior."""

    instances: list[_FakeClient] = []
    behaviors: dict[str, object] = {}

    def __init__(
        self,
        spec: dict[str, dict[str, object]],
        *,
        tool_interceptors: list[object] | None = None,
        callbacks: object | None = None,
    ) -> None:
        self.spec = spec
        self.callbacks = callbacks
        self.tool_interceptors = tool_interceptors
        type(self).instances.append(self)

    async def get_tools(self, *, server_name: str | None = None) -> list[object]:
        behavior = type(self).behaviors[server_name]
        if isinstance(behavior, BaseException):
            raise behavior
        assert isinstance(behavior, list)
        return behavior


@pytest.fixture()
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClient]:
    import langchain_mcp_adapters.client as adapter_client

    _FakeClient.instances = []
    _FakeClient.behaviors = {}
    monkeypatch.setattr(adapter_client, "MultiServerMCPClient", _FakeClient)
    return _FakeClient


def _runtime() -> DeepAgentsRuntime:
    return object.__new__(DeepAgentsRuntime)


def _session(*configs: object) -> SimpleNamespace:
    return SimpleNamespace(mcp_servers=list(configs))


def _http(name: str) -> McpHttpServerConfig:
    return McpHttpServerConfig(name=name, url=f"http://127.0.0.1:1/{name}")


async def test_should_skip_stdio_server_whose_command_is_missing(fake_client) -> None:
    session = _session(
        McpStdioServerConfig(name="broken", command="valuz-test-definitely-missing-cmd"),
        _http("ok"),
    )
    fake_client.behaviors = {"ok": ["tool-a"]}

    tools = await _runtime()._build_mcp_tools(session)

    assert tools == ["tool-a"]
    assert list(fake_client.instances[0].spec) == ["ok"]


async def test_should_keep_stdio_server_whose_command_exists(fake_client) -> None:
    session = _session(McpStdioServerConfig(name="local", command=sys.executable))
    fake_client.behaviors = {"local": ["tool-b"]}

    tools = await _runtime()._build_mcp_tools(session)

    assert tools == ["tool-b"]
    assert fake_client.instances[0].spec["local"]["command"] == sys.executable
    # The MCP result-metadata interceptor must reach the client — it is what
    # keeps citation metadata alive through LangChain's result conversion.
    assert fake_client.instances[0].tool_interceptors == [_preserve_mcp_source_metadata]


async def test_should_drop_only_the_failing_server_and_keep_the_rest(fake_client) -> None:
    session = _session(_http("dead"), _http("alive"))
    fake_client.behaviors = {
        "dead": FileNotFoundError(2, "No such file or directory"),
        "alive": ["tool-c"],
    }

    tools = await _runtime()._build_mcp_tools(session)

    assert tools == ["tool-c"]


async def test_should_track_external_mcp_tools_but_exclude_host_harness(fake_client) -> None:
    session = _session(_http("harness"), _http("valuz_docs"), _http("third_party"))
    harness_tool = SimpleNamespace(name="deliver_artifacts")
    valuz_tool = SimpleNamespace(name="document_search")
    connector_tool = SimpleNamespace(name="search_records")
    fake_client.behaviors = {
        "harness": [harness_tool],
        "valuz_docs": [valuz_tool],
        "third_party": [connector_tool],
    }
    runtime = _runtime()

    tools = await runtime._build_mcp_tools(session)

    assert tools == [harness_tool, valuz_tool, connector_tool]
    assert runtime._external_mcp_tool_names == {"document_search", "search_records"}


async def test_should_return_empty_when_every_server_fails(fake_client) -> None:
    session = _session(_http("dead-1"), _http("dead-2"))
    fake_client.behaviors = {
        "dead-1": RuntimeError("connect failed"),
        "dead-2": FileNotFoundError(2, "No such file or directory"),
    }

    tools = await _runtime()._build_mcp_tools(session)

    assert tools == []


async def test_should_not_connect_at_all_when_every_server_is_pruned(fake_client) -> None:
    session = _session(
        McpStdioServerConfig(name="broken", command="valuz-test-definitely-missing-cmd"),
    )

    tools = await _runtime()._build_mcp_tools(session)

    assert tools == []
    assert fake_client.instances == []

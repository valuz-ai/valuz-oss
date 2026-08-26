"""deepagents must survive a long MCP tool call, and see its progress.

This client has no idle-timeout-with-reset: the MCP SDK wraps a request in
``anyio.fail_after``, so the declared timeout is a HARD ceiling that progress
notifications cannot extend — and no measured client behaves differently
(codex and the Claude CLI both abort exactly at their ceiling with beats still
arriving). Two things therefore have to reach the client — the server's
declared ``tool_timeout_sec``, which is the only lever that actually keeps a
long generation alive, and a progress callback, because the SDK attaches
``_meta.progressToken`` only when one exists and without it servers are told
not to report at all.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

import pytest

from src.core.types import McpHttpServerConfig
from src.runtimes.deepagents.runtime import DeepAgentsRuntime, _log_mcp_progress


class _FakeClient:
    """Captures what the runtime hands ``MultiServerMCPClient``."""

    last: _FakeClient | None = None

    def __init__(self, spec: dict[str, dict[str, object]], **kwargs: object) -> None:
        self.spec = spec
        self.kwargs = kwargs
        type(self).last = self

    async def get_tools(self, *, server_name: str | None = None) -> list[object]:
        return []


@pytest.fixture()
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClient]:
    import langchain_mcp_adapters.client as adapter_client

    _FakeClient.last = None
    monkeypatch.setattr(adapter_client, "MultiServerMCPClient", _FakeClient)
    return _FakeClient


async def _spec_for(cfg: object, fake_client: type[_FakeClient]) -> dict[str, object]:
    runtime = object.__new__(DeepAgentsRuntime)
    await runtime._build_mcp_tools(SimpleNamespace(mcp_servers=[cfg]))
    assert fake_client.last is not None
    return fake_client.last.spec["harness"]


def _harness(**extra: object) -> McpHttpServerConfig:
    return McpHttpServerConfig(
        name="harness",
        url="http://127.0.0.1:8000/_internal/mcp/toolkit/base/mcp",
        **extra,  # type: ignore[arg-type]
    )


async def test_streamable_http_read_timeout_follows_the_declared_ceiling(
    fake_client,
) -> None:
    entry = await _spec_for(_harness(tool_timeout_sec=720.0), fake_client)

    # ``StreamableHttpConnection`` types this field as a timedelta.
    assert entry["sse_read_timeout"] == timedelta(seconds=720)


async def test_session_read_timeout_follows_the_declared_ceiling(fake_client) -> None:
    entry = await _spec_for(_harness(tool_timeout_sec=720.0), fake_client)

    # The transport read is only half of it: ``ClientSession`` applies its own
    # per-request ceiling, and the SHORTER of the two is what actually aborts.
    assert entry["session_kwargs"] == {"read_timeout_seconds": timedelta(seconds=720)}


async def test_sse_transport_takes_seconds_as_a_float(fake_client) -> None:
    entry = await _spec_for(
        _harness(transport="sse", tool_timeout_sec=720.0), fake_client
    )

    # ``SSEConnection`` types the same field as seconds-as-float; a timedelta
    # here would reach ``httpx.Timeout`` and blow up at connect time.
    assert entry["sse_read_timeout"] == 720.0


async def test_server_without_a_declared_timeout_keeps_the_sdk_defaults(
    fake_client,
) -> None:
    entry = await _spec_for(_harness(), fake_client)

    assert "sse_read_timeout" not in entry
    assert "session_kwargs" not in entry


async def test_a_progress_callback_is_registered_so_servers_may_report(
    fake_client,
) -> None:
    await _spec_for(_harness(), fake_client)
    assert fake_client.last is not None

    callbacks = fake_client.last.kwargs["callbacks"]
    assert getattr(callbacks, "on_progress", None) is _log_mcp_progress


async def test_the_progress_callback_tolerates_a_bare_notification() -> None:
    # ``total`` and ``message`` are optional in the protocol; a server that
    # sends neither must not blow up the tool call inside the callback.
    await _log_mcp_progress(1.0, None, None)

"""Edition always-on MCP extension point (ports/mcp_always_on)."""

from collections.abc import Iterator

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (kernel bootstrap side effect)
from valuz_agent.adapters.capability_resolver import always_on_http_mcp_servers
from valuz_agent.ports.extensions import Extensions, ext
from valuz_agent.ports.mcp_always_on import AlwaysOnMcpServerSpec


@pytest.fixture
def restore_specs() -> Iterator[None]:
    saved = list(ext.always_on_mcp_specs)
    try:
        yield
    finally:
        ext.always_on_mcp_specs = saved


def test_oss_default_registers_no_edition_server() -> None:
    assert Extensions().always_on_mcp_specs == []


@pytest.mark.asyncio
async def test_edition_server_rides_every_session(restore_specs: None) -> None:
    ext.always_on_mcp_specs = [
        AlwaysOnMcpServerSpec(name="valuz_finance", path="/_internal/mcp/finance/base")
    ]

    servers = await always_on_http_mcp_servers("session-1", owner_user_id="owner-1")

    by_name = {server.name: server for server in servers}
    assert set(by_name) >= {
        "valuz_docs",
        "valuz_automations",
        "valuz_playbooks",
        "valuz_connectors",
        "harness",
        "valuz_finance",
    }
    finance = by_name["valuz_finance"]
    assert finance.url.endswith("/_internal/mcp/finance/base/mcp")
    assert finance.headers["X-Valuz-Session-Id"] == "session-1"
    assert "X-Valuz-Internal" in finance.headers


@pytest.mark.asyncio
async def test_reserved_names_cannot_be_shadowed(restore_specs: None) -> None:
    ext.always_on_mcp_specs = [
        AlwaysOnMcpServerSpec(name="harness", path="/_internal/mcp/evil"),
        AlwaysOnMcpServerSpec(name="valuz_finance", path="/_internal/mcp/finance/base"),
    ]

    servers = await always_on_http_mcp_servers("session-1", owner_user_id="owner-1")

    harness = [server for server in servers if server.name == "harness"]
    assert len(harness) == 1
    assert "/evil" not in harness[0].url
    assert any(server.name == "valuz_finance" for server in servers)

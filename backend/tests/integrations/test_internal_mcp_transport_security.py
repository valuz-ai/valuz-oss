"""The built-in MCP servers must work behind a multi-replica public ingress.

Two FastMCP defaults break that deployment, and both bit on
``/_internal/mcp/{docs,automations,connectors}`` while the toolkit server
(raw ``Server`` + stateless session manager, no security settings) kept
working:

- ``FastMCP`` auto-enables DNS-rebinding protection when built with its
  default ``host="127.0.0.1"`` — only localhost ``Host`` headers pass, so a
  sandbox kernel reaching the host callback through a public ingress hostname
  got ``421 Misdirected Request``. Auth for these endpoints lives in
  ``build_internal_mcp_asgi``'s per-owner token check, so the Host allowlist
  must stay off.
- ``stateless_http`` defaults to ``False`` — the session manager keeps
  ``Mcp-Session-Id`` state in process memory, so a follow-up request routed
  to another replica/worker 404s and the client raises ``McpError: Session
  terminated``. These servers must stay stateless like the toolkit.

These tests pin both.
"""

from __future__ import annotations

import pytest
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.requests import Request

from valuz_agent.integrations.automations_mcp_server import _mcp as _automations_mcp
from valuz_agent.integrations.connectors_mcp_server import _mcp as _connectors_mcp
from valuz_agent.integrations.docs_mcp_server import _mcp as _docs_mcp

_SERVERS = {
    "connectors": _connectors_mcp,
    "docs": _docs_mcp,
    "automations": _automations_mcp,
}


@pytest.mark.parametrize("name", sorted(_SERVERS))
def test_rebinding_protection_disabled(name: str) -> None:
    settings = _SERVERS[name].settings.transport_security
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is False


@pytest.mark.parametrize("name", sorted(_SERVERS))
def test_stateless_http(name: str) -> None:
    """No in-memory MCP session state — replica-safe behind a load balancer."""
    assert _SERVERS[name].settings.stateless_http is True


@pytest.mark.parametrize("name", sorted(_SERVERS))
async def test_public_host_request_passes_transport_security(name: str) -> None:
    """A tokenized kernel POST arriving with a public Host must not 421."""
    middleware = TransportSecurityMiddleware(_SERVERS[name].settings.transport_security)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "query_string": b"",
        "headers": [
            (b"host", b"api.example.com"),
            (b"content-type", b"application/json"),
        ],
    }
    assert await middleware.validate_request(Request(scope), is_post=True) is None

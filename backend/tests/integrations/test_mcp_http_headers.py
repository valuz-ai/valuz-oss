"""Outbound MCP request-header policy regression tests."""

from __future__ import annotations

from valuz_agent.integrations.mcp_http import MCP_USER_AGENT, mcp_request_headers


def test_should_add_valuz_user_agent_to_outbound_mcp_requests() -> None:
    assert mcp_request_headers({"Authorization": "Bearer token"}) == {
        "Authorization": "Bearer token",
        "User-Agent": MCP_USER_AGENT,
    }


def test_should_preserve_explicit_user_agent_case_insensitively() -> None:
    assert mcp_request_headers({"user-agent": "CustomClient/2.0"}) == {
        "user-agent": "CustomClient/2.0"
    }


def test_should_not_mutate_the_caller_headers() -> None:
    supplied = {"X-API-Key": "secret"}

    result = mcp_request_headers(supplied)

    assert supplied == {"X-API-Key": "secret"}
    assert result["User-Agent"] == MCP_USER_AGENT

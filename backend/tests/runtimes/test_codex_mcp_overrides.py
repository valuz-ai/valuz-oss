"""Regression: MCP map fields (``http_headers`` / ``env``) must be emitted as
per-key dotted config entries, never as an inline table.

Codex's config overlay parser reads an inline-table RHS
(``mcp_servers.X.http_headers={ Authorization = "…" }``) as a *string* and
aborts at app-server startup with ``invalid type: string … expected a map in
mcp_servers.X.http_headers`` (verified against codex-cli 0.137.0-alpha.4
in-sandbox). That crash then surfaced to the user as a bogus ``runtime process
interrupted`` for EVERY session carrying a header-bearing HTTP MCP server. The
dotted form ``mcp_servers.X.http_headers.Authorization="…"`` parses as a table
entry.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.types import McpHttpServerConfig, McpStdioServerConfig, Session
from src.runtimes.codex.runtime import _build_config_overrides, _toml_key


def _session(*servers: object) -> Session:
    return Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="codex",
        mcp_servers=tuple(servers),  # type: ignore[arg-type]
    )


def test_http_headers_are_dotted_keys_not_inline_table() -> None:
    session = _session(
        McpHttpServerConfig(
            name="valuz-search",
            url="https://mcp.example.com/mcp",
            headers={"Authorization": "Bearer tok", "X-Valuz-Session-Id": "s1"},
        )
    )
    ov = _build_config_overrides(session, None, "gpt-5.5")

    # No inline table anywhere — that is the exact shape codex rejects.
    assert not any("http_headers={" in o for o in ov), ov
    assert 'mcp_servers.valuz-search.url="https://mcp.example.com/mcp"' in ov
    assert 'mcp_servers.valuz-search.http_headers.Authorization="Bearer tok"' in ov
    assert 'mcp_servers.valuz-search.http_headers.X-Valuz-Session-Id="s1"' in ov


def test_stdio_env_is_dotted_keys_not_inline_table() -> None:
    session = _session(
        McpStdioServerConfig(name="local", command="run-it", env={"LOG_LEVEL": "debug"})
    )
    ov = _build_config_overrides(session, None, "gpt-5.5")

    assert not any(".env={" in o for o in ov), ov
    assert 'mcp_servers.local.command="run-it"' in ov
    assert 'mcp_servers.local.env.LOG_LEVEL="debug"' in ov


def test_header_value_is_toml_quoted_against_injection() -> None:
    session = _session(
        McpHttpServerConfig(name="s", url="https://x/mcp", headers={"Authorization": 'a"b\\c'})
    )
    ov = _build_config_overrides(session, None, "gpt-5.5")
    # Quotes / backslashes in the value are escaped, not left to corrupt the -c.
    assert r'mcp_servers.s.http_headers.Authorization="a\"b\\c"' in ov


def test_tool_timeout_sec_emitted_as_bare_float_when_set() -> None:
    # The harness toolkit hosts await_members, which parks longer than codex's
    # 120s default tool-call timeout. The declared per-server timeout must reach
    # codex as a BARE number (never quoted) so it parses as a float, not a string.
    session = _session(
        McpHttpServerConfig(
            name="harness",
            url="http://127.0.0.1:8000/_internal/mcp/toolkit/lead/mcp",
            tool_timeout_sec=480.0,  # arbitrary — this asserts the EMIT, not the policy value
        )
    )
    ov = _build_config_overrides(session, None, "gpt-5.5")
    assert "mcp_servers.harness.tool_timeout_sec=480.0" in ov, ov
    assert not any('tool_timeout_sec="' in o for o in ov), ov


def test_tool_timeout_sec_absent_when_unset() -> None:
    # A server that does not declare one keeps codex's own default — no override.
    session = _session(McpHttpServerConfig(name="s", url="https://x/mcp"))
    ov = _build_config_overrides(session, None, "gpt-5.5")
    assert not any("tool_timeout_sec" in o for o in ov), ov


def test_exposed_toolkit_gets_raised_tool_timeout() -> None:
    # The kernel-exposed toolkit path (expose_toolkit) carries await_members too,
    # so it must lift the same 120s ceiling.
    from src.runtimes.codex.runtime import (
        _HARNESS_TOOLKIT_MCP_NAME,
        _HARNESS_TOOLKIT_TOOL_TIMEOUT_SEC,
    )

    ov = _build_config_overrides(_session(), None, "gpt-5.5", expose_toolkit=True)
    assert (
        f"mcp_servers.{_HARNESS_TOOLKIT_MCP_NAME}.tool_timeout_sec="
        f"{_HARNESS_TOOLKIT_TOOL_TIMEOUT_SEC}" in ov
    ), ov


def test_toml_key_bare_vs_quoted() -> None:
    # Real header names are bare-key-safe → emitted bare.
    assert _toml_key("Authorization") == "Authorization"
    assert _toml_key("X-Valuz-Session-Id") == "X-Valuz-Session-Id"
    assert _toml_key("LOG_LEVEL") == "LOG_LEVEL"
    # A dotted-unsafe key (dot / space) must be quoted so it stays ONE segment.
    assert _toml_key("Weird.Key") == '"Weird.Key"'
    assert _toml_key("has space") == '"has space"'

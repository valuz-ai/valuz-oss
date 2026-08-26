"""Codex secret externalization: the session-id header is not a secret.

Field bug: every http header value was env-externalized as a secret, and the
residue guard then matched the ``X-Valuz-Session-Id`` value inside
``mcp_servers.harness_toolkit.url`` — which legitimately embeds the session
id — refusing to launch codex whenever PTC exposed the kernel toolkit.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.types import McpHttpServerConfig, Session
from src.runtimes.codex import runtime as codex_runtime

_SESSION_ID = "sessabc1234567890abcdef1234567890"


def _session() -> Session:
    return Session(
        id=_SESSION_ID,
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        mcp_servers=(
            McpHttpServerConfig(
                name="valuz_docs",
                url="http://127.0.0.1:8000/_internal/mcp/docs/mcp",
                headers={
                    "X-Valuz-Internal": "internal-secret-token",
                    "X-Valuz-Session-Id": _SESSION_ID,
                },
            ),
        ),
    )


def _builder_style_overrides() -> tuple[str, ...]:
    toml_key = codex_runtime._toml_key
    toml_quote = codex_runtime._toml_quote
    return (
        f"mcp_servers.valuz_docs.url={toml_quote('http://127.0.0.1:8000/_internal/mcp/docs/mcp')}",
        f"mcp_servers.valuz_docs.http_headers.{toml_key('X-Valuz-Internal')}="
        f"{toml_quote('internal-secret-token')}",
        f"mcp_servers.valuz_docs.http_headers.{toml_key('X-Valuz-Session-Id')}="
        f"{toml_quote(_SESSION_ID)}",
        # The kernel toolkit bridge URL legally embeds the session id.
        f"mcp_servers.harness_toolkit.url="
        f"{toml_quote(f'http://127.0.0.1:8000/mcp/toolkit/{_SESSION_ID}')}",
    )


def test_session_id_stays_plain_while_the_toolkit_url_embeds_it() -> None:
    safe_overrides, secret_env = codex_runtime._externalize_mcp_secrets(
        _session(), _builder_style_overrides()
    )
    serialized = "\n".join(safe_overrides)

    # The real secret is externalized and gone from argv.
    assert "internal-secret-token" not in serialized
    assert "internal-secret-token" in secret_env.values()
    # The session id is an identifier: plain header line retained, never a
    # probe value — so the toolkit URL's legal embedding cannot trip the guard.
    assert _SESSION_ID not in secret_env.values()
    assert (
        f"mcp_servers.valuz_docs.http_headers."
        f"{codex_runtime._toml_key('X-Valuz-Session-Id')}" in serialized
    )
    assert f"/mcp/toolkit/{_SESSION_ID}" in serialized

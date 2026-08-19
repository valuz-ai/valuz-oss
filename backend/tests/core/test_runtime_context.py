"""Opaque runtime-context materialization."""

# ruff: noqa: I001 — kernel bootstrap side effect must precede src imports
from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401

from src.adapters.sqlalchemy_store.converters import model_provider_to_dict
from src.core.runtime_context import materialize_runtime_context, runtime_context_marker
from src.core.types import McpHttpServerConfig, ModelProvider, Session


_KEY = "example.runtime"
_MARKER = runtime_context_marker(_KEY)


def _session(runtime_provider: str = "claude_agent") -> Session:
    return Session(
        id="session-1",
        agent_config=object(),  # type: ignore[arg-type]
        cwd="/tmp/runtime-context-test",
        runtime_provider=runtime_provider,  # type: ignore[arg-type]
        model="managed-model",
        model_provider=ModelProvider(
            api_key=_MARKER,
            base_url="https://gateway.test",
            api_protocol="anthropic" if runtime_provider == "claude_agent" else "openai_response",
        ),
        mcp_servers=(
            McpHttpServerConfig(
                name="builtin",
                url="https://host.test/mcp",
                headers={"X-Internal": _MARKER, "X-Session": "session-1"},
            ),
            McpHttpServerConfig(
                name="external",
                url="https://external.test/mcp",
                headers={"Authorization": "Bearer external-user-token"},
            ),
        ),
    )


@pytest.mark.parametrize(
    "runtime_provider",
    ["claude_agent", "codex", "deepagents", "deepseek_harness"],
)
def test_materializes_opaque_context_for_every_runtime(runtime_provider: str) -> None:
    persisted = _session(runtime_provider)
    materialized = materialize_runtime_context(persisted, {_KEY: "opaque-value"})

    assert materialized.model_provider is not None
    assert materialized.model_provider.api_key == "opaque-value"
    assert materialized.mcp_servers[0].headers["X-Internal"] == "opaque-value"
    assert materialized.mcp_servers[1].headers == {"Authorization": "Bearer external-user-token"}


def test_persisted_session_never_receives_runtime_context_values() -> None:
    persisted = _session()
    materialized = materialize_runtime_context(persisted, {_KEY: "private-value"})

    assert persisted.model_provider is not None
    assert persisted.model_provider.api_key == _MARKER
    assert persisted.mcp_servers[0].headers["X-Internal"] == _MARKER
    assert model_provider_to_dict(persisted.model_provider)["api_key"] == _MARKER
    assert "private-value" not in repr(persisted)
    assert "private-value" in repr(materialized)


def test_missing_context_marker_fails_closed() -> None:
    with pytest.raises(ValueError, match="missing marker"):
        materialize_runtime_context(_session(), None)


def test_invalid_marker_namespace_is_rejected() -> None:
    with pytest.raises(ValueError, match="namespace"):
        runtime_context_marker("_private")

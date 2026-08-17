"""Codex subprocess secrets must never be serialized into process argv."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import McpHttpServerConfig, McpStdioServerConfig, Session
from src.runtimes.codex import runtime as codex_runtime


class _Sink:
    async def emit(self, event: Event) -> None:
        del event


def _session(*, local_env: dict[str, str] | None = None) -> Session:
    return Session(
        id="secret-session",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="codex",
        mcp_servers=(
            McpHttpServerConfig(
                name="remote",
                url="https://mcp.example.com/mcp",
                headers={"Authorization": "Bearer remote-secret"},
            ),
            McpStdioServerConfig(
                name="local",
                command="local-mcp",
                env=local_env or {"LOCAL_TOKEN": "stdio-secret"},
            ),
        ),
    )


def _runtime() -> codex_runtime.CodexRuntime:
    runtime = codex_runtime.CodexRuntime(
        config=AgentConfig(id="a", name="a"),
        model="gpt-5.5",
        event_sink=_Sink(),
        workspace_root="/tmp",
    )
    runtime._install_approval_handler = lambda: None  # type: ignore[method-assign]
    return runtime


def test_secret_values_use_environment_references_not_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeCodex:
        def __init__(self, *, config: Any) -> None:
            captured["config"] = config

        async def __aenter__(self) -> _FakeCodex:
            return self

        async def close(self) -> None:
            return None

    monkeypatch.setattr(codex_runtime, "_resolve_codex_bin", lambda: "/safe/codex")
    monkeypatch.setattr(codex_runtime, "AsyncCodex", _FakeCodex)

    asyncio.run(_runtime()._ensure_codex(_session()))

    config = captured["config"]
    argv = [config.codex_bin]
    for value in config.config_overrides:
        argv.extend(["--config", value])
    argv.extend(["app-server", "--listen", "stdio://"])
    serialized_argv = " ".join(argv)

    assert config.launch_args_override is None
    assert "--profile" not in argv
    assert "remote-secret" not in serialized_argv
    assert "stdio-secret" not in serialized_argv
    assert "mcp_servers.remote.http_headers.Authorization" not in serialized_argv
    assert "env.LOCAL_TOKEN" not in serialized_argv
    assert "env_http_headers.Authorization" in serialized_argv
    assert 'mcp_servers.local.env_vars=["LOCAL_TOKEN"]' in config.config_overrides
    assert "shell_environment_policy.ignore_default_excludes=false" in config.config_overrides
    assert 'shell_environment_policy.inherit="core"' in config.config_overrides

    secret_env = {
        key: value
        for key, value in config.env.items()
        if key.startswith(codex_runtime._CODEX_MCP_SECRET_ENV_PREFIX) or key == "LOCAL_TOKEN"
    }
    assert set(secret_env.values()) == {"Bearer remote-secret", "stdio-secret"}
    assert all(name not in serialized_argv for name in secret_env.values())
    for env_name in secret_env:
        assert (
            f'shell_environment_policy.filters.{env_name}="exclude"'
            in config.config_overrides
        )


def test_conflicting_stdio_secret_names_fail_closed() -> None:
    session = Session(
        id="conflict",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="codex",
        mcp_servers=(
            McpStdioServerConfig(name="one", command="one", env={"TOKEN": "first"}),
            McpStdioServerConfig(name="two", command="two", env={"TOKEN": "second"}),
        ),
    )
    overrides = codex_runtime._build_config_overrides(session, None, "gpt-5.5")

    with pytest.raises(RuntimeError, match="conflicting values"):
        codex_runtime._externalize_mcp_secrets(session, overrides)


@pytest.mark.parametrize("name", ["PATH", "CODEX_HOME", "HTTPS_PROXY"])
def test_runtime_control_environment_names_fail_closed(name: str) -> None:
    session = _session(local_env={name: "unsafe"})
    overrides = codex_runtime._build_config_overrides(session, None, "gpt-5.5")

    with pytest.raises(RuntimeError, match="cannot securely externalize"):
        codex_runtime._externalize_mcp_secrets(session, overrides)


@pytest.mark.parametrize("base_url", [None, "https://gateway.example.com/v1"])
def test_custom_provider_secrets_force_core_shell_inheritance(base_url: str | None) -> None:
    provider = codex_runtime.ModelProvider(
        api_protocol="openai_response",
        api_key="provider-secret",
        base_url=base_url,
    )

    overrides = codex_runtime._build_config_overrides(
        _session(local_env={}),
        provider,
        "gpt-5.5",
    )

    assert "shell_environment_policy.ignore_default_excludes=false" in overrides
    assert 'shell_environment_policy.inherit="core"' in overrides
    expected_env_key = (
        codex_runtime._HARNESS_PROVIDER_ENV_KEY
        if base_url is not None
        else codex_runtime._CODEX_OPENAI_API_KEY
    )
    assert f'shell_environment_policy.filters.{expected_env_key}="exclude"' in overrides

"""Codex subprocess secrets must never be serialized into process argv."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
import stat
from pathlib import Path
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


def _session() -> Session:
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
                env={"LOCAL_TOKEN": "stdio-secret"},
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


def test_secret_overrides_use_private_ephemeral_profile_not_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeCodex:
        def __init__(self, *, config: Any) -> None:
            captured["config"] = config

        async def __aenter__(self) -> _FakeCodex:
            config = captured["config"]
            profile_name = config.launch_args_override[2]
            profile_path = tmp_path / f"{profile_name}.config.toml"
            captured["profile_path"] = profile_path
            captured["profile_text"] = profile_path.read_text(encoding="utf-8")
            captured["profile_mode"] = stat.S_IMODE(profile_path.stat().st_mode)
            return self

        async def close(self) -> None:
            return None

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(codex_runtime, "_resolve_codex_bin", lambda: "/safe/codex")
    monkeypatch.setattr(codex_runtime, "AsyncCodex", _FakeCodex)

    asyncio.run(_runtime()._ensure_codex(_session()))

    config = captured["config"]
    argv = tuple(config.launch_args_override)
    assert argv[0] == "/safe/codex"
    assert argv[1] == "--profile"
    assert argv[3:] == ("app-server", "--listen", "stdio://")
    assert config.config_overrides == ()
    assert "remote-secret" not in " ".join(argv)
    assert "stdio-secret" not in " ".join(argv)
    assert 'mcp_servers.remote.http_headers.Authorization="Bearer remote-secret"' in captured[
        "profile_text"
    ]
    assert 'mcp_servers.local.env.LOCAL_TOKEN="stdio-secret"' in captured["profile_text"]
    assert captured["profile_mode"] == 0o600
    assert not captured["profile_path"].exists()


def test_secret_profile_is_removed_when_codex_startup_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    class _FailingCodex:
        def __init__(self, *, config: Any) -> None:
            captured["config"] = config

        async def __aenter__(self) -> _FailingCodex:
            raise RuntimeError("startup failed")

        async def close(self) -> None:
            return None

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(codex_runtime, "_resolve_codex_bin", lambda: "/safe/codex")
    monkeypatch.setattr(codex_runtime, "AsyncCodex", _FailingCodex)

    with pytest.raises(RuntimeError, match="startup failed"):
        asyncio.run(_runtime()._ensure_codex(_session()))

    profile_name = captured["config"].launch_args_override[2]
    assert not (tmp_path / f"{profile_name}.config.toml").exists()

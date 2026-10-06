"""B5: Valuz's Codex sessions stay out of the user's own ``~/.codex``.

Env-credential sessions (the ``harness`` provider) run in the Valuz-owned
CODEX_HOME; the ChatGPT subscription keeps the user's home for ``auth.json``
with the user's plugins and MCP servers switched off; an existing thread stays
where its rollout is.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.types import McpHttpServerConfig, ModelProvider, Session
from src.runtimes.codex import runtime as codex_runtime

GATEWAY = ModelProvider(api_key="sk-gateway", base_url="https://gateway.example/v1")
FIRST_PARTY = ModelProvider(api_key="sk-openai")
THREAD = "0199a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"


def _rollout(home: Path, thread_id: str = THREAD) -> None:
    day = home / "sessions" / "2026" / "10" / "06"
    day.mkdir(parents=True, exist_ok=True)
    (day / f"rollout-2026-10-06T10-00-00-{thread_id}.jsonl").write_text("{}\n")


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    user, valuz = tmp_path / "user-codex", tmp_path / "valuz-codex"
    user.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(user))
    monkeypatch.setenv(codex_runtime.VALUZ_CODEX_HOME_ENV, str(valuz))
    return user, valuz


def _select(
    provider: ModelProvider | None, *, egress: str | None = None, thread: str | None = None
):
    return codex_runtime._select_codex_home(provider, egress_base_url=egress, thread_id=thread)


def test_env_credential_sessions_run_in_the_valuz_home(homes: tuple[Path, Path]) -> None:
    _, valuz = homes
    assert _select(GATEWAY) == valuz
    assert _select(FIRST_PARTY, egress="http://127.0.0.1:9") == valuz


def test_auth_json_sessions_keep_the_user_home(homes: tuple[Path, Path]) -> None:
    assert _select(None) is None  # ChatGPT subscription
    assert _select(None, egress="http://127.0.0.1:9") is None
    assert _select(FIRST_PARTY) is None  # built-in openai provider


def test_unset_valuz_home_keeps_codex_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(codex_runtime.VALUZ_CODEX_HOME_ENV, raising=False)
    assert _select(GATEWAY) is None


def test_a_thread_stays_where_its_rollout_is(homes: tuple[Path, Path]) -> None:
    user, valuz = homes
    _rollout(user)
    assert _select(GATEWAY, thread=THREAD) is None  # started before the Valuz home
    _rollout(valuz)
    assert _select(GATEWAY, thread=THREAD) == valuz
    assert _select(GATEWAY, thread="unknown-thread") == valuz


def test_user_home_trim_disables_plugins_and_the_users_mcp_servers(
    homes: tuple[Path, Path],
) -> None:
    user, _ = homes
    (user / "config.toml").write_text(
        '[mcp_servers.node_repl]\ncommand = "node"\n'
        '[mcp_servers."my server"]\nurl = "https://x.example/mcp"\n'
        '[mcp_servers.valuz-search]\nurl = "https://y.example/mcp"\n'
    )
    overrides = codex_runtime._user_home_isolation_overrides({"valuz-search"})
    assert overrides == (
        "features.plugins=false",
        'mcp_servers."my server".enabled=false',
        "mcp_servers.node_repl.enabled=false",
    )


def test_user_home_trim_without_a_readable_config(homes: tuple[Path, Path]) -> None:
    user, _ = homes
    assert codex_runtime._user_home_isolation_overrides(set()) == ("features.plugins=false",)
    (user / "config.toml").write_text("not = [valid")
    assert codex_runtime._user_home_isolation_overrides(set()) == ("features.plugins=false",)


class _Sink:
    async def emit(self, event: Event) -> None:
        del event


def _launch(
    monkeypatch: pytest.MonkeyPatch,
    provider: ModelProvider | None,
    *,
    thread_id: str | None = None,
) -> Any:
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
    runtime = codex_runtime.CodexRuntime(
        config=AgentConfig(id="a", name="a"),
        model="gpt-5.5",
        event_sink=_Sink(),
        workspace_root="/tmp",
        model_provider=provider,
    )
    runtime._install_approval_handler = lambda: None  # type: ignore[method-assign]
    session = Session(
        id="home-session",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="codex",
        mcp_servers=(McpHttpServerConfig(name="valuz-search", url="https://y.example/mcp"),),
    )
    asyncio.run(runtime._ensure_codex(session, thread_id=thread_id))
    return captured["config"]


def test_gateway_session_app_server_gets_the_valuz_home(
    homes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, valuz = homes
    config = _launch(monkeypatch, GATEWAY)
    assert config.env["CODEX_HOME"] == str(valuz)
    assert valuz.is_dir()
    assert "features.plugins=false" not in config.config_overrides


def test_subscription_app_server_keeps_the_user_home_trimmed(
    homes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _ = homes
    (user / "config.toml").write_text(
        '[mcp_servers.cua_repl]\ncommand = "cua"\n[mcp_servers.valuz-search]\nurl = "u"\n'
    )
    config = _launch(monkeypatch, None)
    assert config.env is None or config.env.get("CODEX_HOME") in (None, str(user))
    assert "features.plugins=false" in config.config_overrides
    assert "mcp_servers.cua_repl.enabled=false" in config.config_overrides
    assert "mcp_servers.valuz-search.enabled=false" not in config.config_overrides


def test_a_fork_runs_where_the_source_thread_lives(
    homes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    user, _ = homes
    _rollout(user)
    config = _launch(monkeypatch, GATEWAY, thread_id=THREAD)
    assert config.env.get("CODEX_HOME") in (None, str(user))
    assert "features.plugins=false" in config.config_overrides

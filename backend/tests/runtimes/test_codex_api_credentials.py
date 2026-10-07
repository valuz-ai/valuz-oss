"""API-key sessions must not fall back to the user's subscription identity."""

# ruff: noqa: I001
from __future__ import annotations

from pathlib import Path

import pytest
import valuz_agent.boot.kernel  # noqa: F401
from src.core.agent_config import AgentConfig
from src.core.types import ModelProvider, Session
from src.runtimes.codex import runtime


def test_direct_api_key_uses_explicit_provider_and_secret_env() -> None:
    provider = ModelProvider(api_key="dummy-session-key", api_protocol="openai_response")
    session = Session(
        id="api-key",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="codex",
    )
    overrides = runtime._build_config_overrides(session, provider, "gpt-5.5")
    assert 'model_provider="harness"' in overrides
    assert 'model_providers.harness.base_url="https://api.openai.com/v1"' in overrides
    assert 'model_providers.harness.env_key="HARNESS_CODEX_PROVIDER_API_KEY"' in overrides
    assert "model_providers.harness.requires_openai_auth=false" in overrides
    assert 'shell_environment_policy.filters.HARNESS_CODEX_PROVIDER_API_KEY="exclude"' in overrides
    assert all("dummy-session-key" not in item for item in overrides)
    assert (
        runtime._build_codex_env(provider)[runtime._HARNESS_PROVIDER_ENV_KEY] == "dummy-session-key"
    )


def test_direct_api_key_runs_in_its_own_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "valuz-codex"
    monkeypatch.setenv(runtime.VALUZ_CODEX_HOME_ENV, str(private))
    assert (
        runtime._select_codex_home(
            ModelProvider(api_key="dummy", api_protocol="openai_response"),
            egress_base_url=None,
            thread_id=None,
        )
        == private
    )
    assert runtime._select_codex_home(None, egress_base_url=None, thread_id=None) is None

"""Tests for valuz_agent.adapters.runtime_registry."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from valuz_agent.adapters.runtime_registry import (
    RUNTIME_REGISTRY,
    get_runtime,
    is_runtime_available,
    list_runtimes,
    supports_protocol,
)


def test_should_register_three_runtimes() -> None:
    ids = {spec.id for spec in list_runtimes()}
    assert ids == {"claude_agent", "codex", "deepagents"}


def test_should_return_claude_agent_display_name() -> None:
    spec = get_runtime("claude_agent")
    assert spec is not None
    assert spec.display_name == "Claude Code"


def test_should_return_codex_display_name() -> None:
    spec = get_runtime("codex")
    assert spec is not None
    assert spec.display_name == "OpenAI Codex"


def test_should_return_deepagents_display_name() -> None:
    spec = get_runtime("deepagents")
    assert spec is not None
    assert spec.display_name == "Deep Agents"


def test_should_return_none_for_unknown_runtime() -> None:
    assert get_runtime("nonexistent") is None


def test_claude_agent_supports_only_anthropic() -> None:
    spec = RUNTIME_REGISTRY["claude_agent"]
    assert spec.supported_protocols == ("anthropic",)


def test_codex_supports_only_openai_response() -> None:
    """Kernel V5+bba3014: ``codex`` runtime's allowlist is exactly
    ``{"openai-response"}`` — the Responses API is what its SDK speaks."""
    spec = RUNTIME_REGISTRY["codex"]
    assert spec.supported_protocols == ("openai-response",)


def test_deepagents_supports_three_protocols() -> None:
    """Kernel V5+bba3014: ``deepagents`` allows anthropic (langchain
    ``ChatAnthropic``), openai-completion (``ChatOpenAI``), and gemini
    (``ChatGoogleGenerativeAI``). The user-facing hyphen form is what
    the registry surfaces."""
    spec = RUNTIME_REGISTRY["deepagents"]
    assert set(spec.supported_protocols) == {
        "anthropic",
        "openai-completion",
        "gemini",
    }


def test_supports_protocol_returns_true_when_compatible() -> None:
    assert supports_protocol("claude_agent", "anthropic") is True


def test_supports_protocol_returns_false_when_incompatible() -> None:
    """Kernel V5+bba3014: ``claude_agent`` only speaks anthropic. The
    4-value enum still rejects ``openai-completion`` /
    ``openai-response`` / ``gemini``."""
    assert supports_protocol("claude_agent", "openai-completion") is False


def test_supports_protocol_returns_false_for_unknown_runtime() -> None:
    assert supports_protocol("unknown", "openai") is False


def test_claude_agent_is_always_available() -> None:
    available, reason = is_runtime_available("claude_agent")
    assert available is True
    assert reason is None


def test_deepagents_is_always_available() -> None:
    available, reason = is_runtime_available("deepagents")
    assert available is True
    assert reason is None


def test_unknown_runtime_is_unavailable_with_reason() -> None:
    available, reason = is_runtime_available("ghost")
    assert available is False
    assert reason is not None
    assert "ghost" in reason


def test_codex_is_available_when_binary_on_path() -> None:
    fake_path = "/usr/local/bin/codex"
    with patch("valuz_agent.adapters.runtime_registry.shutil.which", return_value=fake_path):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("CODEX_BIN_OVERRIDE", None)
            available, reason = is_runtime_available("codex")
    assert available is True
    assert reason is None


def test_codex_is_unavailable_when_binary_missing() -> None:
    with patch("valuz_agent.adapters.runtime_registry.shutil.which", return_value=None):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("CODEX_BIN_OVERRIDE", None)
            available, reason = is_runtime_available("codex")
    assert available is False
    assert reason is not None
    assert "codex" in reason


def test_codex_uses_env_override_when_path_is_executable(tmp_path) -> None:
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\nexit 0\n")
    fake_codex.chmod(0o755)

    with patch.dict("os.environ", {"CODEX_BIN_OVERRIDE": str(fake_codex)}):
        # which() should never be called when override resolves; force it to
        # return None so any accidental fallback fails the test.
        with patch("valuz_agent.adapters.runtime_registry.shutil.which", return_value=None):
            available, reason = is_runtime_available("codex")

    assert available is True
    assert reason is None


def test_codex_reports_invalid_env_override() -> None:
    with patch.dict("os.environ", {"CODEX_BIN_OVERRIDE": "/nonexistent/codex"}):
        with patch("valuz_agent.adapters.runtime_registry.shutil.which", return_value=None):
            available, reason = is_runtime_available("codex")

    assert available is False
    assert reason is not None
    assert "CODEX_BIN_OVERRIDE" in reason


@pytest.fixture(autouse=True)
def _clear_codex_env_override():
    """Test isolation: never let CODEX_BIN_OVERRIDE leak between tests."""
    import os

    saved = os.environ.pop("CODEX_BIN_OVERRIDE", None)
    yield
    if saved is not None:
        os.environ["CODEX_BIN_OVERRIDE"] = saved

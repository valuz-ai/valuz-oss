"""``CLAUDE_CODE_MAX_CONTEXT_TOKENS`` from the channel-declared window.

For a gateway / custom model id the Claude CLI can't resolve to a Claude
model it assumes a generic 200k window, so its auto-compaction would fire
against the wrong size. ``_build_model_provider_env`` exports the declared
``max_input_tokens`` as ``CLAUDE_CODE_MAX_CONTEXT_TOKENS`` (the Claude analog
of codex's ``model_context_window``), only when declared; the compaction
threshold inside that window is the CLI's own.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

# Side-effect import: puts the kernel ``src/`` on sys.path at module load,
# before any ``from src.*`` below resolves.
import kernel  # noqa: F401

_KEY = "CLAUDE_CODE_MAX_CONTEXT_TOKENS"


def _env_for(
    *,
    max_input_tokens: int | None,
    has_provider: bool = True,
    model: str = "valuz-pro-anthropic",
    egress: bool = False,
    model_settings_present: bool = True,
) -> dict[str, str] | None:
    from src.core.types import ModelProvider, ModelSettings
    from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime

    rt = object.__new__(ClaudeAgentRuntime)
    rt.model_provider = (
        ModelProvider(api_key="sk-x", base_url="https://gw.example/v1") if has_provider else None
    )
    rt.model = model
    if model_settings_present:
        rt.model_settings = ModelSettings(max_input_tokens=max_input_tokens)
    if egress:
        rt.egress_descriptor = SimpleNamespace(base_url="http://127.0.0.1:43123/client/v1")
        rt._egress_enabled_for_spawn = True
    return rt._build_model_provider_env()


def test_declared_window_is_exported_as_plain_token_count() -> None:
    env = _env_for(max_input_tokens=1_000_000)
    assert env is not None
    # The CLI reads a plain integer only (no ``k`` / ``M`` suffix forms).
    assert env[_KEY] == "1000000"


def test_no_declaration_adds_nothing() -> None:
    with patch.dict("os.environ", {}, clear=True):
        env = _env_for(max_input_tokens=None)
    assert env is not None
    assert _KEY not in env


def test_no_declaration_leaves_an_ambient_value_alone() -> None:
    """Never guessed, never wiped: an operator's own export for the CLI is
    inherited verbatim when the channel declares nothing."""
    with patch.dict("os.environ", {_KEY: "128000"}, clear=False):
        env = _env_for(max_input_tokens=None)
    assert env is not None
    assert env[_KEY] == "128000"


def test_declaration_overrides_an_ambient_value() -> None:
    with patch.dict("os.environ", {_KEY: "128000"}, clear=False):
        env = _env_for(max_input_tokens=400_000)
    assert env is not None
    assert env[_KEY] == "400000"


def test_runtime_without_model_settings_attribute_is_tolerated() -> None:
    env = _env_for(max_input_tokens=None, model_settings_present=False)
    assert env is not None
    assert _KEY not in env


def test_egress_subscription_path_also_exports_the_declaration() -> None:
    """The subscription-through-egress path returns an env dict without an
    API key; the declared window still rides along."""
    env = _env_for(max_input_tokens=200_000, has_provider=False, egress=True)
    assert env is not None
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env[_KEY] == "200000"


def test_first_party_subscription_without_egress_stays_ambient() -> None:
    """No provider and no egress → ``options.env`` stays unset (the SDK
    inherits the parent env verbatim), exactly as before."""
    assert _env_for(max_input_tokens=200_000, has_provider=False) is None


def test_claude_model_id_still_carries_the_declaration() -> None:
    """For an id the CLI resolves to a Claude model the variable is a
    documented no-op (unless ``DISABLE_COMPACT`` is set), so exporting the
    declaration is harmless there and keeps the rule "set iff declared"."""
    env = _env_for(max_input_tokens=200_000, model="claude-sonnet-4-6")
    assert env is not None
    assert env[_KEY] == "200000"

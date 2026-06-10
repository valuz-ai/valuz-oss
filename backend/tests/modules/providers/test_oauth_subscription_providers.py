"""Tests for the OAuth subscription providers added to BUILTIN_PROVIDERS.

These tests verify shape only — they don't seed any providers. Provider
seeding for subscription providers happens lazily when the user runs
``claude /login`` / ``codex /login`` and the host detects the keychain
entry.
"""

from __future__ import annotations

from valuz_agent.modules.providers.service import (
    BUILTIN_PROVIDERS,
    ProviderDescriptor,
    get_provider,
)


def _by_kind(kind: str) -> ProviderDescriptor | None:
    for provider in BUILTIN_PROVIDERS:
        if provider.kind == kind:
            return provider
    return None


def test_should_register_claude_subscription_provider() -> None:
    provider = _by_kind("claude-subscription")
    assert provider is not None
    assert provider.display_name == "Claude Pro / Max"


def test_should_register_codex_subscription_provider() -> None:
    provider = _by_kind("codex-subscription")
    assert provider is not None
    assert provider.display_name == "Codex · ChatGPT"


def test_claude_subscription_uses_oauth_auth_type() -> None:
    provider = get_provider("claude-subscription")
    assert provider.auth_type == "oauth"


def test_codex_subscription_uses_oauth_auth_type() -> None:
    provider = get_provider("codex-subscription")
    assert provider.auth_type == "oauth"


def test_claude_subscription_pins_claude_agent_runtime() -> None:
    provider = get_provider("claude-subscription")
    assert provider.runtime_provider == "claude_agent"


def test_codex_subscription_pins_codex_runtime() -> None:
    provider = get_provider("codex-subscription")
    assert provider.runtime_provider == "codex"


def test_claude_subscription_default_model_is_sonnet() -> None:
    provider = get_provider("claude-subscription")
    assert provider.default_model == "claude-sonnet-4-6"


def test_codex_subscription_default_model_is_gpt_5_5() -> None:
    """Hydrated from resources/subscription_models.json — gpt-5.5 is the
    flagship as of April 2026 (subscription-only, not API-key reachable)."""
    provider = get_provider("codex-subscription")
    assert provider.default_model == "gpt-5.5"


def test_claude_subscription_recommends_pinned_anthropic_models() -> None:
    """Pinned IDs from https://code.claude.com/docs/en/model-config — version
    aliases (sonnet/opus/haiku) intentionally NOT in the list per product
    decision (具体版本号 over alias). Sourced from
    resources/subscription_models.json."""
    provider = get_provider("claude-subscription")
    assert set(provider.model_options) == {
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    }


def test_codex_subscription_recommends_known_codex_models() -> None:
    """Per https://developers.openai.com/codex/models. Includes the Pro-only
    ``gpt-5.3-codex-spark`` preview — listing it lets Pro users pick it;
    lower tiers will fail at SDK call time. Sourced from
    resources/subscription_models.json."""
    provider = get_provider("codex-subscription")
    assert set(provider.model_options) == {
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-codex",
        "gpt-5.3-codex-spark",
        "gpt-5.2",
    }


def test_claude_subscription_carries_login_command() -> None:
    provider = get_provider("claude-subscription")
    assert provider.oauth_login_command == "claude /login"


def test_codex_subscription_carries_login_command() -> None:
    provider = get_provider("codex-subscription")
    assert provider.oauth_login_command == "codex /login"


def test_oauth_subscription_skips_connection_test() -> None:
    """Connection-test path needs an api_key — OAuth providers don't have one."""
    for kind in ("claude-subscription", "codex-subscription"):
        provider = get_provider(kind)
        assert provider.supports_connection_test is False


def test_oauth_subscription_declares_default_protocol() -> None:
    assert get_provider("claude-subscription").default_protocol == "anthropic"
    assert get_provider("codex-subscription").default_protocol == "openai"


def test_existing_api_key_providers_still_have_default_auth_type() -> None:
    """Adding new fields with defaults must not change existing provider shape."""
    for kind in ("anthropic", "openai", "deepseek", "compatible"):
        provider = get_provider(kind)
        assert provider.auth_type == "api_key"
        assert provider.runtime_provider == ""
        assert provider.oauth_login_command == ""
        assert provider.default_protocol == ""

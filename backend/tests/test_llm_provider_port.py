"""Tests for the ``LLMProvider`` port and registry."""

from __future__ import annotations

import pytest

from valuz_agent.ports.llm_provider import (
    SystemLLMProvider,
    SystemProviderImmutable,
    _InMemoryRegistry,
    get_llm_registry,
    set_llm_registry,
)


def _make_descriptor(provider_id: str = "test-channel", **overrides: object) -> SystemLLMProvider:
    kwargs: dict[str, object] = {
        "id": provider_id,
        "name": "Test Channel",
        "provider_kind": "system",
        "runtime_provider": "claude_agent",
        "api_protocol": "anthropic",
        "api_base": "https://example.test/v1",
        "model_options": ("claude-sonnet-4-6",),
        "default_model": "claude-sonnet-4-6",
        "headers": lambda: {"Authorization": "Bearer token-abc"},
        "enabled": lambda: True,
    }
    kwargs.update(overrides)
    return SystemLLMProvider(**kwargs)  # type: ignore[arg-type]


class TestSystemLLMProvider:
    def test_descriptor_carries_static_fields(self) -> None:
        d = _make_descriptor()
        assert d.id == "test-channel"
        assert d.runtime_provider == "claude_agent"
        assert d.api_protocol == "anthropic"
        assert d.api_base == "https://example.test/v1"
        assert d.model_options == ("claude-sonnet-4-6",)

    def test_headers_callable_invoked_each_call(self) -> None:
        counter = {"n": 0}

        def headers() -> dict[str, str]:
            counter["n"] += 1
            return {"Authorization": f"Bearer call-{counter['n']}"}

        d = _make_descriptor(headers=headers)
        assert d.headers()["Authorization"] == "Bearer call-1"
        assert d.headers()["Authorization"] == "Bearer call-2"

    def test_enabled_and_unavailable_reason_defaults(self) -> None:
        d = SystemLLMProvider(
            id="x",
            name="x",
            provider_kind="system",
            runtime_provider="claude_agent",
            api_protocol="anthropic",
            api_base="https://x/v1",
        )
        assert d.enabled() is True
        assert d.unavailable_reason() is None
        assert d.headers() == {}


class TestInMemoryRegistry:
    def setup_method(self) -> None:
        # ensure a clean module-level registry between tests
        set_llm_registry(_InMemoryRegistry())

    def teardown_method(self) -> None:
        set_llm_registry(_InMemoryRegistry())

    def test_register_and_get(self) -> None:
        reg = get_llm_registry()
        d = _make_descriptor()
        reg.register(d)
        assert reg.get("test-channel") is d
        assert list(reg.all()) == [d]

    def test_get_missing_returns_none(self) -> None:
        assert get_llm_registry().get("nope") is None

    def test_duplicate_register_raises(self) -> None:
        reg = get_llm_registry()
        reg.register(_make_descriptor())
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_make_descriptor())

    def test_unregister_idempotent(self) -> None:
        reg = get_llm_registry()
        reg.register(_make_descriptor())
        reg.unregister("test-channel")
        reg.unregister("test-channel")  # no error
        assert reg.get("test-channel") is None

    def test_clear_empties_registry(self) -> None:
        reg = get_llm_registry()
        reg.register(_make_descriptor("a"))
        reg.register(_make_descriptor("b"))
        reg.clear()
        assert list(reg.all()) == []


class TestSystemProviderImmutable:
    def test_carries_provider_id(self) -> None:
        err = SystemProviderImmutable("valuz-channel")
        assert err.provider_id == "valuz-channel"
        assert "valuz-channel" in str(err)

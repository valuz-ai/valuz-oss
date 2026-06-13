"""Tests for valuz_agent.adapters.model_resolver."""

from __future__ import annotations

import json
from dataclasses import dataclass

from valuz_agent.adapters.model_resolver import DEFAULT_MODEL, resolve_model


@dataclass
class _FakeProvider:
    id: str
    default_model: str | None = None
    model_ids: str | None = None
    is_default: bool = False


class _FakeProviderDatastore:
    def __init__(self, providers: list[_FakeProvider] | None = None) -> None:
        self._by_id = {c.id: c for c in (providers or [])}

    async def get_by_id(self, user_id: str, provider_id: str) -> _FakeProvider | None:
        return self._by_id.get(provider_id)

    async def get_default(self, user_id: str) -> _FakeProvider | None:
        for c in self._by_id.values():
            if c.is_default:
                return c
        return None


async def test_should_pick_request_model_id_when_provided() -> None:
    res = await resolve_model(
        providers=_FakeProviderDatastore(),
        request_model_id="gpt-4o",
    )
    assert res.model == "gpt-4o"
    assert res.source == "request"


async def test_should_carry_runtime_hint_through_to_resolution() -> None:
    res = await resolve_model(
        providers=_FakeProviderDatastore(),
        request_model_id="claude-sonnet-4-6",
        request_runtime_id="claude_agent",
    )
    assert res.runtime_hint == "claude_agent"


async def test_should_default_runtime_hint_to_none_for_legacy_callers() -> None:
    res = await resolve_model(
        providers=_FakeProviderDatastore(),
        request_model_id="gpt-4o",
    )
    assert res.runtime_hint is None


async def test_should_flag_custom_model_when_not_in_provider_options() -> None:
    provider = _FakeProvider(
        id="ch-anthropic",
        default_model="claude-sonnet-4-6",
        model_ids=json.dumps(["claude-sonnet-4-6", "claude-opus-4-7"]),
    )
    res = await resolve_model(
        providers=_FakeProviderDatastore([provider]),
        request_model_id="claude-sonnet-some-future-snapshot",
        request_provider_id="ch-anthropic",
    )
    assert res.custom_model_id is True


async def test_should_not_flag_custom_when_model_is_in_provider_options() -> None:
    provider = _FakeProvider(
        id="ch-anthropic",
        default_model="claude-sonnet-4-6",
        model_ids=json.dumps(["claude-sonnet-4-6", "claude-opus-4-7"]),
    )
    res = await resolve_model(
        providers=_FakeProviderDatastore([provider]),
        request_model_id="claude-opus-4-7",
        request_provider_id="ch-anthropic",
    )
    assert res.custom_model_id is False


async def test_should_not_flag_custom_when_provider_has_empty_options() -> None:
    """No options stored = provider hasn't been populated yet; trust user."""
    provider = _FakeProvider(id="ch-x", default_model=None, model_ids=None)
    res = await resolve_model(
        providers=_FakeProviderDatastore([provider]),
        request_model_id="anything",
        request_provider_id="ch-x",
    )
    assert res.custom_model_id is False


async def test_should_tolerate_malformed_model_ids() -> None:
    provider = _FakeProvider(id="ch-x", model_ids="not json")
    res = await resolve_model(
        providers=_FakeProviderDatastore([provider]),
        request_model_id="anything",
        request_provider_id="ch-x",
    )
    assert res.custom_model_id is False


async def test_should_use_provider_default_when_no_request_model() -> None:
    provider = _FakeProvider(id="ch-anthropic", default_model="claude-sonnet-4-6")
    res = await resolve_model(
        providers=_FakeProviderDatastore([provider]),
        request_provider_id="ch-anthropic",
    )
    assert res.model == "claude-sonnet-4-6"
    assert res.source == "provider"


async def test_should_use_project_default_when_no_provider_default() -> None:
    provider = _FakeProvider(id="ch-x", default_model=None)
    res = await resolve_model(
        providers=_FakeProviderDatastore([provider]),
        request_provider_id="ch-x",
        project_default_model_id="project-pinned-model",
    )
    assert res.model == "project-pinned-model"
    assert res.source == "project"


async def test_should_fall_back_to_default_model_when_nothing_specified() -> None:
    res = await resolve_model(providers=_FakeProviderDatastore())
    assert res.model == DEFAULT_MODEL
    assert res.source == "fallback"


async def test_should_fall_back_to_default_provider_default_model_before_global() -> None:
    """REP-107: when nothing else applies, the user's Settings -> 默认提供商
    pick should drive the resolved model — not the hardcoded global
    fallback. Skill creator / scheduler entry points hit this path."""
    default_provider = _FakeProvider(
        id="ch-claude-subscription",
        default_model="claude-opus-4-7",
        is_default=True,
    )
    other = _FakeProvider(id="ch-other", default_model="something-else")
    res = await resolve_model(providers=_FakeProviderDatastore([default_provider, other]))
    assert res.model == "claude-opus-4-7"
    assert res.source == "provider"


async def test_should_skip_default_provider_fallback_when_no_default_model() -> None:
    """The default provider might exist but have no default_model (Reportify
    after the lite/pro cleanup). Don't crash — drop through to the global
    fallback."""
    default_provider = _FakeProvider(
        id="ch-reportify",
        default_model=None,
        is_default=True,
    )
    res = await resolve_model(providers=_FakeProviderDatastore([default_provider]))
    assert res.model == DEFAULT_MODEL
    assert res.source == "fallback"

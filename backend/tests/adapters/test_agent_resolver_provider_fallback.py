"""``_resolve_agent_provider`` chat-parity fallback behavior.

The task path used to resolve ONLY the agent's pinned ``metadata.provider_id``;
an unpinned agent (pack-imported / source-instantiated — provider ids are
install-local and never travel) worked in chat, where the session service falls
back to any enabled provider hosting the model, but failed the dispatch
pre-flight ("no model provider configured"). The resolver now mirrors that
fallback: no pin → model-hosted lookup; a pin that fails to resolve → one
fallback attempt at a different model-hosted provider.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import logging

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect

import pytest

from src.core import AgentConfig

from valuz_agent.adapters import agent_resolver
from valuz_agent.adapters.agent_resolver import (
    _model_hosted_provider_id,
    _resolve_agent_provider,
)


def _agent(provider_id: str | None = None) -> AgentConfig:
    return AgentConfig(
        id="agent:writer",
        name="writer",
        runtime_provider="claude_agent",
        model="m-1",
        metadata={"provider_id": provider_id} if provider_id else {},
    )


def _patch_model_hosted(monkeypatch: pytest.MonkeyPatch, provider_id: str | None) -> list[str]:
    """Stub the model-hosted lookup; returns the list of models it was asked for."""
    calls: list[str] = []

    async def _lookup(*, model: str, providers: object, user_id: str) -> str | None:
        calls.append(model)
        return provider_id

    monkeypatch.setattr(agent_resolver, "_model_hosted_provider_id", _lookup)
    return calls


def _patch_resolve(monkeypatch: pytest.MonkeyPatch, impl) -> None:
    from valuz_agent.adapters import provider_resolver

    monkeypatch.setattr(provider_resolver, "resolve_model_provider", impl)


async def test_no_pin_falls_back_to_model_hosted_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    resolved_ids: list[str] = []

    async def _resolve(*, provider_id: str, **_k) -> object:
        resolved_ids.append(provider_id)
        return sentinel

    lookups = _patch_model_hosted(monkeypatch, "ch-1")
    _patch_resolve(monkeypatch, _resolve)

    result = await _resolve_agent_provider(
        agent=_agent(), model="m-1", providers=object(), user_id="u1"
    )
    assert result is sentinel
    assert lookups == ["m-1"]
    assert resolved_ids == ["ch-1"]


async def test_no_pin_and_no_host_returns_none_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_model_hosted(monkeypatch, None)

    async def _resolve(**_k) -> object:  # pragma: no cover — must not be reached
        raise AssertionError("resolve_model_provider must not be called without a provider id")

    _patch_resolve(monkeypatch, _resolve)

    with caplog.at_level(logging.WARNING, logger="valuz_agent.adapters.agent_resolver"):
        result = await _resolve_agent_provider(
            agent=_agent(), model="m-1", providers=object(), user_id="u1"
        )
    assert result is None
    assert any("no enabled provider hosts model" in r.getMessage() for r in caplog.records)


async def test_pin_wins_over_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    resolved_ids: list[str] = []

    async def _resolve(*, provider_id: str, **_k) -> object:
        resolved_ids.append(provider_id)
        return sentinel

    lookups = _patch_model_hosted(monkeypatch, "ch-other")
    _patch_resolve(monkeypatch, _resolve)

    result = await _resolve_agent_provider(
        agent=_agent("pin-1"), model="m-1", providers=object(), user_id="u1"
    )
    assert result is sentinel
    assert resolved_ids == ["pin-1"]
    assert lookups == []  # a healthy pin never consults the fallback


async def test_broken_pin_falls_back_to_model_hosted_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    resolved_ids: list[str] = []

    async def _resolve(*, provider_id: str, **_k) -> object:
        resolved_ids.append(provider_id)
        if provider_id == "pin-1":
            raise RuntimeError("provider row deleted")
        return sentinel

    _patch_model_hosted(monkeypatch, "ch-2")
    _patch_resolve(monkeypatch, _resolve)

    result = await _resolve_agent_provider(
        agent=_agent("pin-1"), model="m-1", providers=object(), user_id="u1"
    )
    assert result is sentinel
    assert resolved_ids == ["pin-1", "ch-2"]


async def test_broken_pin_with_no_alternative_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_ids: list[str] = []

    async def _resolve(*, provider_id: str, **_k) -> object:
        resolved_ids.append(provider_id)
        raise RuntimeError("no credentials")

    # The fallback lookup lands on the SAME broken pin — no second attempt.
    _patch_model_hosted(monkeypatch, "pin-1")
    _patch_resolve(monkeypatch, _resolve)

    result = await _resolve_agent_provider(
        agent=_agent("pin-1"), model="m-1", providers=object(), user_id="u1"
    )
    assert result is None
    assert resolved_ids == ["pin-1"]


async def test_model_hosted_lookup_delegates_to_provider_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from valuz_agent.modules.providers.service import ProviderService

    row = type("Row", (), {"id": "ch-9", "auth_type": "api_key"})()

    async def _match(self, user_id: str, model_id: str):
        assert (user_id, model_id) == ("u1", "m-1")
        return row

    monkeypatch.setattr(ProviderService, "resolve_provider_for_model", _match)
    assert await _model_hosted_provider_id(model="m-1", providers=object(), user_id="u1") == "ch-9"


async def test_model_hosted_lookup_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from valuz_agent.modules.providers.service import ProviderService

    async def _boom(self, user_id: str, model_id: str):
        raise RuntimeError("datastore down")

    monkeypatch.setattr(ProviderService, "resolve_provider_for_model", _boom)
    assert await _model_hosted_provider_id(model="m-1", providers=object(), user_id="u1") is None

"""Tests for system-provider resolution path (ADR-007).

When a provider id is registered in the ``LLMProviderRegistry``, the
resolver should skip the user table lookup and synthesise a kernel
``ModelProvider`` from the descriptor.
"""

from __future__ import annotations

import pytest

# Side-effect import — surfaces ``src.core...`` on sys.path before
# provider_resolver imports ``ModelProvider`` at module load.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters.provider_resolver import (
    ProviderNotResolvable,
    resolve_model_provider,
    resolve_runtime_provider,
)
from valuz_agent.ports.llm_provider import (
    SystemLLMProvider,
    _InMemoryRegistry,
    set_llm_registry,
)


class _NoProviders:
    async def get_by_id(self, _: str):  # type: ignore[no-untyped-def]
        return None



class _UnusedSecrets:
    def get(self, _: str):  # type: ignore[no-untyped-def]
        return None


def _descriptor(
    *,
    provider_id: str = "valuz-channel",
    api_protocol: str = "anthropic",
    api_base: str = "https://cloud.test/v1",
    runtime_provider: str = "claude_agent",
    enabled: bool = True,
    unavailable_reason: str | None = None,
    headers: dict[str, str] | None = None,
) -> SystemLLMProvider:
    return SystemLLMProvider(
        id=provider_id,
        name="Test System Channel",
        provider_kind="system",
        runtime_provider=runtime_provider,
        api_protocol=api_protocol,
        api_base=api_base,
        model_options=("claude-sonnet-4-6",),
        default_model="claude-sonnet-4-6",
        headers=lambda: headers if headers is not None else {"Authorization": "Bearer abc"},
        enabled=lambda: enabled,
        unavailable_reason=lambda: unavailable_reason,
    )


@pytest.fixture(autouse=True)
def fresh_registry():
    set_llm_registry(_InMemoryRegistry())
    yield
    set_llm_registry(_InMemoryRegistry())


class TestResolveModelProviderSystem:
    async def test_descriptor_resolves_with_bearer(self) -> None:
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor())
        mp = await resolve_model_provider(
            provider_id="valuz-channel",
            model_id="claude-sonnet-4-6",
            providers=_NoProviders(),  # type: ignore[arg-type]
            secrets=_UnusedSecrets(),  # type: ignore[arg-type]
        )
        assert mp is not None
        assert mp.base_url == "https://cloud.test/v1"
        assert mp.api_key == "abc"
        assert mp.api_protocol == "anthropic"

    async def test_disabled_descriptor_raises(self) -> None:
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor(enabled=False, unavailable_reason="未登录"))
        with pytest.raises(ProviderNotResolvable, match="未登录"):
            await resolve_model_provider(
                provider_id="valuz-channel",
                model_id="claude-sonnet-4-6",
                providers=_NoProviders(),  # type: ignore[arg-type]
                secrets=_UnusedSecrets(),  # type: ignore[arg-type]
            )

    async def test_missing_bearer_raises(self) -> None:
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor(headers={}))
        with pytest.raises(ProviderNotResolvable, match="no bearer token"):
            await resolve_model_provider(
                provider_id="valuz-channel",
                model_id="claude-sonnet-4-6",
                providers=_NoProviders(),  # type: ignore[arg-type]
                secrets=_UnusedSecrets(),  # type: ignore[arg-type]
            )

    async def test_invalid_api_protocol_raises(self) -> None:
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor(api_protocol="not-a-protocol"))
        with pytest.raises(ProviderNotResolvable, match="unknown api_protocol"):
            await resolve_model_provider(
                provider_id="valuz-channel",
                model_id="m",
                providers=_NoProviders(),  # type: ignore[arg-type]
                secrets=_UnusedSecrets(),  # type: ignore[arg-type]
            )

    async def test_empty_api_base_becomes_none(self) -> None:
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor(api_base=""))
        mp = await resolve_model_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
            secrets=_UnusedSecrets(),  # type: ignore[arg-type]
        )
        assert mp is not None
        assert mp.base_url is None

    async def test_unknown_id_falls_through_to_user_table(self) -> None:
        with pytest.raises(ProviderNotResolvable, match="not found"):
            await resolve_model_provider(
                provider_id="unknown",
                model_id="m",
                providers=_NoProviders(),  # type: ignore[arg-type]
                secrets=_UnusedSecrets(),  # type: ignore[arg-type]
            )


class TestResolveRuntimeProviderSystem:
    async def test_descriptor_runtime_wins_over_user_table(self) -> None:
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor(runtime_provider="deepagents"))
        rt = await resolve_runtime_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
        )
        assert rt == "deepagents"

    async def test_request_runtime_still_overrides_descriptor(self) -> None:
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor(runtime_provider="claude_agent"))
        rt = await resolve_runtime_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
            request_runtime_id="codex",
        )
        assert rt == "codex"

    async def test_descriptor_invalid_runtime_raises(self) -> None:
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor(runtime_provider="weird"))
        with pytest.raises(ProviderNotResolvable, match="unknown runtime"):
            await resolve_runtime_provider(
                provider_id="valuz-channel",
                model_id="m",
                providers=_NoProviders(),  # type: ignore[arg-type]
            )

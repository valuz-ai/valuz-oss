"""Tests for valuz_agent.adapters.provider_resolver.

Covers: 4-value ``api_protocol`` mapping (kernel V5+bba3014), Optional
``base_url`` (None for first-party SDK fallback), explicit runtime
override, OAuth subscription bypass, dual-protocol descriptors.

The Reportify alias overrides (``reportify-lite`` / ``reportify-pro``)
were removed in the bba3014 upgrade — those tests are gone with the
mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

# Side-effect import — surfaces ``src.core...`` on sys.path before
# provider_resolver tries to import ``ModelProvider`` at module load.
import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters.provider_resolver import (
    ProviderNotResolvable,
    resolve_model_provider,
    resolve_runtime_provider,
)


@dataclass
class _FakeProvider:
    id: str
    runtime_provider: str | None = "deepagents"
    name: str = "Test Provider"
    enabled: bool = True
    auth_type: str = "api_key"
    base_url: str | None = None
    credential_source: str = "none"
    secret_ref: str | None = None
    account_provider_id: str | None = None
    protocol: str | None = None
    provider_kind: str = "compatible"


class _FakeProviderDatastore:
    def __init__(self, providers: list[_FakeProvider] | None = None) -> None:
        self._by_id = {c.id: c for c in (providers or [])}

    async def get_by_id(self, provider_id: str) -> _FakeProvider | None:
        return self._by_id.get(provider_id)



class _UnusedSecrets:
    def get(self, _ref: str):  # type: ignore[no-untyped-def]
        return None


async def test_explicit_runtime_overrides_provider_default() -> None:
    provider = _FakeProvider(id="ch-x", runtime_provider="deepagents")
    runtime = await resolve_runtime_provider(
        provider_id="ch-x",
        model_id="any-model",
        providers=_FakeProviderDatastore([provider]),
        request_runtime_id="claude_agent",
    )
    assert runtime == "claude_agent"


async def test_explicit_unknown_runtime_raises() -> None:
    provider = _FakeProvider(id="ch-x")
    with pytest.raises(ProviderNotResolvable) as exc:
        await resolve_runtime_provider(
            provider_id="ch-x",
            model_id="any-model",
            providers=_FakeProviderDatastore([provider]),
            request_runtime_id="invalid-runtime",
        )
    assert "invalid-runtime" in exc.value.reason


async def test_falls_back_to_derived_runtime_when_no_explicit_runtime() -> None:
    """When no explicit runtime applies, derive from provider_kind."""
    provider = _FakeProvider(
        id="ch-anthropic", runtime_provider="claude_agent", provider_kind="anthropic"
    )
    runtime = await resolve_runtime_provider(
        provider_id="ch-anthropic",
        model_id="claude-sonnet-4-6",
        providers=_FakeProviderDatastore([provider]),
    )
    assert runtime == "claude_agent"


async def test_falls_back_to_deepagents_for_deleted_provider() -> None:
    runtime = await resolve_runtime_provider(
        provider_id="ch-deleted",
        model_id="anything",
        providers=_FakeProviderDatastore([]),
    )
    assert runtime == "deepagents"


async def test_falls_back_to_deepagents_for_unknown_provider_kind() -> None:
    provider = _FakeProvider(id="ch-x", runtime_provider="garbage", provider_kind="unknown")
    runtime = await resolve_runtime_provider(
        provider_id="ch-x",
        model_id="anything",
        providers=_FakeProviderDatastore([provider]),
    )
    assert runtime == "deepagents"


async def test_oauth_subscription_provider_returns_none_model_provider() -> None:
    """OAuth-managed providers (claude /login, codex /login) skip the
    ModelProvider construction so the SDK falls back to the CLI's
    ambient credentials. Critical for REP-107: without this branch the
    'provider has no credentials' error blocks every subscription
    session."""
    provider = _FakeProvider(
        id="ch-claude-subscription",
        name="Claude Pro / Max",
        auth_type="oauth",
        runtime_provider="claude_agent",
    )
    result = await resolve_model_provider(
        provider_id="ch-claude-subscription",
        model_id="claude-sonnet-4-6",
        providers=_FakeProviderDatastore([provider]),
        secrets=_UnusedSecrets(),  # type: ignore[arg-type]
    )
    assert result is None


async def test_oauth_subscription_provider_skips_base_url_check() -> None:
    """Subscription providers have no base_url (CLI-managed); the resolver
    must not raise on that even though api_key providers would."""
    provider = _FakeProvider(
        id="ch-codex-subscription",
        name="Codex · ChatGPT",
        auth_type="oauth",
        runtime_provider="codex",
        base_url=None,
    )
    result = await resolve_model_provider(
        provider_id="ch-codex-subscription",
        model_id="gpt-5-codex",
        providers=_FakeProviderDatastore([provider]),
        secrets=_UnusedSecrets(),  # type: ignore[arg-type]
    )
    assert result is None


async def test_api_key_provider_without_base_url_falls_through_to_first_party() -> None:
    """Kernel V5+bba3014: ``ModelProvider.base_url`` is Optional. When the
    row has no URL, the resolver now returns ``base_url=None`` so the
    runtime falls back to the SDK's ambient endpoint. Only api_key is
    strictly required (no credentials still raises)."""

    class _Secrets:
        def get(self, ref: str) -> str | None:
            return "sk-test" if ref == "channel/broken" else None

    provider = _FakeProvider(
        id="ch-broken",
        name="Broken",
        auth_type="api_key",
        base_url=None,
        credential_source="secret_ref",
        secret_ref="channel/broken",
    )
    result = await resolve_model_provider(
        provider_id="ch-broken",
        model_id="any",
        providers=_FakeProviderDatastore([provider]),
        secrets=_Secrets(),  # type: ignore[arg-type]
    )
    assert result is not None
    assert result.base_url is None
    assert result.api_key == "sk-test"


async def test_api_key_provider_without_credentials_still_raises() -> None:
    """The api_key requirement is preserved — only base_url became
    Optional in the upgrade."""
    provider = _FakeProvider(
        id="ch-broken",
        name="Broken",
        auth_type="api_key",
        base_url="https://example.com",
        credential_source="none",
    )
    with pytest.raises(ProviderNotResolvable) as exc:
        await resolve_model_provider(
            provider_id="ch-broken",
            model_id="any",
            providers=_FakeProviderDatastore([provider]),
            secrets=_UnusedSecrets(),  # type: ignore[arg-type]
        )
    assert "credentials" in exc.value.reason


async def test_dual_protocol_builtin_follows_runtime_to_anthropic_endpoint() -> None:
    """Zhipu (GLM) ships with descriptor.anthropic_base_url — when the
    session picks claude_agent runtime, resolve must route to that URL
    with api_protocol=anthropic, regardless of the row's stored base_url.
    """

    class _Secrets:
        def get(self, ref: str) -> str | None:
            return "sk-test" if ref == "channel/zhipu" else None

    provider = _FakeProvider(
        id="ch-zhipu",
        provider_kind="zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",  # openai endpoint
        credential_source="secret_ref",
        secret_ref="channel/zhipu",
    )
    result = await resolve_model_provider(
        provider_id="ch-zhipu",
        model_id="glm-4-plus",
        providers=_FakeProviderDatastore([provider]),
        secrets=_Secrets(),  # type: ignore[arg-type]
        runtime_provider="claude_agent",
    )
    assert result is not None
    assert result.api_protocol == "anthropic"
    assert result.base_url == "https://open.bigmodel.cn/api/anthropic"


async def test_dual_protocol_builtin_follows_runtime_to_openai_endpoint() -> None:
    """Same provider with deepagents runtime → openai endpoint."""

    class _Secrets:
        def get(self, ref: str) -> str | None:
            return "sk-test" if ref == "channel/zhipu" else None

    provider = _FakeProvider(
        id="ch-zhipu",
        provider_kind="zhipu",
        base_url="https://stale.example.com",  # stale row value, must be ignored
        credential_source="secret_ref",
        secret_ref="channel/zhipu",
    )
    result = await resolve_model_provider(
        provider_id="ch-zhipu",
        model_id="glm-4-plus",
        providers=_FakeProviderDatastore([provider]),
        secrets=_Secrets(),  # type: ignore[arg-type]
        runtime_provider="deepagents",
    )
    assert result is not None
    assert result.api_protocol == "openai_completion"
    assert result.base_url == "https://open.bigmodel.cn/api/paas/v4"


async def test_dual_protocol_builtin_fallback_synthesises_anthropic_path() -> None:
    """DeepSeek has no descriptor.anthropic_base_url — fallback is
    ``${default}/anthropic`` so the kernel can still reach the Claude
    shape of the same upstream.
    """

    class _Secrets:
        def get(self, ref: str) -> str | None:
            return "sk-test" if ref == "channel/ds" else None

    provider = _FakeProvider(
        id="ch-deepseek",
        provider_kind="deepseek",
        base_url="https://api.deepseek.com",
        credential_source="secret_ref",
        secret_ref="channel/ds",
    )
    result = await resolve_model_provider(
        provider_id="ch-deepseek",
        model_id="deepseek-v4-flash",
        providers=_FakeProviderDatastore([provider]),
        secrets=_Secrets(),  # type: ignore[arg-type]
        runtime_provider="claude_agent",
    )
    assert result is not None
    assert result.api_protocol == "anthropic"
    assert result.base_url == "https://api.deepseek.com/anthropic"


async def test_compatible_channel_trusts_row_base_url_under_either_runtime() -> None:
    """Custom (compatible) channel: the user told us where to point, so
    runtime_provider must NOT override the stored base_url.
    """

    class _Secrets:
        def get(self, ref: str) -> str | None:
            return "sk-test" if ref == "channel/custom" else None

    provider = _FakeProvider(
        id="ch-custom",
        provider_kind="compatible",
        base_url="https://my-proxy.example.com/v1",
        credential_source="secret_ref",
        secret_ref="channel/custom",
        protocol="anthropic",
    )
    result = await resolve_model_provider(
        provider_id="ch-custom",
        model_id="whatever",
        providers=_FakeProviderDatastore([provider]),
        secrets=_Secrets(),  # type: ignore[arg-type]
        runtime_provider="deepagents",
    )
    assert result is not None
    # protocol on row pinned to anthropic — wins over runtime_provider.
    assert result.api_protocol == "anthropic"
    assert result.base_url == "https://my-proxy.example.com/v1"


async def test_legacy_call_signature_without_runtime_kwarg_still_works() -> None:
    """Sanity check that adding the new kwarg didn't break call sites
    that pre-date this feature (session_service, schedule worker)."""
    provider = _FakeProvider(id="ch-x", runtime_provider="claude_agent", provider_kind="anthropic")
    runtime = await resolve_runtime_provider(
        provider_id="ch-x",
        model_id="any-model",
        providers=_FakeProviderDatastore([provider]),
    )
    assert runtime == "claude_agent"


# ---------------------------------------------------------------------------
# bba3014: 4-value ``api_protocol`` + Optional ``base_url``
# ---------------------------------------------------------------------------


class _SecretRefSecrets:
    """Minimal secret store that resolves a single channel id."""

    def __init__(self, ref: str, key: str = "sk-test") -> None:
        self._ref, self._key = ref, key

    def get(self, ref: str) -> str | None:
        return self._key if ref == self._ref else None


async def test_row_protocol_openai_completion_maps_to_kernel_underscore_form() -> None:
    """User-facing hyphen ``openai-completion`` translates to kernel
    underscore ``openai_completion`` (kernel V5+bba3014)."""
    provider = _FakeProvider(
        id="ch-x",
        provider_kind="compatible",
        protocol="openai-completion",
        base_url="https://proxy.example.com/v1",
        credential_source="secret_ref",
        secret_ref="ch/x",
    )
    result = await resolve_model_provider(
        provider_id="ch-x",
        model_id="any",
        providers=_FakeProviderDatastore([provider]),
        secrets=_SecretRefSecrets("ch/x"),  # type: ignore[arg-type]
    )
    assert result is not None
    assert result.api_protocol == "openai_completion"


async def test_row_protocol_openai_response_maps_to_kernel_underscore_form() -> None:
    provider = _FakeProvider(
        id="ch-x",
        provider_kind="compatible",
        protocol="openai-response",
        base_url="https://proxy.example.com/v1",
        credential_source="secret_ref",
        secret_ref="ch/x",
    )
    result = await resolve_model_provider(
        provider_id="ch-x",
        model_id="any",
        providers=_FakeProviderDatastore([provider]),
        secrets=_SecretRefSecrets("ch/x"),  # type: ignore[arg-type]
    )
    assert result is not None
    assert result.api_protocol == "openai_response"


async def test_row_protocol_gemini_maps_to_kernel_gemini() -> None:
    provider = _FakeProvider(
        id="ch-x",
        provider_kind="compatible",
        protocol="gemini",
        base_url="https://generativelanguage.googleapis.com",
        credential_source="secret_ref",
        secret_ref="ch/x",
    )
    result = await resolve_model_provider(
        provider_id="ch-x",
        model_id="any",
        providers=_FakeProviderDatastore([provider]),
        secrets=_SecretRefSecrets("ch/x"),  # type: ignore[arg-type]
    )
    assert result is not None
    assert result.api_protocol == "gemini"


async def test_legacy_bare_openai_protocol_maps_to_openai_completion() -> None:
    """Back-compat: rows that still carry the legacy bare ``openai``
    value (pre-bba3014) map to ``openai_completion`` (the broadest
    OpenAI-compatible wire shape — what DeepAgents drives)."""
    provider = _FakeProvider(
        id="ch-x",
        provider_kind="compatible",
        protocol="openai",
        base_url="https://proxy.example.com/v1",
        credential_source="secret_ref",
        secret_ref="ch/x",
    )
    result = await resolve_model_provider(
        provider_id="ch-x",
        model_id="any",
        providers=_FakeProviderDatastore([provider]),
        secrets=_SecretRefSecrets("ch/x"),  # type: ignore[arg-type]
    )
    assert result is not None
    assert result.api_protocol == "openai_completion"


async def test_runtime_default_codex_picks_openai_response() -> None:
    """When the row pins no protocol but runtime is codex, the resolver
    picks ``openai_response`` (the only protocol codex's allowlist
    accepts)."""
    provider = _FakeProvider(
        id="ch-x",
        provider_kind="openai",
        protocol=None,
        base_url="https://api.openai.com/v1",
        credential_source="secret_ref",
        secret_ref="ch/x",
    )
    result = await resolve_model_provider(
        provider_id="ch-x",
        model_id="gpt-5-codex",
        providers=_FakeProviderDatastore([provider]),
        secrets=_SecretRefSecrets("ch/x"),  # type: ignore[arg-type]
        runtime_provider="codex",
    )
    assert result is not None
    assert result.api_protocol == "openai_response"


async def test_empty_base_url_normalizes_to_none() -> None:
    """Whitespace/empty ``base_url`` collapses to ``None`` so the runtime
    fires the first-party SDK fallback path (kernel V5+bba3014)."""
    provider = _FakeProvider(
        id="ch-x",
        provider_kind="compatible",
        protocol="anthropic",
        base_url="   ",
        credential_source="secret_ref",
        secret_ref="ch/x",
    )
    result = await resolve_model_provider(
        provider_id="ch-x",
        model_id="any",
        providers=_FakeProviderDatastore([provider]),
        secrets=_SecretRefSecrets("ch/x"),  # type: ignore[arg-type]
    )
    assert result is not None
    assert result.base_url is None

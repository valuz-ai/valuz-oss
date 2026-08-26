"""Tests for the contributed-channel resolution path (ADR-011).

When a provider id isn't in the user table, the resolver consults
``ext.llm_provider``: ``resolve`` synthesises a kernel ``ModelProvider``
from the returned credential, and ``resolve_runtime_provider`` derives the
runtime from the catalog row's protocols + ``serves_responses``.
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
from valuz_agent.modules.providers.schemas import LLMChannel, LLMModel
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.llm_provider import NoopLLMProvider, ResolvedCredential


class _NoProviders:
    async def get_by_id(self, _user_id: str, _: str):  # type: ignore[no-untyped-def]
        return None


class _FakeCatalog:
    def __init__(
        self,
        rows: list[LLMChannel] | None = None,
        creds: dict[str, ResolvedCredential] | None = None,
    ) -> None:
        self._rows = rows or []
        self._creds = creds or {}
        self.resolve_calls: list[tuple[str, str]] = []

    async def list(self, *, user_id: str) -> list[LLMChannel]:
        return list(self._rows)

    async def resolve(self, provider_id: str, *, user_id: str) -> ResolvedCredential | None:
        self.resolve_calls.append((provider_id, user_id))
        return self._creds.get(provider_id)


@pytest.fixture(autouse=True)
def fresh_catalog():
    ext.llm_provider = NoopLLMProvider()
    yield
    ext.llm_provider = NoopLLMProvider()


def _set(
    rows: list[LLMChannel] | None = None,
    creds: dict[str, ResolvedCredential] | None = None,
) -> None:
    ext.llm_provider = _FakeCatalog(rows, creds)


def _row(
    *,
    provider_id: str = "valuz-channel",
    compatible: list[str],
    model_runtimes: tuple[str, ...] | None = None,
) -> LLMChannel:
    return LLMChannel(
        id=provider_id,
        name="Test System Channel",
        provider_kind="system",
        source="system",
        deletable=False,
        is_default=False,
        credential_source="system_managed",
        auth_type="oauth",
        compatible_protocols=compatible,
        group="system",
        group_rank=20,
        models=[LLMModel(id="m", runtimes=model_runtimes)],
    )


class TestResolveModelProviderViaLLMProvider:
    async def test_cred_resolves(self) -> None:
        _set(
            creds={"valuz-channel": ResolvedCredential("https://cloud.test/v1", "abc", "anthropic")}
        )
        mp = await resolve_model_provider(
            provider_id="valuz-channel",
            model_id="claude-sonnet-4-6",
            providers=_NoProviders(),  # type: ignore[arg-type]
            user_id="u1",
        )
        assert mp is not None
        assert mp.base_url == "https://cloud.test/v1"
        assert mp.api_key == "abc"
        assert mp.api_protocol == "anthropic"

    async def test_invalid_api_protocol_raises(self) -> None:
        _set(
            creds={
                "valuz-channel": ResolvedCredential(
                    "https://cloud.test/v1", "abc", "not-a-protocol"
                )
            }
        )
        with pytest.raises(ProviderNotResolvable, match="unknown api_protocol"):
            await resolve_model_provider(
                provider_id="valuz-channel",
                model_id="m",
                providers=_NoProviders(),  # type: ignore[arg-type]
                user_id="u1",
            )

    async def test_empty_api_base_becomes_none(self) -> None:
        _set(creds={"valuz-channel": ResolvedCredential("", "abc", "anthropic")})
        mp = await resolve_model_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
            user_id="u1",
        )
        assert mp is not None
        assert mp.base_url is None

    async def test_unknown_id_raises_not_found(self) -> None:
        # NoopLLMProvider resolves nothing + user table empty → not found.
        with pytest.raises(ProviderNotResolvable, match="not found"):
            await resolve_model_provider(
                provider_id="unknown",
                model_id="m",
                providers=_NoProviders(),  # type: ignore[arg-type]
                user_id="u1",
            )

    async def test_passes_explicit_user_id_to_llm_provider(self) -> None:
        catalog = _FakeCatalog(
            creds={"valuz-channel": ResolvedCredential("https://cloud.test/v1", "abc", "anthropic")}
        )
        ext.llm_provider = catalog

        await resolve_model_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
            user_id="u-from-automation-row",
        )

        assert catalog.resolve_calls == [("valuz-channel", "u-from-automation-row")]


class TestResolveRuntimeProviderViaLLMProvider:
    async def test_runtime_derived_from_compatible_when_undeclared(self) -> None:
        # model.runtimes None → derive from compatible (openai-completion →
        # deepagents).
        _set(rows=[_row(compatible=["openai-completion"])])
        rt = await resolve_runtime_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
            user_id="u1",
        )
        assert rt == "deepagents"

    async def test_declared_model_runtimes_win(self) -> None:
        # The codex gateway declares codex on its model; the response wire alone
        # wouldn't derive it.
        _set(rows=[_row(compatible=["openai-response"], model_runtimes=("codex",))])
        rt = await resolve_runtime_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
            user_id="u1",
        )
        assert rt == "codex"

    async def test_request_runtime_still_overrides(self) -> None:
        _set(rows=[_row(compatible=["anthropic"])])
        rt = await resolve_runtime_provider(
            provider_id="valuz-channel",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
            request_runtime_id="codex",
            user_id="u1",
        )
        assert rt == "codex"

    async def test_unknown_id_defaults_to_deepagents(self) -> None:
        rt = await resolve_runtime_provider(
            provider_id="unknown",
            model_id="m",
            providers=_NoProviders(),  # type: ignore[arg-type]
            user_id="u1",
        )
        assert rt == "deepagents"


class TestResolveModelMaxInputTokens:
    """Extension-channel (ADR-011) declarations — the valuz-lite/pro alias
    path. User-row declarations are covered in test_provider_resolver.py."""

    @staticmethod
    def _channel(provider_id: str, models: list[LLMModel]) -> LLMChannel:
        return LLMChannel(
            id=provider_id,
            name="Valuz Cloud",
            provider_kind="system",
            source="system",
            deletable=False,
            is_default=False,
            credential_source="system_managed",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            group="system",
            group_rank=20,
            models=models,
        )

    async def test_pinned_channel_declared_window_resolves(self) -> None:
        from valuz_agent.adapters.provider_resolver import resolve_model_max_input_tokens

        _set(
            rows=[
                self._channel(
                    "valuz-channel",
                    [
                        LLMModel(id="valuz-lite-anthropic", max_input_tokens=200_000),
                        LLMModel(id="valuz-pro-anthropic", max_input_tokens=1_000_000),
                    ],
                )
            ]
        )
        declared = await resolve_model_max_input_tokens(
            provider_id="valuz-channel",
            model_id="valuz-pro-anthropic",
            providers=_NoProviders(),  # type: ignore[arg-type]
            user_id="u1",
        )
        assert declared == 1_000_000

    async def test_pinned_channel_without_declaration_returns_none(self) -> None:
        from valuz_agent.adapters.provider_resolver import resolve_model_max_input_tokens

        _set(rows=[self._channel("valuz-channel", [LLMModel(id="valuz-lite-anthropic")])])
        declared = await resolve_model_max_input_tokens(
            provider_id="valuz-channel",
            model_id="valuz-lite-anthropic",
            providers=_NoProviders(),  # type: ignore[arg-type]
            user_id="u1",
        )
        assert declared is None

    async def test_no_pin_scans_channels_hosting_the_model(self) -> None:
        from valuz_agent.adapters.provider_resolver import resolve_model_max_input_tokens

        class _EmptyList(_NoProviders):
            async def list_providers(self, _user_id: str) -> list:  # type: ignore[no-untyped-def]
                return []

        _set(
            rows=[
                self._channel("other-channel", [LLMModel(id="unrelated")]),
                self._channel(
                    "valuz-channel", [LLMModel(id="valuz-lite-anthropic", max_input_tokens=200_000)]
                ),
            ]
        )
        declared = await resolve_model_max_input_tokens(
            provider_id=None,
            model_id="valuz-lite-anthropic",
            providers=_EmptyList(),  # type: ignore[arg-type]
            user_id="u1",
        )
        assert declared == 200_000

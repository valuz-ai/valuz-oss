"""Tests for the model-options read model (GET /v1/settings/model-options).

The builder is pure (no DB / catalog) so these are plain unit tests. ADR-011 +
Option-A: each ``LLMModel`` may declare its own ``runtimes``; when it leaves
them ``None`` the builder derives via ``runtimes_for`` from the channel's
``compatible_protocols`` + ``provider_kind``.
"""

from __future__ import annotations

from valuz_agent.modules.providers.schemas import LLMModel
from valuz_agent.modules.settings.model_options import (
    CurrentDefault,
    ProviderOptionInput,
    build_model_options,
    runtimes_for,
)

_GROUP_RANK = {"subscription": 10, "system": 20, "org": 30, "api_key": 40}


def _group_for(source: str, auth_type: str) -> str:
    if source == "org":
        return "org"
    if source == "system":
        return "system"
    if auth_type == "oauth":
        return "subscription"
    return "api_key"


def _m(
    mid: str,
    label: str | None = None,
    runtimes: tuple[str, ...] | None = None,
    selection_hint: str | None = None,
) -> LLMModel:
    return LLMModel(
        id=mid,
        label=label,
        runtimes=runtimes,
        selection_hint=selection_hint,
    )


def _pin(
    *,
    models: list[LLMModel] | None = None,
    **overrides,
) -> ProviderOptionInput:
    base = dict(
        id="p",
        name="P",
        provider_kind="openai",
        source="user",
        auth_type="api_key",
        enabled=True,
        unavailable_reason=None,
        compatible_protocols=["openai-completion"],
        models=models if models is not None else [_m("m")],
    )
    base.update(overrides)
    group = _group_for(base["source"], base["auth_type"])  # type: ignore[arg-type]
    base["group"] = group
    base["group_rank"] = _GROUP_RANK[group]
    return ProviderOptionInput(**base)  # type: ignore[arg-type]


_NO_DEFAULT = CurrentDefault(runtime=None, provider_id=None, model=None)


# ── runtimes_for (OSS default derivation) ────────────────────────────


class TestRuntimesFor:
    def test_anthropic_plus_completion_runs_claude_deepagents_and_harness(self) -> None:
        assert runtimes_for(["anthropic", "openai-completion"], provider_kind="system") == [
            "claude_agent",
            "deepagents",
            "deepseek_harness",
        ]

    def test_codex_subscription_runs_codex(self) -> None:
        # The codex CLI pins to the response wire.
        assert runtimes_for(["openai-response"], provider_kind="codex-subscription") == ["codex"]

    def test_claude_subscription_runs_claude_only(self) -> None:
        assert runtimes_for(["anthropic"], provider_kind="claude-subscription") == ["claude_agent"]

    def test_user_openai_key_drives_codex_and_deepagents(self) -> None:
        # A user OpenAI key speaking both wires drives codex (Responses) AND
        # deepagents (chat completions). The kernel codex runtime reaches a
        # user-supplied key via OPENAI_API_KEY / the model_providers.harness
        # block — it does NOT require the subscription keychain.
        assert runtimes_for(["openai-completion", "openai-response"], provider_kind="openai") == [
            "codex",
            "deepagents",
            "deepseek_harness",
        ]

    def test_response_only_user_row_drives_codex(self) -> None:
        # openai-response alone, non-subscription → codex (only the Responses
        # wire is spoken, and the kernel codex runtime accepts a custom key).
        assert runtimes_for(["openai-response"], provider_kind="openai") == ["codex"]

    def test_custom_compatible_response_drives_codex(self) -> None:
        # A custom OpenAI-compatible gateway pinned to the Responses wire
        # (e.g. Volcengine Ark) → codex, routed through the kernel's
        # ``model_providers.harness`` block with its base_url + API key.
        assert runtimes_for(["openai-response"], provider_kind="compatible") == ["codex"]

    def test_deepseek_channel_shape_runs_all_four(self) -> None:
        # The unpinned DeepSeek channel derives anthropic + openai-completion
        # + openai-response (the Responses wire is served natively for the
        # whole lineup), so all four runtimes apply — claude_agent first.
        assert runtimes_for(
            ["anthropic", "openai-completion", "openai-response"], provider_kind="deepseek"
        ) == ["claude_agent", "codex", "deepagents", "deepseek_harness"]

    def test_completion_wire_derives_harness_protocol_scoped(self) -> None:
        # deepseek_harness is protocol-scoped, exactly like codex on the
        # Responses wire: ANY non-subscription channel speaking
        # chat-completions derives it — the dsh adapter posts a plain
        # ``${base_url}/chat/completions`` body and follows the channel's
        # endpoint via $DEEPSEEK_BASE_URL.
        assert runtimes_for(["openai-completion"], provider_kind="compatible") == [
            "deepagents",
            "deepseek_harness",
        ]

    def test_anthropic_only_channel_does_not_derive_harness(self) -> None:
        assert "deepseek_harness" not in runtimes_for(["anthropic"], provider_kind="anthropic")

    def test_subscription_channels_do_not_derive_harness(self) -> None:
        # Subscription channels expose no API key + base_url for the dsh
        # adapter to consume.
        assert "deepseek_harness" not in runtimes_for([], provider_kind="claude-subscription")
        assert "deepseek_harness" not in runtimes_for([], provider_kind="codex-subscription")

    def test_empty_protocols_is_treated_as_no_restriction(self) -> None:
        # Empty → every protocol; non-subscription system speaks all wires, so
        # every derivable runtime applies.
        assert runtimes_for([], provider_kind="system") == [
            "claude_agent",
            "codex",
            "deepagents",
            "deepseek_harness",
        ]


# ── build_model_options ──────────────────────────────────────────────


class TestBuildModelOptions:
    def test_preserves_picker_only_selection_hint(self) -> None:
        system = _pin(
            source="system",
            provider_kind="system",
            auth_type="oauth",
            models=[_m("valuz-pro", "Valuz Pro", selection_hint="2×")],
        )

        model = build_model_options([system], _NO_DEFAULT).groups[0].providers[0].models[0]

        assert model.label == "Valuz Pro"
        assert model.selection_hint == "2×"

    def test_derives_runtimes_from_compatible(self) -> None:
        """An anthropic channel: models leave runtimes None → derived from the
        channel's compatible_protocols."""
        sys_provider = _pin(
            id="valuz-channel",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            models=[_m("sys-reportify-pro", "Valuz Pro"), _m("valuz-lite", "Valuz Lite")],
        )
        provider = build_model_options([sys_provider], _NO_DEFAULT).groups[0].providers[0]
        by_id = {m.model_id: m for m in provider.models}
        assert by_id["sys-reportify-pro"].runtimes == ["claude_agent", "deepagents"]
        assert by_id["sys-reportify-pro"].default_runtime == "claude_agent"
        assert by_id["sys-reportify-pro"].label == "Valuz Pro"
        assert by_id["valuz-lite"].label == "Valuz Lite"

    def test_declared_runtimes_win(self) -> None:
        # The codex gateway card declares codex on its models, even though the
        # response wire wouldn't otherwise derive it.
        codex_card = _pin(
            id="valuz-channel-codex",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["openai-response"],
            models=[_m("gpt-5.4-nano", "Valuz Pro", runtimes=("codex",))],
        )
        provider = build_model_options([codex_card], _NO_DEFAULT).groups[0].providers[0]
        by_id = {m.model_id: m for m in provider.models}
        assert by_id["gpt-5.4-nano"].runtimes == ["codex"]
        assert by_id["gpt-5.4-nano"].default_runtime == "codex"

    def test_same_named_channels_merge_into_one_card(self) -> None:
        """An anthropic card (derived) + a codex card (declared) both named
        "Valuz 系统模型" collapse to ONE card; each model keeps its owner."""
        anthropic_card = _pin(
            id="valuz-channel",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            models=[_m("sys-reportify-pro", "Valuz Pro"), _m("valuz-lite", "Valuz Lite")],
        )
        codex_card = _pin(
            id="valuz-channel-codex",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["openai-response"],
            models=[
                _m("valuz-lite-codex", "Valuz Lite Codex", runtimes=("codex",)),
                _m("gpt-5.4-nano", "Valuz Pro", runtimes=("codex",)),
            ],
        )
        resp = build_model_options([anthropic_card, codex_card], _NO_DEFAULT)
        system = next(g for g in resp.groups if g.key == "system")
        assert len(system.providers) == 1
        card = system.providers[0]
        assert len(card.models) == 4
        owner = {m.model_id: m.provider_id for m in card.models}
        assert owner["sys-reportify-pro"] == "valuz-channel"
        assert owner["gpt-5.4-nano"] == "valuz-channel-codex"
        by_id = {m.model_id: m for m in card.models}
        assert by_id["sys-reportify-pro"].runtimes == ["claude_agent", "deepagents"]
        assert by_id["gpt-5.4-nano"].runtimes == ["codex"]

    def test_subscription_status_is_client_resolved_with_cli_tool(self) -> None:
        sub = _pin(
            id="claude-subscription",
            name="Claude Pro / Max",
            provider_kind="claude-subscription",
            source="user",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            models=[_m("claude-opus-4-8")],
        )
        resp = build_model_options([sub], _NO_DEFAULT)
        provider = resp.groups[0].providers[0]
        assert resp.groups[0].key == "subscription"
        assert provider.status == "client_resolved"
        assert provider.cli_tool == "claude"
        assert provider.models[0].runtimes == ["claude_agent"]

    def test_api_key_provider_is_available(self) -> None:
        resp = build_model_options([_pin()], _NO_DEFAULT)
        assert resp.groups[0].key == "api_key"
        provider = resp.groups[0].providers[0]
        assert provider.status == "available"
        assert provider.cli_tool is None

    def test_disabled_channel_reports_unavailable_reason(self) -> None:
        prov = _pin(
            id="valuz-channel",
            name="Valuz 系统模型",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            enabled=False,
            unavailable_reason="未登录 Valuz 账户",
            compatible_protocols=["anthropic"],
            models=[_m("m1")],
        )
        provider = build_model_options([prov], _NO_DEFAULT).groups[0].providers[0]
        assert provider.status == "unavailable"
        assert provider.unavailable_reason == "未登录 Valuz 账户"

    def test_models_without_runnable_runtime_are_dropped(self) -> None:
        # A model that declares an empty runtime set (no runnable runtime) is
        # dropped, and a provider left with no models yields no group.
        prov = _pin(compatible_protocols=["openai-response"], models=[_m("m", runtimes=())])
        assert build_model_options([prov], _NO_DEFAULT).groups == []

    def test_groups_ordered_by_group_rank(self) -> None:
        sub = _pin(
            id="s",
            provider_kind="claude-subscription",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            models=[_m("a")],
        )
        sysp = _pin(
            id="y",
            provider_kind="system",
            source="system",
            auth_type="oauth",
            compatible_protocols=["anthropic"],
            models=[_m("b")],
        )
        apik = _pin(id="k", models=[_m("c")])
        resp = build_model_options([apik, sysp, sub], _NO_DEFAULT)
        assert [g.key for g in resp.groups] == ["subscription", "system", "api_key"]

    def test_is_current_default_flagged(self) -> None:
        prov = _pin(id="k", models=[_m("m1"), _m("m2")])
        current = CurrentDefault(runtime="deepagents", provider_id="k", model="m2")
        models = {
            m.model_id: m
            for m in build_model_options([prov], current).groups[0].providers[0].models
        }
        assert models["m2"].is_current_default is True
        assert models["m1"].is_current_default is False

"""Build the "pick a default model" option list (GET /v1/settings/model-options).

This is a *read model* distinct from the provider-management list
(``GET /v1/providers``, which is for add/edit/delete/test). It returns
fully-resolved, render-ready options so the picker UIs (onboarding's
``ConnectStep`` and Settings → Model's default-config card) can stay dumb:

* every model carries the **runtimes it can run on** + a **preferred
  ``default_runtime``** — the frontend never derives a runtime from a
  provider kind again;
* same-named models inside one provider are **disambiguated** here;
* a logical system channel that an overlay registers as multiple
  per-runtime descriptors collapses into one provider with a unioned model
  list (each model still routes to the descriptor that owns it, via its id).

The one thing this endpoint does NOT resolve is CLI-subscription login
state: that credential lives in the local CLI keychain, invisible to the
server. Subscription providers are returned with ``status="client_resolved"``
and the client fills availability in from its own ``checkCliLogin`` probe.

See ``docs/design/model-default-picker-contract.md`` in the commercial repo
for the full contract + rollout.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from valuz_agent.adapters.runtime_registry import RUNTIME_REGISTRY
from valuz_agent.modules.providers.schemas import LLMChannel, LLMModel

# All UI (hyphen-form) wire protocols. An empty per-model protocol list means
# "no declared restriction" (gateway semantics) → treat as every protocol so
# the model surfaces on every runtime it could conceivably run on.
_ALL_PROTOCOLS: tuple[str, ...] = (
    "anthropic",
    "openai-completion",
    "openai-response",
    "gemini",
)

# CLI-subscription provider kinds. These pin to their CLI's wire shape and are
# excluded from the deepagents (Valuz Agent) runtime — the runtime can't reach
# the CLI's own keychain. Mirrors the frontend ``SUBSCRIPTION_PROVIDER_KINDS``.
_SUBSCRIPTION_KINDS: frozenset[str] = frozenset({"claude-subscription", "codex-subscription"})

# Preferred runtime when a model can run on more than one. Onboarding's one-click
# pick uses ``default_runtime``; this order is the tie-break. claude_agent first
# (richest reasoning), then codex, then the generic deepagents, then the
# DeepSeek-channel-only deepseek_harness.
_RUNTIME_PRIORITY: tuple[str, ...] = ("claude_agent", "codex", "deepagents", "deepseek_harness")

# provider_kind → the CLI tool the client probes / launches for login.
_CLI_TOOL_BY_KIND: dict[str, str] = {
    "claude-subscription": "claude",
    "codex-subscription": "codex",
}


def runtimes_for(
    protocols: list[str] | tuple[str, ...],
    *,
    provider_kind: str,
) -> list[str]:
    """Derive the runtimes a model speaking ``protocols`` can drive (the OSS
    default rule), priority-ordered.

    Used to fill ``LLMModel.runtimes`` when a producer leaves it ``None``. A
    producer that knows better declares ``runtimes`` directly. codex is derived
    here for its own ``codex-subscription`` (CLI keychain login) AND for any
    non-subscription channel that speaks the Responses wire: the kernel codex
    runtime accepts a custom ``base_url`` + API key through a synthetic
    ``[model_providers.harness]`` config block (``wire_api="responses"``,
    ``env_key=HARNESS_CODEX_PROVIDER_API_KEY`` — see
    ``kernel/src/runtimes/codex/runtime.py``), so a user-supplied OpenAI-
    compatible Responses endpoint (e.g. Volcengine Ark) can drive codex too.
    (``web_search`` is force-disabled for non-subscription keys — the one
    feature gap, handled kernel-side.) Empty ``protocols`` = "no declared
    restriction" → every protocol.
    """
    protos = set(protocols) if protocols else set(_ALL_PROTOCOLS)
    out: set[str] = set()

    # claude_agent: Claude Code SDK only sends anthropic-shape requests.
    if "anthropic" in protos & set(RUNTIME_REGISTRY["claude_agent"].supported_protocols):
        out.add("claude_agent")

    # codex: its own ChatGPT subscription, OR any non-subscription channel
    # speaking the Responses wire (custom gateway routed through the kernel's
    # ``model_providers.harness`` block — see docstring).
    if provider_kind == "codex-subscription" or (
        provider_kind not in _SUBSCRIPTION_KINDS
        and (protos & set(RUNTIME_REGISTRY["codex"].supported_protocols))
    ):
        out.add("codex")

    # deepagents: any non-subscription channel speaking a protocol it accepts.
    if provider_kind not in _SUBSCRIPTION_KINDS and (
        protos & set(RUNTIME_REGISTRY["deepagents"].supported_protocols)
    ):
        out.add("deepagents")

    # deepseek_harness: the DeepSeek channel only — the dsh adapter targets
    # DeepSeek's own endpoint/models, so other OpenAI-compatible channels
    # don't derive it (a producer can still declare it explicitly).
    if provider_kind == "deepseek" and (
        protos & set(RUNTIME_REGISTRY["deepseek_harness"].supported_protocols)
    ):
        out.add("deepseek_harness")

    return [r for r in _RUNTIME_PRIORITY if r in out]


# ── Wire schema ──────────────────────────────────────────────────────


class ModelOption(BaseModel):
    model_id: str
    # The provider that OWNS this model — what a pick writes back as
    # ``default_provider_id`` so resolution hits the right descriptor. May
    # differ from the enclosing card's ``provider_id`` when several same-named
    # system descriptors are merged into one display card.
    provider_id: str
    # Display label, disambiguated within its provider (so two genuinely
    # different models that share a name don't read identically).
    label: str
    # Every runtime this model can run on (priority-ordered).
    runtimes: list[str]
    # Preferred runtime for a one-click pick. Always ``runtimes[0]``.
    default_runtime: str
    is_current_default: bool


class ModelOptionProvider(BaseModel):
    provider_id: str
    label: str
    kind: str  # provider_kind
    source: str  # user | system | org | template
    # The CLI tool a subscription provider logs in through (claude / codex);
    # ``None`` for non-subscription providers.
    cli_tool: str | None
    # ``available`` / ``unavailable`` are server-authoritative (system / api_key).
    # ``client_resolved`` = the client must fill it in from CLI keychain state
    # (subscription providers — their credential is local + invisible to us).
    status: str
    unavailable_reason: str | None
    models: list[ModelOption]


class ModelOptionGroup(BaseModel):
    key: str  # subscription | system | api_key | org — frontend localizes the header
    providers: list[ModelOptionProvider]


class CurrentDefault(BaseModel):
    runtime: str | None
    provider_id: str | None
    model: str | None


class ModelOptionsResponse(BaseModel):
    current: CurrentDefault
    groups: list[ModelOptionGroup]


# ── Builder input ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProviderOptionInput:
    """The subset of ``LLMChannel`` the builder reads. Decoupled from the
    dataclass so the builder is pure + trivially testable (no DB / catalog)."""

    id: str
    name: str
    provider_kind: str
    source: str
    auth_type: str
    enabled: bool
    unavailable_reason: str | None
    # Channel-level protocols — used to derive runtimes for any model that didn't
    # declare its own (the OSS default path).
    compatible_protocols: list[str]
    # Per-model rows (ADR-011). Each model's ``runtimes`` win when declared;
    # ``None`` → derive from ``compatible_protocols`` + ``provider_kind``.
    models: list[LLMModel]
    # Opaque grouping key + sort, set by the producing side.
    group: str
    group_rank: int


def to_option_input(ch: LLMChannel) -> ProviderOptionInput:
    """Project a provider-list row (``LLMChannel``) onto the builder's input.

    The single mapping shared by every caller of ``build_model_options`` (the
    ``GET /v1/settings/model-options`` route and the ``list_model_options`` MCP
    tool), so both surfaces agree on which (provider, model, runtime)
    combinations exist. Pure — no DB, trivially testable.
    """
    return ProviderOptionInput(
        id=ch.id,
        name=ch.name,
        provider_kind=ch.provider_kind,
        source=ch.source,
        auth_type=ch.auth_type,
        enabled=ch.enabled,
        unavailable_reason=ch.unavailable_reason,
        compatible_protocols=ch.compatible_protocols,
        models=ch.models,
        group=ch.group,
        group_rank=ch.group_rank,
    )


def _provider_status(p: ProviderOptionInput) -> tuple[str, str | None]:
    if p.group == "subscription":
        # Credential is the local CLI keychain — server can't see it.
        return "client_resolved", None
    if p.enabled:
        return "available", None
    return "unavailable", p.unavailable_reason


def _build_raw_provider(
    p: ProviderOptionInput, current: CurrentDefault
) -> ModelOptionProvider | None:
    """One input provider → a card (models NOT yet disambiguated). ``None`` when
    no model has a runnable runtime (the card would be empty noise)."""
    options: list[ModelOption] = []
    for m in p.models:
        # Declared runtimes win; otherwise derive from the channel (OSS default).
        runtimes = (
            list(m.runtimes)
            if m.runtimes is not None
            else runtimes_for(p.compatible_protocols, provider_kind=p.provider_kind)
        )
        if not runtimes:
            # No runtime can run this model → not a selectable default.
            continue
        options.append(
            ModelOption(
                model_id=m.id,
                provider_id=p.id,
                label=m.label or m.id,
                runtimes=runtimes,
                default_runtime=runtimes[0],
                is_current_default=(p.id == current.provider_id and m.id == current.model),
            )
        )
    if not options:
        return None
    status, reason = _provider_status(p)
    return ModelOptionProvider(
        provider_id=p.id,
        label=p.name,
        kind=p.provider_kind,
        source=p.source,
        cli_tool=_CLI_TOOL_BY_KIND.get(p.provider_kind),
        status=status,
        unavailable_reason=reason,
        models=options,
    )


def _merge_same_name(cards: list[ModelOptionProvider]) -> list[ModelOptionProvider]:
    """Collapse same-labelled cards into one, preserving first-seen order.

    A logical system channel is registered as several per-runtime descriptors
    that share a display name; in a flat picker that reads as duplicate cards.
    Merge them into one whose models are the union (deduped by model_id, runtimes
    unioned) — each ``ModelOption`` keeps its own ``provider_id`` so a pick still
    routes to the descriptor that owns it. The card's own ``provider_id`` / status
    come from the first member (a display anchor only)."""
    merged: dict[str, ModelOptionProvider] = {}
    order: list[str] = []
    for card in cards:
        existing = merged.get(card.label)
        if existing is None:
            merged[card.label] = card.model_copy(deep=True)
            order.append(card.label)
            continue
        seen = {m.model_id for m in existing.models}
        for m in card.models:
            if m.model_id not in seen:
                existing.models.append(m)
                seen.add(m.model_id)
                continue
            # Same model reachable via another descriptor → union its runtimes.
            for cur in existing.models:
                if cur.model_id == m.model_id:
                    union = set(cur.runtimes) | set(m.runtimes)
                    cur.runtimes = [r for r in _RUNTIME_PRIORITY if r in union]
                    break
    return [merged[label] for label in order]


def build_model_options(
    providers: list[ProviderOptionInput],
    current: CurrentDefault,
) -> ModelOptionsResponse:
    """Pure builder: providers (+ per-model protocols) → grouped, resolved options.

    Drops models with no runnable runtime and providers left with no models.
    Same-named system channels collapse to one card (Settings disambiguates them
    by runtime; a flat picker can't), and labels colliding inside a card are
    disambiguated last — after the merge.
    """
    by_group: dict[str, list[ModelOptionProvider]] = {}
    rank: dict[str, int] = {}
    for p in providers:
        card = _build_raw_provider(p, current)
        if card is not None:
            by_group.setdefault(p.group, []).append(card)
            # A group's rank is the smallest group_rank any of its rows declares.
            rank[p.group] = min(rank.get(p.group, p.group_rank), p.group_rank)

    # Merge only the system group: same-named cards there are a deliberate
    # "one logical channel, one descriptor per runtime" signal. Leave user-named
    # groups (api_key / subscription) alone so two coincidentally same-named user
    # keys stay distinct.
    if "system" in by_group:
        by_group["system"] = _merge_same_name(by_group["system"])

    # Order groups by their declared ``group_rank`` (ADR-011 — picker reads the
    # row fields, not a hardcoded source order); ties break on the group key.
    # No label disambiguation: each picker view filters by runtime (onboarding
    # prefers claude_agent; Settings has a runtime selector), so two same-named
    # models — a Claude variant + a Codex variant of one logical model — never
    # appear together in one view. Raw labels keep the picker clean.
    groups = [
        ModelOptionGroup(key=key, providers=by_group[key])
        for key in sorted(by_group, key=lambda g: (rank.get(g, 999), g))
    ]
    return ModelOptionsResponse(current=current, groups=groups)


__all__ = [
    "CurrentDefault",
    "ModelOption",
    "ModelOptionGroup",
    "ModelOptionProvider",
    "ModelOptionsResponse",
    "ProviderOptionInput",
    "build_model_options",
    "runtimes_for",
    "to_option_input",
]

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from valuz_agent.i18n import t
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.secret_store import SecretStorePort
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.providers.cli_login_probe import (
    CliTool,
    detect_cli_login,
)
from valuz_agent.modules.providers.cli_login_probe import (
    invalidate as invalidate_cli_login,
)
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.discover import (
    ModelDiscoveryError,
    PingBatchResult,
    discover_models,
    ping_credentials,
    ping_credentials_batch,
)
from valuz_agent.modules.providers.errors import (
    NoAvailableProvider,
    ProviderNotDeletable,
    ProviderNotFound,
)
from valuz_agent.modules.providers.models import ProviderRow
from valuz_agent.modules.providers.schemas import (
    LLMChannel,
    LLMChannelDetail,
    LLMModel,
)
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.llm_provider import SystemProviderImmutable

logger = logging.getLogger(__name__)


# ── Derivation helper ────────────────────────────────────────────────


def derive_runtime_provider(provider_kind: str) -> str:
    if provider_kind in ("anthropic", "claude-subscription"):
        return "claude_agent"
    if provider_kind == "codex-subscription":
        return "codex"
    return "deepagents"


# provider_kind → the CLI tool whose keychain holds its credential. Subscription
# channels are gated on this CLI being logged in (see ``_gate_subscription_login``).
_SUBSCRIPTION_KIND_TO_TOOL: dict[str, CliTool] = {
    "claude-subscription": "claude",
    "codex-subscription": "codex",
}


async def _gate_subscription_login(channels: list[LLMChannel]) -> None:
    """Hide a subscription channel's models when its CLI keychain isn't logged in.

    The Claude Pro·Max / Codex·ChatGPT credential lives in the CLI's own keychain
    — only ``claude auth status`` / ``codex login status`` know whether it's
    usable (see :mod:`cli_login_probe`). The chat composer flattens a channel's
    ``models`` straight into its picker while trusting ``auth_type == "oauth"``
    (no client-side keychain probe), so a logged-out subscription channel would
    offer models that 422 at session creation. Clearing ``models`` /
    ``default_model`` leaves the card but removes the bad picks.

    Applied on the **per-channel detail path** (``get_provider``) ONLY — that is
    what the composer fetches. It is deliberately NOT applied to ``list_providers``:
    that list feeds ``GET /v1/settings/model-options`` (onboarding ConnectStep +
    Settings default-model picker), which already gate subscription rows
    client-side on the keychain probe; stripping there drops the channel from
    model-options and breaks the onboarding login card.

    Mutates ``channels`` in place. Probes once per tool (cached); skipped entirely
    when subscription login is disabled (cloud / shared multi-user, no local
    keychain) or when no subscription channel is present.
    """
    from valuz_agent.infra.config import settings

    if not settings.subscription_login_enabled:
        return
    present = {c.provider_kind for c in channels if c.provider_kind in _SUBSCRIPTION_KIND_TO_TOOL}
    if not present:
        return
    logged_in = {kind: await detect_cli_login(_SUBSCRIPTION_KIND_TO_TOOL[kind]) for kind in present}
    for c in channels:
        if c.provider_kind in _SUBSCRIPTION_KIND_TO_TOOL and not logged_in.get(c.provider_kind):
            c.models = []
            c.default_model = None


def _invalidate_login_cache(provider_kind: str) -> None:
    """Force a re-probe of ``provider_kind``'s CLI login on the next list.

    Called after a subscription login is materialized (``enable_provider``) so the
    freshly logged-in channel's models surface immediately instead of waiting out
    the probe's TTL. No-op for non-subscription kinds.
    """
    tool = _SUBSCRIPTION_KIND_TO_TOOL.get(provider_kind)
    if tool is not None:
        invalidate_cli_login(tool)


# ── Value Objects ───────────────────────────────────────────────────


@dataclass
class InferConfig:
    provider_id: str
    provider_name: str
    provider_kind: str
    model_id: str
    base_url: str | None
    auth_type: str
    api_key: str | None = None
    auth_ref: str | None = None
    runtime_hints: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectionTestResult:
    success: bool
    latency_ms: int | None = None
    error_message: str | None = None


# ``LLMChannel`` / ``LLMModel`` / ``LLMChannelDetail`` now live in
# ``modules.providers.schemas`` — the shared contract OSS / overlay / frontend
# all speak (ADR-011). The helpers below judge OSS's OWN rows onto that shape;
# contributed rows (``ext.llm_provider.list()``) arrive pre-judged.

# Display order of provider groups in the model picker (smaller = earlier).
# Mirrors ``modules.settings.model_options`` group ordering.
_GROUP_RANK: dict[str, int] = {
    "subscription": 10,
    "system": 20,
    "org": 30,
    "api_key": 40,
}


def _group_for(source: str, auth_type: str) -> str:
    """Bucket an OSS-owned row into a picker group key.

    Contributed (catalog) rows set their own ``group`` — this only judges OSS's
    own rows. Mirrors ``modules.settings.model_options._group_key``.
    """
    if source == "org":
        return "org"
    if source == "system":
        return "system"
    if auth_type == "oauth":
        return "subscription"
    return "api_key"


def _models_for(model_ids: list[str], labels: dict[str, str] | None = None) -> list[LLMModel]:
    """Wrap a row's flat model-id list into per-model rows. The backend supplies a
    display ``label`` for curated lists (the OAuth subscription channels, from
    ``subscription_models.json``); other rows leave it ``None`` so the frontend
    resolves the name. ``runtimes`` stays None → the picker derives it from the
    channel's ``compatible_protocols``."""
    labels = labels or {}
    return [LLMModel(id=m, label=labels.get(m)) for m in model_ids]


# ── Provider Registry ───────────────────────────────────────────────


@dataclass(frozen=True)
class ProviderDescriptor:
    kind: str
    display_name: str
    supports_managed_channel: bool = False
    supports_custom_base_url: bool = False
    supports_connection_test: bool = True
    supports_protocol_selection: bool = False
    default_base_url: str = ""
    default_model: str = ""
    model_options: tuple[str, ...] = ()
    # ``{model_id: display_name}`` for curated lists that ship friendly names
    # (the OAuth subscription channels, from subscription_models.json). Empty for
    # api-key descriptors — their model labels are resolved client-side.
    model_labels: dict[str, str] = field(default_factory=dict)
    docs_url: str = ""
    # When ``supports_protocol_selection`` is True, the anthropic-shape
    # endpoint may live at a different path than ``default_base_url`` +
    # "/anthropic" (the fallback the frontend uses). E.g. Moonshot is
    # ``api.moonshot.cn/v1`` for openai but ``api.moonshot.cn/anthropic``
    # for anthropic — no shared prefix. Empty = use the append fallback.
    anthropic_base_url: str = ""
    # ``api_key`` (default — credentials live in secret_ref and ride along on
    # every request) or ``oauth`` (credentials are managed
    # by an external CLI's keychain — e.g. ``claude /login`` / ``codex /login``
    # — and the runtime SDK reads them out-of-band). OAuth-type providers
    # skip the connection-test path because the host has no api_key to send.
    auth_type: str = "api_key"
    # Empty string = host derives the runtime from ``provider_kind`` /
    # protocol; non-empty pins it (used by the OAuth subscription providers
    # that must run on a specific runtime — claude /login → claude_agent,
    # codex /login → codex). Derived — not stored in the DB.
    runtime_provider: str = ""
    # User-facing CLI command shown in the provider-creation UI when the
    # provider is OAuth-type ("Run `claude /login` to log in"). Empty for
    # api_key providers.
    oauth_login_command: str = ""
    # Default api_protocol the provider should be seeded with. Empty = derive
    # from provider_kind. OAuth subscription providers set this so the
    # provider row carries the correct protocol without the user having to
    # disambiguate.
    default_protocol: str = ""


BUILTIN_PROVIDERS: list[ProviderDescriptor] = [
    # ``model_options`` on api_key providers is intentionally empty.
    # Save flow requires an api_key and runs /v1/models discovery —
    # the model picker is populated entirely from the upstream's real
    # list, so no hardcoded fallback is needed. ``default_model`` stays
    # as the seeded value for legacy rows; new rows have it overwritten
    # to ``discovered[0]`` (or a user-supplied id) at create time.
    ProviderDescriptor(
        kind="anthropic",
        display_name="Anthropic",
        supports_managed_channel=True,
        default_base_url="https://api.anthropic.com/v1",
        default_model="claude-sonnet-4-6",
    ),
    ProviderDescriptor(
        kind="openai",
        display_name="OpenAI",
        supports_managed_channel=True,
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-5.4",
    ),
    ProviderDescriptor(
        kind="deepseek",
        display_name="DeepSeek",
        supports_protocol_selection=True,
        supports_custom_base_url=True,
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-v4-flash",
        docs_url="https://api-docs.deepseek.com/zh-cn/",
    ),
    # ── Chinese providers (REP-107) ──────────────────────────────────
    ProviderDescriptor(
        kind="zhipu",
        display_name="智谱 (GLM)",
        supports_protocol_selection=True,
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        anthropic_base_url="https://open.bigmodel.cn/api/anthropic",
        default_model="glm-4-plus",
        docs_url="https://open.bigmodel.cn/dev/api",
    ),
    ProviderDescriptor(
        kind="moonshot",
        display_name="Moonshot (Kimi)",
        supports_protocol_selection=True,
        default_base_url="https://api.moonshot.cn/v1",
        anthropic_base_url="https://api.moonshot.cn/anthropic",
        default_model="kimi-k2-0905-preview",
        docs_url="https://platform.moonshot.cn/docs/api/chat",
    ),
    ProviderDescriptor(
        kind="minimax",
        display_name="MiniMax",
        supports_protocol_selection=True,
        default_base_url="https://api.minimaxi.com/v1",
        anthropic_base_url="https://api.minimaxi.com/anthropic",
        default_model="MiniMax-M2",
        docs_url="https://platform.minimaxi.com/document/Models",
    ),
    ProviderDescriptor(
        kind="compatible",
        display_name="Custom (OpenAI-compatible)",
        supports_custom_base_url=True,
    ),
    # ── OAuth subscription providers ──────────────────────────────────
    ProviderDescriptor(
        kind="claude-subscription",
        # Tells the user *which* Anthropic subscription plans this channel
        # is meant for. Matches the connection-picker card title verbatim
        # so the list-card label and the picker-card title stay in sync.
        display_name="Claude Pro / Max",
        auth_type="oauth",
        supports_connection_test=False,
        runtime_provider="claude_agent",
        default_protocol="anthropic",
        oauth_login_command="claude /login",
        docs_url="https://code.claude.com/docs/en/model-config",
    ),
    ProviderDescriptor(
        kind="codex-subscription",
        # Tool · platform brand format. Tier-agnostic — covers Plus / Pro
        # / Team / Enterprise; surfacing only "Plus" would falsely
        # exclude Pro users.
        display_name="Codex · ChatGPT",
        auth_type="oauth",
        supports_connection_test=False,
        runtime_provider="codex",
        default_protocol="openai",
        oauth_login_command="codex /login",
        docs_url="https://developers.openai.com/codex/models",
    ),
]


def _load_subscription_models() -> dict[str, dict[str, Any]]:
    """Read recommended subscription model lists.

    Resolution order, last writer wins:

    1. Bundled defaults at ``valuz_agent/resources/subscription_models.json``
       (ships with the app — the source of truth maintainers update when
       Anthropic / OpenAI roll out new subscription models).
    2. Per-user override at ``<settings.data_dir>/subscription_models.local.json``
       (lets a user pin private models or override a stale bundled list
       between releases). Keys present in the override entirely replace the
       corresponding bundled entry — no array merge, simpler mental model.

    Missing files / malformed JSON / wrong shape all degrade silently to
    the previous step's value; a corrupted override never blocks boot.
    """
    # __file__ = .../valuz_agent/modules/providers/service.py
    # parents[0] = providers, [1] = modules, [2] = valuz_agent
    bundled_path = Path(__file__).resolve().parents[2] / "resources" / "subscription_models.json"
    merged: dict[str, dict[str, Any]] = {}

    def _ingest(payload: object) -> None:
        if not isinstance(payload, dict):
            return
        subs = payload.get("subscriptions")
        if not isinstance(subs, dict):
            return
        for kind, spec in subs.items():
            if not isinstance(kind, str) or not isinstance(spec, dict):
                continue
            models = spec.get("models")
            default_model = spec.get("default_model")
            if not isinstance(models, list):
                continue
            # A model entry is either a bare id (``"gpt-5.5"``) or
            # ``{"id": ..., "label": ...}`` — the curated file ships friendly
            # display names so the backend, not the frontend, owns them.
            ids: list[str] = []
            labels: dict[str, str] = {}
            for item in models:
                if isinstance(item, str) and item:
                    ids.append(item)
                elif isinstance(item, dict):
                    mid = item.get("id")
                    if isinstance(mid, str) and mid:
                        ids.append(mid)
                        lbl = item.get("label")
                        if isinstance(lbl, str) and lbl.strip():
                            labels[mid] = lbl
            cleaned_models = tuple(ids)
            if not cleaned_models:
                continue
            merged[kind] = {
                "default_model": default_model
                if isinstance(default_model, str) and default_model
                else cleaned_models[0],
                "model_options": cleaned_models,
                "model_labels": labels,
            }

    try:
        with bundled_path.open("r", encoding="utf-8") as fh:
            _ingest(json.load(fh))
    except FileNotFoundError:
        logger.warning(
            "subscription_models.json missing at %s — subscription providers will "
            "have empty model lists",
            bundled_path,
        )
    except (OSError, ValueError) as exc:
        logger.error("failed to load bundled subscription_models.json: %s", exc)

    # Lazy import to avoid circular import at module load.
    try:
        from valuz_agent.infra.config import settings

        local_path = Path(settings.data_dir) / "subscription_models.local.json"
        if local_path.is_file():
            with local_path.open("r", encoding="utf-8") as fh:
                _ingest(json.load(fh))
    except Exception as exc:  # noqa: BLE001 — don't let user override break boot
        logger.warning("ignoring subscription_models.local.json: %s", exc)

    return merged


def _hydrate_subscription_providers(
    providers: list[ProviderDescriptor],
    models_by_kind: dict[str, dict[str, Any]],
) -> list[ProviderDescriptor]:
    """Inject the JSON-loaded model lists into the matching descriptors.

    Returns a new list — the input list isn't mutated. Frozen-dataclass
    descriptors are replaced via ``dataclasses.replace`` so the rest of
    the module sees the hydrated values immediately on first import.
    """
    from dataclasses import replace as _replace

    out: list[ProviderDescriptor] = []
    for provider in providers:
        spec = models_by_kind.get(provider.kind)
        if spec is None:
            out.append(provider)
            continue
        out.append(
            _replace(
                provider,
                default_model=spec["default_model"],
                model_options=spec["model_options"],
                model_labels=spec.get("model_labels", {}),
            )
        )
    return out


BUILTIN_PROVIDERS = _hydrate_subscription_providers(BUILTIN_PROVIDERS, _load_subscription_models())


_PROVIDER_MAP: dict[str, ProviderDescriptor] = {p.kind: p for p in BUILTIN_PROVIDERS}


# Built-in provider IDs and the descriptor kind they bind to live in
# ``valuz_agent/resources/seeds/providers.json`` — see
# ``valuz_agent.seeds.providers`` for the load + insert path. The
# ``ProviderDescriptor`` map above still owns the rich runtime
# metadata (default_model, model_options, base_url, auth_type) each
# kind needs; the JSON file just enumerates which kinds get a
# built-in row at first boot.


def get_provider(kind: str) -> ProviderDescriptor:
    p = _PROVIDER_MAP.get(kind)
    if not p:
        raise ProviderNotFound(f"Provider {kind!r} not registered")
    return p


# ── Reset / Reseed ──────────────────────────────────────────────────


async def reset_providers(
    ds: ProviderDatastore,
    user_id: str,
    *,
    drop_table: bool = False,
    engine: Any | None = None,
) -> list[LLMChannel]:
    """Wipe ``valuz_provider`` and re-seed it from the current code.

    Use cases:

    - Colleague pulled latest code and the schedule runner can't talk to any
      model because their old SQLite has stale rows (e.g. ``ch-anthropic``
      pointing at the wrong base_url).
    - The on-disk schema is missing a column added in a later commit (only
      possible if the table existed before the column was added — boot
      ``Base.metadata.create_all`` only creates missing tables, never alters).

    Modes:

    - ``drop_table=False`` (default): clear all rows, keep table shape, run
      the boot seeders again. Fixes data corruption without touching schema.
    - ``drop_table=True``: drop the whole table, recreate from the current
      ORM definition, then seed. Requires ``engine``. Fixes schema drift
      too. SQLite single-writer means callers should pause concurrent
      requests during this; the API endpoint executes it inside a single
      request lifecycle.

    Returns the post-reset provider list. Idempotent.
    """
    from typing import cast

    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncEngine

    if drop_table:
        if not isinstance(engine, AsyncEngine):
            raise ValueError("drop_table=True requires an AsyncEngine")
        # ``__table__`` is a ``Table`` at runtime; the declarative base types it
        # as the wider ``FromClause`` (no ``drop``/``create``), so narrow it.
        table = cast(Table, ProviderRow.__table__)
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: table.drop(c, checkfirst=True))
            await conn.run_sync(lambda c: table.create(c, checkfirst=True))
    else:
        for row in list(await ds.list_providers(user_id)):
            await ds.delete(user_id, row.id)

    from valuz_agent.seeds.providers import seed_builtin_providers

    await seed_builtin_providers(user_id, ds)

    return [_row_to_list_item(r) for r in await ds.list_providers(user_id)]


# ── Helpers ─────────────────────────────────────────────────────────


def _derive_effective_protocol(row: ProviderRow) -> str:
    """Map a provider row onto the wire protocol the kernel runtime expects.

    Returns the user-facing hyphen form — one of ``anthropic`` /
    ``openai-completion`` / ``openai-response`` / ``gemini``. Used as a
    single-protocol fallback; the full set lives in
    ``compatible_protocols``.

    Mirrors ``adapters.provider_resolver._resolve_api_protocol`` (with
    output translated from kernel underscore form to user-facing hyphen
    form). Always returns the FIRST element of ``compatible_protocols``
    so the two stay in lock-step.
    """
    return _derive_compatible_protocols(row)[0]


def _derive_compatible_protocols(row: ProviderRow) -> list[str]:
    """All wire protocols this provider can plausibly drive.

    Returns user-facing hyphen form values. Pinned by ``row.protocol``
    when the user has disambiguated (the ProviderEditDialog writes
    ``protocol`` for custom + dual-protocol upstreams). Otherwise the
    descriptor's ``supports_protocol_selection`` flag implies dual
    compatibility: DeepSeek / Zhipu / Moonshot / MiniMax expose both
    ``/v1/chat/completions`` (→ ``openai-completion`` for DeepAgents)
    and ``/anthropic/v1/messages`` (→ ``anthropic`` for claude_agent).

    Subscription providers pin to their CLI's wire shape:
      * claude-subscription → ``["anthropic"]``
      * codex-subscription → ``["openai-response"]``

    Mirrors the kernel ``factory.ALLOWED_PROTOCOLS_BY_RUNTIME`` 4-value
    enum (anthropic / openai-completion / openai-response / gemini), so
    the frontend's per-runtime filter narrows the provider list correctly.
    """
    raw = (row.protocol or "").strip().lower()
    # Explicit row pin wins.
    if raw == "anthropic":
        return ["anthropic"]
    if raw == "openai-completion":
        return ["openai-completion"]
    if raw == "openai-response":
        return ["openai-response"]
    if raw == "gemini":
        return ["gemini"]
    # Legacy bare "openai" — pre-protocol-split. Treat as completion
    # (the broadest match — most legacy OpenAI-shape rows were used
    # with DeepAgents).
    if raw == "openai":
        return ["openai-completion"]

    # Built-in dual-protocol descriptor (DeepSeek / Zhipu / Moonshot /
    # MiniMax) → speaks anthropic via /anthropic + openai-completion
    # via /v1.
    descriptor = _PROVIDER_MAP.get(row.provider_kind)
    if descriptor and descriptor.supports_protocol_selection:
        return ["anthropic", "openai-completion"]

    # Subscription providers — pinned by descriptor.runtime_provider.
    if row.provider_kind == "claude-subscription":
        return ["anthropic"]
    if row.provider_kind == "codex-subscription":
        return ["openai-response"]

    # Single-kind built-ins.
    if row.provider_kind == "anthropic":
        return ["anthropic"]
    if row.provider_kind == "openai":
        # Official OpenAI endpoint speaks both /v1/chat/completions
        # (deepagents) and /v1/responses (codex). Both kernel api_protocols
        # are reachable with the same key + base_url.
        return ["openai-completion", "openai-response"]
    if row.provider_kind == "gemini":
        return ["gemini"]
    # deepseek / compatible / unknown: openai-completion default
    # (DeepAgents-compatible chat completions wire).
    return ["openai-completion"]


def _resolve_model_options(row: ProviderRow) -> list[str]:
    """Decode the row's effective ``model_options`` list.

    Resolution mirrors ``_row_to_detail`` (kept in lockstep so the list
    and detail endpoints never disagree):
      - ``model_ids IS NULL``   → fall back to the descriptor's
        recommended list (fresh user provider, never discovered).
      - ``model_ids = "[]"``    → explicit empty (managed credential-only
        anchors like ch-reportify); do NOT fall back.
      - otherwise               → use the parsed list.
    """
    provider = _PROVIDER_MAP.get(row.provider_kind)
    fallback = list(provider.model_options) if provider else []
    if row.model_ids is None:
        return fallback
    try:
        parsed = json.loads(row.model_ids)
    except (json.JSONDecodeError, TypeError):
        return fallback
    if isinstance(parsed, list):
        return [m for m in parsed if isinstance(m, str)]
    return fallback


def _row_to_list_item(row: ProviderRow) -> LLMChannel:
    compatible = _derive_compatible_protocols(row)
    group = _group_for(row.source, row.auth_type)
    descriptor = _PROVIDER_MAP.get(row.provider_kind)
    return LLMChannel(
        id=row.id,
        name=row.name,
        provider_kind=row.provider_kind,
        source=row.source,
        enabled=row.enabled,
        is_default=row.is_default,
        deletable=row.deletable,
        default_model=row.default_model,
        test_status=row.test_status,
        credential_source=row.credential_source,
        auth_type=row.auth_type,
        protocol=row.protocol,
        effective_protocol=compatible[0],
        compatible_protocols=compatible,
        # models leave runtimes unset (None) → the picker derives them from
        # compatible_protocols. OSS user/builtin rows never drive codex (codex
        # walks its own keychain) except codex-subscription, which runtimes_for
        # matches by provider_kind.
        group=group,
        group_rank=_GROUP_RANK[group],
        models=_models_for(
            _resolve_model_options(row), descriptor.model_labels if descriptor else None
        ),
    )


def _list_item_to_detail(it: LLMChannel) -> LLMChannelDetail:
    """Wrap a contributed (catalog) list row into a detail view.

    Catalog rows are system-managed: no editable base_url, no connection test.
    Used by ``get_provider`` so the edit dialog opens read-only on a row that
    has no ``valuz_provider`` table entry.
    """
    return LLMChannelDetail(
        id=it.id,
        name=it.name,
        provider_kind=it.provider_kind,
        source=it.source,
        enabled=it.enabled,
        is_default=it.is_default,
        deletable=it.deletable,
        default_model=it.default_model,
        test_status=it.test_status,
        credential_source=it.credential_source,
        auth_type=it.auth_type,
        protocol=it.protocol,
        effective_protocol=it.effective_protocol,
        compatible_protocols=it.compatible_protocols,
        group=it.group,
        group_rank=it.group_rank,
        models=it.models,
        unavailable_reason=it.unavailable_reason,
        base_url=None,
        supports_custom_base_url=False,
        supports_connection_test=False,
    )


def _row_to_detail(row: ProviderRow) -> LLMChannelDetail:
    provider = _PROVIDER_MAP.get(row.provider_kind)
    compatible = _derive_compatible_protocols(row)
    group = _group_for(row.source, row.auth_type)
    return LLMChannelDetail(
        id=row.id,
        name=row.name,
        provider_kind=row.provider_kind,
        source=row.source,
        enabled=row.enabled,
        is_default=row.is_default,
        deletable=row.deletable,
        default_model=row.default_model,
        test_status=row.test_status,
        credential_source=row.credential_source,
        auth_type=row.auth_type,
        protocol=row.protocol,
        effective_protocol=compatible[0],
        compatible_protocols=compatible,
        # models leave runtimes unset (None) → the picker derives them.
        group=group,
        group_rank=_GROUP_RANK[group],
        models=_models_for(
            _resolve_model_options(row), provider.model_labels if provider else None
        ),
        base_url=row.base_url,
        supports_custom_base_url=provider.supports_custom_base_url if provider else False,
        supports_connection_test=(
            (provider.supports_connection_test if provider else True) and row.auth_type != "oauth"
        ),
    )


# ── Service ─────────────────────────────────────────────────────────


_DISCOVERY_PROTOCOL_MAP: dict[str, str] = {
    "anthropic": "anthropic",
    "openai-completion": "openai",
    "openai-response": "openai",
    "gemini": "openai",
}


def _resolve_discovery_protocol(row: ProviderRow) -> str:
    """Pick the API wire shape to probe this provider's upstream.

    Returns a 2-value wire shape (``anthropic`` / ``openai``) — only
    two HTTP shapes exist at the model-discovery level. The user-facing
    4-value ``api_protocol`` (openai-completion / openai-response /
    anthropic / gemini) collapses to one of these for ``/v1/models``
    probing. Explicit ``protocol`` field wins; falls back to
    ``provider_kind=="anthropic"`` → anthropic, else openai.
    """
    mapped = _DISCOVERY_PROTOCOL_MAP.get(row.protocol or "")
    if mapped is not None:
        return mapped
    if row.protocol == "openai":
        return "openai"
    if row.provider_kind == "anthropic":
        return "anthropic"
    return "openai"


def _builtin_subscription_row(entry: Any) -> ProviderRow | None:
    """Transient (unpersisted) ``ProviderRow`` for a built-in OAuth subscription
    catalog entry, or ``None`` when the entry isn't an OAuth kind.

    Shared by the list (``_builtin_subscription_template_items``) and the detail
    (``_virtual_builtin_subscription_detail``) so the virtual template a user
    sees in the list opens identically in the edit dialog. ``model_ids=None``
    keeps the picker tracking the live recommended subscription catalog.

    The template carries its full recommended model list here; whether those
    models are actually surfaced is decided later by ``_gate_subscription_login``
    (stripped when the CLI keychain isn't logged in).
    """
    descriptor = _PROVIDER_MAP.get(entry.provider_kind)
    if descriptor is None or descriptor.auth_type != "oauth":
        return None
    return ProviderRow(
        id=entry.id,
        name=entry.name,
        provider_kind=entry.provider_kind,
        source="builtin",
        credential_source="none",
        base_url=descriptor.default_base_url or None,
        default_model=descriptor.default_model or None,
        model_ids=None,
        account_provider_id=None,
        enabled=True,
        is_default=False,
        deletable=False,
        test_status="never",
        auth_type="oauth",
    )


def _builtin_subscription_template_items(configured_kinds: set[str]) -> list[LLMChannel]:
    """Virtual list entries for OSS CLI-subscription channels (Claude Pro·Max,
    Codex) the caller hasn't configured yet.

    These are NOT persisted. Built-ins are no longer seeded as rows (seeding bred
    rows owned by an identity that may never authenticate — the device-local id
    under single-tenant, no one under a multi-user overlay — plus a well-known-id
    primary-key collision when the same channel is configured by multiple users).
    A real ``valuz_provider`` row is materialized only on login
    (``ProviderService._materialize_builtin_subscription``). A kind already in
    ``configured_kinds`` (a prior login, or a legacy seeded row) is skipped so the
    configured row shows instead of a duplicate template.

    Only OAuth subscription kinds are virtualized here — api_key built-ins are
    discovered through the "add provider" dialog (``GET /v1/providers/config``)
    and materialized by ``create_provider``. Gated by
    ``settings.subscription_login_enabled`` (off on a shared multi-user server
    with no local CLI keychain).
    """
    from valuz_agent.infra.config import settings
    from valuz_agent.seeds._io import load_provider_seeds

    if not settings.subscription_login_enabled:
        return []
    items: list[LLMChannel] = []
    for entry in load_provider_seeds().providers:
        if entry.provider_kind in configured_kinds:
            continue
        row = _builtin_subscription_row(entry)
        if row is None:
            continue
        items.append(_row_to_list_item(row))
    return items


def _virtual_builtin_subscription_detail(provider_id: str) -> LLMChannelDetail | None:
    """Detail for an unconfigured built-in OAuth subscription template.

    No DB row exists for a virtual template until the user logs in, so the edit
    dialog's ``GET /v1/providers/{id}`` would 404. Mirror what
    ``_builtin_subscription_template_items`` surfaces in the list so the dialog
    opens on the same data. ``None`` when ``provider_id`` isn't a built-in OAuth
    catalog id or subscription login is disabled for this deployment.
    """
    from valuz_agent.infra.config import settings
    from valuz_agent.seeds._io import load_provider_seeds

    if not settings.subscription_login_enabled:
        return None
    entry = next((e for e in load_provider_seeds().providers if e.id == provider_id), None)
    if entry is None:
        return None
    row = _builtin_subscription_row(entry)
    if row is None:
        return None
    return _row_to_detail(row)


class ProviderService:
    def __init__(
        self,
        datastore: ProviderDatastore,
        secret_store: SecretStorePort,
        event_bus: EventBus,
    ) -> None:
        self._ds = datastore
        self._secrets = secret_store
        self._bus = event_bus

    # ── Queries ──────────────────────────────────────────────────

    async def list_providers(self, user_id: str) -> list[LLMChannel]:
        rows = await self._ds.list_providers(user_id)
        policy = ext.policy
        # When the caller's org locks custom models, hide their own
        # (``source="user"``) providers so they can't be selected — the
        # "禁止使用" half of the lock. Managed/system rows are unaffected.
        hide_user = await policy.hide_user_providers()
        user_items = [
            _row_to_list_item(r)
            for r in rows
            if r.enabled and not (hide_user and r.source == "user")
        ]
        # Prepend overlay-contributed rows (ADR-011). The single
        # ``ext.llm_provider`` returns already-judged, key-free
        # ``LLMChannel`` rows (e.g. the commercial "Valuz 系统模型" +
        # "组织模型" channels). OSS makes zero judgement on them — pure
        # append. A contributed row with no selectable models is dropped: a
        # card with nothing to pick is noise (e.g. the 组织模型 card when the
        # org has no model of that protocol).
        extra_items = [it for it in await ext.llm_provider.list() if it.models]
        # Virtual CLI-subscription templates for kinds not yet configured (no DB
        # row exists until the user logs in — see ``_materialize_builtin_subscription``).
        configured_kinds = {r.provider_kind for r in rows}
        template_items = _builtin_subscription_template_items(configured_kinds)
        combined = extra_items + user_items + template_items
        # Richer visibility filter (overlay-bound policy): hide whole provider
        # ids the caller isn't allowed to see — e.g. builtin personal channels
        # (Claude Pro/Max, Codex) when the platform disables member-configured
        # models. Superset of the coarse ``hide_user`` filter above; the default
        # ``AllowAllProviderPolicy`` hides nothing, so OSS behaviour is unchanged.
        hidden = await policy.hidden_provider_ids(combined)
        if hidden:
            combined = [it for it in combined if it.id not in hidden]
        # NB: subscription-login gating is applied in ``get_provider`` (the
        # per-channel detail the composer fetches), NOT here. The list feeds
        # ``GET /v1/settings/model-options`` (onboarding ConnectStep + Settings
        # default-model picker), which already gate subscription rows client-side
        # on the CLI keychain probe (``status="client_resolved"`` +
        # ``isModelProviderUsable``). Stripping models here would drop the channel
        # from model-options entirely and break the onboarding login card.
        return combined

    async def get_provider(self, user_id: str, provider_id: str) -> LLMChannelDetail:
        row = await self._ds.get_by_id(user_id, provider_id)
        if row is not None:
            detail = _row_to_detail(row)
            # Same login gate as ``list_providers`` — the composer fetches the
            # per-channel detail, so a logged-out subscription row must hide its
            # models here too.
            await _gate_subscription_login([detail])
            return detail
        # Not a user row — maybe an overlay-contributed (catalog) channel
        # (ADR-011). Catalog ids don't collide with user UUIDs, so checking
        # the user table first is safe.
        for it in await ext.llm_provider.list():
            if it.id == provider_id:
                return _list_item_to_detail(it)
        # Still nothing: the id may be an unconfigured built-in subscription
        # template (virtualized in ``list_providers``) — resolve it so the edit
        # dialog opens instead of erroring with "获取模型详情失败".
        virtual = _virtual_builtin_subscription_detail(provider_id)
        if virtual is not None:
            await _gate_subscription_login([virtual])
            return virtual
        raise ProviderNotFound(f"Provider {provider_id!r} not found")

    async def _guard_not_system(self, provider_id: str) -> None:
        """Raise ``SystemProviderImmutable`` if id is a non-deletable
        contributed (catalog) row.

        Used by every write operation so overlay-contributed channels can't be
        edited / deleted / tested via the user CRUD path. The judgement is the
        row's ``deletable`` flag, not its source (ADR-011 治理). The route layer
        maps this to HTTP 409.
        """
        for it in await ext.llm_provider.list():
            if it.id == provider_id and not it.deletable:
                raise SystemProviderImmutable(provider_id)

    def list_provider_descriptors(self) -> list[ProviderDescriptor]:
        return list(BUILTIN_PROVIDERS)

    async def probe_models(
        self,
        provider_kind: str,
        api_key: str,
        base_url: str | None = None,
        protocol: str | None = None,
    ) -> dict[str, Any]:
        """Stateless probe of ``<base>/v1/models``.

        Used by the add-provider dialog's [test] button so the user can see
        real models *before* the row is persisted. Does NOT write to the
        DB or secret_store. Raises ``ModelDiscoveryError`` on auth /
        network failures — router translates to HTTP 422.

        Returns ``{models, suggested_default}`` where ``suggested_default``
        prefers descriptor.default_model when it appears in the discovered
        list, falling back to ``discovered[0]``.
        """
        descriptor = get_provider(provider_kind)
        effective_base_url = base_url or descriptor.default_base_url
        if not effective_base_url:
            raise ModelDiscoveryError("base_url is required")

        stripped_key = api_key.strip()
        # HTTP Authorization headers are latin-1 only — non-ASCII keys
        # crash httpx with UnicodeEncodeError before the request even
        # leaves the host. Fail fast with a user-readable message so the
        # router can surface 422 + reason instead of a generic 500 that
        # the browser sees as "Failed to fetch".
        if not stripped_key:
            raise ModelDiscoveryError(t("backend.provider.apiKeyEmpty"))
        try:
            stripped_key.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ModelDiscoveryError(t("backend.provider.apiKeyNonAscii")) from exc

        protocol_for_discovery = _DISCOVERY_PROTOCOL_MAP.get(protocol or "") or (
            "anthropic" if provider_kind == "anthropic" else "openai"
        )

        discovered = await discover_models(
            base_url=effective_base_url,
            api_key=stripped_key,
            protocol=protocol_for_discovery,
        )

        models = sorted(set(discovered))
        suggested: str | None = None
        if descriptor.default_model and descriptor.default_model in models:
            suggested = descriptor.default_model
        elif models:
            suggested = models[0]

        return {"models": models, "suggested_default": suggested}

    async def read_stored_api_key(self, user_id: str, provider_id: str) -> str | None:
        """Pull the persisted api_key out of secret_store for a row.

        Used by the ping endpoint to support edit-mode flows where the
        user wants to re-test after adding a new model id without
        re-typing the saved key. Returns ``None`` when the row has no
        ``secret_ref`` (OAuth-type providers, or fresh rows whose key
        was never saved). Raises ``ProviderNotFound`` if the row itself
        doesn't exist.
        """
        row = await self._ds.get_by_id(user_id, provider_id)
        if row is None:
            raise ProviderNotFound(f"Provider {provider_id!r} not found")
        if not row.secret_ref:
            return None
        return self._secrets.get(user_id, row.secret_ref)

    async def ping_compatible_batch(
        self,
        *,
        api_key: str,
        base_url: str,
        protocol: str | None,
        models: list[str],
    ) -> PingBatchResult:
        """Per-model 1-token chat/messages probe; returns the verified subset.

        Used by the Add/Edit dialog's [连接测试] button so the user can
        see which ids work *before* save. The save flow re-runs the
        batch server-side so frontend test state can't leak unverified
        ids into ``model_ids``.
        """
        stripped_key = (api_key or "").strip()
        if not stripped_key:
            raise ModelDiscoveryError("API Key 不能为空")
        try:
            stripped_key.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ModelDiscoveryError(
                "API Key 含非 ASCII 字符，可能是误粘贴了中文或不可见空白"
            ) from exc
        if not base_url:
            raise ModelDiscoveryError("Endpoint 不能为空")
        cleaned = [m.strip() for m in models if isinstance(m, str) and m.strip()]
        if not cleaned:
            raise ModelDiscoveryError("至少需要 1 个模型 id")

        protocol_for_ping = _DISCOVERY_PROTOCOL_MAP.get(protocol or "") or "openai"
        return await ping_credentials_batch(
            base_url=base_url,
            api_key=stripped_key,
            protocol=protocol_for_ping,
            models=cleaned,
        )

    async def ping_compatible(
        self,
        *,
        api_key: str,
        base_url: str,
        protocol: str | None,
        model: str,
    ) -> None:
        """Stateless 1-token chat/messages ping for the custom channel.

        The "compatible" provider can't rely on ``GET /v1/models`` for
        validation — Anthropic-shape endpoints don't expose it, and many
        private proxies skip it. The add-channel dialog's [连接测试]
        button calls this instead: a 200 from the chat / messages
        endpoint confirms the (api_key, endpoint, model) tuple works
        end-to-end, which is what the user actually needs to know.

        Raises ``ModelDiscoveryError`` on auth / network / upstream
        failures — router translates to 422.
        """
        stripped_key = (api_key or "").strip()
        if not stripped_key:
            raise ModelDiscoveryError("API Key 不能为空")
        try:
            stripped_key.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ModelDiscoveryError(
                "API Key 含非 ASCII 字符，可能是误粘贴了中文或不可见空白"
            ) from exc

        protocol_for_ping = _DISCOVERY_PROTOCOL_MAP.get(protocol or "") or "openai"
        await ping_credentials(
            base_url=base_url,
            api_key=stripped_key,
            protocol=protocol_for_ping,
            model=model,
        )

    # ── Commands ─────────────────────────────────────────────────

    async def create_provider(
        self,
        user_id: str,
        name: str,
        provider_kind: str,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        protocol: str | None = None,
        models: list[str] | None = None,
    ) -> LLMChannelDetail:
        provider = get_provider(provider_kind)

        effective_base_url = base_url or provider.default_base_url
        # Fall back to descriptor-bundled model list when discovery is
        # skipped (no api_key) or returns empty.
        fallback_models: list[str] = list(provider.model_options or ())

        is_custom = provider_kind == "compatible"
        # Custom (compatible) channels: the upstream may not expose
        # /v1/models (Anthropic-shape doesn't; many private proxies skip
        # it too), so the user provides the model list verbatim. We
        # verify the (api_key, endpoint, model) tuple with a 1-token
        # chat/messages ping instead — that's what they actually care
        # about: "can this channel answer my next prompt".
        user_models: list[str] = []
        if is_custom:
            user_models = [m.strip() for m in (models or []) if isinstance(m, str) and m.strip()]
            if not user_models:
                raise ModelDiscoveryError("请至少填写 1 个模型 id")

        # B-mode: when the caller supplies an api_key we validate it
        # before persisting so the user never ends up with a stored
        # channel they can't use. Built-ins go through /v1/models
        # discovery (also hydrates ``model_ids``); custom channels go
        # through ``ping_credentials`` (validates auth + endpoint + model).
        # OAuth subscription kinds never snapshot the recommended list —
        # ``model_ids`` stays NULL so the row tracks the live descriptor
        # (mirrors ``seeds/providers.py``; the empty list also keeps the
        # default_model chain below on the descriptor default).
        if provider.auth_type == "oauth":
            model_ids_list: list[str] = []
        else:
            model_ids_list = list(user_models) if is_custom else list(fallback_models)
        if api_key:
            stripped_key = api_key.strip()
            # HTTP Authorization is latin-1 only; non-ASCII (e.g. user
            # pasted Chinese by mistake) crashes httpx with
            # UnicodeEncodeError before the request leaves the host —
            # browser sees "Failed to fetch". Fail fast with a clear msg.
            if not stripped_key:
                raise ModelDiscoveryError(t("backend.provider.apiKeyEmpty"))
            try:
                stripped_key.encode("ascii")
            except UnicodeEncodeError as exc:
                raise ModelDiscoveryError(t("backend.provider.apiKeyNonAscii")) from exc
            # protocol for /v1/models discovery: explicit ``protocol`` arg
            # wins, else openai for everything except plain anthropic.
            protocol_for_discovery = _DISCOVERY_PROTOCOL_MAP.get(protocol or "") or (
                "anthropic" if provider_kind == "anthropic" else "openai"
            )
            if is_custom:
                # Ping every user-supplied model — keep the ones that
                # work, drop the rest. The user's intent is "save what's
                # usable"; silently shipping broken ids leaves footguns
                # in the picker.
                batch = await ping_credentials_batch(
                    base_url=effective_base_url,
                    api_key=stripped_key,
                    protocol=protocol_for_discovery,
                    models=user_models,
                )
                if not batch.ok:
                    # All failed — surface every reason so the user can
                    # see which models broke and why.
                    summary = "；".join(f"{m}: {r}" for m, r in batch.failed)
                    raise ModelDiscoveryError(f"所有模型连接测试均失败：{summary}")
                # Replace the user-provided list with the verified
                # subset. Failed ids are dropped from model_ids and from
                # default_model resolution below.
                model_ids_list = list(batch.ok)
                user_models = list(batch.ok)
            else:
                try:
                    discovered = await discover_models(
                        base_url=effective_base_url,
                        api_key=stripped_key,
                        protocol=protocol_for_discovery,
                    )
                except ModelDiscoveryError:
                    # Caller-actionable — propagate; router translates to 422.
                    raise
                # Discovery returned (possibly empty) — key is valid. Trust
                # the upstream list verbatim instead of unioning with the
                # descriptor's hardcoded ``model_options``: those exist as
                # pre-key-entry placeholders only and tend to drift (stale
                # ids, in-house labels like ``deepseek-v4-pro[1m]`` that the
                # real API doesn't list). When the upstream returns nothing
                # we keep the hardcoded fallback so the picker isn't empty.
                if discovered:
                    model_ids_list = sorted(set(discovered))
                else:
                    model_ids_list = list(fallback_models)

        # Resolve default_model with sensible fallback chain:
        #   user-supplied → descriptor.default (if in list) → first in list
        #   → descriptor.default (last resort, for rows with empty list).
        effective_default: str | None
        if default_model and (not model_ids_list or default_model in model_ids_list):
            effective_default = default_model
        elif provider.default_model and provider.default_model in model_ids_list:
            effective_default = provider.default_model
        elif model_ids_list:
            effective_default = model_ids_list[0]
        else:
            effective_default = provider.default_model

        # Only now stash the secret — at this point we've validated the
        # key (or none was provided), so we won't end up with orphaned
        # entries in secret_store on failure.
        secret_ref: str | None = None
        if api_key:
            secret_ref = f"channel/{uuid4().hex[:12]}"
            self._secrets.put(user_id, secret_ref, api_key.strip())

        row = ProviderRow(
            name=name.strip(),
            provider_kind=provider_kind,
            source="user",
            credential_source="secret_ref" if api_key else "none",
            base_url=effective_base_url,
            default_model=effective_default,
            model_ids=json.dumps(model_ids_list) if model_ids_list else None,
            secret_ref=secret_ref,
            enabled=True,
            is_default=False,
            deletable=True,
            # api_key path went through discover_models successfully → ``success``.
            # no-key path stays ``never`` until the user comes back and adds one.
            test_status="success" if api_key else "never",
            protocol=protocol,
        )
        await self._ds.create(user_id, row)

        if len([c for c in await self._ds.list_providers(user_id) if c.enabled]) == 1:
            await self._set_default_internal(user_id, row.id)

        self._bus.publish("provider.created", provider_id=row.id)
        return _row_to_detail(row)

    async def update_provider(
        self,
        user_id: str,
        provider_id: str,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
        protocol: str | None = None,
        auth_type: str | None = None,
        models: list[str] | None = None,
    ) -> LLMChannelDetail:
        await self._guard_not_system(provider_id)
        row = await self._ds.get_by_id(user_id, provider_id)
        if not row:
            # Edit dialog on an unconfigured built-in subscription template: no
            # row exists until login. The OAuth dialog persists nothing (api key
            # / endpoint / models are all hidden), so an empty save is a harmless
            # no-op — return the virtual detail rather than 404, matching the
            # pre-virtualization seeded-row behaviour. A patch that DOES carry a
            # change still 404s: log the channel in first so a real row exists.
            no_changes = not any(
                (name, base_url, api_key, default_model, protocol, auth_type, models is not None)
            )
            if no_changes:
                virtual = _virtual_builtin_subscription_detail(provider_id)
                if virtual is not None:
                    return virtual
            raise ProviderNotFound(f"Provider {provider_id!r} not found")

        if row.source == "managed":
            if name or base_url or api_key:
                from valuz_agent.modules.providers.errors import BadRequestError

                raise BadRequestError("Managed provider: only default_model can be changed")
            if default_model:
                row.default_model = default_model
                row.updated_at = now_ms()
                await self._ds.update(row)
                return _row_to_detail(row)

        if name:
            row.name = name.strip()
        if base_url:
            row.base_url = base_url
        if default_model:
            row.default_model = default_model
        if protocol:
            row.protocol = protocol
        if models is not None and row.provider_kind == "compatible":
            cleaned = [m.strip() for m in models if isinstance(m, str) and m.strip()]
            if not cleaned:
                from valuz_agent.modules.providers.errors import BadRequestError

                raise BadRequestError("自定义通道至少需要 1 个模型 id")

            # Re-ping each model so we never persist ids we know don't
            # work. Uses whatever api_key landed in this same update —
            # if the caller supplied a new key it overrides the stored
            # one for the validation step; otherwise we pull the
            # currently-stored key out of secret_store.
            stripped_new_key = (api_key or "").strip() if api_key else None
            effective_key = stripped_new_key or (
                self._secrets.get(user_id, row.secret_ref) if row.secret_ref else None
            )
            effective_url = (base_url or row.base_url or "").strip()
            effective_proto = protocol or row.protocol
            if effective_key and effective_url:
                protocol_for_ping = _DISCOVERY_PROTOCOL_MAP.get(effective_proto or "") or "openai"
                batch = await ping_credentials_batch(
                    base_url=effective_url,
                    api_key=effective_key,
                    protocol=protocol_for_ping,
                    models=cleaned,
                )
                if not batch.ok:
                    summary = "；".join(f"{m}: {r}" for m, r in batch.failed)
                    raise ModelDiscoveryError(f"所有模型连接测试均失败：{summary}")
                cleaned = list(batch.ok)

            row.model_ids = json.dumps(cleaned)
            # Keep default_model coherent — if the user removed it from
            # the list, fall back to the first remaining entry.
            if row.default_model not in cleaned:
                row.default_model = cleaned[0]
        if api_key:
            if row.secret_ref:
                self._secrets.put(user_id, row.secret_ref, api_key.strip())
            else:
                row.secret_ref = f"channel/{uuid4().hex[:12]}"
                self._secrets.put(user_id, row.secret_ref, api_key.strip())
                row.credential_source = "secret_ref"
            row.test_status = "never"
            # Setting an api_key explicitly opts the provider into the api_key
            # auth path even if it had been flipped to ``oauth`` earlier (e.g.
            # the user ran ``claude /login`` once, then later pasted a real
            # api key). Keep auth_type and credential_source consistent.
            row.auth_type = "api_key"
        if auth_type is not None:
            if auth_type not in ("api_key", "oauth"):
                from valuz_agent.modules.providers.errors import BadRequestError

                raise BadRequestError(f"Invalid auth_type: {auth_type!r}")
            row.auth_type = auth_type
            if auth_type == "oauth":
                # OAuth flow handles auth out-of-band; clear stale per-key
                # test_status so the UI doesn't show a leftover failure badge.
                row.test_status = "never"
                row.test_error = None

        row.updated_at = now_ms()
        await self._ds.update(row)
        self._bus.publish("provider.updated", provider_id=row.id)
        return _row_to_detail(row)

    async def discover_models(self, user_id: str, provider_id: str) -> dict[str, Any]:
        """Probe the provider's upstream for the available model list.

        System providers reject this (registry-backed; no upstream to
        probe). The route layer maps ``SystemProviderImmutable`` to 409.

        Calls ``GET <base_url>/v1/models`` (OpenAI-compatible) or the
        Anthropic equivalent, then merges discovered ids into
        ``provider.model_options`` *without* clobbering anything the user
        has already added by hand.

        Errors that the user can act on (no API key on this provider,
        upstream returned 401/404, timeout, ...) are raised as
        ``ModelDiscoveryError`` and translated to 502 by the router.
        ``ProviderNotFound`` becomes 404.
        """
        await self._guard_not_system(provider_id)
        row = await self._ds.get_by_id(user_id, provider_id)
        if not row:
            raise ProviderNotFound(f"Provider {provider_id!r} not found")

        if row.credential_source != "secret_ref" or not row.secret_ref:
            raise ModelDiscoveryError(
                "provider has no API key — auto-detect only works on api_key providers; "
                "add models manually instead"
            )

        api_key = self._secrets.get(user_id, row.secret_ref)
        if not api_key:
            raise ModelDiscoveryError("provider's API key is missing from secret store")

        base_url = (row.base_url or "").strip()
        if not base_url:
            raise ModelDiscoveryError("provider has no base_url configured")

        protocol = _resolve_discovery_protocol(row)

        # ``discover_models`` is async; the service now runs on the event
        # loop, so the coroutine is awaited directly.
        discovered = await discover_models(
            base_url=base_url,
            api_key=api_key,
            protocol=protocol,
        )

        # Authoritative replace, not union. The previous union-with-
        # existing kept stale ids around forever — including the
        # descriptor's hardcoded fallback labels (e.g.
        # ``deepseek-v4-pro[1m]``) that the real upstream doesn't list.
        # If the user really wants a manually-typed id to stick around,
        # the right place for that is a future "edit model list" UI;
        # silently preserving every id we've ever seen is too sticky.
        merged = sorted(set(discovered))
        row.model_ids = json.dumps(merged)
        row.updated_at = now_ms()
        await self._ds.update(row)
        self._bus.publish("provider.updated", provider_id=row.id)

        return {
            "provider_id": row.id,
            "discovered": list(discovered),
            "merged": merged,
        }

    async def delete_provider(self, user_id: str, provider_id: str) -> None:
        await self._guard_not_system(provider_id)
        row = await self._ds.get_by_id(user_id, provider_id)
        if not row:
            raise ProviderNotFound(f"Provider {provider_id!r} not found")
        if row.source != "user" or not row.deletable:
            raise ProviderNotDeletable(f"Provider {provider_id!r} cannot be deleted")

        if row.secret_ref:
            self._secrets.delete(user_id, row.secret_ref)

        was_default = row.is_default
        await self._ds.delete(user_id, provider_id)

        if was_default:
            fallback = await self._ds.get_default(user_id) or await self._first_enabled(user_id)
            if fallback:
                await self._set_default_internal(user_id, fallback.id)

        self._bus.publish("provider.deleted", provider_id=provider_id)

    async def enable_provider(self, user_id: str, provider_id: str) -> LLMChannelDetail:
        """Mark an OAuth/subscription provider as enabled.

        For ``auth_type="oauth"`` providers (e.g. ``ch-claude-subscription``
        / ``ch-codex-subscription``) the host has no API key — credentials
        live in the respective CLI's keychain.  ``enable_provider`` flips
        ``enabled=True`` and sets ``credential_source="cli_keychain"`` to
        signal that the user has completed the out-of-band login.  Calling
        this on an already-enabled row is a no-op (idempotent).

        Built-ins aren't seeded, so a subscription login is the FIRST time a row
        exists. When no row matches ``provider_id``, treat it as a built-in
        catalog id (e.g. ``ch-claude-subscription``) and materialize a row on
        demand (``_materialize_builtin_subscription``); a non-builtin id still
        404s.

        System-managed providers are immutable (403 via ``SystemProviderImmutable``).
        """
        await self._guard_not_system(provider_id)
        row = await self._ds.get_by_id(user_id, provider_id)
        if not row:
            materialized = await self._materialize_builtin_subscription(user_id, provider_id)
            if materialized is None:
                raise ProviderNotFound(f"Provider {provider_id!r} not found")
            # The frontend calls enable right after detecting a fresh CLI login —
            # drop the cached logged-out state so the new models surface at once.
            _invalidate_login_cache(materialized.provider_kind)
            self._bus.publish("provider.updated", provider_id=materialized.id)
            return _row_to_detail(materialized)

        if row.enabled and row.credential_source == "cli_keychain":
            # Already in the desired state — idempotent, return current detail.
            return _row_to_detail(row)

        row.enabled = True
        if row.auth_type == "oauth":
            row.credential_source = "cli_keychain"
        row.updated_at = now_ms()
        await self._ds.update(row)
        _invalidate_login_cache(row.provider_kind)
        self._bus.publish("provider.updated", provider_id=row.id)
        return _row_to_detail(row)

    async def _materialize_builtin_subscription(
        self, user_id: str, provider_id: str
    ) -> ProviderRow | None:
        """Create (or reuse) the row for a built-in OAuth subscription channel.

        ``provider_id`` is a well-known catalog id (e.g. ``ch-claude-subscription``).
        Built-ins aren't seeded, so the first ``claude``/``codex`` ``/login`` is
        when the row first appears. Idempotent by *kind*: if the user already has
        a row of that kind (a prior login, or a legacy seeded row) it is enabled
        and returned rather than duplicated — the materialized row carries a fresh
        uuid id, not the catalog id, so a lookup by id alone can't dedupe.

        Returns ``None`` when ``provider_id`` isn't a known OAuth built-in or
        subscription login is disabled for this deployment — the caller maps that
        to ``ProviderNotFound``.
        """
        from valuz_agent.infra.config import settings
        from valuz_agent.seeds._io import load_provider_seeds

        if not settings.subscription_login_enabled:
            return None
        entry = next((e for e in load_provider_seeds().providers if e.id == provider_id), None)
        if entry is None:
            return None
        descriptor = _PROVIDER_MAP.get(entry.provider_kind)
        if descriptor is None or descriptor.auth_type != "oauth":
            return None

        # Idempotency by kind — reuse an existing row (prior login / legacy seed).
        for existing in await self._ds.list_providers(user_id):
            if existing.provider_kind == entry.provider_kind:
                existing.enabled = True
                existing.credential_source = "cli_keychain"
                existing.updated_at = now_ms()
                await self._ds.update(existing)
                return existing

        row = ProviderRow(
            name=entry.name,
            provider_kind=entry.provider_kind,
            source="user",
            credential_source="cli_keychain",
            base_url=descriptor.default_base_url or None,
            default_model=descriptor.default_model or None,
            model_ids=None,
            enabled=True,
            is_default=False,
            deletable=True,
            test_status="never",
            auth_type="oauth",
        )
        await self._ds.create(user_id, row)
        return row

    async def set_default(
        self, user_id: str, provider_id: str, *, default_model: str | None = None
    ) -> None:
        # System providers can't carry the ``is_default`` flag on the
        # providers table (no row exists). Users pin a system provider
        # as default through the settings-preferences path
        # (``PATCH /v1/settings/model-defaults``) instead.
        await self._guard_not_system(provider_id)
        row = await self._ds.get_by_id(user_id, provider_id)
        if not row:
            # Built-in CLI-subscription channels (claude/codex ``/login``) have
            # no DB row until first use — they're surfaced as virtual templates
            # keyed by their catalog id, so selecting one as the default would
            # otherwise 404. Materialize it on demand (mirrors enable_provider).
            # The materialized row carries a fresh uuid id, NOT the catalog id,
            # so every write below keys off ``row.id`` rather than the
            # ``provider_id`` argument.
            row = await self._materialize_builtin_subscription(user_id, provider_id)
            if row is None:
                raise ProviderNotFound(f"Provider {provider_id!r} not found")
        if not row.enabled:
            raise NoAvailableProvider(f"Provider {provider_id!r} is disabled")
        resolved_id = row.id
        await self._set_default_internal(user_id, resolved_id)

        # Optionally update the provider row's default_model.
        if default_model is not None:
            updated_row = await self._ds.get_by_id(user_id, resolved_id)
            if updated_row:
                updated_row.default_model = default_model
                updated_row.updated_at = now_ms()
                await self._ds.update(updated_row)

        # Sync the app-setting keys so model_resolver (reads is_default +
        # default_model) and the Composer/SettingsPage (reads
        # KEY_DEFAULT_PROVIDER_ID / KEY_DEFAULT_MODEL) agree on the same
        # default.  Runs inside the same AsyncSession that owns the
        # ProviderDatastore so there's no cross-uow boundary risk.
        from valuz_agent.modules.settings.preferences import (
            set_default_model,
            set_default_provider_id,
        )

        db = self._ds._db  # noqa: SLF001 — sanctioned cross-module db reuse (mirrors resolve_infer_config)
        await set_default_provider_id(db, resolved_id)
        effective_model = default_model if default_model is not None else row.default_model
        if effective_model:
            await set_default_model(db, effective_model)

        self._bus.publish("provider.default.changed", provider_id=resolved_id)

    # ── Resolution ───────────────────────────────────────────────

    async def resolve_default_provider(self, user_id: str) -> ProviderRow:
        row = await self._ds.get_default(user_id)
        if row:
            return row
        row = await self._first_enabled(user_id)
        if row:
            return row
        raise NoAvailableProvider("No available model provider configured")

    async def resolve_provider_for_model(self, user_id: str, model_id: str) -> ProviderRow | None:
        """Find the configured provider that should host ``model_id``.

        Deterministic resolution — no fallback walk. Picks the first enabled,
        credential-bearing provider whose ``default_model`` equals ``model_id``
        OR whose ``model_ids`` lists it. Returns ``None`` when no
        configured provider hosts the model — caller decides whether that's a
        hard error (preferred) or a soft fall-through to ``ANTHROPIC_API_KEY``
        already in env (dev convenience).

        Used at session creation time so the user's explicit model pick
        binds to the correct provider without surprising the runtime layer
        with "any credential-bearing provider" guesses.

        REP-107: OAuth subscription providers (claude /login, codex /login)
        store nothing on the host — credentials live in the corresponding
        CLI's keychain — so they read as ``credential_source="none"`` from
        the API. They ARE credential-bearing in practice; trust the
        ``auth_type=="oauth"`` flag and let them through.
        """
        if not model_id:
            return None

        def _has_creds(row: ProviderRow) -> bool:
            if row.credential_source != "none":
                return True
            return row.auth_type == "oauth"

        rows = list(await self._ds.list_providers(user_id))
        # 1) Exact default_model match wins (the provider's primary model).
        for row in rows:
            if not row.enabled:
                continue
            if row.default_model != model_id:
                continue
            if not _has_creds(row):
                continue
            return row
        # 2) Otherwise the first enabled provider that lists the model id in
        #    its options AND has credentials. Goes through
        #    ``_resolve_model_options`` (same resolution as the list/detail
        #    endpoints) so rows with ``model_ids IS NULL`` — notably the
        #    OAuth subscription rows, which never snapshot their recommended
        #    list — fall back to the live descriptor instead of becoming
        #    unresolvable: whatever the picker shows must bind here too.
        for row in rows:
            if not row.enabled or not _has_creds(row):
                continue
            if model_id in _resolve_model_options(row):
                return row
        return None

    async def resolve_infer_config(
        self,
        user_id: str,
        provider_id: str | None = None,
        locked_model_id: str | None = None,
    ) -> InferConfig:
        if provider_id:
            row = await self._ds.get_by_id(user_id, provider_id)
            if not row:
                raise ProviderNotFound(f"Provider {provider_id!r} not found")
        else:
            row = await self.resolve_default_provider(user_id)

        api_key: str | None = None
        auth_type = "none"
        if row.credential_source == "secret_ref" and row.secret_ref:
            api_key = self._secrets.get(user_id, row.secret_ref)
            auth_type = "api_key"

        # Protocol override drives the wire shape used during connection
        # test / inference.
        provider_descriptor = _PROVIDER_MAP.get(row.provider_kind)
        protocol_selectable = bool(
            provider_descriptor and provider_descriptor.supports_protocol_selection
        )
        effective_kind = row.provider_kind
        if (
            row.provider_kind == "compatible" or protocol_selectable
        ) and row.protocol == "anthropic":
            effective_kind = "anthropic"

        return InferConfig(
            provider_id=row.id,
            provider_name=row.name,
            provider_kind=effective_kind,
            model_id=locked_model_id or row.default_model or "",
            base_url=row.base_url,
            auth_type=auth_type,
            api_key=api_key,
            auth_ref=row.secret_ref,
        )

    # ── Connection Test ──────────────────────────────────────────

    async def test_provider(self, user_id: str, provider_id: str) -> ConnectionTestResult:
        await self._guard_not_system(provider_id)
        row = await self._ds.get_by_id(user_id, provider_id)
        if not row:
            raise ProviderNotFound(f"Provider {provider_id!r} not found")
        if row.auth_type == "oauth":
            from valuz_agent.modules.providers.errors import (
                ProviderTestNotSupported,
            )

            raise ProviderTestNotSupported(
                "OAuth providers authenticate through the provider CLI; "
                "connection test is not applicable"
            )

        infer = await self.resolve_infer_config(user_id, provider_id=provider_id)
        provider = _PROVIDER_MAP.get(row.provider_kind)

        start = time.monotonic()
        try:
            # Pure network probe (sync httpx), no DB — run on the generic
            # threadpool, not the dedicated DB-writer thread.
            result = await asyncio.to_thread(_test_provider_connection, infer, provider)
        except Exception as exc:
            result = ConnectionTestResult(success=False, error_message=str(exc))
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result.latency_ms = elapsed_ms

        row.test_status = "success" if result.success else "failed"
        row.test_error = result.error_message
        row.tested_at = now_ms()
        row.updated_at = now_ms()
        await self._ds.update(row)

        return result

    async def validate_credentials(
        self,
        user_id: str,
        provider_kind: str,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        protocol: str | None = None,
    ) -> ConnectionTestResult:
        provider = get_provider(provider_kind)
        url = base_url or provider.default_base_url
        model = default_model or provider.default_model

        # Same protocol-override rule as ``resolve_infer_config``: ``compatible``
        # always honours the picked protocol, and any provider that declares
        # ``supports_protocol_selection`` (DeepSeek, Moonshot, ...) does too.
        effective_kind = provider_kind
        if (
            provider_kind == "compatible" or provider.supports_protocol_selection
        ) and protocol == "anthropic":
            effective_kind = "anthropic"

        infer = InferConfig(
            provider_id="__validate__",
            provider_name="validate",
            provider_kind=effective_kind,
            model_id=model or "",
            base_url=url,
            auth_type="api_key" if api_key else "none",
            api_key=api_key.strip() if api_key else None,
        )

        start = time.monotonic()
        try:
            # Pure network probe (sync httpx), no DB — run on the generic
            # threadpool, not the dedicated DB-writer thread.
            result = await asyncio.to_thread(_test_provider_connection, infer, provider)
        except Exception as exc:
            result = ConnectionTestResult(success=False, error_message=str(exc))
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result.latency_ms = elapsed_ms
        return result

    # ── Internal ─────────────────────────────────────────────────

    async def _set_default_internal(self, user_id: str, provider_id: str) -> None:
        await self._ds.clear_default(user_id)
        row = await self._ds.get_by_id(user_id, provider_id)
        if row:
            row.is_default = True
            row.updated_at = now_ms()
            await self._ds.update(row)

    async def _first_enabled(self, user_id: str) -> ProviderRow | None:
        for row in await self._ds.list_providers(user_id):
            if row.enabled:
                return row
        return None


# ── Connection Test Implementation ──────────────────────────────────

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 10


def _test_provider_connection(
    infer: InferConfig, provider: ProviderDescriptor | None
) -> ConnectionTestResult:
    if not infer.base_url:
        return ConnectionTestResult(success=False, error_message="No base URL configured")

    headers: dict[str, str] = {}
    if infer.api_key:
        if infer.provider_kind == "anthropic":
            headers["x-api-key"] = infer.api_key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {infer.api_key}"

    if infer.provider_kind == "anthropic":
        base = infer.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        url = f"{base}/messages"
        body: dict[str, Any] = {
            "model": infer.model_id or "claude-sonnet-4-6",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "Hi"}],
        }
    else:
        url = f"{infer.base_url.rstrip('/')}/chat/completions"
        body = {
            "model": infer.model_id or "gpt-4o",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "Hi"}],
        }

    try:
        resp = httpx.post(
            url,
            headers=headers,
            json=body,
            timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=5.0, pool=15.0),
        )
        if resp.status_code < 400:
            return ConnectionTestResult(success=True)
        return ConnectionTestResult(
            success=False,
            error_message=f"HTTP {resp.status_code}: {resp.text[:200]}",
        )
    except httpx.TimeoutException:
        return ConnectionTestResult(success=False, error_message="Connection timed out")
    except httpx.ConnectError as exc:
        return ConnectionTestResult(success=False, error_message=f"Connection failed: {exc}")


# MODEL_CATALOG is gone in the kernel as of 39ec84c — runtime
# dispatch is now per-session ``api_protocol`` rather than a curated list,
# so provider CRUD no longer needs to push model ids into the kernel.
# See ``valuz_agent.adapters.provider_resolver`` for the new flow.

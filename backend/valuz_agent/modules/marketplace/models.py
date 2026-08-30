"""Marketplace DTOs — mirror ``api/openapi.yaml`` (Marketplace* schemas)."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

MarketplaceItemType = Literal[
    "skill", "agent_template", "agent_team_template", "connector", "plugin"
]
# ``source`` is an OPEN string on the wire. Where an item comes from is data
# the market index grows over time (a new upstream store, a new ingest) and
# must never require a client release to render — a closed enum here once
# made the whole skills tab fail the moment the index published a source this
# build had not heard of. ``KNOWN_MARKETPLACE_SOURCES`` only documents the
# values in use; the UI renders anything else with a generic label.
MarketplaceSource = str
KNOWN_MARKETPLACE_SOURCES: tuple[str, ...] = (
    "skillhub",
    "valuz_official",
    "modelscope",
    "redskill",
    "plugin",
)
# Derived from a plugin's components — never authored: ``skills_only`` (a "skill
# suite") vs ``with_connectors`` (declares ``mcp.json`` servers).
MarketplacePluginComposition = Literal["skills_only", "with_connectors"]
MarketplaceBadge = Literal[
    "free_install",
    "requires_api_key",
    "third_party_cost",
    "reviewed_skillhub",
    "reviewed_valuz",
    "community",
    "verified",
    "locked",
]
_BADGE_VALUES: frozenset[str] = frozenset(get_args(MarketplaceBadge))
MarketplaceInstallTarget = Literal[
    "skill_library",
    "agent_library",
    "agent_library_project",
    "connector_library",
    "plugin_library",
]
ConnectorRequirementKind = Literal["required", "optional", "api_key", "cost"]
MarketplaceConnectorFieldTarget = Literal["env", "header", "param"]


class MarketplaceStats(BaseModel):
    downloads: int | None = None
    stars: int | None = None
    installs: int | None = None
    views: int | None = None


class MarketplaceTeamMember(BaseModel):
    slug: str | None = None
    name: str
    role: str
    lead: bool = False
    skill_count: int | None = None


class MarketplacePluginMember(BaseModel):
    """One member of a ``plugin`` item (detail only) — a skill or an MCP
    server / connector the plugin declares. ``path`` is the member's location
    inside the package (``skills/<slug>`` / ``mcp.json#<name>``)."""

    kind: Literal["skill", "connector"]
    slug: str
    name: str
    description: str | None = None
    meta_version: str | None = None
    path: str | None = None


class MarketplaceConnectorRequirement(BaseModel):
    name: str
    requirement: ConnectorRequirementKind


class MarketplaceConnectorConfigField(BaseModel):
    key: str
    name: str
    target: MarketplaceConnectorFieldTarget
    label: str
    required: bool = False
    secret: bool = False
    placeholder: str | None = None
    prefix: str | None = None


class MarketplaceConnectorConfig(BaseModel):
    slug: str
    transport: Literal["stdio", "http", "sse"]
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    auth_type: Literal["none", "bearer", "oauth"] = "none"
    oauth_authorization_endpoint: str | None = None
    oauth_token_endpoint: str | None = None
    oauth_registration_endpoint: str | None = None
    oauth_scopes: list[str] = Field(default_factory=list)
    fields: list[MarketplaceConnectorConfigField] = Field(default_factory=list)
    supported: bool = True
    unsupported_reason: str | None = None


class MarketplaceFileEntry(BaseModel):
    path: str
    size: int | None = None
    sha256: str | None = None


class MarketplaceSecurityProviderReport(BaseModel):
    provider: str
    status: str
    url: str | None = None


class MarketplaceSecurityReport(BaseModel):
    status: Literal["benign", "unknown", "flagged"]
    summary: str
    reports: list[MarketplaceSecurityProviderReport] = Field(default_factory=list)


class MarketplaceEvaluationDimension(BaseModel):
    key: Literal["trust", "reliability", "adaptability", "convention", "effectiveness"]
    code: Literal["T", "R", "A", "C", "E"]
    label: str
    score: float | None = None
    summary: str | None = None


class MarketplaceEvaluationReport(BaseModel):
    system: Literal["TRACE"] = "TRACE"
    score: float | None = None
    rating: str | None = None
    summary: str | None = None
    dimensions: list[MarketplaceEvaluationDimension] = Field(default_factory=list)


class MarketplaceItem(BaseModel):
    """The normalized card shape shared by every marketplace source.

    ``id`` is a stable ``{source}:{type}:{ref}`` string, e.g.
    ``skillhub:skill:agent-memory`` / ``valuz:agent:meeting-notes`` /
    ``valuz:team:investment``.
    """

    id: str
    type: MarketplaceItemType
    source: MarketplaceSource
    source_ref: str
    title: str
    subtitle: str | None = None
    description: str
    icon: str | None = None
    category: str | None = None
    category_label: str | None = None
    subcategories: list[str] = Field(default_factory=list)
    badges: list[MarketplaceBadge] = Field(default_factory=list)
    stats: MarketplaceStats = Field(default_factory=MarketplaceStats)
    version: str | None = None
    runtime: str | None = None
    skill_count: int | None = None
    # ``plugin`` items only — component counts + derived composition.
    connector_count: int | None = None
    composition: MarketplacePluginComposition | None = None
    # Team cards carry the member-agent roster; plugin DETAILS carry the
    # skill / connector member list (same field, discriminated by shape).
    members: Sequence[MarketplaceTeamMember | MarketplacePluginMember] | None = None
    install_target: MarketplaceInstallTarget
    installed: bool = False
    locked: bool = False

    @field_validator("badges", mode="before")
    @classmethod
    def _drop_unknown_badges(cls, value: Any) -> Any:
        """Badges are a closed set on the client (each one has a style +
        label); an index that starts sending a new badge must not make the
        item unrenderable — unknown badges are silently dropped."""
        if not isinstance(value, list):
            return value
        return [badge for badge in value if badge in _BADGE_VALUES]


class MarketplaceItemDetail(MarketplaceItem):
    owner: str | None = None
    origin_url: str | None = None
    updated_at: str | None = None
    instructions: str | None = None
    workflow: list[str] | None = None
    deliverables: list[str] | None = None
    usage_notes: list[str] | None = None
    bound_skills: list[str] | None = None
    connectors: list[MarketplaceConnectorRequirement] | None = None
    files: list[MarketplaceFileEntry] | None = None
    security: MarketplaceSecurityReport | None = None
    evaluation: MarketplaceEvaluationReport | None = None
    connector_config: MarketplaceConnectorConfig | None = None
    # ``plugin`` items — the package's ``plugin.json`` object (Agent Plugins
    # 1.0.0 manifest) as published by the index.
    plugin_manifest: dict[str, Any] | None = None
    # Opaque, type-varies-by-``type`` install payload from the market index:
    # skill → {download_url, sha256?, size_bytes?}; agent_template →
    # an AgentTemplateDef-shaped object; agent_team_template → a pack
    # manifest object (skill deps rewritten to {slug, source:"url",
    # download_url}); plugin → {download_url, sha256?, size_bytes?} of the
    # Agent Plugins layout zip. Never produced locally — only the index sets it.
    install_manifest: dict[str, Any] | None = None


class MarketplaceItemList(BaseModel):
    items: list[MarketplaceItem]
    total: int
    page: int
    page_size: int
    degraded: bool = False

    @classmethod
    def from_index_payload(cls, payload: Mapping[str, Any]) -> MarketplaceItemList:
        """Parse one market-index page, keeping every item this build can
        render and dropping the rest (unknown ``type`` / ``install_target`` /
        malformed row) instead of failing the whole page. New item kinds
        reach the index before every client has updated; an old build must
        simply not see them. ``total`` stays the index's count so paging is
        unaffected."""
        raw_items = payload.get("items")
        items: list[MarketplaceItem] = []
        if isinstance(raw_items, list):
            for raw in raw_items:
                try:
                    items.append(MarketplaceItem.model_validate(raw))
                except ValidationError as exc:
                    ref = raw.get("id") if isinstance(raw, Mapping) else None
                    logger.warning(
                        "marketplace: dropping index item %r this client cannot render: %s",
                        ref,
                        exc.errors()[0].get("msg") if exc.errors() else exc,
                    )
        envelope = {**payload, "items": []}
        result = cls.model_validate(envelope)
        result.items = items
        return result


class MarketplaceSubcategory(BaseModel):
    key: str
    label: str
    count: int | None = None


class MarketplaceCategory(BaseModel):
    key: str
    label: str
    count: int | None = None
    subcategories: list[MarketplaceSubcategory] = Field(default_factory=list)


class MarketplaceCategoryList(BaseModel):
    categories: list[MarketplaceCategory]
    degraded: bool = False


class MarketplaceInstallResult(BaseModel):
    item_id: str
    status: Literal["installed", "already_installed"]
    installed_ref: str | None = None
    created: int | None = None
    skipped: int | None = None

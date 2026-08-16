"""Marketplace DTOs — mirror ``api/openapi.yaml`` (Marketplace* schemas)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

MarketplaceItemType = Literal[
    "skill", "agent_template", "agent_team_template", "connector", "plugin"
]
MarketplaceSource = Literal["skillhub", "valuz_official", "modelscope", "redskill", "pluginmarket"]
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


class MarketplaceSubcategory(BaseModel):
    key: str
    label: str


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

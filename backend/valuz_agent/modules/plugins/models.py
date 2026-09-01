"""Plugin ORM rows (``valuz_plugin`` / ``valuz_plugin_component``) and the
Pydantic API views (mirror ``api/openapi.yaml`` → ``Plugin*`` schemas).

Ownership model (design §4.1): a plugin row is the install unit; the
component rows are the many-to-many membership between a plugin and the
user's library resources (skills by slug in the user skill root, connectors
by slug). The ``skills`` / ``connectors`` tables carry NO plugin column —
membership (badges, reference counting) is always read from here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import Boolean, String, Text, UniqueConstraint, false, true
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin

PluginSource = Literal[
    "market", "local_dir", "zip", "url", "claude_plugin", "codebuddy_plugin", "builtin"
]
PluginComposition = Literal["skills_only", "with_connectors"]
PluginMemberKind = Literal["skill", "connector"]
PluginOnConflict = Literal["skip", "overwrite"]
PluginInstallStatus = Literal["installed", "updated", "already_installed"]
PluginKeepReason = Literal["referenced_by_other_plugin", "standalone"]
# ``installed`` — this plugin brought the resource into the library;
# ``linked`` — the resource already existed (standalone or via another plugin).
PluginComponentOrigin = Literal["installed", "linked"]


# ---------------------------------------------------------------------------
# ORM
# ---------------------------------------------------------------------------


class PluginRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """One installed plugin (per owner, unique by ``name``)."""

    __tablename__ = "valuz_plugin"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_valuz_plugin_user_name"),)

    # ``plugin.json.name`` (spec §5.5 — max 64 chars, path-safe by construction).
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(64), default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # market | local_dir | zip | url | claude_plugin | codebuddy_plugin
    source: Mapped[str] = mapped_column(String(32))
    # market item id / directory path / URL — ``None`` for a one-off zip.
    source_ref: Mapped[str | None] = mapped_column(Text, default=None)
    # agent_plugins | claude_plugin | codebuddy_plugin — the layout it was read from.
    format: Mapped[str] = mapped_column(String(32), default="agent_plugins")
    # The normalized Agent Plugins ``plugin.json`` object (JSON text) — legacy
    # manifests are kept verbatim under ``extensions["io.valuz.agent"]``.
    manifest_json: Mapped[str] = mapped_column(Text)
    # The normalized ``mcp.json`` object (JSON text), ``None`` when the plugin
    # declares no MCP servers. Portable form (placeholders unexpanded) so the
    # export writes back exactly what was declared.
    mcp_json: Mapped[str | None] = mapped_column(Text, default=None)
    # PLUGIN_ROOT / PLUGIN_DATA for this install (absolute).
    root_path: Mapped[str] = mapped_column(Text)
    data_path: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    # Builtin plugins (``source="builtin"``) are app-managed: disable-able but
    # not deletable — DELETE returns 409 and points at the enabled switch
    # (docs/design/builtin-resources in the commercial repo, §6.5.4).
    deletable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )


class PluginComponentRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Membership of one library resource (skill / connector) in one plugin."""

    __tablename__ = "valuz_plugin_component"
    __table_args__ = (
        UniqueConstraint("plugin_id", "kind", "slug", name="uq_valuz_plugin_component_member"),
    )

    plugin_id: Mapped[str] = mapped_column(String(36), index=True)
    # skill | connector
    kind: Mapped[str] = mapped_column(String(16))
    # Library slug (skill directory name / connector slug).
    slug: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # Skill ``metadata.version`` (display only); connectors have none.
    meta_version: Mapped[str | None] = mapped_column(String(64), default=None)
    # Content hash of the member as DECLARED by the plugin at install/update
    # time (skill directory hash / normalized MCP server entry hash).
    content_hash: Mapped[str] = mapped_column(String(64))
    # installed | linked — see module docstring / reference counting.
    origin: Mapped[str] = mapped_column(String(16), default="linked")
    # The library copy differs from what this plugin declares (a conflict the
    # user chose to skip). Cleared when the two agree again.
    content_differs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Set while THIS plugin's disable turned the member off, so enable only
    # re-enables what the plugin disabled (a member the user disabled on its
    # own stays disabled).
    disabled_by_plugin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )


# ---------------------------------------------------------------------------
# API views (contract: PluginMember / PluginView / PluginInstallResult / …)
# ---------------------------------------------------------------------------


class PluginAuthor(BaseModel):
    name: str | None = None
    email: str | None = None
    url: str | None = None


class PluginMember(BaseModel):
    kind: PluginMemberKind
    slug: str
    name: str
    description: str | None = None
    meta_version: str | None = None
    content_hash: str
    # The resource currently exists in the user's library.
    installed: bool = False
    # The library copy differs from what the plugin declares.
    content_differs: bool = False


class PluginView(BaseModel):
    id: str
    name: str
    version: str | None = None
    description: str | None = None
    author: PluginAuthor | None = None
    homepage: str | None = None
    repository: str | None = None
    license: str | None = None
    keywords: list[str] = Field(default_factory=list)
    source: PluginSource
    source_ref: str | None = None
    composition: PluginComposition
    enabled: bool = True
    deletable: bool = True
    members: list[PluginMember] = Field(default_factory=list)
    skill_count: int = 0
    connector_count: int = 0
    root_path: str
    # ISO-8601 UTC instants.
    installed_at: str
    updated_at: str
    update_available: bool | None = None
    # True when any member skill is protected — a plugin is exactly as protected
    # as the strictest thing inside it. DERIVED per request, never stored: a
    # member that becomes protected later must not leave a stale ``False`` on the
    # parent. Clients use it to stop offering actions the server will refuse
    # (``export`` returns 403 for these); it does not hide the plugin itself,
    # whose name, description and member list stay visible.
    protected: bool = False


class PluginSkippedMember(BaseModel):
    kind: Literal["skill", "connector", "skills", "mcp"]
    slug: str
    reason: str


class PluginConflictMember(BaseModel):
    kind: PluginMemberKind
    slug: str


class PluginInstallResult(BaseModel):
    plugin: PluginView
    status: PluginInstallStatus
    skipped: list[PluginSkippedMember] = Field(default_factory=list)
    conflicts: list[PluginConflictMember] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PluginPreview(BaseModel):
    """``POST /v1/plugins/preview`` — no side effects."""

    manifest: dict[str, Any]
    format: Literal["agent_plugins", "claude_plugin", "codebuddy_plugin"]
    composition: PluginComposition
    members: list[PluginMember] = Field(default_factory=list)
    conflicts: list[PluginConflictMember] = Field(default_factory=list)
    skipped: list[PluginSkippedMember] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # A same-name plugin already installed: ``None`` (fresh), ``"same_source"``
    # (install = update) or ``"other_source"`` (install would 409).
    existing: Literal["same_source", "other_source"] | None = None


class PluginInstallRequest(BaseModel):
    path: str | None = None
    url: str | None = None
    market_item_id: str | None = None
    on_conflict: PluginOnConflict = "skip"


class PluginUpdateRequest(BaseModel):
    on_conflict: PluginOnConflict = "skip"


class PluginRemovedMember(BaseModel):
    kind: PluginMemberKind
    slug: str


class PluginKeptMember(BaseModel):
    kind: PluginMemberKind
    slug: str
    reason: PluginKeepReason


class PluginUninstallResult(BaseModel):
    removed_members: list[PluginRemovedMember] = Field(default_factory=list)
    kept_members: list[PluginKeptMember] = Field(default_factory=list)


class PluginList(BaseModel):
    items: list[PluginView]


class PluginMembershipRef(BaseModel):
    id: str
    name: str


__all__ = [
    "PluginAuthor",
    "PluginComponentOrigin",
    "PluginComponentRow",
    "PluginComposition",
    "PluginConflictMember",
    "PluginInstallRequest",
    "PluginInstallResult",
    "PluginInstallStatus",
    "PluginKeepReason",
    "PluginKeptMember",
    "PluginList",
    "PluginMember",
    "PluginMemberKind",
    "PluginMembershipRef",
    "PluginOnConflict",
    "PluginPreview",
    "PluginRemovedMember",
    "PluginRow",
    "PluginSkippedMember",
    "PluginSource",
    "PluginUninstallResult",
    "PluginUpdateRequest",
    "PluginView",
]

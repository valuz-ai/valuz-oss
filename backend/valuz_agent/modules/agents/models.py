"""ORM models for the Agent and Project Member tables.

Agent (valuz_agent):
  Stateless blueprint layer — method, default runtime/model, skill refs,
  connector type declarations. Global across projects; MVP is official
  (read-only seed). Source-of-truth for ``deploy_agent``.

Project Member (valuz_project_member):
  Per-project mapping of a project-local handle ("agent_slug") to its
  source library agent (``source_agent_slug``). Sessions build their
  embedded config snapshot from the source row at creation time.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, CheckConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin


class AgentRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Stateless Agent — blueprint layer, global + official-only for MVP."""

    __tablename__ = "valuz_agent"
    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_valuz_agent_user_slug"),
        CheckConstraint(
            "kind IN ('system', 'standard')",
            name="ck_valuz_agent_kind",
        ),
        CheckConstraint(
            "resource_policy IN ('explicit', 'all_available')",
            name="ck_valuz_agent_resource_policy",
        ),
    )

    slug: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    runtime: Mapped[str] = mapped_column(String(64), default="claude_agent")
    model: Mapped[str] = mapped_column(String(128), default="claude-sonnet-4-6")
    # JSON list[str] of skill slugs referencing valuz_skill_index
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    # JSON list[str] of connector catalog slugs (types, not bound instances)
    connector_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    # JSON list[str] of knowledge-base ids allowed for this Agent. The docs MCP
    # performs authorization again at use time; this is only an explicit scope
    # definition, never copied document content.
    knowledge_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Default model provider id for instances. A model id alone is ambiguous —
    # the provider supplies base_url/api_key/protocol — so an agent carries
    # the (provider, model) pair. Nullable: official/seeded agents leave it
    # unset (provider ids are install-local) and rely on instance-time pinning.
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Default reasoning-effort budget (kernel ``ModelSettings.effort`` — one of
    # low/medium/high/xhigh/max) prefilled into instances at instantiate time.
    # Nullable: ``None`` means "no agent-level override" — the runtime falls
    # through to its SDK default. Project conversations read effort from the
    # bound agent, so this is the source of truth for that session's budget.
    effort: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # System identity and resource selection are separate from provenance.
    # ``kind`` has exactly two values: Valurion is ``system``; every other
    # Agent is ``standard``. ``resource_policy=all_available`` is reserved for
    # Valurion; ordinary Agents use their explicit resource fields.
    kind: Mapped[str] = mapped_column(String(16), default="standard")
    resource_policy: Mapped[str] = mapped_column(String(24), default="explicit")
    inherit_global_instructions: Mapped[bool] = mapped_column(Boolean, default=True)
    # Portable execution preference used by copy/export. Runtime enforcement
    # still happens at the session boundary.
    permission_mode: Mapped[str] = mapped_column(String(32), default="full_access")
    # Provenance: ``builtin`` for Valurion, ``user`` for newly-created/copy
    # rows. Legacy ``official``/``custom`` values remain readable.
    source: Mapped[str] = mapped_column(String(32), default="official")
    readonly: Mapped[bool] = mapped_column(Boolean, default=False)
    deletable: Mapped[bool] = mapped_column(Boolean, default=True)
    # Preset icon key or uploaded asset URL for the agent's avatar (08-agents-module
    # v2). v1 supports preset keys only; nullable, no default. Surfaced on the
    # agent identity panel + list cards.
    avatar: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ProjectMemberRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Per-project agent membership row — maps a slug handle to a library agent."""

    __tablename__ = "valuz_project_member"

    __table_args__ = (
        UniqueConstraint("project_id", "agent_slug", name="uq_project_member_ws_slug"),
    )

    project_id: Mapped[str] = mapped_column(String(36), index=True)
    # Project-local human handle — used as the ``agent`` param in dispatch calls
    agent_slug: Mapped[str] = mapped_column(String(128))
    # Provenance: which source agent was instantiated (NULL = created from blank)
    source_agent_slug: Mapped[str | None] = mapped_column(String(128), nullable=True)

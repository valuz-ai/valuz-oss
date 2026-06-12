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

from sqlalchemy import JSON, Boolean, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin


class AgentRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Stateless Agent — blueprint layer, global + official-only for MVP."""

    __tablename__ = "valuz_agent"

    slug: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    runtime: Mapped[str] = mapped_column(String(64), default="claude_agent")
    model: Mapped[str] = mapped_column(String(128), default="claude-sonnet-4-6")
    # JSON list[str] of skill slugs referencing valuz_skill_index
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    # JSON list[str] of connector catalog slugs (types, not bound instances)
    connector_types: Mapped[list[str]] = mapped_column(JSON, default=list)
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
    # "official" for MVP seed rows; "user" reserved for future user-created agents
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

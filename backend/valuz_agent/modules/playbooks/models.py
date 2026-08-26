"""Generic Playbook identities, immutable versions and execution records."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin

_JSON_VARIANT = JSON().with_variant(JSONB(), "postgresql")


class PlaybookDefinitionRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    __tablename__ = "valuz_playbook_definition"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name="ck_playbook_definition_status",
        ),
        CheckConstraint(
            "origin IN ('user', 'system_example_copy', 'fork')",
            name="ck_playbook_definition_origin",
        ),
        UniqueConstraint("user_id", "project_id", "name", name="uq_playbook_project_name"),
    )

    # Optional placement/default context. Definition ownership is user_id; the
    # service must never manufacture a hidden Project to satisfy this column.
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    source_definition_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PlaybookVersionRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    __tablename__ = "valuz_playbook_version"
    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uq_playbook_definition_version"),
    )

    definition_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Sole authoritative executable body. The legacy workflow columns below
    # remain as a migration/read-compatibility envelope only and are never
    # composed into a second hidden execution contract.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reference_metadata: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    default_executor: Mapped[dict[str, Any]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=dict
    )
    # Deprecated physical compatibility columns introduced by migration 0038.
    # ``goal`` mirrors ``content`` for old readers during the migration window.
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    applicability: Mapped[dict[str, Any]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=dict
    )
    inputs: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    context_reads: Mapped[list[str]] = mapped_column(_JSON_VARIANT, nullable=False, default=list)
    stages: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    required_skills: Mapped[list[str]] = mapped_column(_JSON_VARIANT, nullable=False, default=list)
    allowed_skills: Mapped[list[str]] = mapped_column(_JSON_VARIANT, nullable=False, default=list)
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    approvals: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    outputs: Mapped[list[str]] = mapped_column(_JSON_VARIANT, nullable=False, default=list)
    context_writes: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    failure_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="stop")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    produced_by_run: Mapped[str | None] = mapped_column(String(128), nullable=True)
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PlaybookRunRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    __tablename__ = "valuz_playbook_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'planning', 'running', 'waiting_approval', "
            "'completed', 'failed', 'stopped')",
            name="ck_playbook_run_status",
        ),
        CheckConstraint(
            "trigger_kind IN ('user', 'agent', 'automation', 'playbook', 'api')",
            name="ck_playbook_run_trigger_kind",
        ),
    )

    definition_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # The actual execution container, not necessarily the Definition owner.
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    research_scope_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    trigger_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    trigger_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subject_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=dict
    )
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=dict
    )
    content_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_references: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    extra_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    executor_snapshot: Mapped[dict[str, Any]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=dict
    )
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    plan: Mapped[list[dict[str, Any]]] = mapped_column(_JSON_VARIANT, nullable=False, default=list)
    tasks: Mapped[list[dict[str, Any]]] = mapped_column(_JSON_VARIANT, nullable=False, default=list)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    approvals: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    artifact_refs: Mapped[list[str]] = mapped_column(_JSON_VARIANT, nullable=False, default=list)
    change_set_refs: Mapped[list[str]] = mapped_column(_JSON_VARIANT, nullable=False, default=list)
    output_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    checkpoint: Mapped[dict[str, Any]] = mapped_column(_JSON_VARIANT, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


__all__ = ["PlaybookDefinitionRow", "PlaybookRunRow", "PlaybookVersionRow"]

"""Shared evidence persistence. Domain bindings live with their consumers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin

_JSON = JSON().with_variant(JSONB(), "postgresql")


class EvidenceSnapshotRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    __tablename__ = "valuz_evidence_snapshot"
    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint", name="uq_evidence_owner_fingerprint"),
        CheckConstraint(
            "status IN ('ready', 'stale', 'missing', 'forbidden', 'retracted', 'superseded')",
            name="ck_evidence_snapshot_status",
        ),
    )
    fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(256))
    source_title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    canonical_url: Mapped[str | None] = mapped_column(Text)
    locator: Mapped[dict[str, Any] | None] = mapped_column(_JSON)
    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    as_of: Mapped[str | None] = mapped_column(String(64))
    period: Mapped[str | None] = mapped_column(String(128))
    unit: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(16))
    scale: Mapped[str | None] = mapped_column(String(32))
    basis: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")


class ProvenanceRecordRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    __tablename__ = "valuz_provenance_record"
    __table_args__ = (
        Index("ix_provenance_owner_subject", "user_id", "subject_type", "subject_id", "created_at"),
    )
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    context_refs: Mapped[list[dict[str, Any]]] = mapped_column(_JSON, nullable=False, default=list)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    subject_version: Mapped[str | None] = mapped_column(String(128))
    origin_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(_JSON, nullable=False, default=list)
    evidence_snapshot_refs: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    parent_provenance_refs: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    session_id: Mapped[str | None] = mapped_column(String(64))
    turn_id: Mapped[str | None] = mapped_column(String(64))
    message_id: Mapped[str | None] = mapped_column(String(64))
    run_ref: Mapped[str | None] = mapped_column(String(128))
    actor_user_id: Mapped[str | None] = mapped_column(String(64))
    agent_ref: Mapped[str | None] = mapped_column(String(128))
    tool_name: Mapped[str | None] = mapped_column(String(128))
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    method_ref: Mapped[str | None] = mapped_column(String(128))
    model_ref: Mapped[str | None] = mapped_column(String(256))
    policy_revision: Mapped[str | None] = mapped_column(String(128))
    transformation_class: Mapped[str | None] = mapped_column(String(32))
    input_version_refs: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    base_version_ref: Mapped[str | None] = mapped_column(String(128))
    output_version_ref: Mapped[str | None] = mapped_column(String(128))
    data_as_of: Mapped[str | None] = mapped_column(String(64))
    observed_at: Mapped[str | None] = mapped_column(String(64))
    approval_ref: Mapped[str | None] = mapped_column(String(128))
    domain_operation_ref: Mapped[str | None] = mapped_column(String(36), index=True)
    notes: Mapped[str | None] = mapped_column(Text)


class PendingEvidenceSealRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    __tablename__ = "valuz_pending_evidence_seal"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "operation_id",
            "message_id",
            "citation_id",
            name="uq_evidence_seal_operation_citation",
        ),
        CheckConstraint(
            "status IN ('pending', 'consumed', 'expired', 'invalid')",
            name="ck_evidence_seal_status",
        ),
    )
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    citation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    claim_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    citation_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    validation_revision: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # Imported control records retain their original state but cannot regain
    # execution authority merely because their old state happened to be pending.
    historical_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

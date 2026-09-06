"""Generic persistent mutation envelope and append-only decisions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin

_JSON_VARIANT = JSON().with_variant(JSONB(), "postgresql")


class OperationRecordRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """One side-effecting product operation from proposal to canonical result."""

    __tablename__ = "valuz_operation_record"
    __table_args__ = (
        CheckConstraint(
            "state IN ('proposed', 'awaiting_confirmation', 'executing', "
            "'succeeded', 'failed', 'cancelled', 'expired', 'stale', 'superseded')",
            name="ck_operation_record_state",
        ),
        CheckConstraint(
            "risk_level IN ('low', 'material', 'destructive', 'external')",
            name="ck_operation_record_risk",
        ),
        CheckConstraint(
            "actor_kind IN ('user', 'agent', 'playbook', 'automation', 'system')",
            name="ck_operation_record_actor_kind",
        ),
        CheckConstraint(
            "confirmation_policy IN ('direct', 'explicit_submit', 'confirm', "
            "'approval', 'preauthorized')",
            name="ck_operation_record_confirmation_policy",
        ),
        UniqueConstraint("user_id", "idempotency_key", name="uq_operation_owner_idempotency"),
    )

    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    operation_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    operation_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    actor_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    origin_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    origin_tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    origin_playbook_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    origin_automation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    target_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    input_payload: Mapped[dict[str, Any]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=dict
    )
    preview: Mapped[dict[str, Any]] = mapped_column(_JSON_VARIANT, nullable=False, default=dict)
    expected_revisions: Mapped[dict[str, Any]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=dict
    )
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="material")
    confirmation_policy: Mapped[str] = mapped_column(String(24), nullable=False, default="confirm")
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="awaiting_confirmation", index=True
    )
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_result_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=list
    )
    result_payload: Mapped[dict[str, Any]] = mapped_column(
        _JSON_VARIANT, nullable=False, default=dict
    )
    error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Epoch ms after which a still-pending proposal can no longer be
    #: confirmed. ``None`` = never. Read lazily: a pending row past this
    #: instant reports ``expired`` before anything wrote it.
    expires_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: The newer proposal for the same owner/type/target that replaced
    #: this one (state ``superseded``).
    superseded_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ConfirmationDecisionRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Append-only user decision attached to an OperationRecord."""

    __tablename__ = "valuz_confirmation_decision"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approve', 'reject', 'request_changes')",
            name="ck_confirmation_decision_value",
        ),
    )

    operation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["ConfirmationDecisionRow", "OperationRecordRow"]

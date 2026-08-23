"""add generic operation records and confirmation decisions

Revision ID: 0040
Revises: 0039
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def identity() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
    ]


def times() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "valuz_operation_record",
        *identity(),
        sa.Column("project_id", sa.String(36), nullable=True, index=True),
        sa.Column("operation_type", sa.String(96), nullable=False, index=True),
        sa.Column("operation_version", sa.Integer(), nullable=False),
        sa.Column("actor_kind", sa.String(24), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=True),
        sa.Column("origin_session_id", sa.String(36), nullable=True, index=True),
        sa.Column("origin_tool_call_id", sa.String(128), nullable=True, index=True),
        sa.Column("origin_playbook_run_id", sa.String(36), nullable=True),
        sa.Column("origin_automation_run_id", sa.String(36), nullable=True),
        sa.Column("target_refs", _JSON, nullable=False),
        sa.Column("input_payload", _JSON, nullable=False),
        sa.Column("preview", _JSON, nullable=False),
        sa.Column("expected_revisions", _JSON, nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("confirmation_policy", sa.String(24), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, index=True),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("canonical_result_refs", _JSON, nullable=False),
        sa.Column("result_payload", _JSON, nullable=False),
        sa.Column("error_code", sa.String(96), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *times(),
        sa.CheckConstraint(
            "state IN ('proposed', 'awaiting_confirmation', 'executing', "
            "'succeeded', 'failed', 'cancelled', 'expired', 'stale', 'superseded')",
            name="ck_operation_record_state",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'material', 'destructive', 'external')",
            name="ck_operation_record_risk",
        ),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'agent', 'playbook', 'automation', 'system')",
            name="ck_operation_record_actor_kind",
        ),
        sa.CheckConstraint(
            "confirmation_policy IN ('direct', 'explicit_submit', 'confirm', "
            "'approval', 'preauthorized')",
            name="ck_operation_record_confirmation_policy",
        ),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_operation_owner_idempotency"),
    )
    op.create_table(
        "valuz_confirmation_decision",
        *identity(),
        sa.Column("operation_id", sa.String(36), nullable=False, index=True),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("decided_by", sa.String(64), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        *times(),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject', 'request_changes')",
            name="ck_confirmation_decision_value",
        ),
    )


def downgrade() -> None:
    op.drop_table("valuz_confirmation_decision")
    op.drop_table("valuz_operation_record")

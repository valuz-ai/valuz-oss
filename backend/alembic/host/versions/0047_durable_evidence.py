"""Shared durable evidence and provenance; domain bindings stay in extensions.

Revision ID: 0047
Revises: 0046
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def _identity() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "valuz_evidence_snapshot",
        *_identity(),
        sa.Column("fingerprint", sa.String(80), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("provider_id", sa.String(128), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("source_version", sa.String(256), nullable=True),
        sa.Column("source_title", sa.String(500), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("locator", _JSON, nullable=True),
        sa.Column("evidence_kind", sa.String(32), nullable=False),
        sa.Column("snapshot", _JSON, nullable=False),
        sa.Column("content_hash", sa.String(80), nullable=False),
        sa.Column("as_of", sa.String(64), nullable=True),
        sa.Column("period", sa.String(128), nullable=True),
        sa.Column("unit", sa.String(64), nullable=True),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("scale", sa.String(32), nullable=True),
        sa.Column("basis", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.UniqueConstraint("user_id", "fingerprint", name="uq_evidence_owner_fingerprint"),
        sa.CheckConstraint(
            "status IN ('ready', 'stale', 'missing', 'forbidden', 'retracted', 'superseded')",
            name="ck_evidence_snapshot_status",
        ),
    )
    op.create_table(
        "valuz_provenance_record",
        *_identity(),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("context_refs", _JSON, nullable=False),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column("subject_version", sa.String(128), nullable=True),
        sa.Column("origin_kind", sa.String(32), nullable=False),
        sa.Column("source_refs", _JSON, nullable=False),
        sa.Column("evidence_snapshot_refs", _JSON, nullable=False),
        sa.Column("parent_provenance_refs", _JSON, nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("turn_id", sa.String(64), nullable=True),
        sa.Column("message_id", sa.String(64), nullable=True),
        sa.Column("run_ref", sa.String(128), nullable=True),
        sa.Column("actor_user_id", sa.String(64), nullable=True),
        sa.Column("agent_ref", sa.String(128), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=True),
        sa.Column("tool_call_id", sa.String(128), nullable=True),
        sa.Column("method_ref", sa.String(128), nullable=True),
        sa.Column("model_ref", sa.String(256), nullable=True),
        sa.Column("policy_revision", sa.String(128), nullable=True),
        sa.Column("transformation_class", sa.String(32), nullable=True),
        sa.Column("input_version_refs", _JSON, nullable=False),
        sa.Column("base_version_ref", sa.String(128), nullable=True),
        sa.Column("output_version_ref", sa.String(128), nullable=True),
        sa.Column("data_as_of", sa.String(64), nullable=True),
        sa.Column("observed_at", sa.String(64), nullable=True),
        sa.Column("approval_ref", sa.String(128), nullable=True),
        sa.Column("domain_operation_ref", sa.String(36), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_table(
        "valuz_pending_evidence_seal",
        *_identity(),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("message_id", sa.String(64), nullable=False),
        sa.Column("citation_id", sa.String(128), nullable=False),
        sa.Column("claim_id", sa.String(128), nullable=True),
        sa.Column("payload", _JSON, nullable=False),
        sa.Column("citation_hash", sa.String(80), nullable=False),
        sa.Column("validation_revision", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("historical_only", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "operation_id",
            "message_id",
            "citation_id",
            name="uq_evidence_seal_operation_citation",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'consumed', 'expired', 'invalid')",
            name="ck_evidence_seal_status",
        ),
    )
    for table in (
        "valuz_evidence_snapshot",
        "valuz_provenance_record",
        "valuz_pending_evidence_seal",
    ):
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])
    for column in ("project_id", "subject_id", "domain_operation_ref"):
        op.create_index(f"ix_valuz_provenance_record_{column}", "valuz_provenance_record", [column])
    op.create_index(
        "ix_provenance_owner_subject",
        "valuz_provenance_record",
        ["user_id", "subject_type", "subject_id", "created_at"],
    )
    op.create_index(
        "ix_valuz_pending_evidence_seal_operation_id",
        "valuz_pending_evidence_seal",
        ["operation_id"],
    )


def downgrade() -> None:
    # Never silently erase durable sources or control history on rollback.
    for table in (
        "valuz_evidence_snapshot",
        "valuz_provenance_record",
        "valuz_pending_evidence_seal",
    ):
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError("durable_evidence_downgrade_requires_empty_tables")
    for table in (
        "valuz_pending_evidence_seal",
        "valuz_provenance_record",
        "valuz_evidence_snapshot",
    ):
        op.drop_table(table)

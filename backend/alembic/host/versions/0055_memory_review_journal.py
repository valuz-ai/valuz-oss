"""Durable memory review candidates, progress and bounded call policy.

Revision ID: 0055
Revises: 0054
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "valuz_memory_review_job",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.BigInteger(), nullable=False),
        sa.Column("rerun_due_at", sa.BigInteger()),
        sa.Column("fence_token", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("base_revision", sa.BigInteger()),
        sa.Column("review_id", sa.String(64)),
        sa.Column("authority_id", sa.String(256)),
        sa.Column("authority_epoch", sa.BigInteger()),
        sa.Column("project_id", sa.String(128)),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("plan_ready", sa.Boolean(), nullable=False),
        sa.Column("planned_ops", sa.JSON(), nullable=False),
        sa.Column("receipts", sa.JSON(), nullable=False),
        sa.Column("next_op", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(48)),
    )
    for column in ("user_id", "status", "due_at"):
        op.create_index(f"ix_valuz_memory_review_job_{column}", "valuz_memory_review_job", [column])
    op.create_table(
        "valuz_memory_review_owner",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("purge_revision", sa.BigInteger(), nullable=False),
        sa.Column("authority_id", sa.String(256)),
        sa.Column("authority_epoch", sa.BigInteger()),
        sa.Column("clear_before_ms", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.Integer(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("valuz_memory_review_owner")
    op.drop_table("valuz_memory_review_job")

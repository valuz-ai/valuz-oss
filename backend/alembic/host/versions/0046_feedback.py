"""valuz_feedback — user outcome signals on assistant turns.

One row per ``(user_id, message_id, action, block_ref)``: rating (👍/👎 +
reason), copy, and the server-emitted regenerate / fork / share actions.
Repeats bump ``occurrences``; the unique index is a plain one (no partial
index) so every write is an idempotent upsert. ``message_id`` is a weak
reference to the kernel ``messages`` table — no FK, the kernel store may
live in another file.

Revision ID: 0046
Revises: 0045
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "valuz_feedback",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("block_ref", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("value", sa.String(length=8), nullable=True),
        sa.Column("reason_code", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("target_type", sa.String(length=24), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("surface", sa.String(length=16), nullable=True),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "action IN ('rating', 'copy', 'regenerate', 'fork', 'share', 'research_share')",
            name="ck_valuz_feedback_action",
        ),
        sa.CheckConstraint(
            "value IS NULL OR value IN ('up', 'down')", name="ck_valuz_feedback_value"
        ),
        sa.CheckConstraint("source IN ('ui', 'api', 'server')", name="ck_valuz_feedback_source"),
        sa.UniqueConstraint(
            "user_id", "message_id", "action", "block_ref", name="uq_valuz_feedback_subject"
        ),
    )
    op.create_index("ix_valuz_feedback_user_id", "valuz_feedback", ["user_id"])
    op.create_index("ix_valuz_feedback_session", "valuz_feedback", ["user_id", "session_id"])
    op.create_index("ix_valuz_feedback_updated_at", "valuz_feedback", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_valuz_feedback_updated_at", table_name="valuz_feedback")
    op.drop_index("ix_valuz_feedback_session", table_name="valuz_feedback")
    op.drop_index("ix_valuz_feedback_user_id", table_name="valuz_feedback")
    op.drop_table("valuz_feedback")

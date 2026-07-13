"""notifications: durable attention ledger

The single persisted account of "things needing the user" — questions,
task failures, etc. — with a read/resolved lifecycle that survives restart.
See docs/design/notifications.md.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-13

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "valuz_notification"


def upgrade() -> None:
    op.create_table(
        _TABLE_NAME,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("body", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column("route", sa.String(length=512), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("urgency", sa.String(length=16), nullable=False, server_default="actionable"),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("pending_id", sa.String(length=64), nullable=True),
        sa.Column("source_event_id", sa.String(length=36), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("read_at", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("user_id", "dedup_key", name="uq_notification_user_dedup"),
    )
    op.create_index("ix_notification_user_id", _TABLE_NAME, ["user_id"])
    op.create_index("ix_notification_task_id", _TABLE_NAME, ["task_id"])
    op.create_index(
        "ix_notification_user_created", _TABLE_NAME, ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_notification_user_created", table_name=_TABLE_NAME)
    op.drop_index("ix_notification_task_id", table_name=_TABLE_NAME)
    op.drop_index("ix_notification_user_id", table_name=_TABLE_NAME)
    op.drop_table(_TABLE_NAME)

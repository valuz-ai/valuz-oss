"""Durable queued-input outcomes and existing-session automation targets.

Revision ID: 0053
Revises: 0052
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("valuz_automation", sa.Column("target_session_id", sa.String(36)))
    op.add_column("valuz_queued_input", sa.Column("completed_at", sa.BigInteger()))
    op.add_column("valuz_queued_input", sa.Column("output_message_id", sa.String(36)))
    op.add_column("valuz_queued_input", sa.Column("result_summary", sa.Text()))


def downgrade() -> None:
    op.drop_column("valuz_queued_input", "result_summary")
    op.drop_column("valuz_queued_input", "output_message_id")
    op.drop_column("valuz_queued_input", "completed_at")
    op.drop_column("valuz_automation", "target_session_id")

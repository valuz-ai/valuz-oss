"""Read-only operation history, independent from original approval state."""

import sqlalchemy as sa

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "valuz_operation_record",
        sa.Column(
            "historical_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM valuz_operation_record WHERE historical_only = TRUE")
    ):
        raise RuntimeError("operation_history_downgrade_requires_no_historical_records")
    op.drop_column("valuz_operation_record", "historical_only")

"""valuz_operation_record.expires_at / superseded_by_id — proposal lifetime.

``expires_at`` (epoch ms, NULL = never) is the instant after which a still
pending proposal reads as ``expired`` and refuses confirmation.
``superseded_by_id`` points at the newer proposal for the same
owner/type/target that replaced a pending one (state ``superseded``). Both
states already existed in the state check; nothing wrote them before.

Revision ID: 0045
Revises: 0044
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_operation_record") as batch:
        batch.add_column(sa.Column("expires_at", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("superseded_by_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("valuz_operation_record") as batch:
        batch.drop_column("superseded_by_id")
        batch.drop_column("expires_at")

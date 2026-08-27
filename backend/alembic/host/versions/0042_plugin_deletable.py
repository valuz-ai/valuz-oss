"""valuz_plugin.deletable — builtin (app-managed) plugins are not deletable.

Builtin plugins (``source="builtin"``) install with ``deletable=False``;
everything already installed stays deletable (server default ``true``).

Revision ID: 0042
Revises: 0041
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_plugin") as batch:
        batch.add_column(
            sa.Column(
                "deletable",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("valuz_plugin") as batch:
        batch.drop_column("deletable")

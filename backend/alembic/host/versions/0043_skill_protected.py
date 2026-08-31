"""valuz_skill_index.protected — packages that are usable but never disclosed.

Mirrors the ``.protected`` marker file a host writes into a package directory,
so a catalog read does not have to stat every package. Everything already
indexed is unprotected (server default ``false``); the next scan copies the
marker in for any package that carries one.

Revision ID: 0043
Revises: 0042
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_skill_index") as batch:
        batch.add_column(
            sa.Column(
                "protected",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("valuz_skill_index") as batch:
        batch.drop_column("protected")

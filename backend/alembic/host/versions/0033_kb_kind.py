"""Knowledge-base class discriminator.

``valuz_knowledge_base.kind`` is a neutral open string describing what class
of knowledge base a row is. OSS only ever writes/reads ``"normal"``; hosts
embedding valuz use it to distinguish their own classes and to route them to
different roots via ``FsRegistry.set_kb_root_resolver``. Existing rows are
backfilled to ``"normal"``, so behavior is unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "valuz_knowledge_base",
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="normal"),
    )


def downgrade() -> None:
    op.drop_column("valuz_knowledge_base", "kind")

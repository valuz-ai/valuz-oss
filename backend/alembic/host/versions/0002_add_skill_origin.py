"""add valuz_skill_index.origin_json (import provenance)

Stores JSON provenance ({type, source_url, path}) for skills imported from a
URL/GitHub so the UI can show "Imported from …" and link back. Host-only
bookkeeping, like ``creation_origin`` — never written into SKILL.md.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_skill_index", schema=None) as batch_op:
        batch_op.add_column(sa.Column("origin_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("valuz_skill_index", schema=None) as batch_op:
        batch_op.drop_column("origin_json")

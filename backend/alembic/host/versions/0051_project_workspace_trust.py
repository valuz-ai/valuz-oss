"""projects: add workspace trust

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-05

``workspace_trust`` decides whether a project folder's own hook configuration
(``.claude/settings.json`` / ``.codex`` hooks) may run in its sessions —
``trusted`` / ``untrusted``. Existing rows stay ``NULL``, which behaves as
trusted, so upgrading changes nothing for them.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "valuz_project"
_COLUMN = "workspace_trust"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column(_COLUMN, sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)

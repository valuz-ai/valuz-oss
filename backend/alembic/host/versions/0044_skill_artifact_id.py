"""valuz_skill_index.artifact_id — the artifact lineage that versions this skill.

Set by the skill library the first time a skill-creator result is saved (the
artifact is a ``kind=skill`` deliverable whose revisions are the skill's
versions). Host-only bookkeeping like ``creation_origin``: the filesystem scan
never writes it, so it survives rescans; a renamed or forked directory is a new
row with no lineage until its first save.

Revision ID: 0044
Revises: 0043
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_skill_index") as batch:
        batch.add_column(sa.Column("artifact_id", sa.String(length=16), nullable=True))
        batch.create_index("ix_valuz_skill_index_artifact_id", ["artifact_id"])


def downgrade() -> None:
    with op.batch_alter_table("valuz_skill_index") as batch:
        batch.drop_index("ix_valuz_skill_index_artifact_id")
        batch.drop_column("artifact_id")

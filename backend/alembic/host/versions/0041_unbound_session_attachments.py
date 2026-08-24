"""let a conversation attachment exist before its session does

An attachment used to require a session, so attaching a file created one — and
under scoped allocation creating a session provisions a sandbox. The upload
path never uses it: the bytes go to the owner's data dir and the parse is a
host task over HTTP. Making ``session_id`` nullable is what lets the upload
stand on its own; the send binds it.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_session_attachment") as batch:
        batch.alter_column("session_id", existing_type=sa.String(36), nullable=True)


def downgrade() -> None:
    # Unbound rows have no session to fall back to, and inventing one would
    # attach a stranger's files to a real conversation. They are drafts nobody
    # sent: drop them, then restore the constraint.
    op.execute("DELETE FROM valuz_session_attachment WHERE session_id IS NULL")
    with op.batch_alter_table("valuz_session_attachment") as batch:
        batch.alter_column("session_id", existing_type=sa.String(36), nullable=False)

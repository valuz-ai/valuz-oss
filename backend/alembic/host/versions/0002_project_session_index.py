"""project↔session index table

Host-side record of which project each kernel session belongs to and what
role it plays (chat conversation vs task-internal lead/subtask run). Written
at session-creation time; replaces filtering on the kernel's
``sessions.project_id`` column (scheduled for removal) and the json_extract
``metadata.valuz.task_id`` predicate for user-session lists.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-10

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
    op.create_table(
        "valuz_project_session",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("valuz_project_session", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_valuz_project_session_project_id"), ["project_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_valuz_project_session_session_id"), ["session_id"], unique=True
        )
        batch_op.create_index(
            batch_op.f("ix_valuz_project_session_user_id"), ["user_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("valuz_project_session", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_valuz_project_session_user_id"))
        batch_op.drop_index(batch_op.f("ix_valuz_project_session_session_id"))
        batch_op.drop_index(batch_op.f("ix_valuz_project_session_project_id"))

    op.drop_table("valuz_project_session")

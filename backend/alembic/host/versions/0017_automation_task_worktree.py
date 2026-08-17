"""automations: add task_worktree flag

Task-level worktree isolation (docs/design/project-worktree-design.md §5):
a ``task``-action automation with this flag runs each fired task — lead and
every member — in one git worktree of the project repo. Default off; ignored
for ``chat`` action rows.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_automation") as batch:
        batch.add_column(
            sa.Column(
                "task_worktree",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("valuz_automation") as batch:
        batch.drop_column("task_worktree")

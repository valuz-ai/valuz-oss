"""Widen ck_sessions_runtime_provider to allow 'deepseek_harness'.

SQLite cannot ALTER a CHECK constraint in place, so batch mode rebuilds the
table (copy-and-swap). The reverse migration restores the three-value
constraint; it fails loudly if any 'deepseek_harness' rows exist, which is
the correct outcome — downgrading with live sessions of the new runtime
would strand them.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None

_OLD = "runtime_provider IN ('claude_agent', 'codex', 'deepagents')"
_NEW = "runtime_provider IN ('claude_agent', 'codex', 'deepagents', 'deepseek_harness')"
_NAME = "ck_sessions_runtime_provider"


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint(_NAME, type_="check")
        batch_op.create_check_constraint(_NAME, sa.text(_NEW))


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_constraint(_NAME, type_="check")
        batch_op.create_check_constraint(_NAME, sa.text(_OLD))

"""tasks: per-task execution lease so only one process drives a task

The task actor loops, their mailboxes and the live-member registry are
process-local, but the tasks they drive live in a database that several host
processes may share (``uvicorn --workers N``, or multiple replicas). The
watchdog's liveness test was ``mailbox_registry.is_owned()`` — true only inside
the asking process — so a lead running in a sibling process read as dead and
its task was flipped to ``blocked(reason="lead_dead")`` mid-run.

``valuz_task_lease`` names the current driver of each task and carries a TTL
that driver must renew. ``task_id`` is the primary key: a task has at most one
driver, and the constraint is what enforces it under a concurrent insert.

Empty on upgrade — existing active tasks acquire a lease the next time their
lead loop starts. Readers treat a MISSING row as "unknown", never as "dead",
so a rolling deploy where old processes still drive leaseless tasks is safe.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "valuz_task_lease",
        sa.Column("task_id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("lead_session_id", sa.String(length=36), nullable=False),
        sa.Column("holder_id", sa.String(length=128), nullable=False),
        sa.Column("fence_token", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("heartbeat_at", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_valuz_task_lease_user_id", "valuz_task_lease", ["user_id"])
    op.create_index("ix_valuz_task_lease_expires", "valuz_task_lease", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_valuz_task_lease_expires", table_name="valuz_task_lease")
    op.drop_index("ix_valuz_task_lease_user_id", table_name="valuz_task_lease")
    op.drop_table("valuz_task_lease")

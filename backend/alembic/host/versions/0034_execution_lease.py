"""cross-process execution ownership: one holder per (scope, key)

Several subsystems coordinate long-running work through PROCESS-LOCAL memory
while the work lives in a database several host processes share (``uvicorn
--workers N``, or multiple replicas). Each broke differently: the task watchdog
judged liveness from a per-process mailbox and blocked healthy tasks mid-run;
boot recovery re-drove every active task in every process; the session
queue-drain guard and the polling scheduler are in-memory too.

``valuz_execution_lease`` names the current holder of a ``(scope, key)`` and
carries a TTL that holder renews. The composite key IS the primary key: a key
has at most one holder, and that constraint settles the concurrent insert.
Generic rather than per-subsystem so fencing is written — and reviewed — once.

Empty on upgrade; holders acquire as they start. Readers treat a MISSING row as
"unknown", never as "dead", so a rolling deploy in which older processes still
hold leaseless work is safe.

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
        "valuz_execution_lease",
        sa.Column("scope", sa.String(length=32), primary_key=True),
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("holder_id", sa.String(length=128), nullable=False),
        sa.Column("fence_token", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("heartbeat_at", sa.BigInteger(), nullable=False),
        sa.Column("lease_expires_at", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_valuz_execution_lease_expires", "valuz_execution_lease", ["lease_expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_valuz_execution_lease_expires", table_name="valuz_execution_lease")
    op.drop_table("valuz_execution_lease")

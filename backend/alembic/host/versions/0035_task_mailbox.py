"""durable delivery for actor messages

Task actors talked through an ``asyncio.Queue`` per session — a channel only
if the sender and the receiving loop share a process. The host runs
``uvicorn --workers N`` across several replicas, so a chat instruction, a
member's report and a lead's follow-up each land wherever the load balancer
put them, and every message that crossed a process boundary was dropped
without a trace. Each symptom then grew its own repair (one reading run rows,
one reading the event log); none of them was a delivery mechanism.

``valuz_task_mailbox`` is the delivery mechanism, and it carries FACTS only —
"a member finished", "the user said X". Control signals (stop / pause /
takeover) are not messages: they revoke an actor's right to run and live in
``valuz_execution_lease``, whose fence token names the one incarnation being
revoked. A persisted ``shutdown`` would be replayed to the replacement loop
and kill it, which is exactly the bug that made this design necessary.

Rollout is producer-first: writers switch to this table while consumers read
BOTH it and the legacy in-memory queue. That ordering is a safety
requirement, not a preference — switching consumers first would let a message
be delivered twice during the rolling window.

Mirrors ``valuz_queued_input`` (sessions), which solved the same problem one
layer down.

See docs/design/task-delivery-and-control.md.

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "valuz_task_mailbox",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("from_session", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("origin", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("consumed_at", sa.BigInteger(), nullable=True),
        sa.Column("consumed_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_valuz_task_mailbox_task_id", "valuz_task_mailbox", ["task_id"])
    # The drain, run at every idle tick of every live actor.
    op.create_index(
        "ix_valuz_task_mailbox_pending",
        "valuz_task_mailbox",
        ["session_id", "state", "position"],
    )


def downgrade() -> None:
    op.drop_index("ix_valuz_task_mailbox_pending", table_name="valuz_task_mailbox")
    op.drop_index("ix_valuz_task_mailbox_task_id", table_name="valuz_task_mailbox")
    op.drop_table("valuz_task_mailbox")

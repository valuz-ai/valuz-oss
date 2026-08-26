"""kernel: SaaS-ready store (event_uid idempotency)

Consolidated schema change for the model-A remote/durable store work, additive
over the 0001 baseline:

``events.event_uid`` (nullable) + UNIQUE ``(user_id, event_uid)`` — at-least-once
append idempotency: a retried append (same client ``request_id``) conflicts on
the index and the store returns the original row's ``seq``. NULL (local
in-process appends) is distinct under the index on SQLite + Postgres, so
existing local behaviour is unchanged.

Owner isolation is app-layer by construction — every access path resolves
``user_id`` from the verified token and every ``StorePort`` method requires
it. There is deliberately no DB-level RLS: it would break the host data
plane's legitimate cross-owner reads (recovery sweeps), and without a
dedicated non-owner DB role it never applied anyway.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-29 00:00:00.000000

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
    op.add_column("events", sa.Column("event_uid", sa.String(length=64), nullable=True))
    op.create_index("uq_events_owner_uid", "events", ["user_id", "event_uid"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_events_owner_uid", table_name="events")
    op.drop_column("events", "event_uid")

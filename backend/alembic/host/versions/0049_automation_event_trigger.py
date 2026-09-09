"""Let an automation be woken by an event source, not only by the clock.

Adds ``event_source`` / ``event_refs`` on the automation, ``event_id`` on the
run, and widens ``trigger_kind`` to accept ``event``. The two automation
columns are additive on purpose: a cron row may also carry a subscription,
and ``trigger_kind='event'`` only means "no clock at all".
subscription, and ``trigger_kind='event'`` only means "no clock at all". See
``valuz_agent/ports/automation_event_source.py`` for the contract — OSS ships no
source, so these columns stay NULL until an overlay registers one.

The CHECK change is why this migration is longer than an ``add_column``.
Postgres alters the named constraint in place; SQLite has to recreate the whole
table, which alembic does from a reflection — the four unrelated CHECKs survive
that on their own, so only the one that changes is dropped and rewritten. Both
dialects end up with the same six constraints.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")

_TRIGGER_KIND_WITH_EVENT = "trigger_kind IN ('cron', 'interval', 'manual', 'event')"
_TRIGGER_KIND_WITHOUT_EVENT = "trigger_kind IN ('cron', 'interval', 'manual')"
_EVENT_SOURCE_PRESENT = (
    "(trigger_kind = 'event' AND event_source IS NOT NULL) OR trigger_kind != 'event'"
)


def upgrade() -> None:
    op.add_column(
        "valuz_automation", sa.Column("event_source", sa.String(64), nullable=True)
    )
    op.add_column("valuz_automation", sa.Column("event_refs", _JSON, nullable=True))
    op.create_index(
        "ix_valuz_automation_event_source", "valuz_automation", ["event_source"]
    )

    # Idempotency for deliveries. NULL for every clock/manual run, and SQLite
    # and Postgres both treat NULLs as distinct in a unique index, so the
    # existing rows do not collide with each other.
    with op.batch_alter_table("valuz_automation_run") as batch:
        batch.add_column(sa.Column("event_id", sa.String(128), nullable=True))
        batch.create_unique_constraint(
            "uq_automation_run_event", ["automation_id", "event_id"]
        )

    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "ck_automation_trigger_kind", "valuz_automation", type_="check"
        )
        op.create_check_constraint(
            "ck_automation_trigger_kind", "valuz_automation", _TRIGGER_KIND_WITH_EVENT
        )
        op.create_check_constraint(
            "ck_automation_event_source_when_event",
            "valuz_automation",
            _EVENT_SOURCE_PRESENT,
        )
        return

    # SQLite recreates the table; its dialect DOES reflect CHECK constraints,
    # so the four unrelated ones come along by themselves. Only touch the one
    # that changes, and add the new one.
    with op.batch_alter_table("valuz_automation", recreate="always") as batch:
        batch.drop_constraint("ck_automation_trigger_kind", type_="check")
        batch.create_check_constraint(
            "ck_automation_trigger_kind", _TRIGGER_KIND_WITH_EVENT
        )
        batch.create_check_constraint(
            "ck_automation_event_source_when_event", _EVENT_SOURCE_PRESENT
        )


def downgrade() -> None:
    """Back to a clock-only automation.

    Event rows have no meaning without the columns, so they are dropped rather
    than silently rewritten into cron rows that would then fire on a schedule
    nobody asked for.
    """
    op.execute("DELETE FROM valuz_automation WHERE trigger_kind = 'event'")
    op.execute("DELETE FROM valuz_automation_run WHERE event_id IS NOT NULL")
    with op.batch_alter_table("valuz_automation_run") as batch:
        batch.drop_constraint("uq_automation_run_event", type_="unique")
        batch.drop_column("event_id")

    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(
            "ck_automation_event_source_when_event", "valuz_automation", type_="check"
        )
        op.drop_constraint(
            "ck_automation_trigger_kind", "valuz_automation", type_="check"
        )
        op.create_check_constraint(
            "ck_automation_trigger_kind", "valuz_automation", _TRIGGER_KIND_WITHOUT_EVENT
        )
        op.drop_index("ix_valuz_automation_event_source", table_name="valuz_automation")
        op.drop_column("valuz_automation", "event_refs")
        op.drop_column("valuz_automation", "event_source")
        return

    op.drop_index("ix_valuz_automation_event_source", table_name="valuz_automation")
    with op.batch_alter_table("valuz_automation", recreate="always") as batch:
        batch.drop_constraint("ck_automation_event_source_when_event", type_="check")
        batch.drop_constraint("ck_automation_trigger_kind", type_="check")
        batch.create_check_constraint(
            "ck_automation_trigger_kind", _TRIGGER_KIND_WITHOUT_EVENT
        )
        batch.drop_column("event_refs")
        batch.drop_column("event_source")

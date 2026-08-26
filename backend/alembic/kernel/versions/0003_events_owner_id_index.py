"""kernel: (user_id, id) composite index on events for the user-level stream

The always-on user control-plane stream reads ALL of one owner's events after a
global cursor: ``WHERE user_id = ? AND id > ? ORDER BY id`` (StorePort
``get_events_after_for_user``). The existing indexes are ``(session_id, …)`` and
the unique ``(user_id, event_uid)`` — neither serves a ``user_id`` + ``id``-range
scan, so without this index the read filters on the plain ``user_id`` index then
sorts by ``id``. This composite makes it an index-range scan.

Additive and reversible; no data change. See
docs/design/event-delivery-unification.md §5.1 / §8.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-13 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_events_owner_id", "events", ["user_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_events_owner_id", table_name="events")

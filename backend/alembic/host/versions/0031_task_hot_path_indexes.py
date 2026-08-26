"""tasks: index the queries that run per-poll, drop two that nothing uses

Three hot reads had no index to stand on, and two indexes existed for queries
that were never written:

ADDED
  ix_valuz_task_user_updated  (user_id, updated_at)
      ``list_all`` (sidebar TASKS, every page load) and ``list_tasks_page``
      (the polled activity feed) are ``WHERE user_id ... ORDER BY updated_at
      DESC LIMIT n``. With single-column indexes only, SQLite walked every one
      of the owner's rows into the sorter — materializing each FULL row, plan
      JSON and goal text included (5-30 KB each) — to keep 20.

  ix_valuz_task_status  (status)
      ``list_active`` is ``WHERE status = 'active'``. The health watchdog runs
      it every 60 seconds for the life of the process, so this was a full-table
      scan per minute, growing with install age.

DROPPED
  ix_valuz_task_session_project_id
      ``TaskSessionRow.project_id`` appears in no WHERE clause anywhere —
      maintained on every run insert for nothing.

  ix_valuz_task_event_project_id
      ``TaskEventRow.project_id`` is only ever filtered together with
      ``task_id``, which the unique ``(project_id, task_id, sequence)`` index
      already covers as a prefix. Redundant on the module's highest-frequency
      insert.

Reversible: downgrade restores both dropped indexes and removes the new ones.

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_task") as batch_op:
        batch_op.create_index(
            "ix_valuz_task_user_updated", ["user_id", "updated_at"], unique=False
        )
        batch_op.create_index("ix_valuz_task_status", ["status"], unique=False)

    with op.batch_alter_table("valuz_task_session") as batch_op:
        batch_op.drop_index("ix_valuz_task_session_project_id")

    with op.batch_alter_table("valuz_task_event") as batch_op:
        batch_op.drop_index("ix_valuz_task_event_project_id")


def downgrade() -> None:
    with op.batch_alter_table("valuz_task_event") as batch_op:
        batch_op.create_index("ix_valuz_task_event_project_id", ["project_id"], unique=False)

    with op.batch_alter_table("valuz_task_session") as batch_op:
        batch_op.create_index("ix_valuz_task_session_project_id", ["project_id"], unique=False)

    with op.batch_alter_table("valuz_task") as batch_op:
        batch_op.drop_index("ix_valuz_task_status")
        batch_op.drop_index("ix_valuz_task_user_updated")

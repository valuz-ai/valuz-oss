"""make Playbooks owner-scoped versioned prompts

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("valuz_playbook_definition") as batch:
        batch.alter_column(
            "project_id",
            existing_type=sa.String(36),
            nullable=True,
        )

    with op.batch_alter_table("valuz_playbook_version") as batch:
        batch.add_column(sa.Column("content", sa.Text(), nullable=True))
        batch.add_column(sa.Column("reference_metadata", _JSON, nullable=True))
        batch.add_column(sa.Column("default_executor", _JSON, nullable=True))
    op.execute("UPDATE valuz_playbook_version SET content = goal WHERE content IS NULL")
    op.execute(
        "UPDATE valuz_playbook_version SET reference_metadata = '[]' "
        "WHERE reference_metadata IS NULL"
    )
    op.execute(
        "UPDATE valuz_playbook_version SET default_executor = '{}' WHERE default_executor IS NULL"
    )
    with op.batch_alter_table("valuz_playbook_version") as batch:
        batch.alter_column("content", existing_type=sa.Text(), nullable=False)
        batch.alter_column("reference_metadata", existing_type=_JSON, nullable=False)
        batch.alter_column("default_executor", existing_type=_JSON, nullable=False)

    with op.batch_alter_table("valuz_playbook_run") as batch:
        batch.alter_column(
            "project_id",
            existing_type=sa.String(36),
            nullable=True,
        )
        batch.add_column(sa.Column("content_snapshot", sa.Text(), nullable=True))
        batch.add_column(sa.Column("resolved_references", _JSON, nullable=True))
        batch.add_column(sa.Column("extra_instruction", sa.Text(), nullable=True))
        batch.add_column(sa.Column("executor_snapshot", _JSON, nullable=True))
        batch.add_column(sa.Column("session_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("task_id", sa.String(36), nullable=True))
    op.execute(
        "UPDATE valuz_playbook_run SET content_snapshot = "
        "(SELECT content FROM valuz_playbook_version "
        "WHERE valuz_playbook_version.definition_id = valuz_playbook_run.definition_id "
        "AND valuz_playbook_version.version = valuz_playbook_run.definition_version) "
        "WHERE content_snapshot IS NULL"
    )
    op.execute("UPDATE valuz_playbook_run SET content_snapshot = '' WHERE content_snapshot IS NULL")
    op.execute(
        "UPDATE valuz_playbook_run SET resolved_references = '[]' WHERE resolved_references IS NULL"
    )
    op.execute(
        "UPDATE valuz_playbook_run SET executor_snapshot = '{}' WHERE executor_snapshot IS NULL"
    )
    with op.batch_alter_table("valuz_playbook_run") as batch:
        batch.alter_column("content_snapshot", existing_type=sa.Text(), nullable=False)
        batch.alter_column("resolved_references", existing_type=_JSON, nullable=False)
        batch.alter_column("executor_snapshot", existing_type=_JSON, nullable=False)
        batch.create_index("ix_valuz_playbook_run_session_id", ["session_id"])
        batch.create_index("ix_valuz_playbook_run_task_id", ["task_id"])


def downgrade() -> None:
    # Migration 0038 cannot represent owner-global Definitions or Runs. Abort
    # before mutating the schema instead of manufacturing a hidden Project.
    connection = op.get_bind()
    global_definitions = connection.execute(
        sa.text("SELECT COUNT(*) FROM valuz_playbook_definition WHERE project_id IS NULL")
    ).scalar_one()
    global_runs = connection.execute(
        sa.text("SELECT COUNT(*) FROM valuz_playbook_run WHERE project_id IS NULL")
    ).scalar_one()
    if global_definitions or global_runs:
        raise RuntimeError("cannot downgrade 0039 with global Playbook definitions or runs")

    with op.batch_alter_table("valuz_playbook_run") as batch:
        batch.drop_index("ix_valuz_playbook_run_task_id")
        batch.drop_index("ix_valuz_playbook_run_session_id")
        batch.drop_column("task_id")
        batch.drop_column("session_id")
        batch.drop_column("executor_snapshot")
        batch.drop_column("extra_instruction")
        batch.drop_column("resolved_references")
        batch.drop_column("content_snapshot")
        batch.alter_column(
            "project_id",
            existing_type=sa.String(36),
            nullable=False,
        )

    with op.batch_alter_table("valuz_playbook_version") as batch:
        batch.drop_column("default_executor")
        batch.drop_column("reference_metadata")
        batch.drop_column("content")

    with op.batch_alter_table("valuz_playbook_definition") as batch:
        batch.alter_column(
            "project_id",
            existing_type=sa.String(36),
            nullable=False,
        )

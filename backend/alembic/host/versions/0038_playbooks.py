"""add versioned Playbook definitions/runs and Automation binding

Revision ID: 0038
Revises: 0037
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def identity() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
    ]


def times() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "valuz_playbook_definition",
        *identity(),
        sa.Column("project_id", sa.String(36), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("origin", sa.String(32), nullable=False, server_default="user"),
        sa.Column("source_definition_id", sa.String(36), nullable=True),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        *times(),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name="ck_playbook_definition_status",
        ),
        sa.CheckConstraint(
            "origin IN ('user', 'system_example_copy', 'fork')",
            name="ck_playbook_definition_origin",
        ),
        sa.UniqueConstraint("user_id", "project_id", "name", name="uq_playbook_project_name"),
    )
    op.create_table(
        "valuz_playbook_version",
        *identity(),
        sa.Column("definition_id", sa.String(36), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("applicability", _JSON, nullable=False),
        sa.Column("inputs", _JSON, nullable=False),
        sa.Column("context_reads", _JSON, nullable=False),
        sa.Column("stages", _JSON, nullable=False),
        sa.Column("required_skills", _JSON, nullable=False),
        sa.Column("allowed_skills", _JSON, nullable=False),
        sa.Column("conditions", _JSON, nullable=False),
        sa.Column("approvals", _JSON, nullable=False),
        sa.Column("outputs", _JSON, nullable=False),
        sa.Column("context_writes", _JSON, nullable=False),
        sa.Column("failure_policy", sa.String(32), nullable=False, server_default="stop"),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("produced_by_run", sa.String(128), nullable=True),
        sa.Column("base_version", sa.Integer(), nullable=True),
        *times(),
        sa.UniqueConstraint("definition_id", "version", name="uq_playbook_definition_version"),
    )
    op.create_table(
        "valuz_playbook_run",
        *identity(),
        sa.Column("definition_id", sa.String(36), nullable=False, index=True),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False, index=True),
        sa.Column("research_scope_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("trigger_kind", sa.String(16), nullable=False, server_default="user"),
        sa.Column("trigger_ref", sa.String(128), nullable=True),
        sa.Column("subject_refs", _JSON, nullable=False),
        sa.Column("input_snapshot", _JSON, nullable=False),
        sa.Column("context_snapshot", _JSON, nullable=False),
        sa.Column("plan", _JSON, nullable=False),
        sa.Column("tasks", _JSON, nullable=False),
        sa.Column("tool_calls", _JSON, nullable=False),
        sa.Column("approvals", _JSON, nullable=False),
        sa.Column("artifact_refs", _JSON, nullable=False),
        sa.Column("change_set_refs", _JSON, nullable=False),
        sa.Column("output_refs", _JSON, nullable=False),
        sa.Column("checkpoint", _JSON, nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.BigInteger(), nullable=True),
        sa.Column("completed_at", sa.BigInteger(), nullable=True),
        *times(),
        sa.CheckConstraint(
            "status IN ('queued', 'planning', 'running', 'waiting_approval', "
            "'completed', 'failed', 'stopped')",
            name="ck_playbook_run_status",
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('user', 'agent', 'automation', 'playbook', 'api')",
            name="ck_playbook_run_trigger_kind",
        ),
    )
    with op.batch_alter_table("valuz_automation") as batch:
        batch.add_column(sa.Column("playbook_definition_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("playbook_version", sa.Integer(), nullable=True))
        batch.create_index(
            "ix_valuz_automation_playbook_definition_id",
            ["playbook_definition_id"],
        )
    with op.batch_alter_table("valuz_automation_run") as batch:
        batch.add_column(sa.Column("playbook_run_id", sa.String(36), nullable=True))
        batch.create_index(
            "ix_valuz_automation_run_playbook_run_id",
            ["playbook_run_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("valuz_automation_run") as batch:
        batch.drop_index("ix_valuz_automation_run_playbook_run_id")
        batch.drop_column("playbook_run_id")
    with op.batch_alter_table("valuz_automation") as batch:
        batch.drop_index("ix_valuz_automation_playbook_definition_id")
        batch.drop_column("playbook_version")
        batch.drop_column("playbook_definition_id")
    op.drop_table("valuz_playbook_run")
    op.drop_table("valuz_playbook_version")
    op.drop_table("valuz_playbook_definition")

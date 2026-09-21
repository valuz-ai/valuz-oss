"""Give an automation three contracts: input, execution, result.

``valuz_automation`` gains ``execution_kind`` (``agent`` — everything that
existed — or ``code``: a program in the project, no agent), the code columns
(``code_runtime`` / ``code_entry`` / ``code_timeout_s``), the input contract
(``input_kind`` / ``input_schema`` / ``default_input``) and the result
contract (``result_kind`` / ``result_schema``). ``agent_kind`` / ``agent_slug``
become nullable because a program has no agent; the new
``ck_automation_agent_when_agent`` CHECK keeps them mandatory for agent rows.

``valuz_automation_run`` gains the per-run counterparts: ``input_json`` (the
effective object input), ``artifact_json`` / ``files_json`` (what the run
produced), ``executor_ref`` / ``log_tail`` (where and how a program ran),
``invoked_by_ref`` (who asked, for ``trigger_type='api'``) and
``cancel_requested_at``.

Existing rows keep their meaning through the server defaults:
``execution_kind='agent'``, ``input_kind='none'``, ``result_kind='conversation'``.

SQLite has to recreate ``valuz_automation`` to relax the two NOT NULLs and
rewrite ``ck_automation_agent_kind``; alembic's batch mode does that from a
reflection, and the five unrelated CHECKs come along (same technique as 0049).
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")

_AGENT_KIND_OLD = "agent_kind IN ('project_member', 'library_agent')"
_AGENT_KIND_NEW = "agent_kind IS NULL OR agent_kind IN ('project_member', 'library_agent')"
_NEW_CHECKS = (
    ("ck_automation_execution_kind", "execution_kind IN ('agent', 'code')"),
    (
        "ck_automation_code_entry_when_code",
        "(execution_kind = 'code' AND code_entry IS NOT NULL "
        "AND code_runtime IN ('python', 'shell')) OR execution_kind != 'code'",
    ),
    (
        "ck_automation_agent_when_agent",
        "execution_kind = 'code' OR (agent_kind IS NOT NULL AND agent_slug IS NOT NULL)",
    ),
    ("ck_automation_input_kind", "input_kind IN ('none', 'text', 'json')"),
    ("ck_automation_result_kind", "result_kind IN ('conversation', 'artifact')"),
)

_AUTOMATION_COLUMNS = (
    sa.Column("execution_kind", sa.String(16), nullable=False, server_default="agent"),
    sa.Column("code_runtime", sa.String(16), nullable=True),
    sa.Column("code_entry", sa.String(512), nullable=True),
    sa.Column("code_timeout_s", sa.Integer(), nullable=True),
    sa.Column("input_kind", sa.String(16), nullable=False, server_default="none"),
    sa.Column("input_schema", _JSON, nullable=True),
    sa.Column("default_input", _JSON, nullable=True),
    sa.Column("result_kind", sa.String(16), nullable=False, server_default="conversation"),
    sa.Column("result_schema", _JSON, nullable=True),
)

_RUN_COLUMNS = (
    sa.Column("input_json", _JSON, nullable=True),
    sa.Column("artifact_json", _JSON, nullable=True),
    sa.Column("files_json", _JSON, nullable=True),
    sa.Column("executor_ref", sa.String(128), nullable=True),
    sa.Column("log_tail", sa.Text(), nullable=True),
    sa.Column("invoked_by_ref", sa.String(128), nullable=True),
    sa.Column("cancel_requested_at", sa.BigInteger(), nullable=True),
)


def upgrade() -> None:
    with op.batch_alter_table("valuz_automation_run") as batch:
        for column in _RUN_COLUMNS:
            batch.add_column(column)

    if op.get_bind().dialect.name == "postgresql":
        for column in _AUTOMATION_COLUMNS:
            op.add_column("valuz_automation", column)
        op.alter_column("valuz_automation", "agent_kind", nullable=True)
        op.alter_column("valuz_automation", "agent_slug", nullable=True)
        op.drop_constraint("ck_automation_agent_kind", "valuz_automation", type_="check")
        op.create_check_constraint("ck_automation_agent_kind", "valuz_automation", _AGENT_KIND_NEW)
        for name, expr in _NEW_CHECKS:
            op.create_check_constraint(name, "valuz_automation", expr)
        return

    with op.batch_alter_table("valuz_automation", recreate="always") as batch:
        for column in _AUTOMATION_COLUMNS:
            batch.add_column(column)
        batch.alter_column("agent_kind", existing_type=sa.String(32), nullable=True)
        batch.alter_column("agent_slug", existing_type=sa.String(128), nullable=True)
        batch.drop_constraint("ck_automation_agent_kind", type_="check")
        batch.create_check_constraint("ck_automation_agent_kind", _AGENT_KIND_NEW)
        for name, expr in _NEW_CHECKS:
            batch.create_check_constraint(name, expr)


def downgrade() -> None:
    """Back to agent-only automations.

    Code rows cannot satisfy the restored NOT NULL on ``agent_slug`` and mean
    nothing without their columns, so they are deleted rather than rewritten
    into agent rows that would then run a prompt nobody wrote.
    """
    op.execute(
        "DELETE FROM valuz_automation_run WHERE automation_id IN "
        "(SELECT id FROM valuz_automation WHERE execution_kind = 'code')"
    )
    op.execute("DELETE FROM valuz_automation WHERE execution_kind = 'code'")

    if op.get_bind().dialect.name == "postgresql":
        for name, _expr in _NEW_CHECKS:
            op.drop_constraint(name, "valuz_automation", type_="check")
        op.drop_constraint("ck_automation_agent_kind", "valuz_automation", type_="check")
        op.create_check_constraint("ck_automation_agent_kind", "valuz_automation", _AGENT_KIND_OLD)
        op.alter_column("valuz_automation", "agent_slug", nullable=False)
        op.alter_column("valuz_automation", "agent_kind", nullable=False)
        for column in reversed(_AUTOMATION_COLUMNS):
            op.drop_column("valuz_automation", column.name)
    else:
        with op.batch_alter_table("valuz_automation", recreate="always") as batch:
            for name, _expr in _NEW_CHECKS:
                batch.drop_constraint(name, type_="check")
            batch.drop_constraint("ck_automation_agent_kind", type_="check")
            batch.create_check_constraint("ck_automation_agent_kind", _AGENT_KIND_OLD)
            batch.alter_column("agent_slug", existing_type=sa.String(128), nullable=False)
            batch.alter_column("agent_kind", existing_type=sa.String(32), nullable=False)
            for column in reversed(_AUTOMATION_COLUMNS):
                batch.drop_column(column.name)

    with op.batch_alter_table("valuz_automation_run") as batch:
        for column in reversed(_RUN_COLUMNS):
            batch.drop_column(column.name)

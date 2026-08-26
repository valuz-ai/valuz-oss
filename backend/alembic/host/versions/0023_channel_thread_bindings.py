"""channels: add external thread bindings

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "valuz_channel_thread_binding"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("channel_instance_id", sa.String(length=128), nullable=False),
        sa.Column("external_chat_id", sa.String(length=256), nullable=False),
        sa.Column("external_thread_id", sa.String(length=256), nullable=False),
        sa.Column("agent_slug", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "channel_instance_id",
            "external_chat_id",
            "external_thread_id",
            "agent_slug",
            "project_id",
            name="uq_channel_thread_binding_route",
        ),
    )
    op.create_index(
        "ix_valuz_channel_thread_binding_user_id",
        _TABLE,
        ["user_id"],
    )
    op.create_index(
        "ix_valuz_channel_thread_binding_channel_instance_id",
        _TABLE,
        ["channel_instance_id"],
    )
    op.create_index(
        "ix_valuz_channel_thread_binding_external_chat_id",
        _TABLE,
        ["external_chat_id"],
    )
    op.create_index(
        "ix_valuz_channel_thread_binding_external_thread_id",
        _TABLE,
        ["external_thread_id"],
    )
    op.create_index("ix_valuz_channel_thread_binding_agent_slug", _TABLE, ["agent_slug"])
    op.create_index("ix_valuz_channel_thread_binding_project_id", _TABLE, ["project_id"])
    op.create_index("ix_valuz_channel_thread_binding_session_id", _TABLE, ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_valuz_channel_thread_binding_session_id", table_name=_TABLE)
    op.drop_index("ix_valuz_channel_thread_binding_project_id", table_name=_TABLE)
    op.drop_index("ix_valuz_channel_thread_binding_agent_slug", table_name=_TABLE)
    op.drop_index(
        "ix_valuz_channel_thread_binding_external_thread_id",
        table_name=_TABLE,
    )
    op.drop_index(
        "ix_valuz_channel_thread_binding_external_chat_id",
        table_name=_TABLE,
    )
    op.drop_index(
        "ix_valuz_channel_thread_binding_channel_instance_id",
        table_name=_TABLE,
    )
    op.drop_index("ix_valuz_channel_thread_binding_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)

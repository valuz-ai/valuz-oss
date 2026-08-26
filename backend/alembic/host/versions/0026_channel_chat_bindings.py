"""channels: bind an external chat to a project

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-28

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "valuz_channel_chat_binding"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("channel_instance_id", sa.String(length=128), nullable=False),
        sa.Column("external_chat_id", sa.String(length=256), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("default_agent_slug", sa.String(length=128), nullable=True),
        sa.Column("external_chat_name", sa.String(length=256), nullable=True),
        sa.Column("bound_by_external_user", sa.String(length=256), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "channel_instance_id",
            "external_chat_id",
            name="uq_channel_chat_binding_chat",
        ),
    )
    op.create_index("ix_valuz_channel_chat_binding_user_id", _TABLE, ["user_id"])
    op.create_index(
        "ix_valuz_channel_chat_binding_channel_instance_id",
        _TABLE,
        ["channel_instance_id"],
    )
    op.create_index(
        "ix_valuz_channel_chat_binding_external_chat_id", _TABLE, ["external_chat_id"]
    )
    op.create_index("ix_valuz_channel_chat_binding_project_id", _TABLE, ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_valuz_channel_chat_binding_project_id", table_name=_TABLE)
    op.drop_index("ix_valuz_channel_chat_binding_external_chat_id", table_name=_TABLE)
    op.drop_index(
        "ix_valuz_channel_chat_binding_channel_instance_id", table_name=_TABLE
    )
    op.drop_index("ix_valuz_channel_chat_binding_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)

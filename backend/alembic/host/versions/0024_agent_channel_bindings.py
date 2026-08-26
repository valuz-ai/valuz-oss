"""channels: add agent channel bindings

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-27

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "valuz_agent_channel_binding"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("channel_instance_id", sa.String(length=128), nullable=False),
        sa.Column("agent_slug", sa.String(length=128), nullable=False),
        sa.Column("bot_id", sa.String(length=256), nullable=False),
        sa.Column("secret_ref", sa.String(length=256), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("bot_name", sa.String(length=128), nullable=True),
        sa.Column("ws_url", sa.String(length=512), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "platform",
            "agent_slug",
            name="uq_agent_channel_binding_agent",
        ),
    )
    op.create_index("ix_valuz_agent_channel_binding_user_id", _TABLE, ["user_id"])
    op.create_index("ix_valuz_agent_channel_binding_platform", _TABLE, ["platform"])
    op.create_index(
        "ix_valuz_agent_channel_binding_channel_instance_id",
        _TABLE,
        ["channel_instance_id"],
    )
    op.create_index("ix_valuz_agent_channel_binding_agent_slug", _TABLE, ["agent_slug"])
    op.create_index("ix_valuz_agent_channel_binding_bot_id", _TABLE, ["bot_id"])


def downgrade() -> None:
    op.drop_index("ix_valuz_agent_channel_binding_bot_id", table_name=_TABLE)
    op.drop_index("ix_valuz_agent_channel_binding_agent_slug", table_name=_TABLE)
    op.drop_index(
        "ix_valuz_agent_channel_binding_channel_instance_id",
        table_name=_TABLE,
    )
    op.drop_index("ix_valuz_agent_channel_binding_platform", table_name=_TABLE)
    op.drop_index("ix_valuz_agent_channel_binding_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)

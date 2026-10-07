"""application plugins: storage, config, and plugin-owned automations

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-07

ADR-034 / docs task card 04 §A.

* ``valuz_app_plugin_storage`` — per user x plugin key-value store (JSON values).
* ``valuz_app_plugin_config`` — per user x plugin settings.
* ``valuz_automation`` gains nullable ``app_plugin_id`` / ``app_plugin_name``: the
  code automations a plugin declares in its manifest are created on install and
  marked with the plugin that owns them (NULL for every other automation).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "valuz_app_plugin_storage",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("app_plugin_id", sa.String(length=128), nullable=False),
        sa.Column("key", sa.String(length=256), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("user_id", "app_plugin_id", "key"),
    )
    op.create_table(
        "valuz_app_plugin_config",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("app_plugin_id", sa.String(length=128), nullable=False),
        sa.Column("values_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.BigInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("user_id", "app_plugin_id"),
    )
    with op.batch_alter_table("valuz_automation") as batch:
        batch.add_column(sa.Column("app_plugin_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("app_plugin_name", sa.String(length=256), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("valuz_automation") as batch:
        batch.drop_column("app_plugin_name")
        batch.drop_column("app_plugin_id")
    op.drop_table("valuz_app_plugin_config")
    op.drop_table("valuz_app_plugin_storage")

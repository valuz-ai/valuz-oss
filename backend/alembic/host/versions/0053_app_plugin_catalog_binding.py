"""App Plugin managed automation source identity.

Revision ID: 0053
Revises: 0052

NULL source kinds are unconfirmed existing managed rows. No installation,
project, storage or configuration data is deleted or backfilled by inference.
"""

import sqlalchemy as sa

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("valuz_automation") as batch:
        batch.add_column(sa.Column("app_plugin_source_kind", sa.String(32), nullable=True))
        batch.add_column(sa.Column("app_plugin_catalog_binding", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("valuz_automation") as batch:
        batch.drop_column("app_plugin_catalog_binding")
        batch.drop_column("app_plugin_source_kind")

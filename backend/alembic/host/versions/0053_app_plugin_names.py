"""Canonical application plugin storage and automation ownership names.

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-07

Rename in place so per-user configuration, storage quotas and automation owners
survive the terminology change. Revision 0052 remains historical and untouched.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rename(old: str, new: str) -> None:
    for suffix in ("storage", "config"):
        old_table = f"valuz_{old}_{suffix}"
        new_table = f"valuz_{new}_{suffix}"
        op.rename_table(old_table, new_table)
        with op.batch_alter_table(new_table) as batch:
            batch.alter_column(
                f"{old}_id", new_column_name=f"{new}_id", existing_type=sa.String(128)
            )
        if op.get_bind().dialect.name == "postgresql":
            op.execute(
                f'ALTER TABLE "{new_table}" '
                f'RENAME CONSTRAINT "{old_table}_pkey" TO "{new_table}_pkey"'
            )
    with op.batch_alter_table("valuz_automation") as batch:
        batch.alter_column(f"{old}_id", new_column_name=f"{new}_id", existing_type=sa.String(128))
        batch.alter_column(
            f"{old}_name", new_column_name=f"{new}_name", existing_type=sa.String(256)
        )


def upgrade() -> None:
    _rename("extension", "app_plugin")
    _rename_notifications("extension", "app_plugin", "plugin_id", "app_plugin_id")


def downgrade() -> None:
    _rename_notifications("app_plugin", "extension", "app_plugin_id", "plugin_id")
    _rename("app_plugin", "extension")


def _rename_notifications(old_kind: str, new_kind: str, old_key: str, new_key: str) -> None:
    """Preserve existing ledger entries while canonicalising their projection."""
    table = sa.table(
        "valuz_notification",
        sa.column("id", sa.String),
        sa.column("kind", sa.String),
        sa.column("payload", sa.JSON),
    )
    connection = op.get_bind()
    for row in connection.execute(
        sa.select(table.c.id, table.c.payload).where(table.c.kind == old_kind)
    ):
        payload = row.payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = None
        if isinstance(payload, dict) and old_key in payload:
            payload = {**payload}
            payload[new_key] = payload.pop(old_key)
            connection.execute(
                table.update().where(table.c.id == row.id).values(kind=new_kind, payload=payload)
            )
        else:
            connection.execute(table.update().where(table.c.id == row.id).values(kind=new_kind))

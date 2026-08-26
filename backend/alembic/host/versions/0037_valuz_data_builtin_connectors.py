"""replace Reportify search/stock connectors with Valuz Data built-ins

Revision ID: 0037
Revises: 0036
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_agent_bindings(bind) -> None:
    agents = sa.table(
        "valuz_agent",
        sa.column("id", sa.String()),
        sa.column("connector_types", sa.JSON()),
    )
    rows = bind.execute(sa.select(agents.c.id, agents.c.connector_types)).mappings()
    for row in rows:
        slugs = row["connector_types"]
        if not isinstance(slugs, list) or "valuz-stock" not in slugs:
            continue
        replaced = list(dict.fromkeys("valuz-data" if s == "valuz-stock" else s for s in slugs))
        bind.execute(
            sa.update(agents).where(agents.c.id == row["id"]).values(connector_types=replaced)
        )


def upgrade() -> None:
    bind = op.get_bind()

    # Project bindings are a set. Avoid a PK collision when a user already
    # installed valuz-data manually before this migration.
    old_bindings = bind.execute(
        sa.text("SELECT project_id, user_id FROM valuz_project_connector WHERE slug = 'valuz-stock'")
    ).mappings()
    for row in old_bindings:
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM valuz_project_connector "
                "WHERE project_id = :project_id AND user_id = :user_id AND slug = 'valuz-data'"
            ),
            row,
        ).first()
        if exists:
            bind.execute(
                sa.text(
                    "DELETE FROM valuz_project_connector "
                    "WHERE project_id = :project_id AND user_id = :user_id AND slug = 'valuz-stock'"
                ),
                row,
            )
        else:
            bind.execute(
                sa.text(
                    "UPDATE valuz_project_connector SET slug = 'valuz-data' "
                    "WHERE project_id = :project_id AND user_id = :user_id AND slug = 'valuz-stock'"
                ),
                row,
            )

    old_rows = bind.execute(
        sa.text("SELECT id, user_id FROM valuz_connector WHERE slug = 'valuz-stock'")
    ).mappings()
    for row in old_rows:
        duplicate = bind.execute(
            sa.text(
                "SELECT id FROM valuz_connector "
                "WHERE user_id = :user_id AND slug = 'valuz-data'"
            ),
            row,
        ).first()
        bind.execute(
            sa.text("DELETE FROM valuz_connector_oauth WHERE connector_id = :id"), row
        )
        if duplicate:
            bind.execute(sa.text("DELETE FROM valuz_connector_attr WHERE connector_id = :id"), row)
            bind.execute(sa.text("DELETE FROM valuz_connector WHERE id = :id"), row)
        else:
            bind.execute(
                sa.text(
                    "UPDATE valuz_connector SET slug = 'valuz-data', "
                    "display_name = 'Valuz · 数据', "
                    "description = '完整的 Valuz Data 金融数据与文档工具集', "
                    "connector_type = 'builtin', url = 'https://data.valuz.cn/mcp', "
                    "auth_type = 'oauth', enabled = :disabled, status = 'pending_auth', "
                    "error_message = NULL WHERE id = :id"
                ).bindparams(sa.bindparam("disabled", type_=sa.Boolean())),
                {"id": row["id"], "disabled": False},
            )

    # Normalize both migrated rows and a pre-existing valuz-data row. The
    # latter may have been manually added against another provider; keeping its
    # OAuth family would send a token to the new Valuz Data audience.
    data_rows = bind.execute(
        sa.text("SELECT id FROM valuz_connector WHERE slug = 'valuz-data'")
    ).mappings()
    for row in data_rows:
        bind.execute(sa.text("DELETE FROM valuz_connector_oauth WHERE connector_id = :id"), row)
        bind.execute(
            sa.text(
                "UPDATE valuz_connector SET display_name = 'Valuz · 数据', "
                "description = '完整的 Valuz Data 金融数据与文档工具集', "
                "connector_type = 'builtin', url = 'https://data.valuz.cn/mcp', "
                "auth_type = 'oauth', enabled = :disabled, status = 'pending_auth', "
                "error_message = NULL WHERE id = :id"
            ).bindparams(sa.bindparam("disabled", type_=sa.Boolean())),
            {"id": row["id"], "disabled": False},
        )

    search_rows = bind.execute(
        sa.text("SELECT id FROM valuz_connector WHERE slug = 'valuz-search'")
    ).mappings()
    for row in search_rows:
        bind.execute(sa.text("DELETE FROM valuz_connector_oauth WHERE connector_id = :id"), row)
        bind.execute(
            sa.text(
                "UPDATE valuz_connector SET connector_type = 'builtin', "
                "url = 'https://data.valuz.cn/mcp/search', auth_type = 'oauth', "
                "enabled = :disabled, status = 'pending_auth', error_message = NULL "
                "WHERE id = :id"
            ).bindparams(sa.bindparam("disabled", type_=sa.Boolean())),
            {"id": row["id"], "disabled": False},
        )

    _replace_agent_bindings(bind)


def downgrade() -> None:
    # Provider credentials are intentionally not reconstructed on downgrade.
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE valuz_connector SET slug = 'valuz-stock', "
            "display_name = 'Valuz · 行情', connector_type = 'builtin', "
            "url = 'https://mcp.reportify.cn/stock/mcp', enabled = :disabled, "
            "status = 'pending_auth' WHERE slug = 'valuz-data'"
        ).bindparams(sa.bindparam("disabled", type_=sa.Boolean())),
        {"disabled": False},
    )

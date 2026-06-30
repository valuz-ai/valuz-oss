"""connector: scope slug uniqueness by owner

Connector slugs are user-scoped everywhere in the service/datastore layer
(``get_by_slug(user_id, slug)``), but the DB schema still enforced a global
``UNIQUE(slug)``. On shared backends that prevented two users from installing
the same catalog connector. Replace it with ``UNIQUE(user_id, slug)``.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-30

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLUMNS = (
    "slug",
    "display_name",
    "description",
    "connector_type",
    "transport",
    "url",
    "auth_type",
    "command",
    "working_dir",
    "enabled",
    "status",
    "tool_count",
    "last_tested_at",
    "error_message",
    "id",
    "created_at",
    "updated_at",
    "user_id",
    "oauth_metadata",
    "args",
)


def _connector_columns() -> list[sa.Column]:
    return [
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("connector_type", sa.String(length=32), nullable=False),
        sa.Column("transport", sa.String(length=16), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("auth_type", sa.String(length=32), nullable=False),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("working_dir", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tool_count", sa.Integer(), nullable=True),
        sa.Column("last_tested_at", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("oauth_metadata", sa.Text(), nullable=True),
        sa.Column("args", sa.Text(), nullable=True),
    ]


def _copy_sql(dst: str, src: str) -> sa.TextClause:
    cols = ", ".join(_COLUMNS)
    return sa.text(f"INSERT INTO {dst} ({cols}) SELECT {cols} FROM {src}")


def _rebuild_sqlite_connector(unique: sa.UniqueConstraint) -> None:
    tmp = "valuz_connector__tmp_0012"
    op.create_table(
        tmp,
        *_connector_columns(),
        sa.CheckConstraint(
            "auth_type IN ('none', 'bearer', 'oauth')",
            name="ck_valuz_connector_auth_type",
        ),
        sa.CheckConstraint(
            "transport IN ('http', 'sse', 'stdio')",
            name="ck_valuz_connector_transport",
        ),
        sa.PrimaryKeyConstraint("id"),
        unique,
    )
    op.execute(_copy_sql(tmp, "valuz_connector"))
    op.drop_table("valuz_connector")
    op.rename_table(tmp, "valuz_connector")
    op.create_index(op.f("ix_valuz_connector_user_id"), "valuz_connector", ["user_id"])


def _find_unique_name(columns: list[str]) -> str:
    bind = op.get_bind()
    for constraint in sa.inspect(bind).get_unique_constraints("valuz_connector"):
        if constraint.get("column_names") == columns and constraint.get("name"):
            return str(constraint["name"])
    raise RuntimeError(f"Could not find valuz_connector unique constraint for {columns!r}")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _rebuild_sqlite_connector(
            sa.UniqueConstraint("user_id", "slug", name="uq_valuz_connector_user_slug")
        )
        return

    op.drop_constraint(_find_unique_name(["slug"]), "valuz_connector", type_="unique")
    op.create_unique_constraint(
        "uq_valuz_connector_user_slug", "valuz_connector", ["user_id", "slug"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _rebuild_sqlite_connector(sa.UniqueConstraint("slug"))
        return

    op.drop_constraint("uq_valuz_connector_user_slug", "valuz_connector", type_="unique")
    op.create_unique_constraint("uq_valuz_connector_slug", "valuz_connector", ["slug"])

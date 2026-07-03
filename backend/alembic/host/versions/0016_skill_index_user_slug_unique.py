"""skills: add per-owner unique skill slug

Keep the existing ``valuz_skill_index.id`` primary key unchanged and add the
business uniqueness rule for one owner's skill slug.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-03

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ux_valuz_skill_index_user_slug"
_TABLE_NAME = "valuz_skill_index"


def _has_index(index_name: str) -> bool:
    return any(
        index["name"] == index_name
        for index in sa.inspect(op.get_bind()).get_indexes(_TABLE_NAME)
    )


def _assert_no_per_owner_duplicate_slugs() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT user_id, slug
                FROM valuz_skill_index
                GROUP BY user_id, slug
                HAVING COUNT(*) > 1
                """
            )
        )
        .fetchall()
    )
    if not duplicates:
        return
    slugs = ", ".join(f"{row[0]}:{row[1]}" for row in duplicates[:10])
    extra = "" if len(duplicates) <= 10 else f" (+{len(duplicates) - 10} more)"
    raise RuntimeError(
        "Cannot add skill-index unique slug constraint while duplicate "
        f"user/slug rows exist: {slugs}{extra}"
    )


def upgrade() -> None:
    if _has_index(_INDEX_NAME):
        return
    _assert_no_per_owner_duplicate_slugs()
    op.create_index(
        _INDEX_NAME,
        _TABLE_NAME,
        ["user_id", "slug"],
        unique=True,
    )


def downgrade() -> None:
    if _has_index(_INDEX_NAME):
        op.drop_index(_INDEX_NAME, table_name=_TABLE_NAME)

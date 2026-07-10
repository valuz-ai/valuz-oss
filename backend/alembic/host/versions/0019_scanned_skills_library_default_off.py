"""skills: default the library switch OFF for system-scanned skills

Skills discovered by the filesystem scan (~/.claude/skills, ~/.codex/skills,
or folders found in the shared user library root) were never opted into
Valuz, yet every chat session auto-carried ALL of them into the prompt. New
user-scope rows now insert with ``library_enabled = false`` (service
``_upsert_skill_row``); this migration applies the same default to existing
rows so current installs stop flooding chat prompts immediately. Only
``discovered`` user-scope rows are touched — skills the user created or
imported through Valuz (``creation_origin`` created / imported) and official /
built-in rows keep their switch on.

The flip is intentionally coarse: a scanned row the user explicitly switched
ON is indistinguishable from the old default, so it is reset too and must be
re-enabled once on the Skills page.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-10

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREDICATE = (
    "scope = 'user' AND (creation_origin IS NULL OR creation_origin = 'discovered')"
)


def upgrade() -> None:
    op.execute(
        sa.text(
            f"UPDATE valuz_skill_index SET library_enabled = false WHERE {_PREDICATE}"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            f"UPDATE valuz_skill_index SET library_enabled = true WHERE {_PREDICATE}"
        )
    )

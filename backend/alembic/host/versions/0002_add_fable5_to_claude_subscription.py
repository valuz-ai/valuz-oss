"""Add claude-fable-5 to the ch-claude-subscription recommended models.

``subscription_models.json`` gained ``claude-fable-5`` (claude-agent-sdk
0.2.95 / CLI 2.1.170), but the provider seeder is pure-insert and
``_resolve_model_options`` prefers the row's persisted ``model_ids``
snapshot over the descriptor, so installs seeded before the bump never
see the new model in the picker. Per the seed contract
(``valuz_agent/seeds/providers.py``), built-in row updates land as
alembic revisions — this one merges ``claude-fable-5`` into the
``ch-claude-subscription`` row's list.

Merge-in, not overwrite: the row's ``model_ids`` may carry user edits
(the channel-edit dialog writes the same column), so we only prepend
the one missing id and otherwise leave the list untouched. Rows where
``model_ids`` is NULL fall back to the live descriptor (which already
includes fable-5) and are skipped; unparseable JSON is left alone.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-09

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_ROW_ID = "ch-claude-subscription"
_NEW_MODEL = "claude-fable-5"


def _load_row_model_ids(conn: sa.Connection) -> list[str] | None:
    """Return the row's parsed model list, or ``None`` to skip the row.

    ``None`` covers: row absent, ``model_ids`` NULL (descriptor fallback
    already serves the new model), or JSON that doesn't parse to a list
    (defensive — a migration must never destroy data it can't read).
    """
    raw = conn.execute(
        sa.text("SELECT model_ids FROM valuz_provider WHERE id = :id"),
        {"id": _ROW_ID},
    ).scalar()
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    return [m for m in parsed if isinstance(m, str)]


def _write_row_model_ids(conn: sa.Connection, models: list[str]) -> None:
    conn.execute(
        sa.text("UPDATE valuz_provider SET model_ids = :ids WHERE id = :id"),
        {"ids": json.dumps(models), "id": _ROW_ID},
    )


def upgrade() -> None:
    conn = op.get_bind()
    models = _load_row_model_ids(conn)
    if models is None or _NEW_MODEL in models:
        return
    _write_row_model_ids(conn, [_NEW_MODEL, *models])


def downgrade() -> None:
    conn = op.get_bind()
    models = _load_row_model_ids(conn)
    if models is None or _NEW_MODEL not in models:
        return
    remaining = [m for m in models if m != _NEW_MODEL]
    # An emptied list would flip the row into the explicit-empty ("[]")
    # state, which suppresses the descriptor fallback. NULL restores the
    # pre-snapshot fallback semantics instead.
    if remaining:
        _write_row_model_ids(conn, remaining)
    else:
        conn.execute(
            sa.text("UPDATE valuz_provider SET model_ids = NULL WHERE id = :id"),
            {"id": _ROW_ID},
        )

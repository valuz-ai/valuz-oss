"""0046 creates ``valuz_feedback`` with the subject unique index; downgrade drops it."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def config(path: Path) -> Config:
    migrations = Path(__file__).resolve().parents[2] / "alembic" / "host"
    value = Config(str(migrations / "alembic.ini"))
    value.set_main_option("script_location", str(migrations))
    value.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return value


def _inspect(db_path: Path) -> tuple[set[str], set[str], set[str]]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        inspector = sa.inspect(engine)
        tables = set(inspector.get_table_names())
        if "valuz_feedback" not in tables:
            return tables, set(), set()
        columns = {c["name"] for c in inspector.get_columns("valuz_feedback")}
        uniques = {
            u["name"] for u in inspector.get_unique_constraints("valuz_feedback") if u["name"]
        }
        return tables, columns, uniques
    finally:
        engine.dispose()


def test_feedback_table_upgrade_and_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``alembic/host/env.py`` prefers ``DATABASE_URL`` over the config's
    # ``sqlalchemy.url``; an app-booting test earlier in the run leaves that
    # env var pointing at ITS temp db, so migrations would land there instead.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "host.db"
    cfg = config(db_path)
    command.upgrade(cfg, "head")
    tables, columns, uniques = _inspect(db_path)
    assert "valuz_feedback" in tables
    assert {
        "id",
        "user_id",
        "session_id",
        "message_id",
        "action",
        "block_ref",
        "value",
        "reason_code",
        "reason",
        "target_type",
        "target_id",
        "source",
        "surface",
        "occurrences",
        "metadata",
        "created_at",
        "updated_at",
    } <= columns
    assert "uq_valuz_feedback_subject" in uniques

    command.downgrade(cfg, "0045")
    tables, _, _ = _inspect(db_path)
    assert "valuz_feedback" not in tables

    command.upgrade(cfg, "head")
    assert "valuz_feedback" in _inspect(db_path)[0]


def test_model_matches_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from valuz_agent.modules.feedback.models import FeedbackRow

    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "host.db"
    command.upgrade(config(db_path), "head")
    _, migrated, _ = _inspect(db_path)
    declared = {c.name for c in FeedbackRow.__table__.columns}
    assert declared == migrated, declared ^ migrated

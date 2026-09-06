"""0045 adds the proposal lifetime columns to valuz_operation_record."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def config(path: Path) -> Config:
    migrations = Path(__file__).resolve().parents[2] / "alembic" / "host"
    value = Config(str(migrations / "alembic.ini"))
    value.set_main_option("script_location", str(migrations))
    value.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return value


def _columns(db_path: Path) -> set[str]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        return {item["name"] for item in sa.inspect(engine).get_columns("valuz_operation_record")}
    finally:
        engine.dispose()


def test_expiry_and_supersede_columns_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "host.db"
    cfg = config(db_path)
    command.upgrade(cfg, "head")
    assert {"expires_at", "superseded_by_id"} <= _columns(db_path)

    command.downgrade(cfg, "0044")
    assert not ({"expires_at", "superseded_by_id"} & _columns(db_path))

    command.upgrade(cfg, "head")
    assert {"expires_at", "superseded_by_id"} <= _columns(db_path)

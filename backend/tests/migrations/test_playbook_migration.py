"""Host migration keeps Playbook ORM and Automation binding columns aligned."""

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


def test_playbook_tables_and_automation_binding_upgrade_and_downgrade(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "host.db"
    cfg = config(db_path)
    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "valuz_playbook_definition",
        "valuz_playbook_version",
        "valuz_playbook_run",
    } <= tables
    automation = {item["name"] for item in inspector.get_columns("valuz_automation")}
    assert {"playbook_definition_id", "playbook_version"} <= automation
    automation_run = {item["name"] for item in inspector.get_columns("valuz_automation_run")}
    assert "playbook_run_id" in automation_run
    engine.dispose()

    command.downgrade(cfg, "0037")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    inspector = sa.inspect(engine)
    assert "valuz_playbook_definition" not in set(inspector.get_table_names())
    automation = {item["name"] for item in inspector.get_columns("valuz_automation")}
    assert "playbook_definition_id" not in automation
    automation_run = {item["name"] for item in inspector.get_columns("valuz_automation_run")}
    assert "playbook_run_id" not in automation_run
    engine.dispose()

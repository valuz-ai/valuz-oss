"""0052: plugin storage / config tables and the automation ownership columns, both ways."""

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


def _cols(engine: sa.Engine, table: str) -> dict[str, dict]:
    return {c["name"]: c for c in sa.inspect(engine).get_columns(table)}


def test_upgrade_and_downgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "host.db"
    cfg = config(db_path)
    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite:///{db_path}")

    storage = _cols(engine, "valuz_extension_storage")
    assert set(storage) == {"user_id", "extension_id", "key", "value_json", "size", "updated_at"}
    assert sa.inspect(engine).get_pk_constraint("valuz_extension_storage")[
        "constrained_columns"
    ] == ["user_id", "extension_id", "key"]
    config_cols = _cols(engine, "valuz_extension_config")
    assert set(config_cols) == {"user_id", "extension_id", "values_json", "updated_at"}
    automation = _cols(engine, "valuz_automation")
    for name in ("extension_id", "extension_name"):
        assert name in automation and automation[name]["nullable"] is True
    checks = {c["name"] for c in sa.inspect(engine).get_check_constraints("valuz_automation")}
    assert {"ck_automation_execution_kind", "ck_automation_trigger_kind"} <= checks

    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO valuz_extension_storage VALUES ('u','acme.x','k','1',1,5)")
        )
        with pytest.raises(sa.exc.IntegrityError):  # the primary key is (user, plugin, key)
            conn.execute(
                sa.text("INSERT INTO valuz_extension_storage VALUES ('u','acme.x','k','2',1,6)")
            )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO valuz_automation (id, user_id, name, project_id, prompt_template, "
                "action_kind, worktree, trigger_kind, status, execution_kind, input_kind, "
                "result_kind, code_runtime, code_entry, extension_id, extension_name, "
                "created_at, updated_at) VALUES ('a1','u','n','p','','chat',0,'manual','enabled',"
                "'code','none','artifact','python','x.py','acme.x','Acme',0,0)"
            )
        )
    engine.dispose()

    command.downgrade(cfg, "0051")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    tables = set(sa.inspect(engine).get_table_names())
    assert "valuz_extension_storage" not in tables and "valuz_extension_config" not in tables
    automation = _cols(engine, "valuz_automation")
    assert "extension_id" not in automation and "extension_name" not in automation
    checks = {c["name"] for c in sa.inspect(engine).get_check_constraints("valuz_automation")}
    assert {"ck_automation_execution_kind", "ck_automation_trigger_kind"} <= checks
    with engine.connect() as conn:  # the row itself survives, without the plugin marker
        assert conn.execute(sa.text("SELECT id FROM valuz_automation")).scalars().all() == ["a1"]
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    assert "valuz_extension_storage" in sa.inspect(engine).get_table_names()
    assert _cols(engine, "valuz_automation")["extension_id"]["nullable"] is True
    engine.dispose()

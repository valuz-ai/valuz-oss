"""0053 adds internal provenance without guessing or removing existing owner data."""

from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def test_provenance_upgrade_downgrade_preserves_existing_plugin_state(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "host.db"
    migrations = Path(__file__).resolve().parents[2] / "alembic" / "host"
    config = Config(str(migrations / "alembic.ini"))
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, "0052")
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as db:
        db.execute(
            sa.text(
                "INSERT INTO valuz_app_plugin_storage VALUES "
                "('u','extension.dashboard','k','1',1,5)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO valuz_app_plugin_config VALUES "
                "('u','extension.dashboard','{\"extension_id\":\"literal\"}',5)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO valuz_automation (id,user_id,name,project_id,prompt_template,"
                "action_kind,worktree,trigger_kind,status,execution_kind,input_kind,result_kind,"
                "code_runtime,code_entry,app_plugin_id,app_plugin_name,created_at,updated_at)"
                " VALUES ('a','u','n','p','','chat',0,'manual','paused','code','none','artifact',"
                "'python','x.py','extension.dashboard','extension',0,0)"
            )
        )
    engine.dispose()
    command.upgrade(config, "0053")
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as db:
        row = db.execute(
            sa.text(
                "SELECT app_plugin_id,project_id,status,app_plugin_source_kind,"
                "app_plugin_catalog_binding FROM valuz_automation"
            )
        ).one()
        assert tuple(row) == ("extension.dashboard", "p", "paused", None, None)
        assert (
            db.execute(sa.text("SELECT value_json FROM valuz_app_plugin_storage")).scalar() == "1"
        )
        assert (
            db.execute(sa.text("SELECT values_json FROM valuz_app_plugin_config")).scalar()
            == '{"extension_id":"literal"}'
        )
    engine.dispose()
    command.downgrade(config, "0052")
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as db:
        assert tuple(
            db.execute(
                sa.text("SELECT app_plugin_id,project_id,status FROM valuz_automation")
            ).one()
        ) == ("extension.dashboard", "p", "paused")
        assert (
            db.execute(sa.text("SELECT value_json FROM valuz_app_plugin_storage")).scalar() == "1"
        )
        assert (
            db.execute(sa.text("SELECT values_json FROM valuz_app_plugin_config")).scalar()
            == '{"extension_id":"literal"}'
        )
    assert "app_plugin_catalog_binding" not in {
        c["name"] for c in sa.inspect(engine).get_columns("valuz_automation")
    }
    engine.dispose()

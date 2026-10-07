"""0054 preserves pending inputs and upstream catalog identity when rolled back."""

from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def test_input_receipts_upgrade_and_downgrade(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    migrations = Path(__file__).resolve().parents[2] / "alembic" / "host"
    cfg = Config(str(migrations / "alembic.ini"))
    cfg.set_main_option("script_location", str(migrations))
    db = tmp_path / "host.db"
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db}")
    command.upgrade(cfg, "0053")
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as connection:
        connection.execute(
            sa.text("""
            INSERT INTO valuz_queued_input
            (id,user_id,created_at,updated_at,session_id,input,status,position)
            VALUES ('input','owner',1,1,'main','{"text":"pending"}','queued',0)
        """)
        )
        connection.execute(
            sa.text(
                "INSERT INTO valuz_automation (id,user_id,name,project_id,prompt_template,"
                "action_kind,worktree,trigger_kind,status,execution_kind,input_kind,result_kind,"
                "code_runtime,code_entry,app_plugin_id,app_plugin_name,created_at,updated_at,"
                "app_plugin_source_kind,app_plugin_catalog_binding) VALUES "
                "('automation','owner','n','project','','chat',0,'manual','paused','code',"
                "'none','artifact','python','job.py','extension.dashboard','Dashboard',0,0,"
                "'builtin',:binding)"
            ),
            {"binding": '{"source_id":"catalog-job"}'},
        )
    command.upgrade(cfg, "0054")
    cols = {c["name"] for c in sa.inspect(engine).get_columns("valuz_queued_input")}
    assert {"completed_at", "output_message_id", "result_summary"} <= cols
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE valuz_automation SET target_session_id='main' WHERE id='automation'")
        )
    with engine.connect() as connection:
        status = connection.execute(
            sa.text("SELECT status FROM valuz_queued_input WHERE id='input'")
        )
        assert status.scalar_one() == "queued"
        assert connection.execute(
            sa.text(
                "SELECT project_id,app_plugin_source_kind,app_plugin_catalog_binding "
                "FROM valuz_automation WHERE id='automation'"
            )
        ).one() == ("project", "builtin", '{"source_id":"catalog-job"}')
    command.downgrade(cfg, "0053")
    cols = {c["name"] for c in sa.inspect(engine).get_columns("valuz_queued_input")}
    assert not {"completed_at", "output_message_id", "result_summary"} & cols
    automation_cols = {c["name"] for c in sa.inspect(engine).get_columns("valuz_automation")}
    assert {"app_plugin_source_kind", "app_plugin_catalog_binding"} <= automation_cols
    assert "target_session_id" not in automation_cols
    with engine.connect() as connection:
        status = connection.execute(
            sa.text("SELECT status FROM valuz_queued_input WHERE id='input'")
        )
        assert status.scalar_one() == "queued"
        catalog = connection.execute(
            sa.text(
                "SELECT project_id,app_plugin_source_kind,app_plugin_catalog_binding "
                "FROM valuz_automation WHERE id='automation'"
            )
        ).one()
        assert catalog == ("project", "builtin", '{"source_id":"catalog-job"}')
    engine.dispose()

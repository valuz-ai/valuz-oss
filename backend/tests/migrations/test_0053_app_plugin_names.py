"""Canonical names preserve plugin data, owner isolation and reversible schema."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def test_app_plugin_names_upgrade_and_downgrade_preserve_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "host.db"
    migrations = Path(__file__).resolve().parents[2] / "alembic" / "host"
    config = Config(str(migrations / "alembic.ini"))
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, "0052")
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO valuz_extension_storage VALUES "
                "('u1','acme.app','key','42',2,123), "
                "('u2','acme.app','key','43',2,124)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO valuz_extension_config VALUES "
                "('u1','acme.app','{\"region\":\"cn\"}',123)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO valuz_automation "
                "(id,user_id,name,project_id,prompt_template,action_kind,worktree,"
                "trigger_kind,status,execution_kind,input_kind,result_kind,code_runtime,"
                "code_entry,extension_id,extension_name,created_at,updated_at) VALUES "
                "('a1','u1','run','p','','chat',0,'manual','enabled','code','none',"
                "'artifact','python','.valuz/extensions/acme.app/job.py','acme.app','App',0,0)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO valuz_notification "
                "(id,user_id,kind,dedup_key,title,body,action,urgency,payload,"
                "created_at,updated_at) "
                "VALUES ('n1','u1','extension','ext:acme.app:old','Ready','','none','info',"
                '\'{"plugin_id":"acme.app","link":"/x/acme.app"}\',0,0)'
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = sa.create_engine(f"sqlite:///{path}")
    inspector = sa.inspect(engine)
    assert "valuz_extension_storage" not in inspector.get_table_names()
    assert inspector.get_pk_constraint("valuz_app_plugin_storage")["constrained_columns"] == [
        "user_id",
        "app_plugin_id",
        "key",
    ]
    assert inspector.get_pk_constraint("valuz_app_plugin_config")["constrained_columns"] == [
        "user_id",
        "app_plugin_id",
    ]
    columns = {column["name"]: column for column in inspector.get_columns("valuz_automation")}
    assert "extension_id" not in columns and columns["app_plugin_id"]["nullable"]
    checks = {check["name"] for check in inspector.get_check_constraints("valuz_automation")}
    assert {"ck_automation_execution_kind", "ck_automation_trigger_kind"} <= checks
    with engine.connect() as connection:
        assert connection.execute(
            sa.text(
                "SELECT user_id,app_plugin_id,value_json "
                "FROM valuz_app_plugin_storage ORDER BY user_id"
            )
        ).all() == [("u1", "acme.app", "42"), ("u2", "acme.app", "43")]
        assert (
            connection.execute(sa.text("SELECT values_json FROM valuz_app_plugin_config")).scalar()
            == '{"region":"cn"}'
        )
        assert connection.execute(
            sa.text("SELECT app_plugin_id,app_plugin_name,code_entry FROM valuz_automation")
        ).one() == ("acme.app", "App", ".valuz/extensions/acme.app/job.py")
        notice = connection.execute(
            sa.text("SELECT kind,payload,dedup_key FROM valuz_notification")
        ).one()
        assert notice.kind == "app_plugin" and '"app_plugin_id"' in notice.payload
        assert notice.dedup_key == "ext:acme.app:old"
    with engine.begin() as connection:
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "INSERT INTO valuz_app_plugin_storage VALUES ('u1','acme.app','key','99',2,125)"
                )
            )
    engine.dispose()

    command.downgrade(config, "0052")
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        assert connection.execute(
            sa.text(
                "SELECT user_id,extension_id,value_json "
                "FROM valuz_extension_storage ORDER BY user_id"
            )
        ).all() == [("u1", "acme.app", "42"), ("u2", "acme.app", "43")]
        assert connection.execute(
            sa.text("SELECT extension_id,extension_name FROM valuz_automation")
        ).one() == ("acme.app", "App")
        notice = connection.execute(sa.text("SELECT kind,payload FROM valuz_notification")).one()
        assert notice.kind == "extension" and '"plugin_id"' in notice.payload
    engine.dispose()

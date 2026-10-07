"""0053 preserves pending inputs and rolls back only additive columns."""

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
    command.upgrade(cfg, "0052")
    engine = sa.create_engine(f"sqlite:///{db}")
    with engine.begin() as connection:
        connection.execute(
            sa.text("""
            INSERT INTO valuz_queued_input
            (id,user_id,created_at,updated_at,session_id,input,status,position)
            VALUES ('input','owner',1,1,'main','{"text":"pending"}','queued',0)
        """)
        )
    command.upgrade(cfg, "0053")
    cols = {c["name"] for c in sa.inspect(engine).get_columns("valuz_queued_input")}
    assert {"completed_at", "output_message_id", "result_summary"} <= cols
    with engine.connect() as connection:
        status = connection.execute(
            sa.text("SELECT status FROM valuz_queued_input WHERE id='input'")
        )
        assert status.scalar_one() == "queued"
    command.downgrade(cfg, "0052")
    cols = {c["name"] for c in sa.inspect(engine).get_columns("valuz_queued_input")}
    assert not {"completed_at", "output_message_id", "result_summary"} & cols
    with engine.connect() as connection:
        status = connection.execute(
            sa.text("SELECT status FROM valuz_queued_input WHERE id='input'")
        )
        assert status.scalar_one() == "queued"
    engine.dispose()

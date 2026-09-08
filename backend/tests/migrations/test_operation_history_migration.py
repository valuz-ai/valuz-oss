"""An additive fence cannot be downgraded into execution authority."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from valuz_agent.facade.durable_operations import OperationRecordStorage


def test_history_schema_and_lossless_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    root = Path(__file__).resolve().parents[2] / "alembic" / "host"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root))
    path = tmp_path / "host.db"
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(cfg, "0047")
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO valuz_operation_record (id, user_id, created_at, updated_at, "
                    "operation_type, operation_version, actor_kind, target_refs, input_payload, "
                    "preview, expected_revisions, risk_level, confirmation_policy, state, "
                    "proposal_hash, idempotency_key, canonical_result_refs, result_payload) "
                    "VALUES ('old', 'owner', 1, 2, 'test', 1, 'agent', '[]', '{}', '{}', '{}', "
                    "'material', 'confirm', 'awaiting_confirmation', :hash, 'old', '[]', '{}')"
                ),
                {"hash": "a" * 64},
            )
        command.upgrade(cfg, "0048")
        assert {c["name"] for c in sa.inspect(engine).get_columns("valuz_operation_record")} == set(
            OperationRecordStorage.__table__.columns.keys()
        )
        with engine.connect() as connection:
            row = connection.execute(sa.select(OperationRecordStorage.__table__)).mappings().one()
            assert not row["historical_only"] and row["updated_at"] == 2
        command.downgrade(cfg, "0047")
        command.upgrade(cfg, "0048")
        with engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE valuz_operation_record SET historical_only = TRUE WHERE id = 'old'")
            )
        with pytest.raises(RuntimeError, match="requires_no_historical_records"):
            command.downgrade(cfg, "0047")
        with engine.connect() as connection:
            assert connection.scalar(sa.select(OperationRecordStorage.historical_only))
            assert (
                connection.scalar(sa.text("SELECT version_num FROM alembic_version_host")) == "0048"
            )
    finally:
        engine.dispose()

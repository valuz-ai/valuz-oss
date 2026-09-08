"""Additive host schema, exact model columns and lossless rollback guard."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from valuz_agent.facade.durable_evidence import (
    EvidenceSnapshotStorage,
    PendingEvidenceSealStorage,
    ProvenanceRecordStorage,
)


def test_schema_upgrade_downgrade_and_data_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    root = Path(__file__).resolve().parents[2] / "alembic" / "host"
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root))
    path = tmp_path / "host.db"
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(cfg, "0046")
    command.upgrade(cfg, "0047")
    engine = sa.create_engine(f"sqlite:///{path}")
    try:
        for model in (EvidenceSnapshotStorage, PendingEvidenceSealStorage, ProvenanceRecordStorage):
            assert {c["name"] for c in sa.inspect(engine).get_columns(model.__tablename__)} == set(
                model.__table__.columns.keys()
            )
        command.downgrade(cfg, "0046")
        assert "valuz_evidence_snapshot" not in sa.inspect(engine).get_table_names()
        command.upgrade(cfg, "0047")
        with engine.begin() as connection:
            connection.execute(
                PendingEvidenceSealStorage.__table__.insert().values(
                    id="historic",
                    user_id="owner",
                    created_at=1,
                    updated_at=2,
                    operation_id="operation",
                    message_id="message",
                    citation_id="citation",
                    payload={},
                    citation_hash="original-hash",
                    validation_revision="v1",
                    status="consumed",
                    historical_only=True,
                )
            )
        with pytest.raises(RuntimeError, match="requires_empty_tables"):
            command.downgrade(cfg, "0046")
        with engine.connect() as connection:
            row = (
                connection.execute(sa.select(PendingEvidenceSealStorage.__table__)).mappings().one()
            )
            assert row["id"] == "historic" and row["citation_hash"] == "original-hash"
            assert (
                connection.scalar(sa.text("SELECT version_num FROM alembic_version_host")) == "0047"
            )
        assert "valuz_provenance_record" in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()

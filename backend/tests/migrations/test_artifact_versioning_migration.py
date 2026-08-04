"""Migration 0030 must describe exactly what the ORM models declare.

The artifact tables are created by hand-written DDL rather than autogenerate, so
nothing keeps the two in step on its own. A drifted column is not caught by the
datastore tests — those build their schema from ``Base.metadata`` and would pass
against a migration that never ran.

Runs the whole host chain against a temp SQLite file, so it also proves 0030
applies on top of 0029 rather than only in isolation.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from valuz_agent.modules.artifacts import models as m

_TABLES = (
    m.ArtifactRow,
    m.ArtifactKeyRow,
    m.ArtifactHeadRow,
    m.ArtifactRevisionRow,
    m.ArtifactContentRow,
)


@pytest.fixture(scope="module")
def migrated_db():  # type: ignore[no-untyped-def]
    db_path = os.path.join(tempfile.mkdtemp(), "host.db")
    previous = os.environ.get("DATABASE_URL")
    # ``alembic/host/env.py`` reads DATABASE_URL first and runs async, so this
    # has to be the aiosqlite driver, not bare sqlite.
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    try:
        cfg = Config("alembic/host/alembic.ini")
        cfg.set_main_option("script_location", "alembic/host")
        command.upgrade(cfg, "head")
        yield create_engine(f"sqlite:///{db_path}")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.mark.parametrize("model", _TABLES, ids=[t.__tablename__ for t in _TABLES])
def test_migration_columns_match_orm(migrated_db, model) -> None:  # type: ignore[no-untyped-def]
    inspector = inspect(migrated_db)
    assert model.__tablename__ in inspector.get_table_names()
    live = {c["name"] for c in inspector.get_columns(model.__tablename__)}
    declared = {c.name for c in model.__table__.columns}
    assert live == declared


def test_identity_and_idempotency_constraints_exist(migrated_db) -> None:  # type: ignore[no-untyped-def]
    """The two constraints the design leans on, present in the actual DDL.

    ``ux_artifact_key`` is what makes identity lookup a lookup; without it two
    deliveries could register the same key against different artifacts.
    ``ux_artifact_revision_content`` is the idempotency line that replaces the
    unavailable tool_call_id — a replayed delivery must not mint a version.
    """
    inspector = inspect(migrated_db)
    for table, want in (
        ("valuz_artifact_key", "ux_artifact_key"),
        ("valuz_artifact_revision", "ux_artifact_revision_content"),
    ):
        names = {u["name"] for u in inspector.get_unique_constraints(table)} | {
            i["name"] for i in inspector.get_indexes(table) if i.get("unique")
        }
        assert want in names


def test_legacy_artifact_table_is_untouched(migrated_db) -> None:  # type: ignore[no-untyped-def]
    """0030 adds tables and changes nothing else.

    The old table stays the source of truth until the cutover release, and the
    backfill only ever reads it — that is what makes rolling the cutover back a
    redeploy rather than a data restore.
    """
    inspector = inspect(migrated_db)
    assert "valuz_session_artifact" in inspector.get_table_names()
    legacy = {c["name"] for c in inspector.get_columns("valuz_session_artifact")}
    assert legacy == {
        "id",
        "user_id",
        "session_id",
        "file_path",
        "file_name",
        "file_size",
        "mime_type",
        "created_at",
        "updated_at",
    }

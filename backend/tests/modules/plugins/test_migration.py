"""Host migration 0036 must describe exactly what the plugin ORM models
declare (the datastore tests build their schema from ``Base.metadata`` and
would pass against a migration that never ran). Runs the whole host chain
against a temp SQLite file, so it also proves 0036 applies on top of 0035."""

from __future__ import annotations

import os
import tempfile

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from valuz_agent.modules.plugins import models as m

_TABLES = (m.PluginRow, m.PluginComponentRow)


@pytest.fixture(scope="module")
def migrated_db():  # type: ignore[no-untyped-def]
    db_path = os.path.join(tempfile.mkdtemp(), "host.db")
    previous = os.environ.get("DATABASE_URL")
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
    live = {c["name"]: c for c in inspector.get_columns(model.__tablename__)}
    declared = {c.name: c for c in model.__table__.columns}
    assert set(live) == set(declared)
    for name, column in declared.items():
        assert bool(live[name]["nullable"]) == bool(column.nullable), name


def _unique_names(inspector, table: str) -> set[str]:  # type: ignore[no-untyped-def]
    return {u["name"] for u in inspector.get_unique_constraints(table)} | {
        i["name"] for i in inspector.get_indexes(table) if i.get("unique")
    }


def test_identity_constraints_and_indexes(migrated_db) -> None:  # type: ignore[no-untyped-def]
    inspector = inspect(migrated_db)
    assert "uq_valuz_plugin_user_name" in _unique_names(inspector, "valuz_plugin")
    assert "uq_valuz_plugin_component_member" in _unique_names(inspector, "valuz_plugin_component")
    component_indexes = {i["name"] for i in inspector.get_indexes("valuz_plugin_component")}
    assert {"ix_valuz_plugin_component_user_id", "ix_valuz_plugin_component_plugin_id"} <= (
        component_indexes
    )
    assert "ix_valuz_plugin_user_id" in {i["name"] for i in inspector.get_indexes("valuz_plugin")}


def test_downgrade_removes_both_tables(migrated_db) -> None:  # type: ignore[no-untyped-def]
    cfg = Config("alembic/host/alembic.ini")
    cfg.set_main_option("script_location", "alembic/host")
    command.downgrade(cfg, "0035")
    try:
        names = set(inspect(migrated_db).get_table_names())
        assert "valuz_plugin" not in names and "valuz_plugin_component" not in names
    finally:
        command.upgrade(cfg, "head")

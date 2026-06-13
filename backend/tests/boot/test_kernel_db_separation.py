"""DB-separation probe — kernel storage in its own SQLite file.

The architecture-level acceptance check for the kernel seam: with
``VALUZ_KERNEL_DATABASE_URL`` pointing the kernel at a separate file,
both migration chains run, the kernel tables exist ONLY in the kernel
file, the ``valuz_*`` tables exist ONLY in the host file, and a full
session round-trip through ``kernel_client`` works. Any residual code
path where the host reaches kernel tables through its own engine
surfaces here as a missing-table error.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest


_REIMPORT_PREFIXES = (
    "valuz_agent.infra.config",
    "valuz_agent.boot.kernel",
    "valuz_agent.boot.schema",
    "valuz_agent.infra.database",
    "valuz_agent.infra.db",
)


@pytest.fixture
async def split_db(tmp_path, monkeypatch):
    """Fresh host + kernel SQLite files, both chains migrated, kernel up.

    The settings-bearing modules are re-imported so they pick up the
    probe's env vars, and the ORIGINAL module objects are restored on
    teardown — later tests monkeypatch module attributes (e.g.
    ``infra.db.AsyncSessionLocal``) and must target the same objects the
    already-imported call sites hold, not fresh re-imports.
    """
    monkeypatch.setenv("VALUZ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VALUZ_DB_FILENAME", "host-probe.db")
    kernel_db = tmp_path / "kernel-probe.db"
    host_db = tmp_path / "host-probe.db"
    monkeypatch.setenv("VALUZ_KERNEL_DATABASE_URL", f"sqlite:///{kernel_db}")

    saved_modules = {
        name: mod for name, mod in sys.modules.items() if name.startswith(_REIMPORT_PREFIXES)
    }
    saved_db_url = os.environ.get("DATABASE_URL")
    for name in saved_modules:
        sys.modules.pop(name, None)

    try:
        import valuz_agent.boot.kernel as kb  # noqa: F401 — sys.path side-effect

        kb.run_kernel_migrations()

        import valuz_agent.boot.schema as sb

        sb.run_host_migrations()

        from app.config import AppConfig  # type: ignore[import-not-found]
        from app.dependencies import (  # type: ignore[import-not-found]
            init_dependencies,
            shutdown_dependencies,
        )

        await init_dependencies(AppConfig())
        try:
            yield host_db, kernel_db
        finally:
            await shutdown_dependencies()
    finally:
        for name in [n for n in sys.modules if n.startswith(_REIMPORT_PREFIXES)]:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        if saved_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_db_url


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }


KERNEL_TABLES = {"sessions", "messages", "events"}


@pytest.mark.asyncio
async def test_kernel_tables_live_only_in_kernel_db(split_db) -> None:
    host_db, kernel_db = split_db

    host_tables = _tables(host_db)
    kernel_tables = _tables(kernel_db)

    assert KERNEL_TABLES <= kernel_tables
    assert "alembic_version" in kernel_tables

    # The host file carries ONLY host concerns: no kernel tables, no
    # kernel alembic stamp; the kernel file carries no valuz_* tables.
    assert not (KERNEL_TABLES & host_tables)
    assert any(t.startswith("valuz_") for t in host_tables)
    assert "alembic_version_host" in host_tables
    assert not any(t.startswith("valuz_") for t in kernel_tables)


@pytest.mark.asyncio
async def test_session_round_trip_via_seam_with_split_storage(split_db) -> None:
    host_db, kernel_db = split_db

    from app.schemas import (  # type: ignore[import-not-found]
        AgentConfigSchema,
        CreateSessionRequest,
        UpdateSessionRequest,
    )

    from valuz_agent.adapters import kernel_client

    session_id = str(uuid.uuid4())
    created = await kernel_client.create_session(
        CreateSessionRequest(
            id=session_id,
            agent_config=AgentConfigSchema(name="probe-agent"),
            cwd=str(kernel_db.parent),
            runtime_provider="claude_agent",
            metadata={"valuz": {"name": "probe"}},
        )
    )
    assert created.id == session_id

    loaded = await kernel_client.get_session(session_id)
    assert loaded is not None and loaded.metadata["valuz"]["name"] == "probe"

    listed = await kernel_client.list_sessions(ids=[session_id])
    assert [s.id for s in listed] == [session_id]

    updated = await kernel_client.update_session(
        session_id, UpdateSessionRequest(metadata={"valuz": {"name": "probe-renamed"}})
    )
    assert updated.metadata["valuz"]["name"] == "probe-renamed"

    assert await kernel_client.get_events(session_id, after_seq=0) == []
    assert await kernel_client.usage_rollup(0, 4_102_444_800_000) == []

    # The row physically lives in the kernel file, not the host file.
    with sqlite3.connect(kernel_db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()[0]
    assert count == 1
    assert "sessions" not in _tables(host_db)

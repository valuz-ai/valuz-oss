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
import uuid
from pathlib import Path

import pytest

from conftest import reimported_modules


_REIMPORT_PREFIXES = (
    "valuz_agent.infra.config",
    "valuz_agent.infra.db_urls",
    "valuz_agent.infra.fs_registry",
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
    teardown by ``reimported_modules`` — later tests monkeypatch module
    attributes (e.g. ``infra.db.AsyncSessionLocal``) and must target the same
    objects the already-imported call sites hold, not fresh re-imports.
    """
    monkeypatch.setenv("VALUZ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VALUZ_DB_FILENAME", "host-probe.db")
    kernel_db = tmp_path / "kernel-probe.db"
    monkeypatch.setenv("VALUZ_KERNEL_DATABASE_URL", f"sqlite:///{kernel_db}")
    # This probe exercises the SEPARATED-kernel, NO-durable seam (host stays
    # clean of kernel tables). Pin the durable OFF so ``_set_kernel_env``'s
    # default co-locate injection (durable = host valuz.db) doesn't apply here —
    # the co-locate path has its own coverage.
    monkeypatch.setenv("VALUZ_DURABLE_DATABASE_URL", "")

    saved_db_url = os.environ.get("DATABASE_URL")

    try:
        with reimported_modules(*_REIMPORT_PREFIXES):
            import valuz_agent.boot.kernel as kb  # noqa: F401 — sys.path side-effect

            kb.run_kernel_migrations()

            import valuz_agent.boot.schema as sb
            from valuz_agent.infra.db_urls import db_url, sqlite_path_from_url

            host_db = sqlite_path_from_url(db_url())
            assert host_db is not None

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


def test_kernel_env_keeps_deepagents_checkpoints_in_a_sibling_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new runtime must not reconfigure WAL on the live kernel database."""
    import valuz_agent.boot.kernel as kb

    kernel_db = tmp_path / "kernel.db"
    monkeypatch.setattr(kb, "kernel_db_url_async", lambda: f"sqlite+aiosqlite:///{kernel_db}")
    monkeypatch.setattr(kb, "kernel_db_url", lambda: f"sqlite:///{kernel_db}")
    monkeypatch.delenv("DEEPAGENTS_CHECKPOINT_DB", raising=False)
    monkeypatch.delenv("DEEPAGENTS_CHECKPOINT_ROOT", raising=False)
    monkeypatch.setenv("KERNEL_STORE", "remote")

    kb._set_kernel_env()

    checkpoint_db = Path(os.environ["DEEPAGENTS_CHECKPOINT_DB"])
    assert checkpoint_db == tmp_path / "deepagents_checkpoints.db"
    assert checkpoint_db != kernel_db
    assert Path(os.environ["DEEPAGENTS_CHECKPOINT_ROOT"]) == tmp_path / "deepagents-checkpoints"


def test_kernel_env_preserves_an_explicit_checkpoint_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import valuz_agent.boot.kernel as kb

    kernel_db = tmp_path / "kernel.db"
    external_checkpoint_db = tmp_path / "operator-checkpoints.db"
    monkeypatch.setattr(kb, "kernel_db_url_async", lambda: f"sqlite+aiosqlite:///{kernel_db}")
    monkeypatch.setattr(kb, "kernel_db_url", lambda: f"sqlite:///{kernel_db}")
    monkeypatch.setenv("DEEPAGENTS_CHECKPOINT_DB", str(external_checkpoint_db))
    monkeypatch.setenv("KERNEL_STORE", "remote")

    kb._set_kernel_env()

    assert Path(os.environ["DEEPAGENTS_CHECKPOINT_DB"]) == external_checkpoint_db


def test_unreadable_legacy_kernel_db_is_quarantined_when_durable_is_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import valuz_agent.boot.kernel as kb
    from valuz_agent.infra.config import settings

    kernel_db = tmp_path / "kernel.db"
    durable_db = tmp_path / "valuz.db"
    kernel_db.write_bytes(b"legacy-checkpoint-corruption")
    with sqlite3.connect(durable_db) as conn:
        conn.execute("CREATE TABLE valuz_projects (id TEXT PRIMARY KEY)")
    monkeypatch.setattr(kb, "kernel_db_url", lambda: f"sqlite:///{kernel_db}")
    monkeypatch.setattr(kb, "db_url", lambda: f"sqlite:///{durable_db}")
    monkeypatch.setattr(settings, "kernel_database_url", None)

    recovery = kb._prepare_default_kernel_db()

    assert recovery is not None
    assert recovery.read_bytes() == b"legacy-checkpoint-corruption"
    assert not kernel_db.exists()
    assert durable_db.read_bytes().startswith(b"SQLite format 3\x00")


def test_explicit_unreadable_kernel_db_is_never_quarantined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import valuz_agent.boot.kernel as kb
    from valuz_agent.infra.config import settings

    kernel_db = tmp_path / "kernel.db"
    durable_db = tmp_path / "valuz.db"
    kernel_db.write_bytes(b"operator-managed")
    with sqlite3.connect(durable_db) as conn:
        conn.execute("CREATE TABLE valuz_projects (id TEXT PRIMARY KEY)")
    monkeypatch.setattr(kb, "kernel_db_url", lambda: f"sqlite:///{kernel_db}")
    monkeypatch.setattr(kb, "db_url", lambda: f"sqlite:///{durable_db}")
    monkeypatch.setattr(
        settings,
        "kernel_database_url",
        f"sqlite:///{kernel_db}",
    )

    assert kb._prepare_default_kernel_db() is None
    assert kernel_db.read_bytes() == b"operator-managed"


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

    owner = "local-test-owner"
    session_id = str(uuid.uuid4())
    created = await kernel_client.create_session(
        owner,
        CreateSessionRequest(
            id=session_id,
            agent_config=AgentConfigSchema(name="probe-agent"),
            cwd=str(kernel_db.parent),
            runtime_provider="claude_agent",
            metadata={"valuz": {"name": "probe"}},
        ),
    )
    assert created.id == session_id

    loaded = await kernel_client.get_session(owner, session_id)
    assert loaded is not None and loaded.metadata["valuz"]["name"] == "probe"

    listed = await kernel_client.list_sessions(owner, ids=[session_id])
    assert [s.id for s in listed] == [session_id]

    updated = await kernel_client.update_session(
        owner, session_id, UpdateSessionRequest(metadata={"valuz": {"name": "probe-renamed"}})
    )
    assert updated.metadata["valuz"]["name"] == "probe-renamed"

    assert await kernel_client.get_events(owner, session_id, after_seq=0) == []
    assert await kernel_client.usage_rollup(owner, 0, 4_102_444_800_000) == []

    # The row physically lives in the kernel file, not the host file.
    with sqlite3.connect(kernel_db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()[0]
    assert count == 1
    assert "sessions" not in _tables(host_db)

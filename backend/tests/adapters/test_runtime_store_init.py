"""``init_dependencies`` store composition — ONE composition, every tier.

Drives the real dependency wiring with distinct SQLite files standing in for
the backends (a PG DSN / an HTTP DataService URL are the only differences in
prod). Asserts the uniform contract: whenever a mirror backend resolves, the
kernel store is ``RuntimeStore`` (runtime sqlite authority + inline dual-write)
— ``KERNEL_STORE`` selects the mirror backend, never a different composition.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.*/app.* (sys.path)
from __future__ import annotations

import uuid

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*/app.*

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import dependencies as deps
from app.config import AppConfig
from src.adapters.runtime_store import RuntimeStore
from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core.agent_config import AgentConfig
from src.core.types import Session


async def _create_schema(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


def _session(sid: str, cwd: str) -> Session:
    return Session(
        id=sid,
        user_id="u",
        agent_config=AgentConfig(id="a", name="a", model="claude-sonnet-4-6"),
        cwd=cwd,
    )


async def _assert_dual_written(durable_url: str, sid: str) -> None:
    engine = create_async_engine(durable_url)
    try:
        durable = SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False))
        assert await durable.load_session("u", sid) is not None
    finally:
        await engine.dispose()


@pytest.mark.parametrize("kernel_store", ["local", "pg"])
@pytest.mark.asyncio
async def test_init_with_mirror_backend_is_runtime_store(tmp_path, kernel_store):
    """``local`` and ``pg`` compose IDENTICALLY — only the mirror DSN differs."""
    local_url = f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"
    durable_url = f"sqlite+aiosqlite:///{tmp_path / 'mirror.db'}"
    await _create_schema(local_url)

    config = AppConfig(
        database_url=local_url,
        kernel_store=kernel_store,
        durable_database_url=durable_url,
    )
    await deps.init_dependencies(config)
    try:
        store = deps.get_store()
        assert isinstance(store, RuntimeStore)

        sid = uuid.uuid4().hex
        await store.save_session(_session(sid, str(tmp_path)))

        # Runtime sqlite holds it AND the mirror already received the
        # dual-write (inline, no barrier; mirror schema auto-created).
        assert await store.load_session("u", sid) is not None
        await _assert_dual_written(durable_url, sid)
    finally:
        await deps.shutdown_dependencies()

    # Shutdown disposed the mirror engine and cleared the global.
    assert deps._durable_engine is None


@pytest.mark.asyncio
async def test_init_without_mirror_is_plain_local(tmp_path, monkeypatch):
    monkeypatch.delenv("VALUZ_DURABLE_DATABASE_URL", raising=False)
    local_url = f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"
    await _create_schema(local_url)

    config = AppConfig(database_url=local_url, kernel_store="local")
    await deps.init_dependencies(config)
    try:
        assert not isinstance(deps.get_store(), RuntimeStore)
    finally:
        await deps.shutdown_dependencies()


@pytest.mark.asyncio
async def test_init_collapsed_dsn_is_single_write(tmp_path):
    """durable DSN == runtime DSN → one file already; the dual-write collapses."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'one.db'}"
    await _create_schema(url)

    config = AppConfig(database_url=url, kernel_store="local", durable_database_url=url)
    await deps.init_dependencies(config)
    try:
        assert not isinstance(deps.get_store(), RuntimeStore)
    finally:
        await deps.shutdown_dependencies()


@pytest.mark.asyncio
async def test_init_pg_requires_durable_dsn(tmp_path, monkeypatch):
    monkeypatch.delenv("VALUZ_DURABLE_DATABASE_URL", raising=False)
    local_url = f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"
    await _create_schema(local_url)

    config = AppConfig(database_url=local_url, kernel_store="pg")
    with pytest.raises(RuntimeError, match="VALUZ_DURABLE_DATABASE_URL"):
        await deps.init_dependencies(config)
    await deps.shutdown_dependencies()

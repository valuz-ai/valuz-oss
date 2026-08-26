"""Async engine factory — creates dialect-appropriate AsyncEngine from URL."""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str, **kwargs: Any) -> AsyncEngine:
    """Create an async engine with dialect-specific defaults.

    Supported URL prefixes:
    - sqlite+aiosqlite:///path/to/db.sqlite (or :// for in-memory)
    - postgresql+asyncpg://user:pass@host/db
    - mysql+aiomysql://user:pass@host/db
    """
    defaults: dict[str, Any] = {}

    if url.startswith("sqlite"):
        defaults["connect_args"] = {"check_same_thread": False}
    else:
        defaults.setdefault("pool_size", 5)
        defaults.setdefault("max_overflow", 10)
        # Server deployments sit behind idle-timeout middleboxes (LB / k8s
        # conntrack) and PG restarts: a pooled connection can die while idle
        # and the next checkout raises "connection is closed" mid-request.
        # pre_ping validates on checkout; recycle retires connections before
        # typical idle-timeout windows.
        defaults.setdefault("pool_pre_ping", True)
        defaults.setdefault("pool_recycle", 1800)

    defaults.update(kwargs)
    engine = create_async_engine(url, **defaults)

    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn: Any, _connection_record: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            # The store is the highest-frequency writer during a turn (one write
            # per persisted event). SQLite's default busy_timeout=0 raises
            # "database is locked" the instant another engine holds the write
            # lock on a shared file (the durable engine shares valuz.db with the
            # host engine, which waits 15s) — so every engine built here must
            # wait, not fail, under contention.
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the engine."""
    return async_sessionmaker(engine, expire_on_commit=False)

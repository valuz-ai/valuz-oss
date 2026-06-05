"""PostgreSQL integration tests (Slice 3).

These tests require a running PostgreSQL instance. Skip automatically when
DATABASE_URL is not set or the connection fails.

Usage:
    docker compose -f docker-compose.test.yml up -d postgres
    DATABASE_URL=postgresql://valuz:valuz@localhost:5432/valuz_test \
        uv run python -m pytest tests/test_database_pg.py -v
"""

from __future__ import annotations

import os

import pytest

PG_URL = os.environ.get("DATABASE_URL", "")
skip_no_pg = pytest.mark.skipif(
    not PG_URL.startswith("postgresql"),
    reason="DATABASE_URL not set to a PostgreSQL URL",
)


@skip_no_pg
class TestPostgresConfig:
    def test_settings_recognises_pg_url(self) -> None:
        from valuz_agent.infra.config import Settings

        s = Settings(database_url=PG_URL)
        assert s.is_sqlite is False
        assert "asyncpg" in s.db_url_async

    def test_sync_engine_connects(self) -> None:
        from sqlalchemy import create_engine, text

        engine = create_engine(PG_URL, echo=False)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
        engine.dispose()

    def test_async_engine_connects(self) -> None:
        import asyncio

        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text

        from valuz_agent.infra.config import Settings

        s = Settings(database_url=PG_URL)

        async def _check() -> int:
            engine = create_async_engine(s.db_url_async, echo=False)
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                val = result.scalar()
            await engine.dispose()
            return val  # type: ignore[return-value]

        assert asyncio.run(_check()) == 1

    def test_create_all_succeeds(self) -> None:
        from sqlalchemy import create_engine

        from valuz_agent.infra.database import Base

        engine = create_engine(PG_URL, echo=False)
        Base.metadata.create_all(engine)
        Base.metadata.drop_all(engine)
        engine.dispose()

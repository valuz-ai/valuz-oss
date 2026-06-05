"""Tests for database abstraction (Slice 3) and config extensions."""

from __future__ import annotations

from valuz_agent.infra.config import Settings


class TestDatabaseUrlConfig:
    def test_default_is_sqlite(self) -> None:
        s = Settings(data_dir="/tmp/valuz-test-db")
        assert s.is_sqlite is True
        assert s.db_url.startswith("sqlite:///")
        assert s.db_url_async.startswith("sqlite+aiosqlite:///")

    def test_explicit_pg_url(self) -> None:
        s = Settings(
            data_dir="/tmp/valuz-test-db",
            database_url="postgresql://valuz:valuz@localhost:5432/valuz",
        )
        assert s.is_sqlite is False
        assert s.db_url == "postgresql://valuz:valuz@localhost:5432/valuz"
        assert s.db_url_async == "postgresql+asyncpg://valuz:valuz@localhost:5432/valuz"

    def test_explicit_sqlite_url(self) -> None:
        s = Settings(
            data_dir="/tmp/valuz-test-db",
            database_url="sqlite:///custom.db",
        )
        assert s.is_sqlite is True
        assert s.db_url == "sqlite:///custom.db"
        assert s.db_url_async == "sqlite+aiosqlite:///custom.db"

    def test_to_async_url_passthrough(self) -> None:
        assert Settings._to_async_url("mysql://x") == "mysql://x"

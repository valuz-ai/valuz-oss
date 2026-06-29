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

    def test_skill_local_index_defaults_on(self) -> None:
        assert Settings(data_dir="/tmp/valuz-test-db").skill_local_index_enabled is True
        assert (
            Settings(
                data_dir="/tmp/valuz-test-db",
                database_url="postgresql://valuz:valuz@localhost:5432/valuz",
            ).skill_local_index_enabled
            is True
        )

    def test_skill_local_index_can_be_disabled(self) -> None:
        assert (
            Settings(
                data_dir="/tmp/valuz-test-db",
                database_url="postgresql://valuz:valuz@localhost:5432/valuz",
                skill_local_index_enabled=False,
            ).skill_local_index_enabled
            is False
        )


class TestKernelDbUrlConfig:
    def test_default_kernel_db_is_separate_file(self) -> None:
        """No override: the kernel gets its OWN kernel.db, distinct from valuz.db."""
        s = Settings(data_dir="/tmp/valuz-test-db")
        assert s.kernel_db_url == "sqlite:////tmp/valuz-test-db/kernel.db"
        assert s.kernel_db_url_async == "sqlite+aiosqlite:////tmp/valuz-test-db/kernel.db"
        assert s.kernel_db_url != s.db_url  # the split

    def test_explicit_database_url_colocates_kernel(self) -> None:
        """An explicit host DB (e.g. Postgres) shares the store with the kernel."""
        s = Settings(
            data_dir="/tmp/valuz-test-db",
            database_url="postgresql://valuz:valuz@localhost:5432/valuz",
        )
        assert s.kernel_db_url == s.db_url
        assert s.kernel_db_url_async == s.db_url_async

    def test_explicit_kernel_database_url_wins(self) -> None:
        s = Settings(
            data_dir="/tmp/valuz-test-db",
            database_url="postgresql://valuz:valuz@localhost:5432/valuz",
            kernel_database_url="sqlite:///kernel-only.db",
        )
        assert s.kernel_db_url == "sqlite:///kernel-only.db"
        assert s.kernel_db_url_async == "sqlite+aiosqlite:///kernel-only.db"

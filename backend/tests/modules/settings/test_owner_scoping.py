"""Owner-scoping regression for ``SettingsDatastore`` reads + write-stamp.

The table keeps its original ``key`` primary key; the multi-user change is only
that ``user_id`` is stamped explicitly on write and reads filter by it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.settings.datastore import SettingsDatastore
from valuz_agent.modules.settings.models import AppSettingRow


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "settings.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[AppSettingRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


async def _set(sm, owner: str, key: str, value: str) -> None:
    async with sm() as db:
        await SettingsDatastore(db).upsert_setting(
            owner, AppSettingRow(key=key, value_json=value, updated_at=0)
        )


class TestSettingsOwnerScoping:
    async def test_create_stamps_owner(self, sessionmaker_) -> None:
        await _set(sessionmaker_, "user-A", "a.key", "v")
        async with sessionmaker_() as db:
            row = await SettingsDatastore(db).get_setting("user-A", "a.key")
            assert row is not None and row.user_id == "user-A"

    async def test_get_absent_for_other_owner(self, sessionmaker_) -> None:
        await _set(sessionmaker_, "user-A", "a.key", "v")
        async with sessionmaker_() as db:
            ds = SettingsDatastore(db)
            assert await ds.get_setting("user-A", "a.key") is not None
            assert await ds.get_setting("user-B", "a.key") is None

    async def test_list_returns_only_callers_rows(self, sessionmaker_) -> None:
        await _set(sessionmaker_, "user-A", "a.key", "v")
        await _set(sessionmaker_, "user-B", "b.key", "v")
        async with sessionmaker_() as db:
            ds = SettingsDatastore(db)
            assert {r.key for r in await ds.list_settings("user-A")} == {"a.key"}
            assert {r.key for r in await ds.list_settings("user-B")} == {"b.key"}

"""Owner-scoping + composite-PK regression for ``SettingsDatastore``.

The load-bearing property: the composite PK ``(key, user_id)`` lets two users
hold the *same* key without one's upsert clobbering the other's.
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
            owner,
            AppSettingRow(user_id="local-test-owner", key=key, value_json=value, updated_at=0),
        )


class TestSettingsOwnerScoping:
    async def test_same_key_does_not_clobber_across_owners(self, sessionmaker_) -> None:
        await _set(sessionmaker_, "user-A", "model.default", "A-value")
        await _set(sessionmaker_, "user-B", "model.default", "B-value")
        async with sessionmaker_() as db:
            ds = SettingsDatastore(db)
            a = await ds.get_setting("user-A", "model.default")
            b = await ds.get_setting("user-B", "model.default")
            assert a is not None and a.value_json == "A-value"
            assert b is not None and b.value_json == "B-value"

    async def test_get_absent_for_other_owner(self, sessionmaker_) -> None:
        await _set(sessionmaker_, "user-A", "k", "v")
        async with sessionmaker_() as db:
            ds = SettingsDatastore(db)
            assert await ds.get_setting("user-A", "k") is not None
            assert await ds.get_setting("user-B", "k") is None

    async def test_list_returns_only_callers_rows(self, sessionmaker_) -> None:
        await _set(sessionmaker_, "user-A", "k", "v")
        await _set(sessionmaker_, "user-B", "k", "v")
        async with sessionmaker_() as db:
            ds = SettingsDatastore(db)
            assert {r.user_id for r in await ds.list_settings("user-A")} == {"user-A"}
            assert {r.user_id for r in await ds.list_settings("user-B")} == {"user-B"}

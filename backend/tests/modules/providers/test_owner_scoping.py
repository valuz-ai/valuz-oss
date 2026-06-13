"""Owner-scoping regression for ``ProviderDatastore`` reads + write-stamp.

Single-instance multi-user safety: a provider owned by user A must read as
absent for user B, ``list`` returns only the caller's rows, and a cross-owner
delete is a no-op. ``create`` stamps the explicitly-passed ``user_id`` (no
ContextVar default).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.models import ProviderRow


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "prov.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[ProviderRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


async def _create(sm, owner: str, name: str) -> str:
    async with sm() as db:
        row = ProviderRow(
            name=name,
            provider_kind="compatible",
            source="user",
            credential_source="none",
            enabled=True,
        )
        await ProviderDatastore(db).create(owner, row)
        return row.id


class TestProviderOwnerScoping:
    async def test_create_stamps_passed_owner(self, sessionmaker_) -> None:
        pid = await _create(sessionmaker_, "user-A", "A1")
        async with sessionmaker_() as db:
            row = await ProviderDatastore(db).get_by_id("user-A", pid)
            assert row is not None and row.user_id == "user-A"

    async def test_get_by_id_absent_for_other_owner(self, sessionmaker_) -> None:
        pid = await _create(sessionmaker_, "user-A", "A1")
        async with sessionmaker_() as db:
            ds = ProviderDatastore(db)
            assert await ds.get_by_id("user-A", pid) is not None
            assert await ds.get_by_id("user-B", pid) is None

    async def test_list_returns_only_callers_rows(self, sessionmaker_) -> None:
        await _create(sessionmaker_, "user-A", "A1")
        await _create(sessionmaker_, "user-B", "B1")
        async with sessionmaker_() as db:
            ds = ProviderDatastore(db)
            assert {r.name for r in await ds.list_providers("user-A")} == {"A1"}
            assert {r.name for r in await ds.list_providers("user-B")} == {"B1"}

    async def test_delete_across_owners_is_a_noop(self, sessionmaker_) -> None:
        pid = await _create(sessionmaker_, "user-A", "A1")
        async with sessionmaker_() as db:
            await ProviderDatastore(db).delete("user-B", pid)
        async with sessionmaker_() as db:
            assert await ProviderDatastore(db).get_by_id("user-A", pid) is not None
        async with sessionmaker_() as db:
            await ProviderDatastore(db).delete("user-A", pid)
        async with sessionmaker_() as db:
            assert await ProviderDatastore(db).get_by_id("user-A", pid) is None

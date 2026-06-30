"""Owner-scoping regression for ``ConnectorDatastore`` reads + write-stamp."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
)


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "conn.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[
            ConnectorRow.__table__,
            ConnectorAttrRow.__table__,
            ConnectorOAuthRow.__table__,
        ],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


async def _create(sm, owner: str, slug: str) -> str:
    async with sm() as db:
        row = ConnectorRow(
            user_id="local-test-owner",
            slug=slug,
            display_name=slug,
            connector_type="custom",
            enabled=True,
        )
        await ConnectorDatastore(db).create(owner, row)
        return row.id


class TestConnectorOwnerScoping:
    async def test_create_stamps_passed_owner(self, sessionmaker_) -> None:
        cid = await _create(sessionmaker_, "user-A", "a1")
        async with sessionmaker_() as db:
            row = await ConnectorDatastore(db).get_by_id("user-A", cid)
            assert row is not None and row.user_id == "user-A"

    async def test_reads_absent_for_other_owner(self, sessionmaker_) -> None:
        cid = await _create(sessionmaker_, "user-A", "a1")
        async with sessionmaker_() as db:
            ds = ConnectorDatastore(db)
            assert await ds.get_by_id("user-A", cid) is not None
            assert await ds.get_by_id("user-B", cid) is None
            assert await ds.get_by_slug("user-B", "a1") is None

    async def test_list_returns_only_callers_rows(self, sessionmaker_) -> None:
        await _create(sessionmaker_, "user-A", "a1")
        await _create(sessionmaker_, "user-B", "b1")
        async with sessionmaker_() as db:
            ds = ConnectorDatastore(db)
            assert {r.slug for r in await ds.list_all("user-A")} == {"a1"}
            assert {r.slug for r in await ds.list_enabled("user-B")} == {"b1"}

    async def test_same_slug_is_allowed_for_different_owners(self, sessionmaker_) -> None:
        await _create(sessionmaker_, "user-A", "firecrawl")
        await _create(sessionmaker_, "user-B", "firecrawl")

        async with sessionmaker_() as db:
            ds = ConnectorDatastore(db)
            user_a_row = await ds.get_by_slug("user-A", "firecrawl")
            user_b_row = await ds.get_by_slug("user-B", "firecrawl")
            assert user_a_row is not None and user_a_row.user_id == "user-A"
            assert user_b_row is not None and user_b_row.user_id == "user-B"

    async def test_same_slug_is_rejected_for_same_owner(self, sessionmaker_) -> None:
        await _create(sessionmaker_, "user-A", "firecrawl")
        with pytest.raises(IntegrityError):
            await _create(sessionmaker_, "user-A", "firecrawl")

    async def test_delete_across_owners_is_a_noop(self, sessionmaker_) -> None:
        cid = await _create(sessionmaker_, "user-A", "a1")
        async with sessionmaker_() as db:
            assert await ConnectorDatastore(db).delete("user-B", cid) is False
        async with sessionmaker_() as db:
            assert await ConnectorDatastore(db).get_by_id("user-A", cid) is not None
        async with sessionmaker_() as db:
            assert await ConnectorDatastore(db).delete("user-A", cid) is True

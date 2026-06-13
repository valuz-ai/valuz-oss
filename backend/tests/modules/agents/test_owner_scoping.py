"""Owner-scoping regression for Agent + ProjectMember datastores."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.datastore import AgentDatastore, ProjectMemberDatastore
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "agents.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[AgentRow.__table__, ProjectMemberRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


class TestAgentOwnerScoping:
    async def test_agent_reads_scoped_by_owner(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            await AgentDatastore(db).create(
                "user-A", AgentRow(slug="a1", name="A1", source="custom")
            )
            await AgentDatastore(db).create(
                "user-B", AgentRow(slug="b1", name="B1", source="custom")
            )
        async with sessionmaker_() as db:
            ds = AgentDatastore(db)
            assert await ds.get_agent("user-A", "a1") is not None
            assert await ds.get_agent("user-B", "a1") is None
            assert {r.slug for r in await ds.list_agents("user-A")} == {"a1"}

    async def test_agent_delete_is_owner_scoped(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            await AgentDatastore(db).create(
                "user-A", AgentRow(slug="a1", name="A1", source="custom")
            )
        async with sessionmaker_() as db:
            assert await AgentDatastore(db).delete("user-B", "a1") is False
        async with sessionmaker_() as db:
            assert await AgentDatastore(db).get_agent("user-A", "a1") is not None

    async def test_member_reads_scoped_by_owner(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            await ProjectMemberDatastore(db).create(
                "user-A", ProjectMemberRow(project_id="p1", agent_slug="m1", source_agent_slug="a1")
            )
        async with sessionmaker_() as db:
            ds = ProjectMemberDatastore(db)
            assert await ds.get("user-A", "p1", "m1") is not None
            assert await ds.get("user-B", "p1", "m1") is None
            assert {r.agent_slug for r in await ds.list_by_project("user-A", "p1")} == {"m1"}
            assert await ds.list_by_project("user-B", "p1") == []

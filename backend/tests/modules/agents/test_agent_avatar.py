"""Agent ``avatar`` field round-trips through create / get / update (08-agents-module v2).

First test for the agents service — a focused check that the new nullable
``avatar`` column persists and clears via the service surface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.models import AgentRow
from valuz_agent.modules.agents.service import AgentService


@pytest.fixture
async def svc(tmp_path) -> AsyncIterator[AgentService]:
    db_file = tmp_path / "agents.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[AgentRow.__table__])
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield AgentService(session)  # type: ignore[arg-type]  # async session (pre-existing typing)
    finally:
        await session.close()
        await engine.dispose()


async def test_should_persist_avatar_when_creating_agent(svc: AgentService) -> None:
    row = await svc.create_agent("local-test-owner", {"name": "Analyst", "avatar": "icon:analyst"})
    assert row.avatar == "icon:analyst"
    fetched = await svc.get_agent("local-test-owner", row.slug)
    assert fetched.avatar == "icon:analyst"


async def test_should_default_avatar_to_none_when_omitted(svc: AgentService) -> None:
    row = await svc.create_agent("local-test-owner", {"name": "No Avatar"})
    assert row.avatar is None


async def test_should_update_avatar_when_patched(svc: AgentService) -> None:
    row = await svc.create_agent("local-test-owner", {"name": "Modeler", "avatar": "icon:old"})
    updated = await svc.update_agent("local-test-owner", row.slug, {"avatar": "icon:new"})
    assert updated.avatar == "icon:new"


async def test_should_clear_avatar_when_patched_with_empty(svc: AgentService) -> None:
    row = await svc.create_agent("local-test-owner", {"name": "Tracker", "avatar": "icon:set"})
    updated = await svc.update_agent("local-test-owner", row.slug, {"avatar": ""})
    assert updated.avatar is None

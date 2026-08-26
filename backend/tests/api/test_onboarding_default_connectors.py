"""Onboarding installs Valurion without copying or installing resources."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.api.routes.onboarding import _ensure_valurion
from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.models import AgentRow
from valuz_agent.modules.connectors.models import ConnectorRow

USER = "local-test-owner"


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'valurion.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[AgentRow.__table__, ConnectorRow.__table__],
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def test_onboarding_ensures_one_canonical_valurion_without_resource_copies(db) -> None:
    assert await _ensure_valurion(USER, db) == "valurion"
    assert await _ensure_valurion(USER, db) == "valurion"

    agents = list((await db.execute(select(AgentRow))).scalars())
    connectors = list((await db.execute(select(ConnectorRow))).scalars())

    assert len(agents) == 1
    assert agents[0].slug == "valurion"
    assert agents[0].kind == "system"
    assert agents[0].resource_policy == "all_available"
    assert agents[0].skills == []
    assert agents[0].connector_types == []
    assert agents[0].knowledge_scope == []
    assert connectors == []

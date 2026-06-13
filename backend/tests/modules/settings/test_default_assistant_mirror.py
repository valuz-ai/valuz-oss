"""09-assistant Phase Z2: the 默认助手 base agent's brain mirrors the global
model default (Settings = source of truth). Editing the model-defaults tuple
re-syncs the default-assistant's runtime/model/provider/effort.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.api.routes.settings import (
    ModelDefaultsResponse,
    _mirror_to_default_assistant,
)
from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.datastore import AgentDatastore
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.agents.seed import DEFAULT_ASSISTANT_SLUG


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mirror.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[AgentRow.__table__, ProjectMemberRow.__table__],
        )
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def test_should_mirror_model_defaults_onto_default_assistant(db) -> None:
    await AgentDatastore(db).create(
        "local-test-owner",
        AgentRow(
            user_id="local-test-owner",
            slug=DEFAULT_ASSISTANT_SLUG,
            name="默认助手",
            source="official",
            deletable=False,
            runtime="claude_agent",
            model="claude-sonnet-4-6",
        ),
    )

    await _mirror_to_default_assistant(
        "local-test-owner",
        db,
        ModelDefaultsResponse(
            default_runtime="codex",
            default_provider_id="prov-x",
            default_model="gpt-5",
            default_effort="max",
        ),
    )

    agent = await AgentDatastore(db).get_agent("local-test-owner", DEFAULT_ASSISTANT_SLUG)
    assert agent is not None
    assert agent.runtime == "codex"
    assert agent.model == "gpt-5"
    assert agent.provider_id == "prov-x"
    assert agent.effort == "max"


async def test_should_noop_when_default_assistant_not_seeded(db) -> None:
    # Fresh DB before the boot seeder runs — mirror must not raise.
    await _mirror_to_default_assistant(
        "local-test-owner",
        db,
        ModelDefaultsResponse(
            default_runtime="claude_agent",
            default_provider_id=None,
            default_model="claude-sonnet-4-6",
            default_effort="high",
        ),
    )
    assert await AgentDatastore(db).get_agent("local-test-owner", DEFAULT_ASSISTANT_SLUG) is None

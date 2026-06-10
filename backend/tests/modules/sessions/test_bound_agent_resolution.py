"""09-assistant Phase A–D: a session's ``agent_slug`` resolves to a kernel
``AgentConfig`` id from EITHER a project member OR a global library agent.

Temp / quick-chat conversations bind the seeded ``default-assistant`` — a
global library agent that is NOT a member of any project. Before this fix
``_create_agent_bound_session`` looked the slug up only in
``ProjectMemberDatastore`` and 400'd ("agent '…' not found in this project")
for every temp send. The resolver below adds the library-agent fallback.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect
from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.datastore import AgentDatastore, ProjectMemberDatastore
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.agents.seed import DEFAULT_ASSISTANT_SLUG
from valuz_agent.modules.sessions import service as session_service
from valuz_agent.modules.sessions.errors import SessionNotRunnable
from valuz_agent.modules.sessions.service import SessionService


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bound.db'}")
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


@pytest.fixture
def patch_uow(db, monkeypatch):
    """Make the resolver's ``async_unit_of_work`` yield the test session.

    The resolver opens two units of work (member lookup, then the committed
    library-agent path); both share the one fixture session here.
    """

    @asynccontextmanager
    async def _fake_uow(*, commit: bool = True):
        yield db
        if commit:
            await db.commit()

    monkeypatch.setattr(session_service, "async_unit_of_work", _fake_uow)


async def _resolve(project_id: str, agent_slug: str) -> str:
    # The resolver reads no instance state — a bare stand-in for ``self`` is fine.
    return await SessionService._resolve_bound_kernel_agent_id(
        SimpleNamespace(), project_id, agent_slug
    )


async def test_should_resolve_global_library_agent_when_not_a_project_member(db, patch_uow) -> None:
    await AgentDatastore(db).create(
        AgentRow(
            slug=DEFAULT_ASSISTANT_SLUG,
            name="默认助手",
            source="official",
            deletable=False,
            runtime="claude_agent",
            model="claude-sonnet-4-6",
            kernel_agent_id="ker-default-assistant",
        )
    )

    # chat-default project has no members → falls back to the library agent.
    resolved = await _resolve("chat-default", DEFAULT_ASSISTANT_SLUG)

    assert resolved == "ker-default-assistant"


async def test_should_prefer_project_member_over_library_agent(db, patch_uow) -> None:
    # Same slug exists both as a library agent AND as a project member; the
    # project-scoped member wins for project conversations.
    await AgentDatastore(db).create(
        AgentRow(
            slug="architect",
            name="架构师",
            source="custom",
            runtime="claude_agent",
            model="claude-sonnet-4-6",
            kernel_agent_id="ker-library",
        )
    )
    await ProjectMemberDatastore(db).create(
        ProjectMemberRow(
            project_id="ws-proj",
            agent_slug="architect",
            kernel_agent_id="ker-member",
        )
    )

    resolved = await _resolve("ws-proj", "architect")

    assert resolved == "ker-member"


async def test_should_raise_when_slug_is_neither_member_nor_library_agent(db, patch_uow) -> None:
    with pytest.raises(SessionNotRunnable):
        await _resolve("chat-default", "ghost-agent")

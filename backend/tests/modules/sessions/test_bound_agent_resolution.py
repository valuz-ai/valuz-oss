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
from valuz_agent.modules.connectors.models import ConnectorRow
from valuz_agent.modules.sessions import service as session_service
from valuz_agent.modules.sessions.errors import SessionNotRunnable
from valuz_agent.modules.sessions.service import SessionService


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bound.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[AgentRow.__table__, ProjectMemberRow.__table__, ConnectorRow.__table__],
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
    kernel_agent_id, _config = await SessionService._resolve_bound_agent(
        SimpleNamespace(), project_id, agent_slug
    )
    return kernel_agent_id


async def test_should_resolve_global_library_agent_when_not_a_project_member(db, patch_uow) -> None:
    await AgentDatastore(db).create("local-test-owner", 
        AgentRow(
            slug=DEFAULT_ASSISTANT_SLUG,
            name="默认助手",
            source="official",
            deletable=False,
            runtime="claude_agent",
            model="claude-sonnet-4-6",
        )
    )

    # chat-default project has no members → falls back to the library agent.
    resolved = await _resolve("chat-default", DEFAULT_ASSISTANT_SLUG)

    assert resolved == "agent:default-assistant"


async def test_should_prefer_project_member_over_library_agent(db, patch_uow) -> None:
    # Same slug exists both as a library agent AND as a project member; the
    # project-scoped member wins for project conversations.
    await AgentDatastore(db).create("local-test-owner", 
        AgentRow(
            slug="architect",
            name="架构师",
            source="custom",
            runtime="claude_agent",
            model="claude-sonnet-4-6",
        )
    )
    await ProjectMemberDatastore(db).create("local-test-owner", 
        ProjectMemberRow(
            project_id="ws-proj",
            agent_slug="architect",
            source_agent_slug="architect",
        )
    )

    resolved = await _resolve("ws-proj", "architect")

    assert resolved == "agent:architect"


async def test_should_raise_when_slug_is_neither_member_nor_library_agent(db, patch_uow) -> None:
    with pytest.raises(SessionNotRunnable):
        await _resolve("chat-default", "ghost-agent")


async def test_member_resolution_builds_snapshot_from_library_row(db, patch_uow) -> None:
    """Live-reference semantics: the member's config snapshot is built from
    the CURRENT library row fields, keyed to the member's kernel id."""
    await AgentDatastore(db).create("local-test-owner", 
        AgentRow(
            slug="researcher",
            name="研究员",
            source="custom",
            runtime="claude_agent",
            model="claude-opus-4-8",
            instructions="dig deep",
        )
    )
    await ProjectMemberDatastore(db).create("local-test-owner", 
        ProjectMemberRow(
            project_id="ws-x",
            agent_slug="researcher",
            source_agent_slug="researcher",
        )
    )

    kernel_agent_id, config = await SessionService._resolve_bound_agent(
        SimpleNamespace(), "ws-x", "researcher"
    )

    assert kernel_agent_id == "agent:researcher"
    assert config.id == "agent:researcher"
    assert config.name == "研究员"
    assert config.model == "claude-opus-4-8"
    assert config.instructions == "dig deep"
    # Tool surfaces ride the session's ``harness`` MCP entry now — the
    # snapshot carries no tool declarations.
    assert tuple(config.tools or ()) == ()


async def test_resolution_carries_connector_types_into_mcp_servers(db, patch_uow) -> None:
    """Regression: the session-resolution path builds ``AgentService`` ad hoc
    (no DI container), which used to leave it without a ConnectorService — so
    the agent's ``connector_types`` were silently dropped and the session's
    ``mcp_servers`` came out empty. The default-factory wiring must resolve
    them into live MCP server configs."""
    await db.merge(
        ConnectorRow(
            slug="my-search",
            display_name="My Search",
            connector_type="custom",
            transport="http",
            url="https://mcp.example.com/mcp",
            auth_type="none",
            enabled=True,
        )
    )
    await db.commit()
    await AgentDatastore(db).create("local-test-owner", 
        AgentRow(
            slug="analyst",
            name="分析师",
            source="custom",
            runtime="claude_agent",
            model="claude-sonnet-4-6",
            connector_types=["my-search"],
        )
    )

    _kernel_agent_id, config = await SessionService._resolve_bound_agent(
        SimpleNamespace(), "chat-default", "analyst"
    )

    assert [m.name for m in config.mcp_servers] == ["my-search"]
    assert config.mcp_servers[0].url == "https://mcp.example.com/mcp"
    # The binding declaration also rides metadata for downstream adapters.
    assert config.metadata["connector_bindings"] == [{"type": "my-search"}]

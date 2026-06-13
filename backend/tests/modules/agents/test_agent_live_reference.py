"""v2 live-reference invariants that don't need the kernel store (08/03 §5.5).

Covers the data-layer guarantees: the delete guard blocks deleting a still-
deployed agent, 解除派驻 leaves the agent row intact, and a member resolves
back to its library AgentRow via ``source_agent_slug``. The config snapshot
build (deploy + session-creation propagation) is exercised elsewhere.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.datastore import AgentDatastore, ProjectMemberDatastore
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.agents.service import (
    AgentNotDeletableError,
    AgentService,
    AgentStillDeployedError,
)


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    db_file = tmp_path / "agents_ref.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[AgentRow.__table__, ProjectMemberRow.__table__],
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


async def _deploy_row(db, *, slug: str, project_id: str, handle: str) -> None:
    """Insert a library AgentRow + a project member referencing it via
    provenance — the post-派驻 state, without the full deploy path."""
    agents = AgentDatastore(db)
    members = ProjectMemberDatastore(db)
    await agents.create(
        "local-test-owner",
        AgentRow(user_id="local-test-owner", slug=slug, name=slug.upper(), source="custom"),
    )
    await members.create(
        "local-test-owner",
        ProjectMemberRow(
            user_id="local-test-owner",
            project_id=project_id,
            agent_slug=handle,
            source_agent_slug=slug,
        ),
    )


async def test_should_block_delete_when_agent_still_deployed(db) -> None:
    await _deploy_row(db, slug="analyst", project_id="w1", handle="analyst")
    svc = AgentService(db)  # type: ignore[arg-type]

    with pytest.raises(AgentStillDeployedError) as exc:
        await svc.delete_agent("local-test-owner", "analyst")
    assert exc.value.deployment_count == 1
    # The agent row survives the blocked delete.
    assert await AgentDatastore(db).get_agent("local-test-owner", "analyst") is not None


async def test_should_allow_delete_after_undeploy(db) -> None:
    await _deploy_row(db, slug="modeler", project_id="w1", handle="modeler")
    svc = AgentService(db)  # type: ignore[arg-type]

    # 解除派驻 deletes ONLY the member row — agent row stays.
    await svc.delete_member("local-test-owner", "w1", "modeler")
    assert await ProjectMemberDatastore(db).get("local-test-owner", "w1", "modeler") is None
    assert await AgentDatastore(db).get_agent("local-test-owner", "modeler") is not None

    # Now the delete guard is clear.
    await svc.delete_agent("local-test-owner", "modeler")
    assert await AgentDatastore(db).get_agent("local-test-owner", "modeler") is None


async def test_should_block_delete_when_agent_not_deletable(db) -> None:
    # The 默认助手 base agent is seeded with deletable=False; delete must be
    # rejected and the row must survive.
    await AgentDatastore(db).create(
        "local-test-owner",
        AgentRow(
            user_id="local-test-owner",
            slug="default-assistant",
            name="默认助手",
            source="official",
            deletable=False,
        ),
    )
    svc = AgentService(db)  # type: ignore[arg-type]

    with pytest.raises(AgentNotDeletableError):
        await svc.delete_agent("local-test-owner", "default-assistant")
    assert await AgentDatastore(db).get_agent("local-test-owner", "default-assistant") is not None


async def test_should_resolve_member_back_to_library_agent(db) -> None:
    await _deploy_row(db, slug="tracker", project_id="w1", handle="tracker-1")
    member = await ProjectMemberDatastore(db).get("local-test-owner", "w1", "tracker-1")
    assert member is not None and member.source_agent_slug == "tracker"
    row = await AgentDatastore(db).get_agent("local-test-owner", member.source_agent_slug)
    assert row is not None and row.slug == "tracker"


async def test_should_list_all_deployments_of_a_shared_agent(db) -> None:
    # Same library agent派驻'd into two projects.
    await _deploy_row(db, slug="pm", project_id="w1", handle="pm")
    await ProjectMemberDatastore(db).create(
        "local-test-owner",
        ProjectMemberRow(
            user_id="local-test-owner", project_id="w2", agent_slug="pm", source_agent_slug="pm"
        ),
    )
    deployments = await ProjectMemberDatastore(db).list_by_source_agent_slug(
        "local-test-owner", "pm"
    )
    assert {m.project_id for m in deployments} == {"w1", "w2"}


async def test_list_deployments_service_resolves_projects(db) -> None:
    await _deploy_row(db, slug="reviewer", project_id="w1", handle="reviewer")
    await ProjectMemberDatastore(db).create(
        "local-test-owner",
        ProjectMemberRow(
            user_id="local-test-owner",
            project_id="w2",
            agent_slug="reviewer",
            source_agent_slug="reviewer",
        ),
    )
    svc = AgentService(db)  # type: ignore[arg-type]
    deployments = await svc.list_deployments("local-test-owner", "reviewer")
    assert {d["project_id"] for d in deployments} == {"w1", "w2"}


async def test_list_deployments_empty_for_never_deployed_agent(db) -> None:
    await AgentDatastore(db).create(
        "local-test-owner",
        AgentRow(user_id="local-test-owner", slug="solo", name="Solo", source="custom"),
    )
    svc = AgentService(db)  # type: ignore[arg-type]
    assert await svc.list_deployments("local-test-owner", "solo") == []

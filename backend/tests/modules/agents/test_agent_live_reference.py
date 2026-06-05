"""v2 live-reference invariants that don't need the kernel store (08/03 §5.5).

Covers the data-layer guarantees: the delete guard blocks deleting a still-
deployed agent, 解除派驻 leaves the agent row intact, and a member resolves
back to its library AgentRow via the shared ``kernel_agent_id``. The kernel-
config build/cascade (deploy + edit propagation) is exercised by the boot
integration smoke, not here.
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


async def _deploy_row(db, *, slug: str, kid: str, workspace_id: str, handle: str) -> None:
    """Insert a library AgentRow + a project member referencing its shared id —
    the post-派驻 state, without going through the kernel-touching deploy path."""
    agents = AgentDatastore(db)
    members = ProjectMemberDatastore(db)
    await agents.create(
        AgentRow(slug=slug, name=slug.upper(), source="custom", kernel_agent_id=kid)
    )
    await members.create(
        ProjectMemberRow(workspace_id=workspace_id, agent_slug=handle, kernel_agent_id=kid)
    )


async def test_should_block_delete_when_agent_still_deployed(db) -> None:
    await _deploy_row(db, slug="analyst", kid="k-1", workspace_id="w1", handle="analyst")
    svc = AgentService(db)  # type: ignore[arg-type]

    with pytest.raises(AgentStillDeployedError) as exc:
        await svc.delete_agent("analyst")
    assert exc.value.deployment_count == 1
    # The agent row survives the blocked delete.
    assert await AgentDatastore(db).get_agent("analyst") is not None


async def test_should_allow_delete_after_undeploy(db) -> None:
    await _deploy_row(db, slug="modeler", kid="k-2", workspace_id="w1", handle="modeler")
    svc = AgentService(db)  # type: ignore[arg-type]

    # 解除派驻 deletes ONLY the member row — agent row stays.
    await svc.delete_member("w1", "modeler")
    assert await ProjectMemberDatastore(db).get("w1", "modeler") is None
    assert await AgentDatastore(db).get_agent("modeler") is not None

    # Now the delete guard is clear.
    await svc.delete_agent("modeler")
    assert await AgentDatastore(db).get_agent("modeler") is None


async def test_should_block_delete_when_agent_not_deletable(db) -> None:
    # The 默认助手 base agent is seeded with deletable=False; delete must be
    # rejected and the row must survive.
    await AgentDatastore(db).create(
        AgentRow(slug="default-assistant", name="默认助手", source="official", deletable=False)
    )
    svc = AgentService(db)  # type: ignore[arg-type]

    with pytest.raises(AgentNotDeletableError):
        await svc.delete_agent("default-assistant")
    assert await AgentDatastore(db).get_agent("default-assistant") is not None


async def test_should_resolve_member_back_to_library_agent(db) -> None:
    await _deploy_row(db, slug="tracker", kid="k-3", workspace_id="w1", handle="tracker-1")
    row = await AgentDatastore(db).get_by_kernel_agent_id("k-3")
    assert row is not None and row.slug == "tracker"


async def test_should_list_all_deployments_of_a_shared_agent(db) -> None:
    # Same shared kernel id派驻'd into two workspaces.
    await _deploy_row(db, slug="pm", kid="k-4", workspace_id="w1", handle="pm")
    await ProjectMemberDatastore(db).create(
        ProjectMemberRow(workspace_id="w2", agent_slug="pm", kernel_agent_id="k-4")
    )
    deployments = await ProjectMemberDatastore(db).list_by_kernel_agent("k-4")
    assert {m.workspace_id for m in deployments} == {"w1", "w2"}


async def test_list_deployments_service_resolves_workspaces(db) -> None:
    await _deploy_row(db, slug="reviewer", kid="k-5", workspace_id="w1", handle="reviewer")
    await ProjectMemberDatastore(db).create(
        ProjectMemberRow(workspace_id="w2", agent_slug="reviewer", kernel_agent_id="k-5")
    )
    svc = AgentService(db)  # type: ignore[arg-type]
    deployments = await svc.list_deployments("reviewer")
    assert {d["workspace_id"] for d in deployments} == {"w1", "w2"}


async def test_list_deployments_empty_for_never_deployed_agent(db) -> None:
    await AgentDatastore(db).create(AgentRow(slug="solo", name="Solo", source="custom"))
    svc = AgentService(db)  # type: ignore[arg-type]
    assert await svc.list_deployments("solo") == []

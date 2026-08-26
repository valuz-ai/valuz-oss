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
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.modules.agents.datastore import AgentDatastore, ProjectMemberDatastore
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.agents.service import (
    AgentNotDeletableError,
    AgentService,
    AgentStillDeployedError,
)
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.projects.service import ProjectService
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow


@pytest.fixture
async def db(tmp_path, monkeypatch) -> AsyncIterator:
    db_file = tmp_path / "agents_ref.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                AgentRow.__table__,
                ProjectMemberRow.__table__,
                ProjectRow.__table__,
                # Project deletion cascades into the task tables.
                TaskRow.__table__,
                TaskSessionRow.__table__,
                TaskEventRow.__table__,
            ],
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    # Bind the global unit of work to this engine too: project deletion
    # cascades into the task tables through ``tasks.purge``, which opens its
    # own ``async_unit_of_work`` (every datastore method self-commits, so
    # there is no single transaction to keep it inside of).
    import valuz_agent.infra.db as db_mod

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
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
    await _ensure_project(db, project_id)
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


async def _ensure_project(db, project_id: str) -> None:
    existing = await ProjectDatastore(db).get_by_id("local-test-owner", project_id)
    if existing is not None:
        return
    await ProjectDatastore(db).create(
        "local-test-owner",
        ProjectRow(
            id=project_id,
            user_id="local-test-owner",
            name=project_id.upper(),
            kind="project",
            root_path=f"/tmp/{project_id}",
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


async def test_cascade_delete_removes_all_deployments_then_agent(db) -> None:
    # One library agent deployed into two projects (shared live reference).
    agents = AgentDatastore(db)
    members = ProjectMemberDatastore(db)
    await agents.create(
        "local-test-owner",
        AgentRow(user_id="local-test-owner", slug="scout", name="SCOUT", source="custom"),
    )
    for pid, handle in (("w1", "scout"), ("w2", "scout-2")):
        await _ensure_project(db, pid)
        await members.create(
            "local-test-owner",
            ProjectMemberRow(
                user_id="local-test-owner",
                project_id=pid,
                agent_slug=handle,
                source_agent_slug="scout",
            ),
        )
    svc = AgentService(db)  # type: ignore[arg-type]

    # Default (no cascade) still blocks — the guard is intact for safe callers.
    with pytest.raises(AgentStillDeployedError) as exc:
        await svc.delete_agent("local-test-owner", "scout")
    assert exc.value.deployment_count == 2

    # cascade=True 解除 both 派驻, then deletes the agent — one confirmed action.
    await svc.delete_agent("local-test-owner", "scout", cascade=True)
    assert await AgentDatastore(db).get_agent("local-test-owner", "scout") is None
    assert await ProjectMemberDatastore(db).get("local-test-owner", "w1", "scout") is None
    assert await ProjectMemberDatastore(db).get("local-test-owner", "w2", "scout-2") is None


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
    await _ensure_project(db, "w2")
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
    await _ensure_project(db, "w2")
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


async def test_should_ignore_orphan_deployment_rows_for_deleted_projects(db) -> None:
    await _deploy_row(db, slug="archivist", project_id="live", handle="archivist")
    await ProjectMemberDatastore(db).create(
        "local-test-owner",
        ProjectMemberRow(
            user_id="local-test-owner",
            project_id="deleted-project",
            agent_slug="archivist",
            source_agent_slug="archivist",
        ),
    )

    svc = AgentService(db)  # type: ignore[arg-type]
    deployments = await svc.list_deployments("local-test-owner", "archivist")
    assert deployments == [{"project_id": "live", "agent_slug": "archivist"}]

    with pytest.raises(AgentStillDeployedError) as exc:
        await svc.delete_agent("local-test-owner", "archivist")
    assert exc.value.deployment_count == 1


async def test_project_delete_removes_member_rows_but_keeps_library_agent(db) -> None:
    await _deploy_row(db, slug="planner", project_id="doomed", handle="planner")
    svc = ProjectService(
        datastore=ProjectDatastore(db),
        event_bus=EventBus(),
        member_datastore=ProjectMemberDatastore(db),
    )

    await svc.delete_project("local-test-owner", "doomed")

    assert await ProjectDatastore(db).get_by_id("local-test-owner", "doomed") is None
    assert await ProjectMemberDatastore(db).get("local-test-owner", "doomed", "planner") is None
    assert await AgentDatastore(db).get_agent("local-test-owner", "planner") is not None


async def test_list_deployments_empty_for_never_deployed_agent(db) -> None:
    await AgentDatastore(db).create(
        "local-test-owner",
        AgentRow(user_id="local-test-owner", slug="solo", name="Solo", source="custom"),
    )
    svc = AgentService(db)  # type: ignore[arg-type]
    assert await svc.list_deployments("local-test-owner", "solo") == []

"""Export-side tests for ``ProjectPackService`` — drives the service with
in-memory DBs over a fresh sqlite, never the real keychain. Import-side
round-trip is covered by ``test_projects_export_import.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.infra.eventbus import event_bus
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow
from valuz_agent.modules.automations.service import AutomationService
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
    ProjectConnectorRow,
)
from valuz_agent.modules.connectors.service import ConnectorService
from valuz_agent.modules.packs_common import extract_archive
from valuz_agent.modules.project_packs.errors import (
    ProjectNotExportable,
    ProjectPackNotFound,
)
from valuz_agent.modules.project_packs.service import ProjectPackService
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.projects.service import ProjectService
from valuz_agent.modules.skills.models import ProjectSkillConfigRow, SkillIndexRow

USER = "user-1"


async def _bootstrap(tables, workdir: Path):  # type: ignore[no-untyped-def]
    """Create a fresh sqlite db with the requested tables."""
    workdir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{workdir / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    return session, engine


_ALL_TABLES = [
    ProjectRow.__table__,
    AgentRow.__table__,
    ProjectMemberRow.__table__,
    AutomationRow.__table__,
    AutomationRunRow.__table__,
    ConnectorRow.__table__,
    ConnectorAttrRow.__table__,
    ConnectorOAuthRow.__table__,
    ProjectConnectorRow.__table__,
    SkillIndexRow.__table__,
    ProjectSkillConfigRow.__table__,
]


@pytest.fixture
async def env(tmp_path, monkeypatch) -> AsyncIterator[tuple]:
    from valuz_agent.infra import fs_registry as fsr
    from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id

    # Pin the data dir so memory/project writes land under tmp.
    monkeypatch.setenv("VALUZ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(fsr.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(fsr.settings, "user_skills_dir", tmp_path / "user-skills")
    session, engine = await _bootstrap(_ALL_TABLES, tmp_path / "db")
    connector_svc = ConnectorService(ConnectorDatastore(session))
    agent_svc = AgentService(session, connector_service=connector_svc)
    agent_pack_svc = AgentPackService(agent_svc)
    project_svc = ProjectService(
        datastore=ProjectDatastore(session),
        event_bus=event_bus,
    )
    automation_svc = AutomationService(
        db=session,
        event_bus=event_bus,
        project_service=project_svc,
        agent_service=agent_svc,
    )
    svc = ProjectPackService(
        project_service=project_svc,
        agent_service=agent_svc,
        agent_pack_service=agent_pack_svc,
        automation_service=automation_svc,
    )
    # AutomationService.list_automations_in_project reads the owner from
    # the ambient auth context — set it for the test, reset on teardown.
    token = set_current_user_id(USER)
    try:
        yield svc, session, engine
    finally:
        reset_current_user_id(token)
        await session.close()
        await engine.dispose()


async def _seed_project(env, name="Test Project") -> ProjectRow:
    svc, session, _ = env
    from uuid import uuid4

    row = ProjectRow(
        id=uuid4().hex,
        name=name,
        kind="project",
        root_path="/tmp/some-bound-dir",
        sort_order=10,
    )
    await ProjectDatastore(session).create(USER, row)
    return row


async def _seed_agent(env, slug="lead-agent") -> AgentRow:
    """Unused placeholder retained for clarity — agents are seeded inline
    in each test so the connector / skill wiring stays co-located."""
    raise NotImplementedError


async def test_export_unknown_project_raises(env) -> None:
    svc = env[0]
    with pytest.raises(ProjectPackNotFound):
        await svc.export_project(USER, "missing-id")


async def test_export_chat_project_raises(env) -> None:
    svc, session, _ = env
    from uuid import uuid4

    chat = ProjectRow(
        id=uuid4().hex,
        name="Chat",
        kind="chat",
        sort_order=0,
    )
    await ProjectDatastore(session).create(USER, chat)
    with pytest.raises(ProjectNotExportable):
        await svc.export_project(USER, chat.id)


async def test_export_round_trips_members_and_automations(env) -> None:
    svc, session, _ = env
    # Seed a project, a library agent, a member linking them, and one automation.
    project = await _seed_project(env)
    agent = AgentRow(
        slug="lead-1",
        name="Lead",
        description="Lead agent",
        instructions="do lead stuff",
        runtime="claude_agent",
        model="claude-sonnet-4-6",
        skills=[],
        connector_types=[],
        provider_id="prov-1",
        source="custom",
    )
    agent.user_id = USER
    session.add(agent)
    await session.commit()
    member = ProjectMemberRow(
        project_id=project.id,
        agent_slug="lead",
        source_agent_slug="lead-1",
    )
    member.user_id = USER
    session.add(member)
    await session.commit()

    automation = AutomationRow(
        id="auto-1",
        name="Daily brief",
        agent_kind="project_member",
        agent_slug="lead",
        project_id=project.id,
        prompt_template="Prompt body text",
        action_kind="chat",
        trigger_kind="cron",
        cron_expr="0 9 * * *",
        timezone="UTC",
        status="enabled",
    )
    automation.user_id = USER
    session.add(automation)
    await session.commit()

    data = await svc.export_project(USER, project.id)
    assert isinstance(data, bytes) and len(data) > 0

    parsed, root = extract_archive(data)
    assert parsed.project is not None
    assert parsed.project.name == "Test Project"
    assert len(parsed.project.members) == 1
    m = parsed.project.members[0]
    assert m.agent_slug == "lead"
    assert m.source_agent_slug == "lead-1"
    # The agent definition is hoisted into the top-level ``agents[]`` payload,
    # referenced by the member's source slug.
    agent = next(a for a in parsed.agents if a.slug == "lead-1")
    # provider_id is dropped (PackAgent has no provider_id field)
    assert not hasattr(agent, "provider_id") or getattr(agent, "provider_id", None) is None
    # model is demoted to model_hint
    assert agent.model_hint == "claude-sonnet-4-6"
    assert len(parsed.project.automations) == 1
    assert parsed.project.automations[0].name == "Daily brief"
    assert parsed.project.automations[0].trigger_kind == "cron"
    assert parsed.project.automations[0].cron_expr == "0 9 * * *"
    # prompt_template must travel in the archive — the list shape omits it,
    # so the export fetches detail per automation. An empty prompt here would
    # make every automation fail AutomationPromptEmpty on import.
    assert parsed.project.automations[0].prompt_template == "Prompt body text"


async def test_export_strips_connector_secrets(env) -> None:
    svc, session, _ = env
    from valuz_agent.modules.connectors.service import CredEntry

    project = await _seed_project(env)
    # Create a custom connector carrying a header secret, then bind it to
    # the agent and to the project.
    connector_svc = svc._agents._connectors
    view = await connector_svc.create_connector(
        USER,
        slug="my-mcp",
        display_name="My MCP",
        transport="http",
        url="https://example.com/mcp",
        auth_type="bearer",
        headers=[CredEntry(key="Authorization", value="Bearer SECRET", secret=True)],
    )
    assert view.slug == "my-mcp"
    agent = AgentRow(
        slug="c-agent",
        name="Connector Agent",
        runtime="claude_agent",
        model="claude-sonnet-4-6",
        skills=[],
        connector_types=["my-mcp"],
        source="custom",
    )
    agent.user_id = USER
    session.add(agent)
    await session.commit()
    member = ProjectMemberRow(
        project_id=project.id,
        agent_slug="c-1",
        source_agent_slug="c-agent",
    )
    member.user_id = USER
    session.add(member)
    pc = ProjectConnectorRow(project_id=project.id, slug="my-mcp")
    pc.user_id = USER
    session.add(pc)
    await session.commit()

    data = await svc.export_project(USER, project.id)
    parsed, _ = extract_archive(data)
    assert len(parsed.connectors) == 1
    c = parsed.connectors[0]
    assert c.slug == "my-mcp"
    # Secret-bearing fields are NOT carried — only url / command / args /
    # auth_type / transport.
    assert c.url == "https://example.com/mcp"
    assert c.auth_type == "bearer"
    assert c.requires_credentials is True
    assert "headers_json" not in c.model_dump()
    assert "params_json" not in c.model_dump()
    assert "env_json" not in c.model_dump()


async def test_export_rejects_code_automation_without_losing_it(env) -> None:
    svc, session, _ = env
    project = await _seed_project(env)
    automation = AutomationRow(
        id="code-run",
        user_id=USER,
        name="Code task",
        project_id=project.id,
        execution_kind="code",
        code_runtime="python",
        code_entry="job.py",
        agent_kind=None,
        agent_slug=None,
        prompt_template="",
        action_kind="chat",
        trigger_kind="manual",
        status="enabled",
        result_kind="artifact",
    )
    session.add(automation)
    await session.commit()
    with pytest.raises(ProjectNotExportable, match="Code automations"):
        await svc.export_project(USER, project.id)
    await session.refresh(automation)
    assert automation.execution_kind == "code" and automation.status == "enabled"

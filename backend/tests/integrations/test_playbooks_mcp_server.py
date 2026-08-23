"""Agent Playbook tool proposes mutations and runs pinned content."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from valuz_agent.facade.projects import ProjectRef
from valuz_agent.infra.database import Base
from valuz_agent.integrations import playbooks_mcp_server as mcp
from valuz_agent.modules.operations.service import OperationService
from valuz_agent.modules.playbooks.models import PlaybookDefinitionRow
from valuz_agent.modules.playbooks.service import PlaybookService

USER = "owner-1"


class Projects:
    async def get(self, user_id: str, project_id: str) -> ProjectRef | None:
        if user_id == USER and project_id == "p1":
            return ProjectRef(id="p1", name="Research", kind="project")
        return None


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield session
    await engine.dispose()


async def test_agent_create_requires_operation_confirmation_then_can_run(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = Projects()

    @asynccontextmanager
    async def unit_of_work(*args, **kwargs):  # type: ignore[no-untyped-def]
        yield db

    async def playbook_service(_db):  # type: ignore[no-untyped-def]
        return PlaybookService(db, projects)

    async def operation_service(_db):  # type: ignore[no-untyped-def]
        return OperationService(db, projects)  # type: ignore[arg-type]

    async def session_project(session_id: str, user_id: str) -> tuple[str, str]:
        assert session_id == "session-1"
        assert user_id == USER
        return "p1", "project"

    from valuz_agent.infra import db as db_module

    monkeypatch.setattr(db_module, "async_unit_of_work", unit_of_work)
    monkeypatch.setattr(mcp, "get_current_mcp_session_id", lambda: "session-1")
    monkeypatch.setattr(mcp, "get_current_mcp_user_id", lambda: USER)
    monkeypatch.setattr(mcp, "_session_project", session_project)
    monkeypatch.setattr(mcp, "_service", playbook_service)
    monkeypatch.setattr(mcp, "_operation_service", operation_service)

    proposed = json.loads(
        await mcp.playbook_invoke(
            action="create",
            name="Earnings review",
            content="Review the latest earnings and update the research context.",
            agent_slug="assistant-1",
        )
    )
    assert proposed["ok"] is True
    assert proposed["operation"]["state"] == "awaiting_confirmation"
    assert list((await db.scalars(select(PlaybookDefinitionRow))).all()) == []

    operation = await OperationService(db, projects).confirm(  # type: ignore[arg-type]
        USER,
        proposed["operation"]["id"],
        expected_proposal_hash=proposed["operation"]["proposal_hash"],
    )
    assert operation.state == "succeeded"
    definition_id = operation.result_payload["definition_id"]

    started = json.loads(await mcp.playbook_invoke(action="run", definition_id=definition_id))
    assert started["ok"] is True
    assert started["definition_version"] == 1
    assert "Review the latest earnings" in started["content"]

    finished = json.loads(
        await mcp.playbook_invoke(
            action="finish",
            run_id=started["run_id"],
            status="completed",
        )
    )
    assert finished["status"] == "completed"

    metadata_proposal = json.loads(
        await mcp.playbook_invoke(
            action="update_definition",
            definition_id=definition_id,
            expected_revision=1,
            name="Earnings review v2",
        )
    )
    assert metadata_proposal["operation"]["preview"]["change"] == "metadata"
    metadata_operation = await OperationService(db, projects).confirm(  # type: ignore[arg-type]
        USER,
        metadata_proposal["operation"]["id"],
        expected_proposal_hash=metadata_proposal["operation"]["proposal_hash"],
    )
    assert metadata_operation.result_payload["name"] == "Earnings review v2"

    version_proposal = json.loads(
        await mcp.playbook_invoke(
            action="update",
            definition_id=definition_id,
            base_version=1,
            content="Review earnings and compare the result with prior guidance.",
        )
    )
    version_operation = await OperationService(db, projects).confirm(  # type: ignore[arg-type]
        USER,
        version_proposal["operation"]["id"],
        expected_proposal_hash=version_proposal["operation"]["proposal_hash"],
    )
    assert version_operation.result_payload["definition_version"] == 2

    history = json.loads(
        await mcp.playbook_invoke(
            action="list_versions",
            definition_id=definition_id,
        )
    )
    assert [item["version"] for item in history["versions"]] == [2, 1]
    old_version = json.loads(
        await mcp.playbook_invoke(
            action="get",
            definition_id=definition_id,
            version=1,
        )
    )
    assert "latest earnings" in old_version["version"]["content"]
    current = json.loads(await mcp.playbook_invoke(action="get", definition_id=definition_id))
    assert current["current_version"]["version"] == 2
    assert current["current_version"]["default_executor"] == {"agent_slug": "assistant-1"}

    status_proposal = json.loads(
        await mcp.playbook_invoke(
            action="set_status",
            definition_id=definition_id,
            expected_revision=3,
            status="active",
        )
    )
    assert status_proposal["operation"]["preview"]["status"] == "active"
    status_operation = await OperationService(db, projects).confirm(  # type: ignore[arg-type]
        USER,
        status_proposal["operation"]["id"],
        expected_proposal_hash=status_proposal["operation"]["proposal_hash"],
    )
    assert status_operation.result_payload["status"] == "active"

    delete_proposal = json.loads(
        await mcp.playbook_invoke(
            action="delete",
            definition_id=definition_id,
            expected_revision=4,
        )
    )
    assert delete_proposal["operation"]["risk_level"] == "destructive"
    delete_operation = await OperationService(db, projects).confirm(  # type: ignore[arg-type]
        USER,
        delete_proposal["operation"]["id"],
        expected_proposal_hash=delete_proposal["operation"]["proposal_hash"],
    )
    assert delete_operation.state == "succeeded"
    assert delete_operation.result_payload["deleted_definition_id"] == definition_id
    assert (
        await db.scalar(
            select(PlaybookDefinitionRow).where(PlaybookDefinitionRow.id == definition_id)
        )
        is None
    )


async def test_agent_cannot_finish_a_run_from_another_session(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = Projects()

    @asynccontextmanager
    async def unit_of_work(*args, **kwargs):  # type: ignore[no-untyped-def]
        yield db

    async def playbook_service(_db):  # type: ignore[no-untyped-def]
        return PlaybookService(db, projects)

    async def session_project(_session_id: str, _user_id: str) -> tuple[str, str]:
        return "p1", "project"

    from valuz_agent.infra import db as db_module
    from valuz_agent.modules.playbooks.schemas import (
        PlaybookCreateRequest,
        PlaybookRunCreateRequest,
    )

    definition, _ = await PlaybookService(db, projects).create_definition(
        USER,
        PlaybookCreateRequest(name="Review", content="Review earnings", project_id="p1"),
    )
    run = await PlaybookService(db, projects).create_run(
        USER,
        PlaybookRunCreateRequest(
            definition_id=definition.id,
            project_id="p1",
            session_id="session-1",
        ),
    )

    monkeypatch.setattr(db_module, "async_unit_of_work", unit_of_work)
    monkeypatch.setattr(mcp, "get_current_mcp_session_id", lambda: "session-2")
    monkeypatch.setattr(mcp, "get_current_mcp_user_id", lambda: USER)
    monkeypatch.setattr(mcp, "_session_project", session_project)
    monkeypatch.setattr(mcp, "_service", playbook_service)

    rejected = json.loads(
        await mcp.playbook_invoke(
            action="finish",
            run_id=run.id,
            status="completed",
        )
    )
    assert rejected == {
        "ok": False,
        "action": "finish",
        "error_code": "playbook_run_session_mismatch",
    }

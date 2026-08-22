"""Edition-facing Playbook projection never leaks owner data or ORM rows."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.facade.playbooks import PlaybookLibrary
from valuz_agent.facade.projects import ProjectRef
from valuz_agent.infra.database import Base
from valuz_agent.modules.playbooks.models import (
    PlaybookDefinitionRow,
    PlaybookRunRow,
    PlaybookVersionRow,
)


@pytest.mark.asyncio
async def test_project_definitions_and_runs_are_owner_scoped() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        definition = PlaybookDefinitionRow(
            user_id="owner-1",
            project_id="p1",
            name="Earnings review",
            status="active",
            current_version=1,
            revision=1,
        )
        db.add(definition)
        await db.flush()
        db.add(
            PlaybookVersionRow(
                user_id="owner-1",
                definition_id=definition.id,
                version=1,
                goal="Review earnings",
                required_skills=["earnings-analysis"],
            )
        )
        run = PlaybookRunRow(
            user_id="owner-1",
            definition_id=definition.id,
            definition_version=1,
            project_id="p1",
            status="completed",
            trigger_kind="user",
            output_refs=[{"type": "artifact", "id": "a1"}],
        )
        db.add(run)
        await db.flush()

        library = PlaybookLibrary(db)
        rows = await library.list_project("owner-1", "p1")
        assert rows[0][0].id == definition.id
        assert rows[0][1].required_skills == ("earnings-analysis",)
        runs = await library.list_runs("owner-1", "p1")
        assert runs[0].id == run.id
        assert await library.list_project("another-owner", "p1") == []
        assert await library.list_runs("another-owner", "p1") == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_command_facade_validates_payload_and_returns_immutable_refs() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class Projects:
        async def get(self, user_id: str, project_id: str):
            if user_id == "owner-1" and project_id == "p1":
                return ProjectRef(id="p1", name="Research", kind="project")
            return None

        async def create_chat(self, user_id: str, *, name: str = "Chat"):
            return ProjectRef(id="hidden", name=name, kind="chat")

        async def delete(self, user_id: str, project_id: str):
            return True

    async with sessions() as db:
        library = PlaybookLibrary(db, Projects())
        definition, version, created_project = await library.create_definition(
            "owner-1",
            {
                "project_id": "p1",
                "name": "Earnings review",
                "goal": "Review earnings against the active Thesis",
                "required_skills": ["earnings-analysis"],
                "outputs": ["strategy_evaluation"],
            },
        )
        run = await library.create_run(
            "owner-1",
            {
                "definition_id": definition.id,
                "project_id": "p1",
                "trigger_kind": "agent",
            },
        )

        assert created_project is False
        assert version.required_skills == ("earnings-analysis",)
        assert run.project_id == "p1"
        assert not hasattr(definition, "user_id")
    await engine.dispose()

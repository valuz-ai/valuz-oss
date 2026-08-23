"""Project resolution, immutable versions and fixed-version PlaybookRuns."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from valuz_agent.facade.projects import ProjectRef
from valuz_agent.infra.database import Base
from valuz_agent.modules.playbooks.models import (
    PlaybookDefinitionRow,
    PlaybookRunRow,
    PlaybookVersionRow,
)
from valuz_agent.modules.playbooks.schemas import (
    PlaybookCreateRequest,
    PlaybookRunCreateRequest,
    PlaybookRunUpdateRequest,
    PlaybookVersionCreateRequest,
)
from valuz_agent.modules.playbooks.service import PlaybookService

USER = "owner-1"


class FakeProjects:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], ProjectRef] = {}
        self.created_chat = 0
        self.deleted: list[str] = []

    def seed(self, user_id: str, project_id: str, *, kind: str = "project") -> ProjectRef:
        row = ProjectRef(id=project_id, name=project_id, kind=kind)  # type: ignore[arg-type]
        self.rows[(user_id, project_id)] = row
        return row

    async def get(self, user_id: str, project_id: str) -> ProjectRef | None:
        return self.rows.get((user_id, project_id))

    async def create_chat(self, user_id: str, *, name: str = "Chat") -> ProjectRef:
        self.created_chat += 1
        row = ProjectRef(id=f"chat-{self.created_chat}", name=name, kind="chat")
        self.rows[(user_id, row.id)] = row
        return row

    async def delete(self, user_id: str, project_id: str) -> bool:
        self.deleted.append(project_id)
        return self.rows.pop((user_id, project_id), None) is not None


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    # Import side effect is part of this test: all three tables share Base.
    assert PlaybookDefinitionRow.__table__ is not None
    assert PlaybookVersionRow.__table__ is not None
    assert PlaybookRunRow.__table__ is not None
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield session
    await engine.dispose()


def create_request(**updates) -> PlaybookCreateRequest:  # type: ignore[no-untyped-def]
    values = {
        "name": "Earnings review",
        "content": "Use /earnings-analysis to review earnings and propose research updates.",
        "reference_metadata": [{"kind": "skill", "ref": "earnings-analysis"}],
    }
    values.update(updates)
    return PlaybookCreateRequest.model_validate(values)


async def test_omitted_project_stays_owner_global_without_hidden_project(
    db: AsyncSession,
) -> None:
    projects = FakeProjects()
    service = PlaybookService(db, projects)
    definition, version = await service.create_definition(USER, create_request())
    assert definition.project_id is None
    assert projects.created_chat == 0
    assert version.definition_id == definition.id
    assert version.version == 1
    assert version.content.startswith("Use /earnings-analysis")


async def test_explicit_project_is_owner_scoped(db: AsyncSession) -> None:
    projects = FakeProjects()
    projects.seed(USER, "p1")
    projects.seed("another-owner", "private")
    service = PlaybookService(db, projects)
    definition, _ = await service.create_definition(USER, create_request(project_id="p1"))
    assert definition.project_id == "p1"
    with pytest.raises(LookupError, match="playbook_project_not_found"):
        await service.create_definition(USER, create_request(project_id="private"))


async def test_updates_append_versions_and_stale_base_never_overwrites(
    db: AsyncSession,
) -> None:
    projects = FakeProjects()
    projects.seed(USER, "p1")
    service = PlaybookService(db, projects)
    definition, _ = await service.create_definition(USER, create_request(project_id="p1"))
    definition, version = await service.create_version(
        USER,
        definition.id,
        PlaybookVersionCreateRequest(
            content="Review earnings and explicitly search for disconfirming evidence.",
            base_version=1,
            status="active",
        ),
    )
    assert definition.current_version == 2
    assert definition.status == "active"
    assert version.base_version == 1
    with pytest.raises(ValueError, match="stale Playbook version"):
        await service.create_version(
            USER,
            definition.id,
            PlaybookVersionCreateRequest(content="stale", base_version=1),
        )
    assert [item.version for item in await service.list_versions(USER, definition.id)] == [
        2,
        1,
    ]


async def test_run_fixes_definition_version_and_uses_actual_target_project(
    db: AsyncSession,
) -> None:
    projects = FakeProjects()
    projects.seed(USER, "definition-project")
    projects.seed(USER, "target-project")
    service = PlaybookService(db, projects)
    definition, _ = await service.create_definition(
        USER, create_request(project_id="definition-project")
    )
    run = await service.create_run(
        USER,
        PlaybookRunCreateRequest(
            definition_id=definition.id,
            definition_version=1,
            project_id="target-project",
            research_scope_id="scope-1",
            trigger_kind="automation",
            trigger_ref="automation-1",
            input_snapshot={"period": "FY2026"},
            context_snapshot={"thesis": {"id": "thesis-1", "version": 3}},
        ),
    )
    assert run.project_id == "target-project"
    assert run.definition_version == 1
    assert run.content_snapshot.startswith("Use /earnings-analysis")
    assert run.context_snapshot["thesis"]["version"] == 3

    current_target = await service.create_run(
        USER,
        PlaybookRunCreateRequest(
            definition_id=definition.id,
            current_project_id="target-project",
        ),
    )
    assert current_target.project_id == "target-project"

    global_run = await service.create_run(
        USER, PlaybookRunCreateRequest(definition_id=definition.id)
    )
    assert global_run.project_id is None
    assert projects.created_chat == 0

    running = await service.update_run(
        USER,
        run.id,
        PlaybookRunUpdateRequest(
            status="running",
            plan=[{"step": "fetch filings"}],
            tasks=[{"id": "task-1", "status": "running"}],
        ),
    )
    assert running.started_at is not None
    completed = await service.update_run(
        USER,
        run.id,
        PlaybookRunUpdateRequest(
            status="completed",
            artifact_refs=["artifact-1"],
            change_set_refs=["change-set-1"],
            output_refs=[{"type": "strategy_draft", "id": "draft-1"}],
        ),
    )
    assert completed.completed_at is not None
    assert completed.change_set_refs == ["change-set-1"]
    with pytest.raises(ValueError, match="invalid PlaybookRun transition"):
        await service.update_run(USER, run.id, PlaybookRunUpdateRequest(status="running"))


async def test_owner_cannot_read_definition_or_run(db: AsyncSession) -> None:
    projects = FakeProjects()
    projects.seed(USER, "p1")
    service = PlaybookService(db, projects)
    definition, _ = await service.create_definition(USER, create_request(project_id="p1"))
    run = await service.create_run(USER, PlaybookRunCreateRequest(definition_id=definition.id))
    with pytest.raises(LookupError):
        await service.get_definition("another-owner", definition.id)
    with pytest.raises(LookupError):
        await service.get_run("another-owner", run.id)

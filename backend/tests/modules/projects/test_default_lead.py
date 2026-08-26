"""Project default lead — the member that leads a task when none is named.

Stored on the project row (not as a flag on the member table) so "at most one
default lead" holds by construction; see
docs/design/channel-project-binding-and-default-lead.md §3.1.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.projects.service import ProjectService


class _FakeMembers:
    """The ``ProjectMembers`` protocol slice the service uses."""

    def __init__(self, slugs: set[str]) -> None:
        self.slugs = slugs

    async def get(self, user_id: str, project_id: str, agent_slug: str) -> Any:
        return object() if agent_slug in self.slugs else None

    async def delete_by_project(self, user_id: str, project_id: str) -> int:
        return 0


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "proj.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[ProjectRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


async def _project(sm, owner: str = "u1") -> str:
    async with sm() as db:
        row = ProjectRow(name="研究", kind="project", root_path="/tmp/research")
        await ProjectDatastore(db).create(owner, row)
        return row.id


def _service(db, members: _FakeMembers) -> ProjectService:
    return ProjectService(
        datastore=ProjectDatastore(db),
        event_bus=EventBus(),
        member_datastore=members,
    )


async def test_set_and_clear_default_lead(sessionmaker_) -> None:
    project_id = await _project(sessionmaker_)
    async with sessionmaker_() as db:
        svc = _service(db, _FakeMembers({"analyst"}))
        detail = await svc.set_default_lead("u1", project_id, "analyst")
        assert detail.default_lead_agent_slug == "analyst"

        cleared = await svc.set_default_lead("u1", project_id, None)
        assert cleared.default_lead_agent_slug is None


async def test_default_lead_must_be_a_member(sessionmaker_) -> None:
    """Rejecting a stranger here beats a launcher failure much later, far from
    the mistake."""
    project_id = await _project(sessionmaker_)
    async with sessionmaker_() as db:
        svc = _service(db, _FakeMembers({"analyst"}))
        with pytest.raises(ValueError, match="not a member"):
            await svc.set_default_lead("u1", project_id, "stranger")


async def test_set_default_lead_on_missing_project_raises(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        svc = _service(db, _FakeMembers({"analyst"}))
        with pytest.raises(KeyError):
            await svc.set_default_lead("u1", "nope", "analyst")


async def test_detail_surfaces_the_default_lead(sessionmaker_) -> None:
    project_id = await _project(sessionmaker_)
    async with sessionmaker_() as db:
        svc = _service(db, _FakeMembers({"analyst"}))
        await svc.set_default_lead("u1", project_id, "analyst")
    async with sessionmaker_() as db:
        svc = _service(db, _FakeMembers({"analyst"}))
        detail = await svc.get_project("u1", project_id)
        assert detail.default_lead_agent_slug == "analyst"


async def test_clear_default_lead_if_matches(sessionmaker_, monkeypatch) -> None:
    """Undeploying the default lead clears the pointer, so the project page
    stops advertising a lead that is no longer on the team."""
    from contextlib import asynccontextmanager

    import valuz_agent.infra.db as db_mod
    from valuz_agent.modules.projects.service import clear_default_lead_if

    project_id = await _project(sessionmaker_)
    async with sessionmaker_() as db:
        await _service(db, _FakeMembers({"analyst"})).set_default_lead(
            "u1", project_id, "analyst"
        )

    @asynccontextmanager
    async def fake_uow(*_args, **_kwargs):
        async with sessionmaker_() as db:
            yield db
            await db.commit()

    monkeypatch.setattr(db_mod, "async_unit_of_work", fake_uow)

    # A different member is undeployed — the lead pointer stays put.
    assert await clear_default_lead_if("u1", project_id, "researcher") is False
    # The lead itself is undeployed — cleared.
    assert await clear_default_lead_if("u1", project_id, "analyst") is True

    async with sessionmaker_() as db:
        row = await ProjectDatastore(db).get_by_id("u1", project_id)
        assert row is not None and row.default_lead_agent_slug is None

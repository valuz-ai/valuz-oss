"""Owner-scoping regression for ``ProjectDatastore`` reads.

The multi-user (single-instance) deployment relies on every owner-scoped read
filtering by the caller's ``user_id`` — without it, a shared backend leaks one
user's projects to another. These tests pin that boundary at the datastore: a
row owned by user A must read as absent for user B, ``list`` returns only the
caller's rows, and a cross-owner ``delete`` is a no-op.

The write-stamp (``UserMixin.default``) is exercised by setting the owner
context around ``create``; reads pass ``user_id`` explicitly (the production
callers source it from the request/boundary owner).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow


@pytest.fixture
def sessionmaker_(tmp_path):
    """Tmp-SQLite async sessionmaker with just the ``valuz_project`` table."""
    db_file = tmp_path / "proj.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[ProjectRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


async def _create_project_as(sm, owner: str, name: str) -> str:
    """Insert a project owned by ``owner`` (stamped explicitly via create)."""
    async with sm() as db:
        row = ProjectRow(name=name, kind="project", root_path=f"/tmp/{name}")
        await ProjectDatastore(db).create(owner, row)
        return row.id


class TestProjectReadOwnerScoping:
    async def test_get_by_id_reads_as_absent_for_other_owner(self, sessionmaker_) -> None:
        pid = await _create_project_as(sessionmaker_, "user-A", "A1")
        async with sessionmaker_() as db:
            ds = ProjectDatastore(db)
            assert await ds.get_by_id("user-A", pid) is not None
            assert await ds.get_by_id("user-B", pid) is None

    async def test_list_projects_returns_only_callers_rows(self, sessionmaker_) -> None:
        await _create_project_as(sessionmaker_, "user-A", "A1")
        await _create_project_as(sessionmaker_, "user-B", "B1")
        async with sessionmaker_() as db:
            ds = ProjectDatastore(db)
            assert {r.name for r in await ds.list_projects("user-A")} == {"A1"}
            assert {r.name for r in await ds.list_projects("user-B")} == {"B1"}

    async def test_get_by_root_path_is_owner_scoped(self, sessionmaker_) -> None:
        await _create_project_as(sessionmaker_, "user-A", "A1")
        async with sessionmaker_() as db:
            ds = ProjectDatastore(db)
            assert await ds.get_by_root_path("user-A", "/tmp/A1") is not None
            assert await ds.get_by_root_path("user-B", "/tmp/A1") is None

    async def test_delete_across_owners_is_a_noop(self, sessionmaker_) -> None:
        pid = await _create_project_as(sessionmaker_, "user-A", "A1")

        # Wrong owner: must not destroy A's row.
        async with sessionmaker_() as db:
            await ProjectDatastore(db).delete("user-B", pid)
        async with sessionmaker_() as db:
            assert await ProjectDatastore(db).get_by_id("user-A", pid) is not None

        # Correct owner: gone.
        async with sessionmaker_() as db:
            await ProjectDatastore(db).delete("user-A", pid)
        async with sessionmaker_() as db:
            assert await ProjectDatastore(db).get_by_id("user-A", pid) is None

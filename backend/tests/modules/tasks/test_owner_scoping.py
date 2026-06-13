"""Owner-scoping regression for the task datastores.

User-facing reads filter by ``user_id`` and writes stamp the owner. A few
methods stay cross-owner on purpose and are asserted here too:
``TaskDatastore.list_active`` (startup recovery) and
``TaskSessionDatastore.get_run`` / ``next_sequence`` (keyed on the
globally-unique kernel session id / per-task sequence).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.tasks.datastore import (
    TaskDatastore,
    TaskEventDatastore,
    TaskSessionDatastore,
)
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "tasks.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[TaskRow.__table__, TaskEventRow.__table__, TaskSessionRow.__table__],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


def _task(project_id: str = "p1", *, status: str = "active") -> TaskRow:
    return TaskRow(
        id=uuid4().hex,
        project_id=project_id,
        file_path="/x",
        title="T",
        goal="g",
        status=status,
        lead_agent_slug="lead",
        current_holder="lead",
    )


def _run(task_id: str, *, session_id: str, project_id: str = "p1") -> TaskSessionRow:
    return TaskSessionRow(
        id=uuid4().hex,
        project_id=project_id,
        task_id=task_id,
        session_id=session_id,
        agent_slug="a1",
        sequence=1,
        kind="lead",
    )


class TestTaskOwnerScoping:
    async def test_task_reads_scoped_by_owner(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            t = await TaskDatastore(db).create_task("user-A", _task())
        async with sessionmaker_() as db:
            ds = TaskDatastore(db)
            assert (await ds.get_task("user-A", t.id)) is not None
            assert (await ds.get_task("user-B", t.id)) is None
            assert (await ds.get_task_by_project("user-A", "p1", t.id)) is not None
            assert (await ds.get_task_by_project("user-B", "p1", t.id)) is None
            assert {r.id for r in await ds.list_tasks("user-A", "p1")} == {t.id}
            assert await ds.list_tasks("user-B", "p1") == []
            assert {r.id for r in await ds.list_all("user-A")} == {t.id}
            assert await ds.list_all("user-B") == []

    async def test_update_status_owner_scoped(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            t = await TaskDatastore(db).create_task("user-A", _task())
        async with sessionmaker_() as db:
            assert (await TaskDatastore(db).update_task_status("user-B", t.id, "stopped")) is False
        async with sessionmaker_() as db:
            assert (await TaskDatastore(db).update_task_status("user-A", t.id, "stopped")) is True

    async def test_list_active_is_cross_owner(self, sessionmaker_) -> None:
        # Startup recovery resumes every owner's active tasks.
        async with sessionmaker_() as db:
            ds = TaskDatastore(db)
            await ds.create_task("user-A", _task(status="active"))
            await ds.create_task("user-B", _task(status="active"))
        async with sessionmaker_() as db:
            active = await TaskDatastore(db).list_active()
            assert {r.user_id for r in active} == {"user-A", "user-B"}

    async def test_events_scoped_by_owner(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            ev = await TaskEventDatastore(db).append_event(
                "user-A", "p1", "t1", type="x", actor="lead"
            )
        async with sessionmaker_() as db:
            ds = TaskEventDatastore(db)
            assert {r.id for r in await ds.list_events("user-A", "p1", "t1")} == {ev.id}
            assert await ds.list_events("user-B", "p1", "t1") == []
            assert (await ds.get_event("user-A", ev.id)) is not None
            assert (await ds.get_event("user-B", ev.id)) is None
            assert (await ds.latest_event("user-A", "t1")) is not None
            assert (await ds.latest_event("user-B", "t1")) is None

    async def test_runs_scoped_by_owner(self, sessionmaker_) -> None:
        async with sessionmaker_() as db:
            r = await TaskSessionDatastore(db).create_run("user-A", _run("t1", session_id="s1"))
        async with sessionmaker_() as db:
            ds = TaskSessionDatastore(db)
            assert {x.id for x in await ds.list_runs("user-A", "t1")} == {r.id}
            assert await ds.list_runs("user-B", "t1") == []
            assert {x.id for x in await ds.list_all("user-A")} == {r.id}
            assert await ds.list_all("user-B") == []
            assert (await ds.get_run_by_id("user-A", r.id)) is not None
            assert (await ds.get_run_by_id("user-B", r.id)) is None
            # get_run is keyed on the globally-unique kernel session id (system).
            assert (await ds.get_run("s1")) is not None

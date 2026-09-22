"""A task-mode run is resolved from its LEAD session (valuz/valuz#26).

The run row is written before the lead session exists, so ``session_id`` on
the run is empty at the moment the lead calls ``output``; the task row the
run kicked off is the join.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow
from valuz_agent.modules.tasks.models import TaskRow, TaskSessionRow


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "lead.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[
            AutomationRow.__table__,
            AutomationRunRow.__table__,
            TaskRow.__table__,
            TaskSessionRow.__table__,
        ],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


def _run(run_id: str, triggered_at: int) -> AutomationRunRow:
    return AutomationRunRow(
        id=run_id,
        user_id="u1",
        automation_id="auto-1",
        project_id="p1",
        trigger_type="cron",
        status="success",
        triggered_at=triggered_at,
    )


def _task(task_id: str, *, created_at: int, metadata: dict | None = None) -> TaskRow:
    return TaskRow(
        id=task_id,
        user_id="u1",
        project_id="p1",
        file_path="/t",
        title="t",
        goal="g",
        lead_agent_slug="lead",
        current_holder="lead",
        trigger_type="automation",
        trigger_automation_id="auto-1",
        metadata_=metadata or {},
        created_at=created_at,
    )


def _lead(task_id: str, session_id: str, kind: str = "lead") -> TaskSessionRow:
    return TaskSessionRow(
        user_id="u1",
        project_id="p1",
        task_id=task_id,
        session_id=session_id,
        agent_slug="lead",
        sequence=0,
        kind=kind,
        status="active",
    )


async def test_the_task_row_names_the_run_that_kicked_it_off(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        db.add_all(
            [
                _run("run-old", 1_000),
                _run("run-new", 5_000),
                _task("task-1", created_at=1_500, metadata={"automation_run_id": "run-old"}),
                _lead("task-1", "sess-lead"),
            ]
        )
        await db.commit()
    async with sessionmaker_() as db:
        run = await AutomationDatastore(db).get_run_for_task_lead_session("u1", "sess-lead")
    assert run is not None and run.id == "run-old"


async def test_a_task_row_without_the_run_id_matches_the_newest_run_before_it(
    sessionmaker_,
) -> None:
    """Rows written before ``automation_run_id`` existed: the run is the newest
    one that does not postdate the task (a later run belongs to a later task)."""
    async with sessionmaker_() as db:
        db.add_all(
            [
                _run("run-1", 1_000),
                _run("run-2", 2_000),
                _run("run-3", 9_000),
                _task("task-2", created_at=2_500),
                _lead("task-2", "sess-lead-2"),
            ]
        )
        await db.commit()
    async with sessionmaker_() as db:
        run = await AutomationDatastore(db).get_run_for_task_lead_session("u1", "sess-lead-2")
    assert run is not None and run.id == "run-2"


async def test_only_a_lead_of_an_automation_task_resolves(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        db.add_all(
            [
                _run("run-1", 1_000),
                _task("task-3", created_at=1_500, metadata={"automation_run_id": "run-1"}),
                _lead("task-3", "sess-member", kind="member"),
            ]
        )
        await db.commit()
    async with sessionmaker_() as db:
        ds = AutomationDatastore(db)
        assert await ds.get_run_for_task_lead_session("u1", "sess-member") is None
        assert await ds.get_run_for_task_lead_session("u1", "sess-unknown") is None
        assert await ds.get_run_for_task_lead_session("u2", "sess-member") is None


@pytest.mark.asyncio
async def test_service_falls_back_to_the_task_lead_lookup() -> None:
    from valuz_agent.modules.automations.service import AutomationService

    ds = Mock()
    ds.get_run_by_session = AsyncMock(return_value=None)
    lead_run = SimpleNamespace(id="run-x")
    ds.get_run_for_task_lead_session = AsyncMock(return_value=lead_run)
    svc = AutomationService.__new__(AutomationService)
    svc._ds = ds
    svc._require_user_id = lambda user_id: user_id  # type: ignore[method-assign]

    assert await svc.get_run_for_session("sess-lead", user_id="u1") is lead_run
    ds.get_run_by_session.assert_awaited_once_with("u1", "sess-lead")
    ds.get_run_for_task_lead_session.assert_awaited_once_with("u1", "sess-lead")

    ds.get_run_by_session = AsyncMock(return_value=SimpleNamespace(id="run-chat"))
    ds.get_run_for_task_lead_session.reset_mock()
    assert (await svc.get_run_for_session("sess-chat", user_id="u1")).id == "run-chat"
    ds.get_run_for_task_lead_session.assert_not_awaited()

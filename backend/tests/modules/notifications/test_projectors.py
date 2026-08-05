"""Projector integration — question (via aggregator) + failure (via tasks
messaging) feed the durable notification ledger (docs/design/notifications.md)."""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.modules.decisions.aggregator import DecisionAggregator
from valuz_agent.modules.notifications.models import NotificationRow
from valuz_agent.modules.notifications.service import notification_service
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.notifications import projectors
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow

OWNER = "local-test-owner"


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "proj.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[
            TaskRow.__table__,
            TaskSessionRow.__table__,
            TaskEventRow.__table__,
            ProjectRow.__table__,
            NotificationRow.__table__,
        ],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", async_factory)
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


def _seed_task(db_factory, *, task_id="t1", project_id="w1", subtask_key="arch"):
    db = db_factory()
    try:
        db.add(ProjectRow(user_id=OWNER, id=project_id, name="P", kind="project", icon="🛠"))
        db.add(
            TaskRow(
                user_id=OWNER,
                id=task_id,
                project_id=project_id,
                file_path="/tmp/t.md",
                title="季度报告",
                goal="g",
                status="active",
                created_by="user",
                lead_agent_slug="lead",
                current_holder="lead",
                plan={"subtasks": [{"key": subtask_key, "title": "架构", "agent": "architect",
                                    "status": "in_progress", "depends_on": []}]},
            )
        )
        db.add(
            TaskSessionRow(
                user_id=OWNER, id="run1", project_id=project_id, task_id=task_id,
                session_id="sub-sess", agent_slug="architect", sequence=1,
                kind="subtask", subtask_key=subtask_key, status="active",
            )
        )
        db.commit()
    finally:
        db.close()


def _notifs(db_factory):
    db = db_factory()
    try:
        result = db.execute(
            select(NotificationRow).order_by(NotificationRow.created_at)
        )
        return list(result.scalars())
    finally:
        db.close()


def _subtask_session():
    return SimpleNamespace(
        id="sub-sess", user_id=OWNER, status="running",
        metadata={"valuz": {"run_kind": "subtask", "task_id": "t1", "agent_slug": "architect"}},
    )


def _requires_action_event(pending_id="p1"):
    return SimpleNamespace(
        type="requires_action", timestamp=datetime.now(UTC),
        data={"pending_id": pending_id, "subject": "clarifying_questions",
              "payload": {"questions": [{"question": "选哪种布局？"}]}},
    )


def _resolved_event(pending_id="p1"):
    return SimpleNamespace(type="action_resolved", timestamp=datetime.now(UTC),
                           data={"pending_id": pending_id, "decision": "answer"})


def _agg():
    agg = DecisionAggregator()

    async def _load(_o, sid):
        return _subtask_session() if sid == "sub-sess" else None

    async def _noop(_o):
        return None

    agg._load_session = _load  # type: ignore[assignment]
    agg._hydrate_owner = _noop  # type: ignore[assignment]
    return agg


def test_question_projects_notification(db_factory) -> None:
    _seed_task(db_factory)
    agg = _agg()
    asyncio.run(agg._handle_event(OWNER, "sub-sess", _requires_action_event()))
    rows = _notifs(db_factory)
    assert len(rows) == 1
    n = rows[0]
    assert n.kind == "question"
    assert n.dedup_key == "q:p1"
    assert n.action == "answer"
    assert n.route == "/tasks/t1"
    assert n.body == "选哪种布局？"
    assert n.payload["question_payload"]["questions"][0]["question"] == "选哪种布局？"


def test_question_resolve_clears_notification(db_factory) -> None:
    _seed_task(db_factory)
    agg = _agg()
    asyncio.run(agg._handle_event(OWNER, "sub-sess", _requires_action_event()))
    asyncio.run(agg._handle_event(OWNER, "sub-sess", _resolved_event()))
    entries, unread = asyncio.run(notification_service.snapshot(OWNER))
    assert entries == []
    assert unread == 0


def test_failure_projects_notification_and_resolves_on_resume(db_factory) -> None:
    _seed_task(db_factory)
    # Simulate a task_blocked event landing, then the projector call.
    async def run_ingest():
        await projectors.record_task_failure_notification(
            task_id="t1", project_id="w1", event_id="ev-9",
            event_type="task_blocked", reason="lead crashed", user_id=OWNER,
        )

    asyncio.run(run_ingest())
    rows = _notifs(db_factory)
    assert len(rows) == 1
    assert rows[0].kind == "task_failed"
    assert rows[0].action == "resume"
    assert rows[0].title == "季度报告"  # looked up from the task row
    assert rows[0].body == "lead crashed"

    # Resuming clears it.
    asyncio.run(notification_service.resolve_task(OWNER, "t1"))
    entries, unread = asyncio.run(notification_service.snapshot(OWNER))
    assert entries == []
    assert unread == 0


def test_failure_deduped_by_event_id(db_factory) -> None:
    _seed_task(db_factory)

    async def run():
        await projectors.record_task_failure_notification(
            task_id="t1", project_id="w1", event_id="ev-1",
            event_type="task_blocked", reason="x", user_id=OWNER,
        )
        await projectors.record_task_failure_notification(
            task_id="t1", project_id="w1", event_id="ev-1",  # same event → no dup
            event_type="task_blocked", reason="x", user_id=OWNER,
        )

    asyncio.run(run())
    assert len(_notifs(db_factory)) == 1


def test_completed_task_projects_informational_notification(db_factory) -> None:
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.events import finalize_task

    _seed_task(db_factory)

    async def run() -> None:
        async with async_unit_of_work() as db:
            await finalize_task(
                db,
                user_id=OWNER,
                project_id="w1",
                task_id="t1",
                status="completed",
                event_type="task_completed",
                actor="lead",
                session_id="lead-session",
                payload={"summary": "报告已经生成"},
            )

    asyncio.run(run())

    rows = _notifs(db_factory)
    assert len(rows) == 1
    notification = rows[0]
    assert notification.kind == "task_completed"
    assert notification.title == "季度报告"
    assert notification.body == "报告已经生成"
    assert notification.action == "none"
    assert notification.urgency == "info"
    assert notification.route == "/tasks/t1"

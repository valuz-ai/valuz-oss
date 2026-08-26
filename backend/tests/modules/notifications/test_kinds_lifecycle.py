"""Functional lifecycle test for EVERY notification kind
(docs/design/notifications.md).

One flow per kind, end-to-end through the real projectors + service + the HTTP
route handlers (called directly with a tmp DB): create → snapshot → read →
resolve. This is the "all kinds" functional matrix — adding a new kind should
add a case here.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

import valuz_agent.api.routes.notifications as notif_routes
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

    db_file = tmp_path / "kinds.db"
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


def _seed_task(db_factory):
    db = db_factory()
    try:
        db.add(ProjectRow(user_id=OWNER, id="w1", name="P", kind="project", icon="🛠"))
        db.add(
            TaskRow(
                user_id=OWNER, id="t1", project_id="w1", file_path="/tmp/t.md",
                title="季度报告", goal="g", status="active", created_by="user",
                lead_agent_slug="lead", current_holder="lead",
                plan={"subtasks": [{"key": "arch", "title": "架构", "agent": "architect",
                                    "status": "in_progress", "depends_on": []}]},
            )
        )
        db.add(
            TaskSessionRow(
                user_id=OWNER, id="run1", project_id="w1", task_id="t1",
                session_id="sub-sess", agent_slug="architect", sequence=1,
                kind="subtask", subtask_key="arch", status="active",
            )
        )
        db.commit()
    finally:
        db.close()


# ---- route handlers driven directly ---------------------------------


def _list():
    return asyncio.run(notif_routes.list_notifications(user_id=OWNER))


def _read(nid):
    return asyncio.run(notif_routes.mark_read(nid, user_id=OWNER))


def _read_all():
    return asyncio.run(notif_routes.mark_all_read(user_id=OWNER))


def _dismiss(nid):
    return asyncio.run(notif_routes.dismiss(nid, user_id=OWNER))


# ---- question kind: full lifecycle ----------------------------------


def _agg():
    agg = DecisionAggregator()

    async def _load(_o, sid):
        return (
            SimpleNamespace(
                id="sub-sess", user_id=OWNER, status="running",
                metadata={"valuz": {"run_kind": "subtask", "task_id": "t1",
                                    "agent_slug": "architect"}},
            )
            if sid == "sub-sess"
            else None
        )

    async def _noop(_o):
        return None

    agg._load_session = _load  # type: ignore[assignment]
    agg._hydrate_owner = _noop  # type: ignore[assignment]
    return agg


def test_question_kind_lifecycle(db_factory) -> None:
    _seed_task(db_factory)
    agg = _agg()
    ra = SimpleNamespace(
        type="requires_action", timestamp=datetime.now(UTC),
        data={"pending_id": "p1", "subject": "clarifying_questions",
              "payload": {"questions": [{"question": "选哪种布局？", "options": [{"label": "A"}]}]}},
    )
    asyncio.run(agg._handle_event(OWNER, "sub-sess", ra))

    # create → visible in the list with the answer payload intact.
    res = _list()
    assert res.unread == 1
    q = res.entries[0]
    assert q.kind == "question"
    assert q.action == "answer"
    assert q.session_id == "sub-sess" and q.pending_id == "p1"
    assert q.payload["question_payload"]["questions"][0]["question"] == "选哪种布局？"

    # read → unread clears, still open.
    _read(q.id)
    assert _list().unread == 0
    assert len(_list().entries) == 1

    # answer (kernel action_resolved) → resolves the notification.
    resolved = SimpleNamespace(type="action_resolved", timestamp=datetime.now(UTC),
                               data={"pending_id": "p1", "decision": "answer"})
    asyncio.run(agg._handle_event(OWNER, "sub-sess", resolved))
    assert _list().entries == []


# ---- task_failed kind: full lifecycle -------------------------------


def test_task_failed_kind_lifecycle_resume(db_factory) -> None:
    _seed_task(db_factory)
    asyncio.run(
        projectors.record_task_failure_notification(
            task_id="t1", project_id="w1", event_id="ev-1",
            event_type="task_blocked", reason="lead crashed", user_id=OWNER,
        )
    )
    res = _list()
    assert res.unread == 1
    f = res.entries[0]
    assert f.kind == "task_failed"
    assert f.action == "resume"
    assert f.route == "/tasks/t1"
    assert f.title == "季度报告" and f.body == "lead crashed"

    # resume clears it (the resume path calls resolve_task).
    asyncio.run(notification_service.resolve_task(OWNER, "t1"))
    assert _list().entries == []


def test_task_failed_kind_lifecycle_dismiss(db_factory) -> None:
    _seed_task(db_factory)
    asyncio.run(
        projectors.record_task_failure_notification(
            task_id="t1", project_id="w1", event_id="ev-2",
            event_type="kickoff_failed", reason="missing api key", user_id=OWNER,
        )
    )
    fid = _list().entries[0].id
    # user dismisses instead of resuming → resolved, gone.
    _dismiss(fid)
    assert _list().entries == []


def test_read_all_across_kinds(db_factory) -> None:
    _seed_task(db_factory)
    # one of each kind.
    asyncio.run(_run_question(db_factory))
    asyncio.run(
        projectors.record_task_failure_notification(
            task_id="t1", project_id="w1", event_id="ev-3",
            event_type="task_blocked", reason="x", user_id=OWNER,
        )
    )
    assert _list().unread == 2
    _read_all()
    assert _list().unread == 0
    assert len(_list().entries) == 2  # read, but still open


async def _run_question(_db_factory):
    agg = _agg()
    ra = SimpleNamespace(
        type="requires_action", timestamp=datetime.now(UTC),
        data={"pending_id": "pX", "subject": "clarifying_questions",
              "payload": {"questions": [{"question": "q?"}]}},
    )
    await agg._handle_event(OWNER, "sub-sess", ra)

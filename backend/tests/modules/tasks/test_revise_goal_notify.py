"""Tests for messaging.notify_lead_goal_revised — the MVP that pushes a user
goal revision to a running task's lead via a ``revise_goal`` mailbox message
(task.goal alone is pull-only; the lead never re-reads it mid-run).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.modules.tasks import messaging
from valuz_agent.modules.tasks.mailbox import mailbox_registry
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "revise_goal.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine,
        tables=[TaskRow.__table__, TaskSessionRow.__table__, TaskEventRow.__table__],
    )
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod, "AsyncSessionLocal", async_sessionmaker(bind=async_engine, expire_on_commit=False)
    )
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _reset_mailbox():
    mailbox_registry._boxes.clear()
    yield
    mailbox_registry._boxes.clear()


def _seed_lead(db_factory, tmp_path, *, lead_session_id="lead-1"):
    db = db_factory()
    try:
        db.add(
            TaskSessionRow(
                user_id="local-test-owner",
                project_id="w1",
                task_id="t1",
                session_id=lead_session_id,
                agent_slug="lead-agent",
                sequence=0,
                kind="lead",
                status="active",
                label="Kickoff",
                goal="old goal",
                project_mode="shared",
                run_dir=str(tmp_path),
            )
        )
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_delivers_revise_goal_message_to_registered_lead(db_factory, tmp_path):
    _seed_lead(db_factory, tmp_path, lead_session_id="lead-1")
    mailbox_registry.register("lead-1")

    res = await messaging.notify_lead_goal_revised(
        task_id="t1", project_id="w1", new_goal="NEW GOAL"
    )

    assert res["delivered"] is True
    assert res["lead_session_id"] == "lead-1"
    assert res["reason"] is None
    msg = mailbox_registry._boxes["lead-1"].get_nowait()
    assert msg.kind == "revise_goal"
    assert msg.payload["goal"] == "NEW GOAL"
    # the wrapper carries the new goal + the goal-mode "authoritative" caveat
    assert "NEW GOAL" in msg.text
    assert "authoritative" in msg.text


@pytest.mark.asyncio
async def test_offline_lead_returns_lead_offline_not_delivered(db_factory, tmp_path):
    _seed_lead(db_factory, tmp_path, lead_session_id="lead-1")
    # lead run exists but its mailbox is NOT registered (already finished)

    res = await messaging.notify_lead_goal_revised(task_id="t1", project_id="w1", new_goal="NEW")

    assert res["delivered"] is False
    assert res["reason"] == "LEAD_OFFLINE"
    assert res["lead_session_id"] == "lead-1"


@pytest.mark.asyncio
async def test_no_lead_run_returns_no_lead(db_factory, tmp_path):
    # no lead session seeded for the task
    res = await messaging.notify_lead_goal_revised(task_id="t1", project_id="w1", new_goal="NEW")

    assert res["delivered"] is False
    assert res["reason"] == "NO_LEAD"
    assert res["lead_session_id"] is None

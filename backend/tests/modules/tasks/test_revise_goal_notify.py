"""Tests for messaging.notify_lead_goal_revised — the MVP that pushes a user
goal revision to a running task's lead via a ``revise_goal`` mailbox message
(task.goal alone is pull-only; the lead never re-reads it mid-run).
"""

from __future__ import annotations

import pytest

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import mailbox_store, messaging
from valuz_agent.modules.tasks.models import TaskSessionRow

LOCAL_USER_ID = "local-test-owner"


@pytest.fixture(autouse=True)
def _reset_mailbox():
    yield


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
async def test_a_goal_revision_reaches_the_lead_with_its_caveat(db_factory, tmp_path):
    _seed_lead(db_factory, tmp_path, lead_session_id="lead-1")

    async with async_unit_of_work() as db:
        res = await messaging.notify_lead_goal_revised(
            db, task_id="t1", project_id="w1", new_goal="NEW GOAL", user_id=LOCAL_USER_ID
        )
    assert res["delivered"] is True
    assert res["lead_session_id"] == "lead-1"
    assert res["reason"] is None

    drained = await mailbox_store.drain("lead-1")
    assert len(drained) == 1
    msg = drained[0]
    assert msg.kind == "revise_goal"
    assert msg.payload["goal"] == "NEW GOAL"
    assert msg.origin == "goal-revised"
    # the wrapper carries the new goal + the goal-mode "authoritative" caveat
    assert "NEW GOAL" in msg.text
    assert "authoritative" in msg.text


@pytest.mark.asyncio
async def test_a_goal_revision_reaches_a_lead_driven_by_another_process(db_factory, tmp_path):
    """Dropping a revision was the worst of the available outcomes.

    The goal row was updated either way, so the task LOOKED redirected while
    the lead kept pursuing the old objective. The mailbox here is empty, as it
    is in every host process but the one driving the lead.
    """
    _seed_lead(db_factory, tmp_path, lead_session_id="lead-1")
    async with async_unit_of_work() as db:
        res = await messaging.notify_lead_goal_revised(
            db, task_id="t1", project_id="w1", new_goal="PIVOT", user_id=LOCAL_USER_ID
        )
    assert res["delivered"] is True

    drained = await mailbox_store.drain("lead-1")
    assert [m.kind for m in drained] == ["revise_goal"]
    assert "PIVOT" in drained[0].text


@pytest.mark.asyncio
async def test_a_revision_is_rolled_back_with_its_transaction(db_factory, tmp_path):
    """The revision and the goal row must not be able to disagree.

    ``notify_lead_goal_revised`` runs on the caller's transaction precisely so
    that a failure after it — the timeline write, the row update — takes the
    queued revision down with it, rather than leaving a lead told to pursue a
    goal the task never adopted.
    """
    _seed_lead(db_factory, tmp_path, lead_session_id="lead-1")

    class _BoomError(RuntimeError):
        pass

    with pytest.raises(_BoomError):
        async with async_unit_of_work() as db:
            await messaging.notify_lead_goal_revised(
                db, task_id="t1", project_id="w1", new_goal="DOOMED", user_id=LOCAL_USER_ID
            )
            raise _BoomError

    assert await mailbox_store.drain("lead-1") == []


@pytest.mark.asyncio
async def test_no_lead_run_returns_no_lead(db_factory, tmp_path):
    # no lead session seeded for the task
    async with async_unit_of_work() as db:
        res = await messaging.notify_lead_goal_revised(
            db, task_id="t1", project_id="w1", new_goal="NEW", user_id=LOCAL_USER_ID
        )

    assert res["delivered"] is False
    assert res["reason"] == "NO_LEAD"
    assert res["lead_session_id"] is None

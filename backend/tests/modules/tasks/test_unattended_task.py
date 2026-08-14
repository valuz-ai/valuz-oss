"""What happens to a task when nobody is minding it.

Defects that shared one shape — a task whose actor is gone, or whose project
or session is gone, kept looking fine to everything that checks:

* an inbox with no reader read as a live lead, so the watchdog never blocked
  the task and ``inject_into_task`` reported delivery into a queue nobody drains;
* deleting a project left its tasks behind, and the next boot resurrected them
  against deleted kernel sessions and announced them as blocked;
* deleting a session left its run-index row behind, pointing at a session that
  no longer exists, with nothing anywhere to reconcile it.
"""

from __future__ import annotations

import asyncio

from valuz_agent.modules.tasks.mailbox import MailboxRegistry
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow
from valuz_agent.modules.tasks.plan import TaskPlan
from valuz_agent.modules.tasks.purge import purge_project_tasks, purge_tasks

OWNER = "local-test-owner"


def _seed(db_factory, *, task_id: str, project_id: str = "p1", status: str = "active") -> None:
    plan = TaskPlan()
    plan.add([{"key": "a", "title": "A", "goal": "g", "agent": "worker"}])
    db = db_factory()
    try:
        db.add(
            TaskRow(
                id=task_id,
                user_id=OWNER,
                project_id=project_id,
                file_path=f"tasks/{task_id}.md",
                title="t",
                goal="g",
                status=status,
                lead_agent_slug="lead",
                current_holder="lead",
                plan=plan.to_dict(),
            )
        )
        db.add(
            TaskSessionRow(
                id=f"run-{task_id}",
                user_id=OWNER,
                project_id=project_id,
                task_id=task_id,
                session_id=f"lead-{task_id}",
                agent_slug="lead",
                sequence=1,
                kind="lead",
                status="active",
            )
        )
        db.add(
            TaskEventRow(
                id=f"ev-{task_id}",
                user_id=OWNER,
                project_id=project_id,
                task_id=task_id,
                sequence=1,
                type="task_created",
                actor="user",
                payload={},
            )
        )
        db.commit()
    finally:
        db.close()


def _counts(db_factory, task_id: str) -> tuple[int, int, int]:
    db = db_factory()
    try:
        return (
            len([r for r in db.query(TaskRow).all() if r.id == task_id]),
            len([r for r in db.query(TaskSessionRow).all() if r.task_id == task_id]),
            len([r for r in db.query(TaskEventRow).all() if r.task_id == task_id]),
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# The liveness oracle
# ---------------------------------------------------------------------------


def test_a_box_with_no_reader_is_not_a_live_session() -> None:
    """``register`` is non-owning, so "a box exists" cannot mean "someone reads it".

    This is the whole bug in one assertion: three subsystems asked
    ``is_registered`` whether the lead loop was alive, and a box pre-seeded for
    a loop that never started answered yes for the life of the process.

    The registry no longer claims to know. Liveness moved out of it entirely —
    to the execution lease, which is shared state and can therefore answer for
    every process rather than for this one. All that survives here is the
    honest, narrow statement ``is_registered`` was always making: a queue
    exists, and nothing more.
    """
    reg = MailboxRegistry()
    reg.register("s1")

    assert reg.has_pending("s1") is False
    assert not hasattr(reg, "is_owned"), (
        "the registry must not offer a liveness oracle again — a box pre-seeded "
        "for a loop that never started answered yes for the life of the process, "
        "and that blinded the watchdog that exists to catch exactly this task"
    )


def test_a_box_is_reclaimed_by_whoever_still_owns_the_session() -> None:
    """Boxes are dropped on exit now, and only by the current holder.

    Nothing dropped them for a while, so a long-lived process accumulated one
    queue per session it had ever run. Reclaiming them is safe only because the
    box is now a local buffer with a single owner and the loop gates the drop
    on still holding its lease — an ungated drop is the race the claim token
    used to guard.
    """
    reg = MailboxRegistry()
    reg.register("s1")
    reg.unregister("s1")

    assert reg.try_get("s1") is None, "the box is gone, and reading it is not an error"
    assert not hasattr(reg, "release"), (
        "nothing may drop a box on the way out: that race — a stale loop's "
        "teardown popping the box a resumed loop was reading — is why the "
        "claim token existed, and why ownership left this class"
    )


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


def test_purge_removes_the_task_and_everything_indexed_under_it(db_factory) -> None:
    _seed(db_factory, task_id="t-purge")
    assert _counts(db_factory, "t-purge") == (1, 1, 1)

    assert asyncio.run(purge_tasks(OWNER, ["t-purge"])) == 1

    assert _counts(db_factory, "t-purge") == (0, 0, 0), (
        "runs and events have no foreign key to the header — leaving them "
        "behind makes invisible garbage that nothing else will ever collect"
    )


def test_purge_is_idempotent_and_owner_scoped(db_factory) -> None:
    _seed(db_factory, task_id="t-mine")
    db = db_factory()
    try:
        row = db.get(TaskRow, "t-mine")
        db.add(
            TaskRow(
                id="t-theirs",
                user_id="somebody-else",
                project_id=row.project_id,
                file_path="tasks/t-theirs.md",
                title="t",
                goal="g",
                status="active",
                lead_agent_slug="lead",
                current_holder="lead",
                plan={},
            )
        )
        db.commit()
    finally:
        db.close()

    assert asyncio.run(purge_tasks(OWNER, ["t-mine", "t-theirs"])) == 1
    assert asyncio.run(purge_tasks(OWNER, ["t-mine"])) == 0

    db = db_factory()
    try:
        assert db.get(TaskRow, "t-theirs") is not None, "another owner's task is not ours to delete"
    finally:
        db.close()


def test_project_deletion_actually_calls_the_cascade(db_factory) -> None:
    """The wiring, not just the door.

    `purge_project_tasks` existing proves nothing on its own — the bug was that
    `delete_project` never called anything like it.
    """
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.eventbus import EventBus
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.projects.models import ProjectRow
    from valuz_agent.modules.projects.service import ProjectService

    _seed(db_factory, task_id="t-cascade", project_id="doomed")
    db = db_factory()
    try:
        db.add(
            ProjectRow(
                id="doomed",
                user_id=OWNER,
                name="Doomed",
                kind="project",
                root_path="/tmp/doomed",
            )
        )
        db.commit()
    finally:
        db.close()

    async def _delete() -> None:
        async with async_unit_of_work() as db:
            await ProjectService(
                datastore=ProjectDatastore(db), event_bus=EventBus()
            ).delete_project(OWNER, "doomed")

    asyncio.run(_delete())

    assert _counts(db_factory, "t-cascade") == (0, 0, 0), (
        "an active task surviving its project gets respawned by the next boot "
        "against a deleted kernel session and announced as blocked"
    )


def test_deleting_a_project_takes_its_tasks_with_it(db_factory) -> None:
    """The cascade that was missing.

    Left behind, an ``active`` task with no kernel sessions gets respawned by
    the next boot against a dead session id and announced as blocked — for a
    project the user deleted, on a row with no delete path.
    """
    _seed(db_factory, task_id="t-a", project_id="doomed")
    _seed(db_factory, task_id="t-b", project_id="doomed")
    _seed(db_factory, task_id="t-keep", project_id="other")

    assert asyncio.run(purge_project_tasks(OWNER, "doomed")) == 2

    assert _counts(db_factory, "t-a") == (0, 0, 0)
    assert _counts(db_factory, "t-b") == (0, 0, 0)
    assert _counts(db_factory, "t-keep") == (1, 1, 1)


# ---------------------------------------------------------------------------
# Deleting the session behind a run
# ---------------------------------------------------------------------------


def _add_run(db_factory, *, task_id: str, session_id: str, kind: str) -> None:
    db = db_factory()
    try:
        db.add(
            TaskSessionRow(
                id=f"run-{session_id}",
                user_id=OWNER,
                project_id="p1",
                task_id=task_id,
                session_id=session_id,
                agent_slug="worker",
                sequence=2,
                kind=kind,
                status="active",
            )
        )
        db.commit()
    finally:
        db.close()


def test_deleting_a_member_session_drops_its_run_index_row(db_factory) -> None:
    """The row is an INDEX of kernel sessions — one pointing at a deleted
    session is garbage that nothing else reconciles."""
    from valuz_agent.modules.tasks.purge import forget_session

    _seed(db_factory, task_id="t-member", status="active")
    _add_run(db_factory, task_id="t-member", session_id="member-1", kind="subtask")

    asyncio.run(forget_session(OWNER, "member-1"))

    db = db_factory()
    try:
        left = [r.session_id for r in db.query(TaskSessionRow).all() if r.task_id == "t-member"]
        assert "member-1" not in left
        assert "lead-t-member" in left, "only the deleted session's row goes"
    finally:
        db.close()


def test_deleting_the_lead_session_of_a_live_task_is_refused(db_factory) -> None:
    """Dropping that row is not a cleanup, it's a lobotomy.

    ``pick_lead_run`` would return None; recovery declines a task with no lead
    and the health monitor's "no lead run at all" branch deliberately does
    nothing — the task sits active forever with no actor and no way back.
    """
    import pytest

    from valuz_agent.modules.tasks.errors import TaskLeadSessionInUse
    from valuz_agent.modules.tasks.purge import forget_session

    _seed(db_factory, task_id="t-live", status="active")

    with pytest.raises(TaskLeadSessionInUse):
        asyncio.run(forget_session(OWNER, "lead-t-live"))

    db = db_factory()
    try:
        assert any(r.session_id == "lead-t-live" for r in db.query(TaskSessionRow).all())
    finally:
        db.close()


def test_a_stopped_task_still_owns_its_lead_because_resume_needs_it(db_factory) -> None:
    """``stopped`` and ``blocked`` are revivable — only completed/abandoned are
    terminal, so the guard has to key on that, not on "not active"."""
    import pytest

    from valuz_agent.modules.tasks.errors import TaskLeadSessionInUse
    from valuz_agent.modules.tasks.purge import forget_session

    _seed(db_factory, task_id="t-stopped", status="stopped")

    with pytest.raises(TaskLeadSessionInUse):
        asyncio.run(forget_session(OWNER, "lead-t-stopped"))


def test_the_lead_of_a_finished_task_can_be_deleted(db_factory) -> None:
    from valuz_agent.modules.tasks.purge import forget_session

    _seed(db_factory, task_id="t-done", status="completed")

    asyncio.run(forget_session(OWNER, "lead-t-done"))

    db = db_factory()
    try:
        assert not any(r.session_id == "lead-t-done" for r in db.query(TaskSessionRow).all())
    finally:
        db.close()


def test_forgetting_an_unrelated_session_is_a_no_op(db_factory) -> None:
    from valuz_agent.modules.tasks.purge import forget_session

    _seed(db_factory, task_id="t-x", status="active")
    asyncio.run(forget_session(OWNER, "some-plain-chat"))  # must not raise

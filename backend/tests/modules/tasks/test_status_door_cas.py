"""The task-status door is a CAS — TOCTOU writers and double commits lose.

Before this, update_task_status read the source with one SELECT, asserted the
transition, then UPDATEd with no status predicate: two writers who both read
``active`` both passed the assert and the last write won — a stop_task racing
an auto-finalize could persist the forbidden net transition stopped → blocked
and publish ``task.finalized`` twice with contradictory statuses. And
commit_task/abandon_task bypassed the door entirely (whole-row merge), so a
double commit produced two lead sessions.
"""

from __future__ import annotations

import asyncio

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks.datastore import TaskDatastore, TaskEventDatastore
from valuz_agent.modules.tasks.events import finalize_task
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow

OWNER = "local-test-owner"


def _seed(db_factory, status: str = "active") -> None:
    db = db_factory()
    try:
        db.add(
            TaskRow(
                id="t-door",
                user_id=OWNER,
                project_id="w1",
                file_path="tasks/t-door.md",
                title="door",
                goal="g",
                status=status,
                lead_agent_slug="lead",
                current_holder="lead",
            )
        )
        db.commit()
    finally:
        db.close()


def _status(db_factory) -> str:
    db = db_factory()
    try:
        return db.get(TaskRow, "t-door").status
    finally:
        db.close()


def test_expect_makes_the_flip_a_mutex(db_factory) -> None:
    """expect='draft': of two commit flips, exactly one wins (the second sees
    the row already active and gets False — no second lead is spawned)."""
    _seed(db_factory, status="draft")

    async def _run() -> tuple[bool, bool]:
        async with async_unit_of_work() as db:
            ds = TaskDatastore(db)
            first = await ds.update_task_status(OWNER, "t-door", "active", expect="draft")
            second = await ds.update_task_status(OWNER, "t-door", "active", expect="draft")
            return first, second

    first, second = asyncio.run(_run())
    assert first is True
    assert second is False, "the loser of the commit mutex must NOT proceed"
    assert _status(db_factory) == "active"


def test_stale_read_loses_the_row_level_race(db_factory) -> None:
    """The TOCTOU itself: a writer whose read went stale (row moved
    active → stopped underneath it) must not land its now-forbidden write."""
    _seed(db_factory, status="stopped")

    async def _run() -> bool:
        async with async_unit_of_work() as db:
            ds = TaskDatastore(db)

            async def _stale(_uid: str, _tid: str) -> str:
                return "active"  # what the racing writer read before losing

            ds._current_status = _stale  # type: ignore[method-assign]
            return await ds.update_task_status(OWNER, "t-door", "blocked")

    assert asyncio.run(_run()) is False
    assert _status(db_factory) == "stopped", "stopped → blocked must never persist"


def test_finalize_task_skips_announce_when_race_lost(db_factory) -> None:
    """A finalizer that loses the status race must not append its terminal
    event — the winner already recorded its own; two contradictory terminals
    on one task is the bug this closes."""
    _seed(db_factory, status="stopped")

    async def _run() -> object:
        async with async_unit_of_work() as db:
            calls = {"n": 0}
            orig = TaskDatastore._current_status

            async def _stale(self: TaskDatastore, uid: str, tid: str) -> str | None:
                calls["n"] += 1
                if calls["n"] == 1:
                    return "active"  # the stale pre-race read
                return await orig(self, uid, tid)  # the post-loss re-read is real

            # finalize_task builds its own TaskDatastore — patch at class level.
            TaskDatastore._current_status = _stale  # type: ignore[method-assign, assignment]
            try:
                return await finalize_task(
                    db,
                    user_id=OWNER,
                    project_id="w1",
                    task_id="t-door",
                    status="blocked",
                    event_type="task_failed",
                    actor="system",
                )
            finally:
                TaskDatastore._current_status = orig  # type: ignore[method-assign]

    assert asyncio.run(_run()) is None
    db = db_factory()
    try:
        rows = db.query(TaskEventRow).filter_by(task_id="t-door").all()
        assert rows == [], "no terminal event may ride a lost status race"
    finally:
        db.close()
    assert _status(db_factory) == "stopped"


def test_same_transition_concurrently_is_idempotent(db_factory) -> None:
    """Two writers performing the SAME legal transition: the loser's outcome
    is indistinguishable from success (no expect) — True, not an error."""
    _seed(db_factory, status="completed")

    async def _run() -> bool:
        async with async_unit_of_work() as db:
            ds = TaskDatastore(db)
            calls = {"n": 0}
            real = TaskDatastore._current_status

            async def _stale(uid: str, tid: str) -> str | None:
                calls["n"] += 1
                if calls["n"] == 1:
                    return "active"  # read before the winner landed "completed"
                return await real(ds, uid, tid)  # honest re-read after losing

            ds._current_status = _stale  # type: ignore[method-assign]
            return await ds.update_task_status(OWNER, "t-door", "completed")

    assert asyncio.run(_run()) is True


def test_event_ds_import_is_used() -> None:  # keep TaskEventDatastore import honest
    assert TaskEventDatastore is not None


def test_pick_lead_run_skips_commit_race_loser() -> None:
    """A rejected lead-kind row (commit-race loser) must never be 'the lead'
    while the winner's row exists — the watchdog would block a healthy task
    via the dead session's never-registered mailbox."""
    from types import SimpleNamespace

    from valuz_agent.modules.tasks.datastore import pick_lead_run

    loser = SimpleNamespace(kind="lead", session_id="loser", status="rejected", sequence=0)
    winner = SimpleNamespace(kind="lead", session_id="winner", status="active", sequence=0)
    member = SimpleNamespace(kind="subtask", session_id="m1", status="active", sequence=1)

    picked = pick_lead_run([loser, winner, member])  # loser first — rowid order
    assert picked is not None and picked.session_id == "winner"
    # Fallback: a lone rejected lead is still returned (legacy/terminal rows).
    picked = pick_lead_run([loser])
    assert picked is not None and picked.session_id == "loser"
    assert pick_lead_run([member]) is None

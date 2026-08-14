"""TaskHealthMonitor watchdog tests (task attention & reliability, P2).

Drives ``sweep_once`` directly against a tmp-SQLite fixture. Liveness is the
task's LEASE (``tasks/lease.py``) — shared state, so it answers for every host
process, unlike the process-local mailbox it replaced. The monitor only acts
after ``confirm_sweeps`` consecutive dead-looking passes, flipping the task
``active → blocked`` and emitting ``task_blocked(reason="lead_dead")``.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import select

from valuz_agent.modules.tasks.recovery import (
    TaskHealthConfig,
    TaskHealthMonitor,
)
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.infra.execution_lease import ExecutionLeaseRow
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow, TaskSessionRow

OWNER = "local-test-owner"
# A holder id that is not this process, so ``LeaseState.is_foreign`` holds.
PEER = "peer-host:999:deadbeef"




@pytest.fixture(autouse=True)
def _reset_mailbox():
    yield


def _seed(
    db_factory,
    *,
    task_id="t1",
    status="active",
    lead_session_id="lead-s",
    lease="expired",
) -> None:
    """Seed a task (+ lead run, + lease row).

    ``lease``: ``"expired"`` = a driver that died without releasing (the case
    the watchdog exists for), ``"live"`` = someone is driving it right now,
    ``"released"`` = a driver that exited cleanly, ``None`` = no lease row at
    all (a task nobody has claimed under this scheme).
    """
    db = db_factory()
    try:
        db.add(
            TaskRow(
                user_id=OWNER,
                id=task_id,
                project_id="w1",
                file_path="/tmp/t.md",
                title="T",
                goal="g",
                status=status,
                created_by="user",
                lead_agent_slug="lead",
                current_holder="lead",
                plan={"subtasks": []},
            )
        )
        if lead_session_id is not None:
            db.add(
                TaskSessionRow(
                    user_id=OWNER,
                    project_id="w1",
                    task_id=task_id,
                    session_id=lead_session_id,
                    agent_slug="lead",
                    sequence=0,
                    kind="lead",
                    status="active",
                )
            )
        if lease is not None:
            now = now_ms()
            expires = {
                "expired": now - 1_000,
                "live": now + 600_000,
                "released": 0,
            }[lease]
            db.add(
                ExecutionLeaseRow(
                    # Keyed by the LEAD SESSION now: every actor loop holds a
                    # lease on its own session, so the watchdog asks about the
                    # runner rather than about the task.
                    scope="actor",
                    key=lead_session_id or "lead-s",
                    holder_id=PEER,
                    note=task_id,
                    fence_token=1,
                    state="released" if lease == "released" else "held",
                    heartbeat_at=now,
                    lease_expires_at=expires,
                )
            )
        db.commit()
    finally:
        db.close()


def _task_status(db_factory, task_id="t1") -> str:
    db = db_factory()
    try:
        return db.execute(select(TaskRow.status).filter_by(id=task_id)).scalar_one()
    finally:
        db.close()


def _event_types(db_factory, task_id="t1") -> list[str]:
    db = db_factory()
    try:
        return [
            e.type
            for e in db.execute(
                select(TaskEventRow).filter_by(task_id=task_id).order_by(TaskEventRow.sequence)
            )
            .scalars()
            .all()
        ]
    finally:
        db.close()


def _monitor(revive: bool = False, attempts: list[str] | None = None) -> TaskHealthMonitor:
    """A monitor whose adoption attempt is scripted.

    Before blocking a task, the monitor now tries to take it over — boot
    recovery only runs once and stands down on a lease that has not expired
    yet, so a task orphaned by a hard kill has no other way back. ``revive``
    says whether that attempt succeeds; the default (False) is "nobody could
    adopt it", which is the state every blocking test here is about.
    """

    async def _recover(task_id: str, _project_id: str, _user_id: str) -> bool:
        if attempts is not None:
            attempts.append(task_id)
        return revive

    # confirm_sweeps=2 default; startup_delay irrelevant (we call sweep_once).
    return TaskHealthMonitor(TaskHealthConfig(), recover_one=_recover)


def test_live_lease_is_healthy(db_factory) -> None:
    _seed(db_factory, lease="live")
    mon = _monitor()
    for _ in range(4):  # well past confirm_sweeps
        assert asyncio.run(mon.sweep_once()) == []
    assert _task_status(db_factory) == "active"


def test_lease_held_by_another_process_is_never_blocked(db_factory) -> None:
    """Regression: the lead runs in a SIBLING process.

    The watchdog used to ask ``mailbox_registry.is_owned()``, which is
    process-local, so a lead driven by another worker/replica read as dead and
    its task was flipped to ``blocked`` mid-run while its conversation was
    still streaming. Nothing here registers a mailbox — that is the point: this
    process has no local trace of the driver, only the shared lease.
    """
    _seed(db_factory, lease="live")
    mon = _monitor()
    for _ in range(4):
        assert asyncio.run(mon.sweep_once()) == []
    assert _task_status(db_factory) == "active"
    assert "task_blocked" not in _event_types(db_factory)


def test_task_without_a_lease_row_is_left_alone(db_factory) -> None:
    """Absence is not death — this is what makes the change safe to roll out.

    A task active since before the lease table existed, or one still driven by
    a process on an older build, has no row. Blocking those would turn a
    gradual rollout into an outage.
    """
    _seed(db_factory, lease=None)
    mon = _monitor()
    for _ in range(4):
        assert asyncio.run(mon.sweep_once()) == []
    assert _task_status(db_factory) == "active"


def test_released_lease_is_dead(db_factory) -> None:
    """A driver that exited cleanly released its lease; the task is orphaned."""
    _seed(db_factory, lease="released")
    mon = _monitor()
    assert asyncio.run(mon.sweep_once()) == []
    assert asyncio.run(mon.sweep_once()) == ["t1"]
    assert _task_status(db_factory) == "blocked"


def test_dead_lead_needs_two_sweeps_before_blocking(db_factory) -> None:
    _seed(db_factory)  # lease expired → its holder died without releasing
    mon = _monitor()
    # First sweep: suspected, not yet acted.
    assert asyncio.run(mon.sweep_once()) == []
    assert _task_status(db_factory) == "active"
    # Second consecutive sweep: confirmed → blocked.
    assert asyncio.run(mon.sweep_once()) == ["t1"]
    assert _task_status(db_factory) == "blocked"
    assert "task_blocked" in _event_types(db_factory)


def _set_lease_expiry(db_factory, *, expires_at: int) -> None:
    """Move the lead's lease in or out of liveness, as a runner would."""
    db = db_factory()
    try:
        db.execute(
            ExecutionLeaseRow.__table__.update()
            .where(ExecutionLeaseRow.scope == "actor", ExecutionLeaseRow.key == "lead-s")
            .values(lease_expires_at=expires_at)
        )
        db.commit()
    finally:
        db.close()


def test_recovered_lead_clears_suspicion(db_factory) -> None:
    """Suspicion must reset when the lead comes back, and re-arm if it dies again.

    "Came back" used to mean a mailbox appearing in THIS process. It means a
    live lease now, which is the same statement made where every process can
    read it.
    """
    _seed(db_factory, lease="expired")
    mon = _monitor()
    assert asyncio.run(mon.sweep_once()) == []  # suspected once

    _set_lease_expiry(db_factory, expires_at=now_ms() + 600_000)  # a resume landed
    assert asyncio.run(mon.sweep_once()) == []  # suspicion cleared
    assert _task_status(db_factory) == "active"

    # A later death restarts the 2-sweep count from scratch.
    _set_lease_expiry(db_factory, expires_at=now_ms() - 1_000)
    assert asyncio.run(mon.sweep_once()) == []
    assert asyncio.run(mon.sweep_once()) == ["t1"]
    assert _task_status(db_factory) == "blocked"


def test_blocked_event_payload_reason_is_lead_dead(db_factory) -> None:
    _seed(db_factory)
    mon = _monitor()
    asyncio.run(mon.sweep_once())
    asyncio.run(mon.sweep_once())
    db = db_factory()
    try:
        ev = (
            db.execute(
                select(TaskEventRow).filter_by(task_id="t1", type="task_blocked")
            )
            .scalars()
            .one()
        )
        assert ev.payload["reason"] == "lead_dead"
    finally:
        db.close()


def test_task_with_no_lead_run_is_left_alone(db_factory) -> None:
    _seed(db_factory, lead_session_id=None)
    mon = _monitor()
    assert asyncio.run(mon.sweep_once()) == []
    assert asyncio.run(mon.sweep_once()) == []
    assert _task_status(db_factory) == "active"


def test_only_active_tasks_are_swept(db_factory) -> None:
    _seed(db_factory, task_id="paused-1", status="paused")
    mon = _monitor()
    asyncio.run(mon.sweep_once())
    asyncio.run(mon.sweep_once())
    assert _task_status(db_factory, "paused-1") == "paused"


def test_disabled_when_interval_zero() -> None:
    from datetime import timedelta

    cfg = TaskHealthConfig(interval=timedelta(seconds=0))
    assert cfg.enabled is False


def test_active_lead_bindings_is_one_query_and_skips_rejected_leads(db_factory) -> None:
    """The sweep runs every 60s forever, so it reads four columns in ONE query
    instead of a full-row scan plus a list_runs per task — and it must make the
    same pick as ``pick_lead_run``: a commit-race loser's rejected lead row
    never wins over the live one."""
    import asyncio

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks.datastore import TaskDatastore
    from valuz_agent.modules.tasks.models import TaskRow, TaskSessionRow

    db = db_factory()
    try:
        db.add(
            TaskRow(
                id="t-live",
                user_id=OWNER,
                project_id="w1",
                file_path="tasks/t-live.md",
                title="live",
                goal="g",
                status="active",
                lead_agent_slug="lead",
                current_holder="lead",
            )
        )
        db.add(
            TaskRow(
                id="t-done",
                user_id=OWNER,
                project_id="w1",
                file_path="tasks/t-done.md",
                title="done",
                goal="g",
                status="completed",
                lead_agent_slug="lead",
                current_holder="lead",
            )
        )
        # A commit-race loser sits alongside the winner, inserted FIRST.
        for rid, sid, status in (
            ("r-loser", "loser-sess", "rejected"),
            ("r-winner", "winner-sess", "active"),
        ):
            db.add(
                TaskSessionRow(
                    id=rid,
                    user_id=OWNER,
                    project_id="w1",
                    task_id="t-live",
                    session_id=sid,
                    agent_slug="lead",
                    sequence=0,
                    kind="lead",
                    status=status,
                )
            )
        db.commit()
    finally:
        db.close()

    async def _run() -> list[tuple]:
        async with async_unit_of_work(commit=False) as adb:
            return await TaskDatastore(adb).list_active_lead_bindings()

    bindings = asyncio.run(_run())
    assert [b[0] for b in bindings] == ["t-live"], "only ACTIVE tasks are swept"
    assert bindings[0][1:] == (OWNER, "w1", "winner-sess"), (
        "a rejected lead row must never be handed to the watchdog — it would "
        "flip a healthy task to blocked via a mailbox that never registers"
    )


def test_blocking_a_task_parks_the_lead_run_it_declared_dead(db_factory) -> None:
    """No terminal transition may leave a run row claiming its lead is running.

    ``stop_task`` and ``finish_task`` both settle it. Blocking did not — and a
    lead's loop leaves a halted task WITHOUT finalizing, because the terminal
    state belongs to whoever halted it, so nothing else ever would. A task
    blocked precisely BECAUSE its lead is gone, still indexed as having a
    running lead, is the reading the watchdog exists to correct.
    """
    _seed(db_factory, lease="expired")
    mon = _monitor()

    assert asyncio.run(mon.sweep_once()) == []  # suspected
    assert asyncio.run(mon.sweep_once()) == ["t1"]  # confirmed → blocked
    assert _task_status(db_factory) == "blocked"

    db = db_factory()
    try:
        run = (
            db.execute(select(TaskSessionRow).filter_by(session_id="lead-s")).scalars().first()
        )
        assert run is not None and run.status == "paused", (
            "the lead run must not still read 'active' on a task blocked for "
            f"having no lead (got {run.status if run else None})"
        )
        assert run.ended_at, "and it must carry an end time"
    finally:
        db.close()


def test_an_orphaned_task_is_adopted_instead_of_blocked(db_factory) -> None:
    """The hard-kill case boot recovery structurally cannot cover.

    ``recover_active_tasks`` runs once, at startup, and stands down on any task
    whose lead lease is still live. A process killed outright leaves its lease
    HELD for the rest of its 90s TTL; every fresh process boots inside that
    window, stands down, and by the time the lease expires the only sweep that
    would have adopted the task has already run. Observed on qa: a task killed
    mid-run by an ordinary deploy sat ``active`` behind a lease whose holder pod
    no longer existed, waiting for a human.
    """
    attempts: list[str] = []
    _seed(db_factory, lease="expired")
    mon = _monitor(revive=True, attempts=attempts)

    assert asyncio.run(mon.sweep_once()) == []  # suspected
    assert asyncio.run(mon.sweep_once()) == []  # confirmed → adopted, NOT blocked

    assert attempts == ["t1"], "the watchdog must have tried to take the task over"
    assert _task_status(db_factory) == "active", (
        "an adopted task keeps running — blocking it would hand a resumable "
        "task back to the user for no reason"
    )


def test_a_task_that_cannot_be_adopted_is_still_blocked(db_factory) -> None:
    """Adoption is an attempt, not a promise: blocking is what happens when it
    fails (a peer won the lease, the lead run is gone, the respawn threw)."""
    _seed(db_factory, lease="expired")
    mon = _monitor(revive=False)

    assert asyncio.run(mon.sweep_once()) == []
    assert asyncio.run(mon.sweep_once()) == ["t1"]
    assert _task_status(db_factory) == "blocked"


def test_adoption_is_attempted_once_per_task(db_factory) -> None:
    """A task whose lead dies again immediately must not be respawned forever.

    This is the failure mode a watchdog may not have: revive → die → revive,
    each round costing a real model turn. One attempt per task per process,
    then the task is blocked and it becomes the user's call.
    """
    attempts: list[str] = []
    _seed(db_factory, lease="expired")
    # Adoption "succeeds" every time, but the lease stays dead — exactly what a
    # lead that respawns and immediately exits again looks like from here.
    mon = _monitor(revive=True, attempts=attempts)

    for _ in range(6):
        asyncio.run(mon.sweep_once())

    assert attempts == ["t1"], f"expected exactly one attempt, got {attempts}"
    assert _task_status(db_factory) == "blocked", "and then it must give up and block"

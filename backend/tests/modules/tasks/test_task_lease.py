"""Task execution lease — the cross-process "one driver at a time" guard.

Two host processes are simulated by monkeypatching ``lease._HOLDER_ID``, which
is the only thing that distinguishes them: every other input (the database, the
clock) is genuinely shared, exactly as it is in a real multi-worker deployment.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from sqlalchemy import select

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.infra import execution_lease as lease_mod
from valuz_agent.modules.tasks.lease import (
    acquire_task_lease,
    is_driven_elsewhere,
    load_task_lease_states,
)
from valuz_agent.infra.execution_lease import ExecutionLeaseRow

OWNER = "local-test-owner"
TASK = "t1"


@pytest.fixture(autouse=True)
def _multi_process_world(monkeypatch):
    """Pin the world these tests are about: several processes, real leases.

    ``_exclusive_by_construction`` is ambient — it answers True whenever the
    single-writer lock happens to be held in this interpreter, and any test in
    the session that boots the app leaves it held (``skills/test_staging_api``
    does, and sorts before this file). Leases would then need no renewal, and
    every fencing assertion below would be testing the desktop path instead.
    """
    monkeypatch.setattr(lease_mod, "_exclusive_by_construction", lambda: False)


@pytest.fixture
def as_process(monkeypatch):
    """Run a coroutine as if it were a given host process."""

    def _run(holder: str, coro_factory):
        monkeypatch.setattr(lease_mod, "_HOLDER_ID", holder)
        return asyncio.run(coro_factory())

    return _run


def _acquire(session_id: str = "lead-s"):
    return lambda: acquire_task_lease(user_id=OWNER, task_id=TASK, lead_session_id=session_id)


def _row(db_factory) -> ExecutionLeaseRow:
    db = db_factory()
    try:
        return (
            db.execute(select(ExecutionLeaseRow).filter_by(scope="task", key=TASK)).scalars().one()
        )
    finally:
        db.close()


def test_first_acquisition_creates_the_row(db_factory, as_process) -> None:
    lease = as_process("proc-a", _acquire())
    assert lease is not None
    assert lease.fence_token == 1
    row = _row(db_factory)
    assert row.holder_id == "proc-a"
    assert row.state == "held"
    assert row.lease_expires_at > now_ms()


def test_second_process_is_refused_while_the_lease_is_live(db_factory, as_process) -> None:
    assert as_process("proc-a", _acquire()) is not None
    # THE invariant: a peer must not start a second driver on a live task.
    assert as_process("proc-b", _acquire()) is None
    assert _row(db_factory).holder_id == "proc-a"


def test_expired_lease_is_taken_over_and_fenced(db_factory, as_process) -> None:
    first = as_process("proc-a", _acquire())
    assert first is not None

    db = db_factory()
    try:
        db.execute(
            ExecutionLeaseRow.__table__.update()
            .where(ExecutionLeaseRow.key == TASK)
            .values(lease_expires_at=now_ms() - 1)
        )
        db.commit()
    finally:
        db.close()

    second = as_process("proc-b", _acquire())
    assert second is not None
    # Takeover bumps the token, which is what tells the old holder to stop.
    assert second.fence_token > first.fence_token
    assert _row(db_factory).holder_id == "proc-b"

    # The evicted holder's renewal must fail — it no longer owns the task.
    assert as_process("proc-a", first.renew) is False


def test_released_lease_is_free_for_the_taking(db_factory, as_process) -> None:
    first = as_process("proc-a", _acquire())
    assert first is not None
    as_process("proc-a", first.release)
    assert _row(db_factory).state == "released"

    assert as_process("proc-b", _acquire()) is not None
    assert _row(db_factory).holder_id == "proc-b"


def test_reacquiring_in_the_same_process_fences_the_earlier_loop(db_factory, as_process) -> None:
    """Mirrors ``mailbox_registry.claim``: a later acquisition wins.

    A resume can spawn a second loop on the same session while the first is
    still unwinding. The newer one must drive and the older one must find out,
    which is exactly what bumping the token on every acquisition buys.
    """
    first = as_process("proc-a", _acquire())
    second = as_process("proc-a", _acquire())
    assert first is not None and second is not None
    assert second.fence_token > first.fence_token
    assert as_process("proc-a", first.renew) is False
    assert as_process("proc-a", second.renew) is True


def test_stale_holder_cannot_release_the_new_drivers_lease(db_factory, as_process) -> None:
    first = as_process("proc-a", _acquire())
    second = as_process("proc-a", _acquire())
    assert first is not None and second is not None
    as_process("proc-a", first.release)  # fenced → must be a no-op
    assert _row(db_factory).state == "held"
    assert as_process("proc-a", second.renew) is True


def test_renew_extends_the_expiry(db_factory, as_process) -> None:
    lease = as_process("proc-a", _acquire())
    assert lease is not None
    before = _row(db_factory).lease_expires_at
    db = db_factory()
    try:
        db.execute(
            ExecutionLeaseRow.__table__.update()
            .where(ExecutionLeaseRow.key == TASK)
            .values(lease_expires_at=before - 30_000)
        )
        db.commit()
    finally:
        db.close()
    assert as_process("proc-a", lease.renew) is True
    assert _row(db_factory).lease_expires_at >= before


def test_is_driven_elsewhere_ignores_our_own_lease(db_factory, as_process) -> None:
    assert as_process("proc-a", _acquire()) is not None
    # Our own live lease is not "elsewhere" — otherwise boot recovery would
    # refuse to re-drive the tasks this very process is responsible for.
    assert as_process("proc-a", lambda: is_driven_elsewhere(TASK)) is False
    assert as_process("proc-b", lambda: is_driven_elsewhere(TASK)) is True


def test_absent_lease_is_unknown_not_dead(db_factory, as_process) -> None:
    states = as_process("proc-a", lambda: load_task_lease_states(["nope"]))
    assert states == {}
    assert as_process("proc-a", lambda: is_driven_elsewhere("nope")) is False


# ---------------------------------------------------------------------------
# Self-fence: a holder that cannot PROVE it still holds must stand down.
# ---------------------------------------------------------------------------


def test_transient_renew_failure_does_not_evict(db_factory, as_process, monkeypatch) -> None:
    """One failed renewal is a blip, not eviction — the TTL spans several."""
    lease = as_process("proc-a", _acquire())
    assert lease is not None

    def _boom(*a, **k):
        raise RuntimeError("db blip")

    monkeypatch.setattr(lease_mod, "async_unit_of_work", _boom)
    assert as_process("proc-a", lease.renew) is True


def test_persistent_renew_failure_stands_the_holder_down(
    db_factory, as_process, monkeypatch
) -> None:
    """Regression-by-design: a holder whose DB is unreachable must stop.

    Silently retrying forever is the dangerous option — the lease expires while
    the holder keeps working, a peer's watchdog is then free to hand the key on,
    and two processes drive one task. Standing down converts that into "no
    holder", which every consumer already recovers from.
    """
    lease = as_process("proc-a", _acquire())
    assert lease is not None
    # Backdate the last successful renewal past the self-fence threshold.
    lease._last_renewed_at = now_ms() - lease_mod.LEASE_TTL_MS

    def _boom(*a, **k):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(lease_mod, "async_unit_of_work", _boom)
    assert as_process("proc-a", lease.renew) is False


# ---------------------------------------------------------------------------
# Single-process deployments pay nothing.
# ---------------------------------------------------------------------------


def test_proven_exclusivity_skips_renewal_entirely(db_factory, as_process, monkeypatch) -> None:
    """Desktop shape: SQLite + the single-writer flock already prove exclusivity.

    Renewing there would be a disk write every interval per active task, bought
    on a laptop to solve a problem a single process cannot have.
    """
    monkeypatch.setattr(lease_mod, "_exclusive_by_construction", lambda: True)
    lease = as_process("proc-a", _acquire())
    assert lease is not None
    assert lease._renewal_required is False
    assert _row(db_factory).lease_expires_at > now_ms() + 10**12  # effectively never

    # renew() must not touch the database at all on this path.
    def _boom(*a, **k):
        raise AssertionError("renewal must not run when exclusivity is proven")

    monkeypatch.setattr(lease_mod, "async_unit_of_work", _boom)
    assert as_process("proc-a", lease.renew) is True


def test_exclusivity_needs_BOTH_sqlite_and_the_held_lock(monkeypatch) -> None:
    """Postgres, or SQLite with the lock skipped, must fall through to real leases.

    Opts OUT of ``_multi_process_world``: this is the one test that exercises
    the probe itself rather than the world it reports.
    """
    from valuz_agent.infra import single_writer

    monkeypatch.undo()  # drop the autouse stub over the function under test

    monkeypatch.setattr(lease_mod, "_HOLDER_ID", "proc-a")
    monkeypatch.setattr("valuz_agent.infra.db_urls.is_sqlite_runtime", lambda: False)
    monkeypatch.setattr(single_writer, "is_lock_held", lambda: True)
    assert lease_mod._exclusive_by_construction() is False

    monkeypatch.setattr("valuz_agent.infra.db_urls.is_sqlite_runtime", lambda: True)
    monkeypatch.setattr(single_writer, "is_lock_held", lambda: False)
    assert lease_mod._exclusive_by_construction() is False

    monkeypatch.setattr(single_writer, "is_lock_held", lambda: True)
    assert lease_mod._exclusive_by_construction() is True

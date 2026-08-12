"""Manual check: two REAL OS processes, one shared database.

Run it as::

    RUN=/tmp/lease-check && mkdir -p $RUN/data
    export E2E_DB=$RUN/valuz.db E2E_DATA_DIR=$RUN/data PYTHONPATH=$PWD
    uv run python scripts/verify_task_lease_multiprocess.py seed
    uv run python scripts/verify_task_lease_multiprocess.py driver &
    sleep 6
    uv run python scripts/verify_task_lease_multiprocess.py watchdog

Expected: ``E2E_RESULT status=active blocked=no``. Before the task lease it
printed ``status=blocked``, with the driver's turn still running.

This lives outside pytest on purpose: the defect it covers only exists ACROSS
processes, so a single-interpreter test cannot express it — the mailbox
registry is per-process memory, and any in-process "second worker" shares it.

Two REAL OS processes, one shared database — the production failure shape.

The unit tests fake a second process by patching a holder id. This does not:
``driver`` and ``watchdog`` are separate interpreters with separate memory, so
the process-local mailbox registry genuinely cannot be seen across them, exactly
as with `uvicorn --workers N`.

  driver   — seeds an active task and runs a REAL `ActorRunner.run_actor_loop`
             as its lead, with the turn primitive replaced by a sleep (a long
             model turn). Nothing else is faked: same loop, same mailbox, and on
             the fixed build the same lease acquisition + renewal.
  watchdog — runs the REAL `TaskHealthMonitor.sweep_once` against the same
             database and reports the moment the task leaves `active`.

Runs unchanged on both builds; it imports nothing that only exists on one.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

DB = os.environ["E2E_DB"]
DATA_DIR = os.environ["E2E_DATA_DIR"]
os.environ["VALUZ_DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["VALUZ_DATA_DIR"] = DATA_DIR
os.environ["VALUZ_INITIALIZE_USER_CONTENT_ON_STARTUP"] = "false"

OWNER = "e2e-owner"
TASK = "task-e2e"
LEAD = "lead-e2e"
TURN_SECONDS = float(os.environ.get("E2E_TURN_SECONDS", "20"))


def _stamp(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def seed() -> None:
    from valuz_agent.boot.schema import run_host_migrations

    run_host_migrations()

    import sqlite3

    now = int(time.time() * 1000)
    c = sqlite3.connect(DB)
    c.execute(
        "INSERT INTO valuz_task (id, user_id, project_id, file_path, title, goal,"
        " status, created_by, lead_agent_slug, current_holder, metadata, plan,"
        " plan_version, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (TASK, OWNER, "proj", "tasks/t.md", "e2e", "goal", "active", "user",
         "lead", "lead", "{}", "{}", 0, now, now),
    )
    c.execute(
        "INSERT INTO valuz_task_session (id, user_id, project_id, task_id, session_id,"
        " agent_slug, sequence, kind, status, project_mode, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("run1", OWNER, "proj", TASK, LEAD, "lead", 0, "lead", "active", "shared", now, now),
    )
    c.commit()
    c.close()
    _stamp(f"seeded task={TASK} status=active lead={LEAD}")


class _Fakes:
    """Minimal ActorFinalizer + ActorCoordinator. Only the turn is faked."""

    async def finalize_actor(self, **kwargs: Any) -> None:
        _stamp("driver: finalize_actor called")

    async def notify_lead_member_idle(self, session_id, status, user_id) -> None:
        return None

    async def lead_idle_with_no_pending(self, task_id, project_id, user_id, lead_session_id="") -> bool:
        return False  # keep the loop alive on its mailbox after the turn

    async def session_still_working(self, session_id) -> bool:
        return True


def _maybe_shorten_lease() -> None:
    """Shrink the lease TTL for scenarios that must WAIT OUT an expiry."""
    ttl = os.environ.get("E2E_TTL_MS")
    if not ttl:
        return
    try:
        from valuz_agent.modules.tasks import actor_runner as ar
        from valuz_agent.modules.tasks import lease as lease_mod
    except ImportError:
        return  # baseline build has no lease module
    lease_mod.TASK_LEASE_TTL_MS = int(ttl)
    renew = float(os.environ.get("E2E_RENEW_S", "1"))
    lease_mod.TASK_LEASE_RENEW_INTERVAL_S = renew
    ar.TASK_LEASE_RENEW_INTERVAL_S = renew
    _stamp(f"driver: lease TTL={ttl}ms renew={renew}s")


def driver() -> None:
    from valuz_agent.modules.tasks.actor_runner import ActorRunner

    _maybe_shorten_lease()

    fakes = _Fakes()
    runner = ActorRunner(finalizer=fakes, coordinator=fakes)

    async def _turn(session_id: str, content: str, user_id: str | None = None) -> str:
        _stamp(f"driver: turn START (will run {TURN_SECONDS}s — a long model turn)")
        await asyncio.sleep(TURN_SECONDS)
        _stamp("driver: turn END")
        return "idle"

    runner.run_turn = _turn  # type: ignore[method-assign]

    async def _main() -> None:
        _stamp("driver: entering run_actor_loop as LEAD")
        try:
            await asyncio.wait_for(
                runner.run_actor_loop(
                    session_id=LEAD,
                    initial_prompt="go",
                    role="lead",
                    task_id=TASK,
                    project_id="proj",
                    user_id=OWNER,
                    idle_ttl=TURN_SECONDS + 30,
                ),
                timeout=TURN_SECONDS + 25,
            )
        except TimeoutError:
            _stamp("driver: harness timeout — loop was still alive (expected)")

    asyncio.run(_main())
    _stamp("driver: exit")


def watchdog() -> None:
    from datetime import timedelta

    from valuz_agent.modules.tasks.recovery import TaskHealthConfig, TaskHealthMonitor

    # 1s sweeps instead of 60s: same code path, same confirm_sweeps=2, but the
    # verdict lands in seconds instead of minutes.
    mon = TaskHealthMonitor(TaskHealthConfig(interval=timedelta(seconds=1)))

    async def _main() -> None:
        import sqlite3

        deadline = time.time() + TURN_SECONDS + 10
        blocked_at = None
        sweeps = 0
        while time.time() < deadline:
            acted = await mon.sweep_once()
            sweeps += 1
            if acted:
                blocked_at = time.time()
                _stamp(f"watchdog: BLOCKED {acted} after {sweeps} sweeps")
                break
            await asyncio.sleep(1)

        c = sqlite3.connect(DB)
        status = c.execute("SELECT status FROM valuz_task WHERE id=?", (TASK,)).fetchone()[0]
        c.close()
        _stamp(f"watchdog: {sweeps} sweeps, final task status = {status!r}")
        print(f"E2E_RESULT status={status} blocked={'yes' if blocked_at else 'no'}", flush=True)

    asyncio.run(_main())


if __name__ == "__main__":
    {"seed": seed, "driver": driver, "watchdog": watchdog}[sys.argv[1]]()

"""Actor loops drain cleanly at shutdown.

治本 for the restart race: once the process starts shutting down
(``infra.lifecycle.set_draining``), the long-lived task **actor loops** must
stop starting new turns AND skip their ``_finalize_actor`` — leaving in-flight
sessions ``running`` / tasks ``active`` for boot recovery to resume, instead of
racing the teardown of the kernel store + host DB (which spammed
``Dependencies not initialized`` and would wrongly mark tasks terminal).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.infra import lifecycle
from valuz_agent.modules.sessions.turn_driver import run_session_to_idle
from valuz_agent.modules.tasks import actor_runner
from valuz_agent.modules.tasks.actor_runner import ActorRunner

LOCAL_USER_ID = "local-test-owner"


@pytest.fixture(autouse=True)
def _clear_draining():
    lifecycle.reset_draining()
    yield
    lifecycle.reset_draining()


def _as_async(fn: Any) -> Any:
    async def _f(*a: Any, **k: Any) -> Any:
        return fn(*a, **k)

    return _f


class _Bus:
    def publish(self, *a: Any, **k: Any) -> None:
        pass


# ── infra.lifecycle ──────────────────────────────────────────────────────


def test_draining_flag_roundtrip() -> None:
    assert lifecycle.is_draining() is False
    lifecycle.set_draining()
    assert lifecycle.is_draining() is True
    lifecycle.reset_draining()
    assert lifecycle.is_draining() is False


# ── ActorRunner.run_actor_loop ───────────────────────────────────────────


class _RecordingCollaborators:
    """Fake ``ActorFinalizer`` + ``ActorCoordinator`` in one object.

    The loop only ever needs one instance of each and these tests care about
    *whether* a seam fired, not which object owns it — so one fake satisfies
    both protocols and appends to a shared call log.
    """

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    async def finalize_actor(self, **kwargs: Any) -> None:
        self._calls.append("finalize")

    async def notify_lead_member_idle(
        self, session_id: str, status: str, user_id: str
    ) -> None:
        return None

    async def lead_idle_with_no_pending(
        self, task_id: str, project_id: str, user_id: str, lead_session_id: str = ""
    ) -> bool:
        return True

    async def session_still_working(self, session_id: str) -> bool:
        return False


def _runner_recording(calls: list[str], turn_status: str) -> ActorRunner:
    """An ActorRunner whose turn primitive records a call and returns *turn_status*."""
    fake = _RecordingCollaborators(calls)
    runner = ActorRunner(finalizer=fake, coordinator=fake)

    async def _turn(session_id: str, content: str, user_id: str | None = None) -> str:
        calls.append("turn")
        return turn_status

    runner.run_turn = _turn  # type: ignore[method-assign]
    return runner


def test_actor_loop_draining_skips_turn_and_finalize(db_factory) -> None:
    calls: list[str] = []
    runner = _runner_recording(calls, "idle")
    lifecycle.set_draining()
    asyncio.run(
        runner.run_actor_loop(
            session_id="s1",
            initial_prompt="hi",
            role="lead",
            task_id="t1",
            project_id="p1",
            user_id=LOCAL_USER_ID,
        )
    )

    # Draining: the loop breaks at the top — no turn ran, and the whole
    # finalize was skipped (session left for boot recovery).
    assert "turn" not in calls
    assert "finalize" not in calls


def test_actor_loop_runs_normally_when_not_draining(db_factory) -> None:
    calls: list[str] = []
    # terminal status → the loop exits after a single turn
    runner = _runner_recording(calls, "terminated")
    asyncio.run(
        runner.run_actor_loop(
            session_id="s2",
            initial_prompt="hi",
            role="lead",
            task_id="t2",
            project_id="p2",
            user_id=LOCAL_USER_ID,
        )
    )

    # Not draining: the turn runs and finalize happens as usual.
    assert calls == ["turn", "finalize"]


# ── run_session_to_idle ──────────────────────────────────────────────────


def test_run_session_to_idle_draining_skips_finalize(monkeypatch: pytest.MonkeyPatch) -> None:
    finalize_calls: list[str] = []
    sess = SimpleNamespace(status="idle", metadata={})
    monkeypatch.setattr(actor_runner.kernel_client, "get_session", _as_async(lambda *_: sess))
    monkeypatch.setattr(
        actor_runner.kernel_client,
        "run_turn",
        _as_async(lambda *a, **k: SimpleNamespace(id="m", input_tokens=None, output_tokens=None)),
    )
    import valuz_agent.modules.sessions.run_orchestrator as run_orch

    monkeypatch.setattr(
        run_orch, "_finalize_session", _as_async(lambda *a, **k: finalize_calls.append("f"))
    )

    lifecycle.set_draining()
    asyncio.run(run_session_to_idle("s3", "hi", _Bus(), user_id=LOCAL_USER_ID))

    assert finalize_calls == []  # finalize skipped while draining


# ---------------------------------------------------------------------------
# idle-TTL vs background work (the "task blocked while still running" bug)
# ---------------------------------------------------------------------------


class _BgAwareCollaborators(_RecordingCollaborators):
    """Fake collaborators that report the session as still doing background work
    for the first ``busy_for`` probes, then idle."""

    def __init__(self, calls: list[str], busy_for: int) -> None:
        super().__init__(calls)
        self._busy_left = busy_for
        self.probes = 0

    async def lead_idle_with_no_pending(
        self, task_id: str, project_id: str, user_id: str, lead_session_id: str = ""
    ) -> bool:
        # Matches the real case: the plan still had unresolved nodes, so the
        # lead did NOT take the fast exit and parked on its mailbox instead.
        return False

    async def session_still_working(self, session_id: str) -> bool:
        self.probes += 1
        if self._busy_left > 0:
            self._busy_left -= 1
            return True
        return False


def _bg_runner(calls: list[str], fake: _BgAwareCollaborators) -> ActorRunner:
    runner = ActorRunner(finalizer=fake, coordinator=fake)

    async def _turn(session_id: str, content: str, user_id: str | None = None) -> str:
        calls.append("turn")
        return "idle"

    runner.run_turn = _turn  # type: ignore[method-assign]
    return runner


def test_idle_ttl_does_not_reap_a_session_with_background_work(db_factory) -> None:
    """Regression for a real task that was closed while still working.

    A lead spawned two ``run_in_background`` subagents. Its own turn ended, so
    the loop parked on the mailbox — but the CLI kept driving follow-up turns
    on the session as the subagents reported in, and the loop saw none of it.
    Its TTL clock, started at the FIRST turn, expired 30 minutes later and the
    task was finalized ``blocked`` while the work was still running.

    The TTL now only means "our mailbox was quiet"; whether the ACTOR is done
    is a separate question, and the loop asks it before finalizing.
    """
    calls: list[str] = []
    fake = _BgAwareCollaborators(calls, busy_for=2)
    runner = _bg_runner(calls, fake)

    asyncio.run(
        runner.run_actor_loop(
            session_id="lead-bg",
            initial_prompt="go",
            role="lead",
            task_id="t-bg",
            project_id="p1",
            idle_ttl=0.01,
            user_id=LOCAL_USER_ID,
        )
    )
    # Probed on each expiry: extended twice while background work was live,
    # then finalized once the session really was idle.
    assert fake.probes == 3
    assert calls.count("finalize") == 1


def test_idle_ttl_extension_is_bounded(db_factory) -> None:
    """A session wedged ``running`` forever must not pin the loop forever."""
    from valuz_agent.modules.tasks.actor_runner import MAX_IDLE_EXTENSIONS

    calls: list[str] = []
    fake = _BgAwareCollaborators(calls, busy_for=10_000)  # never goes idle
    runner = _bg_runner(calls, fake)

    asyncio.run(
        runner.run_actor_loop(
            session_id="lead-wedged",
            initial_prompt="go",
            role="lead",
            task_id="t-wedged",
            project_id="p1",
            idle_ttl=0.01,
            user_id=LOCAL_USER_ID,
        )
    )
    # Extended up to the cap, then gave up and finalized rather than looping on.
    assert fake.probes == MAX_IDLE_EXTENSIONS
    assert calls.count("finalize") == 1

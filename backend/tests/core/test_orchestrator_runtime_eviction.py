"""Warm-runtime eviction — the leak fix for accumulating claude/codex subprocesses.

Each cached runtime in ``SessionOrchestrator._runtimes`` owns a live CLI
subprocess for the life of the cache entry. Before this change the cache was
unbounded and only ``cleanup(session_id)`` (which nothing called for ordinary
chat sessions) released it, so every session touched leaked one OS process
until host exit. These tests pin the two bounding policies and the teardown:

* LRU cap — at most ``max_warm_runtimes`` warm runtimes; the least-recently-used
  is closed when a new one pushes over the ceiling.
* Idle TTL — a runtime untouched longer than the TTL is closed (here driven via
  the lazy sweep that runs on every ``_ensure_runtime``).
* Active turns are never evicted (covers a parked approval — the session stays
  in ``_active`` while ``run()`` awaits).
* ``shutdown()`` closes every runtime (deterministic process teardown).
* The ``DELETE /sessions/{id}`` route tears the runtime down (Layer 1 — also
  what makes the memory-curator ephemeral session leak-free).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app

import time

import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from src.core.orchestrator import SessionOrchestrator


class FakeRuntime:
    """Minimal RuntimePort stand-in: records close()/interrupt() and the sink."""

    def __init__(self) -> None:
        self.closed = False
        self.interrupted = False
        self.sink = None
        # Mirrors ClaudeAgentRuntime.has_live_background_tasks (duck-typed by
        # the orchestrator's eviction policies). Plain attribute on the fake.
        self.has_live_background_tasks = False

    @property
    def approval_rule_matcher(self) -> object:
        return object()  # only stashed into the rule-finder closure; never run

    def update_sink(self, sink: object) -> None:
        self.sink = sink

    async def prepare(self, session: object) -> None:
        del session

    def set_session_rule_finder(self, finder: object) -> None:  # pragma: no cover
        pass

    async def interrupt(self) -> None:
        self.interrupted = True

    async def close(self) -> None:
        self.closed = True


def _patch_factory(monkeypatch) -> None:
    """Make ``_ensure_runtime`` mint a ``FakeRuntime`` instead of a real one."""
    async def no_egress(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "src.runtimes.factory.create_runtime",
        lambda *a, **k: FakeRuntime(),
    )
    # These tests intentionally pass bare objects for agent/session because
    # they exercise only cache eviction.  Keep the independent network-egress
    # admission/registration boundary out of this unit-test fixture.
    monkeypatch.setattr(
        "src.runtimes.network_egress.prepare_runtime_egress",
        no_egress,
    )


async def _ensure(orch: SessionOrchestrator, sid: str) -> FakeRuntime:
    rt = await orch._ensure_runtime(sid, object(), object(), object(), "/tmp")
    assert isinstance(rt, FakeRuntime)
    return rt


async def test_lru_cap_evicts_least_recently_used(monkeypatch) -> None:
    _patch_factory(monkeypatch)
    # TTL disabled so only the cap is exercised; cap of 2.
    orch = SessionOrchestrator(object(), max_warm_runtimes=2, runtime_idle_ttl_s=0)

    r1 = await _ensure(orch, "s1")
    r2 = await _ensure(orch, "s2")
    # Pin a deterministic recency order (s1 older than s2).
    orch._runtime_last_used["s1"] = 1.0
    orch._runtime_last_used["s2"] = 2.0

    r3 = await _ensure(orch, "s3")  # over cap → evict LRU (s1)

    assert r1.closed is True
    assert r2.closed is False and r3.closed is False
    assert set(orch._runtimes) == {"s2", "s3"}
    assert "s1" not in orch._runtime_last_used


async def test_network_reconfigure_candidates_keep_explicit_owner(monkeypatch) -> None:
    _patch_factory(monkeypatch)
    orch = SessionOrchestrator(object(), max_warm_runtimes=3, runtime_idle_ttl_s=0)

    first = await orch._ensure_runtime(
        "s1", object(), object(), object(), "/tmp", user_id="owner-1"
    )
    second = await orch._ensure_runtime(
        "s2", object(), object(), object(), "/tmp", user_id="owner-2"
    )
    orch._runtime_last_used["s1"] = 1.0
    orch._runtime_last_used["s2"] = 2.0

    assert orch.warm_runtime_candidates(limit=1) == [("owner-2", "s2")]
    await orch.evict_all_warm_runtimes()
    assert first.closed is True
    assert second.closed is True
    assert orch.warm_runtime_candidates(limit=1) == []


async def test_idle_ttl_sweep_closes_stale_runtime(monkeypatch) -> None:
    _patch_factory(monkeypatch)
    # Cap effectively off; short TTL.
    orch = SessionOrchestrator(object(), max_warm_runtimes=100, runtime_idle_ttl_s=1000.0)

    r1 = await _ensure(orch, "s1")
    # Make s1 look untouched for longer than the TTL.
    orch._runtime_last_used["s1"] = time.monotonic() - 5000.0

    r2 = await _ensure(orch, "s2")  # lazy sweep on entry evicts the stale s1

    assert r1.closed is True
    assert r2.closed is False
    assert set(orch._runtimes) == {"s2"}


async def test_active_session_is_never_evicted(monkeypatch) -> None:
    _patch_factory(monkeypatch)
    orch = SessionOrchestrator(object(), max_warm_runtimes=1, runtime_idle_ttl_s=1000.0)

    r1 = await _ensure(orch, "s1")
    # Simulate an in-flight turn (also the parked-approval case) and make it
    # look both stale and over-cap.
    orch._active["s1"] = r1
    orch._runtime_last_used["s1"] = time.monotonic() - 5000.0

    r2 = await _ensure(orch, "s2")  # cap=1 exceeded AND s1 stale, but s1 active

    assert r1.closed is False  # active turn protected
    assert set(orch._runtimes) == {"s1", "s2"}  # cap briefly exceeded by design
    assert r2.closed is False


async def test_bg_busy_runtime_survives_normal_idle_ttl(monkeypatch) -> None:
    """A runtime with a live run_in_background process must not be closed by
    the normal idle TTL — the background process is a child of the CLI
    subprocess and would die with it, losing the user's work mid-task."""
    _patch_factory(monkeypatch)
    orch = SessionOrchestrator(
        object(),
        max_warm_runtimes=100,
        runtime_idle_ttl_s=1000.0,
        bg_busy_runtime_ttl_s=1_000_000.0,
    )

    r1 = await _ensure(orch, "s1")
    r1.has_live_background_tasks = True
    orch._runtime_last_used["s1"] = time.monotonic() - 5000.0  # past normal TTL

    r2 = await _ensure(orch, "s2")  # lazy sweep runs

    assert r1.closed is False  # protected by the extended TTL
    assert r2.closed is False
    assert set(orch._runtimes) == {"s1", "s2"}


async def test_bg_busy_runtime_still_evicted_past_extended_ttl(monkeypatch) -> None:
    """The busy signal EXTENDS the TTL, it doesn't exempt forever — a crashed
    CLI can leave the flag stuck, and the extended TTL is the backstop."""
    _patch_factory(monkeypatch)
    orch = SessionOrchestrator(
        object(),
        max_warm_runtimes=100,
        runtime_idle_ttl_s=1000.0,
        bg_busy_runtime_ttl_s=2000.0,
    )

    r1 = await _ensure(orch, "s1")
    r1.has_live_background_tasks = True
    orch._runtime_last_used["s1"] = time.monotonic() - 5000.0  # past BOTH TTLs

    await _ensure(orch, "s2")

    assert r1.closed is True


async def test_bg_busy_ttl_zero_means_full_exemption(monkeypatch) -> None:
    _patch_factory(monkeypatch)
    orch = SessionOrchestrator(
        object(),
        max_warm_runtimes=100,
        runtime_idle_ttl_s=1000.0,
        bg_busy_runtime_ttl_s=0,
    )

    r1 = await _ensure(orch, "s1")
    r1.has_live_background_tasks = True
    orch._runtime_last_used["s1"] = time.monotonic() - 10_000_000.0

    await _ensure(orch, "s2")

    assert r1.closed is False


async def test_lru_cap_skips_bg_busy_runtime(monkeypatch) -> None:
    """The LRU cap prefers truly idle runtimes; a bg-busy one is skipped even
    as the LRU candidate, briefly exceeding the cap (same principle as active
    turns)."""
    _patch_factory(monkeypatch)
    orch = SessionOrchestrator(object(), max_warm_runtimes=1, runtime_idle_ttl_s=0)

    r1 = await _ensure(orch, "s1")
    r1.has_live_background_tasks = True
    orch._runtime_last_used["s1"] = 1.0  # the LRU candidate

    r2 = await _ensure(orch, "s2")  # over cap, but s1 is protected

    assert r1.closed is False
    assert set(orch._runtimes) == {"s1", "s2"}  # cap briefly exceeded by design
    assert r2.closed is False


async def test_shutdown_closes_all_runtimes(monkeypatch) -> None:
    _patch_factory(monkeypatch)
    orch = SessionOrchestrator(object())
    orch.start()  # background sweeper (cancelled by shutdown)

    r1 = await _ensure(orch, "s1")
    r2 = await _ensure(orch, "s2")

    await orch.shutdown()

    assert r1.closed is True and r2.closed is True
    assert orch._runtimes == {}
    assert orch._sweeper_task is None


async def test_cleanup_drops_last_used_and_closes(monkeypatch) -> None:
    _patch_factory(monkeypatch)
    orch = SessionOrchestrator(object())

    r1 = await _ensure(orch, "s1")
    await orch.cleanup("s1")

    assert r1.closed is True
    assert "s1" not in orch._runtimes
    assert "s1" not in orch._runtime_last_used


async def test_delete_route_tears_down_runtime(monkeypatch) -> None:
    """Layer 1: DELETE /sessions/{id} closes the cached runtime (covers the
    memory-curator ephemeral-session path, which deletes after one turn)."""
    _patch_factory(monkeypatch)
    from app.routes.sessions import delete_session

    class FakeStore:
        async def delete_session(self, owner: str, session_id: str) -> bool:
            return True

    orch = SessionOrchestrator(object())
    r1 = await _ensure(orch, "ephem-1")

    out = await delete_session("ephem-1", FakeStore(), "owner-1", orch)

    assert out == {"data": None}
    assert r1.closed is True
    assert "ephem-1" not in orch._runtimes


async def test_delete_route_interrupts_active_then_tears_down(monkeypatch) -> None:
    _patch_factory(monkeypatch)
    from app.routes.sessions import delete_session

    class FakeStore:
        async def delete_session(self, owner: str, session_id: str) -> bool:
            return True

    orch = SessionOrchestrator(object())
    r1 = await _ensure(orch, "s1")
    orch._active["s1"] = r1  # in-flight turn

    await delete_session("s1", FakeStore(), "owner-1", orch)

    assert r1.interrupted is True
    assert r1.closed is True
    assert "s1" not in orch._runtimes

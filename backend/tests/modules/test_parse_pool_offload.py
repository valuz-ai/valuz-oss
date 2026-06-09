"""Regression: the document parser offloads GIL-bound work to a *process*.

``pymupdf4llm`` / ``markitdown`` do their work in pure Python and hold the GIL,
so ``asyncio.to_thread`` (the superseded approach) leaves the single-threaded
server starving while a parse runs — barely noticeable on fast arm64 cores,
but a multi-second freeze on slower x86 Macs (the reported "service hangs
during local parsing"). The fix routes the parse through a separate process so
the child holds its own GIL and the event loop stays free.

These tests assert the structural property that guarantees the fix — the work
runs in a *different process* — plus a lenient loop-responsiveness check and
the frozen-bundle inline fallback.
"""

import asyncio
import os
import time

import pytest

from valuz_agent.infra import parse_pool


@pytest.fixture
def enabled_pool(monkeypatch):
    """Re-enable the real process pool for this test (the suite disables it)."""
    monkeypatch.setenv("VALUZ_PARSE_POOL_DISABLED", "0")
    parse_pool.reset_for_test()
    yield
    parse_pool.reset_for_test()


async def test_run_parse_async_executes_in_separate_process(enabled_pool):
    pid = await parse_pool.run_parse_async(parse_pool._worker_pid)
    assert isinstance(pid, int) and pid > 0
    assert pid != os.getpid(), "parse must run in a worker process, not the loop process"


def test_run_parse_blocking_executes_in_separate_process(enabled_pool):
    pid = parse_pool.run_parse_blocking(parse_pool._worker_pid)
    assert pid != os.getpid(), "blocking parse must run in a worker process"


async def test_event_loop_stays_responsive_during_process_parse(enabled_pool):
    """A ~1s pure-Python GIL-bound burn, offloaded to a process, must not stall
    the event loop. (The same burn via ``to_thread`` would hold the GIL.)"""
    stop = asyncio.Event()
    gaps: list[float] = []

    async def heartbeat() -> None:
        last = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    hb = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)  # settle the heartbeat cadence
    result = await parse_pool.run_parse_async(parse_pool._cpu_burn, 30_000_000)
    stop.set()
    await hb

    assert isinstance(result, int)  # the child actually did the work
    max_gap_ms = max(gaps) * 1000
    # Process offload measured ~12ms in practice; 100ms is a generous ceiling
    # that still fails loudly if the work regresses onto the loop/GIL.
    assert max_gap_ms < 100, f"event loop stalled {max_gap_ms:.0f}ms during process-offloaded parse"


def test_falls_back_inline_when_pool_disabled(monkeypatch):
    monkeypatch.setenv("VALUZ_PARSE_POOL_DISABLED", "1")
    parse_pool.reset_for_test()
    try:
        pid = parse_pool.run_parse_blocking(parse_pool._worker_pid)
        assert pid == os.getpid(), "disabled pool must run inline in the calling process"
    finally:
        parse_pool.reset_for_test()

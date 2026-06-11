"""Cancellation semantics of ``infra.sse.shielded``.

SSE generators are cancelled by sse-starlette the moment the client
disconnects. ``shielded`` must (1) re-raise the CancelledError immediately so
the generator unwinds, while (2) letting the wrapped coroutine — a per-tick
DB read holding a pooled connection — run to completion in the background so
the connection is returned cleanly instead of being torn down mid-checkin.
"""

from __future__ import annotations

import asyncio

import pytest

from valuz_agent.infra.sse import shielded


async def test_returns_result_when_not_cancelled() -> None:
    async def work() -> int:
        await asyncio.sleep(0)
        return 42

    assert await shielded(work()) == 42


async def test_propagates_inner_exception() -> None:
    async def boom() -> None:
        raise RuntimeError("inner failure")

    with pytest.raises(RuntimeError, match="inner failure"):
        await shielded(boom())


async def test_cancel_unwinds_caller_but_inner_runs_to_completion() -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_db_read() -> str:
        started.set()
        await asyncio.sleep(0.05)
        finished.set()
        return "rows"

    async def caller() -> str:
        return await shielded(slow_db_read())

    task = asyncio.create_task(caller())
    await started.wait()
    task.cancel()

    # (1) the caller observes the cancellation promptly …
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not finished.is_set()

    # (2) … while the inner coroutine keeps running and completes cleanly.
    await asyncio.wait_for(finished.wait(), timeout=1.0)


async def test_cancel_after_inner_completion_still_cancels_caller() -> None:
    """Cancellation racing a just-finished inner task must not swallow it."""
    release = asyncio.Event()

    async def work() -> str:
        return "done"

    async def caller() -> str:
        result = await shielded(work())
        await release.wait()  # park so the cancel lands after shielded()
        return result

    task = asyncio.create_task(caller())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

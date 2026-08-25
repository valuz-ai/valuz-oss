"""Regression: sync parse of an async-only backend from inside a running loop.

The docs reindex / rescan worker runs an async loop in a daemon thread and
calls the parser via the SYNC ``parse_sync`` path. ``asyncio.run`` cannot nest
inside it, so an async-only backend needs somewhere else to run:

- ASYNC_POLL backends (PaddleOCR / MinerU) must run on the ``PollingScheduler``
  loop, where their awaiter futures live.
- Everything else just needs *a* loop, and gets a fresh one on its own thread.

The second case used to raise. See
``test_runs_a_schedulerless_async_backend_on_a_fresh_loop``.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from valuz_agent.modules.parser.router import _drive_async_parse_sync
from valuz_agent.ports.parser_backend import ParseOptions, ParseResult


class _AsyncOnlyBackend:
    """A backend that only exposes async ``parse`` (no ``parse_sync``),
    mirroring ``_PaddleOcrBackend`` / ``MineruBackend``."""

    def __init__(self, markdown: str, scheduler: object | None = None) -> None:
        self._markdown = markdown
        self._scheduler = scheduler

    async def parse(self, file_path: str, options: ParseOptions | None = None) -> ParseResult:
        return ParseResult(markdown=self._markdown, page_count=1)


def test_drives_async_backend_with_no_running_loop() -> None:
    backend = _AsyncOnlyBackend("local-run")
    result = _drive_async_parse_sync(backend, "/tmp/x.pdf", None)
    assert result.markdown == "local-run"


def test_dispatches_to_scheduler_loop_when_already_in_a_running_loop() -> None:
    # A separate "main" loop running on its own thread — stands in for the
    # PollingScheduler's loop.
    main_loop = asyncio.new_event_loop()
    threading.Thread(target=main_loop.run_forever, name="main-loop", daemon=True).start()
    try:
        backend = _AsyncOnlyBackend("dispatched", scheduler=SimpleNamespace(loop=main_loop))

        async def worker_loop() -> ParseResult:
            # Mimic ``_run_reindex_loop``: a *blocking sync* parse call made
            # from inside an already-running (worker) loop. The old code
            # raised RuntimeError here; the fix dispatches to ``main_loop``.
            return _drive_async_parse_sync(backend, "/tmp/x.pdf", None)

        result = asyncio.run(worker_loop())
        assert result.markdown == "dispatched"
    finally:
        main_loop.call_soon_threadsafe(main_loop.stop)


def test_runs_a_schedulerless_async_backend_on_a_fresh_loop() -> None:
    """A backend can be async-implemented without being ASYNC_POLL.

    A cloud plugin that awaits its own HTTP calls has no scheduler and needs no
    particular loop — it just cannot start on the one already running here.
    This used to raise, on the reasoning that a missing scheduler meant a
    missing prerequisite, and that failing loudly was safe because the router
    would fall back to LightLocal.

    Neither half held. Nothing about the coroutine is loop-bound, and the one
    deployment that routes PDFs here pins ``fallback_to_local_on_error=False``
    on purpose — a cloud parse that fails must surface rather than quietly
    landing on a local engine that returns an empty document for scans. So the
    raise had no safety net under it: every PDF failed ``PARSE_ERROR``.
    """
    ran_on: list[object] = []

    class _Recording(_AsyncOnlyBackend):
        async def parse(self, file_path, options=None):  # type: ignore[no-untyped-def]
            ran_on.append(asyncio.get_running_loop())
            return await super().parse(file_path, options)

    backend = _Recording("fresh-loop", scheduler=SimpleNamespace(loop=None))

    async def worker_loop() -> tuple[ParseResult, object]:
        return _drive_async_parse_sync(backend, "/tmp/x.pdf", None), asyncio.get_running_loop()

    result, worker = asyncio.run(worker_loop())

    assert result.markdown == "fresh-loop"
    # A different loop than the caller's — proof it was not nested, which is
    # the thing asyncio refuses outright.
    assert ran_on[0] is not worker


def test_a_backend_with_no_scheduler_attribute_at_all_still_runs() -> None:
    """``_scheduler`` is an ASYNC_POLL implementation detail. A backend that
    never heard of it — which is every plugin outside this repo — reached the
    raise through a ``getattr`` default."""
    backend = _AsyncOnlyBackend("no-attr")
    del backend._scheduler

    async def worker_loop() -> ParseResult:
        return _drive_async_parse_sync(backend, "/tmp/x.pdf", None)

    assert asyncio.run(worker_loop()).markdown == "no-attr"


def test_the_scheduler_loop_still_wins_when_there_is_one() -> None:
    """ASYNC_POLL backends must NOT take the fresh-loop path: their awaiter
    futures live on the scheduler's loop, and a parse started anywhere else
    would wait on a tick that never reaches it."""
    main_loop = asyncio.new_event_loop()
    threading.Thread(target=main_loop.run_forever, name="main-loop", daemon=True).start()
    try:
        ran_on: list[object] = []

        class _Recording(_AsyncOnlyBackend):
            async def parse(self, file_path, options=None):  # type: ignore[no-untyped-def]
                ran_on.append(asyncio.get_running_loop())
                return await super().parse(file_path, options)

        backend = _Recording("polled", scheduler=SimpleNamespace(loop=main_loop))

        async def worker_loop() -> ParseResult:
            return _drive_async_parse_sync(backend, "/tmp/x.pdf", None)

        assert asyncio.run(worker_loop()).markdown == "polled"
        assert ran_on == [main_loop]
    finally:
        main_loop.call_soon_threadsafe(main_loop.stop)

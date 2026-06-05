"""Regression: sync parse of an async-only backend from inside a running loop.

The docs reindex / rescan worker runs an async loop in a daemon thread and
calls the parser via the SYNC ``parse_sync`` path. For ASYNC_POLL backends
(PaddleOCR / MinerU) the old code did ``asyncio.run(backend.parse(...))``,
which raises *"asyncio.run() cannot be called from a running event loop"* when
a loop is already running. The fix dispatches the coroutine onto the
``PollingScheduler``'s main loop via ``run_coroutine_threadsafe``.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

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


def test_raises_when_async_backend_has_no_scheduler_loop_inside_a_loop() -> None:
    # An async backend with no usable scheduler loop, called from within a
    # running loop, must fail loudly (so the router falls back to LightLocal)
    # rather than nesting asyncio.run.
    backend = _AsyncOnlyBackend("nope", scheduler=SimpleNamespace(loop=None))

    async def worker_loop() -> ParseResult:
        return _drive_async_parse_sync(backend, "/tmp/x.pdf", None)

    with pytest.raises(RuntimeError, match="PollingScheduler loop"):
        asyncio.run(worker_loop())

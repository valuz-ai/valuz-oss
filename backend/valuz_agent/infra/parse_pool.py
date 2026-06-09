"""Process-pool offload for GIL-bound document parsers.

Why a *process* and not a thread
--------------------------------
``pymupdf4llm`` / ``markitdown`` do the bulk of their work in **pure Python**
(text-span analysis, table/image detection, Markdown assembly). Pure-Python
code holds the GIL, so ``asyncio.to_thread`` moves that work off the
event-loop *thread* but NOT off the GIL — the worker thread still starves the
single-threaded server while it runs. Measured on a 60-page PPT→PDF deck:

    to_thread + pymupdf4llm : 6.9s parse, event-loop stalls up to 315ms (arm64)
    process   + pymupdf4llm : 6.9s parse, event-loop stalls   <= 12ms

On a slower x86 core the same GIL holds stretch into multi-second freezes —
the reported "service hangs until the parse finishes" on an Intel Mac, even
though arm64 machines barely notice the ~300ms stalls. Running the parse in a
separate process is the only way to free the loop: the child holds its *own*
GIL.

Usage
-----
- ``await run_parse_async(fn, *args)`` — from a coroutine; the loop stays free.
- ``run_parse_blocking(fn, *args)`` — from a *worker* thread or a true-sync
  context (blocks the caller, never the loop). NEVER call on the event loop.

``fn`` and its args/return value must be picklable (a top-level callable;
dataclasses / primitives). Both helpers transparently fall back to in-thread /
inline execution when the process pool can't be created or dies — a
frozen-bundle safety net so behavior never regresses below the old thread path.

Escape hatches (env):
- ``VALUZ_PARSE_POOL_DISABLED=1`` — force the in-thread fallback (the unit
  suite sets this so tests stay fast and subprocess-free).
- ``VALUZ_PARSE_POOL_CTX`` — multiprocessing start method (default ``spawn``).
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
from collections.abc import Callable
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

# Bound true parallelism (and thus peak memory of N concurrent parse jobs).
# Small on purpose: this targets a desktop box, not a server farm.
_MAX_WORKERS = 2

_pool: ProcessPoolExecutor | None = None
_broken = False


def _enabled() -> bool:
    return os.environ.get("VALUZ_PARSE_POOL_DISABLED") != "1"


def _context() -> mp.context.BaseContext:
    # ``spawn`` is fork-free: required on macOS and inside the PyInstaller
    # bundle, and it avoids inheriting the parent's asyncio loop / open fds.
    name = os.environ.get("VALUZ_PARSE_POOL_CTX", "spawn")
    try:
        return mp.get_context(name)
    except ValueError:
        return mp.get_context()


def _get_pool() -> ProcessPoolExecutor | None:
    global _pool
    if _broken or not _enabled():
        return None
    if _pool is None:
        try:
            _pool = ProcessPoolExecutor(max_workers=_MAX_WORKERS, mp_context=_context())
        except Exception:  # noqa: BLE001 — any spawn/exec failure → fallback
            logger.exception("parse process pool unavailable; using in-thread fallback")
            _mark_broken()
            return None
    return _pool


def _mark_broken() -> None:
    global _pool, _broken
    _broken = True
    pool, _pool = _pool, None
    if pool is not None:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass


async def run_parse_async[T](fn: Callable[..., T], *args: Any) -> T:
    """Run ``fn(*args)`` in a separate process and await the result.

    Keeps the calling event loop free of the GIL-bound parse. Falls back to a
    worker thread when the pool is unavailable/broken.
    """
    import asyncio

    pool = _get_pool()
    if pool is None:
        return await asyncio.to_thread(fn, *args)
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(pool, fn, *args)
    except (BrokenExecutor, RuntimeError, OSError):
        # Pool died or couldn't spawn a worker (e.g. a frozen/reload env where
        # ``spawn`` can't re-import the entry). The parse itself never raises —
        # ``_parse_sync_impl`` returns a ParseResult on failure — so these are
        # infra failures: disable the pool and fall back to a thread.
        logger.warning("parse process pool unavailable; falling back to thread", exc_info=True)
        _mark_broken()
        return await asyncio.to_thread(fn, *args)


def run_parse_blocking[T](fn: Callable[..., T], *args: Any) -> T:
    """Run ``fn(*args)`` in a separate process, blocking the CALLING THREAD.

    Safe from a ``to_thread`` worker or a background-thread loop — the blocked
    thread isn't the event loop, and the GIL-bound work runs in the child, so
    the main loop keeps getting time slices. NEVER call on the event loop.
    Falls back to inline execution when the pool is unavailable/broken.
    """
    pool = _get_pool()
    if pool is None:
        return fn(*args)
    try:
        return pool.submit(fn, *args).result()
    except (BrokenExecutor, RuntimeError, OSError):
        # See ``run_parse_async``: infra failure (pool death / spawn failure),
        # not a parse error — disable the pool and run inline.
        logger.warning("parse process pool unavailable; running inline", exc_info=True)
        _mark_broken()
        return fn(*args)


def warm() -> None:
    """Pre-spawn the worker processes (and pre-import the heavy parser libs in
    them) at boot, so the first real parse doesn't pay spawn + import latency
    on the critical path. Best-effort."""
    pool = _get_pool()
    if pool is None:
        return
    try:
        for _ in range(_MAX_WORKERS):
            pool.submit(_warm_worker)
    except Exception:  # noqa: BLE001
        logger.debug("parse pool warm-up skipped", exc_info=True)


def _warm_worker() -> bool:
    try:
        import pymupdf4llm  # type: ignore[import-untyped]  # noqa: F401
    except Exception:  # noqa: BLE001
        pass
    return True


def shutdown() -> None:
    _mark_broken()


# ── test seam ────────────────────────────────────────────────────────────
def reset_for_test() -> None:
    """Drop the cached pool + broken flag so a test can re-create it under a
    fresh environment (see ``test_parse_pool_offload``)."""
    global _pool, _broken
    old, _pool = _pool, None
    _broken = False
    if old is not None:
        try:
            old.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass


def _worker_pid() -> int:
    """Return the executing process id — lets the offload test assert work ran
    in a *separate* process (and the fallback ran in-process)."""
    return os.getpid()


def _cpu_burn(iterations: int) -> int:
    """Pure-Python, GIL-holding busy-work — a stand-in for a pymupdf4llm parse
    in the offload regression test. Top-level so it's importable by the spawned
    worker. Returns a value derived from the loop so it can't be optimized out.
    """
    total = 0
    for i in range(iterations):
        total = (total + i * i) % 2147483647
    return total

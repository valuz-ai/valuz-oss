"""Parent-death watchdog for the desktop sidecar.

The Electron shell owns the sidecar's lifetime, but only the cooperative
paths (``before-quit`` → ``stop()``) actually run on a graceful exit. A
crashed / force-quit / deleted-while-running shell leaves the backend
orphaned — it keeps the single-writer lock and the port, so every later
app launch boots a backend that dies on the lock ("Application startup
failed") while the UI talks to a stale process it cannot manage.

This watchdog closes that hole from the child's side: when the spawner
passes ``VALUZ_PARENT_PID``, the backend polls that pid and shuts itself
down as soon as the parent is gone. Headless / dev runs never set the
env var, so nothing changes for them.

Shutdown path: SIGTERM to ourselves first (uvicorn's graceful shutdown —
WAL checkpoints, lifespan teardown), then a hard ``os._exit`` if the
process is still alive after a grace window. On Windows there is no
useful self-SIGTERM, so the hard exit applies directly; abrupt exit is
already the platform's quit-time behavior (``taskkill /F``) and WAL +
boot recovery cover it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 5.0
GRACEFUL_EXIT_GRACE_S = 15.0

_task: asyncio.Task[None] | None = None


def parent_pid_from_env() -> int | None:
    """The spawner's pid, when it asked to be watched. ``None`` disables."""
    raw = os.environ.get("VALUZ_PARENT_PID", "").strip()
    if not raw:
        return None
    try:
        pid = int(raw)
    except ValueError:
        logger.warning("ignoring non-numeric VALUZ_PARENT_PID=%r", raw)
        return None
    return pid if pid > 0 else None


def parent_alive(pid: int) -> bool:
    """Whether *pid* still exists (not whether it is healthy)."""
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by someone else — still alive.
        return True
    return True


def _shutdown_self() -> None:
    logger.error(
        "parent process is gone — shutting the sidecar down (parent-death watchdog)"
    )
    if sys.platform != "win32":
        # Graceful first: uvicorn translates SIGTERM into a clean lifespan
        # shutdown. The hard exit below only fires if that wedges.
        os.kill(os.getpid(), signal.SIGTERM)

        def _hard_exit() -> None:
            logger.error("graceful shutdown did not complete in time — hard exit")
            os._exit(1)

        loop = asyncio.get_running_loop()
        loop.call_later(GRACEFUL_EXIT_GRACE_S, _hard_exit)
        return
    os._exit(1)


async def watch_parent(
    pid: int,
    *,
    alive: Callable[[int], bool] = parent_alive,
    on_dead: Callable[[], None] = _shutdown_self,
    poll_interval_s: float = POLL_INTERVAL_S,
) -> None:
    """Poll *pid* until it disappears, then invoke *on_dead* once."""
    logger.info("parent-death watchdog armed (parent pid=%d)", pid)
    while True:
        await asyncio.sleep(poll_interval_s)
        if not alive(pid):
            on_dead()
            return


def start_parent_watchdog() -> None:
    """Arm the watchdog when the spawner asked for it (idempotent)."""
    global _task
    if _task is not None and not _task.done():
        return
    pid = parent_pid_from_env()
    if pid is None:
        return
    _task = asyncio.get_running_loop().create_task(
        watch_parent(pid), name="parent-death-watchdog"
    )


def stop_parent_watchdog() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None


__all__ = [
    "parent_alive",
    "parent_pid_from_env",
    "start_parent_watchdog",
    "stop_parent_watchdog",
    "watch_parent",
]

"""Backup scheduler — periodic due-check, same daemon-thread convention as
``modules/docs/scheduler.py`` / ``modules/skills/scheduler.py``.

The tick is cheap (three preference reads); the actual run happens through
``BackupService.execute_backup`` which is single-flight. Missed windows (the
app was closed when a backup came due) fire on the first tick after boot —
``next_run_at <= now`` is the whole due test, mirroring the automations
runner's stranded semantics.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

TICK_INTERVAL_SEC = 60


class BackupScheduler:
    def __init__(self, tick_fn: Callable[[], None], interval: int = TICK_INTERVAL_SEC) -> None:
        self._tick_fn = tick_fn
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="backup-scheduler", daemon=True)
        self._thread.start()
        logger.info("backup scheduler started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("backup scheduler stopped")

    def _loop(self) -> None:
        while not self._stop.wait(timeout=self._interval):
            try:
                self._tick_fn()
            except Exception:  # noqa: BLE001
                logger.exception("backup scheduler tick failed")


def run_backup_tick() -> None:
    """Entry point for the daemon thread. Hosts its own event loop
    (``asyncio.run``) and binds a per-loop DB engine for this foreign loop
    (``run_in_background_db_scope`` — no-op on SQLite, required for asyncpg)."""
    import asyncio

    from valuz_agent.infra.db import run_in_background_db_scope
    from valuz_agent.infra.local_identity import resolve_local_user_id
    from valuz_agent.modules.backup.service import backup_service

    user_id = resolve_local_user_id()
    asyncio.run(run_in_background_db_scope(backup_service.tick(user_id)))


_scheduler: BackupScheduler | None = None


def start_backup_scheduler() -> None:
    global _scheduler
    if _scheduler:
        return
    interval = int(os.environ.get("VALUZ_BACKUP_TICK_SEC", str(TICK_INTERVAL_SEC)))
    if interval <= 0:
        logger.info("backup scheduler disabled (VALUZ_BACKUP_TICK_SEC<=0)")
        return
    _scheduler = BackupScheduler(run_backup_tick, interval=interval)
    _scheduler.start()


def stop_backup_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None

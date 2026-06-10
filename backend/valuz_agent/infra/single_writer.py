"""Process-wide single-writer guard (ADR-011).

Why this exists
---------------
SQLite is *not* the reason. Under WAL with ``busy_timeout`` (see
``infra/database.py``) two processes writing ``~/.valuz/app/valuz.db``
serialize on the single write slot and wait-and-retry — concurrent
access is safe at the storage layer, not corrupting.

The real hazard is duplicated *host* side-effects when a desktop app
starts twice against one ``data_dir`` (GUI-launched backend + launchd
auto-start, or a double-clicked icon):

- Boot recovery assumes a single process. ``recover_running_sessions``
  finalises every ``running`` session as stranded-by-the-previous-process
  (see ``modules/sessions/recovery.py``: *"the host is single-process, so
  any running row at startup is by definition stranded"*), and
  ``seal_orphan_pendings`` / ``recover_active_tasks`` lean on the same
  invariant. A second instance booting would terminate or re-drive
  sessions the first instance is *actively* running.
- The long-lived runners (automation runner + failure monitor, parser
  polling, docs auto-discovery, decision aggregator) would each run
  twice: scheduled automations firing twice, pollers double-calling
  remote APIs, the decision inbox double-processing.

Port collision is too weak a guard. uvicorn runs the whole lifespan
startup — schema bootstrap, *all* the recovery above, and the runners —
*before* it binds the socket, so an ``EADDRINUSE`` on port 8000 only
fires after the damage is done; and a second instance on a different
``VALUZ_BACKEND_PORT`` (same ``data_dir``) never collides at all. So we
refuse the second startup outright — early, and with a clear message.

How it works
------------
On startup we open ``<data_dir>/.single-writer.lock`` (configurable) and try
``fcntl.flock(LOCK_EX | LOCK_NB)``. Success → store the FD on the module
to keep the lock alive for the process lifetime. Failure → ``BlockingIOError``
means another process holds it; bail with ``sys.exit(2)``.

The lock file path is intentionally inside ``data_dir`` so two distinct
``VALUZ_DATA_DIR`` instances (e.g. dev + prod-like via env var) don't
exclude each other — they're meant to be independent.

On macOS / Linux the lock uses ``fcntl.flock(LOCK_EX | LOCK_NB)``.
On Windows it uses ``msvcrt.locking(LK_NBLCK)``. Both are stdlib — no
external dependencies.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Module-level FD reference. ``fcntl.flock`` releases the lock when the
# FD is closed; by holding the reference we keep the lock for the whole
# process lifetime even though nothing else uses ``_lock_fd``.
_lock_fd: int | None = None


class AnotherInstanceRunning(RuntimeError):  # noqa: N818 — historical name; keeps API stable
    """Raised when another valuz-agent backend already holds the lock."""


def acquire_single_writer_lock(lock_path: Path) -> None:
    """Acquire the process-wide writer lock or raise.

    Idempotent within a single process: re-calling is a no-op as long as
    the previous lock is still held.
    """
    global _lock_fd
    if _lock_fd is not None:
        return

    if sys.platform == "win32":
        import msvcrt  # Windows-only; deferred import to keep Unix importable.

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o644)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            os.close(fd)
            msg = (
                f"another valuz-agent backend already holds {lock_path}; "
                "refusing to start a second instance."
            )
            logger.error(msg)
            raise AnotherInstanceRunning(msg) from exc

        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
        _lock_fd = fd
        logger.info("Acquired single-writer lock at %s (pid=%d)", lock_path, os.getpid())
        return

    import fcntl  # POSIX-only; deferred import to keep Windows importable.

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        msg = (
            f"another valuz-agent backend already holds {lock_path}; "
            "refusing to start a second instance."
        )
        logger.error(msg)
        raise AnotherInstanceRunning(msg) from exc

    # Drop the PID into the file so ``doctor`` can identify the holder.
    # We don't fsync — the cost isn't worth it for a hint.
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode("ascii"))
    _lock_fd = fd
    logger.info("Acquired single-writer lock at %s (pid=%d)", lock_path, os.getpid())


def release_single_writer_lock() -> None:
    """Release the lock. Safe to call when no lock is held."""
    global _lock_fd
    if _lock_fd is None:
        return
    try:
        os.close(_lock_fd)
    finally:
        _lock_fd = None


__all__ = [
    "AnotherInstanceRunning",
    "acquire_single_writer_lock",
    "release_single_writer_lock",
]

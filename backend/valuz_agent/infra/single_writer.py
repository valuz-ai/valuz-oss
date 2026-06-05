"""Process-wide single-writer guard (ADR-011).

Why this exists
---------------
SQLite under WAL is happy with many concurrent readers and one writer
across processes, but a desktop app starting up twice (GUI launched
backend + launchd auto-started backend, or the user double-clicked the
icon) would have two processes both writing to ``~/.valuz/app/valuz.db``.
That's not just bad for SQLite — both processes would also fight over
port 8000, write conflicting events, and race the schedule runner's
tick loop. Easier to refuse the second startup outright.

How it works
------------
On startup we open ``<data_dir>/.scheduler.lock`` (configurable) and try
``fcntl.flock(LOCK_EX | LOCK_NB)``. Success → store the FD on the module
to keep the lock alive for the process lifetime. Failure → ``BlockingIOError``
means another process holds it; bail with ``sys.exit(2)``.

The lock file path is intentionally inside ``data_dir`` so two distinct
``VALUZ_DATA_DIR`` instances (e.g. dev + prod-like via env var) don't
exclude each other — they're meant to be independent.

POSIX-only for now; Windows would use ``msvcrt.locking`` or
``portalocker``. The desktop app's first-class platform is macOS so this
covers the canonical path; Windows users get a clear unsupported error
when we reach that.
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
        logger.warning(
            "Single-writer lock is not implemented on Windows; relying on "
            "OS-level port collisions to prevent double-start."
        )
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


def read_lock_holder_pid(lock_path: Path) -> int | None:
    """Inspect the lock file for the holder's PID.

    Used by ``valuz-agent doctor`` so the user can see "which process
    has the lock" without trying to acquire it themselves.
    """
    try:
        with lock_path.open("r", encoding="ascii") as f:
            raw = f.read().strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


__all__ = [
    "AnotherInstanceRunning",
    "acquire_single_writer_lock",
    "release_single_writer_lock",
    "read_lock_holder_pid",
]

"""Host ``python3`` availability probe for PTC code execution.

The packaged backend is a PyInstaller-frozen binary — ``sys.executable`` is
NOT a Python interpreter there, so agent-authored code must run on a real
host ``python3``. Two failure modes make a bare ``shutil.which`` check
insufficient:

- macOS ships a ``/usr/bin/python3`` *stub* that, without the Command Line
  Tools installed, pops a GUI install dialog and exits non-zero;
- an arbitrary PATH entry may be broken or ancient.

The probe therefore actually executes the candidate once (``-c "import
sys"``) with a hard timeout. The result is cached for the process lifetime:
interpreters do not appear mid-session, and a user who installs one can
restart the backend.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading

_PROBE_TIMEOUT_SECONDS = 10.0

# Explicit interpreter override for tests / unusual hosts (e.g. a venv that
# carries pandas). Read at probe time, before PATH discovery.
PTC_PYTHON_ENV = "VALUZ_PTC_PYTHON"

_lock = threading.Lock()
_cached: tuple[str | None, str | None] | None = None  # (path, unavailable_reason)


def _probe() -> tuple[str | None, str | None]:
    override = os.environ.get(PTC_PYTHON_ENV, "").strip()
    candidate = override or shutil.which("python3")
    if not candidate:
        return None, "python3 not found on PATH"
    if override and not (shutil.which(candidate) or os.path.isfile(candidate)):
        return None, f"{PTC_PYTHON_ENV}={candidate!r} is not executable"
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [candidate, "-c", "import sys"],
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"python3 probe failed: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()[-200:]
        return None, f"python3 probe exited {proc.returncode}: {tail or 'no stderr'}"
    return candidate, None


def python3_path() -> str | None:
    """Absolute/command path of a working ``python3``, or ``None``."""
    return _probe_cached()[0]


def python3_unavailable_reason() -> str | None:
    """``None`` when a working ``python3`` exists; otherwise a human reason."""
    return _probe_cached()[1]


def _probe_cached() -> tuple[str | None, str | None]:
    global _cached  # noqa: PLW0603
    with _lock:
        if _cached is None:
            _cached = _probe()
        return _cached


def reset_probe_cache_for_tests() -> None:
    """Drop the cached probe result — pytest hook only."""
    global _cached  # noqa: PLW0603
    with _lock:
        _cached = None

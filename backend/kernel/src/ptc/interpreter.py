"""PTC interpreter resolution — which executable runs agent programs.

Resolution ladder (first hit wins; the result is an argv PREFIX the
executor appends the program path to):

1. ``VALUZ_PTC_PYTHON`` — explicit override, probed by actually executing
   it once. A broken override FAILS LOUDLY (never a silent fallback — the
   user asked for that interpreter specifically).
2. Host ``python3`` on PATH — probed by executing ``-c "import sys"`` with
   a hard timeout. macOS ships a CLT-less stub that pops an install dialog
   and exits non-zero, and Windows resolves ``python3`` to the Store
   alias; both fail the probe and fall through instead of blocking PTC.
3. The backend's own runtime — always launchable, no probe needed (this
   process is running on it): plain ``sys.executable`` in a source
   checkout, or the PyInstaller-frozen binary re-invoked as
   ``valuz-server --ptc-exec`` (``valuz_agent/ptc_exec.py``) when frozen.

Consequence: on a standard build PTC is never "unavailable" — only an
explicitly configured, broken override reports a reason. Cached for the
process lifetime (interpreters do not appear mid-session; a user who
installs one restarts the backend).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

_PROBE_TIMEOUT_SECONDS = 10.0

# Explicit interpreter override (e.g. a venv that carries pandas).
PTC_PYTHON_ENV = "VALUZ_PTC_PYTHON"

_lock = threading.Lock()
_cached: tuple[tuple[str, ...] | None, str | None] | None = None


def _exec_probe(candidate: str) -> str | None:
    """Run the candidate once; ``None`` when it works, else a reason."""
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [candidate, "-c", "import sys"],
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"probe failed: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()[-200:]
        return f"probe exited {proc.returncode}: {tail or 'no stderr'}"
    return None


def _probe() -> tuple[tuple[str, ...] | None, str | None]:
    override = os.environ.get(PTC_PYTHON_ENV, "").strip()
    if override:
        if not (shutil.which(override) or os.path.isfile(override)):
            return None, f"{PTC_PYTHON_ENV}={override!r} is not executable"
        reason = _exec_probe(override)
        if reason is not None:
            return None, f"{PTC_PYTHON_ENV}={override!r} {reason}"
        return (override,), None

    candidate = shutil.which("python3")
    if candidate is not None and _exec_probe(candidate) is None:
        return (candidate,), None

    if getattr(sys, "frozen", False):
        return (sys.executable, "--ptc-exec"), None
    return (sys.executable,), None


def interpreter_argv() -> tuple[str, ...] | None:
    """Argv prefix of the resolved interpreter, or ``None`` (broken override)."""
    return _probe_cached()[0]


def interpreter_unavailable_reason() -> str | None:
    """``None`` when PTC can execute programs; otherwise a human reason."""
    return _probe_cached()[1]


def _probe_cached() -> tuple[tuple[str, ...] | None, str | None]:
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

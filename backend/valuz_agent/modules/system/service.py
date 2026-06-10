"""Collect a snapshot of the backend process for the ``服务`` UI panel.

Cheap on every call — all fields come from in-process state or
already-warm caches:

* ``boot_state``: tracked by the module-level singletons below; updated
  by ``api/app.py`` startup hooks.
* ``kernel_pin``: parsed from ``backend/kernel/KERNEL_VERSION`` on first
  read and memoised. The kernel pin doesn't change at runtime — a kernel
  upgrade requires a process restart, which re-loads the file.
* ``active_session_count``: kernel store query (sync facade, fast).

This module deliberately avoids hitting the network or the filesystem on
every status call, so the desktop UI can poll at a few-second cadence.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from valuz_agent.adapters import kernel_client
from valuz_agent.infra.config import settings
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.system.schemas import SystemStatusResponse

# ── Module-level boot state ────────────────────────────────────────────
# ``api/app.py`` calls ``record_boot_started()`` once at process start
# (before startup hooks run) and ``record_boot_complete()`` once they
# all finish. Anything observed in-between surfaces as ``starting``.

_started_at: int | None = None
_boot_complete: bool = False
_warnings: list[str] = []
_kernel_pin_cache: str | None = None


def record_boot_started() -> None:
    """Stamp the process start time. Call once at the very top of startup."""
    global _started_at
    if _started_at is None:
        _started_at = now_ms()


def record_boot_complete() -> None:
    """Mark the process as having finished its startup hooks."""
    global _boot_complete
    _boot_complete = True


def record_warning(message: str) -> None:
    """Append a non-fatal warning surfaced in the status snapshot.

    Capped at 50 entries (FIFO) so a runaway producer can't bloat the
    response payload. The desktop log viewer is the right place for the
    full firehose; this list is for the curated "things the user might
    want to know about" set.
    """
    _warnings.append(message)
    if len(_warnings) > 50:
        del _warnings[: len(_warnings) - 50]


def clear_warnings() -> None:
    """Reset the warnings buffer — used by tests."""
    _warnings.clear()


# ── Helpers ────────────────────────────────────────────────────────────


def _read_kernel_pin() -> str:
    """Parse ``backend/kernel/KERNEL_VERSION`` for the ``commit:`` line.

    Returns ``"unknown"`` if the file is missing or malformed (defensive
    — the status endpoint must never throw).
    """
    global _kernel_pin_cache
    if _kernel_pin_cache is not None:
        return _kernel_pin_cache

    # KERNEL_VERSION lives at ``backend/kernel/KERNEL_VERSION``. Walk up
    # from this file: modules/system/ → modules/ → valuz_agent/ →
    # backend/. ``parents[3]`` is ``backend/``; appending
    # ``kernel/KERNEL_VERSION`` lands on the kernel version file.
    here = Path(__file__).resolve()
    candidate = here.parents[3] / "kernel" / "KERNEL_VERSION"
    pin = "unknown"
    try:
        for line in candidate.read_text(encoding="utf-8").splitlines():
            if line.startswith("commit:"):
                pin = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    _kernel_pin_cache = pin
    return pin


async def _count_active_sessions() -> int:
    try:
        sessions = await kernel_client.list_sessions(limit=500)
        return sum(1 for s in sessions if s.status == "running")
    except Exception:  # noqa: BLE001 — status must never throw
        return 0


def _runtimes_available() -> list[str]:
    """Best-effort probe of which kernel runtimes can currently dispatch.

    For now we report the static set the kernel exposes; per-runtime
    health (e.g. ``codex`` binary on PATH) is already surfaced via
    ``GET /v1/runtimes`` in more detail. Keeping this list minimal here
    avoids duplicating that probe on every status hit.
    """
    return ["claude_agent", "codex", "deepagents"]


# ── Public API ─────────────────────────────────────────────────────────


async def collect_system_status(*, port: int) -> SystemStatusResponse:
    """Build the ``SystemStatusResponse`` payload for one HTTP call.

    ``port`` is passed in by the router because uvicorn picks it from
    the CLI flag at process start; nothing on the FastAPI app object
    exposes it cleanly.
    """
    started = _started_at or now_ms()
    uptime = max(0.0, (now_ms() - started) / 1000)

    if not _boot_complete:
        status = "starting"
    elif _warnings:
        status = "degraded"
    else:
        status = "running"

    log_dir = settings.log_dir
    log_file = settings.log_file

    return SystemStatusResponse(
        status=status,
        pid=os.getpid(),
        started_at=started,
        uptime_seconds=round(uptime, 3),
        version=_read_app_version(),
        kernel_pin=_read_kernel_pin(),
        port=port,
        active_session_count=await _count_active_sessions(),
        db_path=str(settings.db_path),
        log_path=str(log_file),
        log_dir=str(log_dir),
        data_dir=str(settings.data_dir),
        runtimes_available=_runtimes_available(),
        warnings=list(_warnings),
    )


def _read_app_version() -> str:
    """Read the host package version from ``backend/pyproject.toml``."""
    here = Path(__file__).resolve()
    pyproject = here.parents[4] / "pyproject.toml"
    try:
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version") and "=" in stripped:
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "0.0.0"


# ── Listening port discovery ───────────────────────────────────────────
# uvicorn doesn't expose its socket on the FastAPI app object, but we
# can recover the port from the CLI args the launcher passed in. valuz's
# CLI sets it explicitly via ``--port`` (see ``valuz_agent.cli``).

_listen_port: int = 8000


def record_listen_port(port: int) -> None:
    global _listen_port
    _listen_port = port


def listen_port() -> int:
    return _listen_port


# ``time`` import kept for any caller wanting an alternative monotonic
# uptime base; ``collect_system_status`` uses datetime arithmetic.
_ = time

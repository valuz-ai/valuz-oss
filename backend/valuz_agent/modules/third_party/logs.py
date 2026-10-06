"""Per-plugin logs: ``<shared root>/logs/extensions/<id>.log``, JSON lines.

Each line is ``{"ts": <epoch ms>, "level", "message", "source"}`` with ``source``
one of ``frontend`` (the plugin's ``ctx.log``), ``backend`` (this host, e.g. a
failed automation sync) or ``audit`` (denied and write requests the permission
middleware saw). The file is trimmed to its newest half once it passes ~1 MiB.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from valuz_agent.infra.fs_registry import fs_registry

logger = logging.getLogger(__name__)

MAX_LOG_BYTES = 1024 * 1024
MAX_MESSAGE_CHARS = 8000
LEVELS = ("debug", "info", "warn", "error")
SOURCES = ("frontend", "backend", "audit")

_lock = threading.Lock()


def normalize_level(level: str) -> str:
    lowered = (level or "").strip().lower()
    if lowered == "warning":
        return "warn"
    return lowered if lowered in LEVELS else "info"


def _trim(path: Path) -> None:
    try:
        data = path.read_bytes()
    except OSError:
        return
    keep = data[-(MAX_LOG_BYTES // 2) :]
    newline = keep.find(b"\n")
    if newline != -1:
        keep = keep[newline + 1 :]
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(keep)
    tmp.replace(path)


def append_log(plugin_id: str, level: str, message: str, source: str = "backend") -> None:
    """Best-effort: a log failure never breaks the request that caused it."""
    try:
        path = fs_registry.extension_log_path(plugin_id)
        line = json.dumps(
            {
                "ts": int(time.time() * 1000),
                "level": normalize_level(level),
                "message": str(message)[:MAX_MESSAGE_CHARS],
                "source": source if source in SOURCES else "backend",
            },
            ensure_ascii=False,
        )
        with _lock:
            try:
                oversized = path.stat().st_size > MAX_LOG_BYTES
            except OSError:
                oversized = False
            if oversized:
                _trim(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except (OSError, ValueError):
        logger.debug("could not write the log of plugin %s", plugin_id, exc_info=True)


def read_log(plugin_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """The newest ``limit`` entries, oldest first (newest last)."""
    try:
        path = fs_registry.extension_log_path(plugin_id)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return []
    entries: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def delete_log(plugin_id: str) -> None:
    try:
        fs_registry.extension_log_path(plugin_id).unlink()
    except (OSError, ValueError):
        pass


__all__ = ["LEVELS", "SOURCES", "append_log", "delete_log", "normalize_level", "read_log"]

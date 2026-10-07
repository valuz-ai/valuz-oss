"""``installed.json`` — the device-wide registry of installed third-party plugins.

Shape (docs task card 04 §A)::

    {"version": 1, "generation": N,
     "plugins": {"<id>": {"id", "version", "source": {"kind": "file|url|dev|catalog", ...},
                           "sha256", "installed_at", "dev_path"?, "revision"}}}

Writes are atomic (temp file + ``os.replace``) and serialised by a process-wide
asyncio lock. ``generation`` rises on every change that a renderer must react to
(install, enable / disable, uninstall, dev reload, config edit, safe mode);
``wait_for_change`` wakes long-polling watchers when it does.

Safe mode (``app-plugins/safe-mode.json`` or ``VALUZ_APP_PLUGINS_SAFE_MODE=1``) is
kept here too: the renderer skips every third-party plugin while it is on.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from valuz_agent.infra.fs_registry import fs_registry

logger = logging.getLogger(__name__)

INSTALLED_FILENAME = "installed.json"
SAFE_MODE_FILENAME = "safe-mode.json"
SAFE_MODE_ENV = "VALUZ_APP_PLUGINS_SAFE_MODE"
LEGACY_SAFE_MODE_ENV = "VALUZ_EXTENSIONS_SAFE_MODE"
STATE_VERSION = 1

State = dict[str, Any]


def _empty() -> State:
    return {"version": STATE_VERSION, "generation": 0, "plugins": {}}


class InstalledStore:
    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._waiters: set[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = set()

    # -- paths ---------------------------------------------------------------------

    @staticmethod
    def path() -> Path:
        return fs_registry.app_plugins_root() / INSTALLED_FILENAME

    @staticmethod
    def safe_mode_path() -> Path:
        return fs_registry.app_plugins_root() / SAFE_MODE_FILENAME

    # -- read / write --------------------------------------------------------------

    def read(self) -> State:
        """A private copy of the current state (an empty one when the file is absent
        or unreadable)."""
        path = self.path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _empty()
        except (OSError, ValueError):
            logger.warning("installed.json is unreadable; treating it as empty", exc_info=True)
            self._set_aside(path)
            return _empty()
        if not isinstance(raw, dict) or not isinstance(raw.get("plugins"), dict):
            return _empty()
        state = _empty()
        state["generation"] = int(raw.get("generation") or 0)
        state["plugins"] = {
            k: v for k, v in raw["plugins"].items() if isinstance(k, str) and isinstance(v, dict)
        }
        return state

    @staticmethod
    def _set_aside(path: Path) -> None:
        try:
            os.replace(path, path.with_name(f"{path.name}.corrupt-{int(time.time())}"))
        except OSError:
            pass

    def _write(self, state: State) -> None:
        path = self.path()
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def generation(self) -> int:
        return int(self.read()["generation"])

    # -- transactions --------------------------------------------------------------

    def lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[State]:
        """Hold the lock, hand out the state, write it back when it changed.

        The caller raises ``state["generation"]`` itself (``bump``) when a renderer
        needs to know; a transaction that leaves the state equal writes nothing.
        """
        async with self.lock():
            state = self.read()
            before = copy.deepcopy(state)
            yield state
            if state != before:
                self._write(state)
                if state["generation"] != before["generation"]:
                    self.notify()

    @staticmethod
    def bump(state: State) -> int:
        state["generation"] = int(state["generation"]) + 1
        return int(state["generation"])

    async def bump_generation(self) -> int:
        async with self.transaction() as state:
            return self.bump(state)

    # -- watchers ------------------------------------------------------------------

    def notify(self) -> None:
        for loop, event in list(self._waiters):
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # that loop is closed
                self._waiters.discard((loop, event))

    async def wait_for_change(self, timeout: float) -> None:
        """Sleep until ``notify`` fires or ``timeout`` seconds pass."""
        if timeout <= 0:
            return
        entry = (asyncio.get_running_loop(), asyncio.Event())
        self._waiters.add(entry)
        try:
            await asyncio.wait_for(entry[1].wait(), timeout)
        except TimeoutError:
            pass
        finally:
            self._waiters.discard(entry)

    # -- safe mode -----------------------------------------------------------------

    def safe_mode(self) -> tuple[bool, str | None]:
        for name in (SAFE_MODE_ENV, LEGACY_SAFE_MODE_ENV):
            if os.environ.get(name, "").strip() in ("1", "true", "TRUE", "yes"):
                return True, f"{name}=1"
        try:
            raw = json.loads(self.safe_mode_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False, None
        if not isinstance(raw, dict):
            return True, None
        reason = raw.get("reason")
        return True, reason if isinstance(reason, str) else None

    async def set_safe_mode(self, enabled: bool, reason: str | None = None) -> bool:
        """Switch the file-backed safe mode; ``True`` when it ends up on (the
        environment variable keeps it on regardless)."""
        async with self.lock():
            path = self.safe_mode_path()
            if enabled:
                path.write_text(json.dumps({"reason": reason or ""}) + "\n", encoding="utf-8")
            else:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        await self.bump_generation()
        return self.safe_mode()[0]


installed_store = InstalledStore()

__all__ = ["InstalledStore", "State", "installed_store"]

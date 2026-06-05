from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from watchfiles import awatch

from valuz_agent.infra.eventbus import EventBus

logger = logging.getLogger(__name__)


class SkillFileWatcher:
    def __init__(self, event_bus: EventBus) -> None:
        self._bus = event_bus
        self._paths: set[Path] = set()
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

    def add_path(self, path: Path) -> None:
        self._paths.add(path)

    def remove_path(self, path: Path) -> None:
        self._paths.discard(path)

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._watch_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _watch_loop(self) -> None:
        while True:
            active_paths = [p for p in self._paths if p.exists()]
            if not active_paths:
                await asyncio.sleep(5)
                continue
            try:
                async for changes in awatch(*active_paths, debounce=300):
                    changed_dirs: set[str] = set()
                    for _change_type, changed_path in changes:
                        p = Path(changed_path)
                        for watched in active_paths:
                            try:
                                p.relative_to(watched)
                                changed_dirs.add(str(watched))
                                break
                            except ValueError:
                                continue
                    for skill_dir in changed_dirs:
                        self._bus.publish(
                            "skill.changed",
                            skill_dir=skill_dir,
                            reason="file_watcher",
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("File watcher error, restarting in 5s")
                await asyncio.sleep(5)

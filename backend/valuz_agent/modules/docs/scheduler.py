"""Background KB auto-discovery — startup scan + periodic rescan.

Runs as a daemon thread alongside the FastAPI process. Scans all KBs
with auto_discover=True on startup, then every RESCAN_INTERVAL_SEC.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

RESCAN_INTERVAL_SEC = 5 * 60  # 5 minutes


class KbAutoDiscoveryScheduler:
    def __init__(
        self,
        rescan_factory: Callable[[], None],
        interval: int = RESCAN_INTERVAL_SEC,
    ) -> None:
        self._rescan_factory = rescan_factory
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="kb-auto-discover",
            daemon=True,
        )
        self._thread.start()
        logger.info("KB auto-discovery scheduler started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("KB auto-discovery scheduler stopped")

    def _loop(self) -> None:
        self._run_once()
        while not self._stop.wait(timeout=self._interval):
            self._run_once()

    def _run_once(self) -> None:
        try:
            self._rescan_factory()
        except Exception:
            logger.exception("KB auto-discovery scan failed")


def run_auto_discovery_scan() -> None:
    """Entry point invoked from the daemon-thread scheduler loop.

    The host datastore/service are now async (aiosqlite). This thread has
    no event loop of its own, so it hosts one via ``asyncio.run`` and drives
    the async service inside it. Each KB rescan opens its own
    ``async_unit_of_work`` session so a failed rescan does not poison the
    next KB's transaction.

    ``run_in_background_db_scope`` binds a per-loop DB engine for this foreign
    loop — required under asyncpg, where the shared main-loop pool can't be
    driven from another loop (no-op on SQLite).
    """
    import asyncio

    from valuz_agent.infra.db import run_in_background_db_scope

    asyncio.run(run_in_background_db_scope(_arun_auto_discovery_scan()))


async def _arun_auto_discovery_scan() -> None:
    # Background scan across ALL owners. ``list_auto_discover_kbs`` enumerates
    # every owner's auto-discover KBs (owner-agnostic system read); each is then
    # rescanned via ``service.rescan_kb(kb_id)``, which derives the owner from the
    # KB row itself. The per-KB loop below passes that owner explicitly to
    # owner-scoped reads done BEFORE rescan_kb (e.g. load_routing_config).
    # The old ``resolve_local_user_id()`` seed only ever scanned the single
    # device id.
    #
    # The parser is the SAME configured ParserRouter the request path uses
    # (deps.get_document_service) — routing config + secret resolver + capability
    # gate — so auto-discovered files honour the user's chosen engine
    # (PaddleOCR / MinerU). Reuses process-wide singletons (registry shares the
    # main-loop PollingScheduler; async-poll parses dispatch there).
    from valuz_agent.api.deps import (
        _parser_registry,
        _SecretResolver,
        _setup_controller,
    )
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.docs.datastore import (
        DocumentDatastore,
    )
    from valuz_agent.modules.docs.service import (
        DocumentLibraryService,
    )
    from valuz_agent.modules.parser import ParserRouter
    from valuz_agent.modules.settings.parser_routing import load_routing_config
    from valuz_agent.ports.docs_runtime import get_docs_runtime

    # Snapshot the auto-discover KBs (id, name, owner) across ALL owners in one
    # short-lived session, then run each rescan in its own session below.
    # Keeping the listing read separate means a per-KB rescan failure can't
    # taint the listing transaction.
    async with async_unit_of_work(commit=False) as db:
        kb_refs = [
            (kb.id, kb.name, kb.user_id)
            for kb in await DocumentDatastore(db).list_auto_discover_kbs()
        ]

    if not kb_refs:
        return

    logger.info(
        "KB auto-discovery scanning %d KB(s) with auto_discover=True",
        len(kb_refs),
    )
    for kb_id, kb_name, owner in kb_refs:
        # Background path: no request context. Use the KB row's stored owner and
        # pass it explicitly to all owner-scoped work in this iteration.
        if owner is None:
            logger.warning(
                "KB auto-discovery skipping ownerless KB %s (%s)", kb_id, kb_name
            )
            continue
        try:
            from valuz_agent.infra.fs_registry import fs_registry

            async with async_unit_of_work(commit=False) as db:
                routing_config = await load_routing_config(db, user_id=owner)
                parser = ParserRouter(
                    registry=_parser_registry(),
                    secret_resolver=_SecretResolver(owner),
                    routing_config=routing_config,
                    setup_complete_probe=_setup_controller().is_complete,
                )
                svc = DocumentLibraryService(
                    datastore=DocumentDatastore(db),
                    parser=parser,
                    docs_runtime=get_docs_runtime(owner),
                    event_bus=event_bus,
                    scan_state_dir=fs_registry.docs_scan_state_dir(owner),
                )
                # rescan_kb derives the owner from the KB row.
                result = await svc.rescan_kb(kb_id)
            logger.info(
                "Auto-rescan completed: %s (%s) — %d new/changed files",
                kb_name,
                kb_id,
                result.total_items,
            )
        except Exception:
            # The failed unit-of-work already rolled back + closed; the next
            # KB starts from a fresh session.
            logger.exception("Auto-rescan failed: %s (%s)", kb_name, kb_id)


_scheduler: KbAutoDiscoveryScheduler | None = None


def start_auto_discovery() -> None:
    global _scheduler
    if _scheduler:
        return
    _scheduler = KbAutoDiscoveryScheduler(run_auto_discovery_scan)
    _scheduler.start()


def stop_auto_discovery() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None

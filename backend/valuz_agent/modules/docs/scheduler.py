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
    """
    import asyncio

    asyncio.run(_arun_auto_discovery_scan())


async def _arun_auto_discovery_scan() -> None:
    # Seed the owner ContextVar. This runs in the scheduler's daemon
    # THREAD (its own ``asyncio.run`` loop), which does NOT inherit the
    # main thread's ``valuz_current_user_id`` seeded at boot. Per the
    # ``infra.auth_context`` contract a background runner must set it
    # itself — otherwise the OwnedMixin ``user_id`` default raises
    # ``LookupError`` and every rescan task insert (valuz_document_import_task)
    # fails. ``resolve_local_user_id`` is process-cached, so this returns
    # the same owner id as the main thread.
    from valuz_agent.infra.auth_context import set_current_user_id
    from valuz_agent.infra.local_identity import resolve_local_user_id

    set_current_user_id(resolve_local_user_id())

    # Build the SAME configured ParserRouter the request path uses
    # (deps.get_document_service) — routing config + secret resolver +
    # capability gate — so auto-discovered files honour the user's chosen
    # engine (PaddleOCR / MinerU) instead of always falling back to
    # light_local. Reuses the process-wide singletons (registry shares the
    # main-loop PollingScheduler; async-poll parses dispatch there via
    # ParserRouter._drive_async_parse_sync).
    from valuz_agent.api.deps import (
        _parser_registry,
        _secret_store,
        _SecretStoreResolver,
        _setup_controller,
    )
    from valuz_agent.infra.config import settings
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.integrations.docs_embedded import EmbeddedDocsRuntime
    from valuz_agent.modules.docs.datastore import (
        DocumentDatastore,
    )
    from valuz_agent.modules.docs.service import (
        DocumentLibraryService,
    )
    from valuz_agent.modules.parser import ParserRouter
    from valuz_agent.modules.settings.parser_routing import load_routing_config

    # Snapshot the auto-discover KB (id, name) pairs in one short-lived
    # session, then run each rescan in its own session below. Keeping the
    # listing read separate means a per-KB rescan failure can't taint the
    # listing transaction.
    async with async_unit_of_work(commit=False) as db:
        kbs = await DocumentDatastore(db).list_kbs()
        kb_refs = [(kb.id, kb.name) for kb in kbs if kb.auto_discover]

    if not kb_refs:
        return

    preview_dir = settings.docs_dir / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "KB auto-discovery scanning %d KB(s) with auto_discover=True",
        len(kb_refs),
    )
    for kb_id, kb_name in kb_refs:
        try:
            async with async_unit_of_work(commit=False) as db:
                routing_config = await load_routing_config(db)
                parser = ParserRouter(
                    registry=_parser_registry(),
                    secret_resolver=_SecretStoreResolver(_secret_store()),
                    routing_config=routing_config,
                    setup_complete_probe=_setup_controller().is_complete,
                )
                svc = DocumentLibraryService(
                    datastore=DocumentDatastore(db),
                    parser=parser,
                    docs_runtime=EmbeddedDocsRuntime(preview_dir=preview_dir),
                    event_bus=event_bus,
                    scan_state_dir=settings.docs_dir / "scan_state",
                )
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

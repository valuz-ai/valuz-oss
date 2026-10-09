"""One-time boot migration: seed the DataService durable (host ``valuz.db``) from
the kernel's execution-local ``kernel.db``.

When the OSS default flips to "DataService is always the data layer" (reads come
from ``valuz.db``; DataService design §3 form 1), installs created *before* the
flip have their kernel history only in ``kernel.db`` — so it would read as
missing. This copies the three kernel tables ``kernel.db → valuz.db`` once so the
history stays visible.

- **sqlite-only.** If the durable and the kernel's own store resolve to the same
  file (a shared Postgres / co-located ``database_url``), or either side is not
  sqlite, there is nothing to move — return.
- **idempotent, insert-only.** Sessions already present in ``valuz.db`` are
  skipped; nothing is ever updated or deleted. Gated on a session-count check so
  it is a fast no-op on every boot after the first.
- runs **early** (right after schema bootstrap, before the durable store is read)
  so the backup is taken before long-lived engines open ``valuz.db``.
"""

from __future__ import annotations

import logging
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.store_port import StorePort

from valuz_agent.infra.db_urls import (
    db_url_async,
    kernel_db_url_async,
    sqlite_path_from_url,
)

logger = logging.getLogger(__name__)

_BACKUP_SUFFIX = ".bak-precolocate"


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


async def _copy_sessions(source: StorePort, target: StorePort) -> int:
    """Copy every source session (+messages +events) absent from target.

    Session-level idempotent: a session already in target is skipped (so events
    never double-append). Returns the number of sessions copied.
    """
    from src.core import Event

    copied = 0
    sessions = await source.list_sessions(None, limit=1_000_000)
    for s in sessions:
        uid = s.user_id
        if await target.load_session(uid, s.id) is not None:
            continue
        msgs = await source.list_messages_for_session(uid, s.id, limit=1_000_000)
        events = []
        cursor = 0
        while True:
            page = await source.get_events_after(uid, s.id, after_seq=cursor, limit=500)
            if not page:
                break
            events.extend(page)
            cursor = page[-1].seq
            if len(page) < 500:
                break
        await target.save_session(s)
        for m in msgs:
            await target.save_message(uid, m)
        for e in events:
            await target.append_event(
                uid, s.id, e.message_id, Event(type=e.type, data=e.data, timestamp=e.timestamp)
            )
        copied += 1
    return copied


async def colocate_kernel_history_into_host_db() -> None:
    """Seed ``valuz.db`` (DataService durable) from ``kernel.db`` — one-time."""
    source_url = kernel_db_url_async()  # kernel.db (execution-local)
    target_url = db_url_async()  # valuz.db (DataService durable)
    if source_url == target_url:
        return  # collapsed (shared DB) — the kernel already writes the durable
    if not (_is_sqlite(source_url) and _is_sqlite(target_url)):
        return  # PG/remote durable — not a local co-locate case

    import valuz_agent.boot.kernel as kb

    src_store, src_engine = kb.build_host_data_service_store(source_url)
    tgt_store, tgt_engine = kb.build_host_data_service_store(target_url)
    try:
        # Build the kernel tables in valuz.db (create_all, idempotent) so the copy
        # inserts into the current schema.
        await kb.ensure_host_data_service_schema(tgt_engine)
        src_sessions = await src_store.list_sessions(None, limit=1_000_000)
        if not src_sessions:
            return
        tgt_count = len(await tgt_store.list_sessions(None, limit=1_000_000))
        if tgt_count >= len(src_sessions):
            return  # already seeded — fast no-op
        # Best-effort backup before the first seed (insert-only, so low risk even
        # without it). Keep the FIRST backup if a prior run left one.
        try:
            db_path = sqlite_path_from_url(target_url)
            if db_path is not None:
                dst_path = db_path.with_name(db_path.name + _BACKUP_SUFFIX)
                if not dst_path.exists():
                    shutil.copy2(db_path, dst_path)
        except Exception:  # noqa: BLE001 — backup is best-effort; copy is insert-only
            logger.debug("colocate: valuz.db backup skipped", exc_info=True)
        copied = await _copy_sessions(src_store, tgt_store)
        logger.warning(
            "colocate: seeded %d kernel session(s) from kernel.db into valuz.db "
            "(DataService durable)",
            copied,
        )
    finally:
        await src_engine.dispose()
        await tgt_engine.dispose()

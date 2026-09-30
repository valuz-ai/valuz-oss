"""Keep the DeepAgents sqlite checkpoint store from growing quadratically.

LangGraph writes a checkpoint after every super-step, and the sqlite saver
stores each one with the FULL channel state — the whole message list, not a
delta. A turn of k steps over a history of size S therefore writes about k·S
bytes, and the store kept every one of them forever. A 7-turn cloud session
reached 396 checkpoints / 355 MB, of which the 7 turn-end checkpoints were
7.4 MB, and filled its sandbox's disk mid-turn ("database or disk is full").

Only two kinds of checkpoint are ever read back:

* a thread's LATEST — every next turn resumes from it, with its own pending
  writes;
* a run's LAST checkpoint — the runtime stamps it on the turn's message as the
  fork anchor (``runtime_native.checkpoint_id``) and it is the next turn's
  ``parent_checkpoint_id``.

An intermediate step is superseded the moment the next step of the SAME run is
written: LangGraph resumes from the newer one and loads only that checkpoint's
own pending writes. The parent is referenced by config, never read — except by
time-travel replay of ``update``/``fork`` checkpoints, which are never pruned.

So when a ``loop`` checkpoint is written, its same-namespace parent is deleted
(with its writes) iff that parent is an ``input`` or ``loop`` checkpoint. An
``input`` checkpoint — the start of a run — never prunes its parent: that is
the previous run's last checkpoint (a fork anchor, or a cancelled turn's tip).
What remains is one checkpoint per run plus the live tip.

``compact_checkpoint_store`` applies the same rule to a store written before
this existed, and reclaims the space.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)

# Checkpoint sources a later ``loop`` step of the same run supersedes.
_SUPERSEDABLE = ("input", "loop")

# VACUUM only when compaction freed at least this much: below it the free pages
# are simply reused by the next checkpoints, and VACUUM rewrites the whole file.
VACUUM_MIN_FREE_BYTES = 8 * 1024 * 1024

# ``metadata`` is JSON text stored as a BLOB. ``CAST`` keeps SQLite from reading
# it as JSONB; ``json_valid`` keeps one odd row from failing the whole statement.
_SOURCE = (
    "CASE WHEN json_valid(CAST({t}.metadata AS TEXT))"
    " THEN json_extract(CAST({t}.metadata AS TEXT), '$.source') END"
)


class PruningAsyncSqliteSaver(AsyncSqliteSaver):
    """``AsyncSqliteSaver`` that drops each step's superseded parent."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # (thread_id, checkpoint_ns) -> (checkpoint_id, source) of the last
        # checkpoint this saver wrote: the parent of the next step is almost
        # always that one, so its source is known without reading the row
        # (which means walking past its multi-MB checkpoint blob).
        self._last_written: dict[tuple[str, str], tuple[str, str | None]] = {}

    async def aput(
        self,
        config: Any,
        checkpoint: Any,
        metadata: Any,
        new_versions: Any,
    ) -> Any:
        result = await super().aput(config, checkpoint, metadata, new_versions)
        configurable = config.get("configurable") or {}
        key = (str(configurable.get("thread_id")), str(configurable.get("checkpoint_ns") or ""))
        parent_id = configurable.get("checkpoint_id")
        source = (metadata or {}).get("source")
        previous = self._last_written.get(key)
        self._last_written[key] = (str(checkpoint["id"]), source)
        if source == "loop" and parent_id:
            try:
                await self._prune_superseded(key, str(parent_id), previous)
            except Exception:  # noqa: BLE001 — pruning must never fail a step
                logger.warning(
                    "deepagents checkpoints: could not prune %s in thread %s",
                    parent_id,
                    key[0],
                    exc_info=True,
                )
        return result

    async def _prune_superseded(
        self,
        key: tuple[str, str],
        parent_id: str,
        previous: tuple[str, str | None] | None,
    ) -> None:
        thread_id, ns = key
        if previous is not None and previous[0] == parent_id:
            parent_source = previous[1]
        else:
            async with (
                self.lock,
                self.conn.execute(
                    f"SELECT {_SOURCE.format(t='checkpoints')} FROM checkpoints"
                    " WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                    (thread_id, ns, parent_id),
                ) as cur,
            ):
                row = await cur.fetchone()
            parent_source = row[0] if row else None
        if parent_source not in _SUPERSEDABLE:
            return
        async with self.lock:
            await self.conn.execute(
                "DELETE FROM writes"
                " WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                (thread_id, ns, parent_id),
            )
            await self.conn.execute(
                "DELETE FROM checkpoints"
                " WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?",
                (thread_id, ns, parent_id),
            )
            await self.conn.commit()


async def compact_checkpoint_store(
    saver: AsyncSqliteSaver, *, vacuum_min_free_bytes: int = VACUUM_MIN_FREE_BYTES
) -> dict[str, int | float]:
    """Apply the pruning rule to everything already stored, then reclaim space.

    Deletes every ``input``/``loop`` checkpoint that has a ``loop`` child in the
    same thread and namespace, plus writes whose checkpoint is gone (including
    any a pruned step received late). VACUUMs when that freed at least
    ``vacuum_min_free_bytes``. Idempotent: a pruned store deletes nothing.
    """
    started = time.monotonic()
    conn = saver.conn
    async with saver.lock:
        cur = await conn.execute(
            "DELETE FROM checkpoints WHERE (thread_id, checkpoint_ns, checkpoint_id) IN ("
            " SELECT p.thread_id, p.checkpoint_ns, p.checkpoint_id"
            " FROM checkpoints AS p JOIN checkpoints AS c"
            "  ON c.thread_id = p.thread_id AND c.checkpoint_ns = p.checkpoint_ns"
            "  AND c.parent_checkpoint_id = p.checkpoint_id"
            f" WHERE {_SOURCE.format(t='c')} = 'loop'"
            f"  AND {_SOURCE.format(t='p')} IN ('input', 'loop'))"
        )
        pruned = cur.rowcount
        cur = await conn.execute(
            "DELETE FROM writes WHERE NOT EXISTS ("
            " SELECT 1 FROM checkpoints AS c WHERE c.thread_id = writes.thread_id"
            " AND c.checkpoint_ns = writes.checkpoint_ns"
            " AND c.checkpoint_id = writes.checkpoint_id)"
        )
        orphans = cur.rowcount
        await conn.commit()
        page_size = await _pragma_int(conn, "page_size")
        free_pages = await _pragma_int(conn, "freelist_count")
        freed = free_pages * page_size
        vacuumed = freed >= vacuum_min_free_bytes
        if vacuumed:
            await conn.execute("VACUUM")
            # In WAL mode VACUUM's output lands in the WAL; the file only
            # shrinks once that is checkpointed. Do it now — the whole point
            # is handing space back to a disk that may be nearly full.
            async with conn.execute("PRAGMA wal_checkpoint(TRUNCATE)") as c3:
                await c3.fetchone()
    stats: dict[str, int | float] = {
        "pruned": pruned,
        "orphan_writes": orphans,
        "freed_bytes": freed,
        "vacuumed": int(vacuumed),
        "seconds": round(time.monotonic() - started, 3),
    }
    if pruned or orphans or vacuumed:
        logger.info("deepagents checkpoints compacted: %s", stats)
    return stats


async def _pragma_int(conn: Any, name: str) -> int:
    async with conn.execute(f"PRAGMA {name}") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


__all__ = ["PruningAsyncSqliteSaver", "compact_checkpoint_store", "VACUUM_MIN_FREE_BYTES"]

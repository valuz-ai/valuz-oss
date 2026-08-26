"""Checkpoint-thread fork for the DeepAgents runtime (docs/design/session-fork.md P1).

LangGraph has the same-thread branch primitive built in, but no OSS
cross-thread copy: ``BaseCheckpointSaver.copy_thread`` is declared upstream
(langgraph-checkpoint 4.1.1) yet unimplemented by the sqlite saver — and it
also has no cut point, which fork needs. This helper does the copy directly
against the sqlite store (the only backend in service — local and cloud
alike; the legacy ``FileCheckpointSaver`` is retired and deliberately not
wired for fork):

* **anchor cut** — ``anchor_checkpoint_id`` is the turn-end checkpoint the
  kernel stamped on the anchor message (``runtime_native.checkpoint_id``).
  The copied set is the ``parent_checkpoint_id`` chain from the anchor back
  to the root, so everything after the anchor is excluded and the target
  thread's LATEST checkpoint IS the anchor (checkpoint ids are time-ordered
  uuid6, and "latest" = highest id present).
* **tail copy** — no anchor: every checkpoint (and its pending writes) is
  copied verbatim. A source that never checkpointed (bare completion, never
  ran) copies zero rows — legal for a tail fork, an error with an anchor.

The sqlite rows don't embed the thread id inside the stored payloads (it is
a key column), so the copy is a pure re-keying — no payload rewrites.
"""

from __future__ import annotations

import sqlite3

# Stay well under SQLite's default 999 bound-parameter limit.
_IN_CHUNK = 400


def _chain_ids(parents: dict[str, str | None], anchor: str) -> list[str]:
    """The anchor's ancestor chain (anchor included), root-ward.

    ``parents`` maps checkpoint_id -> parent_checkpoint_id for one thread.
    Raises ``ValueError`` when the anchor is not in the thread — the anchor
    came from a persisted kernel message, so a miss means the checkpoint
    store diverged from the kernel history (or the wrong backend is active).
    """
    if anchor not in parents:
        raise ValueError(f"anchor checkpoint {anchor!r} not found in source thread")
    chain: list[str] = []
    cursor: str | None = anchor
    while cursor is not None and cursor in parents and cursor not in chain:
        chain.append(cursor)
        cursor = parents[cursor]
    return chain


async def fork_sqlite_thread(
    db_path: str,
    source_thread_id: str,
    target_thread_id: str,
    *,
    anchor_checkpoint_id: str | None = None,
) -> int:
    """Copy a thread's checkpoint rows under a new thread id. Returns the
    number of checkpoints copied."""
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        try:
            rows = await db.execute_fetchall(
                "SELECT checkpoint_id, parent_checkpoint_id FROM checkpoints WHERE thread_id = ?",
                (source_thread_id,),
            )
        except sqlite3.OperationalError:
            # No checkpoints table — the store was never written to.
            rows = []
        parents = {str(cid): (str(parent) if parent else None) for cid, parent in rows}

        if anchor_checkpoint_id is not None:
            ids: list[str] | None = _chain_ids(parents, anchor_checkpoint_id)
        else:
            ids = None  # whole thread
            if not parents:
                return 0

        checkpoint_cols = (
            "checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata"
        )
        write_cols = "checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value"
        if ids is None:
            await db.execute(
                f"INSERT OR IGNORE INTO checkpoints (thread_id, {checkpoint_cols})"
                f" SELECT ?, {checkpoint_cols} FROM checkpoints WHERE thread_id = ?",
                (target_thread_id, source_thread_id),
            )
            await db.execute(
                f"INSERT OR IGNORE INTO writes (thread_id, {write_cols})"
                f" SELECT ?, {write_cols} FROM writes WHERE thread_id = ?",
                (target_thread_id, source_thread_id),
            )
            copied = len(parents)
        else:
            for start in range(0, len(ids), _IN_CHUNK):
                chunk = ids[start : start + _IN_CHUNK]
                marks = ",".join("?" for _ in chunk)
                await db.execute(
                    f"INSERT OR IGNORE INTO checkpoints (thread_id, {checkpoint_cols})"
                    f" SELECT ?, {checkpoint_cols} FROM checkpoints"
                    f" WHERE thread_id = ? AND checkpoint_id IN ({marks})",
                    (target_thread_id, source_thread_id, *chunk),
                )
                await db.execute(
                    f"INSERT OR IGNORE INTO writes (thread_id, {write_cols})"
                    f" SELECT ?, {write_cols} FROM writes"
                    f" WHERE thread_id = ? AND checkpoint_id IN ({marks})",
                    (target_thread_id, source_thread_id, *chunk),
                )
            copied = len(ids)
        await db.commit()
        return copied

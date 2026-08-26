"""RuntimeStore — the kernel's ONE store: sqlite runtime authority + DataService dual-write.

The kernel has exactly one storage behaviour, everywhere it runs (the OSS local
process and the cloud sandbox are IDENTICAL):

- **``runtime`` — local sqlite (``kernel.db``) is the SOLE runtime persistence
  source.** Every kernel read and write (turn state, event append + the seq the
  broadcast carries, WS replay, pending derivation, own-lineage boot scans)
  operates on it, synchronously and authoritatively. The kernel has NO remote
  read path.

- **``mirror`` — every write is dual-written to the DataService.** After the
  local write commits, the same op is pushed to the DataService backend —
  locally that backend is the host ``valuz.db``; in the commercial cloud it is
  the configured central Postgres (over HTTP, JWT — the sandbox never holds a
  DSN). The mirror is sequential-inline (local commit order = mirror arrival
  order) and BEST-EFFORT: a mirror failure is logged and never blocks the
  runtime (recovery/reconciliation of a lagging mirror is a separate, explicit
  next step — not this class's job).

- **Seqs are per-store; ``event_uid`` bridges identity.** ``append_event``
  returns the LOCAL seq (the kernel's broadcast + replay cursor). The durable
  assigns its OWN seq on arrival; history readers (the host) use that one. The
  two sequences are never compared — cross-store identity/dedup is the
  ``event_uid`` (the append ``request_id``), which also makes mirror redelivery
  idempotent.

The redundancy of dual-writing on a single machine is deliberate: behaviour is
uniform across deployments, so there is nothing deployment-specific to reason
about. Structurally satisfies ``src.core.StorePort``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from src.core.events import Event
from src.core.store_port import StoredEvent, StorePort, UsageRollupRow
from src.core.types import Message, Session

logger = logging.getLogger(__name__)


class RuntimeStore:
    def __init__(self, runtime: StorePort, mirror: StorePort) -> None:
        self._runtime = runtime
        self._mirror = mirror

    # ---- writes: runtime first (authoritative), then the dual-write mirror --

    async def save_session(self, session: Session) -> None:
        await self._runtime.save_session(session)
        await self._mirror_op("save_session", self._mirror.save_session(session))

    async def save_message(self, user_id: str, message: Message) -> None:
        await self._runtime.save_message(user_id, message)
        await self._mirror_op("save_message", self._mirror.save_message(user_id, message))

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        deleted = await self._runtime.delete_session(user_id, session_id)
        await self._mirror_op("delete_session", self._mirror.delete_session(user_id, session_id))
        return deleted

    async def append_event(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
        event: Event,
        *,
        request_id: str | None = None,
        seq: int | None = None,
    ) -> int | None:
        # One identity (= the event_uid) for both copies: mirror redelivery is
        # idempotent, and consumers merge local-seq live frames with
        # durable-seq history by uid.
        rid = request_id or uuid.uuid4().hex
        local_seq = await self._runtime.append_event(
            user_id, session_id, message_id, event, request_id=rid
        )
        await self._mirror_op(
            "append_event",
            self._mirror.append_event(user_id, session_id, message_id, event, request_id=rid),
        )
        return local_seq

    async def _mirror_op(self, op: str, coro) -> None:
        """Push one op to the DataService mirror — best-effort by design.

        The runtime copy is already committed; a mirror failure must never
        break the turn. Lost mirror ops are a known, logged gap until the
        explicit recovery step lands (next iteration)."""
        try:
            await coro
        except Exception as exc:  # noqa: BLE001 — availability over mirror freshness
            logger.warning("DataService mirror failed (op=%s): %s", op, exc)

    # ---- reads: the runtime sqlite is the sole source ----------------------

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        return await self._runtime.load_session(user_id, session_id)

    async def list_sessions(
        self,
        user_id: str | None,
        *,
        status: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Session]:
        return await self._runtime.list_sessions(
            user_id, status=status, ids=ids, limit=limit, offset=offset
        )

    async def load_message(self, user_id: str, message_id: str) -> Message | None:
        return await self._runtime.load_message(user_id, message_id)

    async def list_messages_for_session(
        self, user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Message]:
        return await self._runtime.list_messages_for_session(
            user_id, session_id, limit=limit, offset=offset
        )

    async def get_events(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        types: Sequence[str] | None = None,
    ) -> list[Event]:
        return await self._runtime.get_events(
            user_id, session_id, limit=limit, offset=offset, types=types
        )

    async def get_events_for_message(
        self, user_id: str, message_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        return await self._runtime.get_events_for_message(
            user_id, message_id, limit=limit, offset=offset
        )

    async def get_events_after(
        self, user_id: str, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]:
        return await self._runtime.get_events_after(
            user_id, session_id, after_seq=after_seq, limit=limit
        )

    async def get_events_after_for_user(
        self,
        user_id: str,
        *,
        after_seq: int = 0,
        types: tuple[str, ...] | None = None,
        limit: int = 200,
    ) -> list[StoredEvent]:
        return await self._runtime.get_events_after_for_user(
            user_id, after_seq=after_seq, types=types, limit=limit
        )

    async def get_events_window(
        self, user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> tuple[list[StoredEvent], bool]:
        return await self._runtime.get_events_window(
            user_id, session_id, before_seq=before_seq, turn_limit=turn_limit
        )

    async def usage_rollup(self, user_id: str, start_ms: int, end_ms: int) -> list[UsageRollupRow]:
        return await self._runtime.usage_rollup(user_id, start_ms, end_ms)

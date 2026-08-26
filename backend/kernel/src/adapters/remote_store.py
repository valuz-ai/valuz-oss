"""RemoteStore — StorePort over an authenticated remote data API.

Bound ONLY in the sandbox/remote deployment (``KERNEL_STORE=remote``): the
kernel process then holds **no database connection** — no DSN, no driver, no
PG credentials. Every ``StorePort`` call becomes an authenticated request to a
trusted remote data service that owns the database and derives the owner from
the request's *verified token* (never from a caller-supplied field). The
sandbox carries only a short-lived JWT + the data-API URL.

This module is the transport-AGNOSTIC half. The abstract :class:`RemoteStore`
owns the cross-cutting policy every remote backend needs:

- **retry** of transient failures only (timeouts / connection errors / 5xx),
  exponential backoff + jitter; a definitive (4xx) failure never retries.
- **idempotency**: each write carries a single ``request_id`` generated ONCE
  before the retry loop and reused across attempts, so an at-least-once retry
  is effectively-once on the server (``append_event`` must return the original
  ``seq`` on a duplicate, never insert twice).
- **fail-loud**: retry exhaustion raises :class:`RemoteFatalError` — a durable
  write is never silently dropped.
- **token refresh**: a per-call ``access_token`` hook supplies a fresh bearer
  so a long turn never fails on expiry.

A concrete backend (PostgREST in Phase B; a self-hosted data service in the T1
fallback) subclasses this and implements only the single-shot ``_*_once``
transport methods. Append ordering: events within one run are emitted serially
by the sink (it awaits each ``emit`` before the next), and a retry blocks the
next append, so ``seq`` stays monotonic without a cross-session lock here.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from src.core.events import Event
from src.core.store_port import StoredEvent, UsageRollupRow
from src.core.types import Message, Session

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

AccessTokenHook = Callable[[], Awaitable[str]]
"""Returns the current bearer (short-lived JWT). Called per request so a
refresh implementation can re-mint before expiry. The sandbox holds no
signing secret — the hook fetches/returns an already-signed token."""


class RemoteStoreError(RuntimeError):
    """Base for remote-store transport failures."""


class RemoteTransientError(RemoteStoreError):
    """A retryable failure — timeout / connection error / 5xx. Subclasses
    raise this for conditions where a retry (with the same idempotency key)
    is safe."""


class RemoteFatalError(RemoteStoreError):
    """A non-retryable failure — 4xx, contract violation, or retry
    exhaustion. Propagates immediately; never swallowed."""


class RemoteStore(abc.ABC):
    """Abstract StorePort backed by a remote data API.

    Structurally satisfies ``src.core.StorePort`` (it implements every
    method); it does not inherit the Protocol to avoid an ABCMeta/_ProtocolMeta
    metaclass clash. Subclasses implement only the ``_*_once`` single-shot
    transport methods; this base wraps them with retry + idempotency + fail-loud.
    """

    def __init__(
        self,
        *,
        access_token: AccessTokenHook,
        max_attempts: int = 5,
        base_backoff_s: float = 0.1,
        max_backoff_s: float = 5.0,
    ) -> None:
        self._access_token = access_token
        self._max_attempts = max(1, max_attempts)
        self._base_backoff_s = base_backoff_s
        self._max_backoff_s = max_backoff_s

    async def _bearer(self) -> str:
        """The current bearer token for a request (refreshed by the hook)."""
        return await self._access_token()

    @staticmethod
    def _new_request_id() -> str:
        """A unique idempotency key, generated ONCE per logical write and
        reused across that write's retries."""
        return uuid.uuid4().hex

    async def _retry(self, op: str, fn: Callable[[], Awaitable[_T]]) -> _T:
        """Run ``fn`` with bounded retry on :class:`RemoteTransientError`.

        Non-transient errors propagate immediately (no retry). On exhaustion,
        raise :class:`RemoteFatalError` — fail-loud, never swallow.
        """
        last: BaseException | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await fn()
            except RemoteTransientError as exc:
                last = exc
                if attempt >= self._max_attempts:
                    break
                base = min(self._max_backoff_s, self._base_backoff_s * (2 ** (attempt - 1)))
                delay = base + random.uniform(0.0, base)  # full-ish jitter
                logger.warning(
                    "remote store %s transient failure (attempt %d/%d): %s; retry in %.2fs",
                    op,
                    attempt,
                    self._max_attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
        raise RemoteFatalError(
            f"remote store {op!r} failed after {self._max_attempts} attempts: {last}"
        ) from last

    # ------------------------------------------------------------------ #
    # StorePort surface — writes (idempotent via request_id) + reads.    #
    # ------------------------------------------------------------------ #

    async def save_session(self, session: Session) -> None:
        rid = self._new_request_id()
        await self._retry("save_session", lambda: self._save_session_once(session, request_id=rid))

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        return await self._retry(
            "load_session", lambda: self._load_session_once(user_id, session_id)
        )

    async def list_sessions(
        self,
        user_id: str | None,
        *,
        status: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Session]:
        return await self._retry(
            "list_sessions",
            lambda: self._list_sessions_once(
                user_id, status=status, ids=ids, limit=limit, offset=offset
            ),
        )

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        rid = self._new_request_id()
        return await self._retry(
            "delete_session", lambda: self._delete_session_once(user_id, session_id, request_id=rid)
        )

    async def save_message(self, user_id: str, message: Message) -> None:
        rid = self._new_request_id()
        await self._retry(
            "save_message", lambda: self._save_message_once(user_id, message, request_id=rid)
        )

    async def load_message(self, user_id: str, message_id: str) -> Message | None:
        return await self._retry(
            "load_message", lambda: self._load_message_once(user_id, message_id)
        )

    async def list_messages_for_session(
        self, user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Message]:
        return await self._retry(
            "list_messages_for_session",
            lambda: self._list_messages_for_session_once(
                user_id, session_id, limit=limit, offset=offset
            ),
        )

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
        rid = request_id or self._new_request_id()  # RuntimeStore passes the shared event_uid
        return await self._retry(
            "append_event",
            lambda: self._append_event_once(
                user_id, session_id, message_id, event, request_id=rid, seq=seq
            ),
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
        return await self._retry(
            "get_events",
            lambda: self._get_events_once(
                user_id, session_id, limit=limit, offset=offset, types=types
            ),
        )

    async def get_events_for_message(
        self, user_id: str, message_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        return await self._retry(
            "get_events_for_message",
            lambda: self._get_events_for_message_once(
                user_id, message_id, limit=limit, offset=offset
            ),
        )

    async def get_events_after(
        self, user_id: str, session_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[StoredEvent]:
        return await self._retry(
            "get_events_after",
            lambda: self._get_events_after_once(
                user_id, session_id, after_seq=after_seq, limit=limit
            ),
        )

    async def get_events_after_for_user(
        self,
        user_id: str,
        *,
        after_seq: int = 0,
        types: tuple[str, ...] | None = None,
        limit: int = 200,
    ) -> list[StoredEvent]:
        return await self._retry(
            "get_events_after_for_user",
            lambda: self._get_events_after_for_user_once(
                user_id, after_seq=after_seq, types=types, limit=limit
            ),
        )

    async def get_events_window(
        self, user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> tuple[list[StoredEvent], bool]:
        return await self._retry(
            "get_events_window",
            lambda: self._get_events_window_once(
                user_id, session_id, before_seq=before_seq, turn_limit=turn_limit
            ),
        )

    async def usage_rollup(self, user_id: str, start_ms: int, end_ms: int) -> list[UsageRollupRow]:
        return await self._retry(
            "usage_rollup", lambda: self._usage_rollup_once(user_id, start_ms, end_ms)
        )

    # ------------------------------------------------------------------ #
    # Single-shot transport hooks — subclass implements exactly these.   #
    # Raise RemoteTransientError (retryable) / RemoteFatalError (not).    #
    # ------------------------------------------------------------------ #

    @abc.abstractmethod
    async def _save_session_once(self, session: Session, *, request_id: str) -> None: ...

    @abc.abstractmethod
    async def _load_session_once(self, user_id: str, session_id: str) -> Session | None: ...

    @abc.abstractmethod
    async def _list_sessions_once(
        self,
        user_id: str | None,
        *,
        status: str | None,
        ids: Sequence[str] | None,
        limit: int,
        offset: int,
    ) -> list[Session]: ...

    @abc.abstractmethod
    async def _delete_session_once(
        self, user_id: str, session_id: str, *, request_id: str
    ) -> bool: ...

    @abc.abstractmethod
    async def _save_message_once(
        self, user_id: str, message: Message, *, request_id: str
    ) -> None: ...

    @abc.abstractmethod
    async def _load_message_once(self, user_id: str, message_id: str) -> Message | None: ...

    @abc.abstractmethod
    async def _list_messages_for_session_once(
        self, user_id: str, session_id: str, *, limit: int, offset: int
    ) -> list[Message]: ...

    @abc.abstractmethod
    async def _append_event_once(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
        event: Event,
        *,
        request_id: str,
        seq: int | None = None,
    ) -> int | None: ...

    @abc.abstractmethod
    async def _get_events_once(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int,
        offset: int,
        types: Sequence[str] | None = None,
    ) -> list[Event]: ...

    @abc.abstractmethod
    async def _get_events_for_message_once(
        self, user_id: str, message_id: str, *, limit: int, offset: int
    ) -> list[Event]: ...

    @abc.abstractmethod
    async def _get_events_after_once(
        self, user_id: str, session_id: str, *, after_seq: int, limit: int
    ) -> list[StoredEvent]: ...

    @abc.abstractmethod
    async def _get_events_after_for_user_once(
        self, user_id: str, *, after_seq: int, types: tuple[str, ...] | None, limit: int
    ) -> list[StoredEvent]: ...

    @abc.abstractmethod
    async def _get_events_window_once(
        self, user_id: str, session_id: str, *, before_seq: int | None, turn_limit: int
    ) -> tuple[list[StoredEvent], bool]: ...

    @abc.abstractmethod
    async def _usage_rollup_once(
        self, user_id: str, start_ms: int, end_ms: int
    ) -> list[UsageRollupRow]: ...


# ---------------------------------------------------------------------------
# Backend registry — concrete backends self-register on import; the dependency
# layer selects one by ``kind`` (Phase B registers ``postgrest``).
# ---------------------------------------------------------------------------

_BACKENDS: dict[str, Callable[..., RemoteStore]] = {}


def register_remote_backend(kind: str, factory: Callable[..., RemoteStore]) -> None:
    """Register a concrete RemoteStore backend under ``kind``."""
    _BACKENDS[kind] = factory


def build_remote_store(*, kind: str, **kwargs: Any) -> RemoteStore:
    """Construct the registered backend for ``kind``.

    Raises :class:`RemoteFatalError` if no backend is registered (e.g. the
    backend module was not imported) so misconfiguration fails loudly.
    """
    try:
        factory = _BACKENDS[kind]
    except KeyError:
        raise RemoteFatalError(
            f"no remote store backend registered for kind={kind!r}; available={sorted(_BACKENDS)}"
        ) from None
    return factory(**kwargs)

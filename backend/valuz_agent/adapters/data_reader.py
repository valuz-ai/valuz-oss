"""The host's READ seam for kernel data (sessions / events) — one typed port.

DataService design §5: session / message / event reads are served by the
DataService backend, never the execution-local sqlite, so a dead/ephemeral
sandbox still serves history. Every host read path goes through this single
seam.

It is a **port** (typed Protocol) bound at the composition root, not a hardwired
import: OSS binds :class:`~valuz_agent.adapters.data_service_local.LocalDataServiceReader`
when a durable DataService is configured; otherwise reads fall back to the kernel
seam. A SaaS build that embeds OSS as a submodule can ``bind_data_reader`` its own
implementation (a split-host HTTP reader, a tenant-scoped reader) with zero edits
to any call site — the same ports/integrations edition-divergence discipline the
rest of the backend uses.
"""

from __future__ import annotations

# ruff: noqa: I001 — the kernel side-effect import must precede ``app.*``.

from collections.abc import Sequence
from typing import Any, Protocol

import valuz_agent.boot.kernel  # noqa: F401  (sys.path side-effect)

from app.schemas import SessionData  # noqa: E402


class DataReader(Protocol):
    """The read surface host code depends on (sessions/messages/events)."""

    async def get_session(self, user_id: str, session_id: str) -> SessionData | None: ...

    async def list_sessions(
        self,
        user_id: str,
        *,
        status: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]: ...

    async def list_all_sessions(
        self,
        *,
        status: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]:
        """Cross-owner sweep (host aggregators / startup recovery / owner
        resolution). Served from the durable store directly when a host reader is
        bound, so it never depends on any per-user kernel being alive."""
        ...

    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Any]: ...

    async def get_events(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        after_seq: int | None = None,
    ) -> list[Any]: ...

    async def get_events_after_for_user(
        self,
        user_id: str,
        *,
        after_seq: int = 0,
        types: tuple[str, ...] | None = None,
        limit: int = 200,
    ) -> list[Any]:
        """Cross-session cursor read for the user-level control-plane stream —
        ALL of one owner's events after ``after_seq``, optionally restricted to
        ``types``. Backs ``iter_user_events_sse``'s backfill."""
        ...

    async def get_events_window(
        self,
        user_id: str,
        session_id: str,
        *,
        before_seq: int | None = None,
        turn_limit: int = 20,
    ) -> Any: ...


class _KernelClientReader:
    """Default :class:`DataReader` — the kernel seam.

    Used when no host durable reader is bound: local-only (the in-process kernel
    store IS the data layer) or a sandbox kernel with its own sqlite (reached over
    HTTP). Thin delegate onto the module-level ``kernel_client`` read functions,
    whose signatures already match :class:`DataReader` 1:1.
    """

    async def get_session(self, user_id: str, session_id: str) -> SessionData | None:
        from valuz_agent.adapters import kernel_client

        return await kernel_client.get_session(user_id, session_id)

    async def list_sessions(
        self,
        user_id: str,
        *,
        status: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]:
        from valuz_agent.adapters import kernel_client

        return await kernel_client.list_sessions(
            user_id,
            status=status,
            ids=list(ids) if ids is not None else None,
            limit=limit,
            offset=offset,
        )

    async def list_all_sessions(
        self,
        *,
        status: str | None = None,
        ids: Sequence[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]:
        from valuz_agent.adapters import kernel_client

        return await kernel_client.list_all_sessions(
            status=status,
            ids=list(ids) if ids is not None else None,
            limit=limit,
            offset=offset,
        )

    async def get_events(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        after_seq: int | None = None,
    ) -> list[Any]:
        from valuz_agent.adapters import kernel_client

        return await kernel_client.get_events(
            user_id, session_id, limit=limit, offset=offset, after_seq=after_seq
        )

    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Any]:
        from valuz_agent.adapters import kernel_client

        return await kernel_client.list_messages(user_id, session_id, limit=limit, offset=offset)

    async def get_events_after_for_user(
        self,
        user_id: str,
        *,
        after_seq: int = 0,
        types: tuple[str, ...] | None = None,
        limit: int = 200,
    ) -> list[Any]:
        # The user-level control-plane stream needs a durable, cross-session
        # read. The per-session kernel seam has no such route, and OSS always
        # binds a durable ``DataReader`` (``LocalDataServiceReader`` in local
        # mode, ``DataServiceReadClient`` in remote) before the stream is
        # reachable — so this fallback is never the live path. Fail loud rather
        # than silently degrade to per-session fan-out.
        raise NotImplementedError(
            "get_events_after_for_user requires a bound durable DataReader; "
            "the kernel-seam fallback does not serve cross-session user reads"
        )

    async def get_events_window(
        self,
        user_id: str,
        session_id: str,
        *,
        before_seq: int | None = None,
        turn_limit: int = 20,
    ) -> Any:
        from valuz_agent.adapters import kernel_client

        return await kernel_client.get_events_window(
            user_id, session_id, before_seq=before_seq, turn_limit=turn_limit
        )


_DEFAULT: DataReader = _KernelClientReader()
_reader: DataReader | None = None


def bind_data_reader(reader: DataReader | None) -> None:
    """Composition-root hook: bind the host read seam (or clear with ``None``).

    OSS binds a ``LocalDataServiceReader`` when a durable DataService is
    configured; a SaaS overlay binds its own ``DataReader`` here.
    """
    global _reader  # noqa: PLW0603
    _reader = reader


def data_reader() -> DataReader:
    """The bound host read seam, or the kernel-seam default (local-only/sandbox)."""
    return _reader if _reader is not None else _DEFAULT

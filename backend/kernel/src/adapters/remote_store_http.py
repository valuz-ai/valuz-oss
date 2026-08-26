"""RemoteStoreHttp — the T1 ``RemoteStore`` backend (own thin data service).

Talks to the kernel **data service** (``app.data_service``) over HTTP with one
RPC endpoint per StorePort method (``POST /rpc/{op}``). The sandbox holds only
a short-lived JWT (the ``access_token`` hook) + the service URL — **no DB
connection**. The owner is NEVER sent on the wire: it is derived server-side
from the verified token, so a compromised sandbox cannot act as another owner.

Serialization is the shared :mod:`src.adapters.store_wire` codec (one contract
for client + server). Transient HTTP failures (timeouts / connection errors /
5xx) raise :class:`RemoteTransientError` so the base retries with the same
idempotency key; 4xx raise :class:`RemoteFatalError` (no retry).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
from src.adapters import store_wire as sw
from src.adapters.remote_store import (
    AccessTokenHook,
    RemoteFatalError,
    RemoteStore,
    RemoteTransientError,
    register_remote_backend,
)
from src.core.events import Event
from src.core.store_port import StoredEvent, UsageRollupRow
from src.core.types import Message, Session


class RemoteStoreHttp(RemoteStore):
    """StorePort over our own data service (T1)."""

    def __init__(
        self,
        *,
        base_url: str,
        access_token: AccessTokenHook,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        **retry_kw: Any,
    ) -> None:
        super().__init__(access_token=access_token, **retry_kw)
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _post(self, op: str, body: dict[str, Any]) -> Any:
        """One RPC round-trip. Returns the ``data`` field; classifies failures."""
        token = await self._bearer()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = await self._http.post(f"/rpc/{op}", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise RemoteTransientError(f"{op}: timeout: {exc}") from exc
        except httpx.TransportError as exc:  # connect/read/write/proxy errors
            raise RemoteTransientError(f"{op}: transport error: {exc}") from exc
        if resp.status_code >= 500:
            raise RemoteTransientError(f"{op}: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise RemoteFatalError(f"{op}: HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        return payload.get("data")

    # -- writes --

    async def _save_session_once(self, session: Session, *, request_id: str) -> None:
        await self._post("save_session", {"session": sw.session_to_row(session)})

    async def _save_message_once(self, user_id: str, message: Message, *, request_id: str) -> None:
        await self._post("save_message", {"message": sw.message_to_row(message)})

    async def _append_event_once(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
        event: Event,
        *,
        request_id: str,
        seq: int | None = None,
    ) -> int | None:
        body: dict[str, Any] = {
            "session_id": session_id,
            "message_id": message_id,
            "event": sw.event_to_row(event),
            "request_id": request_id,
        }
        if seq is not None:
            body["seq"] = seq
        data = await self._post("append_event", body)
        return int(data) if data is not None else None

    async def _delete_session_once(self, user_id: str, session_id: str, *, request_id: str) -> bool:
        data = await self._post("delete_session", {"session_id": session_id})
        return bool(data)

    # -- reads --

    async def _load_session_once(self, user_id: str, session_id: str) -> Session | None:
        data = await self._post("load_session", {"session_id": session_id})
        return sw.row_to_session(data) if data else None

    async def _list_sessions_once(
        self,
        user_id: str | None,
        *,
        status: str | None,
        ids: Sequence[str] | None,
        limit: int,
        offset: int,
    ) -> list[Session]:
        data = await self._post(
            "list_sessions",
            {
                "status": status,
                "ids": list(ids) if ids is not None else None,
                "limit": limit,
                "offset": offset,
            },
        )
        return [sw.row_to_session(r) for r in (data or [])]

    async def _load_message_once(self, user_id: str, message_id: str) -> Message | None:
        data = await self._post("load_message", {"message_id": message_id})
        return sw.row_to_message(data) if data else None

    async def _list_messages_for_session_once(
        self, user_id: str, session_id: str, *, limit: int, offset: int
    ) -> list[Message]:
        data = await self._post(
            "list_messages_for_session",
            {"session_id": session_id, "limit": limit, "offset": offset},
        )
        return [sw.row_to_message(r) for r in (data or [])]

    async def _get_events_once(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int,
        offset: int,
        types: Sequence[str] | None = None,
    ) -> list[Event]:
        body: dict[str, Any] = {"session_id": session_id, "limit": limit, "offset": offset}
        # Only send ``types`` when set so the wire stays byte-identical for
        # the common unfiltered read (and older data services ignore it).
        if types is not None:
            body["types"] = list(types)
        data = await self._post("get_events", body)
        return [sw.row_to_event(r) for r in (data or [])]

    async def _get_events_for_message_once(
        self, user_id: str, message_id: str, *, limit: int, offset: int
    ) -> list[Event]:
        data = await self._post(
            "get_events_for_message", {"message_id": message_id, "limit": limit, "offset": offset}
        )
        return [sw.row_to_event(r) for r in (data or [])]

    async def _get_events_after_once(
        self, user_id: str, session_id: str, *, after_seq: int, limit: int
    ) -> list[StoredEvent]:
        data = await self._post(
            "get_events_after", {"session_id": session_id, "after_seq": after_seq, "limit": limit}
        )
        return [sw.row_to_stored_event(r) for r in (data or [])]

    async def _get_events_after_for_user_once(
        self, user_id: str, *, after_seq: int, types: tuple[str, ...] | None, limit: int
    ) -> list[StoredEvent]:
        body: dict[str, Any] = {"after_seq": after_seq, "limit": limit}
        if types is not None:
            body["types"] = list(types)
        data = await self._post("get_events_after_for_user", body)
        return [sw.row_to_stored_event(r) for r in (data or [])]

    async def _get_events_window_once(
        self, user_id: str, session_id: str, *, before_seq: int | None, turn_limit: int
    ) -> tuple[list[StoredEvent], bool]:
        data = (
            await self._post(
                "get_events_window",
                {"session_id": session_id, "before_seq": before_seq, "turn_limit": turn_limit},
            )
            or {}
        )
        events = [sw.row_to_stored_event(r) for r in data.get("events", [])]
        return events, bool(data.get("has_more", False))

    async def _usage_rollup_once(
        self, user_id: str, start_ms: int, end_ms: int
    ) -> list[UsageRollupRow]:
        data = await self._post("usage_rollup", {"start_ms": start_ms, "end_ms": end_ms})
        return [sw.row_to_usage_rollup(r) for r in (data or [])]


def _factory(*, base_url: str, access_token: AccessTokenHook, **kw: Any) -> RemoteStoreHttp:
    return RemoteStoreHttp(base_url=base_url, access_token=access_token, **kw)


# Self-register so ``build_remote_store(kind="http", ...)`` resolves on import.
register_remote_backend("http", _factory)

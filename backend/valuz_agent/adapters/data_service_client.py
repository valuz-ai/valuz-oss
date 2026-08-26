"""Host-side read client for the kernel DataService (``remote`` tier).

In remote mode the kernel runs in an **ephemeral** sandbox and the central
DataService (Postgres) is the system of record. When the sandbox is gone the
host can't reach the kernel — but it can read history straight from the
DataService over its ``POST /rpc/{op}`` surface. This client implements the
read subset the SSE adapter needs and returns the SAME ``app.schemas`` wire
types (``EventData`` / ``EventWindowData``) the ``KernelClient`` seam yields, so
``event_sse_adapter`` consumes either transport interchangeably.

Read-only. The owner is derived server-side from the bearer token (a JWT minted
for the local user); the body ``user_id`` is ignored by the service, matching
the kernel's own owner-from-token rule. The host never sends a DB credential —
only the configured ``kernel_data_api_url`` + ``kernel_data_api_token``.

This lives in ``adapters/`` (the kernel seam) and may import ``app.schemas``
like ``kernel_client``; it must NOT import kernel ``src.adapters`` internals
(store_wire etc.), so it maps the rpc row dicts to ``EventData`` by hand.
"""

from __future__ import annotations

from typing import Any

import httpx
from app.schemas import EventData, EventWindowData


class DataServiceReadClient:
    """Reads kernel event history directly from the DataService."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout
        )
        self._token = token or ""

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _post(self, op: str, body: dict[str, Any]) -> Any:
        resp = await self._http.post(
            f"/rpc/{op}", json=body, headers={"Authorization": f"Bearer {self._token}"}
        )
        resp.raise_for_status()
        return resp.json().get("data")

    @staticmethod
    def _row_to_event(row: dict[str, Any]) -> EventData:
        return EventData(
            type=row["type"],
            data=row.get("data") or {},
            timestamp=int(row.get("timestamp") or 0),
            seq=row.get("seq"),
            message_id=row.get("message_id"),
            event_uid=row.get("event_uid"),
            session_id=row.get("session_id"),
        )

    async def get_events(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        after_seq: int | None = None,
    ) -> list[EventData]:
        # The SSE adapter reads forward by cursor; map to the durable's
        # seq-bearing ``get_events_after`` (``offset`` is unused on this path).
        data = await self._post(
            "get_events_after",
            {"session_id": session_id, "after_seq": after_seq or 0, "limit": limit},
        )
        return [self._row_to_event(r) for r in (data or [])]

    async def get_events_after_for_user(
        self,
        user_id: str,
        *,
        after_seq: int = 0,
        types: tuple[str, ...] | None = None,
        limit: int = 200,
    ) -> list[EventData]:
        body: dict[str, Any] = {"after_seq": after_seq, "limit": limit}
        if types is not None:
            body["types"] = list(types)
        data = await self._post("get_events_after_for_user", body)
        return [self._row_to_event(r) for r in (data or [])]

    async def get_events_window(
        self,
        user_id: str,
        session_id: str,
        *,
        before_seq: int | None = None,
        turn_limit: int = 20,
    ) -> EventWindowData:
        data = (
            await self._post(
                "get_events_window",
                {"session_id": session_id, "before_seq": before_seq, "turn_limit": turn_limit},
            )
            or {}
        )
        return EventWindowData(
            items=[self._row_to_event(r) for r in data.get("events", [])],
            has_more=bool(data.get("has_more", False)),
        )

    async def list_messages(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Any]:
        # Message rows use the DataService's storage projection. Session detail
        # aggregation only needs the four normalized token buckets, so keep the
        # remote reader independent of kernel ORM/domain serializers.
        data = await self._post(
            "list_messages_for_session",
            {"session_id": session_id, "limit": limit, "offset": offset},
        )
        return [
            {
                "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "cache_read_tokens": row.get("cache_read_tokens"),
                "cache_write_tokens": row.get("cache_write_tokens"),
            }
            for row in (data or [])
        ]

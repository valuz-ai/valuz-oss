"""HttpKernelClient — the remote transport for the ``KernelClient`` seam.

Speaks the kernel's HTTP/WS/SSE API over the network; method-for-method
identical to ``InProcessKernelClient`` (same wire schemas, same error
mapping), so the host can address a kernel running as a separate process
— bare subprocess today, sandboxed/cloud later — by flipping
``VALUZ_KERNEL_MODE=http`` (+ ``VALUZ_KERNEL_URL`` / ``VALUZ_KERNEL_TOKEN``).

Notes on the two non-REST channels:

- ``run_turn`` drives the WS ``/run`` channel: send one user message,
  consume event frames until the turn-terminal ``session_idle`` /
  ``session_error``, then read back the turn's Message row via REST.
- ``subscribe_*`` consume the kernel's SSE streams and yield wire
  ``EventData`` frames, mirroring the in-process bus taps.

The in-process-only supervision hooks (``scan_orphan_*``,
``cleanup_runtime``) are deliberately absent: a standalone kernel runs
its own orphan scans at startup and owns its runtime cache.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, NoReturn

import httpx

import valuz_agent.boot.kernel  # noqa: F401  (sys.path side-effect)

from app.schemas import (  # noqa: E402
    CreateSessionRequest,
    EventData,
    EventPayload,
    EventWindowData,
    FinalizeSessionRequest,
    MessageData,
    SessionData,
    SubmitActionRequest,
    UpdateSessionRequest,
    UsageRollupData,
)

from valuz_agent.adapters.kernel_client import (  # noqa: E402
    KernelBadRequestError,
    KernelClientError,
    KernelConflictError,
    KernelGoneError,
    KernelNotImplementedError,
    KernelSessionNotFoundError,
    KernelUnavailableError,
)

# Events that end a turn on the WS run channel. ``session_update`` with a
# terminal status also closes turns for some runtimes, but every runtime
# emits exactly one of these two as its final frame.
_TURN_TERMINAL_EVENTS = frozenset({"session_idle", "session_error"})


def _raise_for_status(status: int, detail: str) -> NoReturn:
    if status == 404:
        raise KernelSessionNotFoundError(404, detail)
    if status == 400:
        raise KernelBadRequestError(400, detail)
    if status == 409:
        raise KernelConflictError(409, detail)
    if status == 410:
        raise KernelGoneError(410, detail)
    if status == 503:
        raise KernelUnavailableError(503, detail)
    if status == 501:
        raise KernelNotImplementedError(501, detail)
    raise KernelClientError(status, detail)


class HttpKernelClient:
    """``KernelClient`` over the network. See module docstring."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout, read=None),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- plumbing -----------------------------------------------------

    @staticmethod
    def _owner_headers(owner: str | None) -> dict[str, str]:
        # The kernel reads the per-request owner from this header (its
        # ``get_owner_id`` dependency); absent → kernel falls back to its
        # boot-seeded default. None is reserved for the cross-owner facade.
        return {"X-Valuz-Owner-Id": owner} if owner else {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        owner: str | None = None,
    ) -> dict[str, Any]:
        try:
            resp = await self._http.request(
                method, path, json=json_body, params=params, headers=self._owner_headers(owner)
            )
        except httpx.HTTPError as exc:
            raise KernelUnavailableError(503, f"kernel unreachable: {exc}") from exc
        if resp.status_code >= 400:
            detail = str(resp.status_code)
            try:
                detail = str(resp.json().get("detail", detail))
            except Exception:  # noqa: BLE001
                pass
            _raise_for_status(resp.status_code, detail)
        payload: dict[str, Any] = resp.json()
        return payload

    # -- sessions -----------------------------------------------------

    async def create_session(self, user_id: str, req: CreateSessionRequest) -> SessionData:
        # Dynamic mount: the kernel runs in a sandbox, so its cwd must be
        # reachable there. For a project under the static mounts this is a
        # no-op; for an external folder bound after boot it issues a sandbox
        # extension the running kernel consumes (no restart). ``kernel_cwd``
        # is the original path locally, the staged path in a cloud driver —
        # so we always send back what the registry returns.
        if req.cwd:
            from valuz_agent.integrations import sandbox_runtime

            kernel_cwd = await sandbox_runtime.ensure_workspace_granted(req.cwd)
            if kernel_cwd != req.cwd:
                req = req.model_copy(update={"cwd": kernel_cwd})
        result = await self._request(
            "POST", "/api/v1/sessions", json_body=req.model_dump(mode="json"), owner=user_id
        )
        return SessionData(**result["data"])

    async def get_session(self, user_id: str, session_id: str) -> SessionData | None:
        try:
            result = await self._request("GET", f"/api/v1/sessions/{session_id}", owner=user_id)
        except KernelSessionNotFoundError:
            return None
        return SessionData(**result["data"])

    async def list_sessions(
        self,
        user_id: str,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status is not None:
            params["status"] = status
        if ids is not None:
            params["ids"] = ",".join(ids)
        result = await self._request("GET", "/api/v1/sessions", params=params, owner=user_id)
        return [SessionData(**item) for item in result["data"]]

    async def list_all_sessions(
        self,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]:
        # No remote endpoint: the kernel HTTP API is owner-scoped. A remote
        # kernel runs its own startup recovery in-process; a cross-owner host
        # aggregator against a remote kernel would need a dedicated admin route.
        raise KernelNotImplementedError(
            501, "cross-owner list_all_sessions is unsupported over the HTTP kernel transport"
        )

    async def update_session(
        self, user_id: str, session_id: str, req: UpdateSessionRequest
    ) -> SessionData:
        result = await self._request(
            "PATCH",
            f"/api/v1/sessions/{session_id}",
            json_body=req.model_dump(mode="json", exclude_unset=True),
            owner=user_id,
        )
        return SessionData(**result["data"])

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        try:
            await self._request("DELETE", f"/api/v1/sessions/{session_id}", owner=user_id)
        except KernelSessionNotFoundError:
            return False
        return True

    async def set_mode(self, user_id: str, session_id: str, mode: str) -> SessionData:
        result = await self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/mode",
            json_body={"mode": mode},
            owner=user_id,
        )
        return SessionData(**result["data"])

    async def finalize_session(
        self, user_id: str, session_id: str, req: FinalizeSessionRequest
    ) -> SessionData:
        result = await self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/finalize",
            json_body=req.model_dump(mode="json", exclude_unset=True),
            owner=user_id,
        )
        return SessionData(**result["data"])

    # -- events -------------------------------------------------------

    async def append_event(self, user_id: str, session_id: str, event: EventPayload) -> bool:
        result = await self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/events",
            json_body=event.model_dump(mode="json"),
            owner=user_id,
        )
        return bool(result["data"]["persisted"])

    async def emit_live_event(
        self, user_id: str, session_id: str, type: str, data: dict[str, Any]
    ) -> None:
        await self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/events",
            json_body={"type": type, "data": data},
            params={"live_only": "true"},
            owner=user_id,
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
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if after_seq is not None:
            params["after_seq"] = after_seq
        result = await self._request(
            "GET", f"/api/v1/sessions/{session_id}/events", params=params, owner=user_id
        )
        return [EventData(**item) for item in result["data"]]

    async def get_events_window(
        self, user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> EventWindowData:
        params: dict[str, Any] = {"turn_limit": turn_limit}
        if before_seq is not None:
            params["before_seq"] = before_seq
        result = await self._request(
            "GET", f"/api/v1/sessions/{session_id}/events/window", params=params, owner=user_id
        )
        return EventWindowData(**result["data"])

    async def subscribe_session_events(
        self, user_id: str, session_id: str
    ) -> AsyncIterator[EventData]:
        async for item in self._stream_sse(
            f"/api/v1/sessions/{session_id}/events/stream", owner=user_id
        ):
            yield item

    async def subscribe_all_events(self) -> AsyncIterator[EventData]:
        async for item in self._stream_sse("/api/v1/events/stream", owner=None):
            yield item

    async def _stream_sse(self, path: str, *, owner: str | None) -> AsyncIterator[EventData]:
        try:
            async with self._http.stream("GET", path, headers=self._owner_headers(owner)) as resp:
                if resp.status_code >= 400:
                    _raise_for_status(resp.status_code, str(resp.status_code))
                event_name = ""
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line.split(":", 1)[1].strip()
                        continue
                    if line.startswith("data:"):
                        if event_name == "heartbeat":
                            continue
                        payload = line.split(":", 1)[1].strip()
                        if not payload:
                            continue
                        yield EventData(**json.loads(payload))
        except httpx.HTTPError as exc:
            raise KernelUnavailableError(503, f"kernel stream dropped: {exc}") from exc

    async def usage_rollup(self, user_id: str, start_ms: int, end_ms: int) -> list[UsageRollupData]:
        result = await self._request(
            "GET",
            "/api/v1/usage",
            params={"start_ms": start_ms, "end_ms": end_ms},
            owner=user_id,
        )
        return [UsageRollupData(**item) for item in result["data"]]

    # -- messages / actions / run --------------------------------------

    async def list_messages(
        self, user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[MessageData]:
        result = await self._request(
            "GET",
            f"/api/v1/sessions/{session_id}/messages",
            params={"limit": limit, "offset": offset},
            owner=user_id,
        )
        return [MessageData(**item) for item in result["data"]]

    async def submit_action(
        self, user_id: str, session_id: str, req: SubmitActionRequest
    ) -> dict[str, Any]:
        result = await self._request(
            "POST",
            f"/api/v1/sessions/{session_id}/actions",
            json_body=req.model_dump(mode="json"),
            owner=user_id,
        )
        data = result["data"]
        return data if isinstance(data, dict) else dict(data)

    async def interrupt(self, user_id: str, session_id: str) -> None:
        # In-process parity: ``orchestrator.interrupt`` is a silent no-op
        # for unknown / not-running sessions, so a 404 here is swallowed.
        try:
            await self._request("POST", f"/api/v1/sessions/{session_id}/interrupt", owner=user_id)
        except KernelSessionNotFoundError:
            return

    async def run_turn(
        self,
        user_id: str,
        session_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        additional_context: str = "",
    ) -> MessageData:
        import websockets

        ws_base = self._base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        url = f"{ws_base}/api/v1/sessions/{session_id}/run"
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        headers["X-Valuz-Owner-Id"] = user_id
        payload = {
            "message": {
                "text": text,
                "attachments": attachments or [],
                "additional_context": additional_context,
            }
        }
        try:
            async with websockets.connect(url, additional_headers=headers, max_size=None) as ws:
                await ws.send(json.dumps(payload))
                while True:
                    frame = json.loads(await ws.recv())
                    ftype = frame.get("type")
                    if ftype == "error":
                        raise KernelClientError(
                            500, str((frame.get("data") or {}).get("message", "run failed"))
                        )
                    if ftype in _TURN_TERMINAL_EVENTS:
                        break
        except websockets.exceptions.ConnectionClosed as exc:
            raise KernelUnavailableError(503, f"run channel closed: {exc}") from exc
        except OSError as exc:
            raise KernelUnavailableError(503, f"kernel unreachable: {exc}") from exc

        messages = await self.list_messages(user_id, session_id, limit=1)
        if not messages:
            raise KernelClientError(500, "turn completed but no message row found")
        return messages[0]

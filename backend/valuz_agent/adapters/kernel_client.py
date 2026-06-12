"""KernelClient — the host's single operational seam to the kernel.

The method surface mirrors the kernel HTTP API one-to-one (see the table in
the module body); every input/output is a kernel **wire schema**
(``app.schemas`` Pydantic models), never a kernel domain dataclass. The
default ``InProcessKernelClient`` invokes the kernel's own route functions
directly with explicit dependencies — the exact code path HTTP requests
take, minus the network — so a future ``HttpKernelClient`` (remote kernel in
a cloud sandbox) can swap in behind the same protocol without touching call
sites.

Errors surface as ``Kernel*Error`` types owned by this module; the
in-process implementation maps the routes' ``HTTPException``s onto them
(an HTTP implementation would map status codes identically).

| method                   | kernel endpoint                                   |
|--------------------------|---------------------------------------------------|
| create_session           | POST   /api/v1/sessions                           |
| get_session              | GET    /api/v1/sessions/{id}                      |
| list_sessions            | GET    /api/v1/sessions[?status=&ids=]            |
| update_session           | PATCH  /api/v1/sessions/{id}                      |
| delete_session           | DELETE /api/v1/sessions/{id}                      |
| set_mode                 | POST   /api/v1/sessions/{id}/mode                 |
| finalize_session         | POST   /api/v1/sessions/{id}/finalize             |
| append_event             | POST   /api/v1/sessions/{id}/events               |
| emit_live_event          | POST   /api/v1/sessions/{id}/events?live_only=true|
| get_events               | GET    /api/v1/sessions/{id}/events[?after_seq=]  |
| get_events_window        | GET    /api/v1/sessions/{id}/events/window        |
| subscribe_session_events | SSE    /api/v1/sessions/{id}/events/stream        |
| subscribe_all_events     | SSE    /api/v1/events/stream                      |
| usage_rollup             | GET    /api/v1/usage                              |
| list_messages            | GET    /api/v1/sessions/{id}/messages             |
| submit_action            | POST   /api/v1/sessions/{id}/actions              |
| interrupt                | POST   /api/v1/sessions/{id}/interrupt            |
| run_turn                 | WS     /api/v1/sessions/{id}/run                  |
| scan_orphan_*            | (in-process only — no remote analog; the         |
|                          |  kernel runs these itself at startup)             |
"""

from __future__ import annotations

# mypy: disable-error-code="no-any-return"
# The kernel boundary is configured ``follow_imports = "skip"`` so kernel
# types resolve to ``Any``; silenced at module scope like the former
# kernel_store facade.

# ruff: noqa: I001 — the kernel side-effect import must precede ``app.*``.

from collections.abc import AsyncIterator
from typing import Any, NoReturn, Protocol

import valuz_agent.boot.kernel  # noqa: F401  (sys.path side-effect)

from fastapi import HTTPException  # noqa: E402

from app.schemas import (  # noqa: E402
    CreateSessionRequest,
    EventData,
    EventPayload,
    EventWindowData,
    FinalizeSessionRequest,
    MessageData,
    SessionData,
    SetSessionModeRequest,
    SubmitActionRequest,
    UpdateSessionRequest,
    UsageRollupData,
)


# ---------------------------------------------------------------------------
# Errors — owned by the seam, independent of transport.
# ---------------------------------------------------------------------------


class KernelClientError(Exception):
    """Base for kernel seam failures. ``status`` follows HTTP semantics."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class KernelSessionNotFoundError(KernelClientError):
    pass


class KernelBadRequestError(KernelClientError):
    pass


class KernelConflictError(KernelClientError):
    pass


class KernelGoneError(KernelClientError):
    pass


class KernelUnavailableError(KernelClientError):
    pass


class KernelNotImplementedError(KernelClientError):
    pass


def _raise_mapped(exc: HTTPException) -> NoReturn:
    detail = str(exc.detail)
    if exc.status_code == 404:
        raise KernelSessionNotFoundError(404, detail) from exc
    if exc.status_code == 400:
        raise KernelBadRequestError(400, detail) from exc
    if exc.status_code == 409:
        raise KernelConflictError(409, detail) from exc
    if exc.status_code == 410:
        raise KernelGoneError(410, detail) from exc
    if exc.status_code == 503:
        raise KernelUnavailableError(503, detail) from exc
    if exc.status_code == 501:
        raise KernelNotImplementedError(501, detail) from exc
    raise KernelClientError(exc.status_code, detail) from exc


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class KernelClient(Protocol):
    async def create_session(self, req: CreateSessionRequest) -> SessionData: ...

    async def get_session(self, session_id: str) -> SessionData | None: ...

    async def list_sessions(
        self,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]: ...

    async def update_session(self, session_id: str, req: UpdateSessionRequest) -> SessionData: ...

    async def delete_session(self, session_id: str) -> bool: ...

    async def set_mode(self, session_id: str, mode: str) -> SessionData: ...

    async def finalize_session(
        self, session_id: str, req: FinalizeSessionRequest
    ) -> SessionData: ...

    async def append_event(self, session_id: str, event: EventPayload) -> bool: ...

    async def emit_live_event(
        self, session_id: str, type: str, data: dict[str, Any]
    ) -> None: ...

    async def get_events(
        self,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        after_seq: int | None = None,
    ) -> list[EventData]: ...

    async def get_events_window(
        self, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> EventWindowData: ...

    def subscribe_session_events(self, session_id: str) -> AsyncIterator[EventData]: ...

    def subscribe_all_events(self) -> AsyncIterator[EventData]: ...

    async def usage_rollup(self, start_ms: int, end_ms: int) -> list[UsageRollupData]: ...

    async def list_messages(
        self, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[MessageData]: ...

    async def submit_action(self, session_id: str, req: SubmitActionRequest) -> dict[str, Any]: ...

    async def interrupt(self, session_id: str) -> None: ...

    async def run_turn(
        self,
        session_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        additional_context: str = "",
    ) -> MessageData: ...


# ---------------------------------------------------------------------------
# In-process implementation — calls the kernel's route functions directly.
# ---------------------------------------------------------------------------


def _store() -> Any:
    from app.dependencies import get_store

    return get_store()


def _orchestrator() -> Any:
    from app.dependencies import get_orchestrator

    return get_orchestrator()


class InProcessKernelClient:
    """Default transport: the kernel lives in this process.

    Each method drives the same route function the HTTP surface mounts, so
    validation/serialization behaviour is identical by construction.
    """

    async def create_session(self, req: CreateSessionRequest) -> SessionData:
        from app.routes.sessions import create_session

        try:
            result = await create_session(req, _store())
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def get_session(self, session_id: str) -> SessionData | None:
        from app.routes.sessions import get_session

        try:
            result = await get_session(session_id, _store())
        except HTTPException as exc:
            if exc.status_code == 404:
                return None
            _raise_mapped(exc)
        return result["data"]

    async def list_sessions(
        self,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]:
        from app.routes.sessions import list_sessions

        try:
            result = await list_sessions(
                _store(),
                status=status,
                ids=",".join(ids) if ids is not None else None,
                limit=limit,
                offset=offset,
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def update_session(self, session_id: str, req: UpdateSessionRequest) -> SessionData:
        from app.routes.sessions import update_session

        try:
            result = await update_session(session_id, req, _store())
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def delete_session(self, session_id: str) -> bool:
        from app.routes.sessions import delete_session

        try:
            await delete_session(session_id, _store())
        except HTTPException as exc:
            if exc.status_code == 404:
                return False
            _raise_mapped(exc)
        return True

    async def set_mode(self, session_id: str, mode: str) -> SessionData:
        from app.routes.sessions import set_session_mode

        try:
            result = await set_session_mode(
                session_id, SetSessionModeRequest(mode=mode), _store()
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def finalize_session(
        self, session_id: str, req: FinalizeSessionRequest
    ) -> SessionData:
        from app.routes.sessions import finalize_session

        try:
            result = await finalize_session(session_id, req, _store())
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def append_event(self, session_id: str, event: EventPayload) -> bool:
        from app.routes.sessions import append_session_event

        try:
            result = await append_session_event(
                session_id, event, _store(), _orchestrator(), live_only=False
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return bool(result["data"].persisted)

    async def emit_live_event(self, session_id: str, type: str, data: dict[str, Any]) -> None:
        from app.routes.sessions import append_session_event

        try:
            await append_session_event(
                session_id,
                EventPayload(type=type, data=data),
                _store(),
                _orchestrator(),
                live_only=True,
            )
        except HTTPException as exc:
            _raise_mapped(exc)

    async def get_events(
        self,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        after_seq: int | None = None,
    ) -> list[EventData]:
        from app.routes.sessions import get_session_events

        try:
            result = await get_session_events(
                session_id, _store(), limit=limit, offset=offset, after_seq=after_seq
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def get_events_window(
        self, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> EventWindowData:
        from app.routes.sessions import get_session_events_window

        try:
            result = await get_session_events_window(
                session_id, _store(), before_seq=before_seq, turn_limit=turn_limit
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def subscribe_session_events(self, session_id: str) -> AsyncIterator[EventData]:
        """Live tap on one session's event stream (no replay, no backfill —
        pair with ``get_events(after_seq=...)`` for catch-up reads).

        Remote analog: SSE /api/v1/sessions/{id}/events/stream."""
        from app.event_stream import QueueEventSink
        from app.serializers import live_event_to_data

        sink = QueueEventSink()
        orch = _orchestrator()
        await orch.attach_session_tap(session_id, sink)
        try:
            while True:
                event = await sink.queue.get()
                yield live_event_to_data(event)
        finally:
            await orch.detach_session_tap(session_id, sink)

    async def subscribe_all_events(self) -> AsyncIterator[EventData]:
        """Live tap on EVERY session's event stream; frames carry
        ``session_id``. Remote analog: SSE /api/v1/events/stream."""
        from app.event_stream import GlobalQueueTap
        from app.serializers import live_event_to_data

        tap = GlobalQueueTap()
        orch = _orchestrator()
        orch.attach_global_tap(tap)
        try:
            while True:
                session_id, event = await tap.queue.get()
                yield live_event_to_data(event, session_id=session_id)
        finally:
            orch.detach_global_tap(tap)

    async def usage_rollup(self, start_ms: int, end_ms: int) -> list[UsageRollupData]:
        from app.routes.usage import get_usage_rollup

        try:
            result = await get_usage_rollup(_store(), start_ms=start_ms, end_ms=end_ms)
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def list_messages(
        self, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[MessageData]:
        from app.routes.messages import list_session_messages

        try:
            result = await list_session_messages(session_id, _store(), limit=limit, offset=offset)
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def submit_action(self, session_id: str, req: SubmitActionRequest) -> dict[str, Any]:
        from app.routes.sessions import submit_session_action

        try:
            result = await submit_session_action(session_id, req, _orchestrator())
        except HTTPException as exc:
            _raise_mapped(exc)
        data = result["data"]
        return data if isinstance(data, dict) else data.model_dump()

    async def interrupt(self, session_id: str) -> None:
        # Remote analog: POST /api/v1/sessions/{id}/interrupt.
        await _orchestrator().interrupt(session_id)

    async def run_turn(
        self,
        session_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        additional_context: str = "",
    ) -> MessageData:
        # Remote analog: the WS /run channel. The wire shape is
        # {"message": {"text": ..., "attachments": [...],
        #              "additional_context": ...}}; the returned MessageData
        # mirrors the channel's final message frame.
        from app.routes.messages import _message_to_data
        from src.core.types import Attachment, UserMessage

        atts = tuple(
            Attachment(
                source_path=a["source_path"],
                parsed_path=a.get("parsed_path"),
            )
            for a in (attachments or [])
        )
        message = await _orchestrator().run_turn(
            session_id,
            UserMessage(text=text, attachments=atts, additional_context=additional_context),
        )
        return _message_to_data(message)

    # -- In-process-only supervision hooks (no remote analog: a standalone
    # kernel runs its own orphan scans at startup; see app.dependencies). --

    async def scan_orphan_pendings(self) -> int:
        return await _orchestrator().scan_orphan_pendings()

    async def scan_orphan_runs(self) -> int:
        return await _orchestrator().scan_orphan_runs()

    async def cleanup_runtime(self, session_id: str) -> None:
        """Evict the cached runtime for ``session_id`` (in-process only —
        a remote kernel owns its runtime cache)."""
        await _orchestrator().cleanup(session_id)


def _make_client() -> KernelClient:
    """Bind the transport for this process from settings.

    ``inprocess`` (default) — the kernel lives in this process.
    ``http`` — the kernel runs as a separate process (bare subprocess,
    sandbox, or remote) at ``settings.kernel_url``; see
    ``adapters/kernel_client_http.py``.
    """
    from valuz_agent.infra.config import settings

    if settings.kernel_mode == "http":
        from valuz_agent.adapters.kernel_client_http import HttpKernelClient

        return HttpKernelClient(settings.kernel_url, token=settings.kernel_token)
    return InProcessKernelClient()


client: KernelClient = _make_client()


def rebind_client() -> None:
    """Re-select the transport from the current ``settings``.

    The module-level ``client`` is chosen once at import. When the kernel
    endpoint is decided at runtime (e.g. a sandbox provisioned at boot that
    sets ``kernel_mode=http`` + url/token), call this to swap the live
    object — the facade functions read the module global per call, so they
    pick up the new transport without re-import."""
    global client  # noqa: PLW0603
    client = _make_client()


# Module-level facade — call-site ergonomics match the former kernel_store
# (``await kernel_client.get_session(...)``), while the swappable object
# lives behind ``client`` for the HTTP transport.


async def create_session(req: CreateSessionRequest) -> SessionData:
    return await client.create_session(req)


async def get_session(session_id: str) -> SessionData | None:
    return await client.get_session(session_id)


async def list_sessions(
    *,
    status: str | None = None,
    ids: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionData]:
    return await client.list_sessions(status=status, ids=ids, limit=limit, offset=offset)


async def update_session(session_id: str, req: UpdateSessionRequest) -> SessionData:
    return await client.update_session(session_id, req)


async def delete_session(session_id: str) -> bool:
    return await client.delete_session(session_id)


async def set_mode(session_id: str, mode: str) -> SessionData:
    return await client.set_mode(session_id, mode)


async def finalize_session(session_id: str, req: FinalizeSessionRequest) -> SessionData:
    return await client.finalize_session(session_id, req)


async def append_event(session_id: str, event: EventPayload) -> bool:
    return await client.append_event(session_id, event)


async def emit_live_event(session_id: str, type: str, data: dict[str, Any]) -> None:
    await client.emit_live_event(session_id, type, data)


async def get_events(
    session_id: str,
    *,
    limit: int = 200,
    offset: int = 0,
    after_seq: int | None = None,
) -> list[EventData]:
    return await client.get_events(session_id, limit=limit, offset=offset, after_seq=after_seq)


async def get_events_window(
    session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
) -> EventWindowData:
    return await client.get_events_window(
        session_id, before_seq=before_seq, turn_limit=turn_limit
    )


def subscribe_session_events(session_id: str) -> AsyncIterator[EventData]:
    return client.subscribe_session_events(session_id)


def subscribe_all_events() -> AsyncIterator[EventData]:
    return client.subscribe_all_events()


async def usage_rollup(start_ms: int, end_ms: int) -> list[UsageRollupData]:
    return await client.usage_rollup(start_ms, end_ms)


async def list_messages(
    session_id: str, *, limit: int = 50, offset: int = 0
) -> list[MessageData]:
    return await client.list_messages(session_id, limit=limit, offset=offset)


async def latest_message_id(session_id: str) -> str | None:
    messages = await client.list_messages(session_id, limit=1)
    return messages[0].id if messages else None


async def submit_action(session_id: str, req: SubmitActionRequest) -> dict[str, Any]:
    return await client.submit_action(session_id, req)


async def interrupt(session_id: str) -> None:
    await client.interrupt(session_id)


async def run_turn(
    session_id: str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    additional_context: str = "",
) -> MessageData:
    return await client.run_turn(session_id, text, attachments, additional_context)


async def scan_orphan_pendings() -> int:
    return await client.scan_orphan_pendings()  # type: ignore[attr-defined]


async def scan_orphan_runs() -> int:
    return await client.scan_orphan_runs()  # type: ignore[attr-defined]


async def cleanup_runtime(session_id: str) -> None:
    await client.cleanup_runtime(session_id)  # type: ignore[attr-defined]

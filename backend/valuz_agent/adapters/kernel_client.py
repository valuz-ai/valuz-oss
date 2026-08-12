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

Endpoints below are shown under ``{KERNEL_API_PREFIX}`` — this host overrides
it to ``/kernel`` (ADR-013; the kernel's own upstream default is ``/api`` — see
``valuz_agent.boot.kernel.kernel_api_prefix`` /
``kernel/app/routes/__init__.py``).

| method                   | kernel endpoint                                            |
|--------------------------|-------------------------------------------------------------|
| create_session           | POST   {KERNEL_API_PREFIX}/v1/sessions                      |
| get_session              | GET    {KERNEL_API_PREFIX}/v1/sessions/{id}                 |
| list_sessions            | GET    {KERNEL_API_PREFIX}/v1/sessions[?status=&ids=]       |
| update_session           | PATCH  {KERNEL_API_PREFIX}/v1/sessions/{id}                 |
| delete_session           | DELETE {KERNEL_API_PREFIX}/v1/sessions/{id}                 |
| set_mode                 | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/mode             |
| finalize_session         | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/finalize          |
| append_event             | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/events            |
| emit_live_event          | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/events?live_only=true|
| get_events               | GET    {KERNEL_API_PREFIX}/v1/sessions/{id}/events[?after_seq=]|
| get_events_window        | GET    {KERNEL_API_PREFIX}/v1/sessions/{id}/events/window     |
| subscribe_session_events | SSE    {KERNEL_API_PREFIX}/v1/sessions/{id}/events/stream     |
| subscribe_all_events     | SSE    {KERNEL_API_PREFIX}/v1/events/stream                   |
| usage_rollup             | GET    {KERNEL_API_PREFIX}/v1/usage                            |
| list_messages            | GET    {KERNEL_API_PREFIX}/v1/sessions/{id}/messages           |
| get_message              | GET    {KERNEL_API_PREFIX}/v1/messages/{id}                    |
| submit_action            | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/actions            |
| interrupt                | POST   {KERNEL_API_PREFIX}/v1/sessions/{id}/interrupt          |
| run_turn                 | WS     {KERNEL_API_PREFIX}/v1/sessions/{id}/run                |
| scan_orphan_*            | (in-process only — no remote analog; the                     |
|                          |  kernel runs these itself at startup)                        |
"""

from __future__ import annotations

# mypy: disable-error-code="no-any-return"
# The kernel boundary is configured ``follow_imports = "skip"`` so kernel
# types resolve to ``Any``; silenced at module scope like the former
# kernel_store facade.

# ruff: noqa: I001 — the kernel side-effect import must precede ``app.*``.

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from typing import Any, NoReturn, Protocol, TypedDict

import valuz_agent.boot.kernel  # noqa: F401  (sys.path side-effect)

from fastapi import HTTPException  # noqa: E402

from valuz_agent.ports.sandbox_allocator import SandboxScope  # noqa: E402

logger = logging.getLogger(__name__)

from app.schemas import (  # noqa: E402
    CreateSessionRequest,
    EventData,
    EventPayload,
    EventWindowData,
    FinalizeSessionRequest,
    ImportMessageRequest,
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


class RuntimeAvailability(TypedDict):
    """Per-runtime launchability, as reported by the kernel (§3.3)."""

    available: bool
    unavailable_reason: str | None


class KernelClient(Protocol):
    # Owner model (mirrors the host valuz_* tables): every owner-scoped method
    # takes the caller's ``user_id`` FIRST and the kernel filters/stamps on it.
    # ``list_all_sessions`` / ``subscribe_all_events`` / ``scan_orphan_*`` are
    # the deliberate cross-owner exceptions (startup sweeps + host aggregators).

    async def create_session(self, user_id: str, req: CreateSessionRequest) -> SessionData: ...

    async def get_session(self, user_id: str, session_id: str) -> SessionData | None: ...

    async def list_sessions(
        self,
        user_id: str,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]: ...

    async def list_all_sessions(
        self,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]: ...

    async def update_session(
        self, user_id: str, session_id: str, req: UpdateSessionRequest
    ) -> SessionData: ...

    async def delete_session(self, user_id: str, session_id: str) -> bool: ...

    async def set_mode(self, user_id: str, session_id: str, mode: str) -> SessionData: ...

    async def finalize_session(
        self, user_id: str, session_id: str, req: FinalizeSessionRequest
    ) -> SessionData: ...

    async def append_event(self, user_id: str, session_id: str, event: EventPayload) -> bool: ...

    async def emit_live_event(
        self, user_id: str, session_id: str, type: str, data: dict[str, Any]
    ) -> None: ...

    async def get_events(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        after_seq: int | None = None,
    ) -> list[EventData]: ...

    async def get_events_window(
        self, user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> EventWindowData: ...

    def subscribe_session_events(
        self, user_id: str, session_id: str
    ) -> AsyncIterator[EventData]: ...

    def subscribe_all_events(
        self, types: tuple[str, ...] | None = None
    ) -> AsyncIterator[EventData]: ...

    async def usage_rollup(
        self, user_id: str, start_ms: int, end_ms: int
    ) -> list[UsageRollupData]: ...

    async def list_messages(
        self, user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[MessageData]: ...

    async def get_message(self, user_id: str, message_id: str) -> MessageData | None: ...

    async def import_message(
        self,
        user_id: str,
        session_id: str,
        req: ImportMessageRequest,
    ) -> MessageData: ...

    async def submit_action(
        self, user_id: str, session_id: str, req: SubmitActionRequest
    ) -> dict[str, Any]: ...

    async def interrupt(self, user_id: str, session_id: str) -> None: ...

    async def prepare_runtime(self, user_id: str, session_id: str) -> None: ...

    async def run_turn(
        self,
        user_id: str,
        session_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
        additional_context: str = "",
    ) -> MessageData: ...

    async def runtime_availability(self) -> dict[str, RuntimeAvailability]: ...

    async def bg_busy_session_ids(self) -> list[str]: ...


# ---------------------------------------------------------------------------
# In-process implementation — calls the kernel's route functions directly.
# ---------------------------------------------------------------------------


def _store() -> Any:
    from app.dependencies import get_store

    try:
        return get_store()
    except RuntimeError as exc:
        # The kernel's StorePort singleton is torn down on app-lifespan exit
        # (``shutdown_dependencies`` resets it to ``None``). An in-flight
        # in-process call landing here means the kernel is shutting down and no
        # longer serving. Surface it as the typed "unavailable" signal so
        # best-effort callers (e.g. the actor-loop finalize that races shutdown)
        # can skip quietly instead of crashing on a bare ``RuntimeError``.
        raise KernelUnavailableError(503, str(exc)) from exc


def _orchestrator() -> Any:
    from app.dependencies import get_orchestrator

    try:
        return get_orchestrator()
    except RuntimeError as exc:
        raise KernelUnavailableError(503, str(exc)) from exc


class _NoRuntimeOrchestrator:
    """Orchestrator stand-in for the durable-bound data-plane client.

    An at-rest session has no runtime and no live subscribers in THIS process,
    so the runtime-facing side of the session routes (interrupt/cleanup on
    delete, live mode/error broadcast) is a structural no-op — the store
    mutation is the whole operation."""

    active_sessions: frozenset[str] = frozenset()

    async def interrupt(self, session_id: str) -> None:  # pragma: no cover — unreachable
        return None

    async def cleanup(self, session_id: str) -> None:
        return None

    async def emit_session_event(self, *args: Any, **kwargs: Any) -> None:
        return None


class InProcessKernelClient:
    """In-process transport: kernel route functions driven directly.

    Each method drives the same route function the HTTP surface mounts, so
    validation/serialization behaviour is identical by construction.

    ``store_getter`` selects which store the route functions run against:

    - default (``None``) → the process kernel's own store (``app.dependencies``)
      — the execution kernel client.
    - an explicit getter → that store. The host binds one over the DataService
      durable (``bind_host_data_store``) as its NON-RUNTIME data plane: same
      kernel semantics, applied to the durable copy.

    Orchestrator-backed methods (run/interrupt/subscribe/scans) always use the
    process orchestrator — only the execution client meaningfully serves them.
    """

    def __init__(self, store_getter: Callable[[], Any] | None = None) -> None:
        self._store = store_getter or _store
        # The execution client drives the process orchestrator; a durable-bound
        # data-plane client has no runtime side (see _NoRuntimeOrchestrator).
        self._orchestrator: Callable[[], Any] = (
            _orchestrator if store_getter is None else _NoRuntimeOrchestrator
        )

    async def create_session(self, user_id: str, req: CreateSessionRequest) -> SessionData:
        from app.routes.sessions import create_session

        try:
            result = await create_session(req, self._store(), user_id)
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def get_session(self, user_id: str, session_id: str) -> SessionData | None:
        from app.routes.sessions import get_session

        try:
            result = await get_session(session_id, self._store(), user_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return None
            _raise_mapped(exc)
        return result["data"]

    async def list_sessions(
        self,
        user_id: str,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]:
        from app.routes.sessions import list_sessions

        try:
            result = await list_sessions(
                self._store(),
                user_id,
                status=status,
                ids=",".join(ids) if ids is not None else None,
                limit=limit,
                offset=offset,
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def list_all_sessions(
        self,
        *,
        status: str | None = None,
        ids: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SessionData]:
        # Cross-owner sweep (startup recovery / host aggregators). Goes straight
        # to the store with ``user_id=None`` — the owner-injecting route can't
        # express "every owner". Serializes with the route's projection.
        from app.serializers import session_to_data

        sessions = await self._store().list_sessions(
            None, status=status, ids=ids, limit=limit, offset=offset
        )
        return [session_to_data(s) for s in sessions]

    async def update_session(
        self, user_id: str, session_id: str, req: UpdateSessionRequest
    ) -> SessionData:
        from app.routes.sessions import update_session

        try:
            result = await update_session(session_id, req, self._store(), user_id)
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        from app.routes.sessions import delete_session

        try:
            await delete_session(session_id, self._store(), user_id, self._orchestrator())
        except HTTPException as exc:
            if exc.status_code == 404:
                return False
            _raise_mapped(exc)
        return True

    async def set_mode(self, user_id: str, session_id: str, mode: str) -> SessionData:
        from app.routes.sessions import set_session_mode

        try:
            result = await set_session_mode(
                session_id,
                SetSessionModeRequest(mode=mode),
                self._store(),
                self._orchestrator(),
                user_id,
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def finalize_session(
        self, user_id: str, session_id: str, req: FinalizeSessionRequest
    ) -> SessionData:
        from app.routes.sessions import finalize_session

        try:
            result = await finalize_session(session_id, req, self._store(), user_id)
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def append_event(self, user_id: str, session_id: str, event: EventPayload) -> bool:
        from app.routes.sessions import append_session_event

        try:
            result = await append_session_event(
                session_id, event, self._store(), self._orchestrator(), user_id, live_only=False
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return bool(result["data"].persisted)

    async def emit_live_event(
        self, user_id: str, session_id: str, type: str, data: dict[str, Any]
    ) -> None:
        from app.routes.sessions import append_session_event

        try:
            await append_session_event(
                session_id,
                EventPayload(type=type, data=data),
                self._store(),
                self._orchestrator(),
                user_id,
                live_only=True,
            )
        except HTTPException as exc:
            _raise_mapped(exc)

    async def get_events(
        self,
        user_id: str,
        session_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        after_seq: int | None = None,
    ) -> list[EventData]:
        from app.routes.sessions import get_session_events

        try:
            result = await get_session_events(
                session_id, self._store(), user_id, limit=limit, offset=offset, after_seq=after_seq
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def get_events_window(
        self, user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
    ) -> EventWindowData:
        from app.routes.sessions import get_session_events_window

        try:
            result = await get_session_events_window(
                session_id, self._store(), user_id, before_seq=before_seq, turn_limit=turn_limit
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def subscribe_session_events(
        self, user_id: str, session_id: str
    ) -> AsyncIterator[EventData]:
        """Live tap on one session's event stream (no replay, no backfill —
        pair with ``get_events(after_seq=...)`` for catch-up reads).

        The one thing it does deliver up front is the *unsealed* streaming
        state (``live_partial``): the bytes emitted since the last
        canonical event are never persisted, so no catch-up read can ever
        reach them. Without this a mid-turn reconnect renders an empty
        assistant block until the turn ends.

        Remote analog: SSE {KERNEL_API_PREFIX}/v1/sessions/{id}/events/stream (ADR-013)."""
        from app.event_stream import QueueEventSink
        from app.serializers import live_event_to_data

        sink = QueueEventSink()
        orch = _orchestrator()
        await orch.attach_session_tap(user_id, session_id, sink, live_partial=True)
        try:
            while True:
                event = await sink.queue.get()
                yield live_event_to_data(event)
        finally:
            await orch.detach_session_tap(session_id, sink)

    async def subscribe_all_events(
        self, types: tuple[str, ...] | None = None
    ) -> AsyncIterator[EventData]:
        """Live tap on EVERY session's event stream; frames carry
        ``session_id``. ``types`` is an event-type allowlist — a
        lifecycle-only consumer (the host control plane) filters here so
        token deltas are dropped at the source instead of shipped to be
        discarded. Remote analog: SSE {KERNEL_API_PREFIX}/v1/events/stream
        ?types=... (ADR-013)."""
        from app.event_stream import GlobalQueueTap
        from app.serializers import live_event_to_data

        tap = GlobalQueueTap()
        orch = _orchestrator()
        orch.attach_global_tap(tap)
        try:
            while True:
                session_id, event = await tap.queue.get()
                if types is not None and str(event.type) not in types:
                    continue
                yield live_event_to_data(event, session_id=session_id)
        finally:
            orch.detach_global_tap(tap)

    async def usage_rollup(self, user_id: str, start_ms: int, end_ms: int) -> list[UsageRollupData]:
        from app.routes.usage import get_usage_rollup

        try:
            result = await get_usage_rollup(
                self._store(), user_id, start_ms=start_ms, end_ms=end_ms
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def list_messages(
        self, user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[MessageData]:
        from app.routes.messages import list_session_messages

        try:
            result = await list_session_messages(
                session_id, self._store(), user_id, limit=limit, offset=offset
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def get_message(self, user_id: str, message_id: str) -> MessageData | None:
        from app.routes.messages import get_message

        try:
            result = await get_message(message_id, self._store(), user_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return None
            _raise_mapped(exc)
        return result["data"]

    async def import_message(
        self,
        user_id: str,
        session_id: str,
        req: ImportMessageRequest,
    ) -> MessageData:
        from app.routes.messages import import_canonical_message

        try:
            result = await import_canonical_message(
                session_id,
                req,
                self._store(),
                _orchestrator(),
                user_id,
            )
        except HTTPException as exc:
            _raise_mapped(exc)
        return result["data"]

    async def submit_action(
        self, user_id: str, session_id: str, req: SubmitActionRequest
    ) -> dict[str, Any]:
        from app.routes.sessions import submit_session_action

        try:
            result = await submit_session_action(session_id, req, _orchestrator(), user_id)
        except HTTPException as exc:
            _raise_mapped(exc)
        data = result["data"]
        return data if isinstance(data, dict) else data.model_dump()

    async def interrupt(self, user_id: str, session_id: str) -> None:
        # Remote analog: POST {KERNEL_API_PREFIX}/v1/sessions/{id}/interrupt
        # (ADR-013). Route the call through the owner-scoped interrupt route
        # so a cross-owner session_id 404s instead of interrupting another
        # owner's run.
        from app.routes.run import interrupt_session

        try:
            await interrupt_session(session_id, self._store(), user_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return
            _raise_mapped(exc)

    async def prepare_runtime(self, user_id: str, session_id: str) -> None:
        await _orchestrator().prepare_runtime(user_id, session_id)

    async def run_turn(
        self,
        user_id: str,
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
            user_id,
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

    async def reset_stranded_session(self, user_id: str, session_id: str) -> bool:
        # Pure store-driven kernel semantics (src.core.recovery) on THIS
        # client's store — the host's durable-bound client repairs the durable
        # copy of a session whose sandbox is gone.
        from src.core import recovery

        return await recovery.reset_stranded_session(self._store(), user_id, session_id)

    async def cleanup_runtime(self, session_id: str) -> None:
        """Evict the cached runtime for ``session_id`` (in-process only —
        a remote kernel owns its runtime cache)."""
        await _orchestrator().cleanup(session_id)

    async def runtime_availability(self) -> dict[str, RuntimeAvailability]:
        # No store/orchestrator needed — a pure binary probe in this process
        # (host == kernel in-process, so this is the local-host answer).
        from app.routes.runtimes import get_runtime_availability

        result = await get_runtime_availability()
        return result["data"]

    async def bg_busy_session_ids(self) -> list[str]:
        """Sessions whose warm runtime carries a live background task.
        Process-scoped, id-only — callers intersect with their own
        owner-scoped session set (see the kernel route's docstring)."""
        return _orchestrator().bg_busy_session_ids()


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


# ---------------------------------------------------------------------------
# Host data plane (NON-RUNTIME reads + at-rest control writes).
#
# The kernel's runtime store is its local sqlite; every kernel write is
# dual-written to the DataService durable (RuntimeStore). The HOST therefore
# never reads history/state out of an execution kernel — it reads the durable.
# ``bind_host_data_store`` (boot, right after the DataService backend store is
# built) binds an ``InProcessKernelClient`` over that store: identical kernel
# route semantics, applied to the durable copy.
#
# Unbound (unit tests / bare embedding) → the process-global ``client`` — in
# the OSS single-process default that is the same data either way.

_host_data_client: KernelClient | None = None


def bind_host_data_store(store_getter: Callable[[], Any] | None) -> None:
    """Bind the host's non-runtime data plane onto the DataService durable
    store (``None`` unbinds — tests)."""
    global _host_data_client  # noqa: PLW0603
    _host_data_client = None if store_getter is None else InProcessKernelClient(store_getter)


def _data_plane() -> KernelClient:
    """The host's durable-bound client, else the process-global ``client``."""
    return _host_data_client if _host_data_client is not None else client


# The kernel a ``pre_turn`` hook's control writes must land on: the very
# instance ``run_turn`` just allocated for this turn. Set only for the duration
# of that hook (see ``run_turn``), and only ever consulted for the session it
# was pinned for.
#
# Why a pin rather than letting ``_control_kernel`` re-resolve: the hook runs in
# the gap between allocation and the turn, and re-resolving would (a) cost a
# second allocator round-trip (a live health probe per turn) and (b) be
# genuinely racy — a concurrent ``ensure`` on the same scope can repoint the
# registry row between the two lookups, so the refresh would be written to an
# instance that is NOT the one about to read it.
_pinned_control_kernel: ContextVar[tuple[str, KernelClient] | None] = ContextVar(
    "valuz_pinned_control_kernel", default=None
)


async def _control_kernel(user_id: str, session_id: str) -> KernelClient:
    """Route a session CONTROL write (update/mode/finalize/append/delete).

    The session's LIVE execution kernel first — while a kernel holds the
    session its runtime sqlite is the authority, so the write must land there
    (its dual-write mirrors it to the durable). No live kernel (at-rest
    session, sandbox gone) → write the durable directly via the data plane.

    A ``pre_turn`` hook runs with the turn's kernel pinned (``run_turn``); the
    pin is keyed by session id so it can never mis-route a write for a
    different session that happens to share the task/context.
    """
    pinned = _pinned_control_kernel.get()
    if pinned is not None and pinned[0] == session_id:
        return pinned[1]
    k = await _kernel_for_existing(user_id, await _scope_for(user_id, session_id))
    return k if k is not None else _data_plane()


# Module-level facade — call-site ergonomics match the former kernel_store
# (``await kernel_client.get_session(...)``), while the swappable object
# lives behind ``client`` for the HTTP transport.


# ---------------------------------------------------------------------------
# Per-user kernel resolution (fleet seam). EXECUTION / LIVE facade methods route
# through ``_kernel_for(user_id)``; non-runtime reads go to the host data plane
# (``_data_plane``); session control writes route live-kernel-first
# (``_control_kernel``). The OSS default allocator returns endpoint=None → the
# process-global ``client`` (in-process or boot-attached sandbox) — behavior
# unchanged. A commercial allocator returns a per-user endpoint; we cache one
# HttpKernelClient per URL.
# ---------------------------------------------------------------------------

_endpoint_clients: dict[str, KernelClient] = {}

# ---------------------------------------------------------------------------
# Sandbox scope resolution (per-session / per-task on-demand sandboxes).
#
# A scope names the unit of work a sandbox serves (see ``SandboxScope``). The
# facade derives it once per session and caches it — the mapping is immutable
# (a session never changes task membership). Callers that KNOW the scope at
# creation time (tasks pass ``task:{task_id}``) supply it explicitly; every
# other EXEC op resolves via the optional bound resolver (the tasks module
# binds a ``valuz_task_session`` lookup at boot) and falls back to
# ``session:{session_id}``. With the OSS ``BootSingletonAllocator`` the scope
# is ignored entirely — zero behavior change for local / single-user hosts.
# ---------------------------------------------------------------------------

_SCOPE_CACHE_MAX = 4096
_scope_cache: dict[str, SandboxScope] = {}
_scope_resolver: Callable[[str, str], Awaitable[SandboxScope | None]] | None = None


def bind_sandbox_scope_resolver(
    resolver: Callable[[str, str], Awaitable[SandboxScope | None]] | None,
) -> None:
    """Bind the (single) session→scope resolver — ``(user_id, session_id) ->
    SandboxScope | None``. Bound at boot by the tasks module so task sessions
    route to their task's sandbox; ``None`` unbinds (tests)."""
    global _scope_resolver  # noqa: PLW0603
    _scope_resolver = resolver


def _scope_cache_put(session_id: str, scope: SandboxScope) -> None:
    if len(_scope_cache) >= _SCOPE_CACHE_MAX:
        # Bounded: drop an arbitrary ~eighth. Scopes re-derive cheaply.
        for key in list(_scope_cache)[: _SCOPE_CACHE_MAX // 8]:
            _scope_cache.pop(key, None)
    _scope_cache[session_id] = scope


async def _scope_for(user_id: str, session_id: str) -> SandboxScope:
    """The sandbox scope serving ``session_id`` (cached; resolver-aware)."""
    cached = _scope_cache.get(session_id)
    if cached is not None:
        return cached
    scope: SandboxScope | None = None
    if _scope_resolver is not None:
        try:
            scope = await _scope_resolver(user_id, session_id)
        except Exception:  # noqa: BLE001 — resolver failure degrades to session scope
            logger.debug("sandbox scope resolver failed for %s", session_id, exc_info=True)
    if scope is None:
        scope = SandboxScope(kind="session", id=session_id)
    _scope_cache_put(session_id, scope)
    return scope


def _accepts(fn: Any, name: str) -> bool:
    """Whether an allocator method takes the (additive) ``name`` kwarg.

    Allocators written against an older port signature keep working — they are
    simply never handed the kwarg they don't declare (additive contract)."""
    import inspect

    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):  # builtins / exotic callables
        return False


async def _kernel_for(
    user_id: str,
    scope: SandboxScope | None = None,
    *,
    new_turn: bool = False,
    session_id: str = "",
) -> KernelClient:
    """Resolve the execution kernel client for ``user_id`` via the allocator.

    ``new_turn`` marks a fresh conversation turn (``run_turn``); a scoped
    allocator may provision a NEW instance per turn for chat. Passed only when
    the bound allocator's ``ensure`` accepts it (additive contract).

    ``session_id`` is the host-preminted id of the session this allocation
    serves; a task-scoped allocator may stamp it into instance metadata when a
    member reuses the shared task instance. Additive (``_accepts``-gated) - old
    allocators ignore it."""
    from valuz_agent.ports.extensions import ext

    alloc = getattr(ext, "sandbox_allocator", None)
    if alloc is None:
        return client  # no allocator bound → process-global client (current behavior)
    kwargs: dict[str, Any] = {"owner_user_id": user_id}
    if scope is not None and _accepts(alloc.ensure, "scope"):
        kwargs["scope"] = scope
        if new_turn and _accepts(alloc.ensure, "new_turn"):
            kwargs["new_turn"] = True
    if session_id and _accepts(alloc.ensure, "session_id"):
        kwargs["session_id"] = session_id
    lease = await alloc.ensure(**kwargs)
    if lease is None or lease.endpoint is None:
        return client  # "use the process/global client" (BootSingletonAllocator default)
    ep = lease.endpoint
    cached = _endpoint_clients.get(ep.base_url)
    if cached is None:
        from valuz_agent.adapters.kernel_client_http import HttpKernelClient

        cached = HttpKernelClient(ep.base_url, token=ep.token)
        _endpoint_clients[ep.base_url] = cached
    return cached


async def _kernel_for_existing(
    user_id: str, scope: SandboxScope | None = None
) -> KernelClient | None:
    """Resolve the owner's EXISTING kernel for a live tap — never provisions.

    Used by GLOBAL-LIVE (``subscribe_all_events``): opening the decision inbox
    must not spin up a sandbox. Returns ``None`` when the owner has no live
    kernel (caller relies on the durable snapshot). No allocator, or a
    boot-singleton lease (``endpoint=None``) → the process-global ``client``
    (local single-user, in-process kernel) — behavior unchanged.
    """
    from valuz_agent.ports.extensions import ext

    alloc = getattr(ext, "sandbox_allocator", None)
    if alloc is None:
        return client
    peek = getattr(alloc, "peek", None)
    if peek is None:
        return client  # allocator predates the peek seam → best-effort global client
    if scope is not None and _accepts(peek, "scope"):
        lease = await peek(owner_user_id=user_id, scope=scope)
    else:
        lease = await peek(owner_user_id=user_id)
    if lease is None:
        return None  # no live kernel for this owner → no live tap
    if lease.endpoint is None:
        return client  # boot-singleton default → process-global client
    ep = lease.endpoint
    cached = _endpoint_clients.get(ep.base_url)
    if cached is None:
        from valuz_agent.adapters.kernel_client_http import HttpKernelClient

        cached = HttpKernelClient(ep.base_url, token=ep.token)
        _endpoint_clients[ep.base_url] = cached
    return cached


# Identity of "the process-global client". A host with no allocator, or one on
# the boot-singleton default, has exactly ONE kernel for its whole life, so this
# constant never changes and identity-watching consumers never rebind — local /
# desktop behaviour is untouched.
_PROCESS_KERNEL_ID = ""


async def current_kernel_id(user_id: str, session_id: str) -> str | None:
    """Opaque identity of the kernel serving ``session_id`` right now, or
    ``None`` when the owner has no live kernel. NEVER provisions (peek-only).

    Exists because a session's kernel is a MOVING TARGET under scoped
    allocation: chat provisions a fresh instance per turn, so a long-lived
    subscriber has to be able to notice that the session it follows has been
    handed to a different sandbox. Callers only ever compare the value for
    equality; ``base_url`` backs it because that is what actually decides which
    endpoint a client connects to (see ``_endpoint_clients``).
    """
    from valuz_agent.ports.extensions import ext

    alloc = getattr(ext, "sandbox_allocator", None)
    if alloc is None:
        return _PROCESS_KERNEL_ID
    peek = getattr(alloc, "peek", None)
    if peek is None:
        return _PROCESS_KERNEL_ID  # allocator predates the peek seam
    scope = await _scope_for(user_id, session_id)
    if _accepts(peek, "scope"):
        lease = await peek(owner_user_id=user_id, scope=scope)
    else:
        lease = await peek(owner_user_id=user_id)
    if lease is None:
        return None
    return _PROCESS_KERNEL_ID if lease.endpoint is None else lease.endpoint.base_url


async def create_session(
    user_id: str, req: CreateSessionRequest, *, scope: SandboxScope | None = None
) -> SessionData:
    # Scope precedence: explicit (tasks pass ``task:{task_id}`` — the
    # ``valuz_task_session`` row does not exist yet at creation time, so the
    # resolver can't see it) → derived from the host-preminted ``req.id`` →
    # None (owner-singleton) when the caller let the kernel mint the id.
    req_id = getattr(req, "id", None)
    if scope is None and req_id:
        scope = await _scope_for(user_id, req_id)
    elif scope is not None and req_id:
        _scope_cache_put(req_id, scope)
    return await (
        await _kernel_for(user_id, scope, session_id=req_id or "")
    ).create_session(user_id, req)


async def runtime_availability() -> dict[str, RuntimeAvailability]:
    """Per-runtime availability from the process-global kernel client.

    Host-scoped (no ``user_id``) — routes to the process/boot-attached kernel:
    in-process for the bundled desktop (local-host probe), the boot sandbox when
    one is attached. A per-user execution kernel is an overlay concern (§8)."""
    return await client.runtime_availability()


async def bg_busy_session_ids() -> list[str]:
    """Sessions whose warm runtime carries a live background task (process-
    scoped, id-only — intersect with an owner-scoped session set)."""
    return await client.bg_busy_session_ids()


async def get_session(user_id: str, session_id: str) -> SessionData | None:
    return await _data_plane().get_session(user_id, session_id)


async def list_sessions(
    user_id: str,
    *,
    status: str | None = None,
    ids: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionData]:
    return await _data_plane().list_sessions(
        user_id, status=status, ids=ids, limit=limit, offset=offset
    )


async def list_all_sessions(
    *,
    status: str | None = None,
    ids: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionData]:
    """Cross-owner session list — startup recovery + host aggregators only.
    Every request-serving caller uses ``list_sessions(user_id, ...)``."""
    return await _data_plane().list_all_sessions(status=status, ids=ids, limit=limit, offset=offset)


# Control writes route live-kernel-first (see ``_control_kernel``): the live
# runtime is the single writer for its session; the durable is written directly
# only for at-rest sessions.


async def update_session(user_id: str, session_id: str, req: UpdateSessionRequest) -> SessionData:
    return await (await _control_kernel(user_id, session_id)).update_session(
        user_id, session_id, req
    )


async def delete_session(user_id: str, session_id: str) -> bool:
    return await (await _control_kernel(user_id, session_id)).delete_session(user_id, session_id)


async def set_mode(user_id: str, session_id: str, mode: str) -> SessionData:
    return await (await _control_kernel(user_id, session_id)).set_mode(user_id, session_id, mode)


async def finalize_session(
    user_id: str, session_id: str, req: FinalizeSessionRequest
) -> SessionData:
    return await (await _control_kernel(user_id, session_id)).finalize_session(
        user_id, session_id, req
    )


async def append_event(user_id: str, session_id: str, event: EventPayload) -> bool:
    return await (await _control_kernel(user_id, session_id)).append_event(
        user_id, session_id, event
    )


async def emit_live_event(user_id: str, session_id: str, type: str, data: dict[str, Any]) -> None:
    # Live-only broadcast: with no live kernel there is nobody to receive it —
    # peek (never provision) and no-op. Provisioning a sandbox just to emit an
    # ephemeral frame was pure waste (and, under scoped allocation, would spin
    # an instance on every host-side emit for an idle session).
    k = await _kernel_for_existing(user_id, await _scope_for(user_id, session_id))
    if k is None:
        return
    await k.emit_live_event(user_id, session_id, type, data)


async def get_events(
    user_id: str,
    session_id: str,
    *,
    limit: int = 200,
    offset: int = 0,
    after_seq: int | None = None,
) -> list[EventData]:
    return await _data_plane().get_events(
        user_id, session_id, limit=limit, offset=offset, after_seq=after_seq
    )


async def get_events_window(
    user_id: str, session_id: str, *, before_seq: int | None = None, turn_limit: int = 20
) -> EventWindowData:
    return await _data_plane().get_events_window(
        user_id, session_id, before_seq=before_seq, turn_limit=turn_limit
    )


async def subscribe_session_events(user_id: str, session_id: str) -> AsyncIterator[EventData]:
    k = await _kernel_for(user_id, await _scope_for(user_id, session_id))
    async for event in k.subscribe_session_events(user_id, session_id):
        yield event


async def subscribe_session_events_existing(
    user_id: str, session_id: str
) -> AsyncIterator[EventData]:
    """Live tap on ONE session's stream via the EXISTING kernel — never provisions.

    The SSE adapter uses this so that opening a (historical) conversation never
    spins up a sandbox: history is served from the durable store; the live tap
    only attaches when the session's kernel is already running (the adapter
    re-peeks periodically to catch a kernel that comes up mid-stream). Yields
    nothing when there is no live kernel for the session's scope.
    """
    k = await _kernel_for_existing(user_id, await _scope_for(user_id, session_id))
    if k is None:
        return
    async for event in k.subscribe_session_events(user_id, session_id):
        yield event


def subscribe_all_events(
    types: tuple[str, ...] | None = None,
) -> AsyncIterator[EventData]:
    """Process-global live tap (all sessions of the process/boot kernel).

    Unchanged: used by the decision aggregator in LOCAL / single-kernel mode.
    Multi-tenant hosts use :func:`subscribe_all_events_for` instead.
    """
    return client.subscribe_all_events(types)


async def subscribe_all_events_for(
    user_id: str, types: tuple[str, ...] | None = None
) -> AsyncIterator[EventData]:
    """Live tap on ONE owner's cross-session event stream (GLOBAL-LIVE, remote).

    Routed to that owner's EXISTING kernel via ``_kernel_for_existing`` (never
    provisions). A multi-tenant host runs one kernel per owner, so that kernel's
    "all events" stream IS the owner's cross-session stream. Yields nothing when
    the owner has no live kernel — callers rely on the durable snapshot.
    ``types`` is an optional event-type allowlist, filtered at the source
    (in-process: before translation; remote: server-side via ``?types=``).
    """
    k = await _kernel_for_existing(user_id)
    if k is None:
        return
    async for event in k.subscribe_all_events(types):
        yield event


async def usage_rollup(user_id: str, start_ms: int, end_ms: int) -> list[UsageRollupData]:
    return await _data_plane().usage_rollup(user_id, start_ms, end_ms)


async def list_messages(
    user_id: str, session_id: str, *, limit: int = 50, offset: int = 0
) -> list[MessageData]:
    return await _data_plane().list_messages(user_id, session_id, limit=limit, offset=offset)


async def get_message(user_id: str, message_id: str) -> MessageData | None:
    return await _data_plane().get_message(user_id, message_id)


async def import_message(
    user_id: str,
    session_id: str,
    req: ImportMessageRequest,
) -> MessageData:
    return await _data_plane().import_message(user_id, session_id, req)


async def latest_message_id(user_id: str, session_id: str) -> str | None:
    messages = await _data_plane().list_messages(user_id, session_id, limit=1)
    return messages[0].id if messages else None


async def submit_action(user_id: str, session_id: str, req: SubmitActionRequest) -> dict[str, Any]:
    k = await _kernel_for(user_id, await _scope_for(user_id, session_id))
    return await k.submit_action(user_id, session_id, req)


async def interrupt(user_id: str, session_id: str) -> None:
    k = await _kernel_for(user_id, await _scope_for(user_id, session_id))
    await k.interrupt(user_id, session_id)


async def prepare_runtime(user_id: str, session_id: str) -> None:
    k = await _kernel_for(user_id, await _scope_for(user_id, session_id))
    await k.prepare_runtime(user_id, session_id)


async def run_turn(
    user_id: str,
    session_id: str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    additional_context: str = "",
    *,
    pre_turn: Callable[[], Awaitable[None]] | None = None,
) -> MessageData:
    """Drive one turn on the session's execution kernel.

    ``pre_turn`` is the per-turn capability convergence hook (always-on MCP
    re-stamp, docs capabilities, citation policy — see
    ``modules/sessions/pre_turn``). It MUST run here, between allocation and
    the turn, and callers must not run it themselves beforehand:

    Those refreshers write through ``update_session``, which routes
    live-kernel-first via ``_control_kernel`` → ``peek`` — and ``peek`` never
    provisions. Run before allocation, an at-rest session has no live kernel,
    so the write lands on the DURABLE only. The scoped allocator then boots
    this turn's instance and seeds its runtime sqlite from the scope's COS
    snapshot, and the kernel has no remote read path — so the durable write is
    simply never read, and the turn runs on whatever the snapshot froze.

    That is not hypothetical: it is why external connector MCPs 401'd on every
    turn of a conversation resumed after its ~1h OAuth bearer expired. The
    re-stamp minted a fresh token each turn, wrote it to the durable, and the
    sandbox kept using the fossil from the snapshot — with the connectors page
    truthfully reporting "connected" the whole time. (Below the sandbox's idle
    grace the previous instance is still live, the write reaches it, and the
    retire write-back carries it forward — which is exactly why the failure
    only ever showed up on long-idle sessions.)

    Hooks are best-effort by contract: a refresh failure degrades a capability,
    it must never sink the turn.
    """
    # new_turn=True: a scoped allocator may run a fresh instance for this turn
    # (chat = per-turn instance; task reuses its shared one). See sandbox §2.
    k = await _kernel_for(user_id, await _scope_for(user_id, session_id), new_turn=True)
    if pre_turn is not None:
        token = _pinned_control_kernel.set((session_id, k))
        try:
            await pre_turn()
        except Exception:  # noqa: BLE001 — a hook must never block a turn
            logger.warning("pre-turn hook failed for session %s", session_id, exc_info=True)
        finally:
            _pinned_control_kernel.reset(token)
    return await k.run_turn(user_id, session_id, text, attachments, additional_context)


async def run_ephemeral_review_in_scope(
    user_id: str,
    req: CreateSessionRequest,
    prompt: str,
    *,
    reuse_scope: SandboxScope,
) -> str | None:
    """Create + run + delete a throwaway (no-persistence) review session INSIDE
    ``reuse_scope``'s ALREADY-LIVE remote sandbox, returning the assistant text —
    or ``None`` when there is no live remote sandbox to reuse (the caller then
    runs the review its normal way).

    The memory reviewer uses this to run inside the SOURCE session's still-warm
    sandbox instead of cold-provisioning its own. Two properties matter and are
    the reason this doesn't just call ``create_session`` + ``run_turn``:

    - **Never provisions** — it peeks (``_kernel_for_existing``); if ``reuse_scope``
      has no live sandbox it returns ``None``. No sandbox is spun up on this path.
    - **Never renews** — the normal ``run_turn`` path calls ``ensure`` which pushes
      the instance's AGS TTL back to the 24h active window. That would defeat the
      post-turn idle clamp that keeps the source sandbox alive for exactly this
      review window, orphaning it. Reusing the peeked kernel directly leaves the
      clamp's countdown intact, so the sandbox still expires on schedule.

    Returns ``None`` unless a scoped allocator hands back a live REMOTE sandbox
    for ``reuse_scope`` — i.e. it's inert (returns ``None``) for the local /
    single-kernel deployment (no per-scope sandbox to reuse or renew), where the
    caller's ordinary create/run/delete path already targets the one kernel.

    The ephemeral session is routed to ``reuse_scope`` (scope cache) and its
    durable record is deleted in ``finally``; the sandbox itself is the source's
    and is NEVER released here — its lifecycle stays with the source's clamp.
    """
    k = await _kernel_for_existing(user_id, reuse_scope)
    if k is None or k is client:
        # ``None``: scoped allocator but the source sandbox is already gone.
        # ``client``: no scoped allocator (or a boot-singleton lease) — the local
        # single-kernel case has no per-scope sandbox to reuse/renew. Either way,
        # let the caller run the review its normal way.
        return None
    req_id = getattr(req, "id", "") or ""
    if req_id:
        _scope_cache_put(req_id, reuse_scope)  # any stray op stays in-scope
    try:
        await k.create_session(user_id, req)
        msg = await k.run_turn(user_id, req_id, prompt)
        return msg.assistant_message or ""
    finally:
        try:
            await _data_plane().delete_session(user_id, req_id)  # durable record cleanup
        except Exception:  # noqa: BLE001 — best-effort throwaway cleanup
            logger.debug("ephemeral review: durable cleanup failed for %s", req_id)
        _scope_cache.pop(req_id, None)


async def scan_orphan_pendings() -> int:
    return await client.scan_orphan_pendings()  # type: ignore[attr-defined]


async def scan_orphan_runs() -> int:
    return await client.scan_orphan_runs()  # type: ignore[attr-defined]


async def reset_stranded_session(user_id: str, session_id: str) -> bool:
    """Per-session stranded reset (host-driven recovery, multi-sandbox-safe).

    The HOST decides which ``running`` sessions are genuinely stranded (their
    sandbox is gone — ``ext.sandbox_allocator.peek``) and applies the kernel's
    reset semantics (``src.core.recovery``: seal pendings, stamp ``idle`` +
    resumable ``host_restart``, error out running messages) DIRECTLY to the
    durable via the data plane — the stranded session's runtime store died
    with its sandbox, so there is no kernel to round-trip through."""
    return await _data_plane().reset_stranded_session(user_id, session_id)  # type: ignore[attr-defined]


async def cleanup_runtime(session_id: str) -> None:
    await client.cleanup_runtime(session_id)  # type: ignore[attr-defined]

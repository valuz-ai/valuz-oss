"""Background polling driver for cloud parser tasks.

Two collaborators
-----------------

- ``PollingHandler`` — provided by each cloud parser plugin
  (PaddleOCR, MinerU, …). Implements three callbacks:
  ``submit(payload) -> external_id``, ``poll(external_id, payload) ->
  PollOutcome``, ``fetch_result(external_id, payload, raw) -> ParseResult``.
  These are synchronous (blocking HTTP); the scheduler runs them off the
  event loop via ``asyncio.to_thread`` so a slow remote never blocks the loop.

- ``PollingScheduler`` — an **on-loop** asyncio task (mirrors
  ``InProcessScheduleRunner`` / ``InProcessAutomationRunner``). A periodic tick
  scans ``valuz_polling_task`` rows that are due, dispatches to the matching
  handler by ``kind``, updates rows through the async store, and resolves any
  in-process ``await_task`` futures when a task terminates.

Everything — the tick loop, the DB I/O, and the ``await_task`` futures — lives
on the one app event loop, so there is no cross-thread / cross-loop bridge:
``await_task`` simply awaits a future that the tick coroutine resolves directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from valuz_agent.infra.auth_context import require_current_user_id
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.parser.datastore import PollingTaskDatastore
from valuz_agent.modules.parser.models import PollingTaskRow
from valuz_agent.ports.parser_backend import ParseResult

logger = logging.getLogger(__name__)


# ----- outcomes returned by handlers ----------------------------------


@dataclass(frozen=True)
class PollPending:
    """Handler reports the remote task is still working. ``next_in_s``
    is a hint for the scheduler; the scheduler is free to clamp it
    against its own exponential-backoff window."""

    next_in_s: float = 5.0


@dataclass(frozen=True)
class PollSucceeded:
    """Handler reports the remote task is done. ``raw`` is whatever
    blob the handler will pass back to ``fetch_result`` to produce the
    final ``ParseResult``."""

    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PollFailed:
    """Handler reports a terminal failure."""

    error: str


PollOutcome = PollPending | PollSucceeded | PollFailed


# ----- handler protocol ----------------------------------------------


class PollingHandler(Protocol):
    """One per cloud parser. ``kind`` matches the value the plugin
    stores in ``PollingTaskRow.kind`` at enqueue time."""

    kind: str

    # Default polling cadence; can be overridden per-task by handlers
    # that learn a smarter hint from response headers (Retry-After, etc.).
    initial_delay_s: float
    max_delay_s: float
    max_attempts: int

    def submit(self, payload: Mapping[str, Any]) -> str:
        """Submit work to the remote service. Returns the external task id."""

    def poll(self, external_task_id: str, payload: Mapping[str, Any]) -> PollOutcome:
        """Ask the remote service for status. Pure I/O."""

    def fetch_result(
        self, external_task_id: str, payload: Mapping[str, Any], raw: Mapping[str, Any]
    ) -> ParseResult:
        """Download / decode the final result. Runs once per task after
        ``poll`` returns ``PollSucceeded``."""


# ----- scheduler -----------------------------------------------------


class PollingTimeout(Exception):  # noqa: N818  # domain-style naming
    """Raised when a task exceeds ``max_attempts`` without terminating."""


class PollingScheduler:
    """On-loop asyncio task that drives polling-task state transitions.

    Lifecycle:
    - ``await startup()`` — start the tick task on the running loop.
    - ``await enqueue(kind, payload)`` — insert a new row in pending state;
      the tick coroutine picks it up next iteration.
    - ``await await_task(task_id)`` — blocks until the task terminates.
      Returns the ``ParseResult`` or raises on failure.
    - ``await shutdown()`` — stop the tick task; pending awaiters get a
      sensible error.
    """

    # The tick scans for due rows at this interval. Tightening below ~0.5s
    # gains us nothing — the polling cadence is bounded by the remote
    # service's reasonable Retry-After anyway.
    _TICK_INTERVAL_S: float = 1.0

    def __init__(self, handlers: list[PollingHandler]) -> None:
        self._handlers: dict[str, PollingHandler] = {}
        for handler in handlers:
            if handler.kind in self._handlers:
                raise ValueError(f"duplicate polling handler kind: {handler.kind}")
            self._handlers[handler.kind] = handler

        self._tick_task: asyncio.Task[None] | None = None
        # Per task_id: futures awaiting termination. Resolved on the loop.
        self._futures: dict[str, list[asyncio.Future[ParseResult]]] = {}
        # The event loop the tick runs on (captured at ``startup``). Sync
        # callers on a *different* loop/thread (e.g. the docs reindex worker)
        # must drive ``parse`` here via ``run_coroutine_threadsafe`` — the
        # awaiter futures in ``_futures`` are bound to this loop.
        self._loop: asyncio.AbstractEventLoop | None = None

    # ----- handler registration -------------------------------------

    def register(self, handler: PollingHandler) -> None:
        """Add a handler after construction. Useful when handlers depend
        on lazy state (e.g. an API key the user hasn't entered yet)."""
        if handler.kind in self._handlers:
            raise ValueError(f"duplicate polling handler kind: {handler.kind}")
        self._handlers[handler.kind] = handler

    # ----- lifecycle ------------------------------------------------

    async def startup(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self._tick_task is not None and not self._tick_task.done():
            return
        self._tick_task = asyncio.create_task(self._tick_loop(), name="polling-scheduler")

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """The loop the scheduler tick runs on (None before ``startup``)."""
        return self._loop

    async def shutdown(self) -> None:
        task = self._tick_task
        self._tick_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # Fail any leftover awaiters so callers don't hang forever.
        pending = self._futures
        self._futures = {}
        for futures in pending.values():
            for f in futures:
                if not f.done():
                    f.set_exception(RuntimeError("scheduler stopped"))

    # ----- public API -----------------------------------------------

    async def enqueue(self, kind: str, payload: Mapping[str, Any]) -> str:
        """Insert a fresh row in ``pending`` state and return its id."""
        if kind not in self._handlers:
            raise KeyError(f"no polling handler for kind: {kind}")
        task_id = uuid.uuid4().hex
        now = now_ms()
        row = PollingTaskRow(
            id=task_id,
            kind=kind,
            external_task_id=None,
            payload_json=json.dumps(dict(payload)),
            status="pending",
            attempt=0,
            next_poll_at=now,
            result_json="{}",
            error=None,
            created_at=now,
            updated_at=now,
        )
        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work() as db:
            await PollingTaskDatastore(db).insert(require_current_user_id(), row)
        return task_id

    async def await_task(self, task_id: str) -> ParseResult:
        """Block until the task terminates. Raises on failure or stop."""
        # Fast-path: already terminal by the time we register.
        row = await self._read_row(task_id)
        if row is None:
            raise KeyError(f"unknown polling task: {task_id}")
        if row.status == "succeeded":
            return _result_from_row(row)
        if row.status == "failed":
            raise RuntimeError(row.error or "polling task failed")
        if row.status == "cancelled":
            raise RuntimeError("polling task cancelled")

        future: asyncio.Future[ParseResult] = asyncio.get_running_loop().create_future()
        self._futures.setdefault(task_id, []).append(future)
        return await future

    async def cancel(self, task_id: str) -> None:
        """Mark a task cancelled. Awaiters get a ``RuntimeError``."""
        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work() as db:
            ds = PollingTaskDatastore(db)
            row = await ds.get(task_id)
            if row is None or row.status in ("succeeded", "failed", "cancelled"):
                return
            row.status = "cancelled"
            row.error = "cancelled by user"
            await ds.upsert(row.user_id, row)
        self._resolve_awaiters(task_id, error="cancelled by user")

    # ----- tick loop ------------------------------------------------

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("polling scheduler tick failed")
            await asyncio.sleep(self._TICK_INTERVAL_S)

    async def _tick(self) -> None:
        from valuz_agent.infra.db import async_unit_of_work

        now = now_ms()
        async with async_unit_of_work(commit=False) as db:
            due = await PollingTaskDatastore(db).list_due(now=now, limit=32)
        for row in due:
            await self._process_row(row)

    async def _process_row(self, row: PollingTaskRow) -> None:
        handler = self._handlers.get(row.kind)
        if handler is None:
            await self._terminate(row.id, status="failed", error=f"no handler for kind={row.kind}")
            return

        payload = _decode_payload(row.payload_json)

        if row.external_task_id is None:
            try:
                external_id = await asyncio.to_thread(handler.submit, payload)
            except Exception as exc:  # noqa: BLE001
                logger.exception("submit failed for %s", row.id)
                await self._terminate(row.id, status="failed", error=_short(exc))
                return
            await self._write_row_update(
                row.id,
                external_task_id=external_id,
                status="running",
                next_poll_at=now_ms() + int(handler.initial_delay_s * 1000),
            )
            return

        # Poll phase
        try:
            outcome = await asyncio.to_thread(handler.poll, row.external_task_id, payload)
        except Exception as exc:  # noqa: BLE001
            await self._handle_attempt_failure(row, handler, _short(exc))
            return

        if isinstance(outcome, PollSucceeded):
            try:
                result = await asyncio.to_thread(
                    handler.fetch_result, row.external_task_id, payload, dict(outcome.raw)
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("fetch_result failed for %s", row.id)
                await self._terminate(row.id, status="failed", error=_short(exc))
                return
            await self._terminate(row.id, status="succeeded", result=result)
            return

        if isinstance(outcome, PollFailed):
            await self._terminate(row.id, status="failed", error=outcome.error)
            return

        # Pending: reschedule
        await self._handle_attempt_pending(row, handler, outcome)

    async def _handle_attempt_pending(
        self,
        row: PollingTaskRow,
        handler: PollingHandler,
        outcome: PollPending,
    ) -> None:
        new_attempt = row.attempt + 1
        if new_attempt >= handler.max_attempts:
            await self._terminate(
                row.id,
                status="failed",
                error=f"timed out after {handler.max_attempts} polls",
            )
            return
        # Exponential backoff capped at handler.max_delay_s, but never
        # below the handler's own hint (so the remote service's
        # Retry-After is respected).
        backoff = min(
            handler.max_delay_s,
            max(outcome.next_in_s, handler.initial_delay_s * (2**new_attempt)),
        )
        await self._write_row_update(
            row.id,
            status="running",
            attempt=new_attempt,
            next_poll_at=now_ms() + int(backoff * 1000),
        )

    async def _handle_attempt_failure(
        self,
        row: PollingTaskRow,
        handler: PollingHandler,
        error: str,
    ) -> None:
        """A poll RPC failed transiently — treat like Pending for the
        first few tries, then escalate to terminal."""
        new_attempt = row.attempt + 1
        if new_attempt >= handler.max_attempts:
            await self._terminate(row.id, status="failed", error=error)
            return
        backoff = min(handler.max_delay_s, handler.initial_delay_s * (2**new_attempt))
        await self._write_row_update(
            row.id,
            status="running",
            attempt=new_attempt,
            next_poll_at=now_ms() + int(backoff * 1000),
            error=error,
        )

    async def _terminate(
        self,
        task_id: str,
        *,
        status: str,
        result: ParseResult | None = None,
        error: str | None = None,
    ) -> None:
        result_json = "{}"
        if result is not None:
            result_json = json.dumps(
                {
                    "markdown": result.markdown,
                    "page_count": result.page_count,
                    "metadata": result.metadata,
                }
            )
        await self._write_row_update(
            task_id,
            status=status,
            error=error,
            result_json=result_json,
        )
        if status == "succeeded" and result is not None:
            self._resolve_awaiters(task_id, result=result)
        else:
            self._resolve_awaiters(task_id, error=error or "polling task failed")

    def _resolve_awaiters(
        self,
        task_id: str,
        *,
        result: ParseResult | None = None,
        error: str | None = None,
    ) -> None:
        for f in self._futures.pop(task_id, []):
            if f.done():
                continue
            if result is not None:
                f.set_result(result)
            else:
                f.set_exception(RuntimeError(error or "polling task failed"))

    # ----- db helpers ---------------------------------------------

    async def _read_row(self, task_id: str) -> PollingTaskRow | None:
        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work(commit=False) as db:
            return await PollingTaskDatastore(db).get(task_id)

    async def _write_row_update(self, task_id: str, **fields: Any) -> None:
        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work() as db:
            ds = PollingTaskDatastore(db)
            row = await ds.get(task_id)
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)
            await ds.upsert(row.user_id, row)


# ----- helpers --------------------------------------------------------


def _decode_payload(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _result_from_row(row: PollingTaskRow) -> ParseResult:
    try:
        data = json.loads(row.result_json or "{}")
    except (TypeError, ValueError):
        data = {}
    return ParseResult(
        markdown=str(data.get("markdown", "")),
        page_count=int(data.get("page_count", 0)),
        metadata=dict(data.get("metadata", {})),
    )


def _short(exc: BaseException) -> str:
    msg = str(exc) or exc.__class__.__name__
    return msg[:240]

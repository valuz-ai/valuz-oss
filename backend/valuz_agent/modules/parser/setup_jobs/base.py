"""SetupJob protocol + controller (on-loop state machine).

Model
-----
- One ``SetupJobController`` instance for the whole process.
- ``await start(setup_id)`` schedules the job as an asyncio task on the app
  loop. The actual download (blocking ``httpx``) runs off the loop via
  ``asyncio.to_thread`` so it never blocks the loop; the controller writes the
  row's status + progress through the async store.
- A ``threading.Event`` is created per active job. ``await cancel(setup_id)``
  sets it; the job checks it inside its download loop.
- The job never touches the DB. It reports progress via ``progress_cb`` (a
  ``(downloaded, total) -> None`` callback). Because the callback is invoked
  from the ``to_thread`` worker, it bridges back to the loop with
  ``asyncio.run_coroutine_threadsafe`` to persist on the async engine.
- Status reads (``await get``) are async DB reads.

All DB access is on the one app loop (async engine) — no host sync engine.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.parser.datastore import SetupJobDatastore
from valuz_agent.modules.parser.models import SetupJobRow

logger = logging.getLogger(__name__)


class SetupJobNotFound(KeyError):  # noqa: N818  # domain-style naming
    """Raised when a setup_id is referenced that no job class handles."""


class SetupJobAlreadyRunning(RuntimeError):  # noqa: N818  # domain-style naming
    """Raised when ``start`` is called while a task is already mid-run."""


@dataclass(frozen=True)
class SetupJobStatus:
    """Snapshot of one setup_id's state. Returned by the controller +
    serialized over the HTTP API."""

    setup_id: str
    status: str  # pending|running|succeeded|failed|cancelled
    downloaded_bytes: int
    total_bytes: int | None
    error: str | None
    source: str | None
    started_at: int | None
    completed_at: int | None
    updated_at: int | None


ProgressCallback = Callable[[int, int | None], None]
"""``(downloaded_bytes, total_bytes) -> None``. The controller hands one to
each job; calling it persists progress on the job's row. The job never opens a
DB session or touches an engine — the controller owns persistence."""


class SetupJob(Protocol):
    """Per-setup-id worker.

    Implementations are stateless; per-run state lives on the
    ``SetupJobRow`` and the ``threading.Event`` passed via ``run``.
    """

    setup_id: str

    def run(
        self,
        *,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        """Execute the work. The controller invokes this off the event loop
        (``asyncio.to_thread``), so ``run`` may use blocking I/O freely.

        Implementation contract:

        1. Report progress at roughly 1Hz via ``progress_cb(downloaded,
           total)`` — the controller persists it. The job never opens a DB
           session.
        2. Check ``cancel_event.is_set()`` periodically and exit cleanly when
           set — the controller writes ``status="cancelled"``, but ``run`` must
           not leave partially-downloaded files in place (stream to
           ``*.partial`` and ``rename`` only on full success).
        3. Raise on hard errors — the controller wraps the exception and writes
           ``status="failed"`` with a short ``error`` message.
        """

    def is_complete(self) -> bool:
        """Return True if the setup work is already done in the local
        filesystem (e.g. the READY marker exists). Used by the router's
        capability gate without touching the DB."""


class SetupJobController:
    """Singleton orchestrator for setup jobs.

    Owns the per-setup-id asyncio task + cancel event. Routes ``await``
    ``start`` / ``get`` / ``cancel``; the controller never blocks the request
    on download work (it runs in a ``to_thread`` worker)."""

    def __init__(self, jobs: list[SetupJob]) -> None:
        self._jobs: dict[str, SetupJob] = {}
        for job in jobs:
            if job.setup_id in self._jobs:
                raise ValueError(f"duplicate setup_id: {job.setup_id}")
            self._jobs[job.setup_id] = job
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_events: dict[str, threading.Event] = {}

    # ----- registration introspection ---------------------------------

    def known_setup_ids(self) -> list[str]:
        return list(self._jobs.keys())

    def has(self, setup_id: str) -> bool:
        return setup_id in self._jobs

    # ----- status -----------------------------------------------------

    async def get(self, setup_id: str) -> SetupJobStatus:
        if setup_id not in self._jobs:
            raise SetupJobNotFound(setup_id)

        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work(commit=False) as db:
            row = await SetupJobDatastore(db).get(setup_id)

        if row is None:
            # Lazy: a setup_id known to the registry but never started yet.
            # Synthesise a "pending, never started" snapshot — that's what
            # the UI wants to render the "Download" button.
            return SetupJobStatus(
                setup_id=setup_id,
                status="pending",
                downloaded_bytes=0,
                total_bytes=None,
                error=None,
                source=None,
                started_at=None,
                completed_at=None,
                updated_at=None,
            )

        return SetupJobStatus(
            setup_id=row.setup_id,
            status=row.status,
            downloaded_bytes=row.downloaded_bytes,
            total_bytes=row.total_bytes,
            error=row.error,
            source=row.source,
            started_at=row.started_at,
            completed_at=row.completed_at,
            updated_at=row.updated_at,
        )

    def is_complete(self, setup_id: str) -> bool:
        """Fast path for the router's capability gate. Does NOT consult the DB
        — only the filesystem marker. (If a user nukes the marker the next
        router call surfaces ``needs_setup`` cleanly even though the DB still
        says ``succeeded``.)"""
        if setup_id not in self._jobs:
            return False
        try:
            return self._jobs[setup_id].is_complete()
        except Exception:
            logger.exception("is_complete probe for %s raised", setup_id)
            return False

    # ----- lifecycle --------------------------------------------------

    async def start(self, setup_id: str) -> SetupJobStatus:
        """Kick off the job as an asyncio task.

        Idempotency: if a task for this ``setup_id`` is already running, raise
        ``SetupJobAlreadyRunning`` (the route returns 409). If the job's
        filesystem state already satisfies ``is_complete()`` we short-circuit
        to ``succeeded`` without starting a task.
        """
        if setup_id not in self._jobs:
            raise SetupJobNotFound(setup_id)
        job = self._jobs[setup_id]

        existing = self._tasks.get(setup_id)
        if existing is not None and not existing.done():
            raise SetupJobAlreadyRunning(setup_id)

        if job.is_complete():
            await self._write_row(
                setup_id=setup_id,
                status="succeeded",
                downloaded_bytes=0,
                total_bytes=None,
                completed_at=now_ms(),
            )
            return await self.get(setup_id)

        cancel_event = threading.Event()
        self._cancel_events[setup_id] = cancel_event

        await self._write_row(
            setup_id=setup_id,
            status="running",
            downloaded_bytes=0,
            total_bytes=None,
            started_at=now_ms(),
            error=None,
            completed_at=None,
        )

        self._tasks[setup_id] = asyncio.create_task(
            self._run_job(setup_id, cancel_event), name=f"setup-job-{setup_id}"
        )
        return await self.get(setup_id)

    async def cancel(self, setup_id: str) -> SetupJobStatus:
        """Request cancellation. Returns immediately — the worker observes the
        event on its next chunk-loop iteration."""
        if setup_id not in self._jobs:
            raise SetupJobNotFound(setup_id)
        event = self._cancel_events.get(setup_id)
        if event is not None:
            event.set()
        return await self.get(setup_id)

    # ----- internal ---------------------------------------------------

    async def _run_job(self, setup_id: str, cancel_event: threading.Event) -> None:
        job = self._jobs[setup_id]
        progress_cb = self._make_progress_cb(setup_id)
        try:
            # The blocking download runs off the loop; progress_cb bridges back.
            await asyncio.to_thread(job.run, progress_cb=progress_cb, cancel_event=cancel_event)
            if cancel_event.is_set():
                await self._write_row(setup_id=setup_id, status="cancelled", completed_at=now_ms())
            else:
                await self._write_row(
                    setup_id=setup_id,
                    status="succeeded",
                    completed_at=now_ms(),
                    error=None,
                )
        except Exception as exc:  # noqa: BLE001 — capture everything
            logger.exception("setup job %s failed", setup_id)
            await self._write_row(
                setup_id=setup_id,
                status="failed",
                error=_short_error(exc),
                completed_at=now_ms(),
            )
        finally:
            self._tasks.pop(setup_id, None)
            self._cancel_events.pop(setup_id, None)

    def _make_progress_cb(self, setup_id: str) -> ProgressCallback:
        """A progress callback bound to ``setup_id``. Invoked from the
        ``to_thread`` download worker, it bridges the persist coroutine back to
        the loop via ``run_coroutine_threadsafe``."""
        loop = asyncio.get_running_loop()

        def _cb(downloaded_bytes: int, total_bytes: int | None) -> None:
            fut = asyncio.run_coroutine_threadsafe(
                self._update_progress(setup_id, downloaded_bytes, total_bytes), loop
            )
            try:
                fut.result(timeout=15.0)
            except Exception:  # noqa: BLE001 — progress is best-effort
                logger.debug("progress persist failed for %s", setup_id, exc_info=True)

        return _cb

    async def _update_progress(
        self, setup_id: str, downloaded_bytes: int, total_bytes: int | None
    ) -> None:
        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work() as db:
            await SetupJobDatastore(db).update_progress(
                setup_id, downloaded_bytes=downloaded_bytes, total_bytes=total_bytes
            )

    async def _write_row(
        self,
        *,
        setup_id: str,
        status: str,
        downloaded_bytes: int | None = None,
        total_bytes: int | None = None,
        error: str | None = None,
        source: str | None = None,
        started_at: int | None = None,
        completed_at: int | None = None,
    ) -> None:
        from valuz_agent.infra.db import async_unit_of_work

        async with async_unit_of_work() as db:
            ds = SetupJobDatastore(db)
            current = await ds.get(setup_id)
            row = current or SetupJobRow(setup_id=setup_id)
            row.status = status
            if downloaded_bytes is not None:
                row.downloaded_bytes = downloaded_bytes
            if total_bytes is not None:
                row.total_bytes = total_bytes
            row.error = error
            if source is not None:
                row.source = source
            if started_at is not None:
                row.started_at = started_at
            if completed_at is not None:
                row.completed_at = completed_at
            await ds.upsert(row)


def _short_error(exc: BaseException) -> str:
    """User-facing one-line summary. Strip noisy framework prefixes."""
    msg = str(exc) or exc.__class__.__name__
    return msg[:240]


def build_default_setup_controller() -> SetupJobController:
    """Production setup-job set. Today: RapidOCR only."""
    from valuz_agent.modules.parser.setup_jobs.rapidocr import RapidOcrSetupJob

    return SetupJobController(jobs=[RapidOcrSetupJob()])

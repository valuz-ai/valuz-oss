"""Idle-debounced extraction trigger (memory-system-design §7.1).

The session run path finalizes after every turn (the session is idle between
turns), so firing extraction on every finalize would be per-turn-expensive.
Instead each turn (re)arms a delayed task; a new turn cancels and reschedules it,
so extraction fires once when the session truly goes quiet for ``delay`` seconds.

Production finalizers await MemoryScheduler durable registration. The legacy
in-process timer classes remain compatibility helpers for explicitly injected
runners; boot does not wire them to production extraction.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.memory.journal import ReviewJournal, ReviewLimits

logger = logging.getLogger(__name__)

# Debounce window: extraction fires once a session has been quiet this long (a
# new turn resets the timer). 60s is responsive (memory lands ~1 min after you
# stop) yet still longer than typical mid-conversation reading/typing pauses, so
# it rarely reviews an unfinished chat. Lower it for snappier dev testing; raise
# it to be stingier with the per-fire LLM call. (Trivially tunable; a candidate
# to expose as a setting later.)
IDLE_DELAY_SECONDS = 60.0

# Runs the actual extraction for (session_id, user_id) — wired to the runner at
# boot. user_id is captured at notify time because the delayed task fires outside
# the originating request's auth context.
Runner = Callable[[str, str | None], Awaitable[None]]


class IdleExtractionScheduler:
    def __init__(self, runner: Runner | None = None, *, delay: float = IDLE_DELAY_SECONDS) -> None:
        self._runner = runner
        self._delay = delay
        self._pending: dict[str, asyncio.Task[None]] = {}

    def set_runner(self, runner: Runner) -> None:
        """Wire the extraction runner (called at boot once the live path exists)."""
        self._runner = runner

    def notify_turn(self, session_id: str, user_id: str | None = None) -> None:
        if user_id is None:
            raise ValueError("user_id is required")

        """Call after a turn finalizes: (re)arm the session's idle timer. Cheap,
        non-blocking, no-op when no runner is wired or there's no running loop."""
        if self._runner is None or not session_id:
            return
        prev = self._pending.pop(session_id, None)
        if prev is not None and not prev.done():
            prev.cancel()
        try:
            self._pending[session_id] = asyncio.create_task(
                self._fire_after_idle(session_id, user_id)
            )
        except RuntimeError:
            # No running event loop (e.g. called from a sync context) — skip.
            logger.debug("idle scheduler: no running loop for %s", session_id)

    async def _fire_after_idle(self, session_id: str, user_id: str | None) -> None:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            return
        self._pending.pop(session_id, None)
        runner = self._runner
        if runner is None:
            return
        try:
            await runner(session_id, user_id)
        except Exception:  # noqa: BLE001 — extraction is best-effort
            logger.debug("idle extraction failed for %s", session_id, exc_info=True)


idle_scheduler = IdleExtractionScheduler()


# Task-finish trigger (memory-system-design §7.1). Unlike the idle scheduler this
# fires IMMEDIATELY — a finished task is a discrete, once-per-task signal, not an
# idle window, so there is nothing to debounce. Runs(task_id, user_id).
TaskRunner = Callable[[str, str | None], Awaitable[None]]


class TaskFinishScheduler:
    def __init__(self, runner: TaskRunner | None = None) -> None:
        self._runner = runner
        # Hold strong refs so fire-and-forget tasks aren't GC'd mid-flight.
        self._pending: set[asyncio.Task[None]] = set()

    def set_runner(self, runner: TaskRunner) -> None:
        """Wire the task-finish extraction runner (called at boot)."""
        self._runner = runner

    def notify_finished(self, task_id: str, user_id: str | None = None) -> None:
        """Call when a task reaches a terminal ``completed`` state. Cheap,
        non-blocking, no-op when no runner is wired or there's no running loop."""
        if self._runner is None or not task_id:
            return
        try:
            task = asyncio.create_task(self._run(task_id, user_id))
        except RuntimeError:
            logger.debug("task-finish scheduler: no running loop for %s", task_id)
            return
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _run(self, task_id: str, user_id: str | None) -> None:
        runner = self._runner
        if runner is None:
            return
        try:
            await runner(task_id, user_id)
        except Exception:  # noqa: BLE001 — extraction is best-effort
            logger.debug("task-finish extraction failed for %s", task_id, exc_info=True)


task_finish_scheduler = TaskFinishScheduler()


_task_finalized_wired = False


def wire_task_finalized_trigger() -> None:
    """Subscribe the task-finish extraction to the ``task.finalized`` topic.

    Event-first (the task module publishes its terminal contract event via
    ``tasks/events.finalize_task``; memory reacts here instead of the task
    module reaching into this scheduler). Idempotent; wired at boot next to
    ``set_runner``.
    """
    global _task_finalized_wired
    if _task_finalized_wired:
        return
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.tasks.events import TASK_FINALIZED

    def _on_finalized(*, task_id: str, owner_user_id: str, status: str, **_kw: object) -> None:
        # The real finalization path awaits durable enqueue. Legacy bus-only
        # callers remain supported without double-registering production jobs.
        if status == "completed" and not memory_scheduler.registered:
            task_finish_scheduler.notify_finished(task_id, owner_user_id)

    event_bus.subscribe(TASK_FINALIZED, _on_finalized)
    _task_finalized_wired = True


class MemoryScheduler:
    """Poll only already registered candidates; never invent jobs or user cron."""

    def __init__(
        self,
        *,
        journal: ReviewJournal | None = None,
        limits: ReviewLimits | None = None,
        registered: bool = True,
    ) -> None:
        from valuz_agent.modules.memory.journal import ReviewLimits, review_journal

        self.registered = registered
        self.journal = journal if journal is not None else review_journal
        self.limits = limits if limits is not None else ReviewLimits()
        self._task: asyncio.Task[None] | None = None

    async def notify_turn(self, session_id: str, user_id: str) -> None:
        if not self.registered:
            return
        from valuz_agent.infra.time_utils import now_ms

        await self.journal.schedule(
            user_id, "session", session_id, now_ms() + int(self.limits.idle_delay * 1000)
        )

    async def notify_finished(
        self, task_id: str, user_id: str, *, db: AsyncSession | None = None
    ) -> None:
        if not self.registered:
            return
        from valuz_agent.infra.time_utils import now_ms

        await self.journal.schedule(user_id, "task", task_id, now_ms(), db=db)

    async def run_due(self) -> None:
        from valuz_agent.modules.memory.recovery import dispatch

        for job in await self.journal.due():
            await dispatch(job, journal=self.journal, limits=self.limits)

    async def _poll(self) -> None:
        while True:
            try:
                await self.run_due()
            except Exception:  # noqa: BLE001 — keep registered work for bounded retry
                logger.debug("memory candidate polling unavailable", exc_info=True)
            await asyncio.sleep(self.limits.poll_interval)

    async def start(self) -> None:
        self.registered = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll(), name="memory-review-recovery")

    async def stop(self) -> None:
        self.registered = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


memory_scheduler = MemoryScheduler(registered=False)

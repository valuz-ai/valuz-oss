"""Polymorphic trigger evaluation.

Old schedule code keyed the tick loop off ``next_run_at <= now``. That works
for cron (croniter writes next_run_at on every fire) but bakes the
single-trigger assumption into the runner. The refactor introduces three
trigger kinds (cron / interval / manual; webhook to follow) and we want
adding the next one to be a delta of "register one more branch", not "rewrite
the runner".

``TriggerEvaluator`` is the seam. The runner calls:

  - ``next_fire_at(row, after)``  — what to write into ``row.next_run_at``
                                    after a fire completes. ``None`` for
                                    manual + future webhook (they're not
                                    tick-driven).
  - ``is_due(row, now)``          — quick yes/no the tick loop asks per row.

Cron / interval differ in *what* "next" means:

  - Cron is wall-clock anchored — ``croniter.get_next(now)`` is independent of
    when the last fire completed. Long-running fires don't drift the schedule.
  - Interval is *triggered_at* anchored — next = last triggered_at + N.
    Choosing triggered_at over completed_at keeps cadence stable when a fire
    occasionally runs long; ADR-021 Q3 deferred a configurable drift mode.
"""

from __future__ import annotations

from valuz_agent.modules.automations.cron_utils import CronInterpreter
from valuz_agent.modules.automations.models import AutomationRow

# Hard floor for interval triggers. The tick is 30s; anything below that races
# the tick and fires unpredictably. Server-side validation also enforces this,
# but keeping the constant here documents the load-bearing relationship.
MIN_INTERVAL_SECONDS = 30


class TriggerEvaluator:
    """Compute ``next_run_at`` and ``is_due`` for any trigger kind.

    Constructed once per ``AutomationService`` / ``AutomationRunner`` with the
    user-level default timezone — cron rows that left ``timezone`` NULL
    inherit this default.
    """

    def __init__(self, default_timezone: str = "UTC") -> None:
        self._default_tz = default_timezone or "UTC"
        self._cron = CronInterpreter()

    # ── Effective timezone resolution (cron-only) ─────────────────────

    def _effective_tz_for(self, row: AutomationRow) -> str:
        """Resolve a row's ``timezone`` against the user default.

        Mirrors ``ScheduleService.effective_tz_for_row`` so the cron tick
        semantics carry over unchanged from the legacy module.
        """
        return (row.timezone or "").strip() or self._default_tz

    # ── Public API ────────────────────────────────────────────────────

    def next_fire_at(self, row: AutomationRow, after: int) -> int | None:
        """Compute the next fire instant strictly after ``after`` (epoch ms).

        ``None`` means "this trigger isn't tick-driven" — manual + future
        webhook live in that bucket. The runner persists ``None`` to
        ``row.next_run_at`` and ``_check_due_tasks`` simply never picks the
        row up.
        """
        if row.trigger_kind == "cron":
            if not row.cron_expr:
                # CheckConstraint should make this unreachable; tolerate it as
                # a no-op rather than crash the tick loop.
                return None
            return self._cron.next_run(row.cron_expr, self._effective_tz_for(row), after)

        if row.trigger_kind == "interval":
            seconds = row.interval_seconds or MIN_INTERVAL_SECONDS
            return after + seconds * 1000

        # manual / unknown — never tick-driven
        return None

    def is_due(self, row: AutomationRow, now: int) -> bool:
        """True when this row should fire on the current tick.

        Cron / interval use the stored ``next_run_at`` (already computed at
        last fire / at create time). Manual is never due via the tick — only
        ``run_now`` and the future webhook endpoint enqueue manual rows.

        Instants are epoch ms ints — an int comparison, no tz/naive guard.
        """
        if row.status != "enabled":
            return False
        if row.trigger_kind == "manual":
            return False
        if row.next_run_at is None:
            return False
        return row.next_run_at <= now

    # ── Helpers exposed for service-layer validation ──────────────────

    def initial_next_fire(self, row: AutomationRow, *, now: int) -> int | None:
        """Compute the first ``next_run_at`` at create / resume time.

        Cron rows align to the next cron tick after ``now``; interval rows
        align to ``now + interval_seconds`` so the first fire respects the
        cadence (rather than firing immediately on create).
        """
        return self.next_fire_at(row, now)

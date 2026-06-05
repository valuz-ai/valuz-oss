"""Cron expression parsing + human-readable description.

Thin wrapper over ``croniter`` (next-fire calculation) + ``cron-descriptor``
(natural-language). Lifted verbatim from the legacy ``modules.schedules.cron_utils``
during the [ADR-021](../../../../docs/decisions/ADR-021-automation-trigger-agent.md)
refactor — the cron evaluator itself didn't change, only the trigger model that
wraps it. Once the schedules module is deleted in S6, this is the only copy.

Instants cross this boundary as Unix epoch milliseconds (UTC) ``int`` — the
host-wide representation. The cron *rule* still lives in the ``timezone``
STRING + croniter (interpret the expression in that tz); only the resulting
instant is converted to/from ms at the edge. ``datetime`` is used purely as
croniter's internal currency.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytz
from cron_descriptor import Options, get_description
from croniter import croniter

# (valid, human_readable, next_runs_ms, error) — next runs are epoch ms ints.
ValidateResult = tuple[bool, str | None, list[int], str | None]


DEFAULT_LOCALE = "zh_CN"


def _to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class CronInterpreter:
    """Wraps croniter + cron-descriptor with explicit tz + locale."""

    def validate(self, expr: str, tz_name: str, locale: str = DEFAULT_LOCALE) -> ValidateResult:
        if not croniter.is_valid(expr):
            return False, None, [], "Invalid cron expression"
        human_readable = self.describe(expr, locale=locale)
        next_runs = self._next_n_runs(expr, tz_name, 5)
        return True, human_readable, next_runs, None

    def next_run(self, expr: str, tz_name: str | None, after: int) -> int:
        """Next fire strictly after ``after`` (epoch ms), as epoch ms (UTC).

        ``ScheduleTaskRow.timezone`` was nullable (None = "follow user default");
        the automation refactor keeps that contract. ``pytz.timezone(None)``
        raises deep inside the dep, so we coerce to UTC as a safety net —
        callers SHOULD resolve to a concrete IANA name before reaching us.
        """
        tz = pytz.timezone(tz_name or "UTC")
        after_dt = datetime.fromtimestamp(after / 1000, tz=UTC)
        local_after = after_dt.astimezone(tz)
        cron = croniter(expr, local_after)
        next_dt: datetime = cron.get_next(datetime)
        return _to_ms(next_dt)

    def describe(self, expr: str, locale: str = DEFAULT_LOCALE) -> str:
        opts = Options()
        opts.locale_code = locale
        try:
            result: str = get_description(expr, opts)
        except Exception:
            opts.locale_code = DEFAULT_LOCALE
            result = get_description(expr, opts)
        return result

    def _next_n_runs(self, expr: str, tz_name: str, n: int) -> list[int]:
        tz = pytz.timezone(tz_name)
        now = datetime.now(tz)
        cron = croniter(expr, now)
        runs: list[int] = []
        for _ in range(n):
            dt: datetime = cron.get_next(datetime)
            runs.append(_to_ms(dt))
        return runs

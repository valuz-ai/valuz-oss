"""Adversarial timezone-behavior tests.

The two guarantees the user asked about:

1. A cron rule is interpreted in its *scheduling* timezone, but the next fire
   is an ABSOLUTE UTC instant (epoch ms). The scheduler compares UTC ms, so a
   trigger fires at the correct absolute moment regardless of the host clock —
   and ``next_run`` is INVARIANT to the host process timezone (``TZ`` env): the
   same (expr, tz, after) yields the same UTC instant no matter the OS zone, so
   changing the machine's timezone never drifts an existing schedule.

2. The service always stores the EFFECTIVE scheduling tz on the row: an explicit
   tz round-trips; an omitted tz falls back to the service default (which the
   MCP/HTTP paths wire to ``get_effective_default_timezone`` = configured pref →
   detected OS tz) — never a bare ``None``/accidental-UTC.
"""

from __future__ import annotations

import datetime
import os
import time
from datetime import UTC
from datetime import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock

from valuz_agent.modules.automations.cron_utils import CronInterpreter
from valuz_agent.modules.automations.schemas import CronTrigger

# A fixed reference instant so expectations are deterministic.
_AFTER_JUN = int(dt(2026, 6, 5, 0, 0, tzinfo=UTC).timestamp() * 1000)


def _utc(ms: int) -> dt:
    return dt.fromtimestamp(ms / 1000, tz=UTC)


class TestCronUtcSemantics:
    def test_same_wall_clock_different_tz_yields_different_utc(self) -> None:
        """ "18:30 daily" in three zones → three DIFFERENT absolute UTC instants,
        each the correct conversion."""
        ci = CronInterpreter()
        sh = _utc(ci.next_run("30 18 * * *", "Asia/Shanghai", _AFTER_JUN))
        la = _utc(ci.next_run("30 18 * * *", "America/Los_Angeles", _AFTER_JUN))
        u = _utc(ci.next_run("30 18 * * *", "UTC", _AFTER_JUN))

        assert (sh.hour, sh.minute) == (10, 30)  # 18:30 +08:00 → 10:30 UTC
        assert sh.date() == datetime.date(2026, 6, 5)
        assert (u.hour, u.minute) == (18, 30)  # 18:30 UTC
        assert (la.hour, la.minute) == (1, 30)  # 18:30 PDT(-07) → 01:30 UTC
        assert la.date() == datetime.date(2026, 6, 5)
        # Three distinct absolute instants — the tz genuinely changed the result.
        assert len({sh, la, u}) == 3

    def test_next_run_is_invariant_to_process_timezone(self) -> None:
        """ADVERSARIAL: mutate the OS process tz between calls; the same
        (expr, tz, after) must return the SAME UTC instant. This is the
        'no drift when the system timezone changes' guarantee."""
        ci = CronInterpreter()
        original = os.environ.get("TZ")
        try:
            results: set[int] = set()
            for proc_tz in (
                "UTC",
                "America/New_York",
                "Asia/Kolkata",
                "Pacific/Auckland",
                "Europe/London",
            ):
                os.environ["TZ"] = proc_tz
                time.tzset()
                results.add(ci.next_run("30 18 * * *", "Asia/Shanghai", _AFTER_JUN))
            assert len(results) == 1, f"process-tz drift detected: {results}"
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()

    def test_dst_shifts_utc_instant_for_same_local_rule(self) -> None:
        """Same '18:30 local' rule fires at a DIFFERENT UTC instant across DST
        (PST -08 vs PDT -07) — recompute is DST-aware, not a frozen offset."""
        ci = CronInterpreter()
        jan = _utc(
            ci.next_run(
                "30 18 * * *",
                "America/Los_Angeles",
                int(dt(2026, 1, 5, 0, 0, tzinfo=UTC).timestamp() * 1000),
            )
        )
        jul = _utc(
            ci.next_run(
                "30 18 * * *",
                "America/Los_Angeles",
                int(dt(2026, 7, 5, 0, 0, tzinfo=UTC).timestamp() * 1000),
            )
        )
        assert (jan.hour, jan.minute) == (2, 30)  # 18:30 PST(-08) → 02:30 UTC
        assert (jul.hour, jul.minute) == (1, 30)  # 18:30 PDT(-07) → 01:30 UTC


class TestEffectiveTzStorage:
    def _svc(self, default_tz: str):  # type: ignore[no-untyped-def]
        from valuz_agent.modules.automations.service import AutomationService

        return AutomationService(
            db=MagicMock(),
            event_bus=MagicMock(),
            default_timezone=default_tz,
        )

    def test_explicit_tz_round_trips(self) -> None:
        svc = self._svc("UTC")
        row = SimpleNamespace()
        svc._apply_trigger(  # noqa: SLF001 — exercising the projection directly
            row, CronTrigger(cron_expr="30 18 * * *", timezone="America/Los_Angeles")
        )
        assert row.timezone == "America/Los_Angeles"

    def test_omitted_tz_falls_back_to_service_default_never_null(self) -> None:
        svc = self._svc("Asia/Shanghai")
        row = SimpleNamespace()
        svc._apply_trigger(row, CronTrigger(cron_expr="30 18 * * *", timezone=None))  # noqa: SLF001
        assert row.timezone == "Asia/Shanghai"  # not None, not accidental UTC

    def test_blank_tz_also_falls_back(self) -> None:
        svc = self._svc("Asia/Tokyo")
        row = SimpleNamespace()
        svc._apply_trigger(row, CronTrigger(cron_expr="0 9 * * *", timezone="   "))  # noqa: SLF001
        assert row.timezone == "Asia/Tokyo"


class TestEffectiveDefaultFallback:
    async def test_falls_back_to_detected_os_tz_when_unset(self, monkeypatch) -> None:
        """MCP/HTTP no-explicit-tz path: configured pref absent → detected OS tz
        (never UTC-by-accident)."""
        from valuz_agent.modules.settings import preferences as p

        async def _no_pref(db, key):  # type: ignore[no-untyped-def]
            return None

        monkeypatch.setattr(p, "_read", _no_pref)
        monkeypatch.setattr(p, "detect_system_timezone", lambda: "America/Los_Angeles")
        assert await p.get_effective_default_timezone(MagicMock()) == "America/Los_Angeles"

    async def test_prefers_configured_pref_over_detection(self, monkeypatch) -> None:
        from valuz_agent.modules.settings import preferences as p

        async def _pref(db, key):  # type: ignore[no-untyped-def]
            return "Asia/Tokyo"

        # Detection must NOT win when an explicit pref exists.
        monkeypatch.setattr(p, "_read", _pref)
        monkeypatch.setattr(p, "detect_system_timezone", lambda: "America/Los_Angeles")
        assert await p.get_effective_default_timezone(MagicMock()) == "Asia/Tokyo"

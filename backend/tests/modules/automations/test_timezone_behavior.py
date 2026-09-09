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

import pytest

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

        async def _no_pref(db, key, user_id=None):  # type: ignore[no-untyped-def]
            return None

        monkeypatch.setattr(p, "_read", _no_pref)
        monkeypatch.setattr(p, "detect_system_timezone", lambda: "America/Los_Angeles")
        assert await p.get_effective_default_timezone(MagicMock()) == "America/Los_Angeles"

    async def test_prefers_configured_pref_over_detection(self, monkeypatch) -> None:
        from valuz_agent.modules.settings import preferences as p

        async def _pref(db, key, user_id=None):  # type: ignore[no-untyped-def]
            return "Asia/Tokyo"

        # Detection must NOT win when an explicit pref exists.
        monkeypatch.setattr(p, "_read", _pref)
        monkeypatch.setattr(p, "detect_system_timezone", lambda: "America/Los_Angeles")
        assert await p.get_effective_default_timezone(MagicMock()) == "Asia/Tokyo"


class TestInvalidTimeZone:
    """A non-IANA timezone (Windows display name, typo) is a typed 422
    validation error, never an uncaught pytz crash → 500."""

    def _svc(self, default_tz: str = "UTC"):  # type: ignore[no-untyped-def]
        from valuz_agent.modules.automations.service import AutomationService

        return AutomationService(
            db=MagicMock(),
            event_bus=MagicMock(),
            default_timezone=default_tz,
        )

    def test_windows_display_name_tz_raises_invalid_timezone(self) -> None:
        from valuz_agent.modules.automations.errors import InvalidTimeZone

        svc = self._svc()
        with pytest.raises(InvalidTimeZone):
            svc._validate_trigger(  # noqa: SLF001
                CronTrigger(cron_expr="0 9 * * *", timezone="马来西亚半岛标准时间")
            )

    def test_typo_tz_raises_invalid_timezone(self) -> None:
        from valuz_agent.modules.automations.errors import InvalidTimeZone

        svc = self._svc()
        with pytest.raises(InvalidTimeZone):
            svc._validate_trigger(CronTrigger(cron_expr="0 9 * * *", timezone="Asia/Shangh"))  # noqa: SLF001

    def test_default_tz_that_is_not_iana_also_raises(self) -> None:
        """The same guard protects the service default — a Windows display
        name persisted as the user default must not 500 either."""
        from valuz_agent.modules.automations.errors import InvalidTimeZone

        svc = self._svc(default_tz="马来西亚半岛标准时间")
        with pytest.raises(InvalidTimeZone):
            svc._validate_trigger(CronTrigger(cron_expr="0 9 * * *", timezone=None))  # noqa: SLF001

    def test_validate_cron_route_guard(self) -> None:
        from valuz_agent.modules.automations.errors import InvalidTimeZone

        svc = self._svc()
        with pytest.raises(InvalidTimeZone):
            svc.validate_cron("0 9 * * *", "马来西亚半岛标准时间")

    def test_valid_iana_still_passes(self) -> None:
        svc = self._svc()
        result = svc.validate_cron("0 9 * * *", "Asia/Shanghai")
        assert result.valid is True
        assert result.next_runs


class TestWindowsTimezoneDetection:
    """``detect_system_timezone`` must never return a non-IANA Windows
    display name — the name that later crashed cron validation with a 500.
    Detection is the browser's job (``Intl`` is always IANA); the backend
    fallback either trusts a validated ``TZ`` env var or returns UTC."""

    def _fake_now(self, tzname_value: str | None):  # type: ignore[no-untyped-def]
        """A ``datetime.now()`` whose ``astimezone().tzname()`` returns the
        given display name (or raises) — stands in for a Windows host."""
        import datetime as _dt

        class _FakeZoneInfo:
            def tzname(self) -> str:
                if tzname_value is None:
                    raise RuntimeError("no tzinfo")
                return tzname_value

        class _FakeNow(_dt.datetime):
            def astimezone(self, tz=None):  # type: ignore[override]
                return _FakeZoneInfo()

        return lambda: _FakeNow(2026, 8, 28, 12, 0)

    def test_windows_valid_tz_env_is_trusted(self, monkeypatch) -> None:
        import sys as _sys

        from valuz_agent.modules.settings import preferences as p

        monkeypatch.setattr(_sys, "platform", "win32")
        monkeypatch.setenv("TZ", "Asia/Shanghai")
        assert p.detect_system_timezone() == "Asia/Shanghai"

    def test_windows_invalid_tz_env_falls_back_to_utc(self, monkeypatch) -> None:
        import sys as _sys

        from valuz_agent.modules.settings import preferences as p

        monkeypatch.setattr(_sys, "platform", "win32")
        monkeypatch.setenv("TZ", "中国标准时间")
        assert p.detect_system_timezone() == "UTC"

    def test_windows_no_tz_env_is_utc_never_a_display_name(self, monkeypatch) -> None:
        """No guessing server-side: a localized display name (what
        ``tzname()`` returns on zh-CN Windows) must NEVER be returned —
        the browser reports the IANA name on the real detection path."""
        import sys as _sys

        from valuz_agent.modules.settings import preferences as p

        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(_sys, "platform", "win32")
        monkeypatch.setattr(p, "datetime", MagicMock(now=self._fake_now("马来西亚半岛标准时间")))
        assert p.detect_system_timezone() == "UTC"


class TestTimezonePrefNormalisation:
    async def test_invalid_stored_pref_is_treated_as_unset(self, monkeypatch) -> None:
        from valuz_agent.modules.settings import preferences as p

        async def _pref(db, key, user_id=None):  # type: ignore[no-untyped-def]
            return "马来西亚半岛标准时间"

        monkeypatch.setattr(p, "_read", _pref)
        # Bad legacy value → unset → detected/UTC fallback, never the bad name.
        assert await p.get_default_timezone(MagicMock()) == "UTC"
        monkeypatch.setattr(p, "detect_system_timezone", lambda: "Asia/Shanghai")
        assert await p.get_effective_default_timezone(MagicMock()) == "Asia/Shanghai"

    async def test_valid_iana_pref_round_trips(self, monkeypatch) -> None:
        from valuz_agent.modules.settings import preferences as p

        async def _pref(db, key, user_id=None):  # type: ignore[no-untyped-def]
            return "Asia/Kuala_Lumpur"

        monkeypatch.setattr(p, "_read", _pref)
        assert await p.get_default_timezone(MagicMock()) == "Asia/Kuala_Lumpur"


class TestHostWithoutTzDatabase:
    """A host where stdlib ``zoneinfo`` has NO data to read.

    That is the packaged Windows client without the ``tzdata`` wheel: Windows
    ships no system tz database, so ``ZoneInfo(key)`` raises for EVERY key —
    ``ZoneInfo("UTC")`` included. ``tzdata`` is now a declared dependency, so
    this state should not occur; these tests exist because the degradation path
    itself was broken. Rendering must fall back to UTC and keep going, never
    raise out of ``_build_template_variables`` — the escape landed in a region
    with no ``except``, left the run stuck in ``queued`` and made every later
    ``run_now`` fail as already-queued.
    """

    @staticmethod
    def _no_tz_database(monkeypatch, module) -> None:  # type: ignore[no-untyped-def]
        """Make every ``ZoneInfo(...)`` in ``module`` raise, "UTC" included."""
        from zoneinfo import ZoneInfoNotFoundError

        def _always_missing(key: str):  # type: ignore[no-untyped-def]
            raise ZoneInfoNotFoundError(f"No time zone found with key {key}")

        monkeypatch.setattr(module, "ZoneInfo", _always_missing)

    @staticmethod
    def _row():  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            id="auto-1",
            name="Daily digest",
            project_id="proj-1",
            agent_slug="analyst",
            last_run_at=int(dt(2026, 6, 4, 1, 0, tzinfo=UTC).timestamp() * 1000),
        )

    def test_render_falls_back_to_utc_instead_of_raising(self, monkeypatch) -> None:
        from valuz_agent.modules.automations import in_process_runner as ipr

        self._no_tz_database(monkeypatch, ipr)
        variables = ipr._build_template_variables(  # noqa: SLF001
            row=self._row(), project_name="Research", effective_tz="Asia/Shanghai"
        )

        assert variables["tz"] == "UTC"
        assert variables["now"] == variables["now_utc"]
        assert variables["last_run_at"] == "2026-06-04T01:00:00+00:00"

    def test_artifact_stamp_falls_back_to_utc(self, monkeypatch) -> None:
        """Same broken-fallback shape in the artifacts context section."""
        from valuz_agent.modules.artifacts import context as ctx

        self._no_tz_database(monkeypatch, ctx)
        epoch_ms = int(dt(2026, 6, 4, 1, 30, tzinfo=UTC).timestamp() * 1000)
        assert ctx._stamp(epoch_ms, "Asia/Shanghai") == "2026-06-04 01:30"  # noqa: SLF001

    def test_artifact_stamp_without_a_tz_name_needs_no_fallback(self, monkeypatch) -> None:
        """``tz_name=None`` must not route through ``ZoneInfo("UTC")`` at all —
        that call is exactly the one this whole class says cannot be relied on."""
        from valuz_agent.modules.artifacts import context as ctx

        self._no_tz_database(monkeypatch, ctx)
        epoch_ms = int(dt(2026, 6, 4, 1, 30, tzinfo=UTC).timestamp() * 1000)
        assert ctx._stamp(epoch_ms, None) == "2026-06-04 01:30"  # noqa: SLF001


class TestTzDatabaseIsBundled:
    """``tzdata`` must stay a DECLARED dependency.

    stdlib ``zoneinfo`` reads the SYSTEM tz database first, so on the mac/Linux
    dev box every ``ZoneInfo(...)`` passes whether or not the wheel is present —
    the dependency's absence is invisible until a Windows build runs. Two checks,
    because either alone has a hole: the declaration can be dropped while a
    transitive dep keeps the venv working, and the declaration can be present
    while the wheel is missing from the environment under test.
    """

    def test_tzdata_is_declared_in_pyproject(self) -> None:
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
        deps = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]
        assert any(d.split(">=")[0].split("==")[0].strip() == "tzdata" for d in deps), (
            "tzdata must stay in [project] dependencies — stdlib zoneinfo has no "
            "data source on Windows without it, and nothing on mac/Linux notices."
        )

    def test_zoneinfo_resolves_without_a_system_tz_database(self) -> None:
        import zoneinfo

        # ``reset_tzpath`` is the only supported way to change what ZoneInfo
        # reads, and it is process-global — hence the finally, which must not
        # be narrowed to the happy path.
        original = list(zoneinfo.TZPATH)
        try:
            zoneinfo.reset_tzpath(to=[])
            # ``no_cache`` bypasses the module cache so the lookup really has to
            # re-read a source; with TZPATH empty that source can only be tzdata.
            for key in ("Asia/Shanghai", "UTC"):
                assert zoneinfo.ZoneInfo.no_cache(key) is not None
        finally:
            zoneinfo.reset_tzpath(to=original)

"""TriggerEvaluator unit tests.

Cover the three polymorphic branches (cron / interval / manual) plus the
status + ``next_run_at`` gates that the runner relies on. Keeps the
evaluator pure — no DB, no async — so the runner tests in S3 can focus on
plumbing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from valuz_agent.modules.automations.models import AutomationRow
from valuz_agent.modules.automations.triggers import (
    MIN_INTERVAL_SECONDS,
    TriggerEvaluator,
)


def _ms(dt: datetime) -> int:
    """Datetime → Unix epoch ms (UTC). Instants cross the evaluator as ints."""
    return int(dt.timestamp() * 1000)


def _row(**overrides: object) -> AutomationRow:
    base: dict[str, object] = {
        "id": "auto1",
        "name": "test",
        "agent_kind": "project_member",
        "agent_slug": "qa",
        "project_id": "ws1",
        "prompt_template": "p",
        "trigger_kind": "cron",
        "cron_expr": "0 9 * * *",
        "timezone": None,
        "interval_seconds": None,
        "status": "enabled",
        "next_run_at": None,
        "last_run_at": None,
    }
    base.update(overrides)
    return AutomationRow(user_id="local-test-owner", **base)


class TestCronTrigger:
    def test_next_fire_at_picks_next_cron_tick(self) -> None:
        ev = TriggerEvaluator(default_timezone="UTC")
        row = _row(trigger_kind="cron", cron_expr="0 9 * * *")
        # 2026-05-28 08:00 UTC → next 9:00 same day
        after = _ms(datetime(2026, 5, 28, 8, 0, tzinfo=UTC))
        result = ev.next_fire_at(row, after)
        assert result == _ms(datetime(2026, 5, 28, 9, 0, tzinfo=UTC))

    def test_next_fire_at_skips_to_tomorrow_when_past(self) -> None:
        ev = TriggerEvaluator(default_timezone="UTC")
        row = _row(trigger_kind="cron", cron_expr="0 9 * * *")
        # 2026-05-28 09:01 UTC — already past today's 9:00 → tomorrow
        after = _ms(datetime(2026, 5, 28, 9, 1, tzinfo=UTC))
        result = ev.next_fire_at(row, after)
        assert result == _ms(datetime(2026, 5, 29, 9, 0, tzinfo=UTC))

    def test_per_row_timezone_overrides_default(self) -> None:
        ev = TriggerEvaluator(default_timezone="UTC")
        row = _row(trigger_kind="cron", cron_expr="0 9 * * *", timezone="Asia/Shanghai")
        # 2026-05-28 00:00 UTC = 2026-05-28 08:00 SHA → next 09:00 SHA = 01:00 UTC
        after = _ms(datetime(2026, 5, 28, 0, 0, tzinfo=UTC))
        result = ev.next_fire_at(row, after)
        assert result == _ms(datetime(2026, 5, 28, 1, 0, tzinfo=UTC))

    def test_missing_cron_expr_returns_none_not_crash(self) -> None:
        # CheckConstraint blocks this at write time, but the evaluator must
        # not crash if it ever slips through.
        ev = TriggerEvaluator()
        row = _row(trigger_kind="cron", cron_expr=None)
        assert ev.next_fire_at(row, _ms(datetime(2026, 5, 28, tzinfo=UTC))) is None


class TestIntervalTrigger:
    def test_next_fire_at_adds_seconds(self) -> None:
        ev = TriggerEvaluator()
        row = _row(trigger_kind="interval", cron_expr=None, interval_seconds=300)
        after = _ms(datetime(2026, 5, 28, 10, 0, tzinfo=UTC))
        result = ev.next_fire_at(row, after)
        assert result == after + 300 * 1000

    def test_floor_used_when_seconds_missing(self) -> None:
        # CheckConstraint blocks this, but defensive default = MIN_INTERVAL_SECONDS.
        ev = TriggerEvaluator()
        row = _row(trigger_kind="interval", cron_expr=None, interval_seconds=None)
        after = _ms(datetime(2026, 5, 28, tzinfo=UTC))
        result = ev.next_fire_at(row, after)
        assert result == after + MIN_INTERVAL_SECONDS * 1000

    def test_min_interval_is_30(self) -> None:
        # Documents the runner-floor contract — the tick is 30s, so anything
        # below this would alias against it.
        assert MIN_INTERVAL_SECONDS == 30


class TestManualAndIsDue:
    def test_manual_never_tick_driven(self) -> None:
        ev = TriggerEvaluator()
        row = _row(trigger_kind="manual", cron_expr=None)
        assert ev.next_fire_at(row, _ms(datetime(2026, 5, 28, tzinfo=UTC))) is None
        assert ev.is_due(row, _ms(datetime(2026, 5, 28, tzinfo=UTC))) is False

    def test_paused_row_never_due(self) -> None:
        ev = TriggerEvaluator()
        row = _row(
            trigger_kind="cron",
            cron_expr="0 9 * * *",
            status="paused",
            next_run_at=_ms(datetime(2025, 1, 1, tzinfo=UTC)),  # long overdue
        )
        assert ev.is_due(row, _ms(datetime(2026, 5, 28, tzinfo=UTC))) is False

    def test_due_when_next_run_at_passed(self) -> None:
        ev = TriggerEvaluator()
        row = _row(
            trigger_kind="cron",
            cron_expr="0 9 * * *",
            next_run_at=_ms(datetime(2026, 5, 28, 9, 0, tzinfo=UTC)),
        )
        assert ev.is_due(row, _ms(datetime(2026, 5, 28, 9, 0, tzinfo=UTC))) is True
        assert ev.is_due(row, _ms(datetime(2026, 5, 28, 9, 1, tzinfo=UTC))) is True
        assert ev.is_due(row, _ms(datetime(2026, 5, 28, 8, 59, tzinfo=UTC))) is False

    def test_is_due_compares_epoch_ms_ints(self) -> None:
        # Instants are epoch ms ints — a plain int comparison, no tz/naive guard.
        ev = TriggerEvaluator()
        row = _row(
            trigger_kind="interval",
            cron_expr=None,
            interval_seconds=60,
            next_run_at=_ms(datetime(2026, 5, 28, 9, 0, tzinfo=UTC)),
        )
        assert ev.is_due(row, _ms(datetime(2026, 5, 28, 9, 0, tzinfo=UTC))) is True

    def test_no_next_run_at_never_due(self) -> None:
        ev = TriggerEvaluator()
        row = _row(trigger_kind="cron", cron_expr="0 9 * * *", next_run_at=None)
        assert ev.is_due(row, _ms(datetime(2026, 5, 28, tzinfo=UTC))) is False


class TestSchemaTriggerDiscriminator:
    """The Pydantic discriminated union is the API edge guard."""

    def test_interval_below_floor_rejected(self) -> None:
        from pydantic import ValidationError

        from valuz_agent.modules.automations.schemas import IntervalTrigger

        with pytest.raises(ValidationError):
            IntervalTrigger(seconds=29)

    def test_interval_at_floor_accepted(self) -> None:
        from valuz_agent.modules.automations.schemas import IntervalTrigger

        trig = IntervalTrigger(seconds=30)
        assert trig.seconds == 30

    def test_trigger_union_resolves_by_kind(self) -> None:
        from pydantic import TypeAdapter

        from valuz_agent.modules.automations.schemas import Trigger

        adapter: TypeAdapter[Trigger] = TypeAdapter(Trigger)
        cron = adapter.validate_python({"kind": "cron", "cron_expr": "0 9 * * *"})
        assert cron.kind == "cron"
        interval = adapter.validate_python({"kind": "interval", "seconds": 60})
        assert interval.kind == "interval"
        manual = adapter.validate_python({"kind": "manual"})
        assert manual.kind == "manual"

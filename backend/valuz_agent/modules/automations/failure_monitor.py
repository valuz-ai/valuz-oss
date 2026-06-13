"""Failure-rate auto-pause monitor for automations (ADR-012).

Ports the legacy ``schedule.failure_monitor`` to operate on the new
``valuz_automation`` / ``valuz_automation_run`` tables.

Behavior is unchanged — ADR-012's "high min-runs + high fail-ratio over a
lookback window" rule still admits the same offenders; we just point the
datastore + event names at the new module. The env-var knobs are renamed
``VALUZ_SCHEDULE_FAIL_MONITOR_*`` → ``VALUZ_AUTOMATION_FAIL_MONITOR_*`` so
the configuration surface tracks the module name. Same defaults
(24h interval / 7d lookback / 50 min-runs / 0.9 fail-ratio / 60s startup
delay) keep operational behaviour consistent across the rename.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from valuz_agent.i18n import t
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.automations.models import (
    AutomationRow,
    AutomationRunRow,
)

PublishCallable = Callable[..., Any]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FailureMonitorConfig:
    interval: timedelta = timedelta(hours=24)
    lookback: timedelta = timedelta(days=7)
    min_runs: int = 50
    fail_ratio: float = 0.9
    startup_delay: timedelta = timedelta(seconds=60)

    @property
    def enabled(self) -> bool:
        return self.interval.total_seconds() > 0


def _parse_duration_env(name: str, default: timedelta) -> timedelta:
    """``"30"`` → 30s; ``"24h"`` / ``"15m"`` / ``"7d"`` / ``"60s"`` →
    that duration. Bad input warns and returns the default."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    suffixes: dict[str, int] = {"d": 86400, "h": 3600, "m": 60, "s": 1}
    if raw[-1] in suffixes:
        try:
            value = float(raw[:-1])
            return timedelta(seconds=value * suffixes[raw[-1]])
        except ValueError:
            logger.warning("invalid duration env %s=%r, using default %s", name, raw, default)
            return default
    try:
        return timedelta(seconds=float(raw))
    except ValueError:
        logger.warning("invalid duration env %s=%r, using default %s", name, raw, default)
        return default


def load_config_from_env() -> FailureMonitorConfig:
    defaults = FailureMonitorConfig()
    interval = _parse_duration_env("VALUZ_AUTOMATION_FAIL_MONITOR_INTERVAL", defaults.interval)
    lookback = _parse_duration_env("VALUZ_AUTOMATION_FAIL_MONITOR_LOOKBACK", defaults.lookback)
    startup_delay = _parse_duration_env(
        "VALUZ_AUTOMATION_FAIL_MONITOR_STARTUP_DELAY", defaults.startup_delay
    )
    min_runs = defaults.min_runs
    raw_min = os.environ.get("VALUZ_AUTOMATION_FAIL_MONITOR_MIN_RUNS", "").strip()
    if raw_min:
        try:
            v = int(raw_min)
            if v > 0:
                min_runs = v
            else:
                logger.warning("MIN_RUNS must be > 0; got %r — using default", raw_min)
        except ValueError:
            logger.warning("invalid MIN_RUNS %r — using default", raw_min)
    fail_ratio = defaults.fail_ratio
    raw_ratio = os.environ.get("VALUZ_AUTOMATION_FAIL_MONITOR_FAIL_RATIO", "").strip()
    if raw_ratio:
        try:
            v = float(raw_ratio)
            if 0 < v <= 1:
                fail_ratio = v
            else:
                logger.warning("FAIL_RATIO must be in (0, 1]; got %r — using default", raw_ratio)
        except ValueError:
            logger.warning("invalid FAIL_RATIO %r — using default", raw_ratio)
    return FailureMonitorConfig(
        interval=interval,
        lookback=lookback,
        min_runs=min_runs,
        fail_ratio=fail_ratio,
        startup_delay=startup_delay,
    )


@dataclass(frozen=True)
class _Candidate:
    row: AutomationRow
    total: int
    failed: int

    @property
    def fail_pct(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.failed / self.total * 1000) / 10


async def evaluate_candidates(
    rows: list[AutomationRow],
    ds: AutomationDatastore,
    *,
    now: int,
    config: FailureMonitorConfig,
) -> list[_Candidate]:
    """Compute the list of automations that should be auto-paused.

    Pure function over its inputs (modulo the datastore call). Separating
    this from the side-effecting ``perform_sweep`` makes the threshold
    logic trivially testable. ``now`` is epoch ms; ``config.lookback`` stays
    a ``timedelta`` duration, converted to ms at the subtraction.
    """
    since = now - int(config.lookback.total_seconds() * 1000)
    out: list[_Candidate] = []
    for row in rows:
        if row.status != "enabled":
            continue
        total, failed = await ds.count_terminal_runs_since(row.user_id, row.id, since)
        if total < config.min_runs:
            continue
        if total == 0:
            continue
        ratio = failed / total
        if ratio >= config.fail_ratio:
            out.append(_Candidate(row=row, total=total, failed=failed))
    return out


async def perform_sweep(
    ds: AutomationDatastore,
    *,
    now: int,
    config: FailureMonitorConfig,
    publish_event: PublishCallable | None = None,
) -> list[_Candidate]:
    """Run one monitor sweep — find offenders, pause them, leave an
    audit ``AutomationRunRow`` per pause."""
    enabled = await ds.list_enabled()
    candidates = await evaluate_candidates(enabled, ds, now=now, config=config)
    if not candidates:
        return []

    paused: list[_Candidate] = []
    for c in candidates:
        # Re-read to defend against another sweep / manual pause racing us.
        # AutomationDatastore lacks a conditional UPDATE today; the single-
        # writer guarantee from ADR-011 keeps the race window tiny.
        fresh = await ds.get_automation(c.row.user_id, c.row.id)
        if fresh is None or fresh.status != "enabled":
            continue

        fresh.status = "paused"
        fresh.next_run_at = None
        fresh.updated_at = now
        await ds.update_automation(fresh)

        summary = t(
            "backend.automation.autoPaused",
            params={
                "failed": c.failed,
                "total": c.total,
                "pct": c.fail_pct,
            },
        )
        await ds.create_run(
            c.row.user_id,
            AutomationRunRow(
                id=uuid4().hex,
                automation_id=fresh.id,
                project_id=fresh.project_id,
                trigger_type="system",
                status="auto_paused_notice",
                triggered_at=now,
                completed_at=now,
                duration_ms=0,
                result_summary=summary,
                error_code="HIGH_FAILURE_RATE",
                created_files="[]",
            ),
        )
        logger.warning(
            "auto-paused automation %s (%s): %d/%d failed (%s%%) over %s",
            fresh.id,
            fresh.name,
            c.failed,
            c.total,
            c.fail_pct,
            config.lookback,
        )
        if publish_event is not None:
            try:
                publish_event(
                    "automation.auto_paused",
                    project_id=fresh.project_id,
                    automation_id=fresh.id,
                    failed=c.failed,
                    total=c.total,
                    fail_pct=c.fail_pct,
                )
            except Exception:
                logger.exception("failed to publish automation.auto_paused event")
        paused.append(c)
    return paused


class AutomationFailureMonitor:
    """Background task that sweeps for runaway-failing automations.

    Lifecycle mirrors ``AutomationRunner``: ``startup()`` from a FastAPI
    startup hook spawns the tick task; ``shutdown()`` cancels it. Runs in
    the same event loop as the runner — they share no state and SQLite WAL
    + the host single-writer lock (ADR-011) keep concurrent writes safe.
    """

    def __init__(self, config: FailureMonitorConfig | None = None) -> None:
        self._config = config or load_config_from_env()
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def config(self) -> FailureMonitorConfig:
        return self._config

    async def startup(self) -> None:
        if not self._config.enabled:
            logger.info(
                "automation failure monitor: disabled (interval=%s)",
                self._config.interval,
            )
            return
        self._running = True
        self._task = asyncio.create_task(self._tick_loop())
        logger.info(
            "automation failure monitor: started "
            "(interval=%s, lookback=%s, min_runs=%d, fail_ratio=%.2f, startup_delay=%s)",
            self._config.interval,
            self._config.lookback,
            self._config.min_runs,
            self._config.fail_ratio,
            self._config.startup_delay,
        )

    async def shutdown(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        logger.info("automation failure monitor: stopped")

    async def _tick_loop(self) -> None:
        if self._config.startup_delay.total_seconds() > 0:
            try:
                await asyncio.sleep(self._config.startup_delay.total_seconds())
            except asyncio.CancelledError:
                return

        await self._safe_sweep()

        interval_s = self._config.interval.total_seconds()
        while self._running:
            try:
                await asyncio.sleep(interval_s)
            except asyncio.CancelledError:
                break
            if not self._running:
                break
            await self._safe_sweep()

    async def _safe_sweep(self) -> None:
        try:
            await self._sweep_once()
        except Exception:
            logger.exception("automation failure monitor: sweep failed")

    async def _sweep_once(self) -> None:
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.infra.eventbus import event_bus

        async with async_unit_of_work() as db:
            ds = AutomationDatastore(db)
            now = now_ms()
            await perform_sweep(
                ds,
                now=now,
                config=self._config,
                publish_event=event_bus.publish,
            )


# Module-level singleton, parallel to ``automation_runner`` in
# ``in_process_runner.py``. Imported lazily in ``api/app.py`` startup hooks.
automation_failure_monitor = AutomationFailureMonitor()

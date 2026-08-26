"""Stable automation use cases for deployment adapters.

The facade keeps ORM/datastore internals behind the host boundary. Queue
messages contain only immutable IDs; every use case re-reads persisted rows and
checks the stored owner before mutating execution state.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.ports.automation_runtime import (
    AutomationExecutionLease,
    AutomationRunCommand,
)


@dataclass(frozen=True, slots=True)
class RunClaimResult:
    claimed: bool
    reason: str | None = None


async def claim_due_runs(
    *,
    now: int | None = None,
    limit: int = 100,
    lateness_grace_ms: int = 60_000,
) -> list[AutomationRunCommand]:
    """Atomically create queued runs for a locked batch of due rows."""
    from valuz_agent.i18n import t
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.automations.datastore import AutomationDatastore
    from valuz_agent.modules.automations.models import AutomationRunRow
    from valuz_agent.modules.automations.triggers import TriggerEvaluator

    dispatch_now = now_ms() if now is None else now
    commands: list[AutomationRunCommand] = []
    evaluator = TriggerEvaluator(default_timezone="UTC")
    async with async_unit_of_work() as db:
        ds = AutomationDatastore(db)
        rows = await ds.find_due_automations_for_update(dispatch_now, limit=limit)
        for row in rows:
            if row.status != "enabled" or row.next_run_at is None:
                continue
            if await ds.active_run(row.user_id, row.id) is not None:
                continue
            if dispatch_now - row.next_run_at > lateness_grace_ms:
                db.add(
                    AutomationRunRow(
                        id=uuid4().hex,
                        user_id=row.user_id,
                        automation_id=row.id,
                        project_id=row.project_id,
                        trigger_type="recovered_skip",
                        status="skipped",
                        triggered_at=row.next_run_at,
                        completed_at=dispatch_now,
                        result_summary=t("backend.automation.appNotRunning"),
                        error_code="AUTOMATION_MISSED_WHILE_OFFLINE",
                        created_files="[]",
                    )
                )
                row.last_run_at = row.next_run_at
                row.next_run_at = evaluator.next_fire_at(row, dispatch_now)
                row.updated_at = dispatch_now
                continue

            trigger_type = row.trigger_kind if row.trigger_kind in {"cron", "interval"} else "cron"
            run = AutomationRunRow(
                id=uuid4().hex,
                user_id=row.user_id,
                automation_id=row.id,
                project_id=row.project_id,
                trigger_type=trigger_type,
                status="queued",
                triggered_at=dispatch_now,
                created_files="[]",
            )
            db.add(run)
            commands.append(
                AutomationRunCommand(
                    user_id=row.user_id,
                    automation_id=row.id,
                    run_id=run.id,
                )
            )
    return commands


async def mark_run_running(command: AutomationRunCommand) -> RunClaimResult:
    """CAS the exact persisted run from queued to running."""
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.automations.datastore import AutomationDatastore

    async with async_unit_of_work(commit=False) as db:
        ds = AutomationDatastore(db)
        row = await ds.get_automation(command.user_id, command.automation_id)
        run = await ds.get_run(command.user_id, command.automation_id, command.run_id)
        if row is None or run is None or row.user_id != run.user_id:
            return RunClaimResult(False, "owner_or_row_mismatch")
        if run.status != "queued":
            return RunClaimResult(False, f"run_{run.status}")
        changed = await ds.mark_run_running(
            command.user_id,
            command.automation_id,
            command.run_id,
            started_at=now_ms(),
        )
        return RunClaimResult(changed, None if changed else "cas_lost")


async def execute_claimed_run(
    command: AutomationRunCommand,
    lease: AutomationExecutionLease,
) -> None:
    """Execute one claimed run with canonical host behavior and fencing."""
    from valuz_agent.modules.automations.in_process_runner import InProcessAutomationRunner
    from valuz_agent.modules.automations.triggers import TriggerEvaluator

    runner = InProcessAutomationRunner()
    runner._triggers = TriggerEvaluator(default_timezone="UTC")  # noqa: SLF001
    await runner._execute_run(  # noqa: SLF001 -- facade is the sanctioned host boundary
        command.user_id,
        command.automation_id,
        command.run_id,
        lease=lease,
        detach_chat=False,
    )


async def requeue_stale_queued(
    *,
    now: int | None = None,
    older_than_ms: int = 60_000,
    limit: int = 100,
) -> list[AutomationRunCommand]:
    """Return durable queued commands old enough to republish."""
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow

    current = now_ms() if now is None else now
    async with async_unit_of_work(commit=False) as db:
        rows = (
            await db.execute(
                select(AutomationRunRow, AutomationRow.user_id)
                .join(AutomationRow, AutomationRow.id == AutomationRunRow.automation_id)
                .where(
                    AutomationRunRow.status == "queued",
                    AutomationRunRow.triggered_at <= current - older_than_ms,
                    AutomationRunRow.user_id == AutomationRow.user_id,
                )
                .order_by(AutomationRunRow.triggered_at)
                .limit(limit)
            )
        ).all()
        return [
            AutomationRunCommand(
                user_id=owner,
                automation_id=run.automation_id,
                run_id=run.id,
            )
            for run, owner in rows
        ]


async def interrupt_run(command: AutomationRunCommand, *, reason: str) -> bool:
    """Terminalize the exact queued/running run without retrying it."""
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.automations.datastore import AutomationDatastore

    async with async_unit_of_work() as db:
        ds = AutomationDatastore(db)
        run = await ds.get_run(command.user_id, command.automation_id, command.run_id)
        if run is None or run.status not in {"queued", "running"}:
            return False
        completed = now_ms()
        run.status = "interrupted_by_shutdown"
        run.error_code = reason[:64]
        run.completed_at = completed
        if run.started_at is not None:
            run.duration_ms = completed - run.started_at
        await db.merge(run)
        return True


async def run_failure_monitor_once(*, now: int | None = None) -> int:
    """Run one canonical failure-monitor sweep."""
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.automations.datastore import AutomationDatastore
    from valuz_agent.modules.automations.failure_monitor import (
        load_config_from_env,
        perform_sweep,
    )

    async with async_unit_of_work() as db:
        paused = await perform_sweep(
            AutomationDatastore(db),
            now=now_ms() if now is None else now,
            config=load_config_from_env(),
            publish_event=event_bus.publish,
        )
        return len(paused)


__all__ = [
    "RunClaimResult",
    "claim_due_runs",
    "execute_claimed_run",
    "interrupt_run",
    "mark_run_running",
    "requeue_stale_queued",
    "run_failure_monitor_once",
]

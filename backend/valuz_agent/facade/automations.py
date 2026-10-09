"""Stable automation use cases for deployment adapters.

The facade keeps ORM/datastore internals behind the host boundary. Queue
messages contain only immutable IDs; every use case re-reads persisted rows and
checks the stored owner before mutating execution state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, fields
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.automations.contracts import AgentExecution
from valuz_agent.modules.automations.errors import AutomationNotFound
from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow
from valuz_agent.modules.automations.schemas import (
    AutomationCreatePayload as AutomationCreateSpec,
)
from valuz_agent.modules.automations.schemas import (
    AutomationDetailResponse as AutomationDefinition,
)
from valuz_agent.modules.automations.schemas import (
    AutomationRunAcceptedResponse,
    CronTrigger,
    EventTrigger,
    IntervalTrigger,
)
from valuz_agent.modules.automations.schemas import (
    AutomationUpdatePayload as AutomationUpdateSpec,
)
from valuz_agent.modules.automations.service import AutomationService
from valuz_agent.ports.automation_runtime import (
    AutomationExecutionLease,
    AutomationRunCommand,
)


@asynccontextmanager
async def _command_service(user_id: str) -> AsyncIterator[AutomationService]:
    """Explicit owner counterpart of HTTP DI; never invokes ambient identity deps."""
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.agents.service import AgentService
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.modules.projects.datastore import ProjectDatastore
    from valuz_agent.modules.projects.service import ProjectService
    from valuz_agent.modules.settings.preferences import (
        get_default_locale,
        get_effective_default_timezone,
    )

    async with async_unit_of_work() as db:
        yield AutomationService(
            db=db,
            event_bus=event_bus,
            project_service=ProjectService(datastore=ProjectDatastore(db), event_bus=event_bus),
            agent_service=AgentService(
                db=db, connector_service=ConnectorService(datastore=ConnectorDatastore(db))
            ),
            locale=await get_default_locale(db, user_id=user_id),
            default_timezone=await get_effective_default_timezone(db, user_id=user_id),
        )


class AutomationCommands:
    """Canonical owner-explicit mutations; statuses and source subscribe stay service-owned."""

    async def create(
        self,
        user_id: str,
        spec: AutomationCreateSpec,
        *,
        origin_ref: str | None = None,
        initially_paused: bool = False,
    ) -> AutomationDefinition:
        _require_read_identity(user_id, spec.name)
        async with _command_service(user_id) as service:
            return await service.create(
                spec,
                user_id=user_id,
                origin_tool_call_id=origin_ref,
                initial_status="paused" if initially_paused else "enabled",
            )

    async def get(self, user_id: str, automation_id: str) -> AutomationDefinition:
        _require_read_identity(user_id, automation_id)
        async with _command_service(user_id) as service:
            return await service.get_automation_detail(automation_id, user_id=user_id)

    async def find_created(self, user_id: str, origin_ref: str) -> AutomationDefinition | None:
        """Recover a real created object by its original invocation, not prompt/name."""
        _require_read_identity(user_id, origin_ref)
        async with _command_service(user_id) as service:
            ids = await service.confirmed_origin_map([origin_ref], user_id=user_id)
            found = ids.get(origin_ref)
            return await service.get_automation_detail(found, user_id=user_id) if found else None

    async def update(
        self, user_id: str, automation_id: str, spec: AutomationUpdateSpec
    ) -> AutomationDefinition:
        _require_read_identity(user_id, automation_id)
        async with _command_service(user_id) as service:
            return await service.update(automation_id, spec, user_id=user_id)

    async def pause(self, user_id: str, automation_id: str) -> AutomationDefinition:
        _require_read_identity(user_id, automation_id)
        async with _command_service(user_id) as service:
            return await service.pause(automation_id, user_id=user_id)

    async def resume(self, user_id: str, automation_id: str) -> AutomationDefinition:
        _require_read_identity(user_id, automation_id)
        async with _command_service(user_id) as service:
            return await service.resume(automation_id, user_id=user_id)

    async def delete(self, user_id: str, automation_id: str) -> None:
        _require_read_identity(user_id, automation_id)
        async with _command_service(user_id) as service:
            await service.delete(automation_id, user_id=user_id)

    async def run_now(self, user_id: str, automation_id: str) -> AutomationRunAcceptedResponse:
        _require_read_identity(user_id, automation_id)
        async with _command_service(user_id) as service:
            return await service.run_now(automation_id, user_id=user_id)

    async def retry_failed_run(
        self,
        user_id: str,
        automation_id: str,
        source_run_id: str,
        *,
        run_id: str | None = None,
    ) -> AutomationRunAcceptedResponse:
        _require_read_identity(user_id, automation_id)
        _require_read_identity(user_id, source_run_id)
        async with _command_service(user_id) as service:
            return await service.retry_failed_run(
                automation_id, source_run_id, user_id=user_id, run_id=run_id
            )


@dataclass(frozen=True, slots=True)
class RunClaimResult:
    claimed: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AutomationRef:
    id: str
    project_id: str
    name: str
    status: str
    action_kind: str
    trigger_kind: str
    cron_expr: str | None
    timezone: str | None
    interval_seconds: int | None
    playbook_definition_id: str | None
    playbook_version: int | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class AutomationRunRef:
    id: str
    automation_id: str
    project_id: str
    status: str
    trigger_type: str
    triggered_at: int
    started_at: int | None
    completed_at: int | None
    result_summary: str | None
    error_code: str | None
    session_id: str | None
    playbook_run_id: str | None
    event_id: str | None = None
    invoked_by_ref: str | None = None


class AutomationLibrary:
    """Read original records, without scheduling, executing or exporting resources.

    Definition updated_at is a current-state token, not immutable history.
    Run identity survives definition deletion; run status remains current state.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, user_id: str, automation_id: str) -> AutomationRef | None:
        _require_read_identity(user_id, automation_id)
        table = AutomationRow.__table__
        with self._db.no_autoflush:
            row = (
                (
                    await self._db.execute(
                        select(*(table.c[field.name] for field in fields(AutomationRef)))
                        .where(table.c.user_id == user_id, table.c.id == automation_id)
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
        return AutomationRef(**row) if row is not None else None

    async def get_run(self, user_id: str, run_id: str) -> AutomationRunRef | None:
        _require_read_identity(user_id, run_id)
        table = AutomationRunRow.__table__
        with self._db.no_autoflush:
            row = (
                (
                    await self._db.execute(
                        select(*(table.c[field.name] for field in fields(AutomationRunRef)))
                        .where(table.c.user_id == user_id, table.c.id == run_id)
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )
        return AutomationRunRef(**row) if row is not None else None


def _require_read_identity(user_id: str, resource_id: str) -> None:
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id must be non-empty")
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ValueError("resource_id must be non-empty")


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
    "AutomationCommands",
    "AutomationNotFound",
    "AutomationCreateSpec",
    "AutomationUpdateSpec",
    "AutomationDefinition",
    "AutomationRunAcceptedResponse",
    "CronTrigger",
    "IntervalTrigger",
    "EventTrigger",
    "AgentExecution",
    "AutomationLibrary",
    "AutomationRef",
    "AutomationRunRef",
    "RunClaimResult",
    "claim_due_runs",
    "execute_claimed_run",
    "interrupt_run",
    "mark_run_running",
    "requeue_stale_queued",
    "run_failure_monitor_once",
]

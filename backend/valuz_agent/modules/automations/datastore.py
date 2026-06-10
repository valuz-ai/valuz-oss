"""Automation datastore.

Async SQLAlchemy 2.0 — same pattern as every other host datastore (see
``ADR-020``). Method shape mirrors the legacy ``ScheduleDatastore`` so the
service layer ports across without method-name churn. Names use
``automation`` / ``run`` instead of ``task`` / ``run`` to match the new
domain language.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.modules.automations.models import (
    AutomationRow,
    AutomationRunRow,
)


class AutomationDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── Automation rows ───────────────────────────────────────────────

    async def list_automations(self, project_id: str | None = None) -> list[AutomationRow]:
        stmt = select(AutomationRow)
        if project_id:
            stmt = stmt.filter_by(project_id=project_id)
        stmt = stmt.order_by(AutomationRow.created_at)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_automation(self, automation_id: str) -> AutomationRow | None:
        return await self._db.get(AutomationRow, automation_id)

    async def create_automation(self, row: AutomationRow) -> AutomationRow:
        self._db.add(row)
        await async_commit_with_retry(self._db, where="AutomationDatastore.create_automation")
        return row

    async def update_automation(self, row: AutomationRow) -> AutomationRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="AutomationDatastore.update_automation")
        return row

    async def delete_automation(self, automation_id: str) -> None:
        await self._db.execute(
            delete(AutomationRunRow).where(AutomationRunRow.automation_id == automation_id)
        )
        await self._db.execute(delete(AutomationRow).where(AutomationRow.id == automation_id))
        await async_commit_with_retry(self._db, where="AutomationDatastore.delete_automation")

    async def delete_all_for_project(self, project_id: str) -> None:
        automation_ids = list(
            (await self._db.execute(select(AutomationRow.id).filter_by(project_id=project_id)))
            .scalars()
            .all()
        )
        if automation_ids:
            await self._db.execute(
                delete(AutomationRunRow).where(AutomationRunRow.automation_id.in_(automation_ids))
            )
        await self._db.execute(
            delete(AutomationRow).where(AutomationRow.project_id == project_id)
        )
        await async_commit_with_retry(
            self._db, where="AutomationDatastore.delete_all_for_project"
        )

    async def count_by_project(self, project_id: str) -> int:
        return (
            await self._db.execute(
                select(func.count()).select_from(AutomationRow).filter_by(project_id=project_id)
            )
        ).scalar() or 0

    async def list_enabled(self) -> list[AutomationRow]:
        """Every automation whose status is ``enabled``.

        Used by the failure-rate auto-pause monitor (ADR-012) — paused
        automations are already out of the firing path.
        """
        return list(
            (await self._db.execute(select(AutomationRow).filter_by(status="enabled")))
            .scalars()
            .all()
        )

    async def find_due_automations(self, now: int) -> list[AutomationRow]:
        """Rows whose ``next_run_at <= now`` and ``status == enabled``.

        The tick loop calls this once per cycle. Manual rows have
        ``next_run_at=NULL`` so they never surface here — they only fire
        via ``run_now`` (and, in future, webhook).

        Cron + interval both write a concrete ``next_run_at`` at fire time,
        so this single query is correct for both. The polymorphism lives
        in ``TriggerEvaluator.next_fire_at`` — the runner doesn't need to
        branch on ``trigger_kind`` to find due rows.
        """
        return list(
            (
                await self._db.execute(
                    select(AutomationRow)
                    .filter(
                        AutomationRow.status == "enabled",
                        AutomationRow.next_run_at.isnot(None),
                        AutomationRow.next_run_at <= now,
                    )
                    .order_by(AutomationRow.next_run_at, AutomationRow.created_at)
                )
            )
            .scalars()
            .all()
        )

    # ── Run rows ──────────────────────────────────────────────────────

    async def list_runs(
        self, automation_id: str, limit: int = 20, cursor: str | None = None
    ) -> list[AutomationRunRow]:
        stmt = select(AutomationRunRow).filter_by(automation_id=automation_id)
        if cursor:
            cursor_row = await self._db.get(AutomationRunRow, cursor)
            if cursor_row:
                stmt = stmt.filter(AutomationRunRow.triggered_at < cursor_row.triggered_at)
        stmt = stmt.order_by(AutomationRunRow.triggered_at.desc()).limit(limit)
        return list((await self._db.execute(stmt)).scalars().all())

    async def create_run(self, row: AutomationRunRow) -> AutomationRunRow:
        self._db.add(row)
        await async_commit_with_retry(self._db, where="AutomationDatastore.create_run")
        return row

    async def replace_run(self, row: AutomationRunRow) -> AutomationRunRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="AutomationDatastore.replace_run")
        return row

    async def trim_runs(self, automation_id: str, keep: int = 100) -> None:
        subq = (
            select(AutomationRunRow.id)
            .where(AutomationRunRow.automation_id == automation_id)
            .order_by(AutomationRunRow.triggered_at.desc())
            .limit(keep)
            .subquery()
        )
        await self._db.execute(
            delete(AutomationRunRow).where(
                AutomationRunRow.automation_id == automation_id,
                AutomationRunRow.id.notin_(select(subq.c.id)),
            )
        )
        await async_commit_with_retry(self._db, where="AutomationDatastore.trim_runs")

    async def list_stranded_runs(self) -> list[AutomationRunRow]:
        """Runs stuck in ``queued`` or ``running`` — used at runner startup
        to reap zombies left behind by a hard crash."""
        return list(
            (
                await self._db.execute(
                    select(AutomationRunRow).filter(
                        AutomationRunRow.status.in_(("queued", "running"))
                    )
                )
            )
            .scalars()
            .all()
        )

    async def count_runs(self, automation_id: str) -> int:
        return (
            await self._db.execute(
                select(func.count())
                .select_from(AutomationRunRow)
                .filter_by(automation_id=automation_id)
            )
        ).scalar() or 0

    async def count_recent_failures(self, automation_id: str, limit: int = 20) -> int:
        recent_runs = (
            select(AutomationRunRow)
            .filter_by(automation_id=automation_id)
            .order_by(AutomationRunRow.triggered_at.desc())
            .limit(limit)
            .subquery()
        )
        return (
            await self._db.execute(
                select(func.count())
                .select_from(recent_runs)
                .filter(recent_runs.c.status == "failed")
            )
        ).scalar() or 0

    async def last_run(self, automation_id: str) -> AutomationRunRow | None:
        return (
            (
                await self._db.execute(
                    select(AutomationRunRow)
                    .filter_by(automation_id=automation_id)
                    .order_by(AutomationRunRow.triggered_at.desc())
                )
            )
            .scalars()
            .first()
        )

    async def count_terminal_runs_since(self, automation_id: str, since: int) -> tuple[int, int]:
        """``(total, failed)`` over a lookback window — ADR-012 input.

        ``skipped`` rows are excluded from both numerator and denominator
        so that a flood of ``recovered_skip`` rows during an offline
        window doesn't dilute a real failure rate (mirrors the legacy
        schedule counter).
        """
        rows = list(
            (
                await self._db.execute(
                    select(AutomationRunRow.status).filter(
                        AutomationRunRow.automation_id == automation_id,
                        AutomationRunRow.triggered_at >= since,
                        AutomationRunRow.status.in_(("success", "failed")),
                    )
                )
            )
            .scalars()
            .all()
        )
        total = len(rows)
        failed = sum(1 for status in rows if status == "failed")
        return total, failed

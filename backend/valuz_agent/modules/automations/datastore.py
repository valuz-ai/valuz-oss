"""Automation datastore.

Async SQLAlchemy 2.0 — same pattern as every other host datastore (see
``ADR-020``). Method shape mirrors the legacy ``ScheduleDatastore`` so the
service layer ports across without method-name churn.

Owner model: user-facing reads + per-automation operations take the caller's
``user_id`` first and filter on it; writes stamp the owner explicitly. The
**background-scheduler sweeps** that legitimately process every owner's rows —
``find_due_automations`` (tick), ``list_enabled`` (failure monitor),
``list_stranded_runs`` (startup reaper) — deliberately stay cross-owner; their
callers thread the owner from each returned row's ``user_id``.
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

    async def list_automations(
        self, user_id: str, project_id: str | None = None
    ) -> list[AutomationRow]:
        stmt = select(AutomationRow).where(AutomationRow.user_id == user_id)
        if project_id:
            stmt = stmt.where(AutomationRow.project_id == project_id)
        stmt = stmt.order_by(AutomationRow.created_at)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_automation(self, user_id: str, automation_id: str) -> AutomationRow | None:
        return (
            (
                await self._db.execute(
                    select(AutomationRow).where(
                        AutomationRow.id == automation_id, AutomationRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def create_automation(self, user_id: str, row: AutomationRow) -> AutomationRow:
        row.user_id = user_id
        self._db.add(row)
        await async_commit_with_retry(self._db, where="AutomationDatastore.create_automation")
        return row

    async def update_automation(self, row: AutomationRow) -> AutomationRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="AutomationDatastore.update_automation")
        return row

    async def delete_automation(self, user_id: str, automation_id: str) -> None:
        await self._db.execute(
            delete(AutomationRunRow).where(
                AutomationRunRow.automation_id == automation_id,
                AutomationRunRow.user_id == user_id,
            )
        )
        await self._db.execute(
            delete(AutomationRow).where(
                AutomationRow.id == automation_id, AutomationRow.user_id == user_id
            )
        )
        await async_commit_with_retry(self._db, where="AutomationDatastore.delete_automation")

    async def delete_all_for_project(self, user_id: str, project_id: str) -> None:
        automation_ids = list(
            (
                await self._db.execute(
                    select(AutomationRow.id).where(
                        AutomationRow.project_id == project_id, AutomationRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if automation_ids:
            await self._db.execute(
                delete(AutomationRunRow).where(
                    AutomationRunRow.automation_id.in_(automation_ids),
                    AutomationRunRow.user_id == user_id,
                )
            )
        await self._db.execute(
            delete(AutomationRow).where(
                AutomationRow.project_id == project_id, AutomationRow.user_id == user_id
            )
        )
        await async_commit_with_retry(self._db, where="AutomationDatastore.delete_all_for_project")

    async def count_by_project(self, user_id: str, project_id: str) -> int:
        return (
            await self._db.execute(
                select(func.count())
                .select_from(AutomationRow)
                .where(AutomationRow.project_id == project_id, AutomationRow.user_id == user_id)
            )
        ).scalar() or 0

    async def list_enabled(self) -> list[AutomationRow]:
        """SYSTEM SWEEP (cross-owner). Every enabled automation, for the
        failure-rate auto-pause monitor (ADR-012). The monitor threads each
        row's ``user_id`` into the owner-scoped per-automation queries."""
        return list(
            (await self._db.execute(select(AutomationRow).filter_by(status="enabled")))
            .scalars()
            .all()
        )

    async def find_due_automations(self, now: int) -> list[AutomationRow]:
        """SYSTEM SWEEP (cross-owner). Rows whose ``next_run_at <= now`` and
        ``status == enabled`` — the tick loop fires every owner's due rows. The
        runner threads each fired row's ``user_id`` downstream."""
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
        self, user_id: str, automation_id: str, limit: int = 20, cursor: str | None = None
    ) -> list[AutomationRunRow]:
        stmt = select(AutomationRunRow).where(
            AutomationRunRow.automation_id == automation_id,
            AutomationRunRow.user_id == user_id,
        )
        if cursor:
            cursor_row = (
                (
                    await self._db.execute(
                        select(AutomationRunRow).where(
                            AutomationRunRow.id == cursor, AutomationRunRow.user_id == user_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            if cursor_row:
                stmt = stmt.filter(AutomationRunRow.triggered_at < cursor_row.triggered_at)
        stmt = stmt.order_by(AutomationRunRow.triggered_at.desc()).limit(limit)
        return list((await self._db.execute(stmt)).scalars().all())

    async def create_run(self, user_id: str, row: AutomationRunRow) -> AutomationRunRow:
        row.user_id = user_id
        self._db.add(row)
        await async_commit_with_retry(self._db, where="AutomationDatastore.create_run")
        return row

    async def replace_run(self, row: AutomationRunRow) -> AutomationRunRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="AutomationDatastore.replace_run")
        return row

    async def trim_runs(self, user_id: str, automation_id: str, keep: int = 100) -> None:
        subq = (
            select(AutomationRunRow.id)
            .where(
                AutomationRunRow.automation_id == automation_id,
                AutomationRunRow.user_id == user_id,
            )
            .order_by(AutomationRunRow.triggered_at.desc())
            .limit(keep)
            .subquery()
        )
        await self._db.execute(
            delete(AutomationRunRow).where(
                AutomationRunRow.automation_id == automation_id,
                AutomationRunRow.user_id == user_id,
                AutomationRunRow.id.notin_(select(subq.c.id)),
            )
        )
        await async_commit_with_retry(self._db, where="AutomationDatastore.trim_runs")

    async def list_stranded_runs(self) -> list[AutomationRunRow]:
        """SYSTEM SWEEP (cross-owner). Runs stuck in ``queued`` / ``running`` —
        the startup reaper reaps zombies across every owner; it threads each
        run's ``user_id`` into the owner-scoped writes that follow."""
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

    async def count_runs(self, user_id: str, automation_id: str) -> int:
        return (
            await self._db.execute(
                select(func.count())
                .select_from(AutomationRunRow)
                .where(
                    AutomationRunRow.automation_id == automation_id,
                    AutomationRunRow.user_id == user_id,
                )
            )
        ).scalar() or 0

    async def count_recent_failures(self, user_id: str, automation_id: str, limit: int = 20) -> int:
        recent_runs = (
            select(AutomationRunRow)
            .where(
                AutomationRunRow.automation_id == automation_id,
                AutomationRunRow.user_id == user_id,
            )
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

    async def last_run(self, user_id: str, automation_id: str) -> AutomationRunRow | None:
        return (
            (
                await self._db.execute(
                    select(AutomationRunRow)
                    .where(
                        AutomationRunRow.automation_id == automation_id,
                        AutomationRunRow.user_id == user_id,
                    )
                    .order_by(AutomationRunRow.triggered_at.desc())
                )
            )
            .scalars()
            .first()
        )

    async def count_terminal_runs_since(
        self, user_id: str, automation_id: str, since: int
    ) -> tuple[int, int]:
        """``(total, failed)`` over a lookback window — ADR-012 input.

        ``skipped`` rows are excluded from both numerator and denominator so a
        flood of ``recovered_skip`` rows during an offline window doesn't dilute
        a real failure rate (mirrors the legacy schedule counter).
        """
        rows = list(
            (
                await self._db.execute(
                    select(AutomationRunRow.status).filter(
                        AutomationRunRow.automation_id == automation_id,
                        AutomationRunRow.user_id == user_id,
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

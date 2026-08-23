"""Owner-scoped Playbook persistence."""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow
from valuz_agent.modules.playbooks.models import (
    PlaybookDefinitionRow,
    PlaybookRunRow,
    PlaybookVersionRow,
)


class PlaybookDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_definition(
        self, user_id: str, definition_id: str
    ) -> PlaybookDefinitionRow | None:
        result = await self._db.execute(
            select(PlaybookDefinitionRow).where(
                PlaybookDefinitionRow.user_id == user_id,
                PlaybookDefinitionRow.id == definition_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_definitions(
        self, user_id: str, project_id: str | None = None
    ) -> list[PlaybookDefinitionRow]:
        statement = select(PlaybookDefinitionRow).where(PlaybookDefinitionRow.user_id == user_id)
        if project_id is not None:
            statement = statement.where(PlaybookDefinitionRow.project_id == project_id)
        result = await self._db.execute(statement.order_by(PlaybookDefinitionRow.updated_at.desc()))
        return list(result.scalars().all())

    async def get_version(
        self, user_id: str, definition_id: str, version: int
    ) -> PlaybookVersionRow | None:
        result = await self._db.execute(
            select(PlaybookVersionRow).where(
                PlaybookVersionRow.user_id == user_id,
                PlaybookVersionRow.definition_id == definition_id,
                PlaybookVersionRow.version == version,
            )
        )
        return result.scalar_one_or_none()

    async def list_versions(self, user_id: str, definition_id: str) -> list[PlaybookVersionRow]:
        result = await self._db.execute(
            select(PlaybookVersionRow)
            .where(
                PlaybookVersionRow.user_id == user_id,
                PlaybookVersionRow.definition_id == definition_id,
            )
            .order_by(PlaybookVersionRow.version.desc())
        )
        return list(result.scalars().all())

    async def get_run(self, user_id: str, run_id: str) -> PlaybookRunRow | None:
        result = await self._db.execute(
            select(PlaybookRunRow).where(
                PlaybookRunRow.user_id == user_id,
                PlaybookRunRow.id == run_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_runs(
        self,
        user_id: str,
        *,
        project_id: str | None = None,
        definition_id: str | None = None,
    ) -> list[PlaybookRunRow]:
        statement = select(PlaybookRunRow).where(PlaybookRunRow.user_id == user_id)
        if project_id is not None:
            statement = statement.where(PlaybookRunRow.project_id == project_id)
        if definition_id is not None:
            statement = statement.where(PlaybookRunRow.definition_id == definition_id)
        result = await self._db.execute(statement.order_by(PlaybookRunRow.created_at.desc()))
        return list(result.scalars().all())

    async def count_automation_bindings(self, user_id: str, definition_id: str) -> int:
        count = await self._db.scalar(
            select(func.count())
            .select_from(AutomationRow)
            .where(
                AutomationRow.user_id == user_id,
                AutomationRow.playbook_definition_id == definition_id,
            )
        )
        return int(count or 0)

    async def has_active_runs(self, user_id: str, definition_id: str) -> bool:
        run_id = await self._db.scalar(
            select(PlaybookRunRow.id)
            .where(
                PlaybookRunRow.user_id == user_id,
                PlaybookRunRow.definition_id == definition_id,
                PlaybookRunRow.status.in_({"queued", "planning", "running", "waiting_approval"}),
            )
            .limit(1)
        )
        return run_id is not None

    async def delete_definition_graph(self, user_id: str, definition_id: str) -> None:
        """Delete one Definition aggregate after the service validates safety.

        Playbook versions and runs are owned by the Definition lifecycle. Historic
        Automation runs keep their own snapshots, so their optional pointer is
        cleared before the PlaybookRun rows are removed.
        """
        run_ids = list(
            (
                await self._db.scalars(
                    select(PlaybookRunRow.id).where(
                        PlaybookRunRow.user_id == user_id,
                        PlaybookRunRow.definition_id == definition_id,
                    )
                )
            ).all()
        )
        if run_ids:
            await self._db.execute(
                update(AutomationRunRow)
                .where(
                    AutomationRunRow.user_id == user_id,
                    AutomationRunRow.playbook_run_id.in_(run_ids),
                )
                .values(playbook_run_id=None)
            )
        await self._db.execute(
            delete(PlaybookRunRow).where(
                PlaybookRunRow.user_id == user_id,
                PlaybookRunRow.definition_id == definition_id,
            )
        )
        await self._db.execute(
            delete(PlaybookVersionRow).where(
                PlaybookVersionRow.user_id == user_id,
                PlaybookVersionRow.definition_id == definition_id,
            )
        )
        await self._db.execute(
            delete(PlaybookDefinitionRow).where(
                PlaybookDefinitionRow.user_id == user_id,
                PlaybookDefinitionRow.id == definition_id,
            )
        )
        await self._db.flush()

    def add(
        self,
        row: PlaybookDefinitionRow | PlaybookVersionRow | PlaybookRunRow,
    ) -> None:
        self._db.add(row)

    async def flush(self) -> None:
        await self._db.flush()


__all__ = ["PlaybookDatastore"]

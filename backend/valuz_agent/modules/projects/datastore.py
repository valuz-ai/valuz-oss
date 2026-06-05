from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.projects.models import (
    WorkspaceContextRow,
    WorkspaceRow,
)


class WorkspaceDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_workspaces(self) -> list[WorkspaceRow]:
        return list(
            (await self._db.execute(select(WorkspaceRow).order_by(WorkspaceRow.sort_order)))
            .scalars()
            .all()
        )

    async def get_by_id(self, workspace_id: str) -> WorkspaceRow | None:
        return await self._db.get(WorkspaceRow, workspace_id)

    async def get_chat_workspace(self) -> WorkspaceRow | None:
        return (
            (await self._db.execute(select(WorkspaceRow).filter_by(kind="chat"))).scalars().first()
        )

    async def get_by_root_path(self, root_path: str) -> WorkspaceRow | None:
        return (
            (await self._db.execute(select(WorkspaceRow).filter_by(root_path=root_path)))
            .scalars()
            .first()
        )

    async def create(self, row: WorkspaceRow) -> WorkspaceRow:
        self._db.add(row)
        await self._db.commit()
        return row

    async def update(self, row: WorkspaceRow) -> WorkspaceRow:
        await self._db.merge(row)
        await self._db.commit()
        return row

    async def delete(self, workspace_id: str) -> None:
        await self._db.execute(delete(WorkspaceRow).where(WorkspaceRow.id == workspace_id))
        await self._db.execute(
            delete(WorkspaceContextRow).where(WorkspaceContextRow.workspace_id == workspace_id)
        )
        await self._db.commit()

    async def get_context(self, workspace_id: str) -> WorkspaceContextRow | None:
        return await self._db.get(WorkspaceContextRow, workspace_id)

    async def upsert_context(self, ctx: WorkspaceContextRow) -> WorkspaceContextRow:
        await self._db.merge(ctx)
        await self._db.commit()
        return ctx

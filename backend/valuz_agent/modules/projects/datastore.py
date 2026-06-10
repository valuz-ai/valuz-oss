from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.projects.models import ProjectRow


class ProjectDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_projects(self) -> list[ProjectRow]:
        return list(
            (await self._db.execute(select(ProjectRow).order_by(ProjectRow.sort_order)))
            .scalars()
            .all()
        )

    async def get_by_id(self, project_id: str) -> ProjectRow | None:
        return await self._db.get(ProjectRow, project_id)

    async def get_chat_project(self) -> ProjectRow | None:
        return (
            (await self._db.execute(select(ProjectRow).filter_by(kind="chat"))).scalars().first()
        )

    async def get_by_root_path(self, root_path: str) -> ProjectRow | None:
        return (
            (await self._db.execute(select(ProjectRow).filter_by(root_path=root_path)))
            .scalars()
            .first()
        )

    async def create(self, row: ProjectRow) -> ProjectRow:
        self._db.add(row)
        await self._db.commit()
        return row

    async def update(self, row: ProjectRow) -> ProjectRow:
        await self._db.merge(row)
        await self._db.commit()
        return row

    async def delete(self, project_id: str) -> None:
        await self._db.execute(delete(ProjectRow).where(ProjectRow.id == project_id))
        await self._db.commit()

    async def get_context(self, project_id: str) -> ProjectRow | None:
        """Context fields live on the row itself (the former 1:1 context
        table was folded in) — this is ``get_by_id`` under the name the
        context readers use."""
        return await self._db.get(ProjectRow, project_id)

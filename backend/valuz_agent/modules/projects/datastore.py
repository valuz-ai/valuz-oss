from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.projects.models import ProjectRow


class ProjectDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_projects(self, user_id: str) -> list[ProjectRow]:
        return list(
            (
                await self._db.execute(
                    select(ProjectRow)
                    .where(ProjectRow.user_id == user_id)
                    .order_by(ProjectRow.sort_order)
                )
            )
            .scalars()
            .all()
        )

    async def get_by_id(self, user_id: str, project_id: str) -> ProjectRow | None:
        # Owner-scoped by id: a row owned by another user reads as absent. Never
        # ``session.get`` here — it bypasses the WHERE filter and would leak the
        # row across owners.
        return (
            (
                await self._db.execute(
                    select(ProjectRow).where(
                        ProjectRow.id == project_id, ProjectRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def get_chat_project(self, user_id: str) -> ProjectRow | None:
        return (
            (
                await self._db.execute(
                    select(ProjectRow).where(
                        ProjectRow.kind == "chat", ProjectRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def get_by_root_path(self, user_id: str, root_path: str) -> ProjectRow | None:
        return (
            (
                await self._db.execute(
                    select(ProjectRow).where(
                        ProjectRow.root_path == root_path, ProjectRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def create(self, user_id: str, row: ProjectRow) -> ProjectRow:
        # Owner passed explicitly (no ContextVar write-stamp default).
        row.user_id = user_id
        self._db.add(row)
        await self._db.commit()
        return row

    async def update(self, row: ProjectRow) -> ProjectRow:
        await self._db.merge(row)
        await self._db.commit()
        return row

    async def delete(self, user_id: str, project_id: str) -> None:
        # Owner-scoped delete: scoping the WHERE to the caller's id makes a
        # cross-owner delete a no-op instead of destroying another user's row.
        await self._db.execute(
            delete(ProjectRow).where(ProjectRow.id == project_id, ProjectRow.user_id == user_id)
        )
        await self._db.commit()

    async def get_context(self, user_id: str, project_id: str) -> ProjectRow | None:
        """Context fields live on the row itself (the former 1:1 context
        table was folded in) — this is ``get_by_id`` under the name the
        context readers use."""
        return await self.get_by_id(user_id, project_id)

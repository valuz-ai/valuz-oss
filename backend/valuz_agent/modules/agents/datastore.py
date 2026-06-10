"""Datastores for Agent and Project Member tables.

Naming conventions mirror ``modules/schedules/datastore.py``:
  - ``list_*`` → returns list
  - ``get_*`` → returns Optional[Row]
  - ``create`` → adds + commits, returns Row
  - ``update`` → merge + commit, returns Row
  - ``delete`` → removes + commits
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow


class AgentDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_agents(self, source: str | None = None) -> list[AgentRow]:
        stmt = select(AgentRow).order_by(AgentRow.created_at)
        if source is not None:
            stmt = stmt.where(AgentRow.source == source)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_agent(self, slug: str) -> AgentRow | None:
        return (await self._db.execute(select(AgentRow).filter_by(slug=slug))).scalars().first()

    async def get_by_kernel_agent_id(self, kernel_agent_id: str) -> AgentRow | None:
        """Resolve the library AgentRow backing a shared kernel config id.

        Powers the v2 cascade: a project-side member edit resolves through its
        ``kernel_agent_id`` back to the AgentRow, then edits the agent globally.
        """
        return (
            (await self._db.execute(select(AgentRow).filter_by(kernel_agent_id=kernel_agent_id)))
            .scalars()
            .first()
        )

    async def create(self, row: AgentRow) -> AgentRow:
        self._db.add(row)
        await self._db.commit()
        return row

    async def update_fields(self, slug: str, fields: dict[str, object]) -> AgentRow | None:
        """Apply a partial update to an agent by slug. Returns None if absent."""
        row = await self.get_agent(slug)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._db.commit()
        return row

    async def delete(self, slug: str) -> bool:
        row = await self.get_agent(slug)
        if row is None:
            return False
        await self._db.delete(row)
        await self._db.commit()
        return True

    async def upsert(self, row: AgentRow) -> AgentRow:
        """Insert-or-update by slug. Merges by primary key if the id is already
        present; otherwise performs an INSERT. Used exclusively by the official
        agent seeder — never call from user-facing code paths."""
        existing = await self.get_agent(row.slug)
        if existing is not None:
            # Keep existing id; update all mutable fields
            existing.name = row.name
            existing.description = row.description
            existing.instructions = row.instructions
            existing.runtime = row.runtime
            existing.model = row.model
            existing.skills = row.skills
            existing.connector_types = row.connector_types
            existing.provider_id = row.provider_id
            existing.effort = row.effort
            existing.source = row.source
            await self._db.commit()
            return existing
        self._db.add(row)
        await self._db.commit()
        return row


class ProjectMemberDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_by_project(self, project_id: str) -> list[ProjectMemberRow]:
        return list(
            (
                await self._db.execute(
                    select(ProjectMemberRow)
                    .filter_by(project_id=project_id)
                    .order_by(ProjectMemberRow.created_at)
                )
            )
            .scalars()
            .all()
        )

    async def get(self, project_id: str, agent_slug: str) -> ProjectMemberRow | None:
        return (
            (
                await self._db.execute(
                    select(ProjectMemberRow).filter_by(
                        project_id=project_id, agent_slug=agent_slug
                    )
                )
            )
            .scalars()
            .first()
        )

    async def get_by_id(self, member_id: str) -> ProjectMemberRow | None:
        return await self._db.get(ProjectMemberRow, member_id)

    async def list_by_kernel_agent(self, kernel_agent_id: str) -> list[ProjectMemberRow]:
        """Every派驻 (across all projects) of one shared kernel agent.

        Powers the delete guard (block deleting a still-deployed agent) and the
        agent detail page's「派驻于 N 个项目」panel.
        """
        return list(
            (
                await self._db.execute(
                    select(ProjectMemberRow)
                    .filter_by(kernel_agent_id=kernel_agent_id)
                    .order_by(ProjectMemberRow.created_at)
                )
            )
            .scalars()
            .all()
        )

    async def create(self, row: ProjectMemberRow) -> ProjectMemberRow:
        self._db.add(row)
        await self._db.commit()
        return row

    async def update(self, row: ProjectMemberRow) -> ProjectMemberRow:
        await self._db.merge(row)
        await self._db.commit()
        return row

    async def delete(self, project_id: str, agent_slug: str) -> bool:
        res = await self._db.execute(
            sa_delete(ProjectMemberRow).where(
                ProjectMemberRow.project_id == project_id,
                ProjectMemberRow.agent_slug == agent_slug,
            )
        )
        await self._db.commit()
        return bool(res.rowcount)

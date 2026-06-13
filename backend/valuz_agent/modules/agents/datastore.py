"""Datastores for Agent and Project Member tables.

Naming conventions mirror ``modules/schedules/datastore.py``:
  - ``list_*`` → returns list
  - ``get_*`` → returns Optional[Row]
  - ``create`` → adds + commits, returns Row
  - ``update`` → merge + commit, returns Row
  - ``delete`` → removes + commits

Every read takes the caller's ``user_id`` first and filters on it; ``create``
stamps the owner explicitly (no ContextVar write-stamp default).
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow


class AgentDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_agents(self, user_id: str, source: str | None = None) -> list[AgentRow]:
        stmt = select(AgentRow).where(AgentRow.user_id == user_id).order_by(AgentRow.created_at)
        if source is not None:
            stmt = stmt.where(AgentRow.source == source)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_agent(self, user_id: str, slug: str) -> AgentRow | None:
        return (
            (
                await self._db.execute(
                    select(AgentRow).where(AgentRow.slug == slug, AgentRow.user_id == user_id)
                )
            )
            .scalars()
            .first()
        )

    async def create(self, user_id: str, row: AgentRow) -> AgentRow:
        row.user_id = user_id
        self._db.add(row)
        await self._db.commit()
        return row

    async def update_fields(
        self, user_id: str, slug: str, fields: dict[str, object]
    ) -> AgentRow | None:
        """Apply a partial update to an agent by slug. Returns None if absent."""
        row = await self.get_agent(user_id, slug)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._db.commit()
        return row

    async def delete(self, user_id: str, slug: str) -> bool:
        row = await self.get_agent(user_id, slug)
        if row is None:
            return False
        await self._db.delete(row)
        await self._db.commit()
        return True

    async def upsert(self, user_id: str, row: AgentRow) -> AgentRow:
        """Insert-or-update by slug. Merges by primary key if the id is already
        present; otherwise performs an INSERT. Used exclusively by the official
        agent seeder — never call from user-facing code paths."""
        existing = await self.get_agent(user_id, row.slug)
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
        row.user_id = user_id
        self._db.add(row)
        await self._db.commit()
        return row


class ProjectMemberDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_by_project(self, user_id: str, project_id: str) -> list[ProjectMemberRow]:
        return list(
            (
                await self._db.execute(
                    select(ProjectMemberRow)
                    .where(
                        ProjectMemberRow.project_id == project_id,
                        ProjectMemberRow.user_id == user_id,
                    )
                    .order_by(ProjectMemberRow.created_at)
                )
            )
            .scalars()
            .all()
        )

    async def get(self, user_id: str, project_id: str, agent_slug: str) -> ProjectMemberRow | None:
        return (
            (
                await self._db.execute(
                    select(ProjectMemberRow).where(
                        ProjectMemberRow.project_id == project_id,
                        ProjectMemberRow.agent_slug == agent_slug,
                        ProjectMemberRow.user_id == user_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def get_by_id(self, user_id: str, member_id: str) -> ProjectMemberRow | None:
        return (
            (
                await self._db.execute(
                    select(ProjectMemberRow).where(
                        ProjectMemberRow.id == member_id, ProjectMemberRow.user_id == user_id
                    )
                )
            )
            .scalars()
            .first()
        )

    async def list_by_source_agent_slug(
        self, user_id: str, source_agent_slug: str
    ) -> list[ProjectMemberRow]:
        """Every membership row deployed from the given library agent.

        Powers the delete guard (block deleting a still-deployed agent) and
        the agent detail page's「派驻于 N 个项目」panel.
        """
        return list(
            (
                await self._db.execute(
                    select(ProjectMemberRow)
                    .where(
                        ProjectMemberRow.source_agent_slug == source_agent_slug,
                        ProjectMemberRow.user_id == user_id,
                    )
                    .order_by(ProjectMemberRow.created_at)
                )
            )
            .scalars()
            .all()
        )

    async def create(self, user_id: str, row: ProjectMemberRow) -> ProjectMemberRow:
        row.user_id = user_id
        self._db.add(row)
        await self._db.commit()
        return row

    async def update(self, row: ProjectMemberRow) -> ProjectMemberRow:
        await self._db.merge(row)
        await self._db.commit()
        return row

    async def delete(self, user_id: str, project_id: str, agent_slug: str) -> bool:
        res = await self._db.execute(
            sa_delete(ProjectMemberRow).where(
                ProjectMemberRow.project_id == project_id,
                ProjectMemberRow.agent_slug == agent_slug,
                ProjectMemberRow.user_id == user_id,
            )
        )
        await self._db.commit()
        return bool(res.rowcount)

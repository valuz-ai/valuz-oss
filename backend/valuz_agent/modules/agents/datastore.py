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

from sqlalchemy import case, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.projects.models import ProjectRow


class AgentDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_agents(self, user_id: str, source: str | None = None) -> list[AgentRow]:
        stmt = (
            select(AgentRow)
            .where(AgentRow.user_id == user_id)
            .order_by(
                case((AgentRow.kind == "system", 0), else_=1),
                AgentRow.created_at,
            )
        )
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
        present; otherwise performs an INSERT. Used by idempotent system-owned
        paths such as official seeding and runtime resource sync — not by
        user-facing create/update routes that need conflict semantics."""
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
            existing.knowledge_scope = row.knowledge_scope
            existing.provider_id = row.provider_id
            existing.effort = row.effort
            existing.kind = row.kind
            existing.resource_policy = row.resource_policy
            existing.inherit_global_instructions = row.inherit_global_instructions
            existing.permission_mode = row.permission_mode
            existing.source = row.source
            existing.readonly = row.readonly
            existing.deletable = row.deletable
            existing.avatar = row.avatar
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

    async def display_names_by_slug(
        self, user_id: str, project_id: str, agent_slugs: list[str]
    ) -> dict[str, str]:
        """Map member ``agent_slug`` → library-agent display name, in ONE query.

        A display name is the ONLY thing several hot paths want, and the
        general route to it — resolve the membership, then build the member's
        full ``AgentConfig`` — also resolves connectors and can refresh an
        OAuth token. Plan snapshots stamp a name per node on every plan write,
        so that route cost roughly nine queries per write for text that never
        changes. Slugs with no membership, no ``source_agent_slug`` or no
        library row are simply absent; callers fall back to the slug.
        """
        if not agent_slugs:
            return {}
        rows = (
            await self._db.execute(
                select(ProjectMemberRow.agent_slug, AgentRow.name)
                .join(
                    AgentRow,
                    (AgentRow.slug == ProjectMemberRow.source_agent_slug)
                    & (AgentRow.user_id == ProjectMemberRow.user_id),
                )
                .where(
                    ProjectMemberRow.project_id == project_id,
                    ProjectMemberRow.user_id == user_id,
                    ProjectMemberRow.agent_slug.in_(agent_slugs),
                )
            )
        ).all()
        return {slug: name for slug, name in rows if name}

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

        Join through ``ProjectRow`` so stale member rows left behind by older
        project-delete code no longer count as live deployments.
        """
        return list(
            (
                await self._db.execute(
                    select(ProjectMemberRow)
                    .join(
                        ProjectRow,
                        (ProjectRow.id == ProjectMemberRow.project_id)
                        & (ProjectRow.user_id == ProjectMemberRow.user_id),
                    )
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
        return bool(getattr(res, "rowcount", 0))

    async def delete_by_project(self, user_id: str, project_id: str) -> int:
        res = await self._db.execute(
            sa_delete(ProjectMemberRow).where(
                ProjectMemberRow.project_id == project_id,
                ProjectMemberRow.user_id == user_id,
            )
        )
        await self._db.commit()
        return int(getattr(res, "rowcount", 0) or 0)

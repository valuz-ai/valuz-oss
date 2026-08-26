"""Adapters that let the channels module read project agent placements."""

from __future__ import annotations

from valuz_agent.modules.agents.datastore import ProjectMemberDatastore
from valuz_agent.modules.channels.schemas import AgentPlacement
from valuz_agent.modules.projects.datastore import ProjectDatastore


class DatastoreAgentPlacementReader:
    def __init__(self, *, members: ProjectMemberDatastore, projects: ProjectDatastore) -> None:
        self._members = members
        self._projects = projects

    async def list_placements(
        self,
        user_id: str,
        source_agent_slug: str,
    ) -> list[AgentPlacement]:
        rows = await self._members.list_by_source_agent_slug(user_id, source_agent_slug)
        placements: list[AgentPlacement] = []
        for row in rows:
            project = await self._projects.get_by_id(user_id, row.project_id)
            if project is None:
                continue
            placements.append(
                AgentPlacement(
                    project_id=row.project_id,
                    project_name=project.name,
                    agent_slug=row.agent_slug,
                    source_agent_slug=row.source_agent_slug,
                )
            )
        return placements


class DatastoreProjectMemberReader:
    """Members of one project, for resolving an agent named in a chat message."""

    def __init__(self, *, members: ProjectMemberDatastore) -> None:
        self._members = members

    async def list_member_slugs(self, user_id: str, project_id: str) -> list[str]:
        rows = await self._members.list_by_project(user_id, project_id)
        return [row.agent_slug for row in rows]


__all__ = ["DatastoreAgentPlacementReader", "DatastoreProjectMemberReader"]

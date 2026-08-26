"""Stable project commands for overlays and edition plugins.

The projects module owns filesystem allocation, kernel mirroring, delete
previews and owner scoping.  Callers outside OSS must not reconstruct those
rules by importing its datastore or service directly, so this facade exposes
the small command surface needed to resolve a domain object into a Project.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast


@dataclass(frozen=True, slots=True)
class ProjectRef:
    """Owner-scoped project identity returned across the overlay boundary."""

    id: str
    name: str
    kind: Literal["chat", "project"]


@asynccontextmanager
async def _project_service() -> AsyncGenerator[Any, None]:
    # Keep the heterogeneous FastAPI dependency and project implementation on
    # the OSS side of this boundary.  Editions see only ProjectLibrary.
    from valuz_agent.api.deps import get_project_service

    generator = get_project_service()
    service = await generator.__anext__()
    try:
        yield service
    finally:
        await generator.aclose()


def _ref(row: Any) -> ProjectRef:
    kind = cast(
        Literal["chat", "project"],
        row.kind if row.kind in ("chat", "project") else "project",
    )
    return ProjectRef(id=row.id, name=row.name, kind=kind)


class ProjectLibrary:
    """Owner-scoped read/write facade over Projects.

    Every method requires an explicit ``user_id``.  A project belonging to a
    different owner is indistinguishable from a missing project.
    """

    async def list(
        self,
        user_id: str,
        *,
        kind: Literal["chat", "project"] | None = None,
    ) -> list[ProjectRef]:
        async with _project_service() as service:
            rows = await service.list_projects(user_id)
        return [_ref(row) for row in rows if kind is None or row.kind == kind]

    async def get(self, user_id: str, project_id: str) -> ProjectRef | None:
        async with _project_service() as service:
            try:
                row = await service.get_project(user_id, project_id)
            except KeyError:
                return None
        return _ref(row)

    async def create(
        self,
        user_id: str,
        *,
        name: str,
        root_path: str | None = None,
    ) -> ProjectRef:
        """Create a normal Project using the canonical managed-root rules."""
        async with _project_service() as service:
            row = await service.create_project(user_id, name, root_path)
        return _ref(row)

    async def create_chat(self, user_id: str, *, name: str = "Chat") -> ProjectRef:
        """Create the hidden chat Project used by unbound durable runs."""
        async with _project_service() as service:
            row = await service.create_chat_project_for_session(user_id, name=name)
        return _ref(row)

    async def delete(self, user_id: str, project_id: str) -> bool:
        """Delete an owner-scoped Project; return false when it is absent."""
        async with _project_service() as service:
            try:
                await service.delete_project(user_id, project_id)
            except KeyError:
                return False
        return True


async def get_project_library() -> AsyncGenerator[ProjectLibrary, None]:
    """FastAPI-compatible dependency for overlay routes."""
    yield ProjectLibrary()


__all__ = ["ProjectLibrary", "ProjectRef", "get_project_library"]

"""Owner-scoped, fail-closed preparation for canonical Project deletion."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ProjectLifecycleHook(ABC):
    @abstractmethod
    async def before_project_delete(
        self, *, db: AsyncSession, user_id: str, project_id: str
    ) -> None:
        """Run before any children are removed; raising aborts deletion.

        The caller has checked ownership and rejected hidden chat Projects.
        Implementations must tolerate retries. External commits are not part of
        the caller's transaction; use durable, idempotent preparation receipts.
        This hook is NOT called when moving a Project between executors.
        """


class NoopProjectLifecycleHook(ProjectLifecycleHook):
    async def before_project_delete(
        self, *, db: AsyncSession, user_id: str, project_id: str
    ) -> None:
        return None


def get_project_lifecycle_hook() -> ProjectLifecycleHook:
    from valuz_agent.ports.extensions import ext

    return ext.project_lifecycle


def set_project_lifecycle_hook(hook: ProjectLifecycleHook) -> None:
    from valuz_agent.ports.extensions import ext

    ext.project_lifecycle = hook

"""Skill lifecycle extension hook.

OSS keeps skill filesystem/index behavior in ``SkillLibraryService``. Overlays
can bind this hook to mirror successful skill writes/deletes to external
systems without replacing HTTP routes or middleware.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from valuz_agent.modules.skills.models import SkillView

SkillSaveOrigin = Literal["created", "imported"]


class SkillLifecycleHook(ABC):
    """Callbacks around user-visible skill writes and deletes."""

    @abstractmethod
    async def after_bundled_skills_materialized(
        self,
        *,
        user_id: str,
        slugs: tuple[str, ...],
    ) -> None:
        """Called after bundled Agent Pack skills are materialized and indexed."""
        ...

    @abstractmethod
    async def after_skill_saved(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        skill: SkillView,
        creation_origin: SkillSaveOrigin,
    ) -> None:
        """Called after indexing with the still-uncommitted owning unit of work."""
        ...

    @abstractmethod
    async def before_skill_delete(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        skill: SkillView,
    ) -> None:
        """Called before delete with the same unit of work; raising aborts deletion."""
        ...


class NoopSkillLifecycleHook(SkillLifecycleHook):
    async def after_bundled_skills_materialized(
        self,
        *,
        user_id: str,
        slugs: tuple[str, ...],
    ) -> None:
        return None

    async def after_skill_saved(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        skill: SkillView,
        creation_origin: SkillSaveOrigin,
    ) -> None:
        return None

    async def before_skill_delete(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        skill: SkillView,
    ) -> None:
        return None


def get_skill_lifecycle_hook() -> SkillLifecycleHook:
    from valuz_agent.ports.extensions import ext

    return ext.skill_lifecycle


def set_skill_lifecycle_hook(hook: SkillLifecycleHook) -> None:
    from valuz_agent.ports.extensions import ext

    ext.skill_lifecycle = hook


__all__ = [
    "NoopSkillLifecycleHook",
    "SkillLifecycleHook",
    "SkillSaveOrigin",
    "get_skill_lifecycle_hook",
    "set_skill_lifecycle_hook",
]

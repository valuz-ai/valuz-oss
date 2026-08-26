"""Agent lifecycle extension hook.

OSS owns the local agent library rows. Overlays can bind this hook to mirror
successful agent config writes/deletes to an external desired-state service
without replacing routes or monkey-patching the agent service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from valuz_agent.modules.agents.models import AgentRow

AgentSaveOrigin = Literal["created", "updated"]


class AgentLifecycleHook(ABC):
    """Callbacks around user-visible agent config writes and deletes."""

    @abstractmethod
    async def after_agent_saved(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        agent: AgentRow,
        origin: AgentSaveOrigin,
    ) -> None:
        """Called after save with the still-uncommitted owning unit of work."""
        ...

    @abstractmethod
    async def before_agent_delete(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        agent: AgentRow,
    ) -> None:
        """Called before delete with the same unit of work; raising aborts deletion."""
        ...


class NoopAgentLifecycleHook(AgentLifecycleHook):
    async def after_agent_saved(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        agent: AgentRow,
        origin: AgentSaveOrigin,
    ) -> None:
        return None

    async def before_agent_delete(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        agent: AgentRow,
    ) -> None:
        return None


def get_agent_lifecycle_hook() -> AgentLifecycleHook:
    from valuz_agent.ports.extensions import ext

    return ext.agent_lifecycle


def set_agent_lifecycle_hook(hook: AgentLifecycleHook) -> None:
    from valuz_agent.ports.extensions import ext

    ext.agent_lifecycle = hook


__all__ = [
    "AgentLifecycleHook",
    "AgentSaveOrigin",
    "NoopAgentLifecycleHook",
    "get_agent_lifecycle_hook",
    "set_agent_lifecycle_hook",
]

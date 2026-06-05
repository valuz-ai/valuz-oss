"""Async facade over the V5 kernel's async StorePort.

This is the **event-loop-native** counterpart to ``kernel_sync``. Async host
code (route handlers, orchestrator methods, MCP tool handlers) MUST use this
facade — it ``await``s the kernel store on the running loop, so the loop is
never blocked.

``kernel_sync`` (the sync facade) spins a throwaway thread + event loop per
call and ``join()``s it, which **blocks the calling event loop** when invoked
from ``async def`` — defeating the ADR-020 async migration on the kernel half.
It remains valid ONLY for genuinely synchronous seams (background threads, sync
eventbus handlers, CLI, startup). See ``kernel_sync`` docstring.

Migration rule (async contexts only)::

    kernel_sync.load_session_sync(x)   ->   await kernel_store.load_session(x)
    kernel_sync.save_session_sync(s)   ->   await kernel_store.save_session(s)

Keeping all kernel coupling behind ``adapters/`` means the rest of valuz never
imports ``app.dependencies`` directly.
"""

from __future__ import annotations

# mypy: disable-error-code="no-any-return"
# The kernel boundary is configured ``follow_imports = "skip"`` (see pyproject
# [tool.mypy] overrides for ``src.*``), so ``StorePort`` and its return types
# resolve to ``Any``. Every ``return await _store().<m>(...)`` therefore trips
# ``no-any-return`` despite correct annotations. ``kernel_sync`` carries the
# same unavoidable pattern; we silence it here at module scope instead of
# scattering per-line ignores.

# ruff: noqa: I001
# Custom import order: the kernel side-effect import MUST run before any
# ``from src.core ...`` so ``sys.path`` has the kernel root by the time we
# resolve those names (mirrors ``kernel_sync``).

import valuz_agent.boot.kernel  # noqa: F401  (sys.path side-effect)

from src.core import (  # type: ignore[import-not-found]
    AgentConfig,
    Event,
    Message,
    Project,
    Session,
    StorePort,
)


def _store() -> StorePort:
    # Imported lazily so ``init_kernel_dependencies`` has populated the
    # singleton by the time we're called.
    from app.dependencies import get_store  # type: ignore[import-not-found]

    return get_store()


# ---- Project operations ----


async def save_project(project: Project) -> None:
    await _store().save_project(project)


async def load_project(project_id: str) -> Project | None:
    return await _store().load_project(project_id)


async def delete_project(project_id: str) -> bool:
    return await _store().delete_project(project_id)


# ---- Agent operations ----


async def save_agent(agent: AgentConfig) -> None:
    await _store().save_agent(agent)


async def load_agent(agent_id: str) -> AgentConfig | None:
    return await _store().load_agent(agent_id)


async def delete_agent(agent_id: str) -> bool:
    return await _store().delete_agent(agent_id)


async def list_agents(*, status: str = "active") -> list[AgentConfig]:
    """List kernel AgentConfig rows, post-filtered by status client-side.

    Mirrors ``kernel_sync.list_agents_sync``: ``StorePort.list_agents`` has no
    status filter, so we paginate (limit=200, MVP-sufficient) and filter here.
    """
    agents = await _store().list_agents(limit=200, offset=0)
    return [a for a in agents if a.status == status]


# ---- Session operations ----


async def save_session(session: Session) -> None:
    await _store().save_session(session)


async def load_session(session_id: str) -> Session | None:
    return await _store().load_session(session_id)


async def list_sessions(
    *,
    project_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Session]:
    return await _store().list_sessions(project_id=project_id, limit=limit, offset=offset)


async def delete_session(session_id: str) -> bool:
    return await _store().delete_session(session_id)


# ---- Event operations ----


async def get_events(
    session_id: str,
    *,
    limit: int = 200,
    offset: int = 0,
) -> list[Event]:
    return await _store().get_events(session_id, limit=limit, offset=offset)


async def append_event(session_id: str, message_id: str, event: Event) -> None:
    await _store().append_event(session_id, message_id, event)


async def list_messages_for_session(
    session_id: str, *, limit: int = 50, offset: int = 0
) -> list[Message]:
    return await _store().list_messages_for_session(session_id, limit=limit, offset=offset)


async def latest_message_id(session_id: str) -> str | None:
    """Id of the most recent Message for ``session_id``, or None if none yet.

    Mirrors ``kernel_sync.latest_message_id_sync``.
    """
    messages = await _store().list_messages_for_session(session_id, limit=1)
    if not messages:
        return None
    return messages[0].id


async def append_session_scoped_event(session_id: str, event: Event) -> bool:
    """Append an out-of-band event onto the session's latest message.

    Returns ``True`` if persisted, ``False`` if the session has no messages
    yet (event dropped). Mirrors ``kernel_sync.append_session_scoped_event_sync``.
    """
    message_id = await latest_message_id(session_id)
    if message_id is None:
        return False
    await append_event(session_id, message_id, event)
    return True


__all__ = [
    "append_event",
    "append_session_scoped_event",
    "delete_agent",
    "delete_project",
    "delete_session",
    "get_events",
    "latest_message_id",
    "list_agents",
    "list_messages_for_session",
    "list_sessions",
    "load_agent",
    "load_project",
    "load_session",
    "save_agent",
    "save_project",
    "save_session",
]

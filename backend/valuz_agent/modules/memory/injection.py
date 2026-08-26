"""Frozen memory block for a new session's instructions (memory-system-design §8).

The in-scope memory (USER + global MEMORY + this project's MEMORY) is rendered
ONCE at session create and frozen into ``Session.instructions`` as a
``<memory>`` section — the session field is already captured at create time
and immutable for the session's life (ADR-008), so the block is byte-stable
(prefix-cache friendly), appears exactly once per session (not once per user
message), and never reflects mid-session writes — those land on disk and
surface in the next session. Load-time sanitization happens inside
``MemoryStore.render_for_injection``.

The former ``InjectionAssembler`` (per-session in-process LRU feeding the
per-turn additional-context) is retired: freezing into the durable session row
survives host restarts, where the LRU silently re-captured mid-session.
"""

from __future__ import annotations

import logging

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.memory.service import MemoryStore, memory_store
from valuz_agent.modules.settings.preferences import get_memory_enabled

logger = logging.getLogger(__name__)


async def memory_instructions_block(
    *,
    user_id: str | None,
    project_id: str | None = None,
    store: MemoryStore | None = None,
) -> str:
    """Render the memory section body for a session being created.

    Called once per session by the three create paths (chat/project agent
    path, task lead/member path, raw quick-chat path); the caller wraps the
    result in a ``<memory>`` section via ``assemble_session_instructions``.
    Returns ``""`` when memory is disabled, empty, the lookup fails, or the
    caller has no user identity (memory is per-user) — never blocks session
    creation.
    """
    if not user_id:
        return ""
    try:
        async with async_unit_of_work() as db:
            if not await get_memory_enabled(db, user_id=user_id):
                return ""
        return (store or memory_store).render_for_injection(user_id, project_id=project_id)
    except Exception:  # noqa: BLE001 — memory must never block a session create
        logger.debug("memory instructions block skipped", exc_info=True)
        return ""

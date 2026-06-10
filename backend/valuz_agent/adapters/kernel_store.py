"""Async facade over the V5 kernel's async StorePort — the host's single
kernel seam.

Every host access to kernel state goes through this module, awaited on the
running event loop. The former sync facade (``kernel_sync``, a
thread-per-call bridge) is gone: all call sites are async now, so blocking
bridges are no longer needed anywhere.

Keeping all kernel coupling behind ``adapters/`` means the rest of valuz never
imports ``app.dependencies`` directly.
"""

from __future__ import annotations

# mypy: disable-error-code="no-any-return"
# The kernel boundary is configured ``follow_imports = "skip"`` (see pyproject
# [tool.mypy] overrides for ``src.*``), so ``StorePort`` and its return types
# resolve to ``Any``. Every ``return await _store().<m>(...)`` therefore trips
# ``no-any-return`` despite correct annotations; we silence it here at module
# scope instead of scattering per-line ignores.

# ruff: noqa: I001
# Custom import order: the kernel side-effect import MUST run before any
# ``from src.core ...`` so ``sys.path`` has the kernel root by the time we
# resolve those names.

from collections.abc import Sequence

import valuz_agent.boot.kernel  # noqa: F401  (sys.path side-effect)

from src.core import (  # type: ignore[import-not-found]
    Event,
    Message,
    Session,
    StorePort,
)


def _store() -> StorePort:
    # Imported lazily so ``init_kernel_dependencies`` has populated the
    # singleton by the time we're called.
    from app.dependencies import get_store  # type: ignore[import-not-found]

    return get_store()


# ---- Session operations ----


async def save_session(session: Session) -> None:
    await _store().save_session(session)


async def load_session(session_id: str) -> Session | None:
    return await _store().load_session(session_id)


async def list_sessions(
    *,
    project_id: str | None = None,
    status: str | None = None,
    ids: Sequence[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Session]:
    return await _store().list_sessions(
        project_id=project_id, status=status, ids=ids, limit=limit, offset=offset
    )


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
    """Id of the most recent Message for ``session_id``, or None if none yet."""
    messages = await _store().list_messages_for_session(session_id, limit=1)
    if not messages:
        return None
    return messages[0].id


async def append_session_scoped_event(session_id: str, event: Event) -> bool:
    """Append an out-of-band event onto the session's latest message.

    Returns ``True`` if persisted, ``False`` if the session has no messages
    yet (event dropped).
    """
    message_id = await latest_message_id(session_id)
    if message_id is None:
        return False
    await append_event(session_id, message_id, event)
    return True


__all__ = [
    "append_event",
    "append_session_scoped_event",
    "delete_session",
    "get_events",
    "latest_message_id",
    "list_messages_for_session",
    "list_sessions",
    "load_session",
    "save_session",
]

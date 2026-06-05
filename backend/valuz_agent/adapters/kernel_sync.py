"""Sync facade over the V5 kernel's async StorePort.

The vendored kernel is async-only (every ``StorePort`` method is a coroutine).
Most valuz business code is sync — domain services accept a SQLAlchemy ``Session``
and run on FastAPI threadpool handlers. Without a bridge they can't talk to the
kernel.

This module gives us one tightly-scoped escape hatch: a small set of sync
helpers that drive the kernel's async store via ``asyncio.run`` inside a
dedicated thread, the same trick we use for the kernel's Alembic upgrade. By
keeping all kernel-coupling behaviour here, the rest of valuz never touches
``app.dependencies`` or ``src.core`` directly.

If a future caller is itself async, prefer to ``await`` the kernel store
directly via ``app.dependencies.get_store()`` instead of going through this
sync facade.
"""

# ruff: noqa: I001
# Custom import order: the kernel side-effect import MUST run before
# any ``from src.core ...`` so that ``sys.path`` has the kernel root by
# the time we resolve those names. ruff's auto-sorter would otherwise
# move the side-effect line below the ``src.core`` block.

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import TypeVar

import valuz_agent.boot.kernel  # noqa: F401

from src.core import (  # type: ignore[import-not-found]
    AgentConfig,
    Event,
    Project,
    Session,
    StorePort,
)

T = TypeVar("T")


def _run_in_thread(coro_factory: Callable[[], Awaitable[T]]) -> T:
    """Run ``await coro_factory()`` on a fresh event loop in a worker thread.

    Required because callers may already be running inside an event loop
    (FastAPI lifespan, ``TestClient`` startup, async test fixtures). The
    kernel's ``async`` API can't be used from sync code on such a thread —
    ``asyncio.run`` would raise. A dedicated thread sidesteps the issue.
    """
    holder: dict[str, object] = {}

    def _runner() -> None:
        try:
            holder["value"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001
            holder["error"] = exc

    thread = threading.Thread(target=_runner, name="kernel-sync", daemon=True)
    thread.start()
    thread.join()
    if "error" in holder:
        raise holder["error"]  # type: ignore[misc]
    return holder["value"]  # type: ignore[return-value]


def _get_store() -> StorePort:
    # Imported lazily so ``init_kernel_dependencies`` has had a chance to
    # populate the singletons by the time we're called.
    from app.dependencies import get_store  # type: ignore[import-not-found]

    return get_store()


# ---- Project operations ----


def save_project_sync(project: Project) -> None:
    store = _get_store()
    _run_in_thread(lambda: store.save_project(project))


def load_project_sync(project_id: str) -> Project | None:
    store = _get_store()
    return _run_in_thread(lambda: store.load_project(project_id))


def delete_project_sync(project_id: str) -> bool:
    store = _get_store()
    return _run_in_thread(lambda: store.delete_project(project_id))


# ---- Agent operations ----


def save_agent_sync(agent: AgentConfig) -> None:
    store = _get_store()
    _run_in_thread(lambda: store.save_agent(agent))


def load_agent_sync(agent_id: str) -> AgentConfig | None:
    store = _get_store()
    return _run_in_thread(lambda: store.load_agent(agent_id))


def delete_agent_sync(agent_id: str) -> bool:
    store = _get_store()
    return _run_in_thread(lambda: store.delete_agent(agent_id))


def list_agents_sync(*, status: str = "active") -> list[AgentConfig]:
    """List kernel AgentConfig rows, filtered by status.

    Wraps ``StorePort.list_agents`` which returns agents ordered by
    creation time descending. Post-filters by ``status`` client-side
    because ``StorePort.list_agents`` does not accept a status filter.
    Paginates internally (limit=200) which is sufficient for MVP; raise
    a follow-up if the workspace ever exceeds ~200 agents.
    """
    store = _get_store()
    agents: list[AgentConfig] = _run_in_thread(lambda: store.list_agents(limit=200, offset=0))
    return [a for a in agents if a.status == status]


# ---- Session operations ----


def save_session_sync(session: Session) -> None:
    store = _get_store()
    _run_in_thread(lambda: store.save_session(session))


def load_session_sync(session_id: str) -> Session | None:
    store = _get_store()
    return _run_in_thread(lambda: store.load_session(session_id))


def list_sessions_sync(
    *,
    project_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Session]:
    store = _get_store()
    return _run_in_thread(
        lambda: store.list_sessions(project_id=project_id, limit=limit, offset=offset)
    )


def list_user_sessions_sync(
    *,
    project_id: str | None = None,
    limit: int = 50,
) -> list[Session]:
    """``list_sessions`` variant that excludes task-internal sessions
    (lead / dispatched sub-runs) at the SQL layer.

    The kernel's own ``list_sessions`` has no metadata filter and is
    read-only vendored (ADR-003), so we run a filtered SELECT here — the
    single sanctioned place for kernel coupling — adding a json_extract
    predicate on ``metadata.valuz.task_id IS NULL`` and reusing the
    kernel's own row→domain converter so the returned objects are byte-for-
    byte identical to ``list_sessions``. The ``LIMIT`` applies *after* the
    filter, so callers get exactly N user sessions instead of N rows that
    might all be task-internal.
    """
    from sqlalchemy import func, select  # type: ignore[import-not-found]

    from src.adapters.sqlalchemy_store.converters import (  # type: ignore[import-not-found]
        model_to_session,
    )
    from src.adapters.sqlalchemy_store.models import (  # type: ignore[import-not-found]
        SessionModel,
    )

    store = _get_store()
    # The host always wires the SQLAlchemy store; reach its async session
    # factory to run the filtered query on the shared SQLite file.
    session_factory = store._session_factory  # type: ignore[attr-defined]  # noqa: SLF001

    async def _query() -> list[Session]:
        async with session_factory() as db:
            stmt = select(SessionModel).where(
                func.json_extract(SessionModel.metadata_, "$.valuz.task_id").is_(None)
            )
            if project_id is not None:
                stmt = stmt.where(SessionModel.project_id == project_id)
            stmt = stmt.order_by(SessionModel.created_at.desc()).limit(limit)
            result = await db.execute(stmt)
            return [model_to_session(m) for m in result.scalars()]

    return _run_in_thread(_query)


def delete_session_sync(session_id: str) -> bool:
    store = _get_store()
    return _run_in_thread(lambda: store.delete_session(session_id))


# ---- Event operations ----


def get_events_sync(
    session_id: str,
    *,
    limit: int = 200,
    offset: int = 0,
) -> list[Event]:
    store = _get_store()
    return _run_in_thread(lambda: store.get_events(session_id, limit=limit, offset=offset))


def append_event_sync(session_id: str, message_id: str, event: Event) -> None:
    """Append an event scoped to a known (session, message) pair.

    Mirrors the kernel's ``StorePort.append_event`` signature. Callers
    that don't have a message_id in hand should use
    ``append_session_scoped_event_sync`` instead — it looks up the
    latest message for the session.
    """
    store = _get_store()
    _run_in_thread(lambda: store.append_event(session_id, message_id, event))


def latest_message_id_sync(session_id: str) -> str | None:
    """Return the id of the most recent Message for ``session_id``.

    The kernel's V5+ ``messages`` shape requires every event row to
    carry a ``message_id``. Out-of-band emitters (recovery, candidate
    detector, interrupt fallback) anchor onto the latest message
    instead of inventing a synthetic id. Returns ``None`` when no
    messages exist yet — callers should treat that as "skip
    persistence".
    """
    store = _get_store()

    async def _do() -> str | None:
        messages = await store.list_messages_for_session(session_id, limit=1)
        if not messages:
            return None
        return messages[0].id

    return _run_in_thread(_do)


def append_session_scoped_event_sync(session_id: str, event: Event) -> bool:
    """Append an out-of-band event onto the session's latest message.

    Use this when an event needs to land in the kernel ``events`` table
    but the caller is not driving an active turn (recovery on boot,
    skill candidate detection after the fact, interrupt fallback when
    the orchestrator can't be reached). Returns ``True`` if persisted,
    ``False`` if the session has no messages yet (event silently
    dropped — the caller should log if it cares).
    """
    message_id = latest_message_id_sync(session_id)
    if message_id is None:
        return False
    append_event_sync(session_id, message_id, event)
    return True


__all__ = [
    "save_project_sync",
    "load_project_sync",
    "delete_project_sync",
    "save_agent_sync",
    "load_agent_sync",
    "delete_agent_sync",
    "list_agents_sync",
    "save_session_sync",
    "load_session_sync",
    "list_sessions_sync",
    "list_user_sessions_sync",
    "delete_session_sync",
    "get_events_sync",
    "append_event_sync",
    "latest_message_id_sync",
    "append_session_scoped_event_sync",
]

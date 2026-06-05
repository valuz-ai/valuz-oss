"""Single entry point for all host (``valuz_*``) DB access — fully async.

See ADR-020 (host async DB) + ``docs/exec-plans/completed/sqlite-write-contention.md``.

All host data access is async (aiosqlite): aiosqlite runs each connection's
blocking calls on its own background thread and awaits the result, so
``await session.commit()`` never blocks the event loop — which structurally
removes the VALUZ-DBLOCK deadlock. There is no sync session machinery anymore;
the only remaining sync engine (``infra.database.engine``) is a bootstrap-only
tool for Alembic migrations + the pre-v2 wipe (DDL, no sessions).

Async callers (route handlers, services, on-loop scheduler/orchestrator tasks)::

    async with async_unit_of_work() as db:
        await SomeDatastore(db).create_x(...)
    # commit (with lock-retry) on clean exit; rollback on exception; close always.

Route handlers use the ``get_async_session`` dependency.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Lock-contention retry budget. The async host engine and the async kernel
# engine share one SQLite file; under parallel dispatch they compete for the
# single write slot. Exponential backoff + jitter de-correlates competing
# writers. The backoff is ``await asyncio.sleep`` so the loop stays free.
_LOCK_RETRY_ATTEMPTS = 12


async def async_commit_with_retry(
    db: AsyncSession, *, where: str = "commit", attempts: int = _LOCK_RETRY_ATTEMPTS
) -> None:
    """``await db.commit()`` retrying on SQLite 'database is locked'.

    Non-blocking: the backoff is ``await asyncio.sleep`` (the loop stays free),
    and aiosqlite runs the commit itself on its own thread.
    """
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            await db.commit()
            return
        except OperationalError as exc:
            await db.rollback()
            last_exc = exc
            if "locked" not in str(exc).lower():
                raise
            logger.warning("LOCKDIAG[%s] attempt=%d err=%s", where, attempt + 1, str(exc)[:80])
            await asyncio.sleep(min(0.05 * (2**attempt), 1.5) + random.uniform(0, 0.05))
    raise RuntimeError("host DB commit failed after lock-retry") from last_exc


@asynccontextmanager
async def async_unit_of_work(*, commit: bool = True) -> AsyncIterator[AsyncSession]:
    """Async scoped host DB session: commit (with lock-retry) on clean exit,
    rollback on exception, close always. The single place async business code
    touches the session lifecycle — no scattered ``AsyncSessionLocal()`` /
    ``commit()`` / ``close()``.
    """
    db = AsyncSessionLocal()
    try:
        yield db
        if commit:
            await async_commit_with_retry(db, where="async_unit_of_work")
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield an async session inside a unit of work.

    ``async def route(... db: AsyncSession = Depends(get_async_session))``.
    """
    async with async_unit_of_work() as db:
        yield db


__all__ = [
    "async_commit_with_retry",
    "async_unit_of_work",
    "get_async_session",
]

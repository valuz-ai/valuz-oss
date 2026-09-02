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
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from valuz_agent.infra.database import AsyncSessionLocal, new_background_sessionmaker
from valuz_agent.infra.db_urls import is_sqlite_runtime

logger = logging.getLogger(__name__)

# Sessionmaker override for code running on a FOREIGN event loop (a
# background-thread ``asyncio.run``). Unset (``None``) on the main app loop, so
# all ordinary access uses the shared ``AsyncSessionLocal``. ``background_db_scope``
# sets it to a per-loop sessionmaker; ``async_unit_of_work`` reads it each call.
_bg_sessionmaker: ContextVar[async_sessionmaker[AsyncSession] | None] = ContextVar(
    "_bg_sessionmaker", default=None
)


def _active_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return _bg_sessionmaker.get() or AsyncSessionLocal


# Lock-contention retry budget. Within one process the main-loop connection
# pool and the background daemon threads (rescan/reindex/skill-index) each open
# their own aiosqlite connection to the same ``valuz.db``; under parallel writes
# they compete for SQLite's single write slot. ``busy_timeout`` (set in
# ``database._set_async_sqlite_pragma``) waits out plain write-write contention,
# so this retry is the backstop for the cases it can't cover — chiefly the
# WAL read-snapshot→write deadlock, where a connection that opened a read
# transaction (a prior SELECT) then tries to upgrade to a write after another
# connection committed gets ``SQLITE_BUSY`` *immediately*, bypassing
# busy_timeout. Exponential backoff + jitter de-correlates competing writers.
# The backoff is ``await asyncio.sleep`` so the loop stays free.
_LOCK_RETRY_ATTEMPTS = 12


def _is_locked(exc: OperationalError) -> bool:
    return "locked" in str(exc).lower()


# ── deferred commits ──────────────────────────────────────────────────
#
# A unit of work that runs INSIDE another transaction's savepoint (an
# operation handler under ``OperationService.confirm`` → ``begin_nested()``)
# must not commit: SQLAlchemy closes the savepoint context on ``commit()``
# and every statement after it fails with "Can't operate on closed
# transaction inside context manager". The domain code it reuses (the skill
# library's save pipeline, for one) commits at several points, because it
# was written for the request path where it owns the transaction. Rather
# than threading a ``commit=`` flag through every layer, the OUTER owner
# declares the intent here and ``async_commit_with_retry`` turns each commit
# into a flush for the duration. The outer transaction commits once, at the
# end, and a failure rolls everything back together — which is exactly the
# atomicity the operation record promises.

_DEFER_COMMITS: ContextVar[bool] = ContextVar("valuz_defer_commits", default=False)


@asynccontextmanager
async def defer_commits() -> AsyncIterator[None]:
    """Within this block ``async_commit_with_retry`` flushes instead of
    committing. Task-local (a ``ContextVar``), so concurrent requests on the
    same loop are unaffected."""
    token = _DEFER_COMMITS.set(True)
    try:
        yield
    finally:
        _DEFER_COMMITS.reset(token)


def commits_deferred() -> bool:
    return _DEFER_COMMITS.get()


async def async_commit_with_retry(
    db: AsyncSession, *, where: str = "commit", attempts: int = _LOCK_RETRY_ATTEMPTS
) -> None:
    """``await db.commit()`` retrying on SQLite 'database is locked'.

    Non-blocking: the backoff is ``await asyncio.sleep`` (the loop stays free),
    and aiosqlite runs the commit itself on its own thread.

    **State-preserving across the retry.** A SQLAlchemy ``rollback()`` does not
    just abort the SQL transaction — it *expunges* pending (``add``ed) instances
    back to transient and *expires* every other persistent instance in the
    session. A naive "rollback then re-``commit()``" therefore has two silent
    failure modes under contention:

    1. **Lost INSERTs** — the expunged pending rows are no longer in the
       session, so the retried ``commit()`` persists *nothing* yet returns
       success. We defend against this by re-``add``ing the pending rows before
       each retry, so the INSERT actually re-runs.
    2. **Lost UPDATE/DELETE + stale reads** — a rollback reverts dirty rows and
       un-deletes deleted ones, and we cannot faithfully replay that mutation
       from here (the helper never saw the original values / the ``merge``).
       Silently re-committing would persist the *reverted* state and leave the
       caller reading expired instances (an implicit sync load on the
       AsyncSession → ``MissingGreenlet``). So when the rollback discarded
       UPDATE/DELETE work we **fail loud** — re-raise the lock error — rather
       than return a wrong result. The caller's transaction surfaces it instead
       of corrupting data.

    The happy path (commit succeeds first try) is unchanged: no contention, no
    rollback, no behavior difference.
    """
    if _DEFER_COMMITS.get():
        # See ``defer_commits``: an enclosing transaction owns the commit.
        await db.flush()
        return
    last_exc: Exception | None = None
    for attempt in range(attempts):
        # Snapshot the INSERTs a rollback would expunge, and note whether this
        # commit also carries UPDATE/DELETE work we can't replay. Read these
        # BEFORE committing — the rollback in the except branch clears them.
        pending_inserts = list(db.new)
        has_unreplayable = bool(db.dirty or db.deleted)
        try:
            await db.commit()
            return
        except OperationalError as exc:
            await db.rollback()
            last_exc = exc
            if not _is_locked(exc):
                raise
            logger.warning("LOCKDIAG[%s] attempt=%d err=%s", where, attempt + 1, str(exc)[:80])
            if has_unreplayable:
                # Fail loud: the rollback reverted UPDATE/DELETE work that we
                # cannot reconstruct. Surfacing the lock error is strictly
                # safer than silently persisting the reverted state.
                raise
            # Re-stage the expunged INSERTs so the next attempt re-commits them
            # instead of silently persisting an empty transaction.
            for obj in pending_inserts:
                if obj not in db:
                    db.add(obj)
            await asyncio.sleep(min(0.05 * (2**attempt), 1.5) + random.uniform(0, 0.05))
    raise RuntimeError("host DB commit failed after lock-retry") from last_exc


@asynccontextmanager
async def async_unit_of_work(*, commit: bool = True) -> AsyncIterator[AsyncSession]:
    """Async scoped host DB session: commit (with lock-retry) on clean exit,
    rollback on exception, close always. The single place async business code
    touches the session lifecycle — no scattered ``AsyncSessionLocal()`` /
    ``commit()`` / ``close()``.
    """
    db = _active_sessionmaker()()
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


@asynccontextmanager
async def background_db_scope() -> AsyncIterator[None]:
    """Bind a per-loop DB engine for the enclosed scope.

    For code running on a FOREIGN event loop — a background-thread
    ``asyncio.run`` (KB auto-discovery, docs reindex/rescan). Inside the scope,
    ``async_unit_of_work`` / ``get_async_session`` draw sessions from a private
    ``NullPool`` engine instead of the shared, main-loop-bound ``async_engine``.
    That is required under asyncpg, whose connections are bound to the loop that
    created them — driving the shared pool from another loop raises
    ``InterfaceError: another operation is in progress``. The private engine is
    disposed on exit.

    No-op on SQLite (aiosqlite is loop-agnostic): the shared engine is kept, so
    the default single-user deployment is entirely unaffected.
    """
    if is_sqlite_runtime():
        yield
        return
    engine, maker = new_background_sessionmaker()
    token = _bg_sessionmaker.set(maker)
    try:
        yield
    finally:
        _bg_sessionmaker.reset(token)
        await engine.dispose()


async def run_in_background_db_scope[T](coro: Awaitable[T]) -> T:
    """Await ``coro`` inside ``background_db_scope`` — the idiomatic wrapper for
    a background thread's ``asyncio.run``: ``asyncio.run(run_in_background_db_scope(_arun()))``."""
    async with background_db_scope():
        return await coro


__all__ = [
    "async_commit_with_retry",
    "async_unit_of_work",
    "background_db_scope",
    "get_async_session",
    "run_in_background_db_scope",
]

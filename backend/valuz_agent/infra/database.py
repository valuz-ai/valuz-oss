from uuid import uuid4

from sqlalchemy import BigInteger, NullPool, String, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
)

from valuz_agent.infra.config import settings
from valuz_agent.infra.db_urls import db_url_async, is_sqlite_runtime
from valuz_agent.infra.time_utils import now_ms


class Base(DeclarativeBase):
    pass


class PrimaryKeyMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid4().hex)


class TimestampMixin:
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    updated_at: Mapped[int] = mapped_column(
        BigInteger,
        default=now_ms,
        onupdate=now_ms,
    )


class UserMixin:
    """Row ownership — every business table carries the owner's ``user_id``.

    Required (``NOT NULL``) and stamped **explicitly** by each datastore
    ``create_*`` / facade write (which takes the caller's ``user_id`` as its
    first argument). There is deliberately NO column ``default=`` reading the
    request ContextVar: an insert that forgets to stamp fails loudly with a
    ``NOT NULL`` violation rather than being silently attributed to whatever
    owner happens to sit in context. Indexed because every owner-scoped query
    filters on it.
    """

    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


# The host is fully async: ONE aiosqlite engine for ALL data access. There is no
# synchronous *data* engine — every session/ORM path goes through
# ``AsyncSessionLocal`` / ``async_unit_of_work`` (ADR-020). Both alembic chains
# (host + kernel) also run async, each on a dedicated thread off the event loop
# (their ``alembic/env.py`` files call ``asyncio.run``).
#
# The only synchronous SQLite touch is the pair of boot-time self-heal probes
# (``boot.schema.ensure_host_schema_migratable`` / ``boot.kernel.ensure_kernel_schema_migratable``):
# both run OFF the event loop in a dedicated thread (no deadlock risk; the
# ADR-020 hazard is sync-on-loop), own no session, and read no business data.
# They are data-preserving — a DB on a known alembic revision is migrated
# forward in place; only an unknown/foreign/corrupt stamp is dropped + rebuilt.
# Non-SQLite (server PG) pools sit behind idle-timeout middleboxes (LB / k8s
# conntrack) and PG restarts: an idle pooled connection can die silently and
# the next checkout raises asyncpg "connection is closed" mid-request.
# pre_ping validates on checkout; recycle retires connections before typical
# idle-timeout windows. SQLite needs neither (in-process file handles).
_pool_kwargs: dict[str, object] = (
    {} if is_sqlite_runtime() else {"pool_pre_ping": True, "pool_recycle": 1800}
)

async_engine: AsyncEngine = create_async_engine(db_url_async(), echo=settings.debug, **_pool_kwargs)

if is_sqlite_runtime():

    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_async_sqlite_pragma(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        # busy_timeout: wait on write contention instead of raising "database
        # is locked" immediately. Within one process the main-loop connection
        # pool and the background daemon threads each open their own connection
        # to this file and compete for the single write slot. (busy_timeout
        # can't cover the WAL read-snapshot→write deadlock — that path is
        # handled by async_commit_with_retry in infra/db.py.)
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()


AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine, expire_on_commit=False
)


def new_background_sessionmaker() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """A throwaway engine + sessionmaker for code running on a FOREIGN event
    loop — a background-thread ``asyncio.run`` (KB auto-discovery, docs
    reindex/rescan). asyncpg connections are bound to the loop that created
    them, so driving the shared ``async_engine`` pool from another loop raises
    ``InterfaceError: another operation is in progress`` (and corrupts the
    on-loop pool users). Each foreign loop gets its OWN ``NullPool`` engine — no
    pooling, since the loop is short-lived — which the caller disposes when the
    loop ends (see ``infra.db.background_db_scope``). SQLite is loop-agnostic
    (aiosqlite proxies each connection through its own thread), so callers skip
    this for sqlite and keep using the shared ``AsyncSessionLocal``.
    """
    engine = create_async_engine(db_url_async(), echo=settings.debug, poolclass=NullPool)
    return engine, async_sessionmaker(bind=engine, expire_on_commit=False)

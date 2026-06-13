from uuid import uuid4

from sqlalchemy import BigInteger, String, event
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
# ``AsyncSessionLocal`` / ``async_unit_of_work`` (ADR-020). Host Alembic
# migrations + the pre-v2 wipe also run async (``host_bootstrap``; the host
# ``alembic/env.py`` mirrors the kernel's async env).
#
# The ONLY remaining synchronous SQLite touch is ``kernel_bootstrap.
# drop_stale_kernel_tables`` — a boot-time kernel-table-drift DDL probe that runs
# OFF the event loop in a dedicated thread (so it carries no deadlock risk; the
# ADR-020 hazard is sync-on-loop). It owns no session and reads no business data;
# it's a sanctioned sync island alongside the kernel's own alembic, not a host
# data-access engine.
async_engine: AsyncEngine = create_async_engine(settings.db_url_async, echo=settings.debug)

if settings.is_sqlite:

    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_async_sqlite_pragma(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        # busy_timeout: wait-and-retry on write contention instead of raising
        # "database is locked" immediately (host + kernel async engines share
        # the file).
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()


AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine, expire_on_commit=False
)

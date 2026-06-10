"""Alembic environment for the valuz host schema.

Async engine: the host is fully async (aiosqlite) — there is no sync engine
anymore. This env mirrors the kernel's async env (``create_async_engine`` +
``connection.run_sync`` + ``asyncio.run``), so ``run_host_migrations`` drives it
on a dedicated thread (the app startup hook is already on the loop). The host
and kernel share one SQLite file but each owns its own alembic chain with a
non-colliding ``version_table`` (``alembic_version_host`` here vs. the kernel's
default ``alembic_version``).

``target_metadata`` is ``valuz_agent.infra.database.Base.metadata``
which carries every host SQLAlchemy model (``valuz_*`` tables only —
the kernel-owned ``projects`` / ``agents`` / ``sessions`` / ``events``
sit on a separate ``Base`` inside ``kernel/src``).

The ``render_as_batch`` flag is required for SQLite because most DDL
shapes (column drop, type change, constraint rename) are unsupported
in-place; alembic emits a temp-table swap instead.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

# env.py lives at backend/alembic/host/; parents[2] is backend/, which makes
# ``valuz_agent`` importable when alembic runs as a standalone CLI.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncio  # noqa: E402

from sqlalchemy import pool  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

# Side-effect imports — each ``modules/*/models.py`` declares its tables
# against the shared Base. We pull them in here so ``target_metadata``
# reflects the full host schema at autogenerate time.
import valuz_agent.modules.agents.models  # noqa: F401,E402
import valuz_agent.modules.automations.models  # noqa: F401,E402
import valuz_agent.modules.connectors.models  # noqa: F401,E402
import valuz_agent.modules.docs.models  # noqa: F401,E402
import valuz_agent.modules.parser.models  # noqa: F401,E402
import valuz_agent.modules.projects.models  # noqa: F401,E402
import valuz_agent.modules.providers.models  # noqa: F401,E402
import valuz_agent.modules.sessions.models  # noqa: F401,E402
import valuz_agent.modules.settings.models  # noqa: F401,E402
import valuz_agent.modules.skills.models  # noqa: F401,E402
import valuz_agent.modules.tasks.models  # noqa: F401,E402
from alembic import context  # noqa: E402

# Importing the database module registers every ``Base``-derived model
# in ``Base.metadata`` so autogenerate sees the full host schema.
from valuz_agent.infra.database import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_url() -> str:
    """Resolve the DB URL — env override wins so the runtime bootstrap
    can point alembic at the same SQLite file the rest of the app uses
    (``~/.valuz/app/valuz.db`` by default, relocatable via
    ``VALUZ_DATA_DIR``).
    """
    return os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url", ""))


def run_migrations_offline() -> None:
    """Emit SQL without a live connection — used by ``alembic upgrade --sql``."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        version_table="alembic_version_host",
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        render_as_batch=True,
        version_table="alembic_version_host",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against a live async (aiosqlite) engine."""
    connectable = create_async_engine(get_url(), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

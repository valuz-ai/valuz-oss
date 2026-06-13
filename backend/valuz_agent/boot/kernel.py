"""Boot the Agent Harness V5 kernel inside the valuz host process.

The kernel ships under ``backend/kernel/`` with bare top-level imports
(``from src.core ...``, ``from app.config ...``). Importing the ``kernel``
package puts that directory on ``sys.path`` so those imports resolve.

This module is the only place that:
- runs the kernel's Alembic migrations against the valuz SQLite file,
- initializes the kernel's dependency singletons against the same file,
- exposes the kernel's FastAPI routers to the valuz app.

Anything else in valuz that needs the kernel goes through ``get_orchestrator``
or ``get_store`` here.

Note (kernel V5 post-MODEL_CATALOG): the kernel no longer maintains an
internal model catalog. Every kernel ``Session`` carries its own
``model_provider`` (base_url + api_key + api_protocol); the runtime
factory dispatches on ``api_protocol``. Valuz composes the provider at
session creation time from the user-selected channel + (optional) alias —
see ``valuz_agent.adapters.provider_resolver``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from valuz_agent.infra.config import settings

if TYPE_CHECKING:
    from sqlalchemy import Engine

logger = logging.getLogger(__name__)

# Triggers sys.path injection so ``from src.core...`` and ``from app.config...``
# resolve once anyone in the host imports the kernel package.
import kernel  # noqa: F401, E402  (side-effect import)

KERNEL_DIR: Path = Path(__file__).resolve().parents[2] / "kernel"
# The kernel alembic chain was moved out of the kernel tree to
# backend/alembic/kernel (sibling of the host chain at backend/alembic/host).
KERNEL_ALEMBIC_DIR: Path = Path(__file__).resolve().parents[2] / "alembic" / "kernel"
KERNEL_ALEMBIC_INI: Path = KERNEL_ALEMBIC_DIR / "alembic.ini"


def _set_kernel_env() -> None:
    """Make the kernel see the valuz database URL and a sane workspace dir.

    The kernel's ``app.config.AppConfig`` reads ``DATABASE_URL`` from
    os.environ at construction time, so we set it before anything imports
    ``app.config``.

    ``DEEPAGENTS_CHECKPOINT_DB`` points the kernel's DeepAgentsRuntime
    langgraph checkpointer at the SAME SQLite file as the rest of valuz —
    one file to back up, no stray ``./deepagents_checkpoints.db`` left
    in whatever cwd happened to be active when the runtime first booted.
    Langgraph's checkpoint tables (``checkpoints`` / ``writes`` /
    ``checkpoint_blobs``) don't collide with the kernel's
    ``sessions/messages/events`` or valuz's ``valuz_*`` namespaces;
    setdefault honours an external override.
    """
    os.environ["DATABASE_URL"] = settings.kernel_db_url_async
    os.environ.setdefault("DEEPAGENTS_CHECKPOINT_DB", str(settings.db_path))


def drop_stale_kernel_tables(engine: Engine | None = None) -> None:
    """Belt-and-braces drop trigger for kernel-shape drift.

    The kernel's Alembic chain is the only thing that's *supposed* to
    rewrite ``sessions`` / ``messages`` / ``events``, but
    historically the kernel has shipped schema changes that reuse the
    same revision id (so already-stamped DBs skip the upgrade and end
    up missing required columns). This function detects those known
    fingerprints by checking for the presence of marker columns —
    anything missing means "drop the kernel tables so the next
    ``alembic upgrade head`` rebuilds clean".

    Per dev-stage policy: no data preservation. Internal dogfood users
    accepted this trade in exchange for cleaner kernel upgrade
    semantics — see CHANGELOG entry for the V1+V2 schema bootstrap.

    Idempotent: a healthy three-table kernel passes through unchanged.

    Lives next to ``run_kernel_migrations`` so the boot sequence has
    a single import surface for "do everything the kernel needs at
    startup". Called automatically by ``run_kernel_migrations``; tests
    can pass in an ad-hoc engine to pin specific fingerprint cases.
    """
    from sqlalchemy import create_engine, inspect, text

    owns_engine = engine is None
    if engine is None:
        engine = create_engine(settings.kernel_db_url)
    try:
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())

        def _has_col(table: str, col: str) -> bool:
            if table not in existing:
                return False
            return col in {c["name"] for c in inspector.get_columns(table)}

        suspect: list[str] = []

        # De-projectization cutover: the kernel now owns exactly three
        # tables (sessions / messages / events) and sessions embed their
        # agent snapshot. Any DB still carrying the old projects/agents
        # tables — or a sessions shape without agent_config — predates the
        # cutover and must be wiped (regenerated baseline, no ALTER chain).
        if "projects" in existing or "agents" in existing:
            suspect.append("sessions")
        if "sessions" in existing and not _has_col("sessions", "agent_config"):
            if "sessions" not in suspect:
                suspect.append("sessions")
        # V4 fossils that survived the V5 cutover.
        if _has_col("sessions", "environment_id"):
            if "sessions" not in suspect:
                suspect.append("sessions")
        if _has_col("environments", "workspace_mounts"):
            suspect.append("environments")

        # Torn-state recovery: an interrupted previous boot can leave the
        # trio half-created. Drop whatever's there so the next
        # ``alembic upgrade`` rebuilds.
        kernel_tables = {"sessions", "messages", "events"} & existing
        if kernel_tables and len(kernel_tables) < 3:
            for t in kernel_tables:
                if t not in suspect:
                    suspect.append(t)

        # Cascade: if any member is stale the others are too — the kernel's
        # initial migration creates them as a unit. The legacy table names
        # (projects / agents / environments) stay in the drop list so
        # pre-cutover fossils are cleared.
        if "sessions" in suspect:
            for t in ("projects", "agents", "events", "environments", "messages"):
                if t in existing and t not in suspect:
                    suspect.append(t)
            # Also reset the kernel's alembic stamp so the upgrade
            # treats this as a fresh install.
            if "alembic_version" in existing and "alembic_version" not in suspect:
                suspect.append("alembic_version")

        if not suspect:
            return

        logger.warning(
            "Stale kernel tables detected (%s) — dropping for fresh alembic baseline",
            ", ".join(suspect),
        )
        with engine.begin() as conn:
            for table in suspect:
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
    finally:
        if owns_engine:
            engine.dispose()


def _do_alembic_upgrade() -> None:
    _set_kernel_env()

    from alembic.config import Config

    from alembic import command

    cfg = Config(str(KERNEL_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(KERNEL_ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", settings.db_url_async)

    command.upgrade(cfg, "head")


def run_kernel_migrations() -> None:
    """Apply the kernel's Alembic migrations to the valuz SQLite file.

    Two steps under one entry point:

    1. ``_drop_stale_kernel_tables`` — safety net for kernel shape drift
       (see its docstring). No-op on healthy DBs.
    2. The kernel's own alembic ``upgrade head``. Writes its revision
       into the default ``alembic_version`` table; the host's chain
       uses a separate ``alembic_version_host`` row in the same file
       so the two don't collide.

    Always runs in a dedicated thread because the kernel's
    ``alembic/env.py`` calls ``asyncio.run()`` to drive its async
    migrations, and that fails if the calling thread already has a
    running event loop — which is the case for FastAPI/Starlette
    ``on_event("startup")`` and any test using ``TestClient``.
    Spawning a thread keeps the kernel migration code unchanged and
    the host code obvious at the call site.
    """
    import threading

    drop_stale_kernel_tables()

    error: list[BaseException] = []

    def _runner() -> None:
        try:
            _do_alembic_upgrade()
        except BaseException as exc:  # noqa: BLE001 — re-raised on the main thread
            error.append(exc)

    thread = threading.Thread(target=_runner, name="kernel-alembic-upgrade", daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]


async def init_kernel_dependencies() -> None:
    """Initialize the kernel's engine/session/store/orchestrator singletons.

    Mirrors ``app.dependencies.init_dependencies`` but drives it from valuz
    settings instead of the kernel's own AppConfig defaults.
    """
    _set_kernel_env()
    import app.dependencies as kernel_deps
    from app.config import AppConfig
    from app.dependencies import init_dependencies

    await init_dependencies(AppConfig())

    # No kernel-side owner default to seed: every kernel write stamps ``user_id``
    # explicitly (host → kernel_client → route → store), so there is nothing to
    # fall back to. Reads/writes that reach the kernel always carry an owner.

    # The kernel's engine factory (kernel/src/adapters/sqlalchemy_store/engine.py)
    # sets journal_mode=WAL but NOT busy_timeout, so kernel connections run with
    # SQLite's default busy_timeout=0. The kernel is the highest-frequency writer
    # during a turn (every coalesced event delta), so with timeout 0 it raises
    # "database is locked" *instantly* the moment the host's sync engine holds the
    # write lock — no wait, no retry. The host engine was hardened to 15s
    # (infra/database) but this kernel half of the SAME file was not, which is the
    # real source of the dispatch/scheduler lock storms. Attach the missing PRAGMA
    # to the kernel engine here (at the host seam), then dispose the pool so live
    # connections reconnect with it. The tidier home is the kernel's engine
    # factory — fold busy_timeout in there when next touching it.
    if settings.is_sqlite and getattr(kernel_deps, "_engine", None) is not None:
        from sqlalchemy import event as _sa_event

        kernel_engine = kernel_deps._engine

        @_sa_event.listens_for(kernel_engine.sync_engine, "connect")
        def _kernel_busy_timeout(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        await kernel_engine.dispose()


async def shutdown_kernel_dependencies() -> None:
    from app.dependencies import shutdown_dependencies

    await shutdown_dependencies()


def get_kernel_routers() -> list:
    """Return the kernel's FastAPI routers in the order they should be mounted.

    Note: ``GET /api/v1/models`` was removed from the kernel along with the
    MODEL_CATALOG drop — runtime dispatch is now per-session protocol-driven,
    so there's no curated list to expose. Valuz surfaces models through its
    own ``/v1/channels`` API instead.

    Kernel V5+messages adds a ``messages`` router exposing
    ``GET /api/v1/sessions/{id}/messages`` /
    ``GET /api/v1/messages/{id}`` /
    ``GET /api/v1/messages/{id}/events`` so the frontend can read per-turn
    history (one row per ``run_turn``, with usage + todo snapshots).

    Per ADR-008 the kernel's ``app.routes.agents`` is *not* mounted here.
    Valuz keeps a private synthetic agent per project
    (``agent-<project_id>``); exposing the kernel CRUD surface would
    leak those rows to any frontend listing them, and we have no
    user-facing agent gallery yet. If/when product introduces agent
    presets, this decision is revisited in a new ADR.
    """
    from app.routes.events import router as events_router
    from app.routes.messages import router as messages_router
    from app.routes.run import router as run_router
    from app.routes.sessions import router as sessions_router
    from app.routes.usage import router as usage_router

    return [sessions_router, messages_router, run_router, events_router, usage_router]

"""Host-side schema bootstrap — single-baseline ("0-migration") policy.

The host owns its own alembic chain at ``backend/alembic/host`` with a
non-default ``version_table = alembic_version_host`` so it does NOT
collide with the kernel's ``alembic_version`` row in the same SQLite
file.

The chain holds exactly ONE revision: the 0001 baseline that creates the
whole host schema. Pre-launch there are no upgrade migrations — schema
changes regenerate 0001, and any DB not stamped at it is dropped wholesale
and re-initialized (see ``drop_stale_host_tables``). ``run_host_migrations``
then runs ``alembic upgrade head``, which on an empty/clean DB just lays
down the baseline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Engine

logger = logging.getLogger(__name__)

# Module-relative paths so the bootstrap works regardless of CWD.
# schema.py is at backend/valuz_agent/boot/; parents[2] is backend/, and the
# host alembic chain now lives at backend/alembic/host (moved out of the package).
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_DIR = _BACKEND_ROOT / "alembic" / "host"
ALEMBIC_INI = ALEMBIC_DIR / "alembic.ini"
VERSION_TABLE = "alembic_version_host"

# The one and only host revision. If a baseline regeneration ever changes the
# schema shape, bump this id together with the migration's ``revision`` so
# DBs stamped at the previous baseline fail the equality check below and get
# rebuilt — the stamp is the single source of truth, no schema inspection.
# Bumped 0001 → 0002 for the per-user composite PKs on the semantic-key tables
# (valuz_app_setting / valuz_shortcut_binding / valuz_onboarding_state /
# valuz_setup_job key on ``(<semantic_key>, user_id)``). DBs stamped at the
# previous baseline are dropped + rebuilt (0-migration policy).
BASELINE_REVISION = "0002"


def drop_stale_host_tables(engine: Engine | None = None) -> None:
    """0-migration reset probe — host counterpart to
    ``boot.kernel.drop_stale_kernel_tables``.

    The host ships exactly one alembic revision (``BASELINE_REVISION``); there
    is no upgrade path and, pre-launch, no data-preservation contract. The
    check is a single stamp comparison — no schema fingerprinting:

    - stamped at the baseline → trust it, no-op;
    - anything else (an older multi-revision chain, a missing/empty stamp from
      a boot that died mid-initialization, a future id from another branch) →
      drop every ``valuz_*`` table plus the ``alembic_version_host`` stamp, so
      the following ``run_host_migrations`` re-initializes the whole host
      schema cleanly from the baseline.

    No-op on a fresh file. Runs synchronously off the event loop — it owns no
    session and reads no business data, like the kernel probe.
    """
    from sqlalchemy import create_engine, inspect, text

    from valuz_agent.infra.config import settings

    owns_engine = engine is None
    if engine is None:
        engine = create_engine(settings.db_url)
    try:
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())

        stamp: str | None = None
        if VERSION_TABLE in existing:
            with engine.connect() as conn:
                row = conn.execute(
                    text(f"SELECT version_num FROM {VERSION_TABLE}")  # noqa: S608
                ).fetchone()
                stamp = row[0] if row else None

        if stamp == BASELINE_REVISION:
            return  # already on the current baseline

        stale = sorted(t for t in existing if t.startswith("valuz_"))
        if VERSION_TABLE in existing:
            stale.append(VERSION_TABLE)
        if not stale:
            return  # fresh install — nothing to reset

        logger.warning(
            "host schema is not on baseline %s (stamp=%s) — "
            "dropping %d host table(s) for a clean re-initialization",
            BASELINE_REVISION,
            stamp,
            len(stale),
        )
        with engine.begin() as conn:
            for table in stale:
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
    finally:
        if owns_engine:
            engine.dispose()


def run_host_migrations() -> None:
    """Run host ``alembic upgrade head`` against the async (aiosqlite) DB URL.

    The host alembic ``env.py`` is async (``asyncio.run``), so — like
    ``run_kernel_migrations`` — this runs in a dedicated thread: the app startup
    hook is already on the event loop, and a nested ``asyncio.run`` there would
    raise. ``DATABASE_URL`` is set to ``settings.db_url_async`` so ``env.py``'s
    ``get_url()`` picks up the same SQLite file the rest of the host talks to,
    then restored on exit.
    """
    import os
    import threading

    from valuz_agent.infra.config import settings

    db_url = settings.db_url_async

    def _do() -> None:
        from alembic.config import Config

        from alembic import command

        # Reset any DB not stamped at the current baseline before upgrading so
        # the schema rebuilds clean (runs here, off the event loop, in the
        # same dedicated thread as the upgrade).
        drop_stale_host_tables()

        cfg = Config(str(ALEMBIC_INI))
        cfg.set_main_option("script_location", str(ALEMBIC_DIR))
        cfg.set_main_option("sqlalchemy.url", db_url)
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = db_url
        try:
            command.upgrade(cfg, "head")
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous

    error: list[BaseException] = []

    def _runner() -> None:
        try:
            _do()
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller thread
            error.append(exc)

    thread = threading.Thread(target=_runner, name="host-alembic-upgrade", daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]


__all__ = ["run_host_migrations", "drop_stale_host_tables", "VERSION_TABLE", "BASELINE_REVISION"]

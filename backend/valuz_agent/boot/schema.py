"""Host-side schema bootstrap — alembic upgrade head.

The host owns its own alembic chain at ``backend/alembic/host`` with a
non-default ``version_table = alembic_version_host`` so it does NOT
collide with the kernel's ``alembic_version`` row in the same SQLite
file.

``run_host_migrations`` runs ``alembic upgrade head`` against the host
chain: on a fresh install it lays down the baseline schema; on later
boots it applies any new revisions. Reversible per alembic semantics.
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

# Marker table whose ``user_id`` column signals a post-ownership host schema.
# Its ABSENCE on an otherwise-populated host DB identifies a pre-``user_id``
# install that must be wiped + rebuilt (see ``drop_stale_host_tables``).
_OWNERSHIP_MARKER_TABLE = "valuz_provider"
_OWNERSHIP_MARKER_COLUMN = "user_id"

# Marker table for the workspace→project naming cutover: the host baseline was
# regenerated with ``valuz_project`` (and ``valuz_workspace_context`` folded
# into it), so the presence of the old ``valuz_workspace`` table identifies a
# pre-rename install that must be wiped + rebuilt.
_RENAME_MARKER_TABLE = "valuz_workspace"


def _known_revisions() -> set[str]:
    """Revision ids present in the current host alembic chain.

    Pre-release the chain is folded into its baseline whenever the schema
    changes (clean-up policy, no incremental migrations). A dev DB stamped
    with a since-folded revision id would make ``upgrade head`` fail with
    "Can't locate revision" — ``drop_stale_host_tables`` uses this set to
    detect those stamps and wipe instead.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    script = ScriptDirectory.from_config(cfg)
    return {rev.revision for rev in script.walk_revisions()}


def drop_stale_host_tables(engine: Engine | None = None) -> None:
    """Clean-up probe for the ``user_id`` ownership cutover — host counterpart
    to ``boot.kernel.drop_stale_kernel_tables``.

    Per dev-stage policy (no data preservation), the ``user_id`` column was
    added by **regenerating** the host baseline rather than shipping an ALTER
    migration. A host DB created before the cutover therefore has the old
    column-less tables and an ``alembic_version_host`` stamp that already points
    at the head — so a plain ``upgrade head`` would be a no-op and the column
    would never appear.

    This probe detects that fingerprint (the marker table exists but lacks
    ``user_id``) and drops every ``valuz_*`` table plus the
    ``alembic_version_host`` stamp, so the following ``run_host_migrations``
    rebuilds the whole host schema from the baseline.

    No-op on a fresh install (marker table absent) and on an already-migrated DB
    (marker column present). Runs synchronously off the event loop — it owns no
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

        reason: str | None = None
        if _RENAME_MARKER_TABLE in existing:
            # workspace→project naming cutover: the baseline was regenerated
            # with ``valuz_project`` (context table folded in); any DB still
            # carrying ``valuz_workspace`` predates the cutover.
            reason = f"pre-rename host schema detected ({_RENAME_MARKER_TABLE} exists)"
        elif _OWNERSHIP_MARKER_TABLE in existing:
            marker_cols = {c["name"] for c in inspector.get_columns(_OWNERSHIP_MARKER_TABLE)}
            if _OWNERSHIP_MARKER_COLUMN not in marker_cols:
                reason = (
                    f"pre-user_id host schema detected "
                    f"({_OWNERSHIP_MARKER_TABLE} lacks {_OWNERSHIP_MARKER_COLUMN})"
                )

        if reason is None and VERSION_TABLE in existing:
            # Folded-revision stamp: the chain collapses into its baseline
            # pre-release, so a DB stamped with a revision id that no longer
            # exists (e.g. built mid-branch by a since-folded incremental)
            # would crash ``upgrade head`` with "Can't locate revision".
            with engine.connect() as conn:
                stamped = conn.execute(
                    text(f"SELECT version_num FROM {VERSION_TABLE}")  # noqa: S608
                ).scalar()
            if stamped and stamped not in _known_revisions():
                reason = f"host DB stamped at unknown revision {stamped!r} (since folded)"

        if reason is None:
            return  # fresh install or already on the current baseline

        stale = sorted(t for t in existing if t.startswith("valuz_"))
        if VERSION_TABLE in existing:
            stale.append(VERSION_TABLE)

        logger.warning(
            "%s — dropping %d host table(s) for a fresh baseline rebuild",
            reason,
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

        # Wipe a pre-``user_id`` host schema before upgrading so the regenerated
        # baseline rebuilds clean (runs here, off the event loop, in the same
        # dedicated thread as the upgrade).
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


__all__ = ["run_host_migrations", "drop_stale_host_tables", "VERSION_TABLE"]

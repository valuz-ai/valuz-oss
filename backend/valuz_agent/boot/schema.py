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

logger = logging.getLogger(__name__)

# Module-relative paths so the bootstrap works regardless of CWD.
# schema.py is at backend/valuz_agent/boot/; parents[2] is backend/, and the
# host alembic chain now lives at backend/alembic/host (moved out of the package).
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_DIR = _BACKEND_ROOT / "alembic" / "host"
ALEMBIC_INI = ALEMBIC_DIR / "alembic.ini"
VERSION_TABLE = "alembic_version_host"


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
        from alembic import command
        from alembic.config import Config

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


__all__ = ["run_host_migrations", "VERSION_TABLE"]

"""Boot step: apply a staged backup restore (docs/design/client-local-backup.md §8).

MUST run before any engine opens the SQLite files (and before
``ensure_local_identity`` caches the owner id — a restore replaces
``installation.json``). The heavy lifting lives in
``modules/backup/restore.py``; this wrapper only resolves paths and guards
the "nothing pending" fast path.
"""

from __future__ import annotations

import logging

from valuz_agent.infra.fs_registry import fs_registry

logger = logging.getLogger(__name__)


def apply_pending_backup_restore() -> None:
    from valuz_agent.infra.local_identity import resolve_local_user_id
    from valuz_agent.modules.backup.restore import apply_pending_restore

    user_id = resolve_local_user_id()
    pointer = fs_registry.backup_restore_pending_file(user_id)
    if not pointer.is_file():
        return
    logger.info("pending backup restore found — applying before engines open")
    report = apply_pending_restore(
        pointer, fs_registry.backup_restore_result_file(user_id), user_id
    )
    if report and report.get("ok"):
        logger.info("backup restore applied: %s", report.get("version_id"))
    elif report:
        logger.error("backup restore FAILED: %s", report.get("error"))

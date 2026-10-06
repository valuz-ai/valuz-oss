"""Start-up housekeeping for ``extensions/`` (docs plugin-development/04 §5).

After an update the superseded version directory is kept until the next start;
this removes it, along with the version directories of dev-linked plugins (those
read their linked directory) and scratch trees (over an hour old) an interrupted
install left behind.
A directory of a plugin that is not in ``installed.json`` is NEVER removed here: a
lost or unreadable registry must not cost the user their packages.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.third_party.store import installed_store

logger = logging.getLogger(__name__)

TMP_DIRNAME = ".tmp"
#: Scratch trees younger than this may belong to an install running right now.
TMP_MAX_AGE_S = 3600


def cleanup_superseded_versions() -> list[Path]:
    """Remove stale version / scratch directories; returns what was removed."""
    root = fs_registry.extensions_root()
    removed: list[Path] = []
    state = installed_store.read()
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name == TMP_DIRNAME:
            cutoff = time.time() - TMP_MAX_AGE_S
            targets = [c for c in child.iterdir() if c.stat().st_mtime < cutoff]
        else:
            entry = state["plugins"].get(child.name)
            if entry is None:
                continue
            keep = None if entry.get("dev_path") else str(entry.get("version"))
            targets = [c for c in child.iterdir() if c.is_dir() and c.name != keep]
        for target in targets:
            shutil.rmtree(target, ignore_errors=True)
            removed.append(target)
    if removed:
        logger.info("removed %d superseded plugin directories", len(removed))
    return removed


__all__ = ["cleanup_superseded_versions"]

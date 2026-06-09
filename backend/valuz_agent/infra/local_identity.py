"""Local install identity — the OSS owner id.

OSS is single-tenant: every row is owned by one local user. That owner id is a
stable string generated **once** on first install from a device fingerprint,
then persisted to ``~/.valuz/app/installation.json`` so it survives both process
restarts and DB clean-up rebuilds (the file lives outside the business tables on
purpose — see ``infra.config.installation_file``).

The commercial edition never calls this: it overrides identity resolution via
``set_identity_resolver()`` and supplies its own per-request ``user_id`` from the
logged-in user. This module is the OSS default source for that same string id.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import uuid
from functools import lru_cache

from valuz_agent.infra.config import settings
from valuz_agent.infra.time_utils import now_ms

logger = logging.getLogger(__name__)

# Prefix echoes the historical "local-user" principal so the id reads clearly in
# logs / billing / event payloads while staying device-unique.
_LOCAL_ID_PREFIX = "local-"


def _device_fingerprint() -> str:
    """Stable per-device signal.

    Combines the primary interface MAC (``uuid.getnode()``) with the host node
    name. Neither is guaranteed globally unique, but their pair is stable on a
    given machine, which is all OSS needs — the value is persisted on first run,
    so later fingerprint drift (e.g. a NIC change) does not move the id.
    """
    node = uuid.getnode()
    name = platform.node() or ""
    return f"{node:012x}:{name}"


def _fingerprint_to_user_id(fingerprint: str) -> str:
    """Fold a fingerprint into a stable, device-unique owner id string."""
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    return f"{_LOCAL_ID_PREFIX}{digest[:16]}"


@lru_cache(maxsize=1)
def resolve_local_user_id() -> str:
    """Return the persisted OSS owner id, generating it on first call.

    Reads ``settings.installation_file``; if absent (or unreadable / malformed),
    derives the id from the device fingerprint and writes the file. Cached for
    the process lifetime.
    """
    path = settings.installation_file

    existing = _read_installation_file()
    if existing is not None:
        return existing

    fingerprint = _device_fingerprint()
    user_id = _fingerprint_to_user_id(fingerprint)
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "user_id": user_id,
                    "fingerprint": fingerprint,
                    "created_at_ms": now_ms(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("Assigned local install user_id=%s (first run)", user_id)
    except OSError:
        # Persisting is best-effort: the id is deterministic from the
        # fingerprint, so a failed write just means we regenerate next boot.
        logger.exception("Failed to persist installation file at %s", path)
    return user_id


def _read_installation_file() -> str | None:
    """Return the stored ``user_id`` if the file exists and is valid."""
    path = settings.installation_file
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        user_id = data["user_id"]
    except (OSError, KeyError, json.JSONDecodeError):
        logger.warning("Malformed installation file at %s — regenerating", path)
        return None
    if isinstance(user_id, str) and user_id:
        return user_id
    logger.warning("Invalid user_id in %s — regenerating", path)
    return None


__all__ = ["resolve_local_user_id"]

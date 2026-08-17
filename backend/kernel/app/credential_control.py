"""Credential refresh control plane — ``POST /internal/credentials/refresh``.

The DataService bearer is a short-lived JWT. When it expires the sandbox's
dual-write to the host 401s, and since the local sqlite is the runtime
authority, nothing surfaces to the user — the durable mirror just stops. The
host therefore has to hand the kernel a new one before that happens.

Everything except this endpoint already existed:

- the host writes the new value into the same file the config gate reads
  (``KERNEL_CONFIG_FILE``, default ``/run/valuz/env``), so a kernel that DOES
  restart later still comes up with the current credential — the file stays the
  single source of truth;
- ``dependencies.set_data_api_token`` swaps the live value, and the store's
  hook resolves it per request.

The missing piece was a way to run those two steps *inside* the process. A file
write alone cannot do it: one process cannot change another's ``os.environ``,
and nothing here re-reads the file after startup. So the host writes, then
calls this.

**Only the rotatable keys are applied.** Re-applying the whole file would give
the process a fresh ``os.environ`` while every component still holds the values
it captured at startup — a half-applied config masquerading as a reload. This
endpoint promises exactly one thing: the credential is current.

Mounted only when the config gate is enabled (i.e. a host manages this kernel's
env), so a standalone kernel never exposes it. The app's bearer-token
middleware gates it like every other route.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app import config_gate
from app.dependencies import set_data_api_token
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/credentials", tags=["credentials"])

# The only keys this endpoint will apply. Adding one here is a promise that the
# value is read at USE time somewhere — a key whose consumers captured it at
# startup would be updated in ``os.environ`` and nowhere else, which is the
# half-applied state the module docstring rejects.
ROTATABLE_KEYS = ("VALUZ_DATA_API_TOKEN",)


def _read_rotatable(path: str) -> dict[str, str]:
    """Rotatable keys present in the config file; ``{}`` when it is unreadable.

    Unreadable is not an error the caller can act on differently from "no
    rotatable keys in it", and raising here would turn a transient read into a
    500 on a refresh the host will retry anyway.
    """
    try:
        text = Path(path).read_text()
    except OSError as exc:
        logger.warning("credential refresh: cannot read %s: %s", path, exc)
        return {}
    parsed = config_gate.parse_env_lines(text)
    return {k: v for k, v in parsed.items() if k in ROTATABLE_KEYS}


@router.post("/refresh")
async def refresh() -> dict[str, object]:
    """Re-read the config file and apply its rotatable keys to this process.

    Returns which keys were applied and whether the DataService credential
    actually changed — never the values themselves, so the response is safe to
    log on the host side.
    """
    path = config_gate.config_file_path()
    found = _read_rotatable(path)
    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"no rotatable keys in {path} (looked for {', '.join(ROTATABLE_KEYS)})",
        )
    previous = os.environ.get("VALUZ_DATA_API_TOKEN")
    os.environ.update(found)
    token = found.get("VALUZ_DATA_API_TOKEN")
    changed = token is not None and token != previous
    if token is not None:
        set_data_api_token(token)
    logger.info(
        "credential refresh: applied %s from %s (data-api credential %s)",
        ",".join(sorted(found)),
        path,
        "rotated" if changed else "unchanged",
    )
    return {"applied": sorted(found), "rotated": changed}


def should_mount() -> bool:
    """True iff a host manages this kernel's env through the config gate."""
    return config_gate.gate_enabled()

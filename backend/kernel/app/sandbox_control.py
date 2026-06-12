"""Sandbox self-extension control plane — ``/internal/sandbox/{grant,revoke}``.

When the kernel runs inside a long-lived macOS Seatbelt sandbox, its file
access is fixed at launch by the ``sandbox-exec`` profile. A project bound to
an external folder AFTER the kernel is already serving would otherwise be
unreachable without restarting the whole sandbox.

macOS *sandbox extensions* solve this without a restart: the profile
pre-declares that it will honour extension tokens of a class
(``(allow file-read* file-write* (extension "com.apple.app-sandbox.read-write"))``),
the privileged host *issues* a path-bound token, and this endpoint
*consumes* it inside the running kernel process — extending the live
sandbox. Crucially, the grant is inherited by every child the runtime
forks (the codex/claude CLIs), so the agent subtree gets the path too.

This module is deliberately **self-contained** (only ``ctypes`` + stdlib +
FastAPI): the kernel must not learn about the host. The functions are SPI
in ``libsystem_sandbox`` (no public header) but have been stable for the
life of the macOS sandbox and underpin Apple's powerbox and
Chromium/WebKit renderer file access. Off macOS (or if the SPI is absent)
the endpoint answers ``501`` rather than crashing.

Mounted only when ``KERNEL_SANDBOX_CONTROL=1`` (set by the Seatbelt
provider when it spawns the kernel), so a vanilla standalone kernel never
exposes it. The standalone bearer-token middleware still gates it.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/internal/sandbox", tags=["sandbox"])


class _Spi:
    """Lazy ctypes binding to the ``sandbox_extension_*`` SPI.

    Loaded once on first use. ``available`` is False off macOS or if the
    symbols can't be resolved, so callers degrade to a clean 501.
    """

    _loaded = False
    available = False
    _consume: Any = None
    _release: Any = None

    @classmethod
    def load(cls) -> None:
        if cls._loaded:
            return
        cls._loaded = True
        if sys.platform != "darwin":
            return
        try:
            lib = ctypes.CDLL(None)  # libsystem_sandbox is in every process
            consume = lib.sandbox_extension_consume
            consume.restype = ctypes.c_int64
            consume.argtypes = [ctypes.c_char_p]
            release = lib.sandbox_extension_release
            release.restype = ctypes.c_int
            release.argtypes = [ctypes.c_int64]
            cls._consume, cls._release = consume, release
            cls.available = True
        except (OSError, AttributeError):
            cls.available = False

    @classmethod
    def consume(cls, token: str) -> int:
        cls.load()
        if not cls.available:
            raise HTTPException(status_code=501, detail="sandbox extensions unavailable")
        handle = int(cls._consume(token.encode()))
        if handle < 0:
            # -1 = the token didn't validate (wrong class, tampered, or the
            # profile didn't pre-declare the extension class).
            raise HTTPException(status_code=400, detail="extension token rejected")
        return handle

    @classmethod
    def release(cls, handle: int) -> int:
        cls.load()
        if not cls.available:
            raise HTTPException(status_code=501, detail="sandbox extensions unavailable")
        return int(cls._release(ctypes.c_int64(handle)))


class GrantRequest(BaseModel):
    token: str
    """The opaque extension token issued by the host for ``path``."""
    path: str = ""
    """The host path the token grants — for logging/observability only; the
    token itself is path-bound, so this is not trusted for access."""
    mode: str = "rw"


class RevokeRequest(BaseModel):
    handle: int


@router.post("/grant")
async def grant(req: GrantRequest) -> dict[str, Any]:
    """Consume a host-issued extension token, extending this live sandbox.

    Returns the kernel-side ``handle`` the host stores and later passes to
    ``/revoke``. The grant is inherited by forked agent subprocesses.
    """
    handle = _Spi.consume(req.token)
    return {"data": {"handle": handle, "path": req.path, "mode": req.mode}}


@router.post("/revoke")
async def revoke(req: RevokeRequest) -> dict[str, Any]:
    """Release a prior grant, revoking the live access. Idempotent-ish: a
    stale handle just returns a non-zero rc, never raises."""
    rc = _Spi.release(req.handle)
    return {"data": {"released": rc == 0, "rc": rc}}


def should_mount() -> bool:
    """True iff the provider asked for the control plane (sandbox mode)."""
    return os.getenv("KERNEL_SANDBOX_CONTROL") == "1"

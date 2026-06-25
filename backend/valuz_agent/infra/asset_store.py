"""Owner-scoped asset store — the unified storage substrate for all
host-domain "store it / read it" data (credentials, uploaded files, derived
blobs).

It is an **object store with a file view**: ``key -> bytes`` plus a
local-fetch bridge (``fetch`` / ``fetch_group``) for consumers that need a
filesystem ``Path`` (the parser reads a file, the kernel materializes a skill
directory). It is deliberately **NOT a filesystem** — no rename / partial
write / flock. Those POSIX semantics belong to the per-project *workspace*
(a real volume mounted into the sandbox), not here. See
``docs/design/shared-backend-storage.md``.

OSS binds the local default (``LocalAssetStore``); a commercial overlay swaps
in an S3-backed store (keyed by ``user_id/key`` + encryption at rest) for the
shared multi-process backend via ``ext.asset_store``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class AssetStore(Protocol):
    """Owner-scoped object store. Every op carries the owning ``user_id`` so a
    shared backend isolates per owner (the same id that scopes business rows);
    single-user implementations may ignore it."""

    def put(self, user_id: str, key: str, data: bytes) -> None: ...
    def get(self, user_id: str, key: str) -> bytes | None: ...
    def delete(self, user_id: str, key: str) -> None: ...
    def exists(self, user_id: str, key: str) -> bool: ...
    def list(self, user_id: str, prefix: str) -> list[str]: ...
    # Local-fetch bridge — returns a real Path for consumers that read files.
    def fetch(self, user_id: str, key: str) -> Path | None: ...
    def fetch_group(self, user_id: str, prefix: str) -> Path: ...


class LocalAssetStore:
    """Filesystem-backed ``AssetStore`` for the single-user desktop build.

    ``base`` is the existing ``data_dir`` and ``key`` is the relative path
    under it (``secrets/{ref}``, ``attachments/{session}/…``, …) — keeping the
    on-disk layout unchanged (no migration) and making ``fetch`` zero-copy
    (local IS the source of truth). ``user_id`` is accepted for ``AssetStore``
    parity but NOT used for isolation: a desktop install serves exactly one
    user. Owner isolation for a shared backend is the overlay store's job.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir

    def _path(self, key: str) -> Path:
        # ``key`` is a relative posix path under base; reject traversal.
        rel = os.path.normpath(key).lstrip("/")
        if rel.startswith("..") or os.path.isabs(rel):
            raise ValueError(f"invalid asset key: {key!r}")
        return self._base / rel

    def put(self, user_id: str, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, user_id: str, key: str) -> bytes | None:
        p = self._path(key)
        return p.read_bytes() if p.is_file() else None

    def delete(self, user_id: str, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            p.unlink()

    def exists(self, user_id: str, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, user_id: str, prefix: str) -> list[str]:
        root = self._path(prefix)
        if not root.is_dir():
            return []
        return sorted(p.relative_to(self._base).as_posix() for p in root.rglob("*") if p.is_file())

    def fetch(self, user_id: str, key: str) -> Path | None:
        # Local IS the truth → zero-copy, hand back the real path.
        p = self._path(key)
        return p if p.is_file() else None

    def fetch_group(self, user_id: str, prefix: str) -> Path:
        # The directory itself is the local materialisation.
        p = self._path(prefix)
        p.mkdir(parents=True, exist_ok=True)
        return p

"""On-disk backup layout + manifest/summary formats.

Layout (docs/design/client-local-backup.md §3):

    <destination>/
    ├── restore-pending/            # staged restore payload marker (unused v1)
    └── versions/
        └── 20260716-093000/
            ├── manifest.json       # THE fact record for the version
            ├── summary.json        # business-level counts for the UI
            ├── db/valuz.db         # VACUUM INTO product
            ├── db/kernel.db        # VACUUM INTO product
            └── data/…              # file categories, mirrored rel-paths

A version directory without ``manifest.json`` is garbage (an interrupted
backup) — writers create ``manifest.json.partial`` first and atomically rename
on completion. The destination is self-describing: listing versions is a disk
scan; no database table exists (restore must work with a broken app DB).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from valuz_agent.modules.backup.schemas import BackupScope, BackupSummaryCounts

VERSIONS_SUBDIR = "versions"
MANIFEST_NAME = "manifest.json"
MANIFEST_PARTIAL_NAME = "manifest.json.partial"
SUMMARY_NAME = "summary.json"
DB_SUBDIR = "db"
DATA_SUBDIR = "data"

MANIFEST_FORMAT = 1


class ManifestFile(BaseModel):
    """One backed-up file. ``path`` is version-relative ("db/valuz.db",
    "data/memories/MEMORY.md"), always "/"-separated."""

    path: str
    size: int
    mtime_ms: int
    sha256: str | None = None
    # Symlinks are recorded (target string), never followed.
    link: str | None = None


class RestoreTarget(BaseModel):
    """Maps a category subtree inside ``data/`` back to its absolute origin.

    Recorded at backup time so boot-time restore is a dumb, generic loop —
    it needs no knowledge of FsRegistry or scope semantics."""

    rel: str  # version-relative dir or file under data/ (or "db/valuz.db")
    target: str  # absolute path it came from / restores to


class BackupManifest(BaseModel):
    format: int = MANIFEST_FORMAT
    version_id: str
    created_at: int  # epoch ms
    kind: str = "manual"  # manual | scheduled | pre_restore
    app_version: str | None = None
    host_alembic: str | None = None
    kernel_alembic: str | None = None
    scope: BackupScope = Field(default_factory=BackupScope)
    dedup: str = "hardlink"  # hardlink | none
    total_bytes: int = 0
    new_bytes: int = 0
    file_count: int = 0
    duration_ms: int = 0
    # Cheap change detector — hash over every source file's (path, size,
    # mtime). When the next run computes the same value, nothing moved and
    # the run is skipped instead of minting an identical version.
    source_fingerprint: str | None = None
    restore_targets: list[RestoreTarget] = Field(default_factory=list)
    files: list[ManifestFile] = Field(default_factory=list)


class BackupSummary(BaseModel):
    counts: BackupSummaryCounts = Field(default_factory=BackupSummaryCounts)
    recent_session_titles: list[str] = Field(default_factory=list)
    # KB documents are indexed in-place (never copied) — record their source
    # paths + whether each still existed at backup time so a restore can warn
    # about docs whose originals have moved.
    kb_sources: list[dict[str, Any]] = Field(default_factory=list)
    kb_source_missing: int = 0


def versions_dir(destination: Path) -> Path:
    return destination / VERSIONS_SUBDIR


def version_dir(destination: Path, version_id: str) -> Path:
    if "/" in version_id or "\\" in version_id or ".." in version_id:
        raise ValueError(f"invalid version id: {version_id!r}")
    return versions_dir(destination) / version_id


def write_manifest(vdir: Path, manifest: BackupManifest) -> None:
    """Write-partial-then-rename so a version is either complete or garbage."""
    partial = vdir / MANIFEST_PARTIAL_NAME
    partial.write_text(manifest.model_dump_json(), encoding="utf-8")
    partial.replace(vdir / MANIFEST_NAME)


def write_summary(vdir: Path, summary: BackupSummary) -> None:
    (vdir / SUMMARY_NAME).write_text(summary.model_dump_json(), encoding="utf-8")


def load_manifest(vdir: Path) -> BackupManifest | None:
    path = vdir / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        return BackupManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_summary(vdir: Path) -> BackupSummary:
    path = vdir / SUMMARY_NAME
    if not path.is_file():
        return BackupSummary()
    try:
        return BackupSummary.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BackupSummary()


def scan_versions(destination: Path) -> list[tuple[Path, BackupManifest]]:
    """All complete versions, newest first. Incomplete dirs are skipped
    (cleanup is the engine's job, not the reader's)."""
    root = versions_dir(destination)
    if not root.is_dir():
        return []
    out: list[tuple[Path, BackupManifest]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        manifest = load_manifest(child)
        if manifest is not None:
            out.append((child, manifest))
    out.sort(key=lambda item: item[1].created_at, reverse=True)
    return out


def remove_incomplete_versions(destination: Path, *, keep: Path | None = None) -> int:
    """Delete version dirs left behind by interrupted backups."""
    root = versions_dir(destination)
    if not root.is_dir():
        return 0
    removed = 0
    for child in root.iterdir():
        if not child.is_dir() or (keep is not None and child == keep):
            continue
        if not (child / MANIFEST_NAME).is_file():
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed


def load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None

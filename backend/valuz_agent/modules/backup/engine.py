"""Backup engine — synchronous snapshot code, always run OFF the event loop
(``asyncio.to_thread`` from the service, or the scheduler's own thread).

Mechanics (docs/design/client-local-backup.md §4):

- SQLite snapshots via ``VACUUM INTO`` on a dedicated short-lived connection —
  the host/kernel engines keep the files WAL-hot, so a plain file copy would
  tear. ``VACUUM INTO`` yields a transactionally-consistent, WAL-free,
  compacted single file per DB.
- File snapshots walk each source root; files unchanged since the previous
  version (same size + mtime) are HARDLINKED against the previous version's
  copy, so per-version cost is only what actually changed. Symlinks are
  recorded, never followed.
- ``summary.json`` business counts are read from the snapshot DB files (never
  the live DBs), so summary and snapshot are self-consistent.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from valuz_agent.modules.backup import manifest as mf
from valuz_agent.modules.backup.schemas import BackupRetention, BackupScope, BackupSummaryCounts

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int], None]  # (phase, processed_bytes_delta)

_HASH_CHUNK = 1024 * 1024
_KB_SOURCE_CAP = 20_000


@dataclass
class SourceSpec:
    """One category to snapshot: ``src`` (absolute) → ``data/<rel>`` in the
    version dir, restoring back to ``restore_target`` (normally == src)."""

    rel: str
    src: Path
    restore_target: Path | None = None

    def target(self) -> Path:
        return self.restore_target or self.src


@dataclass
class BackupPlan:
    user_id: str
    destination: Path
    kind: str  # manual | scheduled | pre_restore
    scope: BackupScope
    retention: BackupRetention
    host_db: Path | None
    kernel_db: Path | None
    sources: list[SourceSpec]
    app_version: str | None = None
    # When True (boot-time pre-restore snapshot: engines closed) DBs are
    # copied at file level instead of VACUUM INTO.
    db_file_copy: bool = False
    # Paths the walk must never descend into (the destination itself, so a
    # backup can't recursively contain backups).
    exclude_roots: list[Path] = field(default_factory=list)


@dataclass
class BackupResult:
    version_id: str
    manifest: mf.BackupManifest
    skipped_no_change: bool = False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _quote_sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def snapshot_sqlite(src: Path, dest: Path) -> None:
    """Transactionally-consistent snapshot of a (possibly hot) SQLite file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(str(src), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        # VACUUM INTO does not accept bound parameters — escape inline.
        conn.execute(f"VACUUM INTO '{_quote_sql_path(dest)}'")
    finally:
        conn.close()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _iter_source_files(spec: SourceSpec, exclude_roots: list[Path]) -> list[tuple[Path, str]]:
    """(absolute file, version-relative path) pairs for one source, sorted for
    deterministic manifests. Directories are implicit; symlinks surface as
    files with ``link`` set later."""
    out: list[tuple[Path, str]] = []
    src = spec.src
    if src.is_file() or src.is_symlink():
        out.append((src, f"{mf.DATA_SUBDIR}/{spec.rel}"))
        return out
    if not src.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(src):
        dpath = Path(dirpath)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not any(_is_within(dpath / d, ex) for ex in exclude_roots)
            # never descend INTO symlinked dirs — recorded as links instead
            and not (dpath / d).is_symlink()
        )
        for d in sorted(set(os.listdir(dpath)) - set(dirnames) - set(filenames)):
            # symlinked dirs excluded above still need a link record
            child = dpath / d
            if child.is_symlink():
                rel = child.relative_to(src).as_posix()
                out.append((child, f"{mf.DATA_SUBDIR}/{spec.rel}/{rel}"))
        for name in sorted(filenames):
            fpath = dpath / name
            rel = fpath.relative_to(src).as_posix()
            out.append((fpath, f"{mf.DATA_SUBDIR}/{spec.rel}/{rel}"))
    return out


def _stat_or_none(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except OSError:
        return None


def compute_fingerprint(plan: BackupPlan) -> str:
    """Cheap change detector: hash of every source file's (relpath, size,
    mtime) plus the DB files' (size, mtime). If it matches the previous
    version's fingerprint, nothing moved and the run is skipped."""
    digest = hashlib.sha256()
    for db in (plan.host_db, plan.kernel_db):
        st = _stat_or_none(db) if db else None
        if st is not None:
            digest.update(f"db:{db}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    for spec in plan.sources:
        for fpath, rel in _iter_source_files(spec, plan.exclude_roots):
            st = _stat_or_none(fpath)
            if st is not None:
                digest.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def _preflight(plan: BackupPlan, prev: mf.BackupManifest | None) -> None:
    from valuz_agent.modules.backup.errors import BackupPreflightFailed

    dest = plan.destination
    try:
        mf.versions_dir(dest).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupPreflightFailed(f"destination not writable: {exc}") from exc

    needed = 0
    if prev is not None:
        needed = int(prev.total_bytes * 1.2)
    else:
        for db in (plan.host_db, plan.kernel_db):
            st = _stat_or_none(db) if db else None
            if st is not None:
                needed += st.st_size * 2
    try:
        free = shutil.disk_usage(dest).free
    except OSError as exc:
        raise BackupPreflightFailed(f"cannot stat destination: {exc}") from exc
    if needed and free < needed:
        raise BackupPreflightFailed(
            f"not enough free space at destination: need ~{needed} bytes, have {free}"
        )


def _allocate_version_dir(destination: Path) -> tuple[str, Path]:
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    version_id = base
    n = 2
    while mf.version_dir(destination, version_id).exists():
        version_id = f"{base}-{n}"
        n += 1
    vdir = mf.version_dir(destination, version_id)
    vdir.mkdir(parents=True, exist_ok=False)
    return version_id, vdir


def _copy_or_link(
    src: Path,
    dest: Path,
    st: os.stat_result,
    prev_entry: mf.ManifestFile | None,
    prev_vdir: Path | None,
) -> tuple[mf.ManifestFile, bool]:
    """Materialize one file into the version dir. Returns (entry, copied) —
    ``copied=False`` means it was hardlinked against the previous version (or
    is a symlink record, which writes nothing)."""
    mtime_ms = st.st_mtime_ns // 1_000_000

    if src.is_symlink():
        return mf.ManifestFile(path="", size=0, mtime_ms=mtime_ms, link=os.readlink(src)), False

    dest.parent.mkdir(parents=True, exist_ok=True)
    if (
        prev_entry is not None
        and prev_vdir is not None
        and prev_entry.link is None
        and prev_entry.size == st.st_size
        and prev_entry.mtime_ms == mtime_ms
    ):
        prev_file = prev_vdir / prev_entry.path
        if prev_file.is_file():
            try:
                os.link(prev_file, dest)
                return (
                    mf.ManifestFile(
                        path="", size=st.st_size, mtime_ms=mtime_ms, sha256=prev_entry.sha256
                    ),
                    False,
                )
            except OSError:
                # cross-device / FS without hardlinks — fall through to copy
                pass

    shutil.copy2(src, dest)
    return (
        mf.ManifestFile(path="", size=st.st_size, mtime_ms=mtime_ms, sha256=_sha256_file(dest)),
        True,
    )


def _snapshot_files(
    plan: BackupPlan,
    vdir: Path,
    prev: tuple[Path, mf.BackupManifest] | None,
    progress: ProgressFn,
) -> tuple[list[mf.ManifestFile], int, int, str]:
    prev_vdir, prev_map = None, {}
    if prev is not None:
        prev_vdir = prev[0]
        prev_map = {f.path: f for f in prev[1].files}

    entries: list[mf.ManifestFile] = []
    total = 0
    new_bytes = 0
    linked_any = False
    for spec in plan.sources:
        for fpath, rel in _iter_source_files(spec, plan.exclude_roots):
            st = _stat_or_none(fpath)
            if st is None:
                continue
            entry, copied = _copy_or_link(fpath, vdir / rel, st, prev_map.get(rel), prev_vdir)
            entry.path = rel
            entries.append(entry)
            if entry.link is None:
                total += entry.size
                if copied:
                    new_bytes += entry.size
                else:
                    linked_any = True
            progress("files", entry.size if copied else 0)
    dedup = "hardlink" if (prev is None or linked_any or not entries) else "none"
    return entries, total, new_bytes, dedup


def _read_alembic_version(db_path: Path, table: str) -> str | None:
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            row = conn.execute(f"SELECT version_num FROM {table} LIMIT 1").fetchone()
            return str(row[0]) if row else None
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def build_summary(vdir: Path) -> mf.BackupSummary:
    """Business-level counts, read from the SNAPSHOT DBs so summary and
    snapshot can never disagree."""
    summary = mf.BackupSummary()
    counts = BackupSummaryCounts()

    host = vdir / mf.DB_SUBDIR / "valuz.db"
    if host.is_file():
        try:
            conn = sqlite3.connect(str(host), timeout=10)
            try:
                counts.projects = _count(conn, "valuz_project")
                counts.agents = _count(conn, "valuz_agent")
                counts.skills = _count(conn, "valuz_skill_index")
                counts.knowledge_bases = _count(conn, "valuz_knowledge_base")
                counts.documents = _count(conn, "valuz_document_record")
                counts.automations = _count(conn, "valuz_automation")
                try:
                    rows = conn.execute(
                        "SELECT source_path FROM valuz_document_record LIMIT ?",
                        (_KB_SOURCE_CAP,),
                    ).fetchall()
                    missing = 0
                    sources = []
                    for (source_path,) in rows:
                        if not source_path:
                            continue
                        exists = Path(source_path).exists()
                        missing += 0 if exists else 1
                        sources.append({"path": source_path, "exists": exists})
                    summary.kb_sources = sources
                    summary.kb_source_missing = missing
                except sqlite3.Error:
                    pass
            finally:
                conn.close()
        except sqlite3.Error:
            logger.warning("backup summary: host snapshot unreadable", exc_info=True)

    kernel = vdir / mf.DB_SUBDIR / "kernel.db"
    if kernel.is_file():
        try:
            conn = sqlite3.connect(str(kernel), timeout=10)
            try:
                counts.sessions = _count(conn, "sessions")
                counts.messages = _count(conn, "messages")
                try:
                    rows = conn.execute(
                        "SELECT title FROM sessions WHERE title IS NOT NULL "
                        "ORDER BY created_at DESC LIMIT 5"
                    ).fetchall()
                    summary.recent_session_titles = [str(r[0]) for r in rows if r[0]]
                except sqlite3.Error:
                    pass
            finally:
                conn.close()
        except sqlite3.Error:
            logger.warning("backup summary: kernel snapshot unreadable", exc_info=True)

    summary.counts = counts
    return summary


def run_backup(
    plan: BackupPlan,
    progress: ProgressFn = lambda phase, delta: None,
    *,
    skip_if_unchanged: bool = False,
) -> BackupResult:
    """Execute one backup. Raises typed errors from ``errors.py`` on
    preflight failure; any other exception leaves an incomplete (manifest-less)
    version dir behind, cleaned up by the next run."""
    started = time.monotonic()
    progress("preflight", 0)

    prev_scan = mf.scan_versions(plan.destination)
    prev = prev_scan[0] if prev_scan else None
    _preflight(plan, prev[1] if prev else None)
    mf.remove_incomplete_versions(plan.destination)

    fingerprint = None
    if skip_if_unchanged and prev is not None:
        fingerprint = compute_fingerprint(plan)
        if prev[1].source_fingerprint and prev[1].source_fingerprint == fingerprint:
            return BackupResult(
                version_id=prev[1].version_id, manifest=prev[1], skipped_no_change=True
            )

    version_id, vdir = _allocate_version_dir(plan.destination)
    try:
        progress("db", 0)
        db_targets: list[mf.RestoreTarget] = []
        db_entries: list[mf.ManifestFile] = []
        db_bytes = 0
        for name, src in (("valuz.db", plan.host_db), ("kernel.db", plan.kernel_db)):
            if src is None or not src.exists():
                continue
            dest = vdir / mf.DB_SUBDIR / name
            if plan.db_file_copy:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            else:
                snapshot_sqlite(src, dest)
            st = dest.stat()
            db_entries.append(
                mf.ManifestFile(
                    path=f"{mf.DB_SUBDIR}/{name}",
                    size=st.st_size,
                    mtime_ms=st.st_mtime_ns // 1_000_000,
                    sha256=_sha256_file(dest),
                )
            )
            db_targets.append(mf.RestoreTarget(rel=f"{mf.DB_SUBDIR}/{name}", target=str(src)))
            db_bytes += st.st_size
            progress("db", st.st_size)

        file_entries, data_bytes, new_bytes, dedup = _snapshot_files(
            plan, vdir, prev, progress
        )

        progress("summary", 0)
        summary = build_summary(vdir)
        mf.write_summary(vdir, summary)

        progress("finalize", 0)
        manifest = mf.BackupManifest(
            version_id=version_id,
            created_at=int(time.time() * 1000),
            kind=plan.kind,
            app_version=plan.app_version,
            host_alembic=_read_alembic_version(
                vdir / mf.DB_SUBDIR / "valuz.db", "alembic_version_host"
            ),
            kernel_alembic=_read_alembic_version(
                vdir / mf.DB_SUBDIR / "kernel.db", "alembic_version"
            ),
            scope=plan.scope,
            dedup=dedup,
            total_bytes=db_bytes + data_bytes,
            new_bytes=db_bytes + new_bytes,
            file_count=len(db_entries) + len(file_entries),
            duration_ms=int((time.monotonic() - started) * 1000),
            restore_targets=db_targets
            + [
                mf.RestoreTarget(rel=f"{mf.DATA_SUBDIR}/{s.rel}", target=str(s.target()))
                for s in plan.sources
            ],
            files=db_entries + file_entries,
        )
        if fingerprint is None:
            fingerprint = compute_fingerprint(plan)
        manifest.source_fingerprint = fingerprint
        mf.write_manifest(vdir, manifest)

        apply_retention(plan.destination, plan.retention)
        return BackupResult(version_id=version_id, manifest=manifest)
    except Exception:
        # leave no half-version behind on a controlled failure path
        shutil.rmtree(vdir, ignore_errors=True)
        raise


def apply_retention(destination: Path, retention: BackupRetention) -> list[str]:
    """Prune versions per the simplified-GFS policy (design §5). Returns the
    pruned version ids. The most recent version is never deleted."""
    versions = mf.scan_versions(destination)  # newest first
    if len(versions) <= 1:
        return []

    keep: set[str] = set()
    now_ms = int(time.time() * 1000)

    for _vdir, manifest in versions[: retention.keep_recent]:
        keep.add(manifest.version_id)

    seen_days: set[str] = set()
    for _vdir, manifest in versions[retention.keep_recent :]:
        age_days = (now_ms - manifest.created_at) / 86_400_000
        day = datetime.fromtimestamp(manifest.created_at / 1000).strftime("%Y%m%d")
        if age_days <= retention.keep_daily_days and day not in seen_days:
            seen_days.add(day)
            keep.add(manifest.version_id)

    # newest always survives, whatever the numbers say
    keep.add(versions[0][1].version_id)

    survivors = [(v, m) for v, m in versions if m.version_id in keep]
    pruned = [(v, m) for v, m in versions if m.version_id not in keep]

    # size cap: drop oldest survivors (never the newest) until under cap
    if retention.max_total_gb > 0:
        cap = retention.max_total_gb * 1024**3
        total = sum(m.total_bytes for _, m in survivors)
        for vdir, manifest in reversed(survivors):  # oldest first
            if total <= cap or manifest.version_id == versions[0][1].version_id:
                continue
            pruned.append((vdir, manifest))
            total -= manifest.total_bytes

    pruned_ids = []
    for vdir, manifest in pruned:
        shutil.rmtree(vdir, ignore_errors=True)
        pruned_ids.append(manifest.version_id)
    if pruned_ids:
        logger.info("backup retention pruned %d version(s): %s", len(pruned_ids), pruned_ids)
    return pruned_ids

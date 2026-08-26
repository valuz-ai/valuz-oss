"""Restore mechanics — staging (while the app runs) + boot-time apply.

The running app can never replace its own hot SQLite files, so restore is a
two-phase protocol (docs/design/client-local-backup.md §8):

1. ``BackupService.request_restore`` validates the version and writes a small
   pointer file into the data dir (``backup-restore-pending.json``). The
   pointer carries absolute paths only — the boot step must work before any
   DB/preference read is possible.
2. ``apply_pending_restore`` runs from ``boot/backup_restore.py`` BEFORE any
   engine opens the SQLite files: it takes an automatic ``pre_restore``
   safety snapshot of everything it is about to overwrite (plain file copies
   are safe here — nothing has the files open), then replaces each
   ``restore_target`` from the version's payload, then writes a result report
   and clears the pointer.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from valuz_agent.modules.backup import manifest as mf
from valuz_agent.modules.backup.engine import BackupPlan, SourceSpec, run_backup
from valuz_agent.modules.backup.schemas import BackupRetention

logger = logging.getLogger(__name__)

_SQLITE_SIDECARS = ("-wal", "-shm")


def write_pending_request(pointer_file: Path, version_dir: Path) -> None:
    pointer_file.write_text(
        json.dumps(
            {
                "version_dir": str(version_dir),
                "requested_at": int(time.time() * 1000),
            }
        ),
        encoding="utf-8",
    )


def read_pending_request(pointer_file: Path) -> Path | None:
    if not pointer_file.is_file():
        return None
    data = mf.load_json_file(pointer_file)
    raw = (data or {}).get("version_dir")
    return Path(raw) if isinstance(raw, str) and raw else None


def _guard_target(target: Path) -> None:
    """Refuse obviously-dangerous restore targets (relative paths, filesystem
    roots, home itself). The targets come from a manifest the user's own
    machine wrote, but a corrupted manifest must not be able to rmtree /."""
    if not target.is_absolute():
        raise ValueError(f"restore target must be absolute: {target}")
    if len(target.parts) < 3:  # "/", "/Users", "/Users/x" all refused
        raise ValueError(f"refusing to restore over near-root path: {target}")
    if target == Path.home():
        raise ValueError("refusing to restore over the home directory")


def _replace_file(src: Path, target: Path) -> None:
    _guard_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in _SQLITE_SIDECARS:
        sidecar = target.parent / (target.name + suffix)
        sidecar.unlink(missing_ok=True)
    tmp = target.parent / (target.name + ".restore-tmp")
    shutil.copy2(src, tmp)
    tmp.replace(target)


def _replace_tree(src: Path | None, target: Path) -> None:
    """Make ``target`` exactly mirror the backed-up subtree. ``src=None``
    means the category was included but empty at backup time → target becomes
    an empty directory.

    Materialize-then-swap, never delete-then-copy: the payload is fully
    copied to a sibling ``.restore-new`` first, then swapped in via two
    renames. A failure at any point leaves the target either fully old or
    fully new — the original content is never the casualty of a half-done
    copy (a partially-failed ``rmtree`` on a live directory is exactly how
    an early smoke test wiped a real project root)."""
    _guard_target(target)
    tmp_new = target.parent / (target.name + ".restore-new")
    tmp_old = target.parent / (target.name + ".restore-old")

    # Crash recovery from a previous interrupted swap: if the target vanished
    # after being renamed aside, the old content is the only copy — put it back.
    if not target.exists() and tmp_old.exists():
        tmp_old.rename(target)
    # Leftover staging from an interrupted attempt is safe to discard.
    if tmp_new.is_dir() and not tmp_new.is_symlink():
        shutil.rmtree(tmp_new, ignore_errors=True)
    else:
        tmp_new.unlink(missing_ok=True)
    if tmp_old.is_dir() and not tmp_old.is_symlink():
        shutil.rmtree(tmp_old, ignore_errors=True)
    else:
        tmp_old.unlink(missing_ok=True)

    # 1. materialize the new content NEXT TO the target (same filesystem)
    target.parent.mkdir(parents=True, exist_ok=True)
    if src is not None and src.is_dir():
        shutil.copytree(src, tmp_new)
    elif src is not None and src.is_file():
        shutil.copy2(src, tmp_new)
    else:
        tmp_new.mkdir(parents=True, exist_ok=True)

    # 2. swap: two renames, each atomic
    if target.exists() or target.is_symlink():
        target.rename(tmp_old)
    tmp_new.rename(target)

    # 3. best-effort cleanup — a surviving .restore-old is noise, never loss
    if tmp_old.is_dir() and not tmp_old.is_symlink():
        shutil.rmtree(tmp_old, ignore_errors=True)
    else:
        tmp_old.unlink(missing_ok=True)


def _pre_restore_snapshot(
    user_id: str, destination: Path, manifest: mf.BackupManifest
) -> str | None:
    """Safety snapshot of exactly what the apply step will overwrite. Plain
    file copies — the boot step runs before any engine opens the DBs."""
    host_db: Path | None = None
    kernel_db: Path | None = None
    sources: list[SourceSpec] = []
    for rt in manifest.restore_targets:
        target = Path(rt.target)
        if rt.rel == f"{mf.DB_SUBDIR}/valuz.db":
            host_db = target
        elif rt.rel == f"{mf.DB_SUBDIR}/kernel.db":
            kernel_db = target
        elif rt.rel.startswith(f"{mf.DATA_SUBDIR}/"):
            sources.append(
                SourceSpec(rel=rt.rel.removeprefix(f"{mf.DATA_SUBDIR}/"), src=target)
            )
    plan = BackupPlan(
        user_id=user_id,
        destination=destination,
        kind="pre_restore",
        scope=manifest.scope,
        retention=BackupRetention(),
        host_db=host_db,
        kernel_db=kernel_db,
        sources=sources,
        db_file_copy=True,
        exclude_roots=[destination],
    )
    try:
        return run_backup(plan).version_id
    except Exception:  # noqa: BLE001 — safety net must not block restore
        logger.exception("pre-restore safety snapshot failed")
        return None


def apply_pending_restore(
    pointer_file: Path, result_file: Path, user_id: str
) -> dict[str, Any] | None:
    """Boot-time apply. Returns the result report (also persisted), or None
    when no restore is pending. Never raises — a failed restore must not brick
    boot; the report carries the error instead."""
    version_dir = read_pending_request(pointer_file)
    if version_dir is None:
        return None
    # One-shot semantics FIRST: whatever happens below, the next boot must not
    # re-attempt (a crash loop repeating a broken restore forever).
    pointer_file.unlink(missing_ok=True)

    report: dict[str, Any] = {
        "version_dir": str(version_dir),
        "applied_at": int(time.time() * 1000),
        "ok": False,
        "pre_restore_version": None,
        "error": None,
    }
    try:
        manifest = mf.load_manifest(version_dir)
        if manifest is None:
            raise ValueError(f"backup version at {version_dir} is missing its manifest")

        destination = version_dir.parent.parent  # <dest>/versions/<id>
        report["version_id"] = manifest.version_id
        report["pre_restore_version"] = _pre_restore_snapshot(
            user_id, destination, manifest
        )

        for rt in manifest.restore_targets:
            payload = version_dir / rt.rel
            target = Path(rt.target)
            if rt.rel.startswith(f"{mf.DB_SUBDIR}/"):
                if payload.is_file():
                    _replace_file(payload, target)
            else:
                _replace_tree(payload if payload.exists() else None, target)

        report["ok"] = True
        logger.info("backup restore applied: %s", manifest.version_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("backup restore failed")
        report["error"] = str(exc)

    try:
        result_file.write_text(json.dumps(report), encoding="utf-8")
    except OSError:
        logger.warning("could not persist restore result", exc_info=True)
    return report

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

Two invariants the apply step enforces, both learned the hard way:

* **The version under restore must survive its own safety snapshot.** The
  pre-restore snapshot is a backup run into the same destination; a backup
  run normally ends with retention pruning, and the version being restored is
  by definition an old one — exactly what the ladder prunes. The snapshot
  therefore runs with retention OFF (``BackupPlan.retention=None``).
* **"Payload missing" and "category was empty" are different things.** The
  manifest lists every file it captured, so a restore target whose payload
  directory is absent while the manifest lists files under it is a damaged or
  pruned version, and applying it would swap an EMPTY directory over live
  data. Every target is validated against the manifest before anything on
  disk is touched; a single missing payload fails the whole restore and
  leaves the live tree untouched.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from valuz_agent.modules.backup import manifest as mf
from valuz_agent.modules.backup.engine import (
    HOST_DB_NAME,
    KERNEL_DB_NAME,
    BackupPlan,
    SourceSpec,
    run_backup,
)

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


def _materialize_links(root: Path, links: Iterable[tuple[str, str]]) -> None:
    """Recreate the symlinks the manifest recorded for one category.

    The backup walk records links (target string) and never copies them, so
    the version dir has no entry for them — a plain ``copytree`` of the
    payload silently drops every symlink the user had. ``rel`` is the path
    inside the category; ``""`` means the category itself was a link, in
    which case ``root`` must not yet exist and becomes the link."""
    for rel, link_target in links:
        dest = root / rel if rel else root
        if dest.is_symlink() or dest.exists():
            continue  # payload already carried something at this path
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(link_target, dest)


def _replace_tree(src: Path | None, target: Path, links: Iterable[tuple[str, str]] = ()) -> None:
    """Make ``target`` exactly mirror the backed-up subtree. ``src=None``
    means the category was included but empty at backup time → target becomes
    an empty directory (or the recorded symlink, when ``links`` says the
    category itself was one). The caller is responsible for having proven
    that ``src=None`` really means "empty" and not "payload lost" — see
    :func:`_validate_payloads`.

    Materialize-then-swap, never delete-then-copy: the payload is fully
    copied to a sibling ``.restore-new`` first, then swapped in via two
    renames. A failure at any point leaves the target either fully old or
    fully new — the original content is never the casualty of a half-done
    copy (a partially-failed ``rmtree`` on a live directory is exactly how
    an early smoke test wiped a real project root)."""
    _guard_target(target)
    tmp_new = target.parent / (target.name + ".restore-new")
    tmp_old = target.parent / (target.name + ".restore-old")
    links = list(links)
    self_link = next((t for rel, t in links if rel == ""), None)

    # Crash recovery from a previous interrupted swap: if the target vanished
    # after being renamed aside, the old content is the only copy — put it back.
    if not target.exists() and not target.is_symlink() and tmp_old.exists():
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
    if self_link is not None:
        os.symlink(self_link, tmp_new)
    elif src is not None and src.is_dir():
        shutil.copytree(src, tmp_new, symlinks=True)
        _materialize_links(tmp_new, links)
    elif src is not None and src.is_file():
        shutil.copy2(src, tmp_new)
    else:
        tmp_new.mkdir(parents=True, exist_ok=True)
        _materialize_links(tmp_new, links)

    # 2. swap: two renames, each atomic
    if target.exists() or target.is_symlink():
        target.rename(tmp_old)
    tmp_new.rename(target)

    # 3. best-effort cleanup — a surviving .restore-old is noise, never loss
    if tmp_old.is_dir() and not tmp_old.is_symlink():
        shutil.rmtree(tmp_old, ignore_errors=True)
    else:
        tmp_old.unlink(missing_ok=True)


def _entries_under(manifest: mf.BackupManifest, rel: str) -> list[mf.ManifestFile]:
    """Manifest entries belonging to one restore target — the target itself
    (single-file categories) or anything beneath it."""
    prefix = rel + "/"
    return [f for f in manifest.files if f.path == rel or f.path.startswith(prefix)]


def _links_for(entries: Iterable[mf.ManifestFile], rel: str) -> list[tuple[str, str]]:
    """``(path-inside-category, link target)`` for every symlink entry;
    ``""`` when the category itself is the link."""
    out: list[tuple[str, str]] = []
    for entry in entries:
        if entry.link is None:
            continue
        inner = "" if entry.path == rel else entry.path[len(rel) + 1 :]
        out.append((inner, entry.link))
    return out


def _validate_payloads(version_dir: Path, manifest: mf.BackupManifest) -> None:
    """Prove every restore target's payload is present BEFORE touching disk.

    A target may legitimately have no payload only when the manifest records
    nothing but symlinks (or nothing at all) under it. Anything else missing
    means the version is damaged (pruned mid-restore, partially deleted,
    copied off a bad disk) and the restore must not proceed — applying it
    would replace live data with emptiness and report success."""
    missing: list[str] = []
    for rt in manifest.restore_targets:
        payload = version_dir / rt.rel
        if rt.rel.startswith(f"{mf.DB_SUBDIR}/"):
            if not payload.is_file():
                missing.append(rt.rel)
            continue
        expects_bytes = any(f.link is None for f in _entries_under(manifest, rt.rel))
        if expects_bytes and not payload.exists():
            missing.append(rt.rel)
    if missing:
        raise ValueError(
            "backup version is incomplete — payload missing for: "
            + ", ".join(missing)
            + " (the version may have been pruned or damaged; nothing was restored)"
        )


def _pre_restore_snapshot(
    user_id: str, destination: Path, manifest: mf.BackupManifest
) -> str | None:
    """Safety snapshot of exactly what the apply step will overwrite. Plain
    file copies — the boot step runs before any engine opens the DBs.

    Retention is deliberately OFF here (see the module docstring): this run
    must never prune, least of all the version it is protecting."""
    host_db: Path | None = None
    kernel_db: Path | None = None
    extra_dbs: list[SourceSpec] = []
    sources: list[SourceSpec] = []
    db_prefix = f"{mf.DB_SUBDIR}/"
    data_prefix = f"{mf.DATA_SUBDIR}/"
    for rt in manifest.restore_targets:
        target = Path(rt.target)
        if rt.rel == f"{db_prefix}{HOST_DB_NAME}":
            host_db = target
        elif rt.rel == f"{db_prefix}{KERNEL_DB_NAME}":
            kernel_db = target
        elif rt.rel.startswith(db_prefix):
            extra_dbs.append(SourceSpec(rel=rt.rel.removeprefix(db_prefix), src=target))
        elif rt.rel.startswith(data_prefix):
            sources.append(SourceSpec(rel=rt.rel.removeprefix(data_prefix), src=target))
    plan = BackupPlan(
        user_id=user_id,
        destination=destination,
        kind="pre_restore",
        scope=manifest.scope,
        retention=None,
        host_db=host_db,
        kernel_db=kernel_db,
        sources=sources,
        db_file_copy=True,
        exclude_roots=[destination],
        extra_dbs=extra_dbs,
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
        if manifest.format > mf.MANIFEST_FORMAT:
            raise ValueError(
                f"backup manifest format {manifest.format} is newer than this app "
                f"understands ({mf.MANIFEST_FORMAT}); upgrade the app first"
            )
        report["version_id"] = manifest.version_id
        # Validate BEFORE the safety snapshot as well: a damaged version must
        # not even cost the user a pre_restore version worth of disk.
        _validate_payloads(version_dir, manifest)

        destination = version_dir.parent.parent  # <dest>/versions/<id>
        report["pre_restore_version"] = _pre_restore_snapshot(user_id, destination, manifest)
        # The snapshot wrote into the same destination; re-check that the
        # payload is still whole before replacing anything.
        _validate_payloads(version_dir, manifest)

        for rt in manifest.restore_targets:
            payload = version_dir / rt.rel
            target = Path(rt.target)
            if rt.rel.startswith(f"{mf.DB_SUBDIR}/"):
                _replace_file(payload, target)
            else:
                entries = _entries_under(manifest, rt.rel)
                _replace_tree(
                    payload if payload.exists() else None,
                    target,
                    links=_links_for(entries, rt.rel),
                )

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

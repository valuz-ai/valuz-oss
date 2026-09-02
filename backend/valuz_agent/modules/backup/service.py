"""BackupService — orchestrates config, runs, version browsing and restore
staging on top of the sync engine (docs/design/client-local-backup.md).

Single-flight: one backup at a time per process, guarded by a
``threading.Lock`` (the engine runs in a worker thread; both the route path
— ``asyncio.to_thread`` — and the scheduler thread funnel through
``execute_backup``).

Deployment gate: local SQLite + in-process kernel only. Remote/HTTP kernels
don't expose their DB file to this host, and an explicit ``database_url``
(Postgres) makes DB backup a DBA concern — both report ``supported=false``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Literal, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.config import settings
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.db_urls import (
    db_url,
    is_sqlite_runtime,
    kernel_db_url,
    sqlite_path_from_url,
)
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.backup import engine as backup_engine
from valuz_agent.modules.backup import manifest as mf
from valuz_agent.modules.backup.errors import (
    BackupAlreadyRunning,
    BackupDestinationInvalid,
    BackupFileNotFound,
    BackupNotSupported,
    BackupRestoreConflict,
    BackupVersionNotFound,
)
from valuz_agent.modules.backup.schemas import (
    FREQUENCY_INTERVAL_MS,
    BackupConfigPatch,
    BackupConfigResponse,
    BackupFileEntry,
    BackupFileListResponse,
    BackupLastRun,
    BackupRestorePlanEntry,
    BackupRestoreResponse,
    BackupRetention,
    BackupRunProgress,
    BackupRunResponse,
    BackupScope,
    BackupVersionDetail,
    BackupVersionInfo,
    BackupVersionListResponse,
)
from valuz_agent.modules.settings import preferences as prefs

logger = logging.getLogger(__name__)


def _app_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("valuz-oss-backend")
    except Exception:  # noqa: BLE001
        return None


def _version_info(manifest: mf.BackupManifest, vdir: Path) -> BackupVersionInfo:
    summary = mf.load_summary(vdir)
    return BackupVersionInfo(
        id=manifest.version_id,
        created_at=manifest.created_at,
        kind=(
            cast(Literal["scheduled", "manual", "pre_restore"], manifest.kind)
            if manifest.kind in ("scheduled", "manual", "pre_restore")
            else "manual"
        ),
        total_bytes=manifest.total_bytes,
        new_bytes=manifest.new_bytes,
        file_count=manifest.file_count,
        duration_ms=manifest.duration_ms,
        app_version=manifest.app_version,
        counts=summary.counts,
    )


# ── coverage ────────────────────────────────────────────────────────
#
# What a backup contains is decided HERE and nowhere else, and every entry
# is resolved through ``FsRegistry`` — the registry is the one place that
# knows where user data lives, so a new data directory registered there is
# either picked up below or has to be added to ``EXCLUDED_DATA_DIR_ENTRIES``
# with a reason. ``tests/modules/backup/test_backup_plan.py`` enumerates the
# registry and fails on anything that is in neither list; that tripwire is
# how the coverage stays honest (the original hardcoded four-name tuple
# silently missed a month of new directories).

#: Top-level entries under ``data_dir`` that a backup deliberately skips.
#: Each carries the reason so the exclusion is a decision, not an oversight.
EXCLUDED_DATA_DIR_ENTRIES: dict[str, str] = {
    "secrets": "plaintext credentials — never leaves the machine (design §0 #3)",
    "browser-chrome": "isolated Chrome profile; regenerable, may hold session cookies",
    "memory-review": "scratch cwd for memory-review sessions; regenerable",
    "generative-ui": "scratch cwd for generative-UI sessions; regenerable",
    "skill-creator": "legacy per-session skill staging; transient",
    "ptc": "generated PTC code-face skills; rebuilt on codegen-version mismatch",
    "official-skills": (
        "bundled/marketplace-provisioned packages re-landed at boot; may hold "
        "sealed (DRM) blobs that must not be exported through the backup file API"
    ),
    "backup-restore-pending.json": "restore pointer; consumed at boot",
    "backup-restore-result.json": "restore report; consumed by the settings UI",
    # Primary DBs are snapshotted via VACUUM INTO, not walked as files.
    settings.db_filename: "primary host DB — snapshotted as db/valuz.db",
    settings.kernel_db_filename: "kernel DB — snapshotted as db/kernel.db",
}

#: Runtime state the kernel keeps NEXT TO ``kernel.db`` (``boot/kernel.py``
#: sets the env; deployments may override it). Included because the
#: sessions in ``kernel.db`` reference it: a DeepAgents session without its
#: langgraph checkpoint cannot be continued after a restore.
DEEPAGENTS_CHECKPOINT_DB_NAME = "deepagents_checkpoints.db"
DEEPAGENTS_CHECKPOINT_ROOT_NAME = "deepagents-checkpoints"
DSH_STATE_DIR_NAME = "dsh-state"


def _runtime_state_paths(kernel_db: Path | None) -> tuple[Path | None, list[tuple[str, Path]]]:
    """``(checkpoint sqlite, [(rel, dir), ...])`` for the kernel-adjacent
    runtime state. Reads the env the boot step exported (so a deployment
    override is honoured) and falls back to the boot step's own defaults."""
    base = kernel_db.parent if kernel_db is not None else None

    def _env_or(name: str, default: Path | None) -> Path | None:
        raw = os.environ.get(name)
        if raw:
            return Path(raw).expanduser()
        return default

    ckpt_db = _env_or(
        "DEEPAGENTS_CHECKPOINT_DB", base / DEEPAGENTS_CHECKPOINT_DB_NAME if base else None
    )
    dirs: list[tuple[str, Path]] = []
    ckpt_root = _env_or(
        "DEEPAGENTS_CHECKPOINT_ROOT", base / DEEPAGENTS_CHECKPOINT_ROOT_NAME if base else None
    )
    if ckpt_root is not None:
        dirs.append((DEEPAGENTS_CHECKPOINT_ROOT_NAME, ckpt_root))
    dsh = _env_or("VALUZ_DSH_STATE_DIR", base / DSH_STATE_DIR_NAME if base else None)
    if dsh is not None:
        dirs.append((DSH_STATE_DIR_NAME, dsh))
    return ckpt_db, dirs


def build_sources(
    user_id: str,
    scope: BackupScope,
    *,
    external_projects: list[tuple[str, Path]],
    kb_kinds: list[str],
    kernel_db: Path | None,
) -> tuple[list[backup_engine.SourceSpec], list[backup_engine.SourceSpec]]:
    """Resolve the file sources of a backup: ``(sources, extra_dbs)``.

    Pure with respect to the DB — the caller passes what needs a query
    (external project roots, the KB kinds this owner has) so the coverage
    can be unit-tested against a temp data dir. Only sources that exist are
    returned; the manifest records what was actually captured.
    """
    data_dir = fs_registry.data_dir(user_id)
    sources: list[backup_engine.SourceSpec] = []
    seen: set[Path] = set()

    def _add(rel: str, src: Path) -> None:
        key = src.resolve() if src.exists() else src
        if key in seen:
            return
        seen.add(key)
        if src.exists():
            sources.append(backup_engine.SourceSpec(rel=rel, src=src))

    # Always-on application data. Every path comes from the registry.
    _add("docs", fs_registry.docs_root(user_id))
    _add("memories", fs_registry.memory_dir(user_id, "global"))
    _add("attachments", fs_registry.attachments_root(user_id))
    _add("plugins", fs_registry.plugins_root(user_id))
    _add("plugins-data", fs_registry.plugins_data_root(user_id))
    _add("installation.json", fs_registry.installation_file(user_id))
    # Per-version archives of saved skills (history the library cannot rebuild).
    _add("skill-versions", fs_registry.skill_versions_root(user_id))

    # Managed KB roots: the default root plus one per KB class this owner
    # actually has (a host that installs a root resolver routes classes to
    # distinct directories; OSS resolves them all to the same one, and the
    # dedup above folds that back to a single source).
    _add("kb", fs_registry.kb_root(user_id))
    for kind in sorted(set(kb_kinds)):
        try:
            root = fs_registry.kb_root(user_id, kind)
        except (ValueError, OSError):
            # A class the resolver refuses to scope to an owner (org-owned
            # content) is not this user's to back up.
            continue
        rel = f"kb-{kind}"
        try:
            rel = root.relative_to(data_dir).as_posix()
        except ValueError:
            pass
        _add(rel, root)

    # Kernel-adjacent runtime state (see the constants above).
    ckpt_db, runtime_dirs = _runtime_state_paths(kernel_db)
    for rel, path in runtime_dirs:
        _add(rel, path)
    extra_dbs: list[backup_engine.SourceSpec] = []
    if ckpt_db is not None and ckpt_db.exists():
        extra_dbs.append(backup_engine.SourceSpec(rel=DEEPAGENTS_CHECKPOINT_DB_NAME, src=ckpt_db))

    # User-tunable scope.
    if scope.user_skills:
        _add("user-skills", fs_registry.user_skill_root(user_id))
    projects_root = fs_registry.project_root(user_id)
    if scope.managed_projects:
        _add("projects", projects_root)
    if scope.external_projects:
        for project_id, root in external_projects:
            # managed-root children are already covered by "projects"
            if not root.is_dir() or backup_engine._is_within(root, projects_root):
                continue
            _add(f"projects-external/{project_id}", root)

    return sources, extra_dbs


class BackupService:
    def __init__(self) -> None:
        self._run_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._progress = BackupRunProgress()

    # ── deployment gate ──────────────────────────────────────────────

    def support_status(self) -> tuple[bool, str | None]:
        if not is_sqlite_runtime():
            return False, "external_database"
        if settings.kernel_mode != "inprocess":
            return False, "remote_kernel"
        if settings.kernel_database_url and not settings.kernel_database_url.startswith("sqlite"):
            return False, "external_kernel_database"
        return True, None

    def _require_supported(self) -> None:
        supported, reason = self.support_status()
        if not supported:
            raise BackupNotSupported(f"backup unsupported: {reason}")

    # ── config ───────────────────────────────────────────────────────

    async def _destination(self, db: AsyncSession, user_id: str) -> Path:
        raw = await prefs.get_backup_destination(db, user_id=user_id)
        if raw:
            return Path(raw).expanduser()
        return fs_registry.default_backup_root(user_id)

    def _validate_destination(self, raw: str, user_id: str) -> Path:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise BackupDestinationInvalid(f"destination must be an absolute path: {raw}")
        data_dir = fs_registry.data_dir(user_id)
        if backup_engine._is_within(path, data_dir):
            raise BackupDestinationInvalid("destination must be outside the app data directory")
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".valuz-write-probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise BackupDestinationInvalid(f"destination not writable: {exc}") from exc
        return path

    async def get_config(self, db: AsyncSession, user_id: str) -> BackupConfigResponse:
        supported, reason = self.support_status()
        enabled = await prefs.get_backup_enabled(db, user_id=user_id)
        frequency = await prefs.get_backup_frequency(db, user_id=user_id)
        destination = await self._destination(db, user_id)
        scope = BackupScope.model_validate(await prefs.get_backup_scope(db, user_id=user_id) or {})
        retention = BackupRetention.model_validate(
            await prefs.get_backup_retention(db, user_id=user_id) or {}
        )
        last_run = BackupLastRun.model_validate(
            await prefs.get_backup_last_run(db, user_id=user_id) or {}
        )
        next_run_at = await prefs.get_backup_next_run_at(db, user_id=user_id)

        def _scan() -> tuple[int, int]:
            versions = mf.scan_versions(destination)
            return len(versions), sum(m.total_bytes for _, m in versions)

        versions_count, total_bytes = await asyncio.to_thread(_scan)
        restore_result = mf.load_json_file(fs_registry.backup_restore_result_file(user_id))

        return BackupConfigResponse(
            supported=supported,
            unsupported_reason=reason,
            enabled=enabled,
            frequency=frequency,  # type: ignore[arg-type]
            destination=str(destination),
            scope=scope,
            retention=retention,
            last_run=last_run,
            next_run_at=next_run_at if enabled and frequency != "manual" else None,
            run=self.progress(),
            versions_count=versions_count,
            total_bytes=total_bytes,
            restore_result=restore_result,
        )

    async def patch_config(
        self, db: AsyncSession, user_id: str, payload: BackupConfigPatch
    ) -> BackupConfigResponse:
        if payload.destination is not None:
            self._validate_destination(payload.destination, user_id)
            await prefs.set_backup_destination(db, payload.destination, user_id=user_id)
        if payload.frequency is not None:
            await prefs.set_backup_frequency(db, payload.frequency, user_id=user_id)
        if payload.enabled is not None:
            await prefs.set_backup_enabled(db, payload.enabled, user_id=user_id)
        if payload.scope is not None:
            await prefs.set_backup_scope(db, payload.scope.model_dump(), user_id=user_id)
        if payload.retention is not None:
            await prefs.set_backup_retention(db, payload.retention.model_dump(), user_id=user_id)

        # Recompute the schedule whenever enabled/frequency may have moved.
        enabled = await prefs.get_backup_enabled(db, user_id=user_id)
        frequency = await prefs.get_backup_frequency(db, user_id=user_id)
        if enabled and frequency != "manual":
            interval = FREQUENCY_INTERVAL_MS[frequency]
            last = BackupLastRun.model_validate(
                await prefs.get_backup_last_run(db, user_id=user_id) or {}
            )
            base = last.at or now_ms()
            nxt = base + interval
            # never schedule into the past by more than one tick
            await prefs.set_backup_next_run_at(db, max(nxt, now_ms()), user_id=user_id)
        else:
            await prefs.set_backup_next_run_at(db, None, user_id=user_id)

        return await self.get_config(db, user_id)

    # ── run orchestration ────────────────────────────────────────────

    def progress(self) -> BackupRunProgress:
        with self._state_lock:
            return self._progress.model_copy()

    def _progress_cb(self, phase: str, delta: int) -> None:
        with self._state_lock:
            self._progress.phase = phase  # type: ignore[assignment]
            self._progress.processed_bytes += delta

    async def _build_plan(
        self, db: AsyncSession, user_id: str, *, kind: str
    ) -> backup_engine.BackupPlan:
        destination = await self._destination(db, user_id)
        scope = BackupScope.model_validate(await prefs.get_backup_scope(db, user_id=user_id) or {})
        retention = BackupRetention.model_validate(
            await prefs.get_backup_retention(db, user_id=user_id) or {}
        )

        external_projects: list[tuple[str, Path]] = []
        if scope.external_projects:
            from valuz_agent.modules.projects.service import project_root_paths

            for project_id, proj_kind, root_path in await project_root_paths(user_id):
                if proj_kind == "project" and root_path:
                    external_projects.append((project_id, Path(root_path).expanduser()))

        from valuz_agent.modules.docs.service import owner_kb_kinds

        kb_kinds = await owner_kb_kinds(user_id)

        kernel_db = sqlite_path_from_url(kernel_db_url())
        sources, extra_dbs = build_sources(
            user_id,
            scope,
            external_projects=external_projects,
            kb_kinds=kb_kinds,
            kernel_db=kernel_db,
        )
        return backup_engine.BackupPlan(
            user_id=user_id,
            destination=destination,
            kind=kind,
            scope=scope,
            retention=retention,
            host_db=sqlite_path_from_url(db_url()),
            kernel_db=kernel_db,
            sources=sources,
            app_version=_app_version(),
            exclude_roots=[destination],
            extra_dbs=extra_dbs,
        )

    async def start_manual_run(self, user_id: str) -> BackupRunResponse:
        self._require_supported()
        if self._run_lock.locked():
            raise BackupAlreadyRunning()
        task = asyncio.create_task(self.execute_backup(user_id, trigger="manual"))
        # keep a reference so the task isn't GC'd; errors are handled inside
        task.add_done_callback(lambda t: t.exception())
        # give the run a beat to flip the progress flag so the 202 body
        # already shows running=true
        await asyncio.sleep(0)
        return BackupRunResponse(started=True, run=self.progress())

    async def execute_backup(
        self, user_id: str, *, trigger: str, skip_if_unchanged: bool = False
    ) -> backup_engine.BackupResult | None:
        """The single run pipeline used by both the route (manual) and the
        scheduler (scheduled). Returns None when another run holds the lock."""
        if not self._run_lock.acquire(blocking=False):
            return None
        try:
            with self._state_lock:
                self._progress = BackupRunProgress(
                    running=True,
                    phase="preflight",
                    started_at=now_ms(),
                    trigger=trigger,  # type: ignore[arg-type]
                )
            async with async_unit_of_work(commit=False) as db:
                plan = await self._build_plan(db, user_id, kind=trigger)
            result: backup_engine.BackupResult | None = None
            error: str | None = None
            try:
                result = await asyncio.to_thread(
                    backup_engine.run_backup,
                    plan,
                    self._progress_cb,
                    skip_if_unchanged=skip_if_unchanged,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("backup run failed")
                error = str(exc)

            await self._record_outcome(user_id, result=result, error=error)
            return result
        finally:
            with self._state_lock:
                self._progress = BackupRunProgress()
            self._run_lock.release()

    async def _record_outcome(
        self,
        user_id: str,
        *,
        result: backup_engine.BackupResult | None,
        error: str | None,
    ) -> None:
        async with async_unit_of_work() as db:
            if error is not None:
                last = BackupLastRun(at=now_ms(), status="failed", error=error)
            elif result is not None and result.skipped_no_change:
                last = BackupLastRun(
                    at=now_ms(), status="skipped_no_change", version_id=result.version_id
                )
            else:
                last = BackupLastRun(
                    at=now_ms(),
                    status="ok",
                    version_id=result.version_id if result else None,
                )
            await prefs.set_backup_last_run(db, last.model_dump(), user_id=user_id)

            enabled = await prefs.get_backup_enabled(db, user_id=user_id)
            frequency = await prefs.get_backup_frequency(db, user_id=user_id)
            if enabled and frequency != "manual":
                await prefs.set_backup_next_run_at(
                    db, now_ms() + FREQUENCY_INTERVAL_MS[frequency], user_id=user_id
                )

        if error is not None:
            from valuz_agent.modules.notifications.service import notification_service

            await notification_service.ingest(
                user_id,
                dedup_key=f"backup-failed:{now_ms() // 3_600_000}",  # ≤1/hour
                kind="backup_failed",
                title="",  # frontend renders the localized kind label
                body=error,
                route="/settings?tab=backup",
            )

    # ── scheduler hook ───────────────────────────────────────────────

    async def tick(self, user_id: str) -> None:
        """One scheduler heartbeat: run a due scheduled backup, if any.
        Missed windows (app closed) fire on the first tick after boot."""
        supported, _ = self.support_status()
        if not supported or self._run_lock.locked():
            return
        async with async_unit_of_work(commit=False) as db:
            enabled = await prefs.get_backup_enabled(db, user_id=user_id)
            frequency = await prefs.get_backup_frequency(db, user_id=user_id)
            next_run_at = await prefs.get_backup_next_run_at(db, user_id=user_id)
        if not enabled or frequency == "manual":
            return
        if next_run_at is None:
            # enabled but never scheduled (e.g. pre-existing config) — seed it
            async with async_unit_of_work() as db:
                await prefs.set_backup_next_run_at(
                    db, now_ms() + FREQUENCY_INTERVAL_MS[frequency], user_id=user_id
                )
            return
        if next_run_at <= now_ms():
            await self.execute_backup(user_id, trigger="scheduled", skip_if_unchanged=True)

    # ── versions ─────────────────────────────────────────────────────

    async def list_versions(self, db: AsyncSession, user_id: str) -> BackupVersionListResponse:
        destination = await self._destination(db, user_id)

        def _list() -> list[BackupVersionInfo]:
            return [_version_info(m, vdir) for vdir, m in mf.scan_versions(destination)]

        return BackupVersionListResponse(versions=await asyncio.to_thread(_list))

    async def _load_version(
        self, db: AsyncSession, user_id: str, version_id: str
    ) -> tuple[Path, mf.BackupManifest]:
        destination = await self._destination(db, user_id)
        try:
            vdir = mf.version_dir(destination, version_id)
        except ValueError as exc:
            raise BackupVersionNotFound(str(exc)) from exc
        manifest = await asyncio.to_thread(mf.load_manifest, vdir)
        if manifest is None:
            raise BackupVersionNotFound(f"backup version not found: {version_id}")
        return vdir, manifest

    async def get_version(
        self, db: AsyncSession, user_id: str, version_id: str
    ) -> BackupVersionDetail:
        vdir, manifest = await self._load_version(db, user_id, version_id)
        summary = await asyncio.to_thread(mf.load_summary, vdir)
        info = _version_info(manifest, vdir)
        return BackupVersionDetail(
            **info.model_dump(exclude={"counts"}),
            counts=summary.counts,
            host_alembic=manifest.host_alembic,
            kernel_alembic=manifest.kernel_alembic,
            scope=manifest.scope,
            dedup="hardlink" if manifest.dedup == "hardlink" else "none",
            kb_source_count=len(summary.kb_sources),
            kb_source_missing=summary.kb_source_missing,
        )

    async def delete_version(self, db: AsyncSession, user_id: str, version_id: str) -> None:
        vdir, _manifest = await self._load_version(db, user_id, version_id)
        await asyncio.to_thread(shutil.rmtree, vdir, True)

    # ── file browsing / export ───────────────────────────────────────

    async def list_files(
        self, db: AsyncSession, user_id: str, version_id: str, path: str
    ) -> BackupFileListResponse:
        _vdir, manifest = await self._load_version(db, user_id, version_id)
        prefix = path.strip("/")
        base = f"{prefix}/" if prefix else ""
        dirs: dict[str, int] = {}
        files: list[BackupFileEntry] = []
        for entry in manifest.files:
            if not entry.path.startswith(base):
                continue
            rest = entry.path[len(base) :]
            if "/" in rest:
                head = rest.split("/", 1)[0]
                dirs[head] = dirs.get(head, 0) + (entry.size if entry.link is None else 0)
            else:
                files.append(
                    BackupFileEntry(
                        name=rest,
                        path=entry.path,
                        kind="link" if entry.link is not None else "file",
                        size=entry.size,
                    )
                )
        entries = [
            BackupFileEntry(name=name, path=f"{base}{name}", kind="dir", size=size)
            for name, size in sorted(dirs.items())
        ] + sorted(files, key=lambda e: e.name)
        return BackupFileListResponse(path=prefix, entries=entries)

    async def resolve_file(
        self, db: AsyncSession, user_id: str, version_id: str, path: str
    ) -> Path:
        vdir, manifest = await self._load_version(db, user_id, version_id)
        rel = path.strip("/")
        entry = next((f for f in manifest.files if f.path == rel and f.link is None), None)
        if entry is None:
            raise BackupFileNotFound(f"not in this version: {path}")
        resolved = (vdir / rel).resolve()
        if not backup_engine._is_within(resolved, vdir):
            raise BackupFileNotFound(f"invalid path: {path}")
        if not resolved.is_file():
            raise BackupFileNotFound(f"missing on disk: {path}")
        return resolved

    # ── restore ──────────────────────────────────────────────────────

    async def _current_alembic(self, db: AsyncSession, table: str) -> str | None:
        try:
            row = (await db.execute(text(f"SELECT version_num FROM {table} LIMIT 1"))).first()
            return str(row[0]) if row else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _kernel_alembic_current() -> str | None:
        """Kernel chain head, read file-level from kernel.db (infra-level
        metadata read, not a business query — see design §4 note)."""
        path = sqlite_path_from_url(kernel_db_url())
        if path is None or not path.exists():
            return None
        try:
            conn = sqlite3.connect(str(path), timeout=10)
            try:
                row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
                return str(row[0]) if row else None
            finally:
                conn.close()
        except sqlite3.Error:
            return None

    @staticmethod
    def _assert_not_newer(label: str, backup_ver: str | None, current_ver: str | None) -> None:
        """Reject restoring a backup stamped by a NEWER app (numeric alembic
        ids per backend convention). Older backups are fine — boot migrations
        bring them up."""
        if not backup_ver or not current_ver:
            return
        try:
            if int(backup_ver) > int(current_ver):
                raise BackupRestoreConflict(
                    f"backup {label} schema ({backup_ver}) is newer than this app "
                    f"({current_ver}); upgrade the app first"
                )
        except ValueError:
            return  # non-numeric ids — skip the check rather than block

    async def request_restore(
        self, db: AsyncSession, user_id: str, version_id: str, *, dry_run: bool
    ) -> BackupRestoreResponse:
        self._require_supported()
        if self._run_lock.locked():
            raise BackupAlreadyRunning("cannot restore while a backup is running")
        vdir, manifest = await self._load_version(db, user_id, version_id)

        self._assert_not_newer(
            "host", manifest.host_alembic, await self._current_alembic(db, "alembic_version_host")
        )
        self._assert_not_newer(
            "kernel",
            manifest.kernel_alembic,
            await asyncio.to_thread(self._kernel_alembic_current),
        )

        size_by_prefix: dict[str, int] = {}
        for entry in manifest.files:
            for rt in manifest.restore_targets:
                if entry.path == rt.rel or entry.path.startswith(rt.rel + "/"):
                    size_by_prefix[rt.rel] = size_by_prefix.get(rt.rel, 0) + entry.size
                    break
        plan = [
            BackupRestorePlanEntry(
                target=rt.target,
                action="replace" if Path(rt.target).exists() else "create",
                bytes=size_by_prefix.get(rt.rel, 0),
            )
            for rt in manifest.restore_targets
        ]
        if dry_run:
            return BackupRestoreResponse(staged=False, requires_restart=True, plan=plan)

        from valuz_agent.modules.backup.restore import write_pending_request

        await asyncio.to_thread(
            write_pending_request, fs_registry.backup_restore_pending_file(user_id), vdir
        )
        logger.info("backup restore staged: %s (applies on next boot)", version_id)
        return BackupRestoreResponse(staged=True, requires_restart=True, plan=plan)


backup_service = BackupService()

__all__ = ["BackupService", "backup_service"]

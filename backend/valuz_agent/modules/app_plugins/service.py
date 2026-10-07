"""``AppPluginService`` — the one door for every install / lifecycle path.

The HTTP routes, the ``app_plugin_manager`` harness tool and overlay code (catalog
installs) all call this service; the contract is in docs task card 04 §B.

State lives in three places: the packages on disk (``app-plugins/<id>/<version>/``,
or the linked directory of a dev plugin), ``installed.json`` (the registry, see
``store.py``) and ``plugins.json`` (the disabled set, shared with the first-party
plugin prefs). Storage and config are per user x plugin rows in ``valuz.db``.

Package source -> install: a zip (file or https URL) is unpacked safely into a
scratch directory under ``app-plugins/.tmp``; a directory is first packed with the
deterministic ``pack`` (so a directory install and its zip are byte-for-byte the
same package) and unpacked the same way. The scratch tree is validated, then moved
into ``<id>/<version>/`` and recorded. A superseded version directory is kept until
the next start (``maintenance.cleanup_superseded_versions``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.app_plugins import archive, logs
from valuz_agent.modules.app_plugins import manifest as mf
from valuz_agent.modules.app_plugins.datastore import AppPluginDatastore
from valuz_agent.modules.app_plugins.errors import (
    AppPluginError,
    AppPluginsUnavailable,
    AutomationBusy,
    InvalidAutomationInput,
    InvalidConfig,
    InvalidManifest,
    InvalidSource,
    InvalidStorageKey,
    NotDevPlugin,
    PluginAutomationNotFound,
    PluginNotFound,
    PluginRunNotFound,
    Sha256Mismatch,
    StorageKeyNotFound,
    StorageQuotaExceeded,
    VersionNotNewer,
)
from valuz_agent.modules.app_plugins.semver import is_newer, satisfies
from valuz_agent.modules.app_plugins.store import InstalledStore, installed_store

logger = logging.getLogger(__name__)

MAX_STORAGE_KEY = 200
MAX_STORAGE_VALUE_BYTES = 256 * 1024
MAX_STORAGE_TOTAL_BYTES = 10 * 1024 * 1024
MAX_WATCH_SECONDS = 60.0
DEV_POLL_SECONDS = 1.0
TMP_DIRNAME = ".tmp"

#: Statuses (docs task card 04 §D ``Item.status``).
ENABLED = "enabled"
DISABLED = "disabled"
INCOMPATIBLE = "incompatible"
BROKEN = "broken"
REQUIRES_UNMET = "requires-unmet"
BLOCKED = "blocked"


# ---- environment probes (module level so tests can replace them) ---------------------


def running_edition() -> str:
    """The edition id this process runs as (``oss`` on a bare OSS backend)."""
    from valuz_agent.infra.config import settings

    return str(getattr(settings, "edition", None) or os.environ.get("VALUZ_EDITION") or "oss")


def deployment_type() -> str:
    from valuz_agent.infra.config import settings

    return str(getattr(settings, "deployment_type", "local"))


def active_capabilities() -> frozenset[str]:
    """``oss.<feature>`` capabilities of the plugins active in this process."""
    from valuz_agent.features import oss_capabilities
    from valuz_agent.plugin_host import active_plugin_host

    host = active_plugin_host()
    if host is None:
        return oss_capabilities()
    caps: set[str] = set()
    for plugin_id in host.active_ids():
        caps.update(host.get(plugin_id).plugin.provides)
    return frozenset(caps)


def disabled_ids() -> frozenset[str]:
    from valuz_agent.plugin_host import load_plugin_prefs

    return load_plugin_prefs().disabled


def set_plugin_enabled(plugin_id: str, enabled: bool) -> None:
    from valuz_agent.plugin_host import save_enabled

    save_enabled(plugin_id, enabled)


async def enabled_connector_slugs(user_id: str) -> set[str]:
    from valuz_agent.modules.connectors.service import ConnectorService

    async with async_unit_of_work(commit=False) as db:
        views = await ConnectorService.with_defaults(db).list_connectors(user_id)
    return {v.slug for v in views if v.enabled}


@asynccontextmanager
async def _session(db: AsyncSession | None, *, commit: bool = True) -> AsyncIterator[AsyncSession]:
    """The caller's session as is (it owns the transaction: nothing is committed
    here), else a unit of work of our own."""
    if db is not None:
        yield db
        return
    async with async_unit_of_work(commit=commit) as own:
        yield own


# ---- value objects -----------------------------------------------------------------


@dataclass
class _Loaded:
    root: Path
    manifest: dict[str, Any] | None
    errors: list[str]
    warnings: list[str]
    mkey: tuple[int, int] | None
    sig: tuple[Any, ...]


@dataclass
class _Env:
    user_id: str | None
    edition: str
    deployment: str
    capabilities: frozenset[str]
    disabled: frozenset[str]
    db: AsyncSession | None = None
    connectors: set[str] | None = None
    connectors_loaded: bool = False
    automation_ids: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class _Prepared:
    work: Path
    package: Path
    manifest: dict[str, Any] | None
    errors: list[str]
    warnings: list[str]
    sha256: str
    size: int
    source: dict[str, Any]


def _stat_key(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _normalize_sha(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    return text.removeprefix("sha256:") or None


class AppPluginService:
    def __init__(self, store: InstalledStore | None = None) -> None:
        self._store = store or installed_store
        self._cache: dict[Path, _Loaded] = {}
        self._dev_sigs: dict[str, tuple[Any, ...]] = {}

    # ---- gates ---------------------------------------------------------------------

    @staticmethod
    def _require_local() -> None:
        if deployment_type() != "local":
            raise AppPluginsUnavailable()

    # ---- package roots and manifest cache ------------------------------------------

    @staticmethod
    def _is_dev(entry: dict[str, Any]) -> bool:
        return bool((entry.get("source") or {}).get("kind") == "dev" and entry.get("dev_path"))

    def _root_of(self, entry: dict[str, Any]) -> Path:
        if self._is_dev(entry):
            return Path(str(entry["dev_path"]))
        return fs_registry.app_plugin_version_dir(str(entry["id"]), str(entry["version"]))

    @staticmethod
    def _signature(root: Path, manifest: dict[str, Any] | None) -> tuple[Any, ...]:
        """mtime/size of the files whose change a dev reload must notice."""
        names = [mf.MANIFEST_NAME]
        frontend = (manifest or {}).get("frontend")
        if isinstance(frontend, dict):
            if isinstance(frontend.get("entry"), str):
                names.append(frontend["entry"])
            names.extend(s for s in frontend.get("styles") or [] if isinstance(s, str))
        out: list[Any] = []
        for name in names:
            out.append((name, _stat_key(root / name)))
        return tuple(out)

    def _read_cached(self, root: Path) -> _Loaded:
        mkey = _stat_key(root / mf.MANIFEST_NAME)
        cached = self._cache.get(root)
        if cached is not None and cached.mkey == mkey:
            if cached.sig == self._signature(root, cached.manifest):
                return cached
        manifest, errors = mf.read_manifest(root)
        warnings: list[str] = []
        if manifest is not None:
            errors, warnings = mf.validate_manifest(manifest, root)
        loaded = _Loaded(
            root=root,
            manifest=manifest,
            errors=errors,
            warnings=warnings,
            mkey=mkey,
            sig=self._signature(root, manifest),
        )
        self._cache[root] = loaded
        return loaded

    # ---- environment ---------------------------------------------------------------

    async def _env(self, user_id: str | None, db: AsyncSession | None = None) -> _Env:
        return _Env(
            user_id=user_id,
            db=db,
            edition=running_edition(),
            deployment=deployment_type(),
            capabilities=active_capabilities(),
            disabled=disabled_ids(),
        )

    async def _connectors(self, env: _Env) -> set[str] | None:
        if not env.connectors_loaded:
            env.connectors_loaded = True
            if env.user_id is not None:
                try:
                    env.connectors = await enabled_connector_slugs(env.user_id)
                except Exception:  # noqa: BLE001 — an unreachable DB must not break the list
                    logger.debug("could not list connectors for requires", exc_info=True)
        return env.connectors

    async def _unmet(self, requires: list[str], env: _Env) -> list[str]:
        unmet: list[str] = []
        for requirement in requires:
            kind, _, value = requirement.partition(":")
            ok = True
            if kind == "edition":
                ok = env.edition == value
            elif kind == "deployment":
                ok = env.deployment == value
            elif kind == "capability":
                ok = value in env.capabilities or f"oss.{value}" in env.capabilities
            elif kind == "connector":
                slugs = await self._connectors(env)
                ok = slugs is None or value in slugs  # unknown (no user) is not unmet
            if not ok:
                unmet.append(requirement)
        return unmet

    # ---- items ---------------------------------------------------------------------

    async def _automation_ids(self, env: _Env, plugin_id: str, names: list[str]) -> dict[str, str]:
        if env.user_id is None or not names:
            return {}
        if plugin_id not in env.automation_ids:
            from valuz_agent.modules.automations.app_plugin_support import AppPluginAutomations

            found: dict[str, str] = {}
            try:
                async with _session(env.db, commit=False) as db:
                    rows = await AppPluginAutomations(db, env.user_id).ids_by_name(plugin_id, names)
                found = {name: row.id for name, row in rows.items()}
            except Exception:  # noqa: BLE001
                logger.debug("could not read the automations of %s", plugin_id, exc_info=True)
            env.automation_ids[plugin_id] = found
        return env.automation_ids[plugin_id]

    async def _item(self, entry: dict[str, Any], env: _Env) -> dict[str, Any]:
        from valuz_agent.ports.extensions import ext

        plugin_id = str(entry["id"])
        loaded = self._read_cached(self._root_of(entry))
        manifest = loaded.manifest or {}
        revision = int(entry.get("revision") or 1)
        prefix = f"/v1/app-plugin-assets/{plugin_id}/{revision}/"
        frontend: dict[str, Any] = (
            manifest["frontend"] if isinstance(manifest.get("frontend"), dict) else {}
        )
        entry_path = frontend.get("entry")
        declared = [a for a in manifest.get("automations") or [] if isinstance(a, dict)]
        ids = await self._automation_ids(
            env, plugin_id, [str(a.get("name")) for a in declared if a.get("name")]
        )
        requires = mf.requires_of(manifest)
        unmet = await self._unmet(requires, env)
        engines = manifest.get("engines") if isinstance(manifest.get("engines"), dict) else {}
        source = dict(entry.get("source") or {"kind": "file"})
        if self._is_dev(entry):
            source = {"kind": "dev", "path": entry["dev_path"]}
        enabled = plugin_id not in env.disabled
        item: dict[str, Any] = {
            "id": plugin_id,
            "version": str(entry.get("version") or manifest.get("version") or ""),
            "name": manifest.get("name") if manifest.get("name") is not None else plugin_id,
            "description": manifest.get("description"),
            "publisher": manifest.get("publisher") or {"name": ""},
            "source": source,
            "status": ENABLED,
            "status_reason": None,
            "enabled": enabled,
            "permissions": mf.permissions_of(manifest),
            "requires": requires,
            "unmet_requires": unmet,
            "engines": engines,
            "entry_url": prefix + entry_path if isinstance(entry_path, str) else None,
            "style_urls": [
                prefix + s for s in (frontend.get("styles") or []) if isinstance(s, str)
            ],
            "locales": mf.read_locales(loaded.root, manifest) if manifest else {},
            "config_schema": manifest.get("config")
            if isinstance(manifest.get("config"), dict)
            else None,
            "automations": [
                {
                    "name": a.get("name"),
                    "title": a.get("title"),
                    "runtime": a.get("runtime"),
                    "trigger": a.get("trigger", "manual"),
                    "automation_id": ids.get(str(a.get("name"))),
                }
                for a in declared
            ],
            "revision": revision,
            "dev_path": entry.get("dev_path") if self._is_dev(entry) else None,
            "sha256": entry.get("sha256"),
            "installed_at": entry.get("installed_at"),
        }
        status, reason = await self._status(entry, loaded, env, item, unmet, ext)
        item["status"], item["status_reason"] = status, reason
        return item

    async def _status(
        self,
        entry: dict[str, Any],
        loaded: _Loaded,
        env: _Env,
        item: dict[str, Any],
        unmet: list[str],
        ext: Any,
    ) -> tuple[str, str | None]:
        manifest = loaded.manifest
        if manifest is None or loaded.errors:
            return BROKEN, (loaded.errors[0] if loaded.errors else "manifest unreadable")
        if manifest.get("id") != entry.get("id"):
            return BROKEN, "the package manifest does not match the install record"
        rng = (manifest.get("engines") or {}).get("valuz-plugin-api")
        try:
            compatible = bool(rng) and satisfies(mf.API_VERSION, str(rng))
        except ValueError:
            compatible = False
        if not compatible:
            return INCOMPATIBLE, (
                f"requires plugin API {rng}; this Valuz provides {mf.API_VERSION}"
            )
        if str(entry["id"]) in env.disabled:
            return DISABLED, None
        if env.user_id is not None:
            try:
                verdict = await ext.app_plugin_policy.evaluate(env.user_id, dict(item))
            except Exception:  # noqa: BLE001 — an unreachable policy source fails open
                logger.warning("third-party policy evaluation failed", exc_info=True)
            else:
                if not verdict.allowed:
                    return BLOCKED, verdict.reason or "blocked by your organization's policy"
        if unmet:
            return REQUIRES_UNMET, "not met: " + ", ".join(unmet)
        return ENABLED, None

    # ---- dev change detection ------------------------------------------------------

    async def _poll_dev(self) -> bool:
        """Bump the revision of every dev-linked plugin whose files changed since the
        last look. ``True`` when anything did."""
        state = self._store.read()
        suspects = []
        for plugin_id, entry in state["plugins"].items():
            if not self._is_dev(entry):
                continue
            loaded = self._read_cached(self._root_of(entry))
            sig = self._signature(loaded.root, loaded.manifest)
            known = self._dev_sigs.get(plugin_id)
            if known is None:
                self._dev_sigs[plugin_id] = sig
            elif known != sig:
                suspects.append(plugin_id)
        if not suspects:
            return False
        changed = False
        async with self._store.transaction() as live:
            for plugin_id in suspects:
                entry = live["plugins"].get(plugin_id)
                if entry is None or not self._is_dev(entry):
                    continue
                loaded = self._read_cached(self._root_of(entry))
                sig = self._signature(loaded.root, loaded.manifest)
                if self._dev_sigs.get(plugin_id) == sig:
                    continue  # a concurrent poll already took it
                self._dev_sigs[plugin_id] = sig
                entry["revision"] = int(entry.get("revision") or 1) + 1
                if loaded.manifest and isinstance(loaded.manifest.get("version"), str):
                    entry["version"] = loaded.manifest["version"]
                self._store.bump(live)
                changed = True
        return changed

    def _remember_sig(self, entry: dict[str, Any]) -> None:
        if self._is_dev(entry):
            loaded = self._read_cached(self._root_of(entry))
            self._dev_sigs[str(entry["id"])] = self._signature(loaded.root, loaded.manifest)
        else:
            self._dev_sigs.pop(str(entry["id"]), None)

    # ---- listing and watching ------------------------------------------------------

    async def watch(self, since: int, timeout: float = 25.0) -> dict[str, Any]:
        """Long-poll: return as soon as ``generation`` differs from ``since``."""
        self._require_local()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(max(timeout, 0.0), MAX_WATCH_SECONDS)
        while True:
            await self._poll_dev()
            generation = self._store.generation()
            if generation != since:
                return {"generation": generation}
            remaining = deadline - loop.time()
            if remaining <= 0:
                return {"generation": generation}
            await self._store.wait_for_change(min(DEV_POLL_SECONDS, remaining))

    async def bump_generation(self) -> int:
        """Tell every renderer to re-list: something the statuses depend on
        changed without touching the installed list (an overlay's policy sync)."""
        return await self._store.bump_generation()

    async def set_safe_mode(self, enabled: bool, reason: str | None = None) -> dict[str, Any]:
        self._require_local()
        return {"safe_mode": await self._store.set_safe_mode(enabled, reason)}

    # ---- sources -------------------------------------------------------------------

    @staticmethod
    def _work_dir() -> Path:
        work = fs_registry.app_plugins_root() / TMP_DIRNAME / uuid.uuid4().hex
        work.mkdir(parents=True, exist_ok=True)
        return work

    async def _prepare(
        self, source: dict[str, Any], *, archive_source: dict[str, Any] | None = None
    ) -> _Prepared:
        """Resolve ``source`` into a validated, unpacked scratch package."""
        work = self._work_dir()
        try:
            return await self._prepare_into(work, source, archive_source)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise

    async def _prepare_into(
        self, work: Path, source: dict[str, Any], archive_source: dict[str, Any] | None
    ) -> _Prepared:
        url = source.get("url")
        raw_path = source.get("source_path")
        if bool(url) == bool(raw_path):
            raise InvalidSource("give exactly one of source_path or url")
        zip_path: Path
        source_info: dict[str, Any]
        if url:
            zip_path = work / "download.zip"
            await archive.download(str(url), zip_path)
            source_info = {"kind": "url", "url": str(url)}
        else:
            path = Path(str(raw_path)).expanduser()
            if not path.exists():
                raise InvalidSource(f"path not found: {raw_path}")
            path = path.resolve()
            source_info = {"kind": "file", "path": str(path)}
            if path.is_dir():
                report = await asyncio.to_thread(self.validate, str(path))
                if not report["ok"]:  # nothing to unpack; the caller reports the problems
                    return _Prepared(
                        work=work,
                        package=path,
                        manifest=report["manifest"],
                        errors=list(report["errors"]),
                        warnings=list(report["warnings"]),
                        sha256="",
                        size=0,
                        source=archive_source or source_info,
                    )
                zip_path = work / "package.zip"
                await asyncio.to_thread(archive.pack_directory, path, zip_path, report["manifest"])
            else:
                zip_path = path
        if archive_source is not None:
            source_info = dict(archive_source)
        package = await asyncio.to_thread(archive.extract_zip, zip_path, work / "pkg")
        sha = await asyncio.to_thread(archive.sha256_file, zip_path)
        size = zip_path.stat().st_size
        manifest, errors = mf.read_manifest(package)
        warnings: list[str] = []
        if manifest is not None:
            errors, warnings = await asyncio.to_thread(mf.validate_manifest, manifest, package)
        return _Prepared(
            work=work,
            package=package,
            manifest=manifest,
            errors=errors,
            warnings=warnings,
            sha256=sha,
            size=size,
            source=source_info,
        )

    # ---- inspect -------------------------------------------------------------------

    async def inspect(
        self, source: dict[str, Any], *, user_id: str | None = None
    ) -> dict[str, Any]:
        self._require_local()
        prepared = await self._prepare(source)
        try:
            manifest = prepared.manifest
            permissions = mf.permissions_of(manifest or {})
            requires = mf.requires_of(manifest or {})
            env = await self._env(user_id)
            existing: dict[str, Any] | None = None
            added: list[str] = []
            plugin_id = (manifest or {}).get("id")
            if isinstance(plugin_id, str):
                current = self._store.read()["plugins"].get(plugin_id)
                if current is not None:
                    existing = {"version": current.get("version")}
                    old = self._read_cached(self._root_of(current)).manifest or {}
                    old_permissions = set(mf.permissions_of(old))
                    added = [p for p in permissions if p not in old_permissions]
            return {
                "manifest": manifest,
                "sha256": prepared.sha256,
                "size": prepared.size,
                "permissions": permissions,
                "requires": requires,
                "unmet_requires": await self._unmet(requires, env),
                "errors": prepared.errors,
                "warnings": prepared.warnings,
                "has_backend": bool((manifest or {}).get("backend")),
                "existing": existing,
                "added_permissions": added,
            }
        finally:
            shutil.rmtree(prepared.work, ignore_errors=True)

    # ---- install -------------------------------------------------------------------

    async def install(
        self,
        user_id: str,
        source: dict[str, Any],
        *,
        expected_sha256: str | None = None,
        enable: bool = True,
        db: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """``db``: do the DB writes of this call (plugin automations) on the caller's
        session and leave committing it to the caller."""
        self._require_local()
        prepared = await self._prepare(source)
        try:
            return await self._install_prepared(user_id, prepared, expected_sha256, enable, db)
        finally:
            shutil.rmtree(prepared.work, ignore_errors=True)

    async def install_archive(
        self,
        user_id: str,
        archive_path: str,
        *,
        source: dict[str, Any],
        expected_sha256: str | None = None,
        db: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """Install a package a caller already holds as a file (a catalog download).

        ``source`` is the registry record (``{"kind": "catalog", "scope", "item_id", ...}``).
        """
        self._require_local()
        prepared = await self._prepare({"source_path": archive_path}, archive_source=source)
        try:
            return await self._install_prepared(user_id, prepared, expected_sha256, True, db)
        finally:
            shutil.rmtree(prepared.work, ignore_errors=True)

    async def _install_prepared(
        self,
        user_id: str,
        prepared: _Prepared,
        expected_sha256: str | None,
        enable: bool,
        db: AsyncSession | None = None,
    ) -> dict[str, Any]:
        expected = _normalize_sha(expected_sha256)
        if expected is not None and expected != prepared.sha256:
            raise Sha256Mismatch(
                extra={"expected": expected, "actual": prepared.sha256},
            )
        manifest = prepared.manifest
        if manifest is None or prepared.errors:
            raise InvalidManifest(errors=prepared.errors)
        plugin_id = str(manifest["id"])
        version = str(manifest["version"])
        updated_from: str | None = None
        async with self._store.transaction() as state:
            existing = state["plugins"].get(plugin_id)
            had_automations = self._declares_automations(existing)
            if existing is not None:
                updated_from = str(existing.get("version"))
                replaceable = self._is_dev(existing) or self._is_broken(existing)
                if not replaceable and not is_newer(version, updated_from):
                    raise VersionNotNewer(
                        f"{plugin_id} {updated_from} is installed; {version} is not newer",
                        extra={"installed_version": updated_from, "version": version},
                    )
            target = fs_registry.app_plugin_version_dir(plugin_id, version)
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(prepared.package), str(target))
            generation = self._store.bump(state)
            previous = int((existing or {}).get("revision") or 0)
            state["plugins"][plugin_id] = {
                "id": plugin_id,
                "version": version,
                "source": prepared.source,
                "sha256": prepared.sha256,
                "installed_at": now_ms(),
                "revision": previous + 1 if existing else generation,
            }
            set_plugin_enabled(plugin_id, enable)
            entry = dict(state["plugins"][plugin_id])
        self._cache.pop(target, None)
        self._remember_sig(entry)
        await self._sync_automations(
            user_id, entry, enabled=enable, previous=had_automations, db=db
        )
        env = await self._env(user_id, db)
        return {"plugin": await self._item(entry, env), "updated_from": updated_from}

    def _declares_automations(self, entry: dict[str, Any] | None) -> bool:
        if entry is None:
            return False
        manifest = self._read_cached(self._root_of(entry)).manifest or {}
        return bool(manifest.get("automations"))

    def _is_broken(self, entry: dict[str, Any]) -> bool:
        loaded = self._read_cached(self._root_of(entry))
        return loaded.manifest is None or bool(loaded.errors)

    # ---- dev link / reload ---------------------------------------------------------

    async def dev_link(
        self, user_id: str, path: str, *, db: AsyncSession | None = None
    ) -> dict[str, Any]:
        self._require_local()
        root = Path(path).expanduser()
        if not root.is_dir():
            raise InvalidSource(f"not a directory: {path}")
        root = root.resolve()
        manifest, errors = mf.read_manifest(root)
        if manifest is not None:
            errors, _ = mf.validate_manifest(manifest, root)
        if manifest is None or errors:
            raise InvalidManifest(errors=errors)
        plugin_id = str(manifest["id"])
        async with self._store.transaction() as state:
            existing = state["plugins"].get(plugin_id)
            had_automations = self._declares_automations(existing)
            generation = self._store.bump(state)
            state["plugins"][plugin_id] = {
                "id": plugin_id,
                "version": str(manifest["version"]),
                "source": {"kind": "dev", "path": str(root)},
                "sha256": None,
                "installed_at": now_ms(),
                "dev_path": str(root),
                "revision": int((existing or {}).get("revision") or 0) + 1
                if existing
                else generation,
            }
            set_plugin_enabled(plugin_id, True)
            entry = dict(state["plugins"][plugin_id])
        self._cache.pop(root, None)
        self._remember_sig(entry)
        await self._sync_automations(user_id, entry, enabled=True, previous=had_automations, db=db)
        env = await self._env(user_id, db)
        return {"plugin": await self._item(entry, env)}

    async def reload(self, plugin_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        self._require_local()
        async with self._store.transaction() as state:
            entry = state["plugins"].get(plugin_id)
            if entry is None:
                raise PluginNotFound()
            if not self._is_dev(entry):
                raise NotDevPlugin()
            root = self._root_of(entry)
            had_automations = self._declares_automations(entry)
            self._cache.pop(root, None)
            manifest, _ = mf.read_manifest(root)
            if manifest and isinstance(manifest.get("version"), str):
                entry["version"] = manifest["version"]
            entry["revision"] = int(entry.get("revision") or 1) + 1
            self._store.bump(state)
            snapshot = dict(entry)
        self._remember_sig(snapshot)
        if user_id is not None:
            await self._sync_automations(
                user_id,
                snapshot,
                enabled=plugin_id not in disabled_ids(),
                previous=had_automations,
            )
        env = await self._env(user_id)
        return {"plugin": await self._item(snapshot, env)}

    # ---- enable / disable / uninstall ----------------------------------------------

    def _entry(self, plugin_id: str) -> dict[str, Any]:
        entry: dict[str, Any] | None = self._store.read()["plugins"].get(plugin_id)
        if entry is None:
            raise PluginNotFound()
        return entry

    async def set_enabled(self, user_id: str, plugin_id: str, enabled: bool) -> dict[str, Any]:
        self._require_local()
        async with self._store.transaction() as state:
            entry = state["plugins"].get(plugin_id)
            if entry is None:
                raise PluginNotFound()
            set_plugin_enabled(plugin_id, enabled)
            self._store.bump(state)
            snapshot = dict(entry)
        if self._declares_automations(snapshot):
            await self._set_automations_paused(user_id, plugin_id, paused=not enabled)
        env = await self._env(user_id)
        return {"plugin": await self._item(snapshot, env)}

    async def uninstall(
        self,
        user_id: str,
        plugin_id: str,
        *,
        purge_data: bool = False,
        db: AsyncSession | None = None,
    ) -> dict[str, Any]:
        self._require_local()
        existing = self._entry(plugin_id)
        deleted = (
            await self._delete_automations(user_id, plugin_id, db=db)
            if self._declares_automations(existing)
            else 0
        )
        async with self._store.transaction() as state:
            entry = state["plugins"].pop(plugin_id, None)
            if entry is None:
                raise PluginNotFound()
            set_plugin_enabled(plugin_id, True)  # drop the id from the disabled set
            self._store.bump(state)
        self._dev_sigs.pop(plugin_id, None)
        package_dir = fs_registry.app_plugins_root() / plugin_id
        if package_dir.is_dir():
            shutil.rmtree(package_dir, ignore_errors=True)
        self._cache = {k: v for k, v in self._cache.items() if plugin_id not in k.parts}
        if purge_data:
            async with _session(db) as session:
                await AppPluginDatastore(session).purge(plugin_id)
            shutil.rmtree(fs_registry.app_plugin_data_dir(plugin_id), ignore_errors=True)
            logs.delete_log(plugin_id)
        return {"removed": True, "automations_deleted": deleted}

    # ---- validate / pack -----------------------------------------------------------

    def validate(self, path: str) -> dict[str, Any]:
        """``{"ok", "errors", "warnings", "manifest"}`` for a plugin source directory.

        Works on any deployment (``app_plugin_manager`` validates from cloud sessions
        too); only installing is local-only.
        """
        root = Path(path).expanduser()
        if not root.is_dir():
            return {
                "ok": False,
                "errors": [f"not a directory: {path}"],
                "warnings": [],
                "manifest": None,
            }
        manifest, errors = mf.read_manifest(root)
        warnings: list[str] = []
        if manifest is not None:
            errors, warnings = mf.validate_manifest(manifest, root)
            warnings.extend(self._outside_packed_set(root, manifest))
        return {"ok": not errors, "errors": errors, "warnings": warnings, "manifest": manifest}

    @staticmethod
    def _outside_packed_set(root: Path, manifest: dict[str, Any]) -> list[str]:
        """Warnings for manifest files that exist but ``pack`` would leave out (``pack``
        then refuses). Symlink problems are left to ``pack``, which reports them."""
        try:
            packed = {name for name, _ in archive.collect_pack_files(root, manifest)}
        except InvalidManifest:
            return []
        out: list[str] = []
        for where, rel in mf.relative_paths(manifest):
            if where == "locales" or ".." in rel.split("/"):
                continue
            clean = PurePosixPath(rel).as_posix()
            if (root / clean).is_file() and clean not in packed:
                out.append(
                    f"{where}: {rel!r} is outside the packed file set (frontend/, the locales "
                    "directory, automations/, the icon, README.md, LICENSE*); `pack` will refuse it"
                )
        return out

    def pack(self, path: str, out_dir: str | None = None) -> dict[str, Any]:
        """Write ``<out_dir>/<id>-<version>.zip`` (default ``<path>/dist``): the
        deterministic zip. What is shipped is validated again; a manifest path outside
        the packed file set is refused. Works on any deployment."""
        report = self.validate(path)
        if not report["ok"]:
            raise InvalidManifest(errors=list(report["errors"]))
        manifest = report["manifest"]
        root = Path(path).expanduser().resolve()
        target_dir = (Path(out_dir).expanduser() if out_dir else root / "dist").resolve()
        out_zip = target_dir / f"{manifest['id']}-{manifest['version']}.zip"
        files: list[str] = []
        with tempfile.TemporaryDirectory() as scratch:
            built = Path(scratch) / out_zip.name
            sha, size = archive.pack_directory(root, built, manifest, files=files, exclude=out_zip)
            package = archive.extract_zip(built, Path(scratch) / "pkg")
            shipped, _ = mf.read_manifest(package)
            problems = mf.validate_manifest(shipped, package)[0] if shipped else ["manifest lost"]
            if problems:
                raise InvalidManifest(
                    "the packed file set is incomplete (only frontend/, the locales directory, "
                    "automations/, the icon, README.md and LICENSE* are packed)",
                    errors=problems,
                )
            out_zip.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(built), str(out_zip))
        return {
            "path": str(out_zip),
            "sha256": sha,
            "size": size,
            "id": manifest["id"],
            "version": manifest["version"],
            "files": files,
            "warnings": report["warnings"],
        }

    # ---- assets --------------------------------------------------------------------

    def asset_path(self, plugin_id: str, revision: int, relative: str) -> tuple[Path, bool]:
        """The file an asset URL names, and whether it is a dev plugin's (uncached).

        Only the plugin's current revision resolves, only inside its own directory.
        """
        self._require_local()
        entry = self._store.read()["plugins"].get(plugin_id)
        if entry is None or int(entry.get("revision") or 1) != revision:
            raise PluginNotFound("Unknown plugin revision")
        if "\x00" in relative or relative.startswith(("/", "\\")) or ".." in relative.split("/"):
            raise PluginNotFound("Unknown asset")
        root = self._root_of(entry).resolve()
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            raise PluginNotFound("Unknown asset")
        return target, self._is_dev(entry)

    # ---- permission checks (the request middleware) --------------------------------

    def plugin_access(self, plugin_id: str) -> frozenset[str] | None:
        """Permissions of an enabled, loadable plugin; ``None`` when the id is unknown or
        the plugin is not enabled (disabled, broken, incompatible, cloud deployment).

        A light, synchronous check (no policy / requires evaluation): it gates
        every plugin-originated request, so it only reads the registry and the cached
        manifest.
        """
        if deployment_type() != "local":
            return None
        entry = self._store.read()["plugins"].get(plugin_id)
        if entry is None or plugin_id in disabled_ids():
            return None
        loaded = self._read_cached(self._root_of(entry))
        manifest = loaded.manifest
        if manifest is None or loaded.errors or manifest.get("id") != plugin_id:
            return None
        rng = (manifest.get("engines") or {}).get("valuz-plugin-api")
        try:
            if not rng or not satisfies(mf.API_VERSION, str(rng)):
                return None
        except ValueError:
            return None
        return frozenset(mf.permissions_of(manifest))

    # ---- logs ----------------------------------------------------------------------

    def read_logs(self, plugin_id: str, limit: int = 200) -> dict[str, Any]:
        self._require_local()
        self._entry(plugin_id)
        return {"entries": logs.read_log(plugin_id, min(max(limit, 1), 1000))}

    def write_log(self, plugin_id: str, level: str, message: str, source: str = "frontend") -> None:
        self._require_local()
        self._entry(plugin_id)
        logs.append_log(plugin_id, level, message, source)

    # ---- config --------------------------------------------------------------------

    def _config_schema(self, plugin_id: str) -> dict[str, Any] | None:
        entry = self._entry(plugin_id)
        manifest = self._read_cached(self._root_of(entry)).manifest or {}
        schema = manifest.get("config")
        return schema if isinstance(schema, dict) else None

    async def get_config(self, user_id: str, plugin_id: str) -> dict[str, Any]:
        self._require_local()
        schema = self._config_schema(plugin_id)
        async with async_unit_of_work(commit=False) as db:
            row = await AppPluginDatastore(db).get_config(user_id, plugin_id)
        stored = _loads(row.values_json) if row is not None else {}
        return {"values": mf.fill_defaults(schema, stored), "schema": schema}

    async def put_config(
        self, user_id: str, plugin_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

        self._require_local()
        schema = self._config_schema(plugin_id)
        if schema is None:
            if values:
                raise InvalidConfig(errors=["the plugin declares no config schema"])
        else:
            problems = sorted(
                f"{'.'.join(str(p) for p in e.absolute_path) or 'values'}: {e.message}"
                for e in Draft202012Validator(schema).iter_errors(values)
            )
            if problems:
                raise InvalidConfig(errors=problems)
        async with async_unit_of_work() as db:
            await AppPluginDatastore(db).put_config(
                user_id, plugin_id, json.dumps(values, ensure_ascii=False), now_ms()
            )
        await self._store.bump_generation()
        return {"values": mf.fill_defaults(schema, values), "schema": schema}

    # ---- storage -------------------------------------------------------------------

    @staticmethod
    def _check_key(key: str) -> str:
        if not key or len(key) > MAX_STORAGE_KEY or "\x00" in key:
            raise InvalidStorageKey()
        return key

    async def storage_list(self, user_id: str, plugin_id: str, prefix: str = "") -> dict[str, Any]:
        self._require_local()
        self._entry(plugin_id)
        async with async_unit_of_work(commit=False) as db:
            rows = await AppPluginDatastore(db).list_storage(user_id, plugin_id, prefix)
        return {"items": [{"key": r.key, "size": r.size, "updated_at": r.updated_at} for r in rows]}

    async def storage_get(self, user_id: str, plugin_id: str, key: str) -> dict[str, Any]:
        self._require_local()
        self._entry(plugin_id)
        async with async_unit_of_work(commit=False) as db:
            row = await AppPluginDatastore(db).get_storage(user_id, plugin_id, self._check_key(key))
        if row is None:
            raise StorageKeyNotFound()
        return {"key": key, "value": json.loads(row.value_json)}

    async def storage_put(
        self, user_id: str, plugin_id: str, key: str, value: Any
    ) -> dict[str, Any]:
        self._require_local()
        self._entry(plugin_id)
        self._check_key(key)
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        size = len(encoded.encode("utf-8"))
        if size > MAX_STORAGE_VALUE_BYTES:
            raise StorageQuotaExceeded(
                "The value is larger than the 256 KiB limit",
                extra={"limit": MAX_STORAGE_VALUE_BYTES},
            )
        async with async_unit_of_work() as db:
            store = AppPluginDatastore(db)
            others = await store.storage_total(user_id, plugin_id, excluding_key=key)
            if others + size > MAX_STORAGE_TOTAL_BYTES:
                raise StorageQuotaExceeded(
                    "The plugin storage is over its 10 MiB quota",
                    extra={"limit": MAX_STORAGE_TOTAL_BYTES},
                )
            await store.put_storage(user_id, plugin_id, key, encoded, size, now_ms())
        return {"key": key, "size": size}

    async def storage_delete(self, user_id: str, plugin_id: str, key: str) -> None:
        self._require_local()
        self._entry(plugin_id)
        async with async_unit_of_work() as db:
            await AppPluginDatastore(db).delete_storage(user_id, plugin_id, self._check_key(key))

    # ---- plugin-declared automations -----------------------------------------------

    def _declared_automations(self, plugin_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        entry = self._entry(plugin_id)
        loaded = self._read_cached(self._root_of(entry))
        manifest = loaded.manifest or {}
        declared = [a for a in manifest.get("automations") or [] if isinstance(a, dict)]
        return entry, declared

    async def _sync_automations(
        self,
        user_id: str,
        entry: dict[str, Any],
        *,
        enabled: bool,
        previous: bool = True,
        db: AsyncSession | None = None,
    ) -> None:
        """Create / update / delete the plugin's declared automations for ``user_id``.

        Never fails the install: a problem is written to the plugin's log instead.
        """
        from valuz_agent.modules.app_plugins.automations import specs_from_manifest

        plugin_id = str(entry["id"])
        root = self._root_of(entry)
        manifest = self._read_cached(root).manifest
        if manifest is None:
            return
        specs = specs_from_manifest(manifest)
        if not specs and not previous:
            return  # nothing declared now, nothing to clean up
        try:
            from valuz_agent.modules.automations.app_plugin_support import AppPluginAutomations

            async with _session(db) as session:
                await AppPluginAutomations(session, user_id).sync(
                    plugin_id,
                    mf.pick_text(manifest.get("name")) or plugin_id,
                    specs,
                    root,
                    enabled=enabled,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not sync the automations of %s", plugin_id, exc_info=True)
            logs.append_log(plugin_id, "error", f"could not set up automations: {exc}", "backend")

    async def _set_automations_paused(self, user_id: str, plugin_id: str, *, paused: bool) -> None:
        try:
            from valuz_agent.modules.automations.app_plugin_support import AppPluginAutomations

            async with async_unit_of_work() as db:
                await AppPluginAutomations(db, user_id).set_paused(plugin_id, paused)
        except Exception:  # noqa: BLE001
            logger.warning("could not (un)pause the automations of %s", plugin_id, exc_info=True)

    async def _delete_automations(
        self, user_id: str, plugin_id: str, db: AsyncSession | None = None
    ) -> int:
        try:
            from valuz_agent.modules.automations.app_plugin_support import AppPluginAutomations

            async with _session(db) as session:
                return await AppPluginAutomations(session, user_id).delete_all(plugin_id)
        except Exception:  # noqa: BLE001
            logger.warning("could not delete the automations of %s", plugin_id, exc_info=True)
            return 0

    async def automations(self, user_id: str, plugin_id: str) -> dict[str, Any]:
        self._require_local()
        _, declared = self._declared_automations(plugin_id)
        names = [str(a["name"]) for a in declared if a.get("name")]
        out: list[dict[str, Any]] = []
        from valuz_agent.modules.automations.app_plugin_support import AppPluginAutomations

        async with async_unit_of_work(commit=False) as db:
            bridge = AppPluginAutomations(db, user_id)
            rows = await bridge.ids_by_name(plugin_id, names) if names else {}
            service = await bridge.service() if rows else None
            for decl in declared:
                name = str(decl.get("name"))
                row = rows.get(name)
                latest: dict[str, Any] | None = None
                if row is not None and service is not None:
                    runs = await service.list_runs(row.id, limit=1, user_id=user_id)
                    latest = runs[0].model_dump(mode="json") if runs else None
                out.append(
                    {
                        "name": name,
                        "automation_id": row.id if row is not None else None,
                        "runtime": decl.get("runtime"),
                        "trigger": decl.get("trigger", "manual"),
                        "status": row.status if row is not None else None,
                        "latest_run": latest,
                    }
                )
        return {"automations": out}

    async def run_automation(
        self,
        user_id: str,
        plugin_id: str,
        name: str,
        *,
        input: Any = None,
        wait_seconds: int = 0,
    ) -> dict[str, Any]:
        from valuz_agent.modules.automations.app_plugin_support import AppPluginAutomations
        from valuz_agent.modules.automations.errors import (
            AutomationAlreadyQueued,
            AutomationAlreadyRunning,
            AutomationInputInvalid,
        )

        self._require_local()
        entry, declared = self._declared_automations(plugin_id)
        if name not in {str(a.get("name")) for a in declared}:
            raise PluginAutomationNotFound()
        for attempt in (1, 2):
            async with async_unit_of_work() as db:
                bridge = AppPluginAutomations(db, user_id)
                row = (await bridge.ids_by_name(plugin_id, [name])).get(name)
                if row is not None:
                    service = await bridge.service()
                    try:
                        accepted = await service.run_now(
                            row.id,
                            trigger_type="api",
                            invoked_by_ref=f"plugin:{plugin_id}"[:128],
                            run_input=input,
                            user_id=user_id,
                        )
                    except (AutomationAlreadyRunning, AutomationAlreadyQueued) as exc:
                        raise AutomationBusy(exc.message) from exc
                    except AutomationInputInvalid as exc:
                        raise InvalidAutomationInput(exc.message) from exc
                    result: dict[str, Any] = {
                        "automation_id": accepted.automation_id,
                        "run_id": accepted.run_id,
                        "status": accepted.status,
                    }
                    if wait_seconds > 0:
                        # The runner reads the committed run row from its own session.
                        await db.commit()
                        detail = await service.wait_for_run(
                            row.id, accepted.run_id, timeout_s=float(wait_seconds), user_id=user_id
                        )
                        result["status"] = detail.status
                        result["run"] = detail.model_dump(mode="json")
                    return result
            if attempt == 1:  # not created yet (a failed sync): try once more
                await self._sync_automations(
                    user_id, entry, enabled=plugin_id not in disabled_ids()
                )
        raise PluginAutomationNotFound("The automation has not been created for this user")

    async def _plugin_rows(self, bridge: Any, plugin_id: str) -> list[Any]:
        return list(await bridge.rows(plugin_id))

    async def latest_automation_run(
        self, user_id: str, plugin_id: str, name: str
    ) -> dict[str, Any]:
        from valuz_agent.modules.automations.app_plugin_support import AppPluginAutomations

        self._require_local()
        _, declared = self._declared_automations(plugin_id)
        if name not in {str(a.get("name")) for a in declared}:
            raise PluginAutomationNotFound()
        async with async_unit_of_work(commit=False) as db:
            bridge = AppPluginAutomations(db, user_id)
            row = (await bridge.ids_by_name(plugin_id, [name])).get(name)
            if row is None:
                raise PluginRunNotFound()
            service = await bridge.service()
            runs = await service.list_runs(row.id, limit=1, user_id=user_id)
            if not runs:
                raise PluginRunNotFound()
            detail = await service.get_run_detail(row.id, runs[0].run_id, user_id=user_id)
        return detail.model_dump(mode="json")

    async def automation_run(self, user_id: str, plugin_id: str, run_id: str) -> dict[str, Any]:
        from valuz_agent.modules.automations.app_plugin_support import AppPluginAutomations
        from valuz_agent.modules.automations.errors import AutomationRunNotFound

        self._require_local()
        self._entry(plugin_id)
        async with async_unit_of_work(commit=False) as db:
            bridge = AppPluginAutomations(db, user_id)
            service = await bridge.service()
            for row in await bridge.rows(plugin_id):
                try:
                    detail = await service.get_run_detail(row.id, run_id, user_id=user_id)
                except AutomationRunNotFound:
                    continue
                return detail.model_dump(mode="json")
        raise PluginRunNotFound()

    async def list(self, user_id: str | None) -> dict[str, Any]:
        self._require_local()
        await self._poll_dev()
        state = self._store.read()
        env = await self._env(user_id)
        plugins = [await self._item(entry, env) for _, entry in sorted(state["plugins"].items())]
        safe, reason = self._store.safe_mode()
        return {
            "api_version": mf.API_VERSION,
            "safe_mode": safe,
            "safe_mode_reason": reason,
            "generation": state["generation"],
            "plugins": plugins,
        }


def _loads(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


app_plugin_service = AppPluginService()

__all__ = [
    "AppPluginError",
    "AppPluginService",
    "app_plugin_service",
]

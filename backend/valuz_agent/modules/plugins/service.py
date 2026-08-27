"""PluginService — the local plugin library.

Install / preview / update / enable / disable / uninstall / export of Agent
Plugins (and the Claude / WorkBuddy legacy layouts, via the compat readers in
``manifest.py``). The plugin package itself lives under the user data dir
(``plugins/<name>/`` = ``PLUGIN_ROOT``, ``plugins-data/<name>/`` =
``PLUGIN_DATA``); its members are ordinary library resources:

* skills are imported into the user skill root through the existing
  ``SkillLibraryService`` archive-import pipeline (files kept verbatim,
  ``creation_origin=imported``, library switch on) — slug = directory name;
* MCP servers become connectors through ``ConnectorService.create_connector``
  (``stdio``→``stdio``, ``streamable-http``→``http``, ``sse``→``sse``;
  ``${PLUGIN_ROOT}`` / ``${PLUGIN_DATA}`` expanded in ``args`` / ``env`` /
  ``cwd``; ``command`` one token; ``cwd`` defaults to the plugin root; the two
  reserved variables are injected into the subprocess env).

``PLUGIN_ROOT`` always holds the Agent Plugins layout: an ``agent_plugins``
package is copied verbatim, a legacy ``.claude-plugin`` / ``.codebuddy-plugin``
tree is normalized on the way in (root ``plugin.json`` + ``skills/<slug>/`` +
``mcp.json`` + ``io.valuz.agent/legacy/`` for the client-specific leftovers);
in both cases each skill's frontmatter ``name`` is corrected to its directory
name. ``export`` is therefore a straight zip of ``PLUGIN_ROOT``.

One slug = one copy (design §4.3): a member slug exists once per user; several
plugins referencing it share the resource through ``valuz_plugin_component``
rows. Per member: absent → install; present & same content → link only;
present & different → ``on_conflict`` ``skip`` (default: link + flag
``content_differs``) or ``overwrite``. A plugin's OWN previously-installed,
user-unmodified member is refreshed silently on update. Uninstall is
reference-counted: a member still referenced by another plugin, or that
existed before any plugin linked it (``origin=linked``), is kept.

The service never imports FastAPI; HTTP lives in ``api/routes/plugins.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast
from uuid import uuid4

import httpx

from valuz_agent.i18n import get_locale
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.connectors.service import ConnectorService, ConnectorView, CredEntry
from valuz_agent.modules.marketplace.market_index import MarketIndexUnavailableError
from valuz_agent.modules.plugins.datastore import PluginDatastore
from valuz_agent.modules.plugins.errors import (
    PluginConflict,
    PluginFetchFailed,
    PluginInstallFailed,
    PluginInvalid,
    PluginNotDeletable,
    PluginNotFound,
    PluginSourceUnavailable,
)
from valuz_agent.modules.plugins.manifest import (
    MAX_ARCHIVE_FILE_BYTES,
    MAX_ARCHIVE_FILE_COUNT,
    MAX_ARCHIVE_TOTAL_BYTES,
    VALUZ_EXTENSION_NS,
    LoadedPlugin,
    McpServerSpec,
    PluginArchiveError,
    PluginManifest,
    PluginManifestError,
    SkillSpec,
    _iter_contained_files,
    build_export_zip,
    extract_plugin_zip,
    hash_directory,
    load_plugin_dir,
    materialize_plugin,
    parse_mcp_config,
    resolve_stdio_launch,
    zip_plugin_root,
)
from valuz_agent.modules.plugins.models import (
    PluginAuthor,
    PluginComponentRow,
    PluginConflictMember,
    PluginInstallResult,
    PluginKeptMember,
    PluginMember,
    PluginMembershipRef,
    PluginOnConflict,
    PluginPreview,
    PluginRemovedMember,
    PluginRow,
    PluginSkippedMember,
    PluginSource,
    PluginUninstallResult,
    PluginView,
)
from valuz_agent.modules.skills.models import SkillImportArchiveConfirmRequest
from valuz_agent.modules.skills.service import SkillLibraryService

if TYPE_CHECKING:
    from valuz_agent.modules.marketplace.install_store import MarketplaceInstallStore

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 60.0


class MarketIndexReader(Protocol):
    """The slice of ``MarketIndexClient`` the plugin installer needs."""

    channel: str

    async def item_detail(self, item_id: str, locale: str) -> dict[str, Any]: ...


@dataclass
class _Acquired:
    """A plugin source materialized on local disk, ready to be loaded."""

    root: Path
    source: PluginSource
    source_ref: str | None
    cleanup: Path | None = None
    market_version: str | None = None


@dataclass
class _MemberOutcome:
    origin: str  # installed | linked
    content_differs: bool
    action: Literal["installed", "linked", "overwritten", "unchanged", "conflict", "failed"]
    reason: str | None = None


@dataclass
class _Library:
    """Per-call snapshot of the user's library, so views don't re-query per member."""

    skill_root: Path
    connectors: dict[str, ConnectorView] = field(default_factory=dict)


def _iso(ms: int | None) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _connector_slug(name: str) -> str:
    """Mirror ``ConnectorService.create_connector``'s slug normalization."""
    return re.sub(r"[^a-z0-9_-]", "-", name.lower().strip())[:64]


def _connector_transport(spec: McpServerSpec) -> str:
    return {"stdio": "stdio", "streamable-http": "http", "sse": "sse"}[spec.type]


class PluginService:
    def __init__(
        self,
        *,
        datastore: PluginDatastore,
        skill_service: SkillLibraryService,
        connector_service: ConnectorService,
        market: MarketIndexReader | None = None,
        installs: MarketplaceInstallStore | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._ds = datastore
        self._skills = skill_service
        self._connectors = connector_service
        self._market = market
        self._installs = installs
        self._http = http_client

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_plugins(self, user_id: str) -> list[PluginView]:
        rows = await self._ds.list_plugins(user_id)
        library = await self._library(user_id)
        views: list[PluginView] = []
        for row in rows:
            components = await self._ds.list_components(user_id, row.id)
            views.append(self._view(row, components, library))
        return views

    async def get_plugin(self, user_id: str, plugin_id: str) -> PluginView:
        row = await self._require(user_id, plugin_id)
        components = await self._ds.list_components(user_id, row.id)
        return self._view(row, components, await self._library(user_id))

    async def memberships(
        self, user_id: str, kind: str, slugs: list[str]
    ) -> dict[str, list[PluginMembershipRef]]:
        """``{slug: [{id, name}]}`` — which plugins each library resource belongs
        to (library card badges). Every requested slug is present in the
        result, possibly with an empty list."""
        wanted = [s for s in slugs if s]
        out: dict[str, list[PluginMembershipRef]] = {slug: [] for slug in wanted}
        if not wanted:
            return out
        plugins = {row.id: row for row in await self._ds.list_plugins(user_id)}
        wanted_set = set(wanted)
        for comp in await self._ds.list_all_components(user_id):
            if comp.kind != kind or comp.slug not in wanted_set:
                continue
            plugin = plugins.get(comp.plugin_id)
            if plugin is None:
                continue
            out[comp.slug].append(PluginMembershipRef(id=plugin.id, name=plugin.name))
        return out

    # ------------------------------------------------------------------
    # Preview / install / update
    # ------------------------------------------------------------------

    async def preview(
        self,
        user_id: str,
        *,
        zip_bytes: bytes | None = None,
        path: str | None = None,
        url: str | None = None,
        market_item_id: str | None = None,
    ) -> PluginPreview:
        """Load the source and report what an install WOULD do — no side effects."""
        acquired = await self._acquire(
            user_id, zip_bytes=zip_bytes, path=path, url=url, market_item_id=market_item_id
        )
        try:
            loaded = await asyncio.to_thread(self._load, acquired.root)
            library = await self._library(user_id)
            existing = await self._ds.get_by_name(user_id, loaded.manifest.name)
            members: list[PluginMember] = []
            conflicts: list[PluginConflictMember] = []
            for skill in loaded.skills:
                lib_hash = await self._library_skill_hash(library, skill.slug)
                differs = lib_hash is not None and lib_hash != skill.content_hash
                members.append(
                    PluginMember(
                        kind="skill",
                        slug=skill.slug,
                        name=skill.name,
                        description=skill.description,
                        meta_version=skill.meta_version,
                        content_hash=skill.content_hash,
                        installed=lib_hash is not None,
                        content_differs=differs,
                    )
                )
                if differs:
                    conflicts.append(PluginConflictMember(kind="skill", slug=skill.slug))
            root_hint = fs_registry.plugin_root(user_id, loaded.manifest.name)
            data_hint = fs_registry.plugins_data_root(user_id) / loaded.manifest.name
            for server in loaded.servers:
                slug = _connector_slug(server.name)
                view = library.connectors.get(slug)
                differs = view is not None and not self._connector_matches(
                    view, server, root_hint, data_hint
                )
                members.append(
                    PluginMember(
                        kind="connector",
                        slug=slug,
                        name=server.name,
                        description=None,
                        meta_version=None,
                        content_hash=server.content_hash,
                        installed=view is not None,
                        content_differs=differs,
                    )
                )
                if differs:
                    conflicts.append(PluginConflictMember(kind="connector", slug=slug))
            existing_state: Literal["same_source", "other_source"] | None = None
            if existing is not None:
                existing_state = (
                    "same_source"
                    if self._same_source(existing, acquired.source_ref)
                    else "other_source"
                )
            return PluginPreview(
                manifest=loaded.manifest.to_dict(),
                format=loaded.format,
                composition=loaded.composition,
                members=members,
                conflicts=conflicts,
                skipped=[
                    PluginSkippedMember(kind=f.kind, slug=f.ref, reason=f.reason)
                    for f in loaded.skipped
                ],
                warnings=list(loaded.warnings),
                existing=existing_state,
            )
        finally:
            self._cleanup(acquired)

    async def install(
        self,
        user_id: str,
        *,
        zip_bytes: bytes | None = None,
        path: str | None = None,
        url: str | None = None,
        market_item_id: str | None = None,
        on_conflict: PluginOnConflict = "skip",
        builtin: bool = False,
    ) -> PluginInstallResult:
        """``builtin=True`` installs an app-managed builtin plugin: the row is
        marked ``source="builtin"`` / ``deletable=False`` and its member
        skills land in the read-only official root (design D3) instead of the
        user library."""
        acquired = await self._acquire(
            user_id, zip_bytes=zip_bytes, path=path, url=url, market_item_id=market_item_id
        )
        try:
            loaded = await asyncio.to_thread(self._load, acquired.root)
            result = await self._install_loaded(
                user_id, loaded, acquired, on_conflict=on_conflict, builtin=builtin
            )
        finally:
            self._cleanup(acquired)
        if acquired.source == "market" and self._installs is not None and acquired.source_ref:
            await self._installs.record(
                user_id,
                item_id=acquired.source_ref,
                item_type="plugin",
                installed_ref=result.plugin.name,
                version=acquired.market_version or result.plugin.version or "",
                source_channel=getattr(self._market, "channel", "oss"),
            )
        return result

    async def update(
        self, user_id: str, plugin_id: str, *, on_conflict: PluginOnConflict = "skip"
    ) -> PluginInstallResult:
        """Re-install from ``source_ref`` (market item / URL / local directory)
        with a member diff. A one-off zip upload has nothing to re-fetch."""
        row = await self._require(user_id, plugin_id)
        ref = row.source_ref
        if not ref:
            raise PluginSourceUnavailable(
                f"Plugin '{row.name}' was installed from an uploaded archive; upload the new "
                "archive to update it"
            )
        if ref.startswith("market:"):
            return await self.install(user_id, market_item_id=ref, on_conflict=on_conflict)
        if ref.startswith(("http://", "https://")):
            return await self.install(user_id, url=ref, on_conflict=on_conflict)
        return await self.install(user_id, path=ref, on_conflict=on_conflict)

    # ------------------------------------------------------------------
    # Enable / disable / uninstall / export
    # ------------------------------------------------------------------

    async def set_enabled(self, user_id: str, plugin_id: str, enabled: bool) -> PluginView:
        row = await self._require(user_id, plugin_id)
        components = await self._ds.list_components(user_id, row.id)
        await self._apply_enabled(user_id, components, enabled)
        row.enabled = enabled
        row = await self._ds.update_plugin(row)
        components = await self._ds.list_components(user_id, row.id)
        return self._view(row, components, await self._library(user_id))

    async def uninstall(self, user_id: str, plugin_id: str) -> PluginUninstallResult:
        row = await self._require(user_id, plugin_id)
        if not row.deletable:
            # Builtin plugins are app-managed: disable-able, never deletable
            # (a revoked declaration flips ``deletable`` back — D5).
            raise PluginNotDeletable()
        result = PluginUninstallResult()
        for comp in await self._ds.list_components(user_id, row.id):
            kept_reason = await self._release_member(user_id, row.id, comp)
            member_kind = cast(Literal["skill", "connector"], comp.kind)
            if kept_reason is None:
                result.removed_members.append(PluginRemovedMember(kind=member_kind, slug=comp.slug))
            else:
                result.kept_members.append(
                    PluginKeptMember(kind=member_kind, slug=comp.slug, reason=kept_reason)
                )
        await self._ds.delete_plugin(user_id, row.id)
        if row.source == "market" and self._installs is not None:
            try:
                await self._installs.remove_by_ref(user_id, row.name)
            except Exception:  # noqa: BLE001 — provenance cleanup is best-effort
                logger.warning("plugins: provenance cleanup failed for %s", row.name, exc_info=True)
        for raw_path in (row.root_path, row.data_path):
            target = Path(raw_path)
            if target.exists() and self._is_managed_path(user_id, target):
                await asyncio.to_thread(shutil.rmtree, target, True)
        logger.info(
            "plugins: uninstalled %s (removed=%d kept=%d)",
            row.name,
            len(result.removed_members),
            len(result.kept_members),
        )
        return result

    async def export_zip(self, user_id: str, plugin_id: str) -> tuple[str, bytes]:
        """``(filename, bytes)`` — a straight zip of ``PLUGIN_ROOT``, which is
        always the Agent Plugins layout (legacy imports are materialized into
        it at install). Falls back to re-serializing from the stored manifest +
        the library copies of the member skills if the root went missing."""
        row = await self._require(user_id, plugin_id)
        root = Path(row.root_path)
        if root.is_dir() and (root / "plugin.json").is_file():
            data = await asyncio.to_thread(zip_plugin_root, root)
        else:
            components = await self._ds.list_components(user_id, row.id)
            manifest = PluginManifest.from_dict(json.loads(row.manifest_json))
            skill_root = fs_registry.user_skill_root(user_id=user_id)
            skill_dirs = {
                c.slug: skill_root / c.slug
                for c in components
                if c.kind == "skill" and (skill_root / c.slug / "SKILL.md").is_file()
            }
            mcp_config = json.loads(row.mcp_json) if row.mcp_json else None
            data = await asyncio.to_thread(build_export_zip, manifest, skill_dirs, mcp_config)
        stem = f"{row.name}-{row.version}" if row.version else row.name
        return f"{stem}.zip", data

    # ------------------------------------------------------------------
    # Source acquisition
    # ------------------------------------------------------------------

    async def _acquire(
        self,
        user_id: str,
        *,
        zip_bytes: bytes | None,
        path: str | None,
        url: str | None,
        market_item_id: str | None,
    ) -> _Acquired:
        given = [x for x in (zip_bytes, path, url, market_item_id) if x]
        if len(given) != 1:
            raise PluginInstallFailed(
                "Provide exactly one plugin source: an uploaded zip, 'path', 'url' or "
                "'market_item_id'"
            )
        if zip_bytes is not None:
            return self._acquire_zip(user_id, zip_bytes, source="zip", source_ref=None)
        if path:
            local = Path(path).expanduser()
            if local.is_file():
                if not zipfile.is_zipfile(local):
                    raise PluginInstallFailed(f"'{path}' is neither a directory nor a zip archive")
                return self._acquire_zip(
                    user_id, local.read_bytes(), source="zip", source_ref=str(local.resolve())
                )
            if not local.is_dir():
                raise PluginInstallFailed(f"Plugin path does not exist: {path}")
            return _Acquired(root=local, source="local_dir", source_ref=str(local.resolve()))
        if url:
            if not url.startswith(("http://", "https://")):
                raise PluginInstallFailed("Plugin url must be an http(s) URL")
            data = await self._download(url)
            return self._acquire_zip(user_id, data, source="url", source_ref=url)
        assert market_item_id is not None
        return await self._acquire_market(user_id, market_item_id)

    def _acquire_zip(
        self, user_id: str, data: bytes, *, source: PluginSource, source_ref: str | None
    ) -> _Acquired:
        staging = fs_registry.user_temp_dir(user_id) / "plugin-staging" / uuid4().hex
        try:
            root = extract_plugin_zip(data, staging)
        except PluginArchiveError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise PluginInstallFailed(str(exc)) from exc
        except PluginManifestError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise PluginInvalid(str(exc)) from exc
        return _Acquired(root=root, source=source, source_ref=source_ref, cleanup=staging)

    async def _acquire_market(self, user_id: str, item_id: str) -> _Acquired:
        if self._market is None:
            raise PluginFetchFailed("Marketplace is not configured")
        try:
            raw = await self._market.item_detail(item_id, get_locale())
        except MarketIndexUnavailableError as exc:
            raise PluginFetchFailed(str(exc)) from exc
        if raw.get("type") not in (None, "plugin"):
            raise PluginInvalid(f"Marketplace item {item_id} is not a plugin")
        manifest = raw.get("install_manifest")
        download_url = manifest.get("download_url") if isinstance(manifest, dict) else None
        if not download_url:
            raise PluginFetchFailed(f"Missing plugin download_url for {item_id}")
        data = await self._download(str(download_url))
        acquired = self._acquire_zip(user_id, data, source="market", source_ref=item_id)
        version = raw.get("version")
        acquired.market_version = str(version) if version else None
        return acquired

    async def _download(self, url: str) -> bytes:
        client = self._http or httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True)
        chunks: list[bytes] = []
        total = 0
        try:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_ARCHIVE_TOTAL_BYTES:
                        raise PluginInstallFailed("Plugin archive exceeds the size limit")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise PluginFetchFailed(f"Failed to download plugin: {exc}") from exc
        finally:
            if self._http is None:
                await client.aclose()
        return b"".join(chunks)

    @staticmethod
    def _cleanup(acquired: _Acquired) -> None:
        if acquired.cleanup is not None:
            shutil.rmtree(acquired.cleanup, ignore_errors=True)

    @staticmethod
    def _load(root: Path) -> LoadedPlugin:
        try:
            return load_plugin_dir(root)
        except PluginManifestError as exc:
            raise PluginInvalid(str(exc)) from exc

    # ------------------------------------------------------------------
    # Install core
    # ------------------------------------------------------------------

    @staticmethod
    def _same_source(existing: PluginRow, source_ref: str | None) -> bool:
        """Same-name plugin from the same origin → an update; from a different
        origin → a conflict. Two ad-hoc uploads (no ref) count as the same."""
        return (existing.source_ref or None) == (source_ref or None)

    async def _install_loaded(
        self,
        user_id: str,
        staged: LoadedPlugin,
        acquired: _Acquired,
        *,
        on_conflict: PluginOnConflict,
        builtin: bool = False,
    ) -> PluginInstallResult:
        name = staged.manifest.name
        existing = await self._ds.get_by_name(user_id, name)
        if existing is not None and not builtin and not self._same_source(
            existing, acquired.source_ref
        ):
            raise PluginConflict(
                f"Plugin '{name}' is already installed from another source "
                f"({existing.source}); uninstall it first"
            )
        if existing is not None and builtin and existing.source != "builtin":
            # A user install already claimed the name — the builtin sync must
            # not clobber it (create-only conservatism; logged by the caller).
            raise PluginConflict(
                f"Plugin '{name}' is already installed from source '{existing.source}'"
            )
        source: PluginSource = (
            "builtin"
            if builtin
            else (acquired.source if staged.format == "agent_plugins" else staged.format)
        )
        warnings: list[str] = []

        # 1. Materialize PLUGIN_ROOT in the Agent Plugins layout (atomic swap;
        #    PLUGIN_DATA is preserved). Legacy layouts are normalized here;
        #    frontmatter names are corrected to the directory names.
        await asyncio.to_thread(self._enforce_caps, staged.root)
        root = fs_registry.plugin_root(user_id, name)
        data_dir = fs_registry.plugin_data_dir(user_id, name)
        tmp_root = root.parent / f".{name}.installing-{uuid4().hex[:8]}"
        try:
            warnings.extend(await asyncio.to_thread(materialize_plugin, staged, tmp_root))
            if root.exists():
                await asyncio.to_thread(shutil.rmtree, root)
            tmp_root.rename(root)
        except Exception:
            shutil.rmtree(tmp_root, ignore_errors=True)
            raise
        loaded = await asyncio.to_thread(self._load, root)
        warnings.extend(w for w in staged.warnings if w not in warnings)
        warnings.extend(w for w in loaded.warnings if w not in warnings)

        # 2. Members.
        prev_components: dict[tuple[str, str], PluginComponentRow] = {}
        prev_mcp: dict[str, McpServerSpec] = {}
        if existing is not None:
            for comp in await self._ds.list_components(user_id, existing.id):
                prev_components[(comp.kind, comp.slug)] = comp
            prev_mcp = self._servers_from_json(existing.mcp_json)
        library = await self._library(user_id)
        conflicts: list[PluginConflictMember] = []
        skipped: list[PluginSkippedMember] = [
            PluginSkippedMember(kind=f.kind, slug=f.ref, reason=f.reason)
            for f in (*staged.skipped, *loaded.skipped)
        ]
        member_rows: list[tuple[PluginComponentRow, _MemberOutcome]] = []
        changed = False
        seen: set[tuple[str, str]] = set()

        for skill in loaded.skills:
            prev = prev_components.get(("skill", skill.slug))
            if builtin:
                outcome = await self._place_skill_builtin(user_id, skill)
            else:
                outcome = await self._place_skill(user_id, library, skill, prev, on_conflict)
            if outcome.action == "failed":
                skipped.append(
                    PluginSkippedMember(kind="skill", slug=skill.slug, reason=outcome.reason or "")
                )
                if prev is not None:
                    seen.add(("skill", skill.slug))  # keep the previous link untouched
                continue
            if outcome.action == "conflict":
                conflicts.append(PluginConflictMember(kind="skill", slug=skill.slug))
            if outcome.action in ("installed", "overwritten"):
                changed = True
            seen.add(("skill", skill.slug))
            member_rows.append(
                (
                    self._component_row(
                        prev,
                        kind="skill",
                        slug=skill.slug,
                        name=skill.name,
                        description=skill.description,
                        meta_version=skill.meta_version,
                        content_hash=skill.content_hash,
                        outcome=outcome,
                    ),
                    outcome,
                )
            )

        for server in loaded.servers:
            slug = _connector_slug(server.name)
            prev = prev_components.get(("connector", slug))
            outcome = await self._place_connector(
                user_id,
                library,
                server,
                prev,
                prev_mcp.get(server.name),
                on_conflict,
                plugin_name=name,
                plugin_root=root,
                plugin_data=data_dir,
            )
            if outcome.action == "failed":
                skipped.append(
                    PluginSkippedMember(kind="connector", slug=slug, reason=outcome.reason or "")
                )
                if prev is not None:
                    seen.add(("connector", slug))  # keep the previous link untouched
                continue
            if outcome.action == "conflict":
                conflicts.append(PluginConflictMember(kind="connector", slug=slug))
            if outcome.action in ("installed", "overwritten"):
                changed = True
            seen.add(("connector", slug))
            member_rows.append(
                (
                    self._component_row(
                        prev,
                        kind="connector",
                        slug=slug,
                        name=server.name,
                        description=None,
                        meta_version=None,
                        content_hash=server.content_hash,
                        outcome=outcome,
                    ),
                    outcome,
                )
            )

        # 3. Plugin row.
        manifest_json = json.dumps(loaded.manifest.to_dict(), ensure_ascii=False)
        mcp_config = loaded.mcp_config()
        mcp_json = json.dumps(mcp_config, ensure_ascii=False) if mcp_config else None
        if existing is None:
            row = await self._ds.create_plugin(
                user_id,
                PluginRow(
                    name=name,
                    version=loaded.manifest.version,
                    description=loaded.manifest.description,
                    source=source,
                    source_ref=acquired.source_ref,
                    format=staged.format,
                    manifest_json=manifest_json,
                    mcp_json=mcp_json,
                    root_path=str(root),
                    data_path=str(data_dir),
                    enabled=True,
                    deletable=not builtin,
                ),
            )
            status: Literal["installed", "updated", "already_installed"] = "installed"
        else:
            row = existing
            if builtin:
                row.deletable = False
            if (
                row.version != loaded.manifest.version
                or row.manifest_json != manifest_json
                or row.mcp_json != mcp_json
            ):
                changed = True
            row.version = loaded.manifest.version
            row.description = loaded.manifest.description
            row.source = source
            row.source_ref = acquired.source_ref
            row.format = staged.format
            row.manifest_json = manifest_json
            row.mcp_json = mcp_json
            row.root_path = str(root)
            row.data_path = str(data_dir)
            row = await self._ds.update_plugin(row)
            status = "updated"

        for comp, _outcome in member_rows:
            comp.plugin_id = row.id
            if comp.id and (comp.kind, comp.slug) in prev_components:
                await self._ds.update_component(comp)
            else:
                await self._ds.create_component(user_id, comp)

        # 4. Members the new version no longer declares → reference-counted release.
        for key, comp in prev_components.items():
            if key in seen:
                continue
            changed = True
            kept = await self._release_member(user_id, row.id, comp)
            await self._ds.delete_component(user_id, comp.id)
            warnings.append(
                f"member {comp.kind} '{comp.slug}' is no longer part of the plugin: "
                + ("removed" if kept is None else f"kept ({kept})")
            )

        if existing is not None and not changed:
            status = "already_installed"
        if not row.enabled:
            # A disabled plugin stays disabled — freshly installed members follow it.
            await self._apply_enabled(
                user_id, await self._ds.list_components(user_id, row.id), False
            )

        components = await self._ds.list_components(user_id, row.id)
        view = self._view(row, components, await self._library(user_id))
        logger.info(
            "plugins: %s %s (skills=%d connectors=%d conflicts=%d skipped=%d)",
            status,
            name,
            view.skill_count,
            view.connector_count,
            len(conflicts),
            len(skipped),
        )
        return PluginInstallResult(
            plugin=view, status=status, skipped=skipped, conflicts=conflicts, warnings=warnings
        )

    @staticmethod
    def _enforce_caps(root: Path) -> None:
        """The zip path is capped at extraction; a directory source gets the
        same file-count / size caps so a stray ``node_modules`` can't be copied
        wholesale into the plugin root."""
        count = 0
        total = 0
        for file in _iter_contained_files(root):
            count += 1
            if count > MAX_ARCHIVE_FILE_COUNT:
                raise PluginInstallFailed(f"Plugin exceeds the {MAX_ARCHIVE_FILE_COUNT}-file limit")
            size = file.stat().st_size
            if size > MAX_ARCHIVE_FILE_BYTES:
                raise PluginInstallFailed(f"File '{file.name}' exceeds the per-file size limit")
            total += size
            if total > MAX_ARCHIVE_TOTAL_BYTES:
                raise PluginInstallFailed("Plugin exceeds the total size limit")

    @staticmethod
    def _component_row(
        prev: PluginComponentRow | None,
        *,
        kind: str,
        slug: str,
        name: str,
        description: str | None,
        meta_version: str | None,
        content_hash: str,
        outcome: _MemberOutcome,
    ) -> PluginComponentRow:
        row = prev if prev is not None else PluginComponentRow(plugin_id="", kind=kind, slug=slug)
        row.name = name
        row.description = description
        row.meta_version = meta_version
        row.content_hash = content_hash
        row.origin = outcome.origin
        row.content_differs = outcome.content_differs
        if prev is None:
            row.disabled_by_plugin = False
        return row

    @staticmethod
    def _servers_from_json(raw: str | None) -> dict[str, McpServerSpec]:
        if not raw:
            return {}
        try:
            servers, _failures, _top = parse_mcp_config(json.loads(raw))
        except ValueError:
            return {}
        return {s.name: s for s in servers}

    # -- skills --------------------------------------------------------------

    async def _place_skill(
        self,
        user_id: str,
        library: _Library,
        skill: SkillSpec,
        prev: PluginComponentRow | None,
        on_conflict: PluginOnConflict,
    ) -> _MemberOutcome:
        lib_dir = library.skill_root / skill.slug
        lib_hash = await self._library_skill_hash(library, skill.slug)
        prev_origin = prev.origin if prev is not None else "linked"
        try:
            if lib_hash is None:
                if lib_dir.exists():
                    # A leftover directory without a SKILL.md would make the
                    # importer allocate ``<slug>-2``; it is not a skill — clear it.
                    await asyncio.to_thread(shutil.rmtree, lib_dir, True)
                await self._import_skill(user_id, skill)
                return _MemberOutcome(origin="installed", content_differs=False, action="installed")
            if lib_hash == skill.content_hash:
                return _MemberOutcome(origin=prev_origin, content_differs=False, action="unchanged")
            plugin_owned_unmodified = (
                prev is not None and prev.origin == "installed" and prev.content_hash == lib_hash
            )
            if plugin_owned_unmodified or on_conflict == "overwrite":
                await self._replace_skill(user_id, skill, lib_dir)
                return _MemberOutcome(
                    origin=prev_origin, content_differs=False, action="overwritten"
                )
            return _MemberOutcome(origin=prev_origin, content_differs=True, action="conflict")
        except Exception as exc:  # noqa: BLE001 — one bad member must not sink the install
            logger.exception("plugins: failed to install skill %s", skill.slug)
            return _MemberOutcome(
                origin=prev_origin, content_differs=False, action="failed", reason=str(exc)
            )

    async def _place_skill_builtin(self, user_id: str, skill: SkillSpec) -> _MemberOutcome:
        """Land a builtin plugin's member skill in the read-only official root
        (design D3), with the same ``.bundled-version`` content-hash
        convergence the bundled skill trees use — an unchanged pass leaves the
        tree byte-for-byte identical (§7.3)."""
        from valuz_agent.integrations.skills_official_bootstrap import (
            BUNDLED_VERSION_FILE,
            _copy_skill,
            _hash_directory,
        )

        dest_root = fs_registry.official_skill_root(user_id=user_id)
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / skill.slug
        try:
            version_hash = await asyncio.to_thread(_hash_directory, skill.path)
            marker = dest / BUNDLED_VERSION_FILE
            if dest.exists() and marker.exists():
                if marker.read_text(encoding="utf-8").strip() == version_hash:
                    return _MemberOutcome(
                        origin="installed", content_differs=False, action="unchanged"
                    )
            await asyncio.to_thread(_copy_skill, skill.path, dest, version_hash)
            return _MemberOutcome(origin="installed", content_differs=False, action="installed")
        except Exception as exc:  # noqa: BLE001 — one bad member must not sink the install
            logger.exception("plugins: failed to install builtin skill %s", skill.slug)
            return _MemberOutcome(
                origin="installed", content_differs=False, action="failed", reason=str(exc)
            )

    async def _replace_skill(self, user_id: str, skill: SkillSpec, lib_dir: Path) -> None:
        """Overwrite the library copy: park the old directory in the user temp
        root (never inside the skill root, where a scan would index it), import
        the new one, and put the old one back if the import fails."""
        backup_root = fs_registry.user_temp_dir(user_id) / "plugin-skill-backup"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = backup_root / f"{skill.slug}-{uuid4().hex[:8]}"
        await asyncio.to_thread(shutil.move, str(lib_dir), str(backup))
        try:
            await self._import_skill(user_id, skill)
        except Exception:
            shutil.rmtree(lib_dir, ignore_errors=True)
            await asyncio.to_thread(shutil.move, str(backup), str(lib_dir))
            raise
        shutil.rmtree(backup, ignore_errors=True)

    async def _import_skill(self, user_id: str, skill: SkillSpec) -> None:
        """Import ONE skill directory through the skill library's archive
        pipeline (verbatim files, ``imported`` origin, library switch on)."""
        tmp_dir = fs_registry.user_temp_dir(user_id) / "plugin-skill-import"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        zip_path = tmp_dir / f"{skill.slug}-{uuid4().hex[:8]}.zip"

        def _build() -> None:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in _iter_contained_files(skill.path, ignore_names=skill.ignore_names):
                    rel = file.relative_to(skill.path).as_posix()
                    zf.write(file, f"{skill.slug}/{rel}")

        await asyncio.to_thread(_build)
        try:
            preview = await self._skills.import_archive_preview(
                user_id, str(zip_path), "user", None
            )
            view = await self._skills.confirm_archive_import(
                user_id,
                SkillImportArchiveConfirmRequest(
                    preview_id=preview.preview_id, target_scope="user"
                ),
            )
        finally:
            zip_path.unlink(missing_ok=True)
        expected = fs_registry.user_skill_root(user_id=user_id) / skill.slug
        if Path(view.path).resolve() != expected.resolve():
            logger.warning(
                "plugins: skill %s landed at %s instead of %s", skill.slug, view.path, expected
            )

    @staticmethod
    async def _library_skill_hash(library: _Library, slug: str) -> str | None:
        lib_dir = library.skill_root / slug
        if not (lib_dir / "SKILL.md").is_file():
            return None
        return await asyncio.to_thread(hash_directory, lib_dir)

    async def _find_user_skill_row(self, user_id: str, slug: str) -> Any | None:
        """The ``valuz_skill_index`` row for the user-root copy of ``slug``
        (matched by source path, never by slug alone — a same-slug official
        copy must not be mistaken for the plugin's)."""
        target = (fs_registry.user_skill_root(user_id=user_id) / slug).resolve()
        for row in await self._skills.list_indexed_skills(user_id):
            source_path = getattr(row, "source_path", None)
            if not source_path:
                continue
            try:
                if Path(source_path).resolve() == target:
                    return row
            except OSError:
                continue
        return None

    # -- connectors ----------------------------------------------------------

    async def _place_connector(
        self,
        user_id: str,
        library: _Library,
        server: McpServerSpec,
        prev: PluginComponentRow | None,
        prev_spec: McpServerSpec | None,
        on_conflict: PluginOnConflict,
        *,
        plugin_name: str,
        plugin_root: Path,
        plugin_data: Path,
    ) -> _MemberOutcome:
        slug = _connector_slug(server.name)
        prev_origin = prev.origin if prev is not None else "linked"
        launch = None
        if server.type == "stdio":
            launch, reason = resolve_stdio_launch(
                server, plugin_root=plugin_root, plugin_data=plugin_data
            )
            if launch is None:
                return _MemberOutcome(
                    origin=prev_origin, content_differs=False, action="failed", reason=reason
                )
        existing = library.connectors.get(slug)
        try:
            if existing is None:
                await self._create_connector(
                    user_id, server, slug, plugin_name, plugin_root, plugin_data
                )
                return _MemberOutcome(origin="installed", content_differs=False, action="installed")
            if self._connector_matches(existing, server, plugin_root, plugin_data):
                return _MemberOutcome(origin=prev_origin, content_differs=False, action="unchanged")
            plugin_owned_unmodified = (
                prev is not None
                and prev.origin == "installed"
                and prev_spec is not None
                and self._connector_matches(existing, prev_spec, plugin_root, plugin_data)
            )
            if plugin_owned_unmodified or on_conflict == "overwrite":
                await self._overwrite_connector(
                    user_id, existing, server, slug, plugin_name, plugin_root, plugin_data
                )
                return _MemberOutcome(
                    origin=prev_origin, content_differs=False, action="overwritten"
                )
            return _MemberOutcome(origin=prev_origin, content_differs=True, action="conflict")
        except Exception as exc:  # noqa: BLE001 — one bad member must not sink the install
            logger.exception("plugins: failed to install connector %s", slug)
            return _MemberOutcome(
                origin=prev_origin, content_differs=False, action="failed", reason=str(exc)
            )
        finally:
            # Refresh the snapshot so a later member (or the view) sees the change.
            library.connectors = {
                v.slug: v for v in await self._connectors.list_connectors(user_id)
            }

    async def _create_connector(
        self,
        user_id: str,
        server: McpServerSpec,
        slug: str,
        plugin_name: str,
        plugin_root: Path,
        plugin_data: Path,
    ) -> ConnectorView:
        description = f"MCP server '{server.name}' from plugin {plugin_name}"
        if server.type == "stdio":
            launch, reason = resolve_stdio_launch(
                server, plugin_root=plugin_root, plugin_data=plugin_data
            )
            if launch is None:
                raise PluginInstallFailed(reason or "invalid stdio server")
            return await self._connectors.create_connector(
                user_id,
                slug=slug,
                display_name=server.name,
                description=description,
                transport="stdio",
                command=launch.command,
                args=launch.args,
                working_dir=launch.cwd,
                env=launch.env,
            )
        return await self._connectors.create_connector(
            user_id,
            slug=slug,
            display_name=server.name,
            description=description,
            transport="http" if server.type == "streamable-http" else "sse",
            url=server.url,
            auth_type="none",
            headers=[CredEntry(key=k, secret=False, value=v) for k, v in server.headers.items()],
        )

    async def _overwrite_connector(
        self,
        user_id: str,
        existing: ConnectorView,
        server: McpServerSpec,
        slug: str,
        plugin_name: str,
        plugin_root: Path,
        plugin_data: Path,
    ) -> None:
        if existing.transport != _connector_transport(server):
            # Transport changes can't be patched in place — recreate.
            await self._connectors.delete_connector(user_id, existing.id)
            await self._create_connector(
                user_id, server, slug, plugin_name, plugin_root, plugin_data
            )
            return
        if server.type == "stdio":
            launch, reason = resolve_stdio_launch(
                server, plugin_root=plugin_root, plugin_data=plugin_data
            )
            if launch is None:
                raise PluginInstallFailed(reason or "invalid stdio server")
            await self._connectors.update_connector(
                user_id,
                existing.id,
                command=launch.command,
                args=launch.args,
                working_dir=launch.cwd,
                env=launch.env,
            )
            return
        await self._connectors.update_connector(
            user_id,
            existing.id,
            url=server.url,
            headers=[CredEntry(key=k, secret=False, value=v) for k, v in server.headers.items()],
        )

    @staticmethod
    def _connector_matches(
        view: ConnectorView, spec: McpServerSpec, plugin_root: Path, plugin_data: Path
    ) -> bool:
        """The installed connector still reflects ``spec`` (definition-level
        comparison; secret header values and stdio env are not visible on the
        view and are not compared)."""
        if view.transport != _connector_transport(spec):
            return False
        if spec.type == "stdio":
            launch, _reason = resolve_stdio_launch(
                spec, plugin_root=plugin_root, plugin_data=plugin_data
            )
            if launch is None:
                return False
            return (
                (view.command or "") == launch.command
                and list(view.args) == launch.args
                and (view.working_dir or "") == launch.cwd
            )
        headers = {h.key: h.value for h in view.headers if not h.secret and h.value is not None}
        return (view.url or "") == (spec.url or "") and headers == spec.headers

    # ------------------------------------------------------------------
    # Reference counting / enable state
    # ------------------------------------------------------------------

    async def _release_member(
        self, user_id: str, plugin_id: str, comp: PluginComponentRow
    ) -> Literal["referenced_by_other_plugin", "standalone"] | None:
        """Drop ``comp`` from ``plugin_id``: returns the keep reason, or ``None``
        when the library resource itself was removed."""
        others = [
            o
            for o in await self._ds.list_components_by_member(user_id, comp.kind, comp.slug)
            if o.plugin_id != plugin_id
        ]
        if others:
            if comp.origin == "installed" and not any(o.origin == "installed" for o in others):
                # Hand plugin ownership to another referrer so the resource is
                # still reclaimed once the LAST plugin referencing it goes.
                others[0].origin = "installed"
                await self._ds.update_component(others[0])
            return "referenced_by_other_plugin"
        if comp.origin != "installed":
            return "standalone"
        try:
            if comp.kind == "skill":
                await self._remove_skill(user_id, comp.slug)
            else:
                view = next(
                    (
                        v
                        for v in await self._connectors.list_connectors(user_id)
                        if v.slug == comp.slug
                    ),
                    None,
                )
                if view is not None:
                    await self._connectors.delete_connector(user_id, view.id)
        except Exception:  # noqa: BLE001 — best-effort resource cleanup
            logger.exception("plugins: failed to remove %s %s", comp.kind, comp.slug)
        return None

    async def _remove_skill(self, user_id: str, slug: str) -> None:
        row = await self._find_user_skill_row(user_id, slug)
        if row is not None:
            try:
                await self._skills.delete_skill(user_id, f"user:{slug}", mode="confirm")
                return
            except KeyError:
                pass  # not in the catalog (stale index) — fall through to the disk cleanup
        lib_dir = fs_registry.user_skill_root(user_id=user_id) / slug
        if lib_dir.exists():
            await asyncio.to_thread(shutil.rmtree, lib_dir, True)
            try:
                await self._skills.startup_scan(user_id)
            except Exception:  # noqa: BLE001
                logger.debug("plugins: rescan after skill removal failed", exc_info=True)

    async def _apply_enabled(
        self, user_id: str, components: list[PluginComponentRow], enabled: bool
    ) -> None:
        connectors = {v.slug: v for v in await self._connectors.list_connectors(user_id)}
        for comp in components:
            if comp.kind == "skill":
                row = await self._find_user_skill_row(user_id, comp.slug)
                if row is None:
                    continue
                currently = bool(getattr(row, "library_enabled", True))
                if not enabled:
                    if currently:
                        await self._set_skill_enabled(user_id, comp.slug, False)
                        comp.disabled_by_plugin = True
                    else:
                        comp.disabled_by_plugin = False
                elif comp.disabled_by_plugin:
                    await self._set_skill_enabled(user_id, comp.slug, True)
                    comp.disabled_by_plugin = False
            else:
                view = connectors.get(comp.slug)
                if view is None:
                    continue
                if not enabled:
                    if view.enabled:
                        await self._connectors.set_enabled(user_id, view.id, enabled=False)
                        comp.disabled_by_plugin = True
                    else:
                        comp.disabled_by_plugin = False
                elif comp.disabled_by_plugin:
                    await self._connectors.set_enabled(user_id, view.id, enabled=True)
                    comp.disabled_by_plugin = False
            await self._ds.update_component(comp)

    async def _set_skill_enabled(self, user_id: str, slug: str, enabled: bool) -> None:
        try:
            await self._skills.set_library_enabled(user_id, f"user:{slug}", enabled)
        except KeyError:
            logger.warning("plugins: skill %s not in the catalog; cannot toggle", slug)

    # ------------------------------------------------------------------
    # Views
    # ------------------------------------------------------------------

    async def _require(self, user_id: str, plugin_id: str) -> PluginRow:
        row = await self._ds.get_by_id(user_id, plugin_id)
        if row is None:
            raise PluginNotFound(f"Plugin not found: {plugin_id}")
        return row

    async def _library(self, user_id: str) -> _Library:
        return _Library(
            skill_root=fs_registry.user_skill_root(user_id=user_id),
            connectors={v.slug: v for v in await self._connectors.list_connectors(user_id)},
        )

    def _view(
        self, row: PluginRow, components: list[PluginComponentRow], library: _Library
    ) -> PluginView:
        try:
            manifest = json.loads(row.manifest_json)
        except ValueError:
            manifest = {}
        if not isinstance(manifest, dict):
            manifest = {}
        author_raw = manifest.get("author")
        author = PluginAuthor.model_validate(author_raw) if isinstance(author_raw, dict) else None
        members: list[PluginMember] = []
        for comp in components:
            if comp.kind == "skill":
                installed = (library.skill_root / comp.slug / "SKILL.md").is_file()
                if not installed and row.source == "builtin":
                    # Builtin plugin members land in the official root (D3).
                    installed = (
                        fs_registry.official_skill_root(user_id=row.user_id)
                        / comp.slug
                        / "SKILL.md"
                    ).is_file()
            else:
                installed = comp.slug in library.connectors
            members.append(
                PluginMember(
                    kind=cast(Literal["skill", "connector"], comp.kind),
                    slug=comp.slug,
                    name=comp.name,
                    description=comp.description,
                    meta_version=comp.meta_version,
                    content_hash=comp.content_hash,
                    installed=installed,
                    content_differs=bool(comp.content_differs),
                )
            )
        skill_count = sum(1 for m in members if m.kind == "skill")
        connector_count = len(members) - skill_count
        keywords = manifest.get("keywords")
        return PluginView(
            id=row.id,
            name=row.name,
            version=row.version,
            description=row.description,
            author=author,
            homepage=_str_or_none(manifest.get("homepage")),
            repository=_str_or_none(manifest.get("repository")),
            license=_str_or_none(manifest.get("license")),
            keywords=[str(k) for k in keywords] if isinstance(keywords, list) else [],
            source=cast(PluginSource, row.source),
            source_ref=row.source_ref,
            composition="with_connectors" if row.mcp_json else "skills_only",
            enabled=bool(row.enabled),
            deletable=bool(row.deletable),
            members=members,
            skill_count=skill_count,
            connector_count=connector_count,
            root_path=row.root_path,
            installed_at=_iso(row.created_at),
            updated_at=_iso(row.updated_at),
            update_available=None,
        )

    @staticmethod
    def _is_managed_path(user_id: str, target: Path) -> bool:
        """Never rmtree anything outside the user's plugins / plugins-data roots."""
        for base in (fs_registry.plugins_root(user_id), fs_registry.plugins_data_root(user_id)):
            try:
                target.resolve().relative_to(base.resolve())
                return True
            except (ValueError, OSError):
                continue
        return False


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


__all__ = ["MarketIndexReader", "PluginService", "VALUZ_EXTENSION_NS"]

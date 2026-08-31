"""MarketplaceService — reads and install-orchestrates against the market
index, the PRIMARY marketplace data source (see
``docs/cloud-marketplace/design/oss.md``).

- Discovery (categories / items / item detail) is a straight pass-through to
  :class:`~valuz_agent.modules.marketplace.market_index.MarketIndexClient`,
  Pydantic-validated into the ``Marketplace*`` DTOs. The only local work is
  recomputing ``installed`` against this user's library (skills / agents /
  connectors) — the index always reports ``installed: false``.
- Install (``market:skill:*`` / ``market:agent:*`` / ``market:team:*``) reads
  the item's ``install_manifest`` from the index detail, then delegates to
  the existing local pipelines: the skill URL-import pipeline
  (``SkillLibraryService.import_url_preview`` / ``confirm_url_import``),
  ``AgentService.create_agent``, and ``AgentPackService.import_manifest``.
  A successful install writes a provenance row via
  :class:`~valuz_agent.modules.marketplace.install_store.MarketplaceInstallStore`
  (write-only in this phase — see that module's docstring).
- ``market:connector:*`` items are never installed through this service —
  the frontend reads ``connector_config`` off the item detail and calls
  ``POST /v1/connectors`` directly (unchanged, pre-existing mechanism).
- ``market:plugin:*`` items (Agent Plugins packages) delegate to
  :class:`~valuz_agent.modules.plugins.service.PluginService`, which downloads
  the zip through the item's ``install_manifest.download_url``, installs the
  plugin + its member skills / connectors and records the provenance row.

Index outages must never blank the marketplace: list/category reads degrade
to empty results with ``degraded: true``; detail/install reads raise
``MarketplaceUpstreamError`` (502) — UNLESS ``Settings.marketplace_direct_fallback``
is on, in which case it falls through to
:mod:`valuz_agent.modules.marketplace.direct_fallback` — SkillHub / ModelScope
for ``skill``/``connector``, the built-in agent-template resource file /
agent-pack manifests for ``agent_template``/``agent_team_template`` (the
pre-market-index sources) — and is still marked ``degraded: true`` (non-channel
content, even the local built-in one). With the flag off, everything degrades
to empty exactly as it did with no fallback at all. See ``direct_fallback``'s
module docstring for the full contract.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from valuz_agent.i18n import get_locale
from valuz_agent.infra.config import settings
from valuz_agent.modules.agent_packs.manifest import AgentPackManifest
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.service import AgentService, MemberAlreadyExistsError
from valuz_agent.modules.marketplace import direct_fallback
from valuz_agent.modules.marketplace.errors import (
    MarketplaceItemNotFound,
    MarketplaceUpstreamError,
)
from valuz_agent.modules.marketplace.install_store import MarketplaceInstallStore
from valuz_agent.modules.marketplace.market_index import (
    MarketIndexClient,
    MarketIndexUnavailableError,
)
from valuz_agent.modules.marketplace.models import (
    MarketplaceCategoryList,
    MarketplaceInstallResult,
    MarketplaceItem,
    MarketplaceItemDetail,
    MarketplaceItemList,
)
from valuz_agent.modules.marketplace.modelscope import ModelScopeClient
from valuz_agent.modules.marketplace.skillhub import SkillHubClient
from valuz_agent.modules.packs_common.manifest import PackManifest, resolve_text
from valuz_agent.modules.skills.models import SkillImportUrlConfirmRequest
from valuz_agent.modules.skills.service import SkillLibraryService

if TYPE_CHECKING:
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.modules.plugins.service import PluginService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Direct-fallback client singletons — lazily constructed, process-wide (same
# style as the market index client factory in ``api/routes/marketplace.py``).
# Only ever touched when the market index is unreachable AND
# ``settings.marketplace_direct_fallback`` is on.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _skillhub_client() -> SkillHubClient:
    return SkillHubClient()


@lru_cache(maxsize=1)
def _modelscope_client() -> ModelScopeClient:
    return ModelScopeClient()


class MarketplaceService:
    def __init__(
        self,
        *,
        index: MarketIndexClient,
        skill_service: SkillLibraryService,
        agent_service: AgentService,
        pack_service: AgentPackService,
        installs: MarketplaceInstallStore,
        connector_service: ConnectorService | None = None,
        plugin_service: PluginService | None = None,
    ) -> None:
        self._index = index
        self._skills = skill_service
        self._agents = agent_service
        self._packs = pack_service
        self._installs = installs
        self._connectors = connector_service
        self._plugins = plugin_service

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    async def list_categories(self, user_id: str, kind: str) -> MarketplaceCategoryList:
        try:
            payload = await self._index.categories(kind, get_locale())
        except MarketIndexUnavailableError:
            fallback = await self._fallback_categories(user_id, kind)
            if fallback is not None:
                return fallback
            return MarketplaceCategoryList(categories=[], degraded=True)
        return MarketplaceCategoryList.model_validate(payload)

    async def _fallback_categories(self, user_id: str, kind: str) -> MarketplaceCategoryList | None:
        """``None`` means the flag is off — the caller degrades to empty in
        that case. A non-``None`` result is always marked ``degraded``
        regardless of the fallback read's own success, since it isn't
        channel-managed content."""
        if not settings.marketplace_direct_fallback:
            return None
        if kind == "skill":
            result = await direct_fallback.skill_categories(_skillhub_client())
        elif kind == "connector":
            result = direct_fallback.connector_categories()
        elif kind == "agent":
            packs = await self._packs.list_packs(user_id)
            result = direct_fallback.agent_categories(packs)
        else:
            return None
        result.degraded = True
        return result

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    async def list_items(
        self,
        user_id: str,
        *,
        type_: str,
        category: str | None = None,
        subcategory: str | None = None,
        scenario: str | None = None,
        source: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 30,
        composition: str | None = None,
    ) -> MarketplaceItemList:
        try:
            payload = await self._index.list_items(
                type_=type_,
                category=category,
                subcategory=subcategory,
                scenario=scenario,
                source=source,
                q=q,
                page=page,
                page_size=page_size,
                locale=get_locale(),
                composition=composition,
            )
        except MarketIndexUnavailableError:
            fallback = await self._fallback_list_items(
                user_id,
                type_=type_,
                category=category,
                subcategory=subcategory,
                source=source,
                q=q,
                page=page,
                page_size=page_size,
            )
            if fallback is not None:
                return fallback
            return MarketplaceItemList(
                items=[], total=0, page=page, page_size=page_size, degraded=True
            )
        result = MarketplaceItemList.from_index_payload(payload)
        installed_refs = await self._installed_refs(user_id, type_)
        for item in result.items:
            item.installed = self._recompute_installed(item, installed_refs)
        return result

    async def _fallback_list_items(
        self,
        user_id: str,
        *,
        type_: str,
        category: str | None,
        subcategory: str | None,
        source: str | None,
        q: str | None,
        page: int,
        page_size: int,
    ) -> MarketplaceItemList | None:
        """``None`` means the flag is off. A non-``None`` result is always
        marked ``degraded``."""
        if not settings.marketplace_direct_fallback:
            return None
        if type_ == "skill":
            installed = await self._installed_refs(user_id, "skill")
            result = await direct_fallback.list_skills(
                _skillhub_client(),
                category=category,
                subcategory=subcategory,
                q=q,
                page=page,
                page_size=page_size,
                installed_slugs=installed,
            )
        elif type_ == "connector":
            if source not in (None, "modelscope"):
                return MarketplaceItemList(
                    items=[], total=0, page=page, page_size=page_size, degraded=True
                )
            installed = await self._installed_refs(user_id, "connector")
            result = await direct_fallback.list_connectors(
                _modelscope_client(),
                category=category,
                q=q,
                page=page,
                page_size=page_size,
                installed_slugs=installed,
            )
        elif type_ == "agent_template":
            library = await self._installed_refs(user_id, "agent_template")
            result = direct_fallback.list_agent_templates(
                category=category, source=source, q=q, library_slugs=library
            )
        elif type_ == "agent_team_template":
            result = await direct_fallback.list_team_templates(
                self._packs, user_id, category=category, q=q
            )
        else:
            return None
        result.degraded = True
        return result

    async def get_item(self, user_id: str, item_id: str) -> MarketplaceItemDetail:
        ns, kind, ref = self._parse_item_id(item_id)
        if settings.marketplace_direct_fallback:
            if ns == "skillhub" and kind == "skill":
                installed = await self._installed_refs(user_id, "skill")
                return await direct_fallback.skill_detail(_skillhub_client(), ref, installed)
            if ns == "modelscope" and kind == "connector":
                installed = await self._installed_refs(user_id, "connector")
                server_id = direct_fallback.decode_connector_ref(ref)
                return await direct_fallback.connector_detail(
                    _modelscope_client(), server_id, installed
                )
            if ns == "valuz" and kind == "agent":
                library = await self._installed_refs(user_id, "agent_template")
                return direct_fallback.agent_template_detail(ref, library)
            if ns == "valuz" and kind == "team":
                return await direct_fallback.team_detail(self._packs, user_id, ref)
        if ns != "market":
            # ``valuz:*`` / ``skillhub:*`` / ``modelscope:*`` — the
            # direct-source namespaces this service used before the market
            # index. With direct fallback off they land here so old
            # bookmarks / deep links 404 cleanly.
            raise MarketplaceItemNotFound(f"Unknown marketplace item: {item_id}")
        try:
            payload = await self._index.item_detail(item_id, get_locale())
        except MarketIndexUnavailableError as exc:
            raise MarketplaceUpstreamError(str(exc)) from exc
        detail = MarketplaceItemDetail.model_validate(payload)
        installed_refs = await self._installed_refs(user_id, detail.type)
        detail.installed = self._recompute_installed(detail, installed_refs)
        return detail

    @staticmethod
    def _recompute_installed(item: MarketplaceItem, installed_refs: set[str]) -> bool:
        """Team cards list their member agents; "installed" means every member
        is present in the local agent library (the pre-index pack semantics).
        ``source_ref`` (the pack/collection id) is not itself an agent slug, so
        it only serves as the fallback when the index sent no member slugs."""
        if item.type == "agent_team_template" and item.members:
            slugs = [m.slug for m in item.members if m.slug]
            if slugs:
                return all(slug in installed_refs for slug in slugs)
        return item.source_ref in installed_refs

    async def _installed_refs(self, user_id: str, type_: str) -> set[str]:
        """Local-library recompute of ``installed`` — the index always
        reports ``false``. Matches an item's ``source_ref`` against this
        user's installed resources of the same type."""
        if type_ == "skill":
            rows = await self._skills.list_indexed_skills(user_id)
            return {row.slug for row in rows if self._is_installed_skill_row(row)}
        if type_ in ("agent_template", "agent_team_template"):
            return {a.slug for a in await self._agents.list_agents(user_id)}
        if type_ == "connector":
            if self._connectors is None:
                return set()
            return {view.slug for view in await self._connectors.list_connectors(user_id)}
        if type_ == "plugin":
            if self._plugins is None:
                return set()
            return {view.name for view in await self._plugins.list_plugins(user_id)}
        return set()

    @staticmethod
    def _is_installed_skill_row(row: Any) -> bool:
        """A stale index row must not make Marketplace show "installed".

        Deleting a skill removes its directory immediately. Older rows may
        still say ``available`` until a rescan, so verify the indexed source
        path exists when present.
        """
        if getattr(row, "status", "available") != "available":
            return False
        source_path = getattr(row, "source_path", None)
        return not source_path or Path(str(source_path)).exists()

    @staticmethod
    def _parse_item_id(item_id: str) -> tuple[str, str, str]:
        parts = item_id.split(":", 2)
        if len(parts) != 3 or not all(parts):
            raise MarketplaceItemNotFound(f"Malformed marketplace item id: {item_id}")
        return parts[0], parts[1], parts[2]

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    async def install(
        self,
        user_id: str,
        item_id: str,
        *,
        runtime: str | None = None,
        provider_id: str | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> MarketplaceInstallResult:
        """Confirmed install. ``runtime/provider_id/model/effort`` are the
        caller-resolved deploy defaults, required only for agent/team items
        (the route resolves them lazily to keep skill installs independent of
        model-channel setup)."""
        ns, kind, ref = self._parse_item_id(item_id)
        if ns == "market" and kind == "skill":
            return await self._install_market_skill(user_id, item_id, ref)
        if ns == "market" and kind == "agent":
            return await self._install_agent_template(
                user_id,
                item_id,
                ref,
                runtime=runtime,
                provider_id=provider_id,
                model=model,
                effort=effort,
            )
        if ns == "market" and kind == "team":
            return await self._install_team(
                user_id,
                item_id,
                ref,
                runtime=runtime,
                provider_id=provider_id,
                model=model,
                effort=effort,
            )
        if ns == "market" and kind == "plugin":
            return await self._install_plugin(user_id, item_id)
        if settings.marketplace_direct_fallback:
            if ns == "skillhub" and kind == "skill":
                return await self._install_skillhub_skill_fallback(user_id, item_id, ref)
            if ns == "valuz" and kind == "agent":
                return await self._install_agent_template_fallback(
                    user_id,
                    item_id,
                    ref,
                    runtime=runtime,
                    provider_id=provider_id,
                    model=model,
                    effort=effort,
                )
            if ns == "valuz" and kind == "team":
                return await self._install_team_fallback(
                    user_id,
                    item_id,
                    ref,
                    runtime=runtime,
                    provider_id=provider_id,
                    model=model,
                    effort=effort,
                )
        # ``market:connector:*`` never reaches here — the frontend reads
        # ``connector_config`` off the detail and calls POST /v1/connectors
        # directly. ``modelscope:connector:*`` install goes through that same
        # frontend path (``connectorsApi.create``), never through this
        # method. Direct-source ids with fallback off, and anything else, 404.
        raise MarketplaceItemNotFound(f"Unknown marketplace item: {item_id}")

    async def _install_skillhub_skill_fallback(
        self, user_id: str, item_id: str, slug: str
    ) -> MarketplaceInstallResult:
        """Restores the pre-market-index SkillHub install path
        (``1280e99f``'s ``_install_skillhub_skill``), reusing the shared
        URL-import pipeline. A successful install still writes provenance —
        unlike the pre-index era, ``MarketplaceInstallStore`` exists now —
        tagged ``source_channel="direct-fallback"`` so it's distinguishable
        from index-sourced installs."""
        from valuz_agent.modules.marketplace.skillhub import SkillHubUnavailableError

        hub = _skillhub_client()
        result, content_hash = await self._install_skill_from_url(
            user_id, item_id, slug, hub.download_url(slug), allow_rename=True
        )
        if result.installed_ref:
            version = "0.0.0"
            try:
                raw_detail = await hub.skill_detail(slug)
            except SkillHubUnavailableError:
                raw_detail = None
            if raw_detail is not None:
                skill = raw_detail.get("skill") or {}
                latest = raw_detail.get("latestVersion") or {}
                version = str(latest.get("version") or skill.get("version") or "0.0.0")
            await self._installs.record(
                user_id,
                item_id=item_id,
                item_type="skill",
                installed_ref=result.installed_ref,
                version=version,
                source_channel="direct-fallback",
                content_hash=content_hash,
            )
        return result

    async def _install_agent_template_fallback(
        self,
        user_id: str,
        item_id: str,
        template_id: str,
        *,
        runtime: str | None,
        provider_id: str | None,
        model: str | None,
        effort: str | None,
    ) -> MarketplaceInstallResult:
        """Restores the pre-market-index built-in template install path
        (``1280e99f``'s ``_install_agent_template``): the payload comes from
        the bundled ``agent_templates.json`` instead of an index manifest."""
        from valuz_agent.modules.marketplace.templates import load_agent_templates

        tpl = next((t for t in load_agent_templates() if t.id == template_id), None)
        if tpl is None:
            raise MarketplaceItemNotFound(f"Unknown agent template: {template_id}")
        payload: dict[str, Any] = {
            "slug": tpl.slug,
            "name": resolve_text(tpl.name),
            "description": resolve_text(tpl.role),
            "instructions": resolve_text(tpl.instructions),
            "avatar": tpl.icon,
            "effort": effort or tpl.effort,
        }
        if runtime:
            payload["runtime"] = runtime
        if model:
            payload["model"] = model
        if provider_id:
            payload["provider_id"] = provider_id
        status: Literal["installed", "already_installed"] = "installed"
        try:
            row = await self._agents.create_agent(user_id, payload)
            installed_ref = row.slug
        except MemberAlreadyExistsError:
            status = "already_installed"
            installed_ref = tpl.slug
        await self._installs.record(
            user_id,
            item_id=item_id,
            item_type="agent_template",
            installed_ref=installed_ref,
            version="0.0.0",
            source_channel="direct-fallback",
        )
        logger.info(
            "marketplace installed built-in agent template %s as %s", item_id, installed_ref
        )
        return MarketplaceInstallResult(item_id=item_id, status=status, installed_ref=installed_ref)

    async def _install_team_fallback(
        self,
        user_id: str,
        item_id: str,
        pack_id: str,
        *,
        runtime: str | None,
        provider_id: str | None,
        model: str | None,
        effort: str | None,
    ) -> MarketplaceInstallResult:
        """Restores the pre-market-index built-in pack install path
        (``1280e99f``'s ``_install_team``): the pack ships with the client,
        so this imports it directly (SkillHub skill dependencies first)
        instead of reading an index manifest."""
        from valuz_agent.modules.agent_packs.errors import PackNotFound

        hub = _skillhub_client()
        try:
            pack = await self._packs.get_pack(user_id, pack_id)
            for dep in pack.get("skills") or []:
                if not isinstance(dep, dict) or dep.get("source") != "skillhub":
                    continue
                slug = str(dep.get("slug") or "")
                if not slug:
                    continue
                await self._install_skill_from_url(
                    user_id,
                    f"skillhub:skill:{slug}",
                    slug,
                    hub.download_url(slug),
                    allow_rename=False,
                )
            result = await self._packs.import_pack(
                user_id,
                pack_id,
                runtime=runtime or "claude_agent",
                provider_id=provider_id or "",
                model=model or "",
                effort=effort,
            )
        except PackNotFound as exc:
            raise MarketplaceItemNotFound(f"Unknown team template: {pack_id}") from exc
        created = int(result.get("created") or 0)
        skipped = int(result.get("skipped") or 0)
        await self._installs.record(
            user_id,
            item_id=item_id,
            item_type="agent_team_template",
            installed_ref=pack_id,
            version="0.0.0",
            source_channel="direct-fallback",
        )
        logger.info(
            "marketplace installed built-in team pack %s (created=%d skipped=%d)",
            item_id,
            created,
            skipped,
        )
        return MarketplaceInstallResult(
            item_id=item_id,
            status="installed" if created > 0 else "already_installed",
            installed_ref=pack_id,
            created=created,
            skipped=skipped,
        )

    async def _fetch_install_manifest(self, item_id: str) -> dict[str, Any]:
        try:
            raw = await self._index.item_detail(item_id, get_locale())
        except MarketIndexUnavailableError as exc:
            raise MarketplaceUpstreamError(str(exc)) from exc
        manifest = raw.get("install_manifest")
        if not isinstance(manifest, dict):
            raise MarketplaceUpstreamError(f"Missing install manifest for {item_id}")
        return raw

    # -- skills -----------------------------------------------------------

    async def _install_market_skill(
        self, user_id: str, item_id: str, slug: str
    ) -> MarketplaceInstallResult:
        raw = await self._fetch_install_manifest(item_id)
        manifest = raw["install_manifest"]
        download_url = manifest.get("download_url")
        if not download_url:
            raise MarketplaceUpstreamError(f"Missing skill download_url for {item_id}")
        result, content_hash = await self._install_skill_from_url(
            user_id, item_id, slug, str(download_url), allow_rename=True
        )
        if result.installed_ref:
            await self._installs.record(
                user_id,
                item_id=item_id,
                item_type="skill",
                installed_ref=result.installed_ref,
                version=str(raw.get("version") or ""),
                source_channel=self._index.channel,
                content_hash=content_hash,
            )
        return result

    async def _install_skill_from_url(
        self,
        user_id: str,
        item_id: str,
        slug: str,
        download_url: str,
        *,
        allow_rename: bool = True,
    ) -> tuple[MarketplaceInstallResult, str | None]:
        """Shared by the top-level ``market:skill:*`` install and a team
        pack's ``source: "url"`` skill dependencies. Both resolve their
        archive through an index-provided ``download_url`` and run it
        through the existing skill URL-import pipeline (caps + provenance).
        Returns the result plus the installed skill's content hash (``None``
        when the skill was already installed and carries no indexed hash).
        """
        existing = await self._skills.get_indexed_skill(user_id, slug)
        if existing is not None and self._is_installed_skill_row(existing):
            return (
                MarketplaceInstallResult(
                    item_id=item_id, status="already_installed", installed_ref=slug
                ),
                getattr(existing, "content_hash", None),
            )
        from valuz_agent.modules.skills.errors import SkillImportFailed

        try:
            preview = await self._skills.import_url_preview(user_id, download_url)
        except SkillImportFailed as exc:
            # A fetch failure here is an upstream problem (the index's
            # download host or its CDN), not a bad request from the user.
            if "Failed to fetch URL" in str(exc):
                raise MarketplaceUpstreamError(str(exc)) from exc
            raise
        # Preserve the market catalog slug locally. Some archives use a
        # friendlier manifest name than their catalog slug, but the
        # marketplace installed-state is keyed by the catalog slug, so
        # imports must keep that slug stable.
        name = slug
        if allow_rename and preview.name_conflict and preview.suggested_name:
            name = preview.suggested_name
        view = await self._skills.confirm_url_import(
            user_id,
            SkillImportUrlConfirmRequest(preview_id=preview.preview_id, name=name),
        )
        logger.info("marketplace installed skill %s (item=%s) as %s", slug, item_id, view.slug)
        return (
            MarketplaceInstallResult(item_id=item_id, status="installed", installed_ref=view.slug),
            getattr(view, "content_hash", None),
        )

    # -- plugins ----------------------------------------------------------------

    async def _install_plugin(self, user_id: str, item_id: str) -> MarketplaceInstallResult:
        """``market:plugin:<slug>`` — the plugin installer downloads the Agent
        Plugins zip through the item's ``install_manifest.download_url``,
        installs the package + members (default conflict policy ``skip``: an
        already-present member with different content is linked and flagged,
        never silently overwritten) and records the provenance row itself."""
        if self._plugins is None:
            raise MarketplaceItemNotFound(f"Plugin installs are not available: {item_id}")
        result = await self._plugins.install(user_id, market_item_id=item_id, on_conflict="skip")
        logger.info(
            "marketplace installed plugin %s as %s (%s)", item_id, result.plugin.name, result.status
        )
        return MarketplaceInstallResult(
            item_id=item_id,
            status="already_installed" if result.status == "already_installed" else "installed",
            installed_ref=result.plugin.name,
        )

    # -- agent templates ------------------------------------------------------

    async def _install_agent_template(
        self,
        user_id: str,
        item_id: str,
        template_id: str,
        *,
        runtime: str | None,
        provider_id: str | None,
        model: str | None,
        effort: str | None,
    ) -> MarketplaceInstallResult:
        raw = await self._fetch_install_manifest(item_id)
        manifest = raw["install_manifest"]
        slug = str(manifest.get("slug") or template_id)
        payload: dict[str, Any] = {
            "slug": slug,
            "name": resolve_text(manifest.get("name")),
            "description": resolve_text(manifest.get("role")),
            "instructions": resolve_text(manifest.get("instructions")),
            "avatar": manifest.get("icon"),
            "effort": effort or manifest.get("effort"),
        }
        if runtime:
            payload["runtime"] = runtime
        if model:
            payload["model"] = model
        if provider_id:
            payload["provider_id"] = provider_id
        status: Literal["installed", "already_installed"] = "installed"
        try:
            row = await self._agents.create_agent(user_id, payload)
            installed_ref = row.slug
        except MemberAlreadyExistsError:
            status = "already_installed"
            installed_ref = slug
        await self._installs.record(
            user_id,
            item_id=item_id,
            item_type="agent_template",
            installed_ref=installed_ref,
            version=str(raw.get("version") or ""),
            source_channel=self._index.channel,
        )
        logger.info("marketplace installed agent template %s as %s", item_id, installed_ref)
        return MarketplaceInstallResult(item_id=item_id, status=status, installed_ref=installed_ref)

    # -- team templates -------------------------------------------------------

    async def _install_team(
        self,
        user_id: str,
        item_id: str,
        pack_id: str,
        *,
        runtime: str | None,
        provider_id: str | None,
        model: str | None,
        effort: str | None,
    ) -> MarketplaceInstallResult:
        raw = await self._fetch_install_manifest(item_id)
        manifest_raw = raw["install_manifest"]
        await self._install_team_url_dependencies(user_id, manifest_raw)
        manifest = self._parse_pack_manifest(manifest_raw)
        result = await self._packs.import_manifest(
            user_id,
            manifest,
            runtime=runtime or "claude_agent",
            provider_id=provider_id or "",
            model=model or "",
            effort=effort,
        )
        created = int(result.get("created") or 0)
        skipped = int(result.get("skipped") or 0)
        await self._installs.record(
            user_id,
            item_id=item_id,
            item_type="agent_team_template",
            installed_ref=pack_id,
            version=str(raw.get("version") or ""),
            source_channel=self._index.channel,
        )
        logger.info(
            "marketplace installed team pack %s (created=%d skipped=%d)",
            item_id,
            created,
            skipped,
        )
        return MarketplaceInstallResult(
            item_id=item_id,
            status="installed" if created > 0 else "already_installed",
            installed_ref=pack_id,
            created=created,
            skipped=skipped,
        )

    async def _install_team_url_dependencies(
        self, user_id: str, manifest_raw: dict[str, Any]
    ) -> None:
        """Install a team pack's ``source: "url"`` skill dependencies before
        the pack import runs, so the resulting agents can resolve their
        skill slugs immediately. Extracted from the RAW manifest dict (not
        the validated ``AgentPackManifest`` / ``PackManifest``) because
        ``download_url`` is a market-index-only field that isn't part of the
        portable ``PackSkill`` schema."""
        for dep in manifest_raw.get("skills") or []:
            if not isinstance(dep, dict) or dep.get("source") != "url":
                continue
            slug = str(dep.get("slug") or "")
            download_url = dep.get("download_url")
            if not slug or not download_url:
                continue
            await self._install_skill_from_url(
                user_id,
                f"market:skill:{slug}",
                slug,
                str(download_url),
                allow_rename=False,
            )

    @staticmethod
    def _parse_pack_manifest(raw: dict[str, Any]) -> AgentPackManifest | PackManifest:
        if raw.get("project") is not None or raw.get("schema_version") == 2:
            return PackManifest.model_validate(raw)
        return AgentPackManifest.model_validate(raw)

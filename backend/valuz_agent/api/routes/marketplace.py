"""HTTP routes for the marketplace — the normalized discovery/import catalog.

  GET  /v1/marketplace/categories            — category rail for one tab
  GET  /v1/marketplace/items                 — paged normalized item list
  GET  /v1/marketplace/items/{id}            — import-preview detail
  POST /v1/marketplace/items/{id}:install    — confirmed install

The market index (``MarketIndexClient``) is the sole data source — see
``docs/cloud-marketplace/design/oss.md``. This layer normalizes every
response into the shared ``Marketplace*`` item shape and delegates installs
to the existing local pipelines.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import get_current_user_id, get_skill_service
from valuz_agent.infra.config import settings
from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.marketplace.install_store import MarketplaceInstallStore
from valuz_agent.modules.marketplace.market_index import MarketIndexClient
from valuz_agent.modules.marketplace.models import (
    MarketplaceCategoryList,
    MarketplaceInstallResult,
    MarketplaceItemDetail,
    MarketplaceItemList,
)
from valuz_agent.modules.marketplace.service import MarketplaceService
from valuz_agent.modules.skills.service import SkillLibraryService

router = APIRouter(tags=["marketplace"])


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _market_index_client() -> MarketIndexClient:
    """Process-wide market index client so its TTL cache (and, when the base
    url is resolved lazily, the candidate-race pin) span requests. An
    explicit ``marketplace_index_base_url`` skips candidate racing entirely;
    left empty (the OSS default), the client races
    ``marketplace_index_candidates`` on first use."""
    return MarketIndexClient(
        settings.marketplace_index_base_url or None, settings.marketplace_index_channel
    )


async def _get_marketplace_service(
    db: AsyncSession = Depends(get_async_session),
    skill_service: SkillLibraryService = Depends(get_skill_service),
) -> MarketplaceService:
    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.service import ConnectorService
    from valuz_agent.modules.plugins.datastore import PluginDatastore
    from valuz_agent.modules.plugins.service import PluginService

    connector_svc = ConnectorService(ConnectorDatastore(db))
    agent_svc = AgentService(db, connector_service=connector_svc)
    installs = MarketplaceInstallStore(db)
    index = _market_index_client()
    plugin_svc = PluginService(
        datastore=PluginDatastore(db),
        skill_service=skill_service,
        connector_service=connector_svc,
        market=index,
        installs=installs,
    )
    return MarketplaceService(
        index=index,
        skill_service=skill_service,
        agent_service=agent_svc,
        pack_service=AgentPackService(agent_svc),
        installs=installs,
        connector_service=connector_svc,
        plugin_service=plugin_svc,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/v1/marketplace/categories", response_model=MarketplaceCategoryList)
async def list_marketplace_categories(
    kind: Literal["skill", "agent", "connector", "plugin"] = Query(...),
    user_id: str = Depends(get_current_user_id),
    svc: MarketplaceService = Depends(_get_marketplace_service),
) -> MarketplaceCategoryList:
    """Category rail for one marketplace tab; degrades (never fails) when
    the market index is unreachable."""
    return await svc.list_categories(user_id, kind)


@router.get("/v1/marketplace/items", response_model=MarketplaceItemList)
async def list_marketplace_items(
    type: Literal["skill", "agent_template", "agent_team_template", "connector", "plugin"] = Query(
        ...
    ),
    category: str | None = Query(default=None),
    subcategory: str | None = Query(default=None),
    # Open string (see ``models.MarketplaceSource``): the index decides which
    # sources exist; an unknown filter simply matches nothing upstream.
    source: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    composition: Literal["skills_only", "with_connectors"] | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    svc: MarketplaceService = Depends(_get_marketplace_service),
) -> MarketplaceItemList:
    """Paged browse over one item type, served entirely by the market index.
    `degraded` marks an index outage. ``composition`` filters ``type=plugin``
    (skill suites vs plugins with connectors)."""
    return await svc.list_items(
        user_id,
        type_=type,
        category=category,
        subcategory=subcategory,
        source=source,
        q=q,
        page=page,
        page_size=page_size,
        composition=composition,
    )


@router.get("/v1/marketplace/items/{item_id}", response_model=MarketplaceItemDetail)
async def get_marketplace_item(
    item_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: MarketplaceService = Depends(_get_marketplace_service),
) -> MarketplaceItemDetail:
    """Import-preview payload (files + security for skills, roster for teams,
    instructions for agent templates). 404/502 map via the ValuzError handler."""
    return await svc.get_item(user_id, item_id)


@router.post("/v1/marketplace/items/{item_id}:install", response_model=MarketplaceInstallResult)
async def install_marketplace_item(
    item_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: MarketplaceService = Depends(_get_marketplace_service),
    db: AsyncSession = Depends(get_async_session),
) -> MarketplaceInstallResult:
    """Confirmed install (the client showed the preview). Agent/team installs
    resolve runtime/model/provider/effort from the user's global defaults —
    the same resolver onboarding uses (422 when no model channel is wired);
    skill installs skip that requirement entirely."""
    runtime = provider_id = model = effort = None
    if item_id.startswith(("market:agent:", "market:team:", "valuz:agent:", "valuz:team:")):
        from valuz_agent.api.routes.onboarding import _resolve_deploy_target
        from valuz_agent.modules.settings.preferences import get_default_effort

        runtime, provider_id, model = await _resolve_deploy_target(db, user_id)
        effort = await get_default_effort(db, user_id=user_id)
    return await svc.install(
        user_id,
        item_id,
        runtime=runtime,
        provider_id=provider_id,
        model=model,
        effort=effort,
    )

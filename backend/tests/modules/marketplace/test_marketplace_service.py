"""MarketplaceService — index pass-through + install orchestration.

No network, no DB: the market index client, skill/agent/pack/connector
services, and the install-provenance store are all replaced with in-memory
fakes exposing exactly the methods the service consumes. Covers:

- category/item/detail pass-through, Pydantic-validated from the index's raw
  payload, with ``installed`` recomputed against local library state;
- the degraded matrix (index outage → empty+degraded for list/categories,
  ``MarketplaceUpstreamError`` for detail/install);
- the three ``market:*`` install dispatch paths (skill / agent_template /
  agent_team_template) and their provenance writes, including the
  "reinstalling an already-installed item still records provenance" rule;
- legacy (``valuz:*`` / ``skillhub:*`` / ``modelscope:*``) and malformed ids
  404ing instead of resolving locally.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.infra.config import settings
from valuz_agent.modules.agents.service import MemberAlreadyExistsError
from valuz_agent.modules.marketplace.errors import (
    MarketplaceItemNotFound,
    MarketplaceUpstreamError,
)
from valuz_agent.modules.marketplace.market_index import MarketIndexUnavailableError
from valuz_agent.modules.marketplace.service import MarketplaceService
from valuz_agent.modules.packs_common.manifest import PackManifest
from valuz_agent.modules.skills.errors import SkillImportFailed

USER = "user-1"


def _item(
    item_id: str,
    *,
    type_: str,
    source_ref: str,
    source: str = "valuz_official",
    install_target: str = "skill_library",
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": item_id,
        "type": type_,
        "source": source,
        "source_ref": source_ref,
        "title": source_ref,
        "description": f"{source_ref} description",
        "badges": [],
        "install_target": install_target,
        "installed": False,
    }
    base.update(overrides)
    return base


class FakeMarketIndexClient:
    def __init__(self) -> None:
        self.channel = "oss"
        self.categories_payload: dict[str, Any] = {"categories": [], "degraded": False}
        self.items_payload: dict[str, Any] = {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": 30,
            "degraded": False,
        }
        self.details: dict[str, dict[str, Any]] = {}
        self.unavailable = False
        self.categories_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []
        self.detail_calls: list[str] = []

    def _check(self) -> None:
        if self.unavailable:
            raise MarketIndexUnavailableError("down")

    async def categories(self, kind: str, locale: str) -> dict[str, Any]:
        self._check()
        self.categories_calls.append({"kind": kind, "locale": locale})
        return self.categories_payload

    async def list_items(self, **params: Any) -> dict[str, Any]:
        self._check()
        self.list_calls.append(params)
        return self.items_payload

    async def item_detail(self, item_id: str, locale: str) -> dict[str, Any]:
        self._check()
        self.detail_calls.append(item_id)
        if item_id not in self.details:
            raise MarketIndexUnavailableError(f"no detail for {item_id}")
        return self.details[item_id]


class FakeInstallStore:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.removed: list[tuple[str, str]] = []

    async def record(self, user_id: str, **kwargs: Any) -> None:
        self.records.append({"user_id": user_id, **kwargs})

    async def remove_by_ref(self, user_id: str, installed_ref: str) -> None:
        self.removed.append((user_id, installed_ref))


class FakeSkillService:
    """Covers both faces the marketplace consumes: the index reads
    (list_indexed_skills / get_indexed_skill) and the URL-import pipeline."""

    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []
        self.preview = SimpleNamespace(
            preview_id="pv-1", name="fresh", name_conflict=False, suggested_name=None
        )
        self.preview_error: Exception | None = None
        self.confirmed: list[Any] = []

    async def list_indexed_skills(self, user_id: str) -> list[SimpleNamespace]:
        return self.rows

    async def get_indexed_skill(self, user_id: str, slug: str) -> SimpleNamespace | None:
        return next((r for r in self.rows if r.slug == slug), None)

    async def import_url_preview(self, user_id: str, url: str) -> SimpleNamespace:
        if self.preview_error is not None:
            raise self.preview_error
        return self.preview

    async def confirm_url_import(self, user_id: str, payload: Any) -> SimpleNamespace:
        self.confirmed.append(payload)
        slug = payload.name or "imported-skill"
        return SimpleNamespace(slug=slug, content_hash=f"hash-of-{slug}")


class FakeAgentService:
    def __init__(self, slugs: set[str] | None = None) -> None:
        self.slugs = slugs or set()
        self.created: list[dict[str, Any]] = []

    async def list_agents(self, user_id: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(slug=s) for s in self.slugs]

    async def create_agent(self, user_id: str, payload: dict[str, Any]) -> SimpleNamespace:
        if payload["slug"] in self.slugs:
            raise MemberAlreadyExistsError(payload["slug"])
        self.created.append(payload)
        return SimpleNamespace(slug=payload["slug"])


class FakePackService:
    def __init__(self) -> None:
        self.import_result: dict[str, Any] = {"created": 2, "skipped": 0, "roles": []}
        self.import_calls: list[dict[str, Any]] = []

    async def import_manifest(self, user_id: str, manifest: Any, **kwargs: Any) -> dict[str, Any]:
        self.import_calls.append({"manifest": manifest, **kwargs})
        return self.import_result


class FakeConnectorService:
    def __init__(self) -> None:
        self.slugs: set[str] = set()

    async def list_connectors(self, user_id: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(slug=slug) for slug in self.slugs]


@pytest.fixture(autouse=True)
def _no_direct_fallback(monkeypatch):  # type: ignore[no-untyped-def]
    """This suite exercises the pure index pass-through/degrade behavior with
    in-memory fakes only (no network) — direct-source fallback (SkillHub /
    ModelScope) is covered separately in ``test_marketplace_direct_fallback.py``
    with its own hub/ms fakes. Force the flag off here so an index-outage case
    can't accidentally reach the real network through the lazily-constructed
    fallback clients."""
    monkeypatch.setattr(settings, "marketplace_direct_fallback", False)


@pytest.fixture()
def env():  # type: ignore[no-untyped-def]
    index = FakeMarketIndexClient()
    skill_svc = FakeSkillService()
    agent_svc = FakeAgentService()
    pack_svc = FakePackService()
    connector_svc = FakeConnectorService()
    installs = FakeInstallStore()
    svc = MarketplaceService(
        index=index,  # type: ignore[arg-type]
        skill_service=skill_svc,  # type: ignore[arg-type]
        agent_service=agent_svc,  # type: ignore[arg-type]
        pack_service=pack_svc,  # type: ignore[arg-type]
        installs=installs,  # type: ignore[arg-type]
        connector_service=connector_svc,  # type: ignore[arg-type]
    )
    return SimpleNamespace(
        svc=svc,
        index=index,
        skill_svc=skill_svc,
        agent_svc=agent_svc,
        pack_svc=pack_svc,
        connector_svc=connector_svc,
        installs=installs,
    )


# ---------------------------------------------------------------------------
# Categories / list — pass-through + installed recompute + degraded matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_categories_passes_through_index_payload(env):  # type: ignore[no-untyped-def]
    env.index.categories_payload = {
        "categories": [
            {
                "key": "finance",
                "label": "Finance",
                "count": 3,
                "subcategories": [
                    {"key": "brokerage", "label": "Brokerage", "count": 2}
                ],
            }
        ],
        "degraded": False,
    }
    out = await env.svc.list_categories(USER, "skill")
    assert [(c.key, c.count) for c in out.categories] == [("finance", 3)]
    assert [
        (subcategory.key, subcategory.count)
        for subcategory in out.categories[0].subcategories
    ] == [("brokerage", 2)]
    assert (env.index.categories_calls[0]["kind"]) == "skill"


@pytest.mark.asyncio
async def test_list_categories_degrades_on_index_outage(env):  # type: ignore[no-untyped-def]
    env.index.unavailable = True
    out = await env.svc.list_categories(USER, "skill")
    assert out.degraded and out.categories == []


@pytest.mark.asyncio
async def test_list_items_degrades_on_index_outage(env):  # type: ignore[no-untyped-def]
    env.index.unavailable = True
    out = await env.svc.list_items(USER, type_="skill")
    assert out.degraded and out.items == [] and out.total == 0


@pytest.mark.asyncio
async def test_list_items_recomputes_installed_for_skills(env):  # type: ignore[no-untyped-def]
    env.index.items_payload = {
        "items": [
            _item("market:skill:have", type_="skill", source_ref="have", source="skillhub"),
            _item("market:skill:lack", type_="skill", source_ref="lack", source="skillhub"),
        ],
        "total": 2,
        "page": 1,
        "page_size": 30,
        "degraded": False,
    }
    env.skill_svc.rows = [SimpleNamespace(slug="have", status="available", source_path=None)]
    out = await env.svc.list_items(USER, type_="skill")
    flags = {i.source_ref: i.installed for i in out.items}
    assert flags == {"have": True, "lack": False}


@pytest.mark.asyncio
async def test_list_items_accepts_sources_this_build_never_heard_of(env):  # type: ignore[no-untyped-def]
    """``source`` is data the index grows over time — a new upstream store must
    not need a client release. The whole page used to 500 on one such row."""
    env.index.items_payload = {
        "items": [
            _item("market:skill:a", type_="skill", source_ref="a", source="skillhub"),
            _item("market:skill:b", type_="skill", source_ref="b", source="brand-new-store"),
        ],
        "total": 2,
        "page": 1,
        "page_size": 30,
        "degraded": False,
    }
    out = await env.svc.list_items(USER, type_="skill")
    assert [i.source for i in out.items] == ["skillhub", "brand-new-store"]


@pytest.mark.asyncio
async def test_list_items_skips_rows_this_build_cannot_render(env):  # type: ignore[no-untyped-def]
    """New item kinds / install targets reach the index before every client
    has updated: an old build drops those rows and keeps the page (and the
    index's ``total``) instead of failing the request."""
    env.index.items_payload = {
        "items": [
            _item("market:skill:a", type_="skill", source_ref="a"),
            _item("market:hologram:x", type_="hologram", source_ref="x"),
            _item("market:skill:c", type_="skill", source_ref="c", install_target="warp_core"),
            "not-even-an-object",
            _item("market:skill:d", type_="skill", source_ref="d"),
        ],
        "total": 5,
        "page": 1,
        "page_size": 30,
        "degraded": False,
    }
    out = await env.svc.list_items(USER, type_="skill")
    assert [i.source_ref for i in out.items] == ["a", "d"]
    assert out.total == 5 and out.degraded is False


@pytest.mark.asyncio
async def test_list_items_drops_unknown_badges_keeps_item(env):  # type: ignore[no-untyped-def]
    env.index.items_payload = {
        "items": [
            _item(
                "market:skill:a",
                type_="skill",
                source_ref="a",
                badges=["free_install", "shiny-new-badge", "verified"],
            ),
        ],
        "total": 1,
        "page": 1,
        "page_size": 30,
        "degraded": False,
    }
    out = await env.svc.list_items(USER, type_="skill")
    assert out.items[0].badges == ["free_install", "verified"]


@pytest.mark.asyncio
async def test_list_items_installed_skill_row_must_exist_on_disk(env, tmp_path):  # type: ignore[no-untyped-def]
    env.index.items_payload = {
        "items": [
            _item("market:skill:ghost", type_="skill", source_ref="ghost", source="skillhub")
        ],
        "total": 1,
        "page": 1,
        "page_size": 30,
        "degraded": False,
    }
    env.skill_svc.rows = [
        SimpleNamespace(slug="ghost", status="available", source_path=str(tmp_path / "deleted"))
    ]
    out = await env.svc.list_items(USER, type_="skill")
    assert out.items[0].installed is False


@pytest.mark.asyncio
async def test_list_items_recomputes_installed_for_agent_templates(env):  # type: ignore[no-untyped-def]
    env.agent_svc.slugs = {"mkt-equity-research"}
    env.index.items_payload = {
        "items": [
            _item(
                "market:agent:equity-research",
                type_="agent_template",
                source_ref="mkt-equity-research",
                install_target="agent_library",
            ),
            _item(
                "market:agent:longform-writer",
                type_="agent_template",
                source_ref="mkt-longform-writer",
                install_target="agent_library",
            ),
        ],
        "total": 2,
        "page": 1,
        "page_size": 30,
        "degraded": False,
    }
    out = await env.svc.list_items(USER, type_="agent_template")
    flags = {i.source_ref: i.installed for i in out.items}
    assert flags == {"mkt-equity-research": True, "mkt-longform-writer": False}


@pytest.mark.asyncio
async def test_list_items_recomputes_installed_for_team_templates(env):  # type: ignore[no-untyped-def]
    env.agent_svc.slugs = {"investment-lead"}
    env.index.items_payload = {
        "items": [
            _item(
                "market:team:investment",
                type_="agent_team_template",
                source_ref="investment-lead",
                install_target="agent_library",
            ),
        ],
        "total": 1,
        "page": 1,
        "page_size": 30,
        "degraded": False,
    }
    out = await env.svc.list_items(USER, type_="agent_team_template")
    assert out.items[0].installed is True


@pytest.mark.asyncio
async def test_team_installed_requires_all_member_slugs(env):  # type: ignore[no-untyped-def]
    """When the index card carries members, "installed" means every member
    agent is present locally (pack semantics) — source_ref (the collection id)
    only serves as the fallback when members are absent."""
    members = [
        {"slug": "invest-lead", "name": "Lead", "role": "lead", "lead": True},
        {"slug": "invest-analyst", "name": "Analyst", "role": "analyst", "lead": False},
    ]
    env.index.items_payload = {
        "items": [
            _item(
                "market:team:investment",
                type_="agent_team_template",
                source_ref="investment",  # collection id, never an agent slug
                install_target="agent_library",
                members=members,
            ),
        ],
        "total": 1,
        "page": 1,
        "page_size": 30,
        "degraded": False,
    }
    env.agent_svc.slugs = {"invest-lead"}  # one member missing
    out = await env.svc.list_items(USER, type_="agent_team_template")
    assert out.items[0].installed is False

    env.agent_svc.slugs = {"invest-lead", "invest-analyst"}  # all present
    out = await env.svc.list_items(USER, type_="agent_team_template")
    assert out.items[0].installed is True


@pytest.mark.asyncio
async def test_list_items_recomputes_installed_for_connectors(env):  # type: ignore[no-untyped-def]
    env.connector_svc.slugs = {"modelscope-owner-popular"}
    env.index.items_payload = {
        "items": [
            _item(
                "market:connector:owner-popular",
                type_="connector",
                source_ref="modelscope-owner-popular",
                source="modelscope",
                install_target="connector_library",
            ),
        ],
        "total": 1,
        "page": 1,
        "page_size": 30,
        "degraded": False,
    }
    out = await env.svc.list_items(USER, type_="connector")
    assert out.items[0].installed is True


@pytest.mark.asyncio
async def test_list_items_connector_installed_false_without_connector_service(env):  # type: ignore[no-untyped-def]
    svc = MarketplaceService(
        index=env.index,  # type: ignore[arg-type]
        skill_service=env.skill_svc,  # type: ignore[arg-type]
        agent_service=env.agent_svc,  # type: ignore[arg-type]
        pack_service=env.pack_svc,  # type: ignore[arg-type]
        installs=env.installs,  # type: ignore[arg-type]
        connector_service=None,
    )
    env.index.items_payload = {
        "items": [
            _item(
                "market:connector:x",
                type_="connector",
                source_ref="x",
                source="modelscope",
                install_target="connector_library",
            ),
        ],
        "total": 1,
        "page": 1,
        "page_size": 30,
        "degraded": False,
    }
    out = await svc.list_items(USER, type_="connector")
    assert out.items[0].installed is False


@pytest.mark.asyncio
async def test_list_items_forwards_filters_and_locale(env):  # type: ignore[no-untyped-def]
    await env.svc.list_items(
        USER,
        type_="skill",
        category="data-analysis",
        subcategory="insight",
        source="skillhub",
        q="pdf",
        page=2,
        page_size=10,
    )
    (call,) = env.index.list_calls
    assert call["type_"] == "skill"
    assert call["category"] == "data-analysis"
    assert call["subcategory"] == "insight"
    assert call["source"] == "skillhub"
    assert call["q"] == "pdf"
    assert call["page"] == 2
    assert call["page_size"] == 10
    assert "locale" in call


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_item_returns_detail_and_recomputes_installed(env):  # type: ignore[no-untyped-def]
    env.index.details["market:skill:foo"] = {
        **_item("market:skill:foo", type_="skill", source_ref="foo", source="skillhub"),
        "owner": "Acme",
    }
    env.skill_svc.rows = [SimpleNamespace(slug="foo", status="available", source_path=None)]
    detail = await env.svc.get_item(USER, "market:skill:foo")
    assert detail.owner == "Acme"
    assert detail.installed is True


@pytest.mark.asyncio
async def test_get_item_raises_upstream_error_on_index_outage(env):  # type: ignore[no-untyped-def]
    env.index.unavailable = True
    with pytest.raises(MarketplaceUpstreamError):
        await env.svc.get_item(USER, "market:skill:foo")


@pytest.mark.asyncio
async def test_get_item_rejects_legacy_and_malformed_ids(env):  # type: ignore[no-untyped-def]
    for bad in (
        "valuz:agent:missing",
        "valuz:team:missing",
        "skillhub:skill:x",
        "modelscope:connector:x",
        "nope",
    ):
        with pytest.raises(MarketplaceItemNotFound):
            await env.svc.get_item(USER, bad)


# ---------------------------------------------------------------------------
# Install — market:skill:*
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_market_skill_runs_url_pipeline_and_records_provenance(env):  # type: ignore[no-untyped-def]
    env.index.details["market:skill:fresh"] = {
        **_item("market:skill:fresh", type_="skill", source_ref="fresh", source="skillhub"),
        "version": "2.0.0",
        "install_manifest": {"download_url": "https://cdn.example/fresh.zip"},
    }
    result = await env.svc.install(USER, "market:skill:fresh")
    assert result.status == "installed"
    assert result.installed_ref == "fresh"
    (payload,) = env.skill_svc.confirmed
    assert payload.preview_id == "pv-1"
    assert payload.name == "fresh"
    (record,) = env.installs.records
    assert record["item_id"] == "market:skill:fresh"
    assert record["item_type"] == "skill"
    assert record["installed_ref"] == "fresh"
    assert record["version"] == "2.0.0"
    assert record["source_channel"] == "oss"
    assert record["content_hash"] == "hash-of-fresh"


@pytest.mark.asyncio
async def test_install_market_skill_uses_suggested_name_on_conflict(env):  # type: ignore[no-untyped-def]
    env.index.details["market:skill:fresh"] = {
        **_item("market:skill:fresh", type_="skill", source_ref="fresh", source="skillhub"),
        "install_manifest": {"download_url": "https://cdn.example/fresh.zip"},
    }
    env.skill_svc.preview = SimpleNamespace(
        preview_id="pv-2", name="taken", name_conflict=True, suggested_name="taken-2"
    )
    result = await env.svc.install(USER, "market:skill:fresh")
    assert result.installed_ref == "taken-2"


@pytest.mark.asyncio
async def test_install_market_skill_idempotent_still_records_provenance(env):  # type: ignore[no-untyped-def]
    env.skill_svc.rows = [
        SimpleNamespace(
            slug="fresh", status="available", source_path=None, content_hash="existing-hash"
        )
    ]
    env.index.details["market:skill:fresh"] = {
        **_item("market:skill:fresh", type_="skill", source_ref="fresh", source="skillhub"),
        "version": "1.0.0",
        "install_manifest": {"download_url": "https://cdn.example/fresh.zip"},
    }
    result = await env.svc.install(USER, "market:skill:fresh")
    assert result.status == "already_installed"
    assert env.skill_svc.confirmed == []
    (record,) = env.installs.records
    assert record["installed_ref"] == "fresh"
    assert record["content_hash"] == "existing-hash"


@pytest.mark.asyncio
async def test_install_market_skill_missing_download_url_raises_upstream_error(env):  # type: ignore[no-untyped-def]
    env.index.details["market:skill:fresh"] = {
        **_item("market:skill:fresh", type_="skill", source_ref="fresh", source="skillhub"),
        "install_manifest": {},
    }
    with pytest.raises(MarketplaceUpstreamError):
        await env.svc.install(USER, "market:skill:fresh")


@pytest.mark.asyncio
async def test_install_missing_manifest_raises_upstream_error(env):  # type: ignore[no-untyped-def]
    env.index.details["market:skill:fresh"] = _item(
        "market:skill:fresh", type_="skill", source_ref="fresh", source="skillhub"
    )
    with pytest.raises(MarketplaceUpstreamError):
        await env.svc.install(USER, "market:skill:fresh")


@pytest.mark.asyncio
async def test_install_index_outage_raises_upstream_error(env):  # type: ignore[no-untyped-def]
    env.index.unavailable = True
    with pytest.raises(MarketplaceUpstreamError):
        await env.svc.install(USER, "market:skill:fresh")


@pytest.mark.asyncio
async def test_install_market_skill_fetch_failure_maps_to_upstream_error(env):  # type: ignore[no-untyped-def]
    env.index.details["market:skill:fresh"] = {
        **_item("market:skill:fresh", type_="skill", source_ref="fresh", source="skillhub"),
        "install_manifest": {"download_url": "https://cdn.example/fresh.zip"},
    }
    env.skill_svc.preview_error = SkillImportFailed("Failed to fetch URL: boom")
    with pytest.raises(MarketplaceUpstreamError):
        await env.svc.install(USER, "market:skill:fresh")


@pytest.mark.asyncio
async def test_install_market_skill_validation_failure_propagates(env):  # type: ignore[no-untyped-def]
    env.index.details["market:skill:fresh"] = {
        **_item("market:skill:fresh", type_="skill", source_ref="fresh", source="skillhub"),
        "install_manifest": {"download_url": "https://cdn.example/fresh.zip"},
    }
    env.skill_svc.preview_error = SkillImportFailed("No SKILL.md found in the fetched content")
    with pytest.raises(SkillImportFailed):
        await env.svc.install(USER, "market:skill:fresh")


# ---------------------------------------------------------------------------
# Install — market:agent:*
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_agent_template_creates_agent_from_manifest(env):  # type: ignore[no-untyped-def]
    env.index.details["market:agent:meeting-notes"] = {
        **_item(
            "market:agent:meeting-notes",
            type_="agent_template",
            source_ref="meeting-notes",
            install_target="agent_library",
        ),
        "version": "1.0.0",
        "install_manifest": {
            "slug": "mkt-meeting-notes",
            "name": "Meeting Notes",
            "role": "Summarizes meetings",
            "instructions": "Do the thing",
            "icon": "notes",
            "effort": "low",
        },
    }
    result = await env.svc.install(
        USER,
        "market:agent:meeting-notes",
        runtime="deepagents",
        provider_id="prov-1",
        model="m-1",
        effort="high",
    )
    assert result.status == "installed"
    assert result.installed_ref == "mkt-meeting-notes"
    (payload,) = env.agent_svc.created
    assert payload["slug"] == "mkt-meeting-notes"
    assert payload["runtime"] == "deepagents"
    assert payload["model"] == "m-1"
    assert payload["provider_id"] == "prov-1"
    assert payload["effort"] == "high"  # caller-resolved effort wins over the manifest default
    assert payload["instructions"] == "Do the thing"
    (record,) = env.installs.records
    assert record["item_type"] == "agent_template"
    assert record["installed_ref"] == "mkt-meeting-notes"
    assert record["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_install_agent_template_resolves_localized_text(env):  # type: ignore[no-untyped-def]
    env.index.details["market:agent:x"] = {
        **_item(
            "market:agent:x",
            type_="agent_template",
            source_ref="x",
            install_target="agent_library",
        ),
        "install_manifest": {
            "slug": "mkt-x",
            "name": {"zh-CN": "会议纪要", "en-US": "Meeting Notes"},
        },
    }
    await env.svc.install(
        USER, "market:agent:x", runtime="r", provider_id="p", model="m", effort=None
    )
    (payload,) = env.agent_svc.created
    assert payload["name"] in ("会议纪要", "Meeting Notes")


@pytest.mark.asyncio
async def test_install_agent_template_idempotent(env):  # type: ignore[no-untyped-def]
    env.agent_svc.slugs = {"mkt-meeting-notes"}
    env.index.details["market:agent:meeting-notes"] = {
        **_item(
            "market:agent:meeting-notes",
            type_="agent_template",
            source_ref="meeting-notes",
            install_target="agent_library",
        ),
        "install_manifest": {"slug": "mkt-meeting-notes", "name": "Meeting Notes"},
    }
    result = await env.svc.install(
        USER,
        "market:agent:meeting-notes",
        runtime="r",
        provider_id="p",
        model="m",
        effort=None,
    )
    assert result.status == "already_installed"
    assert result.installed_ref == "mkt-meeting-notes"
    (record,) = env.installs.records
    assert record["installed_ref"] == "mkt-meeting-notes"


# ---------------------------------------------------------------------------
# Install — market:team:*
# ---------------------------------------------------------------------------


def _team_manifest(**skill_overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "agent-pack",
        "collection": {
            "id": "investment",
            "name": "Investment",
            "description": "",
            "scenario": "Finance",
            "icon": "gem",
        },
        "agents": [
            {
                "slug": "inv-lead",
                "name": "Lead",
                "description": "",
                "instructions": "",
                "skills": ["comps", "superpowers-tdd"],
                "connectors": [],
            },
        ],
        "skills": [
            {"slug": "comps", "source": "bundled"},
            {
                "slug": "superpowers-tdd",
                "source": "url",
                "download_url": "https://cdn.example/superpowers-tdd.zip",
            },
        ],
        "connectors": [],
    }


@pytest.mark.asyncio
async def test_install_team_installs_url_dependencies_then_imports_manifest(env):  # type: ignore[no-untyped-def]
    env.index.details["market:team:investment"] = {
        **_item(
            "market:team:investment",
            type_="agent_team_template",
            source_ref="investment",
            install_target="agent_library",
        ),
        "version": "3.0.0",
        "install_manifest": _team_manifest(),
    }
    result = await env.svc.install(
        USER,
        "market:team:investment",
        runtime="claude_agent",
        provider_id="p",
        model="m",
        effort="high",
    )
    assert result.status == "installed" and result.created == 2
    (payload,) = env.skill_svc.confirmed
    assert payload.name == "superpowers-tdd"
    (call,) = env.pack_svc.import_calls
    assert call["manifest"].collection.id == "investment"
    assert call["runtime"] == "claude_agent"
    assert call["effort"] == "high"
    (record,) = env.installs.records
    assert record["item_type"] == "agent_team_template"
    assert record["installed_ref"] == "investment"
    assert record["version"] == "3.0.0"


@pytest.mark.asyncio
async def test_install_team_already_installed(env):  # type: ignore[no-untyped-def]
    env.pack_svc.import_result = {"created": 0, "skipped": 3, "roles": []}
    env.index.details["market:team:investment"] = {
        **_item(
            "market:team:investment",
            type_="agent_team_template",
            source_ref="investment",
            install_target="agent_library",
        ),
        "install_manifest": _team_manifest(),
    }
    result = await env.svc.install(
        USER,
        "market:team:investment",
        runtime="claude_agent",
        provider_id="p",
        model="m",
        effort=None,
    )
    assert result.status == "already_installed" and result.skipped == 3


@pytest.mark.asyncio
async def test_install_team_accepts_unified_pack_manifest(env):  # type: ignore[no-untyped-def]
    manifest = {
        "schema_version": 2,
        "kind": "valuz-pack",
        "agents": [],
        "skills": [],
        "connectors": [],
        "collection": {"id": "growth", "name": "Growth"},
    }
    env.index.details["market:team:growth"] = {
        **_item(
            "market:team:growth",
            type_="agent_team_template",
            source_ref="growth",
            install_target="agent_library",
        ),
        "install_manifest": manifest,
    }
    await env.svc.install(
        USER, "market:team:growth", runtime="r", provider_id="p", model="m", effort=None
    )
    (call,) = env.pack_svc.import_calls
    assert isinstance(call["manifest"], PackManifest)
    assert call["manifest"].collection.id == "growth"


# ---------------------------------------------------------------------------
# Install — rejected namespaces/kinds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_unknown_ids_raise_not_found(env):  # type: ignore[no-untyped-def]
    for bad in (
        "market:connector:foo",
        "valuz:agent:x",
        "valuz:team:x",
        "skillhub:skill:x",
        "nope",
    ):
        with pytest.raises(MarketplaceItemNotFound):
            await env.svc.install(USER, bad)


# ---------------------------------------------------------------------------
# Plugins (``market:plugin:*``) — delegated to the plugin installer
# ---------------------------------------------------------------------------


class FakePluginService:
    def __init__(self) -> None:
        self.names: set[str] = set()
        self.calls: list[dict[str, Any]] = []
        self.status = "installed"

    async def list_plugins(self, user_id: str) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=n) for n in self.names]

    async def install(self, user_id: str, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({"user_id": user_id, **kwargs})
        name = str(kwargs["market_item_id"]).rsplit(":", 1)[-1]
        self.names.add(name)
        return SimpleNamespace(plugin=SimpleNamespace(name=name), status=self.status)


@pytest.fixture()
def plugin_env(env):  # type: ignore[no-untyped-def]
    plugins = FakePluginService()
    env.svc._plugins = plugins  # noqa: SLF001
    env.plugins = plugins
    return env


@pytest.mark.asyncio
async def test_list_items_recomputes_installed_for_plugins(plugin_env):  # type: ignore[no-untyped-def]
    plugin_env.plugins.names = {"equity-research"}
    plugin_env.index.items_payload = {
        "items": [
            _item(
                "market:plugin:equity-research",
                type_="plugin",
                source_ref="equity-research",
                install_target="plugin_library",
                skill_count=9,
                connector_count=0,
                composition="skills_only",
            ),
            _item(
                "market:plugin:godot-mcp",
                type_="plugin",
                source_ref="godot-mcp",
                source="plugin",
                install_target="plugin_library",
                skill_count=25,
                connector_count=1,
                composition="with_connectors",
            ),
        ],
        "total": 2,
        "page": 1,
        "page_size": 30,
        "degraded": False,
    }
    out = await plugin_env.svc.list_items(USER, type_="plugin", composition="skills_only")
    flags = {i.source_ref: i.installed for i in out.items}
    assert flags == {"equity-research": True, "godot-mcp": False}
    assert out.items[1].source == "plugin"
    assert out.items[1].connector_count == 1 and out.items[1].composition == "with_connectors"
    (call,) = plugin_env.index.list_calls
    assert call["type_"] == "plugin" and call["composition"] == "skills_only"


@pytest.mark.asyncio
async def test_get_plugin_item_detail_carries_manifest_and_members(plugin_env):  # type: ignore[no-untyped-def]
    plugin_env.index.details["market:plugin:equity-research"] = _item(
        "market:plugin:equity-research",
        type_="plugin",
        source_ref="equity-research",
        install_target="plugin_library",
        composition="skills_only",
        plugin_manifest={
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": "equity-research",
            "version": "1.0.0",
        },
        members=[
            {
                "kind": "skill",
                "slug": "earnings-analysis",
                "name": "earnings-analysis",
                "path": "skills/earnings-analysis",
            },
            {"kind": "connector", "slug": "data-api", "name": "data-api", "meta_version": None},
        ],
        install_manifest={"download_url": "https://cdn.example.com/equity-research.zip"},
    )
    detail = await plugin_env.svc.get_item(USER, "market:plugin:equity-research")
    assert (
        detail.plugin_manifest is not None and detail.plugin_manifest["name"] == "equity-research"
    )
    assert detail.members is not None
    assert [(m.kind, m.slug) for m in detail.members] == [  # type: ignore[union-attr]
        ("skill", "earnings-analysis"),
        ("connector", "data-api"),
    ]
    assert detail.installed is False
    plugin_env.plugins.names = {"equity-research"}
    assert (await plugin_env.svc.get_item(USER, "market:plugin:equity-research")).installed is True


@pytest.mark.asyncio
async def test_install_market_plugin_delegates_to_plugin_installer(plugin_env):  # type: ignore[no-untyped-def]
    out = await plugin_env.svc.install(USER, "market:plugin:equity-research")
    assert out.status == "installed" and out.installed_ref == "equity-research"
    assert out.item_id == "market:plugin:equity-research"
    (call,) = plugin_env.plugins.calls
    assert call["market_item_id"] == "market:plugin:equity-research"
    assert call["on_conflict"] == "skip"
    plugin_env.plugins.status = "already_installed"
    assert (
        await plugin_env.svc.install(USER, "market:plugin:equity-research")
    ).status == "already_installed"
    plugin_env.plugins.status = "updated"
    assert (
        await plugin_env.svc.install(USER, "market:plugin:equity-research")
    ).status == "installed"


@pytest.mark.asyncio
async def test_install_market_plugin_without_installer_is_not_found(env):  # type: ignore[no-untyped-def]
    with pytest.raises(MarketplaceItemNotFound):
        await env.svc.install(USER, "market:plugin:x")

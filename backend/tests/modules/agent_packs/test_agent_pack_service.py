"""AgentPackService — list/import behavior over an in-memory library.

Covers the data-layer guarantees without the kernel or HTTP layer: built-in
packs load from ``resources/agent_packs/*/manifest.json``, ``import_pack``
creates agents with the passed-in deploy target, de-dup is by fixed slug
(idempotent), bundled skills materialize on import, and library state
(``in_library`` / ``added``) is reported back. Deploy-target resolution lives
in the route, so the tests pass a fixed (runtime, provider, model, effort).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.agent_packs.errors import PackNotFound
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
)
from valuz_agent.modules.connectors.service import ConnectorService

USER = "user-1"
# create_agent / list_agents never touch the connector service.
DEPLOY = {
    "runtime": "claude_agent",
    "provider_id": "prov-1",
    "model": "claude-sonnet-4-6",
    "effort": "medium",
}


async def _build_service(workdir):  # type: ignore[no-untyped-def]
    """Build an isolated AgentPackService over a fresh sqlite db.
    Returns (service, session, engine) so the caller can dispose."""
    workdir.mkdir(parents=True, exist_ok=True)
    db_file = workdir / "packs.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                AgentRow.__table__,
                ProjectMemberRow.__table__,
                ConnectorRow.__table__,
                ConnectorAttrRow.__table__,
                ConnectorOAuthRow.__table__,
            ],
        )
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    # Isolated connector service so import-time connector registration writes to
    # the test db, never the real local database.
    connector_svc = ConnectorService(ConnectorDatastore(session))
    agent_svc = AgentService(session, connector_service=connector_svc)
    return AgentPackService(agent_svc), session, engine


@pytest.fixture
async def svc(tmp_path, monkeypatch) -> AsyncIterator[AgentPackService]:
    # import_pack materializes the pack's bundled skills to the official-skills
    # dir under data_dir — pin data_dir under tmp so tests never touch the real
    # ~/.valuz-oss tree.
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "data_dir", tmp_path)
    service, session, engine = await _build_service(tmp_path)
    try:
        yield service
    finally:
        await session.close()
        await engine.dispose()


async def test_list_packs(svc: AgentPackService) -> None:
    packs = await svc.list_packs(USER)
    assert [p["id"] for p in packs] == [
        "product-strategy",
        "design-prototype",
        "development-engineering",
        "qa-testing",
        "investment",
        "supply-chain-tracking",
        "competitive-intelligence",
        "content-growth",
        "campaign-event",
        "content",
        "short-video-growth",
        "contract-review",
        "compliance-review",
        "academic-research",
        "training-program",
        "recruiting-evaluation",
        "chinese-metaphysics",
        "health-report",
        "tarot-astrology",
    ]
    assert sum(len(p["roles"]) for p in packs) == 76
    assert all(
        {skill for role in p["roles"] for skill in (role.get("skills") or [])} for p in packs
    )
    assert all(role.get("skills") for p in packs for role in p["roles"])
    by_id = {p["id"]: p for p in packs}
    assert all(s["source"] == "bundled" for s in by_id["investment"]["skills"])
    assert all(s["source"] == "bundled" for s in by_id["supply-chain-tracking"]["skills"])
    assert all(s["source"] == "bundled" for s in by_id["content"]["skills"])
    for pack_id in (
        "product-strategy",
        "design-prototype",
        "development-engineering",
        "qa-testing",
        "competitive-intelligence",
        "content-growth",
        "campaign-event",
        "short-video-growth",
        "contract-review",
        "compliance-review",
        "academic-research",
        "training-program",
        "recruiting-evaluation",
        "chinese-metaphysics",
        "health-report",
        "tarot-astrology",
    ):
        assert by_id[pack_id]["skills"]
        assert all(s["source"] == "skillhub" for s in by_id[pack_id]["skills"])
    for pack in packs:
        assert pack["added"] is False
        assert all(r["in_library"] is False for r in pack["roles"])
        # text resolved to real strings, not raw locale maps or dotted keys.
        assert pack["name"] and not pack["name"].startswith("agentTemplates.")
        assert pack["scenario"]


async def test_import_pack_creates_agents(svc: AgentPackService) -> None:
    res = await svc.import_pack(USER, "investment", **DEPLOY)
    assert res["created"] == 4
    assert res["skipped"] == 0
    assert sorted(r.slug for r in res["roles"]) == [
        "inv-earnings-tracker",
        "inv-industry-analyst",
        "inv-model-builder",
        "inv-report-writer",
    ]
    analyst = next(r for r in res["roles"] if r.slug == "inv-industry-analyst")
    assert analyst.avatar == "analyst"
    assert analyst.runtime == "claude_agent"
    # the pack's recommended effort wins over the deploy default
    assert analyst.effort == "high"
    assert analyst.source == "custom"
    assert "## 团队协作（Lead）" in analyst.instructions
    assert "同一个 Project" in analyst.instructions
    assert "Financial Modeler" in analyst.instructions
    # 投研 deploys with its global skill set + the Valuz MCPs (search + quotes).
    assert analyst.skills == [
        "sector-overview",
        "competitive-analysis",
        "comps",
        "idea-generation",
    ]
    assert analyst.connector_types == ["valuz-search", "valuz-data"]


async def test_import_supply_chain_tracking_pack(svc: AgentPackService) -> None:
    res = await svc.import_pack(USER, "supply-chain-tracking", **DEPLOY)
    assert res["created"] == 4
    assert sorted(r.slug for r in res["roles"]) == [
        "sct-bottleneck-analyst",
        "sct-chain-mapper",
        "sct-evidence-reviewer",
        "sct-theme-lead",
    ]
    lead = next(r for r in res["roles"] if r.slug == "sct-theme-lead")
    assert lead.name == "Theme Lead"
    assert lead.skills == ["serenity-unified-skill", "serenity-bottleneck-hunter"]
    assert lead.connector_types == ["valuz-search", "valuz-data"]
    assert "Supply Chain Tracking team" in lead.instructions
    assert "not investment advice" in lead.instructions


async def test_import_pack_registers_connectors(svc: AgentPackService) -> None:
    # A pack's connectors activate on import — registered as the user's
    # ConnectorRows (idempotent) so they resolve at session time. No built-in
    # pack ships connectors right now, so drive it with a synthetic manifest.
    from valuz_agent.modules.agent_packs.manifest import (
        AgentPackManifest,
        PackAgent,
        PackCollection,
        PackConnector,
    )

    manifest = AgentPackManifest(
        collection=PackCollection(name="Synthetic"),
        agents=[PackAgent(slug="a1", name="A1", connectors=["my-http"])],
        connectors=[
            PackConnector(
                slug="my-http",
                source="custom",
                display_name="My HTTP MCP",
                transport="http",
                url="https://example.com/mcp",
                auth_type="bearer",
                requires_credentials=True,
            )
        ],
    )
    await svc.import_manifest(USER, manifest, **DEPLOY)
    connector_svc = svc._agents._connectors
    slugs = {v.slug for v in await connector_svc.list_connectors(USER)}
    assert "my-http" in slugs
    # idempotent: a second import doesn't duplicate
    await svc.import_manifest(USER, manifest, **DEPLOY)
    again = [v for v in await connector_svc.list_connectors(USER) if v.slug == "my-http"]
    assert len(again) == 1


async def test_import_pack_resolves_retired_bundled_skills_from_market(
    svc: AgentPackService, tmp_path, monkeypatch
) -> None:
    # The packaged template tree is retired (builtin-resources §5.4): a pack's
    # ``bundled`` skills that the package no longer carries resolve from the
    # market. The import must hand exactly the missing slugs to the fallback.
    requested: list[list[str]] = []

    async def _fake_install(user_id: str, slugs: list[str]) -> list[str]:
        requested.append(list(slugs))
        return list(slugs)

    monkeypatch.setattr(
        "valuz_agent.modules.agent_packs.market_fallback.install_missing_bundled_skills",
        _fake_install,
    )
    await svc.import_pack(USER, "content", **DEPLOY)
    assert requested, "market fallback was not invoked for retired bundled skills"
    assert "xhs-topic-method" in requested[0]
    assert "xhs-note-writing" in requested[0]


async def test_import_pack_notifies_bundled_skill_lifecycle(
    svc: AgentPackService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from valuz_agent.ports.extensions import ext

    notified: list[tuple[str, tuple[str, ...]]] = []

    class Hook:
        async def after_bundled_skills_materialized(
            self,
            *,
            user_id: str,
            slugs: tuple[str, ...],
        ) -> None:
            notified.append((user_id, slugs))

    monkeypatch.setattr(ext, "skill_lifecycle", Hook())

    await svc.import_pack(USER, "investment", **DEPLOY)

    assert notified == [
        (
            USER,
            (
                "sector-overview",
                "competitive-analysis",
                "comps",
                "idea-generation",
                "dcf",
                "3-statement-model",
                "audit-xls",
                "earnings-analysis",
                "earnings-preview",
                "model-update",
                "initiating-coverage",
                "morning-note",
                "pptx-author",
            ),
        )
    ]


async def test_import_pack_indexes_embedded_skills(
    svc: AgentPackService, tmp_path, monkeypatch
) -> None:
    # A pack's embedded (user-authored) skills must be installed AND explicitly
    # indexed at import — otherwise the just-created agent that references them
    # can't resolve the skill until the next boot / periodic scan (≤30 min).
    # We spy the user-scope reindex (it writes via the global unit-of-work, out
    # of this test's reach) and assert the on-disk install, which is the
    # observable half of the same guarantee.
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(
        fsr.settings,
        "user_skills_dir",
        tmp_path / "user-skills",
    )

    calls: list[str] = []

    async def _spy_reindex_user(user_id: str) -> int:
        calls.append(user_id)
        return 0

    monkeypatch.setattr(
        "valuz_agent.modules.skills.service.reindex_user_skills",
        _spy_reindex_user,
    )

    from valuz_agent.modules.agent_packs.manifest import (
        AgentPackManifest,
        PackAgent,
        PackCollection,
        PackSkill,
    )
    from valuz_agent.modules.packs_common import build_archive, extract_archive

    skill_src = tmp_path / "src" / "my-skill"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("---\nname: my-skill\n---\n", encoding="utf-8")

    manifest = AgentPackManifest(
        collection=PackCollection(name="Embedded Pack"),
        agents=[PackAgent(slug="emb-a1", name="A1", skills=["my-skill"])],
        skills=[PackSkill(slug="my-skill", source="embedded")],
    )
    parsed, root = extract_archive(build_archive(manifest, {"my-skill": skill_src}))

    res = await svc.import_manifest(USER, parsed, embedded_skills_root=root, **DEPLOY)

    assert res["created"] == 1
    # explicit index trigger fired for the user-scope skill
    assert calls == [USER]
    # and the skill actually landed in the user library
    assert (tmp_path / "user-skills" / "my-skill" / "SKILL.md").is_file()


async def test_import_pack_idempotent(svc: AgentPackService) -> None:
    await svc.import_pack(USER, "investment", **DEPLOY)
    res2 = await svc.import_pack(USER, "investment", **DEPLOY)
    assert res2["created"] == 0
    assert res2["skipped"] == 4
    assert len({r.slug for r in res2["roles"]}) == 4


async def test_partial_import_only_fills_missing(svc: AgentPackService) -> None:
    await svc._agents.create_agent(USER, {"slug": "inv-model-builder", "name": "manual", **DEPLOY})
    res = await svc.import_pack(USER, "investment", **DEPLOY)
    assert res["created"] == 3
    assert res["skipped"] == 1


async def test_import_pack_upgrades_existing_lead_instructions(
    svc: AgentPackService,
) -> None:
    await svc._agents.create_agent(
        USER,
        {
            "slug": "inv-industry-analyst",
            "name": "manual",
            "instructions": "old lead instruction",
            **DEPLOY,
        },
    )

    res = await svc.import_pack(USER, "investment", **DEPLOY)

    assert res["created"] == 3
    assert res["skipped"] == 1
    lead = next(r for r in res["roles"] if r.slug == "inv-industry-analyst")
    assert "old lead instruction" in lead.instructions
    assert "## 团队协作（Lead）" in lead.instructions
    assert "标准协作流程" in lead.instructions


async def test_list_marks_added_after_import(svc: AgentPackService) -> None:
    await svc.import_pack(USER, "investment", **DEPLOY)
    packs = await svc.list_packs(USER)
    inv = next(p for p in packs if p["id"] == "investment")
    assert inv["added"] is True
    assert all(r["in_library"] for r in inv["roles"])
    product_strategy = next(p for p in packs if p["id"] == "product-strategy")
    assert product_strategy["added"] is False
    assert all(not r["in_library"] for r in product_strategy["roles"])


async def test_product_pack_has_no_equipment(svc: AgentPackService) -> None:
    # 产研 is a skill-less / connector-less team for now.
    res = await svc.import_pack(USER, "product", **DEPLOY)
    assert res["created"] == 4
    assert sorted(r.slug for r in res["roles"]) == [
        "prod-designer",
        "prod-engineer",
        "prod-pm",
        "prod-qa",
    ]
    for role in res["roles"]:
        assert role.skills == []
        assert role.connector_types == []


async def test_unknown_pack_raises(svc: AgentPackService) -> None:
    with pytest.raises(PackNotFound):
        await svc.import_pack(USER, "does-not-exist", **DEPLOY)


# -- import / export ------------------------------------------------------


def test_packaging_roundtrip(tmp_path) -> None:
    from valuz_agent.modules.agent_packs.manifest import (
        AgentPackManifest,
        PackAgent,
        PackCollection,
        PackSkill,
    )
    from valuz_agent.modules.packs_common import (
        build_archive,
        embedded_skill_dir,
        extract_archive,
    )

    # a real on-disk skill dir to embed as files
    skill_src = tmp_path / "my-skill"
    skill_src.mkdir()
    (skill_src / "SKILL.md").write_text("# My Skill\n", encoding="utf-8")

    manifest = AgentPackManifest(
        collection=PackCollection(name="My Pack"),
        agents=[
            PackAgent(slug="a1", name="Agent One", instructions="do things", skills=["my-skill"])
        ],
        skills=[PackSkill(slug="my-skill", source="embedded")],
    )
    data = build_archive(manifest, {"my-skill": skill_src})
    assert isinstance(data, bytes) and len(data) > 0

    parsed, root = extract_archive(data)
    assert [a.slug for a in parsed.agents] == ["a1"]
    assert parsed.agents[0].name == "Agent One"
    # the skill's files travel inside the archive
    extracted = embedded_skill_dir(root, "my-skill")
    assert extracted is not None
    assert (extracted / "SKILL.md").read_text(encoding="utf-8") == "# My Skill\n"


def test_extract_rejects_non_zip() -> None:
    from valuz_agent.modules.packs_common import PackArchiveError, extract_archive

    with pytest.raises(PackArchiveError):
        extract_archive(b"not a zip file")


async def test_export_import_roundtrip(svc: AgentPackService, tmp_path, monkeypatch) -> None:
    from valuz_agent.modules.agent_packs.errors import PackImportFailed
    from valuz_agent.modules.agent_packs.manifest import (
        AgentPackManifest,
        PackAgent,
        PackCollection,
        PackConnector,
    )

    # Seed the source library via a synthetic manifest: two agents, one wired to
    # a custom HTTP connector (the truly-portable kind — import + authorize).
    seed = AgentPackManifest(
        collection=PackCollection(name="My Team"),
        agents=[
            PackAgent(slug="r1", name="R1", instructions="lead", connectors=["my-http"]),
            PackAgent(slug="r2", name="R2", instructions="member"),
        ],
        connectors=[
            PackConnector(
                slug="my-http",
                source="custom",
                display_name="My HTTP MCP",
                transport="http",
                url="https://example.com/mcp",
                auth_type="bearer",
                requires_credentials=True,
            )
        ],
    )
    await svc.import_manifest(USER, seed, **DEPLOY)

    slugs = ["r1", "r2"]
    data = await svc.export_agents(USER, slugs, collection={"name": "My Team"})
    assert isinstance(data, bytes) and len(data) > 0

    # Import into a *separate* install (fresh db + secret store), simulating a
    # different machine — connector slugs are globally unique per install.
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "data_dir", tmp_path / "dest-data")
    monkeypatch.setattr(
        fsr.settings,
        "user_skills_dir",
        tmp_path / "dest-user-skills",
    )
    dest, session2, engine2 = await _build_service(tmp_path / "dest")
    try:
        preview = await dest.preview_import(USER, data)
        assert preview["preview_id"]
        assert preview["collection"]["name"] == "My Team"
        assert {a["slug"] for a in preview["agents"]} == set(slugs)
        assert all(not a["in_library"] for a in preview["agents"])
        # the connector + its "needs a key" flag survive the round-trip
        conn = next(c for c in preview["connectors"] if c["slug"] == "my-http")
        assert conn["requires_credentials"] is True

        result = await dest.confirm_import(USER, preview["preview_id"], **DEPLOY)
        assert result["created"] == 2
        dest_agents = {a.slug for a in await dest._agents.list_agents(USER)}
        assert set(slugs) <= dest_agents
        dest_conns = {v.slug for v in await dest._agents._connectors.list_connectors(USER)}
        assert "my-http" in dest_conns
        # the staged preview is consumed — confirming again fails
        with pytest.raises(PackImportFailed):
            await dest.confirm_import(USER, preview["preview_id"], **DEPLOY)
    finally:
        await session2.close()
        await engine2.dispose()


async def test_export_unknown_agent_raises(svc: AgentPackService) -> None:
    from valuz_agent.modules.agents.service import AgentNotFoundError

    with pytest.raises(AgentNotFoundError):
        await svc.export_agents(USER, ["nope"], collection=None)

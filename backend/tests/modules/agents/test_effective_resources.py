"""All-available resource resolution for Agentless and Valurion sessions."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.effective_resources import EffectiveResourceResolver
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
)
from valuz_agent.modules.docs.models import KnowledgeBaseRow
from valuz_agent.modules.skills.models import SkillIndexRow

OWNER = "owner-a"
OTHER = "owner-b"


@pytest.fixture
async def db(tmp_path) -> AsyncIterator:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'resources.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                SkillIndexRow.__table__,
                ConnectorRow.__table__,
                ConnectorAttrRow.__table__,
                ConnectorOAuthRow.__table__,
                KnowledgeBaseRow.__table__,
            ],
        )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


def _skill(owner: str, path, *, slug: str, **overrides) -> SkillIndexRow:
    values = {
        "user_id": owner,
        "slug": slug,
        "name": slug,
        "description": "",
        "scope": "user",
        "source": "filesystem",
        "source_path": str(path),
        "status": "available",
        "readonly": False,
        "is_locked": False,
        "deletable": True,
        "library_enabled": True,
    }
    values.update(overrides)
    return SkillIndexRow(**values)


def _connector(owner: str, *, slug: str, **overrides) -> ConnectorRow:
    values = {
        "user_id": owner,
        "slug": slug,
        "display_name": slug,
        "connector_type": "custom",
        "transport": "http",
        "auth_type": "none",
        "enabled": True,
        "status": "connected",
    }
    values.update(overrides)
    return ConnectorRow(**values)


async def test_resolver_includes_only_owner_authorized_runnable_resources(
    db,
    tmp_path,
) -> None:
    good_path = tmp_path / "good-skill"
    good_path.mkdir()
    locked_path = tmp_path / "locked-skill"
    locked_path.mkdir()
    other_path = tmp_path / "other-skill"
    other_path.mkdir()

    db.add_all(
        [
            _skill(OWNER, good_path, slug="good"),
            _skill(OWNER, locked_path, slug="locked", is_locked=True),
            _skill(OWNER, tmp_path / "missing", slug="missing"),
            _skill(OWNER, tmp_path / "disabled", slug="disabled", library_enabled=False),
            _skill(OTHER, other_path, slug="other"),
            _connector(OWNER, slug="connected"),
            _connector(OWNER, slug="disabled-connector", enabled=False),
            _connector(OWNER, slug="pending", auth_type="oauth", status="pending_auth"),
            _connector(OWNER, slug="local-stdio", transport="stdio"),
            _connector(OTHER, slug="other-connector"),
            KnowledgeBaseRow(
                id="kb-a",
                user_id=OWNER,
                name="Owner KB",
                root_path=str(tmp_path / "kb-a"),
            ),
            KnowledgeBaseRow(
                id="kb-b",
                user_id=OTHER,
                name="Other KB",
                root_path=str(tmp_path / "kb-b"),
            ),
        ]
    )
    await db.commit()

    manifest = await EffectiveResourceResolver.from_session(db).resolve(
        OWNER,
        runtime="claude_agent",
        supports_stdio=False,
    )

    assert [item.slug for item in manifest.skills] == ["good"]
    assert [item.slug for item in manifest.connectors] == ["connected"]
    assert [item.id for item in manifest.knowledge_bases] == ["kb-a"]
    assert {warning.code for warning in manifest.warnings} == {
        "skill_disabled",
        "skill_locked",
        "skill_path_missing",
        "connector_disabled",
        "connector_not_connected",
        "connector_transport_unsupported",
    }


async def test_manifest_is_secret_and_body_free(db, tmp_path) -> None:
    skill_path = tmp_path / "skill"
    skill_path.mkdir()
    connector = _connector(OWNER, slug="private")
    connector.headers_json = '{"Authorization":{"value":"SECRET","secret":true}}'
    db.add_all(
        [
            _skill(OWNER, skill_path, slug="skill"),
            connector,
            KnowledgeBaseRow(
                id="kb-a",
                user_id=OWNER,
                name="KB",
                root_path=str(tmp_path / "private-root"),
            ),
        ]
    )
    await db.commit()

    payload = (
        await EffectiveResourceResolver.from_session(db).resolve(
            OWNER,
            runtime="claude_agent",
            supports_stdio=True,
        )
    ).to_api()

    rendered = repr(payload)
    assert "SECRET" not in rendered
    assert "private-root" not in rendered
    assert payload["policy"] == "all_available"
    assert payload["counts"] == {"skills": 1, "connectors": 1, "knowledge_bases": 1}


class TestExplicitBindingPolicy:
    """The manifest answers for a self-created agent too, not just Valurion.

    It used to refuse anything but ``all_available``, so the composer's ``/``
    picker re-derived the answer from the agent's ``skills`` array — and that
    array does not carry the always-on baseline the host injects into every
    session. A self-created agent showed nothing for ``/skill-`` while
    ``skill-creator`` was loaded and usable.
    """

    @pytest.fixture(autouse=True)
    def _no_baseline(self, monkeypatch):
        """Default to an empty baseline; a test opts in explicitly."""
        monkeypatch.setattr(
            "valuz_agent.adapters.capability_resolver.always_on_skill_paths",
            lambda *, user_id: [],
        )

    async def _resolve(self, db, *, bound):
        return await EffectiveResourceResolver.from_session(db).resolve(
            OWNER, runtime="claude_agent", supports_stdio=True, bound_skill_slugs=bound
        )

    async def test_lists_exactly_what_the_agent_bound(self, db, tmp_path) -> None:
        for slug in ("bound", "unbound"):
            (tmp_path / slug).mkdir()
            db.add(_skill(OWNER, tmp_path / slug, slug=slug))
        await db.commit()

        manifest = await self._resolve(db, bound=["bound"])

        assert [s.slug for s in manifest.skills] == ["bound"]
        assert manifest.policy == "explicit"

    async def test_a_binding_survives_the_library_switch(self, db, tmp_path) -> None:
        # ``resolve_skill_slugs_to_paths`` — what the session builder actually
        # uses — reads the index for a path and never consults the switch, so
        # gating the manifest on it would under-report the session.
        (tmp_path / "off").mkdir()
        db.add(_skill(OWNER, tmp_path / "off", slug="off", library_enabled=False))
        await db.commit()

        manifest = await self._resolve(db, bound=["off"])

        assert [s.slug for s in manifest.skills] == ["off"]

    async def test_a_dead_binding_is_reported_not_silently_dropped(self, db) -> None:
        manifest = await self._resolve(db, bound=["ghost"])

        assert manifest.skills == ()
        assert [w.code for w in manifest.warnings] == ["skill_unknown"]

    async def test_an_absolute_path_binding_resolves_by_basename(self, db, tmp_path) -> None:
        (tmp_path / "bound").mkdir()
        db.add(_skill(OWNER, tmp_path / "bound", slug="bound"))
        await db.commit()

        manifest = await self._resolve(db, bound=["/somewhere/else/bound"])

        assert [s.slug for s in manifest.skills] == ["bound"]

    async def test_the_baseline_rides_an_agent_that_bound_nothing(
        self, db, tmp_path, monkeypatch
    ) -> None:
        baseline = tmp_path / "skill-creator"
        baseline.mkdir()
        monkeypatch.setattr(
            "valuz_agent.adapters.capability_resolver.always_on_skill_paths",
            lambda *, user_id: [str(baseline)],
        )

        manifest = await self._resolve(db, bound=[])

        assert [s.slug for s in manifest.skills] == ["skill-creator"]

    async def test_the_baseline_is_listed_once_when_also_bound(
        self, db, tmp_path, monkeypatch
    ) -> None:
        baseline = tmp_path / "skill-creator"
        baseline.mkdir()
        db.add(_skill(OWNER, baseline, slug="skill-creator"))
        await db.commit()
        monkeypatch.setattr(
            "valuz_agent.adapters.capability_resolver.always_on_skill_paths",
            lambda *, user_id: [str(baseline)],
        )

        manifest = await self._resolve(db, bound=["skill-creator"])

        assert [s.slug for s in manifest.skills] == ["skill-creator"]

    async def test_all_available_still_gets_the_library_plus_baseline(
        self, db, tmp_path, monkeypatch
    ) -> None:
        (tmp_path / "lib").mkdir()
        baseline = tmp_path / "citation"
        baseline.mkdir()
        db.add(_skill(OWNER, tmp_path / "lib", slug="lib"))
        await db.commit()
        monkeypatch.setattr(
            "valuz_agent.adapters.capability_resolver.always_on_skill_paths",
            lambda *, user_id: [str(baseline)],
        )

        manifest = await self._resolve(db, bound=None)

        assert sorted(s.slug for s in manifest.skills) == ["citation", "lib"]
        assert manifest.policy == "all_available"

"""Tests for ``valuz_agent.facade.resources.ResourceLibrary``.

Agent kind: full round-trip (save → get → list) against a real in-memory SQLite,
using the ``monkeypatch.setattr(db_mod, "AsyncSessionLocal", ...)`` pattern so
``async_unit_of_work`` binds to the test DB.

Skill / connector / kb kinds: list-smoke only (returns [] on empty DB) — the
real services are heavy (filesystem scans, parser setup, secret store) so we
just verify the facade wiring doesn't blow up rather than doing a full
end-to-end with all the moving parts.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from valuz_agent.facade.resources import ResourceLibrary, ResourceRef, ResourceSnapshot
from valuz_agent.infra.database import Base
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow

# ---------------------------------------------------------------------------
# DB fixture — monkeypatches AsyncSessionLocal so async_unit_of_work uses it
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def agent_db(monkeypatch):
    """In-memory async SQLite seeded with agent + member tables.

    Monkeypatches ``infra.db.AsyncSessionLocal`` so every
    ``async_unit_of_work()`` call inside ``ResourceLibrary`` binds to this
    test database.
    """
    import valuz_agent.infra.db as db_mod

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[AgentRow.__table__, ProjectMemberRow.__table__]
            )
        )

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", session_factory)
    yield session_factory
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helper — agent snapshot factory
# ---------------------------------------------------------------------------

USER_ID = "local-test-owner"


def _agent_snapshot(slug: str = "test-agent", name: str = "Test Agent") -> ResourceSnapshot:
    return ResourceSnapshot(
        kind="agent",
        key=slug,
        name=name,
        data={
            "slug": slug,
            "name": name,
            "description": "A test agent",
            "instructions": "Do stuff.",
            "runtime": "claude_agent",
            "model": "claude-sonnet-4-6",
            "skills": [],
            "connector_types": [],
            "provider_id": None,
            "effort": None,
            "avatar": None,
        },
    )


# ---------------------------------------------------------------------------
# Agent round-trip tests
# ---------------------------------------------------------------------------


class TestAgentRoundTrip:
    async def test_save_then_get_returns_matching_snapshot(self, agent_db) -> None:
        lib = ResourceLibrary()
        snap = _agent_snapshot("my-agent", "My Agent")
        ref = await lib.save(USER_ID, snap)

        assert ref.kind == "agent"
        assert ref.key == "my-agent"
        assert ref.name == "My Agent"

        retrieved = await lib.get(USER_ID, "agent", "my-agent")
        assert retrieved is not None
        assert retrieved.kind == "agent"
        assert retrieved.key == "my-agent"
        assert retrieved.name == "My Agent"
        assert retrieved.data["instructions"] == "Do stuff."

    async def test_list_includes_saved_agent(self, agent_db) -> None:
        lib = ResourceLibrary()
        await lib.save(USER_ID, _agent_snapshot("list-me", "List Me"))

        refs = await lib.list(USER_ID, "agent")
        keys = [r.key for r in refs]
        assert "list-me" in keys

    async def test_get_missing_agent_returns_none(self, agent_db) -> None:
        lib = ResourceLibrary()
        result = await lib.get(USER_ID, "agent", "does-not-exist")
        assert result is None

    async def test_save_existing_slug_updates_in_place(self, agent_db) -> None:
        lib = ResourceLibrary()
        await lib.save(USER_ID, _agent_snapshot("upd-agent", "Original"))

        updated_snap = _agent_snapshot("upd-agent", "Updated")
        updated_snap.data["instructions"] = "Updated instructions."
        ref2 = await lib.save(USER_ID, updated_snap)
        assert ref2.key == "upd-agent"

        retrieved = await lib.get(USER_ID, "agent", "upd-agent")
        assert retrieved is not None
        assert retrieved.data["instructions"] == "Updated instructions."

    async def test_list_returns_empty_for_different_user(self, agent_db) -> None:
        lib = ResourceLibrary()
        await lib.save(USER_ID, _agent_snapshot("private-agent"))

        refs = await lib.list("other-user", "agent")
        assert all(r.key != "private-agent" for r in refs)


# ---------------------------------------------------------------------------
# Skill list smoke
# ---------------------------------------------------------------------------


class TestSkillListSmoke:
    async def test_list_skill_returns_list(self, monkeypatch) -> None:
        """list("skill") should return a list (possibly empty) without crashing."""
        # Patch get_skill_service_for_user to return a stub that yields a minimal service
        from valuz_agent.modules.skills.models import SkillsCatalog

        class _FakeSkillService:
            async def list_catalog(
                self, user_id: str, project_id: str, **_: object
            ) -> SkillsCatalog:
                return SkillsCatalog(project_id=project_id, skills=[])

        async def _fake_get_skill_service(user_id: str):  # type: ignore[return]
            del user_id
            yield _FakeSkillService()

        monkeypatch.setattr(
            "valuz_agent.api.deps.get_skill_service_for_user",
            _fake_get_skill_service,
        )

        lib = ResourceLibrary()
        refs = await lib.list(USER_ID, "skill")
        assert isinstance(refs, list)

    async def test_list_skill_maps_skills_to_refs(self, monkeypatch) -> None:
        from valuz_agent.modules.skills.models import SkillsCatalog, SkillView

        fake_view = SkillView(
            id="skill-1",
            name="My Skill",
            description="desc",
            scope="user",
            source="user",
            path="/fake/path",
            slug="my-skill",
            enabled=True,
        )

        class _FakeSkillService:
            async def list_catalog(
                self, user_id: str, project_id: str, **_: object
            ) -> SkillsCatalog:
                return SkillsCatalog(project_id=project_id, skills=[fake_view])

        async def _fake_get_skill_service(user_id: str):  # type: ignore[return]
            del user_id
            yield _FakeSkillService()

        monkeypatch.setattr(
            "valuz_agent.api.deps.get_skill_service_for_user",
            _fake_get_skill_service,
        )

        lib = ResourceLibrary()
        refs = await lib.list(USER_ID, "skill")
        assert len(refs) == 1
        assert refs[0] == ResourceRef(kind="skill", key="my-skill", name="My Skill")


# ---------------------------------------------------------------------------
# Connector list smoke
# ---------------------------------------------------------------------------


class TestConnectorListSmoke:
    async def test_list_connector_returns_list(self, tmp_path, monkeypatch) -> None:
        """list("connector") should return a list without crashing on empty DB."""
        import valuz_agent.infra.db as db_mod

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from valuz_agent.modules.connectors.models import (
            ConnectorAttrRow,
            ConnectorOAuthRow,
            ConnectorRow,
        )

        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(
                    c,
                    tables=[
                        ConnectorRow.__table__,
                        ConnectorAttrRow.__table__,
                        ConnectorOAuthRow.__table__,
                    ],
                )
            )

        monkeypatch.setattr(
            db_mod, "AsyncSessionLocal", async_sessionmaker(bind=engine, expire_on_commit=False)
        )
        # Patch secret store path to avoid touching real keychain. ``secrets_dir``
        # is a computed property (= data_dir / "secrets") — patch the field it
        # derives from, not the property itself.
        from valuz_agent.infra.config import settings

        monkeypatch.setattr(settings, "data_dir", tmp_path)

        lib = ResourceLibrary()
        refs = await lib.list(USER_ID, "connector")
        assert isinstance(refs, list)


# ---------------------------------------------------------------------------
# KB list smoke
# ---------------------------------------------------------------------------


class TestKbListSmoke:
    async def test_list_kb_returns_list(self, monkeypatch) -> None:
        """list("kb") should return a list without crashing."""

        class _FakeDocService:
            async def list_kbs(self, user_id):
                return []

        async def _fake_get_document_service():  # type: ignore[return]
            yield _FakeDocService()

        monkeypatch.setattr("valuz_agent.api.deps.get_document_service", _fake_get_document_service)

        lib = ResourceLibrary()
        refs = await lib.list(USER_ID, "kb")
        assert isinstance(refs, list)
        assert refs == []


# ---------------------------------------------------------------------------
# Connector OAuth access token seam (``get_connector_access_token``)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def connector_oauth_db(monkeypatch):
    """In-memory async SQLite seeded with connector + attr + oauth tables.

    Same monkeypatch pattern as ``agent_db`` above, scoped to the three
    connector-related tables (main row + the two side tables the OAuth token
    and header/param creds live in).
    """
    import valuz_agent.infra.db as db_mod
    from valuz_agent.modules.connectors.models import (
        ConnectorAttrRow,
        ConnectorOAuthRow,
        ConnectorRow,
    )

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    ConnectorRow.__table__,
                    ConnectorAttrRow.__table__,
                    ConnectorOAuthRow.__table__,
                ],
            )
        )

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", session_factory)
    yield session_factory
    await engine.dispose()


async def _make_connector(
    session_factory,
    *,
    user_id: str = USER_ID,
    slug: str,
    auth_type: str = "oauth",
    enabled: bool = True,
    access_token: str | None = None,
    expires_at: int | None = None,
) -> None:
    """Insert a connector row (+ its OAuth side row) via the real datastore.

    Goes through ``ConnectorDatastore.create`` (not a raw INSERT) so the
    side-table split (``ConnectorOAuthRow``) is persisted exactly the way the
    service layer does it — see ``test_connector_credential_split.py``.
    """
    import json

    from valuz_agent.modules.connectors.datastore import ConnectorDatastore
    from valuz_agent.modules.connectors.models import ConnectorRow

    row = ConnectorRow(
        slug=slug,
        display_name=slug,
        connector_type="builtin",
        transport="http",
        url="https://mcp.example.test/mcp",
        auth_type=auth_type,
        enabled=enabled,
    )
    if access_token is not None:
        row.oauth_token_json = json.dumps({"access_token": access_token, "refresh_token": "r1"})
    if expires_at is not None:
        row.oauth_token_expires_at = expires_at

    session = session_factory()
    try:
        await ConnectorDatastore(session).create(user_id, row)
    finally:
        await session.close()


class TestConnectorAccessToken:
    """``ResourceLibrary.get_connector_access_token`` — connector OAuth seam.

    Read-only facade method editions use to authenticate a data plane against
    a connector's OAuth identity (e.g. ``valuz-search``) without importing
    ``modules.connectors`` internals directly.
    """

    async def test_returns_token_for_connected_oauth_connector(self, connector_oauth_db) -> None:
        from valuz_agent.infra.time_utils import now_ms

        await _make_connector(
            connector_oauth_db,
            slug="valuz-search",
            access_token="tok-abc123",
            expires_at=now_ms() + 3_600_000,
        )

        lib = ResourceLibrary()
        token = await lib.get_connector_access_token(USER_ID, "valuz-search")
        assert token == "tok-abc123"

    async def test_returns_none_when_token_json_empty(self, connector_oauth_db) -> None:
        await _make_connector(
            connector_oauth_db,
            slug="valuz-search",
            access_token=None,
        )

        lib = ResourceLibrary()
        token = await lib.get_connector_access_token(USER_ID, "valuz-search")
        assert token is None

    async def test_returns_none_when_slug_not_found(self, connector_oauth_db) -> None:
        lib = ResourceLibrary()
        token = await lib.get_connector_access_token(USER_ID, "does-not-exist")
        assert token is None

    async def test_returns_none_when_auth_type_not_oauth(self, connector_oauth_db) -> None:
        from valuz_agent.infra.time_utils import now_ms

        await _make_connector(
            connector_oauth_db,
            slug="custom-http",
            auth_type="none",
            access_token="tok-should-be-ignored",
            expires_at=now_ms() + 3_600_000,
        )

        lib = ResourceLibrary()
        token = await lib.get_connector_access_token(USER_ID, "custom-http")
        assert token is None

    async def test_returns_none_when_connector_disabled(self, connector_oauth_db) -> None:
        """Bonus case: the facade also gates on ``enabled`` (disabled connectors
        must not hand out a live token even if OAuth creds are still stored)."""
        from valuz_agent.infra.time_utils import now_ms

        await _make_connector(
            connector_oauth_db,
            slug="valuz-search",
            enabled=False,
            access_token="tok-abc123",
            expires_at=now_ms() + 3_600_000,
        )

        lib = ResourceLibrary()
        token = await lib.get_connector_access_token(USER_ID, "valuz-search")
        assert token is None


# ---------------------------------------------------------------------------
# Project kind — list + get + save-raises (smoke via fake services)
# ---------------------------------------------------------------------------


class TestProjectFacade:
    async def test_list_project_excludes_chat_projects(self, monkeypatch) -> None:
        """list("project") should skip ``kind='chat'`` rows — only project-kind is exportable."""

        # ProjectDatastore.list_projects returns ProjectRow ORM objects; here we
        # stand in with simple objects exposing the same attributes.
        class _Row:
            def __init__(self, id: str, name: str, kind: str) -> None:
                self.id = id
                self.name = name
                self.kind = kind

        class _FakeDs:
            def __init__(self, _db) -> None:  # accepts the db arg as ProjectDatastore does
                pass

            async def list_projects(self, user_id: str):
                return [
                    _Row("p1", "Real Project", "project"),
                    _Row("c1", "A Chat", "chat"),
                    _Row("p2", "Another", "project"),
                ]

        # Stub the unit-of-work so the facade doesn't need a real DB.
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_uow(commit: bool = True):
            yield object()

        monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", _fake_uow)
        monkeypatch.setattr("valuz_agent.modules.projects.datastore.ProjectDatastore", _FakeDs)

        lib = ResourceLibrary()
        refs = await lib.list(USER_ID, "project")
        assert [(r.kind, r.key, r.name) for r in refs] == [
            ("project", "p1", "Real Project"),
            ("project", "p2", "Another"),
        ]

    async def test_get_project_returns_snapshot_with_valuzpack_bundle(self, monkeypatch) -> None:
        """get("project") should attach a base64-encoded .valuzpack bundle to ``files``."""
        import base64

        class _Detail:
            def __init__(self) -> None:
                self.name = "Snap Project"
                self.kind = "project"
                self.icon = "📁"
                self.instructions_md = "# Notes\nhello"

        class _FakeProjectService:
            async def get_project(self, user_id, project_id):
                return _Detail()

        class _FakePackService:
            async def export_project(self, user_id, project_id) -> bytes:
                return b"VALUZPACK-BINARY-CONTENT"

        async def _fake_get_project_service():  # type: ignore[return]
            yield _FakeProjectService()

        async def _fake_get_pack_service():  # type: ignore[return]
            yield _FakePackService()

        monkeypatch.setattr("valuz_agent.api.deps.get_project_service", _fake_get_project_service)
        monkeypatch.setattr("valuz_agent.api.deps.get_project_pack_service", _fake_get_pack_service)

        lib = ResourceLibrary()
        snap = await lib.get(USER_ID, "project", "p1")
        assert snap is not None
        assert snap.kind == "project"
        assert snap.key == "p1"
        assert snap.name == "Snap Project"
        assert snap.data["bundle_size"] == len(b"VALUZPACK-BINARY-CONTENT")
        assert snap.files is not None
        encoded = snap.files["bundle.valuzpack"]
        assert base64.b64decode(encoded) == b"VALUZPACK-BINARY-CONTENT"

    async def test_get_project_returns_none_when_missing(self, monkeypatch) -> None:
        """get("project", missing) returns None — ProjectService.get_project raises KeyError."""

        class _FakeProjectService:
            async def get_project(self, user_id, project_id):
                raise KeyError(project_id)

        async def _fake_get_project_service():  # type: ignore[return]
            yield _FakeProjectService()

        monkeypatch.setattr("valuz_agent.api.deps.get_project_service", _fake_get_project_service)

        lib = ResourceLibrary()
        snap = await lib.get(USER_ID, "project", "nope")
        assert snap is None

    async def test_save_project_raises_not_implemented(self) -> None:
        """save("project") raises — pull needs preview+confirm with a user-picked folder."""
        import pytest

        lib = ResourceLibrary()
        snap = ResourceSnapshot(
            kind="project",
            key="p1",
            name="Anything",
            data={},
            files={"bundle.valuzpack": "aGVsbG8="},
        )
        with pytest.raises(NotImplementedError, match="preview"):
            await lib.save(USER_ID, snap)


# ---------------------------------------------------------------------------
# Automation kind — list + get + save (smoke via fake services)
# ---------------------------------------------------------------------------


def _fake_automation_item(automation_id: str, name: str):
    """Minimal stand-in for ``AutomationItemResponse`` — only attrs the facade reads."""

    class _Item:
        pass

    item = _Item()
    item.automation_id = automation_id  # type: ignore[attr-defined]
    item.name = name  # type: ignore[attr-defined]
    return item


def _fake_automation_detail(
    automation_id: str = "a1",
    name: str = "Daily Report",
    trigger_kind: str = "cron",
):
    """Minimal stand-in for ``AutomationDetailResponse`` — only attrs the facade reads."""
    from valuz_agent.modules.automations.schemas import (
        CronTrigger,
        IntervalTrigger,
        ManualTrigger,
    )

    if trigger_kind == "cron":
        trigger = CronTrigger(cron_expr="0 9 * * *", timezone="UTC")
    elif trigger_kind == "interval":
        trigger = IntervalTrigger(seconds=60)
    else:
        trigger = ManualTrigger()

    class _Detail:
        pass

    d = _Detail()
    d.automation_id = automation_id  # type: ignore[attr-defined]
    d.name = name  # type: ignore[attr-defined]
    d.agent_kind = "project_member"  # type: ignore[attr-defined]
    d.agent_slug = "news-agent"  # type: ignore[attr-defined]
    d.agent_name = "News Agent"  # type: ignore[attr-defined]
    d.project_id = "proj-1"  # type: ignore[attr-defined]
    d.project_name = "Daily Reports"  # type: ignore[attr-defined]
    d.project_kind = "project"  # type: ignore[attr-defined]
    d.action_kind = "chat"  # type: ignore[attr-defined]
    d.prompt_template = "Summarise news"  # type: ignore[attr-defined]
    d.trigger = trigger  # type: ignore[attr-defined]
    d.status = "enabled"  # type: ignore[attr-defined]
    return d


class TestAutomationFacade:
    async def test_list_automation_returns_refs(self, monkeypatch) -> None:
        """list("automation") maps each AutomationItemResponse to a ResourceRef by automation_id."""

        class _FakeAutomationService:
            async def list_all_automations(self, user_id):
                return [
                    _fake_automation_item("a1", "Daily"),
                    _fake_automation_item("a2", "Hourly"),
                ]

        async def _fake_get_automation_service():  # type: ignore[return]
            yield _FakeAutomationService()

        monkeypatch.setattr(
            "valuz_agent.api.deps.get_automation_service", _fake_get_automation_service
        )

        lib = ResourceLibrary()
        refs = await lib.list(USER_ID, "automation")
        assert refs == [
            ResourceRef(kind="automation", key="a1", name="Daily"),
            ResourceRef(kind="automation", key="a2", name="Hourly"),
        ]

    async def test_get_automation_returns_snapshot_with_trigger_dict(self, monkeypatch) -> None:
        """get("automation") should serialise the discriminated trigger union to a plain dict."""

        class _FakeAutomationService:
            async def get_automation_detail(self, automation_id, user_id=None):
                return _fake_automation_detail(automation_id=automation_id)

        async def _fake_get_automation_service():  # type: ignore[return]
            yield _FakeAutomationService()

        monkeypatch.setattr(
            "valuz_agent.api.deps.get_automation_service", _fake_get_automation_service
        )

        lib = ResourceLibrary()
        snap = await lib.get(USER_ID, "automation", "a1")
        assert snap is not None
        assert snap.kind == "automation"
        assert snap.key == "a1"
        assert snap.name == "Daily Report"
        assert snap.data["agent_slug"] == "news-agent"
        assert snap.data["project_id_ref"] == "proj-1"
        # Discriminated union → plain dict (kind discriminator preserved)
        assert snap.data["trigger"]["kind"] == "cron"
        assert snap.data["trigger"]["cron_expr"] == "0 9 * * *"
        assert snap.files is None

    async def test_get_automation_returns_none_when_missing(self, monkeypatch) -> None:
        """get("automation", missing) returns None — AutomationService raises AutomationNotFound."""
        from valuz_agent.modules.automations.errors import AutomationNotFound

        class _FakeAutomationService:
            async def get_automation_detail(self, automation_id, user_id=None):
                raise AutomationNotFound()

        async def _fake_get_automation_service():  # type: ignore[return]
            yield _FakeAutomationService()

        monkeypatch.setattr(
            "valuz_agent.api.deps.get_automation_service", _fake_get_automation_service
        )

        lib = ResourceLibrary()
        snap = await lib.get(USER_ID, "automation", "missing")
        assert snap is None

    async def test_save_automation_creates_when_key_unknown(self, monkeypatch) -> None:
        """save("automation") falls through to AutomationService.create when the key is new."""
        from valuz_agent.modules.automations.errors import AutomationNotFound

        create_called_with: dict = {}

        class _FakeAutomationService:
            async def get_automation_detail(self, automation_id, user_id=None):
                raise AutomationNotFound()

            async def create(self, payload, *, user_id=None):
                create_called_with["payload"] = payload
                create_called_with["user_id"] = user_id
                return _fake_automation_detail(automation_id="created-id", name=payload.name)

            async def update(self, *args, **kwargs):  # pragma: no cover — should not be called
                raise AssertionError("update should not be called when key is unknown")

        async def _fake_get_automation_service():  # type: ignore[return]
            yield _FakeAutomationService()

        monkeypatch.setattr(
            "valuz_agent.api.deps.get_automation_service", _fake_get_automation_service
        )

        lib = ResourceLibrary()
        snap = ResourceSnapshot(
            kind="automation",
            key="never-seen",
            name="Imported Auto",
            data={
                "name": "Imported Auto",
                "project_kind": "project",
                "project_id_ref": "proj-1",
                "agent_kind": "project_member",
                "agent_slug": "news-agent",
                "prompt_template": "Summarise",
                "action_kind": "chat",
                "trigger": {"kind": "cron", "cron_expr": "0 9 * * *", "timezone": "UTC"},
            },
        )
        ref = await lib.save(USER_ID, snap)
        assert ref.kind == "automation"
        assert ref.key == "created-id"
        assert ref.name == "Imported Auto"
        # Verify the payload the facade assembled matches the snapshot
        assert create_called_with["user_id"] == USER_ID
        assert create_called_with["payload"].name == "Imported Auto"
        assert create_called_with["payload"].project_id == "proj-1"
        assert create_called_with["payload"].trigger.cron_expr == "0 9 * * *"

    async def test_save_automation_updates_when_key_exists(self, monkeypatch) -> None:
        """save("automation") routes to AutomationService.update when the key resolves locally."""

        update_called_with: dict = {}

        class _FakeAutomationService:
            async def get_automation_detail(self, automation_id, user_id=None):
                return _fake_automation_detail(automation_id=automation_id)

            async def create(self, *args, **kwargs):  # pragma: no cover — should not be called
                raise AssertionError("create should not be called when key exists")

            async def update(self, automation_id, payload, *, user_id=None):
                update_called_with["automation_id"] = automation_id
                update_called_with["payload"] = payload
                return _fake_automation_detail(
                    automation_id=automation_id, name=payload.name or "x"
                )

        async def _fake_get_automation_service():  # type: ignore[return]
            yield _FakeAutomationService()

        monkeypatch.setattr(
            "valuz_agent.api.deps.get_automation_service", _fake_get_automation_service
        )

        lib = ResourceLibrary()
        snap = ResourceSnapshot(
            kind="automation",
            key="existing-id",
            name="Updated Name",
            data={
                "name": "Updated Name",
                "prompt_template": "New prompt",
                "agent_slug": "news-agent",
                "action_kind": "chat",
                "trigger": {"kind": "manual"},
            },
        )
        ref = await lib.save(USER_ID, snap)
        assert ref.key == "existing-id"
        assert update_called_with["automation_id"] == "existing-id"
        assert update_called_with["payload"].name == "Updated Name"
        assert update_called_with["payload"].trigger.kind == "manual"


# ---------------------------------------------------------------------------
# Playbook — definitions are the resource; versions and runs stay local
# ---------------------------------------------------------------------------


class _FakeDefinition:
    def __init__(
        self,
        definition_id: str = "pb-1",
        name: str = "Quarterly Review",
        project_id: str | None = "proj-1",
        current_version: int = 3,
    ) -> None:
        self.id = definition_id
        self.name = name
        self.project_id = project_id
        self.current_version = current_version
        self.status = "active"
        self.origin = "user"


class _FakeVersion:
    def __init__(self, version: int = 3) -> None:
        self.version = version
        self.content = "1. pull filings\n2. summarise"
        self.goal = "LEGACY MIRROR — must not travel"
        self.reference_metadata = [{"kind": "doc", "id": "d1"}]
        self.default_executor = {"agent_slug": "research"}
        self.applicability = {"sector": "tech"}
        self.inputs = [{"name": "ticker"}]
        self.stages = [{"name": "collect"}]
        self.context_reads = ["ctx.a"]
        self.context_writes = [{"key": "ctx.b"}]
        self.required_skills = ["search"]
        self.allowed_skills = ["search", "browse"]
        self.conditions = [{"when": "always"}]
        self.approvals = [{"stage": "collect"}]
        self.outputs = ["report"]
        self.failure_policy = "stop"


def _patch_playbook_service(monkeypatch, service_cls, project=None):
    """Bind the facade's playbook branch to ``service_cls`` and a stub project lib."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_uow(commit: bool = True):
        yield object()

    class _FakeProjectLibrary:
        async def get(self, user_id: str, project_id: str):
            return project

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", _fake_uow)
    monkeypatch.setattr(
        "valuz_agent.modules.playbooks.service.PlaybookService", service_cls
    )
    monkeypatch.setattr("valuz_agent.facade.projects.ProjectLibrary", _FakeProjectLibrary)


class TestPlaybookFacade:
    async def test_list_playbook_returns_definition_refs(self, monkeypatch) -> None:
        """list("playbook") maps every definition the user owns to a ResourceRef by id."""

        class _FakeService:
            def __init__(self, _db, _projects) -> None:
                pass

            async def list_definitions(self, user_id, project_id=None):
                assert project_id is None, "every definition, not just one project's"
                return [
                    _FakeDefinition("pb-1", "Quarterly Review"),
                    _FakeDefinition("pb-2", "Earnings Recap", project_id=None),
                ]

        _patch_playbook_service(monkeypatch, _FakeService)

        lib = ResourceLibrary()
        refs = await lib.list(USER_ID, "playbook")
        assert refs == [
            ResourceRef(kind="playbook", key="pb-1", name="Quarterly Review"),
            ResourceRef(kind="playbook", key="pb-2", name="Earnings Recap"),
        ]

    async def test_get_playbook_exports_the_current_version_body(self, monkeypatch) -> None:
        """get("playbook") flattens the definition's current version into ``data``."""

        class _FakeService:
            def __init__(self, _db, _projects) -> None:
                pass

            async def get_definition(self, user_id, definition_id):
                return _FakeDefinition(definition_id)

            async def get_version(self, user_id, definition_id, version):
                assert version == 3, "must export the definition's current version"
                return _FakeVersion(version)

        class _Project:
            name = "Research"

        _patch_playbook_service(monkeypatch, _FakeService, project=_Project())

        lib = ResourceLibrary()
        snap = await lib.get(USER_ID, "playbook", "pb-1")
        assert snap is not None
        assert snap.kind == "playbook"
        assert snap.key == "pb-1"
        assert snap.name == "Quarterly Review"
        assert snap.data["content"] == "1. pull filings\n2. summarise"
        assert snap.data["stages"] == [{"name": "collect"}]
        assert snap.data["status"] == "active"
        # Project travels as a display-only reference, never as a binding.
        assert snap.data["project_id_ref"] == "proj-1"
        assert snap.data["project_name_ref"] == "Research"
        # The deprecated migration mirror stays local.
        assert "goal" not in snap.data
        assert snap.files is None

    async def test_get_playbook_without_project_leaves_the_refs_empty(self, monkeypatch) -> None:
        """A definition outside any Project exports with null project refs."""

        class _FakeService:
            def __init__(self, _db, _projects) -> None:
                pass

            async def get_definition(self, user_id, definition_id):
                return _FakeDefinition(definition_id, project_id=None)

            async def get_version(self, user_id, definition_id, version):
                return _FakeVersion(version)

        _patch_playbook_service(monkeypatch, _FakeService)

        lib = ResourceLibrary()
        snap = await lib.get(USER_ID, "playbook", "pb-2")
        assert snap is not None
        assert snap.data["project_id_ref"] is None
        assert snap.data["project_name_ref"] is None

    async def test_get_playbook_returns_none_when_missing(self, monkeypatch) -> None:
        """PlaybookService raises LookupError for an unknown/foreign definition."""

        class _FakeService:
            def __init__(self, _db, _projects) -> None:
                pass

            async def get_definition(self, user_id, definition_id):
                raise LookupError("playbook_definition_not_found")

            async def get_version(self, user_id, definition_id, version):  # pragma: no cover
                raise AssertionError("must not be reached")

        _patch_playbook_service(monkeypatch, _FakeService)

        lib = ResourceLibrary()
        assert await lib.get(USER_ID, "playbook", "nope") is None

    async def test_save_playbook_is_not_implemented(self, monkeypatch) -> None:
        """Playbook is export-only for now — importing needs its own conflict story."""
        lib = ResourceLibrary()
        snap = ResourceSnapshot(
            kind="playbook", key="pb-1", name="Quarterly Review", data={"content": "x"}
        )
        with pytest.raises(NotImplementedError):
            await lib.save(USER_ID, snap)

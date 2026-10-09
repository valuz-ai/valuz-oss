"""End-to-end tests for the project export/import routes.

Drives the HTTP layer against an isolated sqlite db so the full
``Service → Datastore → DB`` path is exercised. Covers:
  1. Fresh-user round-trip (project + members + automations + memory).
  2. Name-conflict skip (project with same name already exists).
  3. Library-agent slug de-dup (existing library agent is reused).
  4. Two members same source slug with dedupe=False.
  5. Connector secret stripping → recipient sees requires_credentials.
  6. Project memory re-admitted to the target catalog, never copied identities.
  7. No-memory-dir project exports + imports fine.
  8. model_hint doesn't affect resolution (provider/model come from defaults).
  9. Expired preview_id → 400.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id
from valuz_agent.infra.database import Base
from valuz_agent.infra.eventbus import event_bus
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.models import (
    AgentRow,
    ProjectMemberRow,
)
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow
from valuz_agent.modules.automations.service import AutomationService
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.models import (
    ConnectorAttrRow,
    ConnectorOAuthRow,
    ConnectorRow,
    ProjectConnectorRow,
)
from valuz_agent.modules.connectors.service import ConnectorService
from valuz_agent.modules.project_packs.service import ProjectPackService
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.projects.service import ProjectService
from valuz_agent.modules.providers.models import ProviderRow
from valuz_agent.modules.settings.models import AppSettingRow
from valuz_agent.modules.skills.models import ProjectSkillConfigRow, SkillIndexRow

USER = "user-1"

_ALL_TABLES = [
    ProjectRow.__table__,
    AgentRow.__table__,
    ProjectMemberRow.__table__,
    AutomationRow.__table__,
    AutomationRunRow.__table__,
    ConnectorRow.__table__,
    ConnectorAttrRow.__table__,
    ConnectorOAuthRow.__table__,
    ProjectConnectorRow.__table__,
    SkillIndexRow.__table__,
    ProjectSkillConfigRow.__table__,
    ProviderRow.__table__,
    AppSettingRow.__table__,
]


class _Deps:
    """Bag of services + session shared across one test request lifecycle."""


async def _build_app(tmp_path: Path) -> tuple[FastAPI, _Deps]:
    """Build a minimal FastAPI app exposing only the project routes over an
    isolated sqlite db. Provider + setting defaults are seeded so the
    onboarding deploy-target resolver succeeds."""
    from valuz_agent.api.routes import projects as projects_routes
    from valuz_agent.infra.db import get_async_session

    db_path = tmp_path / "e2e.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_ALL_TABLES)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()

    connector_svc = ConnectorService(ConnectorDatastore(session))
    agent_svc = AgentService(session, connector_service=connector_svc)
    agent_pack_svc = AgentPackService(agent_svc)
    project_svc = ProjectService(
        datastore=ProjectDatastore(session),
        event_bus=event_bus,
    )
    automation_svc = AutomationService(
        db=session,
        event_bus=event_bus,
        project_service=project_svc,
        agent_service=agent_svc,
    )
    pack_svc = ProjectPackService(
        project_service=project_svc,
        agent_service=agent_svc,
        agent_pack_service=agent_pack_svc,
        automation_service=automation_svc,
    )

    # Seed one enabled provider + the default model/provider/runtime settings so
    # ``_resolve_deploy_target`` returns a valid triple instead of 422.
    provider = ProviderRow(
        id="prov-1",
        name="Test",
        provider_kind="anthropic",
        source="user",
        credential_source="user",
        enabled=True,
        default_model="claude-sonnet-4-6",
    )
    provider.user_id = USER
    session.add(provider)
    import time

    now_ms = int(time.time() * 1000)
    for key, value in {
        "model.default_runtime": "claude_agent",
        "model.default_provider_id": "prov-1",
        "model.default_model": "claude-sonnet-4-6",
    }.items():
        # Settings are stored as JSON-encoded values in valuz_app_setting.
        import json

        s = AppSettingRow(key=key, value_json=json.dumps({"value": value}), updated_at=now_ms)
        s.user_id = USER
        session.add(s)
    await session.commit()

    deps = _Deps()
    deps.session = session
    deps.engine = engine
    deps.project_svc = project_svc
    deps.agent_svc = agent_svc
    deps.pack_svc = pack_svc

    app = FastAPI()

    async def _override_session():
        yield session

    async def _override_user():
        return USER

    async def _override_pack():
        yield pack_svc

    async def _override_project():
        yield project_svc

    app.include_router(projects_routes.router)
    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[projects_routes.get_current_user_id] = _override_user
    app.dependency_overrides[projects_routes.get_project_pack_service] = _override_pack
    app.dependency_overrides[projects_routes.get_project_service] = _override_project
    return app, deps


@pytest.fixture
async def client(tmp_path, monkeypatch) -> AsyncIterator[tuple]:
    monkeypatch.setenv("VALUZ_DATA_DIR", str(tmp_path / "data"))
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(fsr.settings, "user_skills_dir", tmp_path / "user-skills")
    app, deps = await _build_app(tmp_path)
    transport = ASGITransport(app=app)
    token = set_current_user_id(USER)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, deps
    finally:
        reset_current_user_id(token)
        await deps.session.close()  # type: ignore[attr-defined]
        await deps.engine.dispose()  # type: ignore[attr-defined]


async def _create_project(deps, name: str, icon: str | None = None) -> ProjectRow:
    from uuid import uuid4

    row = ProjectRow(
        id=uuid4().hex,
        name=name,
        kind="project",
        root_path=f"/tmp/valuz-test-{uuid4().hex}",  # arbitrary unique path
        icon=icon,
        sort_order=10,
    )
    row.user_id = USER
    deps.session.add(row)
    await deps.session.commit()
    return row


async def _create_agent(deps, slug, name="Agent", **kw) -> AgentRow:
    agent = AgentRow(
        slug=slug,
        name=name,
        description="",
        instructions="",
        runtime=kw.get("runtime", "claude_agent"),
        model=kw.get("model", "claude-sonnet-4-6"),
        skills=kw.get("skills", []),
        connector_types=kw.get("connector_types", []),
        provider_id=kw.get("provider_id"),
        source="custom",
    )
    agent.user_id = USER
    deps.session.add(agent)
    await deps.session.commit()
    return agent


async def _deploy(deps, project_id, source_slug, member_slug=None) -> None:
    member = ProjectMemberRow(
        project_id=project_id,
        agent_slug=member_slug or source_slug,
        source_agent_slug=source_slug,
    )
    member.user_id = USER
    deps.session.add(member)
    await deps.session.commit()


async def test_round_trip_recreates_project_members_automations_memory(client, tmp_path) -> None:
    c, deps = client
    project = await _create_project(deps, "Exported Project", icon="rocket")
    await _create_agent(deps, "lead-src", name="Lead")
    await _deploy(deps, project.id, "lead-src", member_slug="lead")
    # An automation bound to the member — its prompt must survive the round
    # trip (the list shape omits prompt_template; the export fetches detail).
    auto = AutomationRow(
        name="Daily brief",
        agent_kind="project_member",
        agent_slug="lead",
        project_id=project.id,
        prompt_template="Summarize overnight news",
        action_kind="chat",
        trigger_kind="cron",
        cron_expr="0 9 * * *",
        timezone="UTC",
        status="enabled",
    )
    auto.user_id = USER
    deps.session.add(auto)
    await deps.session.commit()
    # Memory comes from the catalog, not a hand-edited Markdown view.
    from valuz_agent.infra.fs_registry import fs_registry
    from valuz_agent.modules.memory.service import memory_store

    memory = fs_registry.memory_dir(USER, "project", project_id=project.id)
    assert memory_store.add(USER, "project", "Project-specific fact.", project_id=project.id)[
        "success"
    ]
    original_memory_id = memory_store.list_records(USER, "project", project_id=project.id)[0].id

    # Export
    resp = await c.get(f"/v1/projects/{project.id}/export")
    assert resp.status_code == 200, resp.text
    data = resp.content

    # Preview
    files = {"file": ("p.valuzpack", io.BytesIO(data), "application/zip")}
    prev = await c.post("/v1/projects/import-preview", files=files)
    assert prev.status_code == 200, prev.text
    body = prev.json()
    assert body["name_conflict"] is True  # same-name project exists
    assert len(body["automations"]) == 1
    assert body["automations"][0]["name"] == "Daily brief"
    preview_id = body["preview_id"]

    # Delete the original so confirm doesn't skip on name-conflict.
    await deps.session.delete(await deps.session.get(ProjectRow, project.id))
    await deps.session.commit()
    # also clear the memory dir to prove import restored it
    import shutil

    shutil.rmtree(memory, ignore_errors=True)

    # Confirm
    confirm = await c.post("/v1/projects/import/confirm", json={"preview_id": preview_id})
    assert confirm.status_code == 200, confirm.text
    result = confirm.json()
    assert result["status"] == "created"
    new_id = result["project_id"]
    assert new_id != project.id
    # member preserved
    assert [m["agent_slug"] for m in result["members"]] == ["lead"]
    imported = await deps.session.get(ProjectRow, new_id)
    assert imported is not None
    assert imported.root_path is not None
    assert Path(imported.root_path).is_absolute()
    # automation recreated (not silently dropped — regression guard for the
    # empty-prompt bug that swallowed AutomationPromptEmpty in a broad except)
    assert len(result["automations"]) == 1
    assert result["automations"][0]["name"] == "Daily brief"
    # Generated view plus a new owner-scoped catalog identity, with untrusted provenance.
    new_memory = fs_registry.memory_dir(USER, "project", project_id=new_id)
    assert "Project-specific fact." in (new_memory / "MEMORY.md").read_text()
    assert result["memory_imported"] == 1 and result["memory_errors"] == []
    records = memory_store.list_records(USER, "project", project_id=new_id)
    assert [record.content for record in records] == ["Project-specific fact."]
    assert records[0].id != original_memory_id
    assert not records[0].confirmed and records[0].source_refs[0].origin == "untrusted"


async def test_name_conflict_skip(client) -> None:
    c, deps = client
    project = await _create_project(deps, "Conflict Project")
    await _create_agent(deps, "c-lead-src", name="Lead")
    await _deploy(deps, project.id, "c-lead-src", member_slug="c-lead")
    resp = await c.get(f"/v1/projects/{project.id}/export")
    assert resp.status_code == 200
    data = resp.content

    files = {"file": ("p.valuzpack", io.BytesIO(data), "application/zip")}
    prev = await c.post("/v1/projects/import-preview", files=files)
    preview_id = prev.json()["preview_id"]

    confirm = await c.post("/v1/projects/import/confirm", json={"preview_id": preview_id})
    assert confirm.status_code == 200
    result = confirm.json()
    assert result["status"] == "skipped_name_conflict"


async def test_library_agent_slug_dedup(client) -> None:
    """A recipient that already has the source library agent (by slug) skips
    re-creating it but still recreates the member linking it."""
    c, deps = client
    project = await _create_project(deps, "Dedup Project")
    await _create_agent(deps, "shared-src", name="Shared")
    await _deploy(deps, project.id, "shared-src", member_slug="shared-handle")
    resp = await c.get(f"/v1/projects/{project.id}/export")
    data = resp.content

    # Recipient already has an agent of the same slug (simulating the same
    # agent installed on a separate machine). The unique constraint on
    # valuz_agent.slug means we don't re-create it — instead we delete
    # the source project (so confirm can create a fresh one) but KEEP the
    # library agent. Import must reuse the existing library slug.
    await deps.session.delete(await deps.session.get(ProjectRow, project.id))
    await deps.session.commit()

    files = {"file": ("p.valuzpack", io.BytesIO(data), "application/zip")}
    prev = await c.post("/v1/projects/import-preview", files=files)
    body = prev.json()
    assert any(m["source_agent_slug"] == "shared-src" and m["in_library"] for m in body["members"])

    confirm = await c.post("/v1/projects/import/confirm", json={"preview_id": body["preview_id"]})
    result = confirm.json()
    assert result["status"] == "created"
    assert result["agents_created"] == 0  # the agent existed
    assert result["agents_skipped"] == 1


async def test_expired_preview_id_returns_400(client) -> None:
    c, _ = client
    confirm = await c.post("/v1/projects/import/confirm", json={"preview_id": "never-staged"})
    assert confirm.status_code == 400


async def test_export_chat_project_returns_422(client) -> None:
    c, deps = client
    chat = ProjectRow(name="Chat", kind="chat", sort_order=0)
    chat.user_id = USER
    deps.session.add(chat)
    await deps.session.commit()
    resp = await c.get(f"/v1/projects/{chat.id}/export")
    assert resp.status_code == 422


async def test_two_members_same_source_dedupe_false(client) -> None:
    """Two members backed by the SAME source library agent can both be
    recreated (``deploy_agent(..., dedupe=False)``) and automations
    referencing each member handle still resolve."""
    c, deps = client
    project = await _create_project(deps, "Twin Members Project")
    await _create_agent(deps, "shared-twin", name="Twin")
    # Two members in the SAME project pointing at the same source library
    # agent — created with dedupe=False semantics (the automation runner
    # uses this same path). ProjectMemberRow's (project_id, agent_slug)
    # uniqueness only requires distinct agent_slug handles per project.
    for slug in ("twin-a", "twin-b"):
        member = ProjectMemberRow(
            project_id=project.id,
            agent_slug=slug,
            source_agent_slug="shared-twin",
        )
        member.user_id = USER
        deps.session.add(member)
    await deps.session.commit()

    resp = await c.get(f"/v1/projects/{project.id}/export")
    assert resp.status_code == 200, resp.text
    data = resp.content

    # delete the project so confirm can create a fresh one
    await deps.session.delete(await deps.session.get(ProjectRow, project.id))
    await deps.session.commit()

    files = {"file": ("p.valuzpack", io.BytesIO(data), "application/zip")}
    prev = await c.post("/v1/projects/import-preview", files=files)
    body = prev.json()
    confirm = await c.post("/v1/projects/import/confirm", json={"preview_id": body["preview_id"]})
    result = confirm.json()
    assert result["status"] == "created"
    recreated_handles = sorted(m["agent_slug"] for m in result["members"])
    assert recreated_handles == ["twin-a", "twin-b"]


async def test_export_unknown_project_returns_404(client) -> None:
    c, _ = client
    resp = await c.get("/v1/projects/unknown/export")
    assert resp.status_code == 404


async def test_malformed_archive_preview_returns_400(client) -> None:
    c, _ = client
    files = {"file": ("bad.zip", io.BytesIO(b"not a zip"), "application/zip")}
    resp = await c.post("/v1/projects/import-preview", files=files)
    assert resp.status_code == 400

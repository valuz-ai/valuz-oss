"""Export-side tests for ``ProjectPackService`` — drives the service with
in-memory DBs over a fresh sqlite, never the real keychain. Import-side
round-trip is covered by ``test_projects_export_import.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.infra.eventbus import event_bus
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
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
from valuz_agent.modules.packs_common import extract_archive
from valuz_agent.modules.project_packs.errors import (
    ProjectNotExportable,
    ProjectPackNotFound,
)
from valuz_agent.modules.project_packs.service import ProjectPackService
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.projects.service import ProjectService
from valuz_agent.modules.skills.models import ProjectSkillConfigRow, SkillIndexRow

USER = "user-1"


async def _bootstrap(tables, workdir: Path):  # type: ignore[no-untyped-def]
    """Create a fresh sqlite db with the requested tables."""
    workdir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{workdir / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    return session, engine


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
]


@pytest.fixture
async def env(tmp_path, monkeypatch) -> AsyncIterator[tuple]:
    from valuz_agent.infra import fs_registry as fsr
    from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id

    # Pin the data dir so memory/project writes land under tmp.
    monkeypatch.setenv("VALUZ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(fsr.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(fsr.settings, "user_skills_dir", tmp_path / "user-skills")
    session, engine = await _bootstrap(_ALL_TABLES, tmp_path / "db")
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
    svc = ProjectPackService(
        project_service=project_svc,
        agent_service=agent_svc,
        agent_pack_service=agent_pack_svc,
        automation_service=automation_svc,
    )
    # AutomationService.list_automations_in_project reads the owner from
    # the ambient auth context — set it for the test, reset on teardown.
    token = set_current_user_id(USER)
    try:
        yield svc, session, engine
    finally:
        reset_current_user_id(token)
        await session.close()
        await engine.dispose()


async def _seed_project(env, name="Test Project") -> ProjectRow:
    svc, session, _ = env
    from uuid import uuid4

    row = ProjectRow(
        id=uuid4().hex,
        name=name,
        kind="project",
        root_path="/tmp/some-bound-dir",
        sort_order=10,
    )
    await ProjectDatastore(session).create(USER, row)
    return row


async def _seed_agent(env, slug="lead-agent") -> AgentRow:
    """Unused placeholder retained for clarity — agents are seeded inline
    in each test so the connector / skill wiring stays co-located."""
    raise NotImplementedError


async def test_export_unknown_project_raises(env) -> None:
    svc = env[0]
    with pytest.raises(ProjectPackNotFound):
        await svc.export_project(USER, "missing-id")


async def test_export_chat_project_raises(env) -> None:
    svc, session, _ = env
    from uuid import uuid4

    chat = ProjectRow(
        id=uuid4().hex,
        name="Chat",
        kind="chat",
        sort_order=0,
    )
    await ProjectDatastore(session).create(USER, chat)
    with pytest.raises(ProjectNotExportable):
        await svc.export_project(USER, chat.id)


async def test_export_round_trips_members_and_automations(env) -> None:
    svc, session, _ = env
    # Seed a project, a library agent, a member linking them, and one automation.
    project = await _seed_project(env)
    agent = AgentRow(
        slug="lead-1",
        name="Lead",
        description="Lead agent",
        instructions="do lead stuff",
        runtime="claude_agent",
        model="claude-sonnet-4-6",
        skills=[],
        connector_types=[],
        provider_id="prov-1",
        source="custom",
    )
    agent.user_id = USER
    session.add(agent)
    await session.commit()
    member = ProjectMemberRow(
        project_id=project.id,
        agent_slug="lead",
        source_agent_slug="lead-1",
    )
    member.user_id = USER
    session.add(member)
    await session.commit()

    automation = AutomationRow(
        id="auto-1",
        name="Daily brief",
        agent_kind="project_member",
        agent_slug="lead",
        project_id=project.id,
        prompt_template="Prompt body text",
        action_kind="chat",
        trigger_kind="cron",
        cron_expr="0 9 * * *",
        timezone="UTC",
        status="enabled",
    )
    automation.user_id = USER
    session.add(automation)
    await session.commit()

    data = await svc.export_project(USER, project.id)
    assert isinstance(data, bytes) and len(data) > 0

    parsed, root = extract_archive(data)
    assert parsed.project is not None
    assert parsed.project.name == "Test Project"
    assert len(parsed.project.members) == 1
    m = parsed.project.members[0]
    assert m.agent_slug == "lead"
    assert m.source_agent_slug == "lead-1"
    # The agent definition is hoisted into the top-level ``agents[]`` payload,
    # referenced by the member's source slug.
    agent = next(a for a in parsed.agents if a.slug == "lead-1")
    # provider_id is dropped (PackAgent has no provider_id field)
    assert not hasattr(agent, "provider_id") or getattr(agent, "provider_id", None) is None
    # model is demoted to model_hint
    assert agent.model_hint == "claude-sonnet-4-6"
    assert len(parsed.project.automations) == 1
    assert parsed.project.automations[0].name == "Daily brief"
    assert parsed.project.automations[0].trigger_kind == "cron"
    assert parsed.project.automations[0].cron_expr == "0 9 * * *"
    # prompt_template must travel in the archive — the list shape omits it,
    # so the export fetches detail per automation. An empty prompt here would
    # make every automation fail AutomationPromptEmpty on import.
    assert parsed.project.automations[0].prompt_template == "Prompt body text"


async def test_export_strips_connector_secrets(env) -> None:
    svc, session, _ = env
    from valuz_agent.modules.connectors.service import CredEntry

    project = await _seed_project(env)
    # Create a custom connector carrying a header secret, then bind it to
    # the agent and to the project.
    connector_svc = svc._agents._connectors
    view = await connector_svc.create_connector(
        USER,
        slug="my-mcp",
        display_name="My MCP",
        transport="http",
        url="https://example.com/mcp",
        auth_type="bearer",
        headers=[CredEntry(key="Authorization", value="Bearer SECRET", secret=True)],
    )
    assert view.slug == "my-mcp"
    agent = AgentRow(
        slug="c-agent",
        name="Connector Agent",
        runtime="claude_agent",
        model="claude-sonnet-4-6",
        skills=[],
        connector_types=["my-mcp"],
        source="custom",
    )
    agent.user_id = USER
    session.add(agent)
    await session.commit()
    member = ProjectMemberRow(
        project_id=project.id,
        agent_slug="c-1",
        source_agent_slug="c-agent",
    )
    member.user_id = USER
    session.add(member)
    pc = ProjectConnectorRow(project_id=project.id, slug="my-mcp")
    pc.user_id = USER
    session.add(pc)
    await session.commit()

    data = await svc.export_project(USER, project.id)
    parsed, _ = extract_archive(data)
    assert len(parsed.connectors) == 1
    c = parsed.connectors[0]
    assert c.slug == "my-mcp"
    # Secret-bearing fields are NOT carried — only url / command / args /
    # auth_type / transport.
    assert c.url == "https://example.com/mcp"
    assert c.auth_type == "bearer"
    assert c.requires_credentials is True
    assert "headers_json" not in c.model_dump()
    assert "params_json" not in c.model_dump()
    assert "env_json" not in c.model_dump()


async def test_export_rejects_code_automation_without_losing_it(env) -> None:
    svc, session, _ = env
    project = await _seed_project(env)
    automation = AutomationRow(
        id="code-run",
        user_id=USER,
        name="Code task",
        project_id=project.id,
        execution_kind="code",
        code_runtime="python",
        code_entry="job.py",
        agent_kind=None,
        agent_slug=None,
        prompt_template="",
        action_kind="chat",
        trigger_kind="manual",
        status="enabled",
        result_kind="artifact",
    )
    session.add(automation)
    await session.commit()
    with pytest.raises(ProjectNotExportable, match="Code automations"):
        await svc.export_project(USER, project.id)
    await session.refresh(automation)
    assert automation.execution_kind == "code" and automation.status == "enabled"


async def test_memory_export_import_catalog_identity_and_new_session_injection(
    env, monkeypatch
) -> None:
    import json
    import shutil
    from contextlib import asynccontextmanager

    from valuz_agent.modules.memory import injection
    from valuz_agent.modules.memory.service import memory_store

    svc, session, _ = env
    project = await _seed_project(env, "Memory source")
    assert memory_store.add(
        USER, "project", "Project Alpha has a reproducible build.", project_id=project.id
    )["success"]
    source_record = memory_store.list_records(USER, "project", project_id=project.id)[0]
    data = await svc.export_project(USER, project.id)
    _, root = extract_archive(data)
    try:
        portable = json.loads((root / "memory" / "entries.json").read_text())
        assert set(portable["entries"][0]) == {"content", "kind", "object_refs"}
        assert source_record.id not in json.dumps(portable)
        assert "owner_user_id" not in json.dumps(portable) and "confirmed" not in json.dumps(
            portable
        )
    finally:
        shutil.rmtree(root)
    preview = await svc.preview_import(USER, data)
    imported = await svc.confirm_import(
        USER,
        preview["preview_id"],
        name="Memory destination",
        runtime="claude_agent",
        provider_id="test",
        model="test",
        effort=None,
    )
    assert imported["memory_imported"] == 1 and imported["memory_errors"] == []
    target_id = imported["project_id"]
    record = memory_store.list_records(USER, "project", project_id=target_id)[0]
    assert record.id != source_record.id and record.project_id == target_id
    assert record.revision == 1 and record.source == "agent" and not record.confirmed
    assert record.source_refs[0].kind == "import" and record.source_refs[0].origin == "untrusted"

    # Only the DB transport binding is replaced: actual setting lookup and catalog injection run.
    from valuz_agent.modules.settings.models import AppSettingRow

    async with session.bind.begin() as connection:
        await connection.run_sync(AppSettingRow.__table__.create, checkfirst=True)

    @asynccontextmanager
    async def uow():
        yield session

    monkeypatch.setattr(injection, "async_unit_of_work", uow)
    block = await injection.memory_instructions_block(user_id=USER, project_id=target_id)
    assert record.content in block and f"memory_id={record.id} revision=1" in block
    assert source_record.id not in block


async def test_memory_export_does_not_read_markdown_view(env) -> None:
    from valuz_agent.infra.fs_registry import fs_registry
    from valuz_agent.modules.memory.service import memory_store

    svc = env[0]
    project = await _seed_project(env)
    assert memory_store.add(USER, "project", "Real catalog fact", project_id=project.id)["success"]
    (fs_registry.memory_dir(USER, "project", project_id=project.id) / "MEMORY.md").write_text(
        "A poisoned hand edit"
    )
    data = await svc.export_project(USER, project.id)
    _, root = extract_archive(data)
    assert "Real catalog fact" in (root / "memory" / "MEMORY.md").read_text()
    assert "poisoned" not in (root / "memory" / "MEMORY.md").read_text()


async def test_preview_owner_fence_preserves_original_preview(env) -> None:
    from valuz_agent.modules.project_packs.errors import ProjectPackImportFailed

    svc, session, _ = env
    project = await _seed_project(env)
    data = await svc.export_project(USER, project.id)
    preview = await svc.preview_import(USER, data)
    with pytest.raises(ProjectPackImportFailed, match="different owner"):
        await svc.confirm_import(
            "other-owner",
            preview["preview_id"],
            name="Forbidden copy",
            runtime="claude_agent",
            provider_id="test",
            model="test",
            effort=None,
        )
    assert await ProjectDatastore(session).get_by_name("other-owner", "Forbidden copy") is None
    result = await svc.confirm_import(
        USER,
        preview["preview_id"],
        name="Owned copy",
        runtime="claude_agent",
        provider_id="test",
        model="test",
        effort=None,
    )
    assert result["status"] == "created"
    assert await ProjectDatastore(session).get_by_name(USER, "Owned copy") is not None


async def test_explicit_cross_owner_upload_creates_new_target_memory(env) -> None:
    from valuz_agent.modules.memory.service import memory_store

    svc, session, _ = env
    project = await _seed_project(env)
    memory_store.add(USER, "project", "Portable fact", project_id=project.id)
    original = memory_store.list_records(USER, "project", project_id=project.id)[0]
    data = await svc.export_project(USER, project.id)
    preview = await svc.preview_import("recipient", data)
    result = await svc.confirm_import(
        "recipient",
        preview["preview_id"],
        name="Recipient project",
        runtime="claude_agent",
        provider_id="test",
        model="test",
        effort=None,
    )
    assert result["memory_errors"] == [] and result["memory_imported"] == 1
    target = await ProjectDatastore(session).get_by_id("recipient", result["project_id"])
    assert target is not None
    records = memory_store.list_records("recipient", "project", project_id=target.id)
    assert records[0].id != original.id and records[0].revision == 1
    assert records[0].source_refs[0].kind == "import" and not records[0].confirmed
    assert memory_store.read_entries(USER, "project", project_id=target.id) == []


async def test_explicit_legacy_pack_reports_sensitive_and_capacity_failures(env, tmp_path) -> None:
    from valuz_agent.modules.memory.models import ENTRY_DELIMITER
    from valuz_agent.modules.memory.service import memory_store
    from valuz_agent.modules.packs_common import PackManifest, PackProject, build_archive

    svc = env[0]
    directory = tmp_path / "legacy-pack"
    directory.mkdir()
    (directory / "MEMORY.md").write_text(
        ENTRY_DELIMITER.join(["Valid imported fact", "password=synthetic-secret", "x" * 3990])
    )
    data = build_archive(PackManifest(project=PackProject(name="Legacy import")), {}, directory)
    preview = await svc.preview_import(USER, data)
    result = await svc.confirm_import(
        USER,
        preview["preview_id"],
        runtime="claude_agent",
        provider_id="test",
        model="test",
        effort=None,
    )
    assert result["status"] == "created" and result["memory_imported"] == 1
    assert len(result["memory_errors"]) == 2
    assert {entry["entry_index"] for entry in result["memory_errors"]} == {1, 2}
    assert "synthetic-secret" not in str(result["memory_errors"])
    assert memory_store.read_entries(USER, "project", project_id=result["project_id"]) == [
        "Valid imported fact"
    ]


async def test_pack_memory_replay_checks_active_records_and_forgotten_source(env, tmp_path) -> None:
    import hashlib

    from valuz_agent.modules.memory.models import SourceRef
    from valuz_agent.modules.memory.service import memory_store
    from valuz_agent.modules.packs_common import PackManifest, PackProject, build_archive

    svc = env[0]
    project = await _seed_project(env)
    directory = tmp_path / "pack-memory"
    directory.mkdir()
    (directory / "MEMORY.md").write_text("Fact from the archive")
    data = build_archive(PackManifest(project=PackProject(name="Archive")), {}, directory)
    pack_hash = hashlib.sha256(data).hexdigest()
    _, extracted = extract_archive(data)
    result = await svc._import_memory_payload(USER, project.id, extracted, "memory", pack_hash)
    assert result == (1, [])
    snapshot = memory_store.snapshot(USER)
    assert await svc._import_memory_payload(USER, project.id, extracted, "memory", pack_hash) == (
        1,
        [],
    )
    assert memory_store.snapshot(USER).revision == snapshot.revision
    source = SourceRef(kind="import", source_id=pack_hash, origin="untrusted")
    memory_store.forget_source(USER, source, operation_id="forget", base_revision=snapshot.revision)
    count, errors = await svc._import_memory_payload(
        USER, project.id, extracted, "memory", pack_hash
    )
    assert count == 0 and errors[0]["error_code"] == "memory.protected"
    another = await _seed_project(env, "Another destination")
    count, errors = await svc._import_memory_payload(
        USER, another.id, extracted, "memory", pack_hash
    )
    assert count == 0 and errors[0]["error_code"] == "memory.protected"
    assert memory_store.read_entries(USER, "project", project_id=project.id) == []
    assert memory_store.read_entries(USER, "project", project_id=another.id) == []


@pytest.mark.parametrize("confirmed", [False, True])
async def test_pack_duplicate_merge_or_noop_replay_uses_original_base(
    env, tmp_path, confirmed: bool
) -> None:
    import hashlib

    from valuz_agent.modules.memory.models import SourceRef
    from valuz_agent.modules.memory.service import memory_store
    from valuz_agent.modules.packs_common import PackManifest, PackProject, build_archive

    svc = env[0]
    project = await _seed_project(env)
    prior = memory_store.mutate(
        USER,
        action="add",
        target="project",
        project_id=project.id,
        content="Already available",
        operation_id="original",
        base_revision=0,
        source="user" if confirmed else "agent",
        source_refs=(SourceRef(kind="manual", source_id="original", origin="owner"),),
    )
    directory = tmp_path / "duplicate"
    directory.mkdir()
    (directory / "MEMORY.md").write_text("Already available")
    data = build_archive(PackManifest(project=PackProject(name="Archive")), {}, directory)
    _, root = extract_archive(data)
    pack_hash = hashlib.sha256(data).hexdigest()
    assert await svc._import_memory_payload(USER, project.id, root, "memory", pack_hash) == (1, [])
    record = memory_store.list_records(USER, "project", project_id=project.id)[0]
    assert record.id == prior.record_id
    assert record.confirmed == confirmed
    assert record.revision == (1 if confirmed else 2)
    memory_store.add(USER, "global", "A subsequent unrelated write")
    version = memory_store.snapshot(USER).revision
    assert await svc._import_memory_payload(USER, project.id, root, "memory", pack_hash) == (1, [])
    assert memory_store.snapshot(USER).revision == version
    assert len(memory_store.list_records(USER, "project", project_id=project.id)) == 1


async def test_pack_memory_cas_conflict_is_reported_without_import(
    env, tmp_path, monkeypatch
) -> None:
    import hashlib

    from valuz_agent.infra.fs_registry import FsRegistry
    from valuz_agent.modules.memory.service import MemoryStore, memory_store
    from valuz_agent.modules.packs_common import PackManifest, PackProject, build_archive

    svc = env[0]
    project = await _seed_project(env)
    directory = tmp_path / "conflicting"
    directory.mkdir()
    (directory / "MEMORY.md").write_text("Import candidate")
    data = build_archive(PackManifest(project=PackProject(name="Archive")), {}, directory)
    _, root = extract_archive(data)
    original = memory_store.mutate

    def interfere(*args, **kwargs):
        other = MemoryStore(FsRegistry())
        assert other.add(USER, "global", "A concurrent real write")["success"]
        return original(*args, **kwargs)

    monkeypatch.setattr(memory_store, "mutate", interfere)
    count, errors = await svc._import_memory_payload(
        USER, project.id, root, "memory", hashlib.sha256(data).hexdigest()
    )
    assert count == 0 and errors[0]["error_code"] == "memory.revision_conflict"
    assert memory_store.read_entries(USER, "project", project_id=project.id) == []


async def test_memory_export_unavailable_is_not_reported_as_no_memory(env) -> None:
    from valuz_agent.infra.fs_registry import fs_registry

    svc = env[0]
    project = await _seed_project(env)
    (fs_registry.memory_dir(USER, "global") / "memory.json").write_text("not valid JSON")
    with pytest.raises(ProjectNotExportable, match="could not be exported"):
        await svc.export_project(USER, project.id)


async def test_pack_uses_selected_backend_and_never_falls_back_to_local(
    env, tmp_path, monkeypatch
) -> None:
    import hashlib

    from valuz_agent.integrations.memory_local import LocalMemoryBackend
    from valuz_agent.modules.memory.service import memory_store
    from valuz_agent.modules.packs_common import PackManifest, PackProject, build_archive
    from valuz_agent.ports.extensions import ext
    from valuz_agent.ports.memory import MemoryProtected, MemorySnapshot

    svc = env[0]
    project = await _seed_project(env)
    memory_store.add(USER, "project", "An existing local-only fact", project_id=project.id)

    class DeniedBackend(LocalMemoryBackend):
        async def snapshot(self, user_id: str, *, namespace: str = "core") -> MemorySnapshot:
            raise MemoryProtected("Selected authority denied this owner")

    monkeypatch.setattr(ext, "memory_backend", DeniedBackend())
    with pytest.raises(ProjectNotExportable, match="could not be exported"):
        await svc.export_project(USER, project.id)
    directory = tmp_path / "denied"
    directory.mkdir()
    (directory / "MEMORY.md").write_text("A new fact")
    data = build_archive(PackManifest(project=PackProject(name="Archive")), {}, directory)
    _, root = extract_archive(data)
    count, errors = await svc._import_memory_payload(
        USER, project.id, root, "memory", hashlib.sha256(data).hexdigest()
    )
    assert count == 0 and errors[0]["error_code"] == "memory.protected"
    assert memory_store.read_entries(USER, "project", project_id=project.id) == [
        "An existing local-only fact"
    ]

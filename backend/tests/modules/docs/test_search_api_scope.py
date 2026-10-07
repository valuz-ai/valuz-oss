"""HTTP search selection over real owner-scoped rows and embedded text search."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from valuz_agent.api.deps import get_current_user_id, get_document_service
from valuz_agent.api.routes.docs import router
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.integrations.docs_embedded import EmbeddedDocsRuntime
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.docs.models import (
    DocumentRecordRow,
    KbFolderRow,
    KnowledgeBaseRow,
    ProjectKbBindingRow,
)
from valuz_agent.modules.docs.service import DocumentLibraryService
from valuz_agent.ports.extensions import ext

USER = "search-caller"
MARKER = "QA_SDK_KNOWLEDGE_SCOPE"


class NoParse:
    def parse_sync(self, file_path, options=None):
        raise AssertionError("already-indexed documents must not be reparsed")


@dataclass
class ScopeFixture:
    service: DocumentLibraryService
    kbs: dict[str, KnowledgeBaseRow] = field(default_factory=dict)
    docs: dict[str, DocumentRecordRow] = field(default_factory=dict)
    grants: list[tuple[str, str]] = field(default_factory=list)
    folder_id: str = ""


@pytest_asyncio.fixture
async def scope(db, tmp_path, monkeypatch):
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data" / "{user_id}"))
    fixture = ScopeFixture(
        DocumentLibraryService(
            datastore=DocumentDatastore(db),
            parser=NoParse(),
            docs_runtime=EmbeddedDocsRuntime(),
            event_bus=EventBus(),
        )
    )
    for name, owner in (
        ("own-a", USER),
        ("own-b", USER),
        ("foreign", "other-org-user"),
        ("shared-a", "shared-owner"),
        ("shared-b", "shared-owner"),
    ):
        kb = KnowledgeBaseRow(user_id=owner, name=name, root_path=str(tmp_path / name))
        db.add(kb)
        await db.flush()
        doc = DocumentRecordRow(
            user_id=owner,
            kb_id=kb.id,
            kb_folder_id="",
            relative_path="root.md",
            source_path=str(tmp_path / name / "root.md"),
            source_filename=f"{name}.md",
            mime_type="text/markdown",
            file_size_bytes=40,
            status="ready",
        )
        db.add(doc)
        await db.flush()
        preview_dir = fs_registry.docs_preview_dir(owner)
        preview_dir.mkdir(parents=True, exist_ok=True)
        (preview_dir / f"{doc.id}.md").write_text(f"# {name}\n{MARKER}\n", encoding="utf-8")
        doc.preview_text_path = f"docs/preview/{doc.id}.md"
        fixture.kbs[name], fixture.docs[name] = kb, doc
    folder = KbFolderRow(
        user_id=USER, kb_id=fixture.kbs["own-a"].id, relative_path="sub", display_name="sub"
    )
    db.add(folder)
    await db.flush()
    fixture.folder_id = folder.id
    subdoc = DocumentRecordRow(
        user_id=USER,
        kb_id=folder.kb_id,
        kb_folder_id=folder.id,
        relative_path="sub/child.md",
        source_path=str(tmp_path / "own-a/sub/child.md"),
        source_filename="child.md",
        mime_type="text/markdown",
        file_size_bytes=40,
        status="ready",
    )
    db.add(subdoc)
    await db.flush()
    (fs_registry.docs_preview_dir(USER) / f"{subdoc.id}.md").write_text(
        f"# child\n{MARKER}\n", encoding="utf-8"
    )
    subdoc.preview_text_path = f"docs/preview/{subdoc.id}.md"
    fixture.docs["child"] = subdoc
    fixture.grants = [
        (fixture.docs[n].user_id, fixture.docs[n].id) for n in ("shared-a", "shared-b")
    ]
    db.add(
        ProjectKbBindingRow(
            user_id=USER,
            project_id="project-b",
            binding_kind="kb",
            target_id=fixture.kbs["own-b"].id,
            created_at=1,
        )
    )
    await db.commit()

    async def shared_documents(user_id):
        assert user_id == USER, "HTTP actor must come from Depends, never request data"
        return list(fixture.grants)

    monkeypatch.setattr(ext, "docs_scope_contributor", shared_documents)
    return fixture


@pytest_asyncio.fixture
async def client(scope):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER
    app.dependency_overrides[get_document_service] = lambda: scope.service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def search(client, **selection):
    response = await client.post(
        "/v1/docs/search", json={"query": MARKER, "project_id": "", **selection}
    )
    assert response.status_code == 200, response.text
    return {hit["document_id"] for hit in response.json()["hits"]}


@pytest.mark.asyncio
async def test_explicit_kb_finds_unbound_root_documents_without_shared_widening(client, scope):
    assert scope.docs["own-a"].kb_folder_id == ""
    assert await search(client, knowledge_base_ids=[scope.kbs["own-a"].id]) == {
        scope.docs["own-a"].id,
        scope.docs["child"].id,
    }


@pytest.mark.asyncio
async def test_explicit_kb_replaces_project_bindings(client, scope):
    assert await search(
        client, project_id="project-b", knowledge_base_ids=[scope.kbs["own-a"].id]
    ) == {scope.docs["own-a"].id, scope.docs["child"].id}


@pytest.mark.asyncio
async def test_omitted_selection_retains_project_and_shared_scope(client, scope):
    assert await search(client, project_id="project-b") == {
        scope.docs[n].id for n in ("own-b", "shared-a", "shared-b")
    }
    assert await search(client) == {scope.docs[n].id for n in ("shared-a", "shared-b")}


@pytest.mark.asyncio
async def test_empty_selection_means_none_even_with_project_and_shared_libraries(client):
    assert await search(client, project_id="project-b", knowledge_base_ids=[]) == set()


@pytest.mark.asyncio
async def test_selected_shared_kb_is_authorized_and_does_not_include_other_shared_kbs(
    client, scope
):
    assert await search(client, knowledge_base_ids=[scope.kbs["shared-a"].id]) == {
        scope.docs["shared-a"].id
    }
    scope.grants = []  # Revocation: a stale selection is not an authorization grant.
    assert await search(client, knowledge_base_ids=[scope.kbs["shared-a"].id]) == set()


@pytest.mark.asyncio
async def test_deleted_shared_kb_rejects_stale_document_grant(client, scope, db):
    kb_id = scope.kbs["shared-a"].id
    await db.delete(scope.kbs["shared-a"])
    await db.commit()
    # Simulate an out-of-date host grant while the indexed document still exists.
    assert scope.docs["shared-a"].status == "ready"
    assert await search(client, knowledge_base_ids=[kb_id]) == set()


@pytest.mark.asyncio
async def test_mixed_authorized_and_foreign_selection_only_returns_authorized_docs(client, scope):
    assert await search(
        client,
        knowledge_base_ids=[
            scope.kbs["own-b"].id,
            scope.kbs["shared-a"].id,
            scope.kbs["foreign"].id,
        ],
    ) == {scope.docs["own-b"].id, scope.docs["shared-a"].id}


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["foreign", "missing", "folder"])
async def test_foreign_unknown_and_folder_ids_cannot_be_used_as_kb_authority(
    client, scope, selector
):
    kb_id = {
        "foreign": scope.kbs["foreign"].id,
        "missing": "no-such-kb",
        "folder": scope.folder_id,
    }[selector]
    assert await search(client, knowledge_base_ids=[kb_id]) == set()


@pytest.mark.asyncio
async def test_http_body_cannot_supply_actor_or_pre_authorized_cross_owner_scope(client, scope):
    foreign = scope.docs["foreign"]
    assert (
        await search(
            client,
            knowledge_base_ids=[scope.kbs["foreign"].id],
            user_id=foreign.user_id,
            owner_user_id=foreign.user_id,
            authorized_documents=[[foreign.user_id, foreign.id]],
            authorized_document_ids=[foreign.id],
        )
        == set()
    )


@pytest.mark.asyncio
async def test_folder_and_document_filters_only_narrow_selected_kb(client, scope):
    selection = {"knowledge_base_ids": [scope.kbs["own-a"].id]}
    assert await search(client, **selection, folder_ids=[scope.folder_id]) == {
        scope.docs["child"].id
    }
    assert await search(client, **selection, document_ids=[scope.docs["own-a"].id]) == {
        scope.docs["own-a"].id
    }
    assert await search(client, **selection, document_ids=[scope.docs["foreign"].id]) == set()
    assert await search(client, **selection, folder_ids=[scope.kbs["own-a"].id]) == set()


@pytest.mark.asyncio
async def test_deleted_kb_and_not_ready_docs_immediately_fall_out(client, scope, db):
    for name in ("own-a", "child", "shared-a"):
        scope.docs[name].status = "processing"
    await db.commit()
    assert await search(client, knowledge_base_ids=[scope.kbs["own-a"].id]) == set()
    assert await search(client, knowledge_base_ids=[scope.kbs["shared-a"].id]) == set()
    await db.delete(scope.kbs["own-b"])
    await db.commit()
    assert await search(client, knowledge_base_ids=[scope.kbs["own-b"].id]) == set()

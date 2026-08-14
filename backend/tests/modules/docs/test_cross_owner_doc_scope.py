"""Pre-authorized cross-owner document scope (valuz-oss#839 / #840).

Every docs authorization path is caller-scoped: ``search_docs`` re-authorizes
ids with ``get_by_id(user_id, doc_id)`` where ``user_id`` is the *caller*.
Since ``DocumentRecordRow.user_id`` is the uploader, a host implementing
shared collections could not grant a member search access — injected ids were
silently dropped.

``authorized_documents`` carries ``(owner_user_id, doc_id)`` pairs so the
re-authorization runs under the document's owner. These tests pin both halves:
the grant works, and nothing is widened by accident.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from valuz_agent.infra.database import Base
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.docs.models import DocumentRecordRow, KnowledgeBaseRow
from valuz_agent.modules.docs.service import DocumentLibraryService
from valuz_agent.ports.docs_runtime import DocsHealthSnapshot, SearchResult

OWNER = "user-owner"
MEMBER = "user-member"


class RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def search(self, query, doc_scope_ids, top_k=5, doc_paths=None):  # type: ignore[no-untyped-def]
        self.calls.append({"scope": list(doc_scope_ids), "doc_paths": dict(doc_paths or {})})
        return [SearchResult(document_id=d, score=1.0, snippet="hit") for d in doc_scope_ids]

    def search_sync(self, query, doc_scope_ids, top_k=5, doc_paths=None):  # type: ignore[no-untyped-def]
        self.calls.append({"scope": list(doc_scope_ids), "doc_paths": dict(doc_paths or {})})
        return [SearchResult(document_id=d, score=1.0, snippet="hit") for d in doc_scope_ids]

    async def health(self):  # type: ignore[no-untyped-def]
        return DocsHealthSnapshot(provider_id=self.provider_id, status="healthy")

    @property
    def provider_id(self) -> str:
        return "test.recording"


class _NoParse:
    def parse_sync(self, file_path, options=None):  # type: ignore[no-untyped-def]
        raise AssertionError("parse must not run in these tests")


@pytest_asyncio.fixture()
async def db(tmp_path, monkeypatch):
    from valuz_agent.infra import config as config_mod

    monkeypatch.setattr(config_mod.settings, "data_dir", str(tmp_path / "data" / "{user_id}"))
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    await session.close()
    await engine.dispose()


async def _seed_doc(db, tmp_path, owner: str, status: str = "ready") -> str:
    """One ready doc owned by ``owner``, with a real preview file."""
    from valuz_agent.infra.fs_registry import fs_registry

    kb = KnowledgeBaseRow(user_id=owner, name=f"kb-{owner}", root_path=str(tmp_path / owner))
    db.add(kb)
    await db.flush()

    doc = DocumentRecordRow(
        user_id=owner,
        kb_id=kb.id,
        kb_folder_id="",
        relative_path=f"{owner}.md",
        source_path=str(tmp_path / owner / "a.md"),
        source_filename=f"{owner}.md",
        title="a",
        mime_type="text/markdown",
        file_size_bytes=1,
        content_hash="h",
        status=status,
    )
    db.add(doc)
    await db.flush()

    preview_dir = fs_registry.docs_preview_dir(owner)
    preview_dir.mkdir(parents=True, exist_ok=True)
    (preview_dir / f"{doc.id}.md").write_text("preview body", encoding="utf-8")
    doc.preview_text_path = f"docs/preview/{doc.id}.md"
    await db.commit()
    return doc.id


def _service(db, runtime):
    return DocumentLibraryService(
        datastore=DocumentDatastore(db),
        parser=_NoParse(),
        docs_runtime=runtime,
        event_bus=EventBus(),
    )


@pytest.mark.asyncio
async def test_caller_scoped_path_drops_another_owners_document(db, tmp_path):
    """The pre-existing behavior this seam exists to work around."""
    doc_id = await _seed_doc(db, tmp_path, OWNER)
    runtime = RecordingRuntime()

    hits = await _service(db, runtime).search_docs(
        MEMBER, "proj", "q", authorized_document_ids=[doc_id]
    )

    assert hits == []
    assert not runtime.calls


@pytest.mark.asyncio
async def test_pre_authorized_pair_grants_access_under_the_owner(db, tmp_path):
    doc_id = await _seed_doc(db, tmp_path, OWNER)
    runtime = RecordingRuntime()

    hits = await _service(db, runtime).search_docs(
        MEMBER, "proj", "q", authorized_documents=[(OWNER, doc_id)]
    )

    assert [h.document_id for h in hits] == [doc_id]
    assert runtime.calls[0]["scope"] == [doc_id]


@pytest.mark.asyncio
async def test_preview_content_resolves_under_the_owners_data_dir(db, tmp_path):
    """End-to-end through the real embedded runtime.

    The preview file lives under the OWNER's data dir; resolving it as the
    caller would fail the containment check and return no hit at all.
    """
    from valuz_agent.integrations.docs_embedded import EmbeddedDocsRuntime

    doc_id = await _seed_doc(db, tmp_path, OWNER)
    hits = await _service(db, EmbeddedDocsRuntime()).search_docs(
        MEMBER, "proj", "preview", authorized_documents=[(OWNER, doc_id)]
    )

    assert [h.document_id for h in hits] == [doc_id]
    assert "preview body" in hits[0].snippet


@pytest.mark.asyncio
async def test_wrong_owner_in_pair_is_rejected(db, tmp_path):
    """A pair is only as good as its owner — mismatched pairs resolve to nothing."""
    doc_id = await _seed_doc(db, tmp_path, OWNER)
    runtime = RecordingRuntime()

    hits = await _service(db, runtime).search_docs(
        MEMBER, "proj", "q", authorized_documents=[(MEMBER, doc_id)]
    )

    assert hits == []


@pytest.mark.asyncio
async def test_non_ready_document_is_rejected(db, tmp_path):
    doc_id = await _seed_doc(db, tmp_path, OWNER, status="queued")
    runtime = RecordingRuntime()

    hits = await _service(db, runtime).search_docs(
        MEMBER, "proj", "q", authorized_documents=[(OWNER, doc_id)]
    )

    assert hits == []


@pytest.mark.asyncio
async def test_document_ids_narrowing_still_applies(db, tmp_path):
    doc_id = await _seed_doc(db, tmp_path, OWNER)
    runtime = RecordingRuntime()

    hits = await _service(db, runtime).search_docs(
        MEMBER,
        "proj",
        "q",
        document_ids=["some-other-id"],
        authorized_documents=[(OWNER, doc_id)],
    )

    assert hits == []


def test_seam_is_not_reachable_from_model_facing_tools():
    """docs MCP tools must never populate the cross-owner seam themselves."""
    import inspect

    from valuz_agent.integrations import docs_mcp_server

    src = inspect.getsource(docs_mcp_server)
    assert "authorized_documents=" not in src, (
        "the cross-owner seam must be populated by host code that performed a "
        "membership check — never from a model/tool-facing surface"
    )

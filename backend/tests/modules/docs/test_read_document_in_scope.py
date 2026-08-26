"""Reading one bound document, and refusing every other one.

``doc_search`` answers *which* document; until now nothing answered *what it
says*. An agent that found the right document had no way to open it — and
reached for whatever other document-shaped tool it could see, which addresses a
different corpus with different ids and so returns "not found" for a document
that plainly exists. Observed twice on qa, on two different libraries.

The read is the sharper half of the pair for authorization: a search that
over-reaches leaks a snippet, a read that over-reaches leaks the document. So
the scope is not re-derived here — ``authorized_doc_scope`` is the one answer
both callers get, and these tests pin that they agree.
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
STRANGER = "user-stranger"
BODY = "# 5、 特别约定\n\n- 意外伤害住院津贴每日50元\n"


class _Runtime:
    async def search(self, query, doc_scope_ids, top_k=5, doc_paths=None):  # type: ignore[no-untyped-def]
        return [SearchResult(document_id=d, score=1.0, snippet="hit") for d in doc_scope_ids]

    async def health(self):  # type: ignore[no-untyped-def]
        return DocsHealthSnapshot(provider_id=self.provider_id, status="healthy")

    @property
    def provider_id(self) -> str:
        return "test.read"


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


async def _seed(
    db, tmp_path, owner: str, *, slug: str = "a", body: str = BODY, status: str = "ready"
) -> str:
    """One ready doc with a real preview file. ``slug`` keeps two seeds for the
    same owner off each other's ``(user_id, root_path)`` unique key."""
    from valuz_agent.infra.fs_registry import fs_registry

    kb = KnowledgeBaseRow(
        user_id=owner, name=f"kb-{owner}-{slug}", root_path=str(tmp_path / owner / slug)
    )
    db.add(kb)
    await db.flush()
    doc = DocumentRecordRow(
        user_id=owner,
        kb_id=kb.id,
        kb_folder_id="",
        relative_path="policy/平安金钟罩意外险.pdf",
        source_path=str(tmp_path / owner / "平安金钟罩意外险.pdf"),
        source_filename="平安金钟罩意外险.pdf",
        title="平安金钟罩意外险",
        mime_type="application/pdf",
        file_size_bytes=4096,
        content_hash="h",
        status=status,
    )
    db.add(doc)
    await db.flush()
    preview_dir = fs_registry.docs_preview_dir(owner)
    preview_dir.mkdir(parents=True, exist_ok=True)
    (preview_dir / f"{doc.id}.md").write_text(body, encoding="utf-8")
    doc.preview_text_path = f"docs/preview/{doc.id}.md"
    await db.commit()
    return doc.id


def _service(db) -> DocumentLibraryService:
    return DocumentLibraryService(
        datastore=DocumentDatastore(db),
        parser=_NoParse(),
        docs_runtime=_Runtime(),
        event_bus=EventBus(),
    )


@pytest.mark.asyncio
async def test_an_authorized_document_comes_back_with_its_text_and_where_it_lives(db, tmp_path):
    doc_id = await _seed(db, tmp_path, OWNER)

    doc = await _service(db).read_document_in_scope(
        OWNER, project_id="proj", document_id=doc_id, authorized_document_ids=[doc_id]
    )

    assert doc is not None
    assert doc.markdown == BODY
    assert doc.filename == "平安金钟罩意外险.pdf"
    assert doc.relative_path == "policy/平安金钟罩意外险.pdf"
    assert doc.source_path.endswith("平安金钟罩意外险.pdf")
    assert doc.parsed_path.endswith(f"{doc_id}.md")
    assert doc.mime_type == "application/pdf"


@pytest.mark.asyncio
async def test_another_owners_document_is_not_readable_by_id(db, tmp_path):
    """The whole risk of a read-by-id tool: ids are guessable, scope is not."""
    doc_id = await _seed(db, tmp_path, OWNER)

    doc = await _service(db).read_document_in_scope(
        STRANGER, project_id="proj", document_id=doc_id, authorized_document_ids=[doc_id]
    )

    assert doc is None


@pytest.mark.asyncio
async def test_a_document_outside_the_sessions_scope_is_not_readable(db, tmp_path):
    """Owning it is not enough — the session has to be bound to it. Otherwise
    one project's agent reads every other project's documents."""
    mine = await _seed(db, tmp_path, OWNER, slug="mine")
    other = await _seed(db, tmp_path, OWNER, slug="other")

    doc = await _service(db).read_document_in_scope(
        OWNER, project_id="proj", document_id=other, authorized_document_ids=[mine]
    )

    assert doc is None


@pytest.mark.asyncio
async def test_a_cross_owner_shared_document_reads_under_its_own_owner(db, tmp_path):
    """A shared document's row and preview live under the UPLOADER. Looking
    either up under the caller finds nothing — the failure mode is a shared
    document that reads as empty rather than as forbidden."""
    doc_id = await _seed(db, tmp_path, OWNER)

    doc = await _service(db).read_document_in_scope(
        STRANGER,
        project_id="proj",
        document_id=doc_id,
        authorized_documents=[(OWNER, doc_id)],
    )

    assert doc is not None
    assert doc.markdown == BODY
    # Accurate but unopenable: a shared document lives under its uploader's
    # tree, which this caller's runtime does not mount. Handing the path over
    # would cost the agent a turn to discover that.
    assert doc.source_path is None
    assert doc.parsed_path is None


@pytest.mark.asyncio
async def test_an_unparsed_document_reads_as_empty_not_as_missing(db, tmp_path):
    """``status`` says why there is no text. Returning ``None`` would tell the
    agent the document does not exist, and it would stop asking."""
    doc_id = await _seed(db, tmp_path, OWNER, status="ready")
    from valuz_agent.infra.fs_registry import fs_registry

    (fs_registry.docs_preview_dir(OWNER) / f"{doc_id}.md").unlink()

    doc = await _service(db).read_document_in_scope(
        OWNER, project_id="proj", document_id=doc_id, authorized_document_ids=[doc_id]
    )

    assert doc is not None
    assert doc.markdown == ""
    assert doc.parsed_path is None
    assert doc.status == "ready"


@pytest.mark.asyncio
async def test_read_and_search_resolve_the_same_scope(db, tmp_path):
    """The property the extraction exists for. If these ever disagree, one of
    them is a second authorization rule nobody reviewed."""
    mine = await _seed(db, tmp_path, OWNER, slug="mine")
    other = await _seed(db, tmp_path, OWNER, slug="other")
    svc = _service(db)

    hits = await svc.search_docs(OWNER, "proj", "q", authorized_document_ids=[mine])
    searchable = {hit.document_id for hit in hits}

    assert searchable == {mine}
    assert (
        await svc.read_document_in_scope(
            OWNER, project_id="proj", document_id=mine, authorized_document_ids=[mine]
        )
    ) is not None
    assert (
        await svc.read_document_in_scope(
            OWNER, project_id="proj", document_id=other, authorized_document_ids=[mine]
        )
    ) is None

"""Regression for valuz-oss#838: ``search_docs`` goes through the port only.

The old code special-cased the built-in runtime::

    if isinstance(self._docs_rt, EmbeddedDocsRuntime):
        results = self._docs_rt.search_sync(..., doc_paths=...)
    else:
        results = await self._docs_rt.search(query, scope_ids, top_k)

so an injected third-party ``DocsRuntimePort`` implementation took a
different code path than the embedded baseline — notably it never received
``doc_paths``. These tests pin the collapsed contract: every runtime gets the
same call, including the owner-resolved preview paths.
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
from valuz_agent.ports.docs_runtime import SearchResult

USER = "user-1"


class RecordingRuntime:
    """A minimal third-party ``DocsRuntimePort`` implementation."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def search(self, query, doc_scope_ids, top_k=5, doc_paths=None):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "query": query,
                "scope": list(doc_scope_ids),
                "top_k": top_k,
                "doc_paths": dict(doc_paths or {}),
            }
        )
        return [
            SearchResult(document_id=doc_scope_ids[0], score=1.0, snippet="hit")
            if doc_scope_ids
            else SearchResult(document_id="none", score=0.0, snippet="")
        ]

    async def health(self):  # type: ignore[no-untyped-def]
        from valuz_agent.ports.docs_runtime import DocsHealthSnapshot

        return DocsHealthSnapshot(provider_id=self.provider_id, status="healthy")

    @property
    def provider_id(self) -> str:
        return "test.recording"


class _NoParse:
    def parse_sync(self, file_path, options=None):  # type: ignore[no-untyped-def]
        raise AssertionError("parse must not run in these tests")


@pytest_asyncio.fixture()
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def db(db_engine):
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    session = factory()
    yield session
    await session.close()


async def _seed_ready_doc(db, tmp_path) -> tuple[str, str]:
    """One KB + one ready doc with a real preview file. Returns (kb_id, doc_id)."""
    from valuz_agent.infra.fs_registry import fs_registry

    kb = KnowledgeBaseRow(user_id=USER, name="kb", root_path=str(tmp_path / "root"))
    db.add(kb)
    await db.flush()

    preview_dir = fs_registry.docs_preview_dir(USER)
    preview_dir.mkdir(parents=True, exist_ok=True)

    doc = DocumentRecordRow(
        user_id=USER,
        kb_id=kb.id,
        kb_folder_id="",
        relative_path="a.md",
        source_path=str(tmp_path / "root" / "a.md"),
        source_filename="a.md",
        title="a",
        mime_type="text/markdown",
        file_size_bytes=1,
        content_hash="h",
        status="ready",
    )
    db.add(doc)
    await db.flush()

    preview = preview_dir / f"{doc.id}.md"
    preview.write_text("preview body", encoding="utf-8")
    doc.preview_text_path = f"docs/preview/{doc.id}.md"
    await db.commit()
    return kb.id, doc.id


@pytest.mark.asyncio
async def test_injected_runtime_receives_scope_and_doc_paths(db, tmp_path, monkeypatch):
    monkeypatch.setenv("VALUZ_DATA_DIR", str(tmp_path / "data"))
    from valuz_agent.infra import config as config_mod

    monkeypatch.setattr(config_mod.settings, "data_dir", str(tmp_path / "data"))

    kb_id, doc_id = await _seed_ready_doc(db, tmp_path)

    runtime = RecordingRuntime()
    service = DocumentLibraryService(
        datastore=DocumentDatastore(db),
        parser=_NoParse(),
        docs_runtime=runtime,
        event_bus=EventBus(),
    )

    hits = await service.search_docs(
        USER, "proj-1", "q", knowledge_base_ids=[kb_id]
    )

    assert runtime.calls, "injected DocsRuntimePort was never called"
    call = runtime.calls[0]
    assert call["query"] == "q"
    assert call["scope"] == [doc_id]
    assert call["doc_paths"], "doc_paths must be forwarded through the port"
    assert doc_id in call["doc_paths"]
    assert call["doc_paths"][doc_id].endswith(f"{doc_id}.md")
    assert hits and hits[0].document_id == doc_id


@pytest.mark.asyncio
async def test_no_isinstance_special_case_remains():
    import inspect

    from valuz_agent.modules.docs import service as service_mod

    src = inspect.getsource(service_mod.DocumentLibraryService.search_docs)
    assert "isinstance" not in src, (
        "search_docs must stay runtime-agnostic — dispatch through the "
        "DocsRuntimePort contract, not concrete classes (valuz-oss#838)"
    )

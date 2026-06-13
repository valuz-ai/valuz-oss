"""E2E tests for the v4.3 global KB ↔ doc ↔ project binding closed loop.

Uses a real async SQLite in-memory database — no mocks for the datastore
layer. The host moved off sync ``Session`` to ``AsyncSession`` (aiosqlite);
these tests exercise the async service/datastore surface directly.
"""

from __future__ import annotations

import shutil

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from valuz_agent.infra.database import Base
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.docs.errors import (
    KbNotFound,
    KbRootDuplicated,
    KbRootInaccessible,
)
from valuz_agent.modules.docs.models import DocumentImportTaskRow
from valuz_agent.modules.docs.service import (
    DocumentLibraryService,
)

# ── Fakes ────────────────────────────────────────────────────────────


class FakeParser:
    def parse_sync(self, file_path: str):
        from valuz_agent.ports.parser_backend import ParseResult

        return ParseResult(
            markdown=f"Parsed: {file_path}",
            metadata={"engine": "fake"},
        )


class FakeDocsRuntime:
    def __init__(self) -> None:
        self.preview_dir = None
        self.runtime_id = None

    def search_sync(self, query, doc_scope_ids, top_k=5):
        return []

    async def search(self, query, doc_scope_ids, top_k=5):
        return []


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def db_engine():
    """Shared in-memory async SQLite for the test + all inline bg work.

    ``StaticPool`` keeps a single aiosqlite connection that every session
    shares, so the inline rescan/reindex runners (which open their own
    sessions against the same factory) see the same DB as the test.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture()
def session_factory(db_engine):
    return async_sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest_asyncio.fixture()
async def db(session_factory):
    session = session_factory()
    yield session
    await session.close()


@pytest.fixture()
def tmp_kb_root(tmp_path):
    """Create a temp dir with sample files mimicking a KB root."""
    root = tmp_path / "kb_root"
    root.mkdir()

    (root / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "notes.md").write_text("# Notes\nSome content", encoding="utf-8")

    sub = root / "nvidia"
    sub.mkdir()
    (sub / "Q4-report.pdf").write_bytes(b"%PDF-1.4 Q4 data")
    (sub / "Q3-report.pdf").write_bytes(b"%PDF-1.4 Q3 data")

    drafts = sub / "drafts"
    drafts.mkdir()
    (drafts / "draft.txt").write_text("draft content", encoding="utf-8")

    return root


async def _drain(service: DocumentLibraryService) -> None:
    """Run any inline background work the service queued, to completion.

    Production dispatchers (``_schedule_background_rescan`` /
    ``_schedule_background_reindex``) each spawn a daemon thread that hosts
    its own event loop. That's correct but racy under test — the assertions
    would fire before the threads populate the DB. ``_run_bg_work_inline``
    replaces those dispatchers with ones that append a coroutine factory to
    ``service._pending`` instead of spawning a thread; this helper drains
    that queue against the service's own (test-owned) async session.

    A rescan dispatch can itself enqueue a follow-up reindex, so we loop
    until the queue is empty.
    """
    pending: list = service._pending  # type: ignore[attr-defined]
    while pending:
        make_coro = pending.pop(0)
        await make_coro()


def _run_bg_work_inline(service: DocumentLibraryService) -> None:
    """Patch the service's two background dispatchers so they enqueue
    inline coroutine factories (drained via ``_drain``) instead of
    spawning daemon threads. Tests want deterministic state, so we run the
    work on the test thread against the service's own async session."""

    service._pending = []  # type: ignore[attr-defined]

    def _inline_rescan(kb_id: str, task_id: str) -> None:
        async def _work() -> None:
            kb = await service._ds.get_kb("local-test-owner", kb_id)
            task = await service._ds.get_import_task("local-test-owner", task_id)
            if kb is None or task is None:
                return
            await service._run_rescan(kb, task)

        service._pending.append(_work)  # type: ignore[attr-defined]

    def _inline_reindex(doc_ids: list[str], task_id: str) -> None:
        async def _work() -> None:
            task = await service._ds.get_import_task("local-test-owner", task_id)
            if task is None:
                return
            await service._run_reindex_loop(doc_ids, task)

        service._pending.append(_work)  # type: ignore[attr-defined]

    service._schedule_background_rescan = _inline_rescan  # type: ignore[method-assign]
    service._schedule_background_reindex = _inline_reindex  # type: ignore[method-assign]


def _make_service(db, session_factory, parser=None) -> DocumentLibraryService:
    service = DocumentLibraryService(
        datastore=DocumentDatastore(db),
        parser=parser or FakeParser(),
        docs_runtime=FakeDocsRuntime(),
        event_bus=EventBus(),
        session_factory=session_factory,
    )
    _run_bg_work_inline(service)
    return service


@pytest.fixture()
def svc(db, session_factory):
    return _make_service(db, session_factory)


@pytest.fixture()
def svc_with_root(db, tmp_kb_root, session_factory):
    return _make_service(db, session_factory), tmp_kb_root


async def _create_kb_and_settle(service: DocumentLibraryService, **kwargs):
    """``create_kb`` + drain inline rescan/reindex so docs reach ``ready``.

    ``create_kb`` returns a detail snapshot taken *before* the background
    scan runs (``document_count == 0``, ``last_full_scan_at is None``,
    ``status == "has_processing"``). After draining the inline work the
    scan has populated the DB, so re-fetch the KB to return a settled
    detail that reflects the post-scan state.
    """
    kb = await service.create_kb(**kwargs)
    await _drain(service)
    return await service.get_kb(kb.id)


# ── 1. KB lifecycle ──────────────────────────────────────────────────


class TestKbLifecycle:
    async def test_should_create_kb_when_root_path_valid(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="Research", root_path=str(tmp_kb_root))

        assert kb.id
        assert kb.name == "Research"
        assert kb.root_path == str(tmp_kb_root)
        assert kb.document_count > 0

    async def test_should_reject_create_when_root_path_not_exists(self, svc):
        with pytest.raises(KbRootInaccessible):
            await svc.create_kb(name="Bad", root_path="/nonexistent/path/xyz")

    async def test_should_reject_duplicate_root_path(self, svc, tmp_kb_root):
        await _create_kb_and_settle(svc, name="First", root_path=str(tmp_kb_root))
        with pytest.raises(KbRootDuplicated):
            await svc.create_kb(name="Second", root_path=str(tmp_kb_root))

    async def test_should_list_kbs(self, svc, tmp_kb_root):
        await _create_kb_and_settle(svc, name="KB1", root_path=str(tmp_kb_root))
        kbs = await svc.list_kbs()
        assert len(kbs) == 1
        assert kbs[0].name == "KB1"

    async def test_should_mark_kb_processing_while_scan_task_active(
        self,
        svc,
        tmp_kb_root,
        db,
    ):
        kb = await _create_kb_and_settle(svc, name="KB1", root_path=str(tmp_kb_root))
        db.add(
            DocumentImportTaskRow(
                user_id="local-test-owner",
                id="active-rescan-task",
                task_type="rescan",
                kb_id=kb.id,
                status="processing",
            )
        )
        await db.commit()

        [listed] = await svc.list_kbs()
        assert listed.status == "has_processing"

    async def test_should_get_kb_by_id(self, svc, tmp_kb_root):
        created = await _create_kb_and_settle(svc, name="KB", root_path=str(tmp_kb_root))
        fetched = await svc.get_kb(created.id)
        assert fetched.id == created.id
        assert fetched.name == "KB"

    async def test_should_raise_when_kb_not_found(self, svc):
        with pytest.raises(KbNotFound):
            await svc.get_kb("nonexistent-id")

    async def test_should_update_kb_name(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="Old", root_path=str(tmp_kb_root))
        updated = await svc.update_kb(kb.id, name="New")
        assert updated.name == "New"

    async def test_should_delete_kb_and_cleanup(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="ToDelete", root_path=str(tmp_kb_root))
        kb_id = kb.id
        await svc.delete_kb(kb_id)

        with pytest.raises(KbNotFound):
            await svc.get_kb(kb_id)
        assert await svc.list_kbs() == []


# ── 2. Initial scan ─────────────────────────────────────────────────


class TestInitialScan:
    async def test_should_discover_files_on_create(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="Scan", root_path=str(tmp_kb_root))
        docs = await svc.list_documents(kb_id=kb.id)

        filenames = {d.filename for d in docs}
        assert "report.pdf" in filenames
        assert "notes.md" in filenames
        assert "Q4-report.pdf" in filenames
        assert "Q3-report.pdf" in filenames
        assert "draft.txt" in filenames
        assert len(docs) == 5

    async def test_should_create_folder_structure(self, svc_with_root):
        svc, root = svc_with_root
        kb = await _create_kb_and_settle(svc, name="Folders", root_path=str(root))
        tree = await svc.get_kb_tree(kb.id)

        folder_names = {n.name for n in tree if n.kind == "folder"}
        assert "nvidia" in folder_names

    async def test_should_skip_hidden_files(self, svc, tmp_path):
        root = tmp_path / "hidden_test"
        root.mkdir()
        (root / ".hidden").write_text("secret", encoding="utf-8")
        (root / "visible.txt").write_text("hello", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("git", encoding="utf-8")

        kb = await _create_kb_and_settle(svc, name="Hidden", root_path=str(root))
        docs = await svc.list_documents(kb_id=kb.id)
        assert len(docs) == 1
        assert docs[0].filename == "visible.txt"

    async def test_should_skip_unsupported_extensions(self, svc, tmp_path):
        root = tmp_path / "ext_test"
        root.mkdir()
        (root / "code.py").write_text("x = 1", encoding="utf-8")
        (root / "data.csv").write_text("a,b\n1,2", encoding="utf-8")

        kb = await _create_kb_and_settle(svc, name="Ext", root_path=str(root))
        docs = await svc.list_documents(kb_id=kb.id)
        filenames = {d.filename for d in docs}
        assert "data.csv" in filenames
        assert "code.py" not in filenames


# ── 3. Rescan (D5/D6: missing lifecycle) ─────────────────────────────


class TestRescan:
    async def test_should_detect_new_file_on_rescan(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="Rescan", root_path=str(tmp_kb_root))
        initial_count = len(await svc.list_documents(kb_id=kb.id))

        (tmp_kb_root / "new_file.txt").write_text("new", encoding="utf-8")
        await svc.rescan_kb(kb.id)
        await _drain(svc)

        docs = await svc.list_documents(kb_id=kb.id)
        assert len(docs) == initial_count + 1
        filenames = {d.filename for d in docs}
        assert "new_file.txt" in filenames

    async def test_should_mark_missing_when_file_deleted(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="Missing", root_path=str(tmp_kb_root))

        (tmp_kb_root / "notes.md").unlink()
        await svc.rescan_kb(kb.id)
        await _drain(svc)

        docs = await svc.list_documents(kb_id=kb.id)
        missing_docs = [d for d in docs if d.status == "missing"]
        missing_names = {d.filename for d in missing_docs}
        assert "notes.md" in missing_names

    async def test_should_mark_folder_missing_when_dir_deleted(
        self,
        svc,
        tmp_kb_root,
    ):
        kb = await _create_kb_and_settle(svc, name="FolderMissing", root_path=str(tmp_kb_root))

        shutil.rmtree(tmp_kb_root / "nvidia" / "drafts")
        await svc.rescan_kb(kb.id)
        await _drain(svc)

        docs = await svc.list_documents(kb_id=kb.id)
        draft_docs = [d for d in docs if "draft" in d.filename]
        assert all(d.status == "missing" for d in draft_docs)

    async def test_should_recover_missing_file_on_rescan(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="Recover", root_path=str(tmp_kb_root))

        notes_path = tmp_kb_root / "notes.md"
        notes_path.unlink()
        await svc.rescan_kb(kb.id)
        await _drain(svc)

        notes_path.write_text("recovered!", encoding="utf-8")
        await svc.rescan_kb(kb.id)
        await _drain(svc)

        docs = await svc.list_documents(kb_id=kb.id)
        notes = [d for d in docs if d.filename == "notes.md"]
        assert len(notes) == 1
        assert notes[0].status == "ready"

    async def test_should_requeue_failed_doc_on_rescan(self, svc, tmp_kb_root):
        """Rescan is the natural retry point for a previously-failed
        parse. Flip a ready doc to ``failed`` manually, then rescan
        with the file still on disk — the trigger pushes it back to
        ``queued`` (and the inline reindex parses it). The user's
        contract: 重新扫描 should pick up new configuration / fixed
        credentials and retry."""
        kb = await _create_kb_and_settle(svc, name="FailedRetry", root_path=str(tmp_kb_root))
        # Flip one doc to "failed" with a fake error code.
        docs = await svc.list_documents(kb_id=kb.id)
        target_doc = next(d for d in docs if d.filename == "notes.md")
        row = await svc._ds.get_by_id("local-test-owner", target_doc.id)
        assert row is not None
        row.status = "failed"
        row.last_error_code = "PARSE_ERROR"
        row.last_error_message = "simulated upstream failure"
        await svc._ds.update(row)

        await svc.rescan_kb(kb.id)
        await _drain(svc)

        row = await svc._ds.get_by_id("local-test-owner", target_doc.id)
        assert row is not None
        assert row.status == "ready"

    async def test_should_requeue_when_routing_pick_differs_from_parser_mode(
        self,
        db,
        session_factory,
        tmp_kb_root,
    ):
        """When the user switches parser routing (e.g. MinerU → light_local
        for PDFs), rescan should detect that the doc's recorded engine
        no longer matches the routing and requeue. Modelled here with a
        ``RoutingProbeParser`` that exposes
        ``expected_plugin_id_for_kind`` and can be flipped between
        plugin ids between rescans."""

        class _RoutingProbeParser:
            def __init__(self) -> None:
                self.pdf_pick = "light_local"

            def parse_sync(self, file_path: str):
                from valuz_agent.ports.parser_backend import ParseResult

                # Emit the engine name that ``_engine_to_plugin_id``
                # maps back to ``self.pdf_pick`` for PDFs — so a "ready"
                # parse_mode genuinely reflects the current routing.
                engine = "pymupdf4llm" if self.pdf_pick == "light_local" else "mineru"
                return ParseResult(
                    markdown=f"Parsed: {file_path}",
                    metadata={"engine": engine},
                )

            def expected_plugin_id_for_kind(self, kind: str) -> str:
                if kind == "pdf":
                    return self.pdf_pick
                return "light_local"

        parser = _RoutingProbeParser()
        service = _make_service(db, session_factory, parser=parser)

        kb = await _create_kb_and_settle(service, name="EngineSwap", root_path=str(tmp_kb_root))

        # Initial parse settled every PDF to "ready" with parser_mode
        # mapping to light_local (pymupdf4llm).
        for d in await service.list_documents(kb_id=kb.id):
            row = await service._ds.get_by_id("local-test-owner", d.id)
            assert row is not None and row.status == "ready", d.filename

        # Flip the routing to mineru for PDFs and rescan.
        parser.pdf_pick = "mineru"
        await service.rescan_kb(kb.id)
        await _drain(service)

        pdf_rows = [
            await service._ds.get_by_id("local-test-owner", d.id)
            for d in await service.list_documents(kb_id=kb.id)
            if d.filename.endswith(".pdf")
        ]
        assert pdf_rows and all(r is not None and r.parser_mode == "mineru" for r in pdf_rows), [
            r.parser_mode for r in pdf_rows if r
        ]


# ── 4. Project binding (D3: minimal cover) ───────────────────────────


class TestProjectBinding:
    async def test_should_bind_entire_kb(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="BindKB", root_path=str(tmp_kb_root))
        await svc.update_project_bindings(
            "project-1",
            [{"binding_kind": "kb", "target_id": kb.id}],
        )
        bindings = await svc.list_project_bindings("project-1")
        assert len(bindings) == 1
        assert bindings[0].binding_kind == "kb"
        assert bindings[0].target_id == kb.id

    async def test_should_minimize_redundant_bindings(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="MinCover", root_path=str(tmp_kb_root))
        docs = await svc.list_documents(kb_id=kb.id)
        doc_id = docs[0].id

        await svc.update_project_bindings(
            "project-1",
            [
                {"binding_kind": "kb", "target_id": kb.id},
                {"binding_kind": "document", "target_id": doc_id},
            ],
        )
        bindings = await svc.list_project_bindings("project-1")
        assert len(bindings) == 1
        assert bindings[0].binding_kind == "kb"

    async def test_should_count_bindings(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="Count", root_path=str(tmp_kb_root))
        await svc.update_project_bindings(
            "project-1",
            [{"binding_kind": "kb", "target_id": kb.id}],
        )
        assert await svc.count_project_bindings("project-1") == 1

    async def test_should_remove_all_bindings(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="RemoveAll", root_path=str(tmp_kb_root))
        await svc.update_project_bindings(
            "project-1",
            [{"binding_kind": "kb", "target_id": kb.id}],
        )
        await svc.remove_project_bindings("project-1")
        assert await svc.count_project_bindings("project-1") == 0

    async def test_should_delete_kb_cleanup_bindings(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="Cleanup", root_path=str(tmp_kb_root))
        await svc.update_project_bindings(
            "project-1",
            [{"binding_kind": "kb", "target_id": kb.id}],
        )
        await svc.delete_kb(kb.id)
        assert await svc.count_project_bindings("project-1") == 0


# ── 5. Scope resolution ─────────────────────────────────────────────


class TestScopeResolution:
    async def test_should_resolve_empty_scope_when_no_bindings(self, svc):
        scope = await svc.resolve_doc_scope("no-project")
        assert scope == []

    async def test_should_resolve_all_ready_docs_for_kb_binding(
        self,
        svc,
        tmp_kb_root,
        db,
    ):
        kb = await _create_kb_and_settle(svc, name="Scope", root_path=str(tmp_kb_root))

        ds = DocumentDatastore(db)
        for doc in await ds.list_documents("local-test-owner", kb_id=kb.id):
            doc.status = "ready"
            await ds.update(doc)

        await svc.update_project_bindings(
            "project-1",
            [{"binding_kind": "kb", "target_id": kb.id}],
        )
        scope = await svc.resolve_doc_scope("project-1")
        assert len(scope) == 5

    async def test_should_exclude_missing_docs_from_scope(
        self,
        svc,
        tmp_kb_root,
        db,
    ):
        kb = await _create_kb_and_settle(svc, name="ScopeMissing", root_path=str(tmp_kb_root))

        ds = DocumentDatastore(db)
        all_docs = await ds.list_documents("local-test-owner", kb_id=kb.id)
        for i, doc in enumerate(all_docs):
            doc.status = "ready" if i < 3 else "missing"
            await ds.update(doc)

        await svc.update_project_bindings(
            "project-1",
            [{"binding_kind": "kb", "target_id": kb.id}],
        )
        scope = await svc.resolve_doc_scope("project-1")
        assert len(scope) == 3

    async def test_should_resolve_folder_subtree_binding(
        self,
        svc,
        tmp_kb_root,
        db,
    ):
        kb = await _create_kb_and_settle(svc, name="FolderScope", root_path=str(tmp_kb_root))

        ds = DocumentDatastore(db)
        for doc in await ds.list_documents("local-test-owner", kb_id=kb.id):
            doc.status = "ready"
            await ds.update(doc)

        nvidia_folder = await ds.get_folder_by_path("local-test-owner", kb.id, "nvidia")
        assert nvidia_folder is not None

        await svc.update_project_bindings(
            "project-1",
            [{"binding_kind": "folder", "target_id": nvidia_folder.id}],
        )
        scope = await svc.resolve_doc_scope("project-1")
        assert len(scope) >= 2
        assert len(scope) <= 3

    async def test_should_resolve_single_document_binding(
        self,
        svc,
        tmp_kb_root,
        db,
    ):
        kb = await _create_kb_and_settle(svc, name="DocScope", root_path=str(tmp_kb_root))

        ds = DocumentDatastore(db)
        all_docs = await ds.list_documents("local-test-owner", kb_id=kb.id)
        for doc in all_docs:
            doc.status = "ready"
            await ds.update(doc)

        target_doc = all_docs[0]
        await svc.update_project_bindings(
            "project-1",
            [{"binding_kind": "document", "target_id": target_doc.id}],
        )
        scope = await svc.resolve_doc_scope("project-1")
        assert scope == [target_doc.id]


# ── 6. Doc scope tree (M09 integration) ──────────────────────────────


class TestDocScopeTree:
    async def test_should_build_empty_tree_when_no_bindings(self, svc):
        tree = await svc.build_doc_scope_tree("no-project")
        assert tree.total_documents == 0
        assert tree.knowledge_bases == ()

    async def test_should_build_tree_with_kb_binding(
        self,
        svc,
        tmp_kb_root,
        db,
    ):
        kb = await _create_kb_and_settle(svc, name="TreeKB", root_path=str(tmp_kb_root))

        ds = DocumentDatastore(db)
        for doc in await ds.list_documents("local-test-owner", kb_id=kb.id):
            doc.status = "ready"
            await ds.update(doc)

        await svc.update_project_bindings(
            "project-1",
            [{"binding_kind": "kb", "target_id": kb.id}],
        )
        tree = await svc.build_doc_scope_tree("project-1")
        assert tree.total_documents == 5
        assert len(tree.knowledge_bases) == 1
        assert tree.knowledge_bases[0].name == "TreeKB"
        assert tree.knowledge_bases[0].bound_directly is True


# ── 7. Document CRUD ─────────────────────────────────────────────────


class TestDocumentCrud:
    async def test_should_get_document_detail(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="Detail", root_path=str(tmp_kb_root))
        docs = await svc.list_documents(kb_id=kb.id)
        detail = await svc.get_document(docs[0].id)
        assert detail.source_path is not None
        assert detail.kb_id == kb.id

    async def test_should_delete_document(self, svc, tmp_kb_root):
        kb = await _create_kb_and_settle(svc, name="Delete", root_path=str(tmp_kb_root))
        docs = await svc.list_documents(kb_id=kb.id)
        initial_count = len(docs)

        await svc.delete_document(docs[0].id)
        remaining = await svc.list_documents(kb_id=kb.id)
        assert len(remaining) == initial_count - 1

    async def test_should_list_docs_filtered_by_status(self, svc, tmp_kb_root):
        # ``create_kb`` queues an inline reindex (drained here), so by the
        # time ``_create_kb_and_settle`` returns every queued doc has been
        # parsed and moved to ``ready``. Verify both filters work —
        # ``ready`` returns the full set, ``queued`` returns nothing.
        kb = await _create_kb_and_settle(svc, name="Filter", root_path=str(tmp_kb_root))
        ready = await svc.list_documents(kb_id=kb.id, status="ready")
        assert len(ready) == 5
        queued = await svc.list_documents(kb_id=kb.id, status="queued")
        assert len(queued) == 0


# ── 8. Health endpoint ───────────────────────────────────────────────


class TestHealth:
    async def test_should_report_health_status(self, svc, tmp_kb_root):
        await _create_kb_and_settle(svc, name="Health", root_path=str(tmp_kb_root))
        health = await svc.get_docs_health()
        assert health["status"] == "healthy"
        assert health["total_documents"] == 5


# ── 9. Full E2E scenario (Plan §7) ──────────────────────────────────


class TestFullE2EScenario:
    """Walks through the complete v4.3 scenario from the plan."""

    async def test_full_kb_to_agent_scope_loop(self, svc, tmp_kb_root, db):
        # 1. Create KB → scan → documents queued → inline reindex settles
        kb = await _create_kb_and_settle(
            svc,
            name="研究资料",
            root_path=str(tmp_kb_root),
        )
        assert kb.document_count == 5
        assert kb.last_full_scan_at is not None

        # 2. Mark docs as ready (simulating parse completion)
        ds = DocumentDatastore(db)
        for doc in await ds.list_documents("local-test-owner", kb_id=kb.id):
            doc.status = "ready"
            await ds.update(doc)

        # 3. Project binds nvidia/ folder
        nvidia_folder = await ds.get_folder_by_path("local-test-owner", kb.id, "nvidia")
        assert nvidia_folder is not None
        await svc.update_project_bindings(
            "project-alpha",
            [{"binding_kind": "folder", "target_id": nvidia_folder.id}],
        )

        # 4. Scope resolution → only nvidia subtree docs
        scope = await svc.resolve_doc_scope("project-alpha")
        assert len(scope) >= 2  # Q4, Q3, possibly draft

        # 5. Doc scope tree → shows nvidia folder
        tree = await svc.build_doc_scope_tree("project-alpha")
        assert tree.total_documents > 0
        kb_node = tree.knowledge_bases[0]
        assert kb_node.name == "研究资料"

        # 6. Rescan finds new file → auto-covered by folder binding
        (tmp_kb_root / "nvidia" / "Q2-report.pdf").write_bytes(
            b"%PDF Q2",
        )
        task = await svc.rescan_kb(kb.id)
        assert task.status == "completed"
        await _drain(svc)

        new_doc = await ds.get_by_relative_path("local-test-owner", kb.id, "nvidia/Q2-report.pdf")
        assert new_doc is not None and new_doc.status == "ready"

        scope_after = await svc.resolve_doc_scope("project-alpha")
        assert len(scope_after) > len(scope)

        # 7. Rescan finds deleted file → status=missing, excluded
        (tmp_kb_root / "nvidia" / "Q3-report.pdf").unlink()
        await svc.rescan_kb(kb.id)
        await _drain(svc)

        q3 = [
            d
            for d in await ds.list_documents("local-test-owner", kb_id=kb.id)
            if d.source_filename == "Q3-report.pdf"
        ]
        assert len(q3) == 1
        assert q3[0].status == "missing"

        scope_after_missing = await svc.resolve_doc_scope("project-alpha")
        assert q3[0].id not in scope_after_missing

        # 8. User manually deletes the missing doc
        await svc.delete_document(q3[0].id)
        remaining = await ds.list_documents("local-test-owner", kb_id=kb.id)
        assert not any(d.source_filename == "Q3-report.pdf" for d in remaining)

        # 9. Delete KB → all bindings cleaned
        await svc.delete_kb(kb.id)
        assert await svc.count_project_bindings("project-alpha") == 0
        assert await svc.list_kbs() == []


# ── 10. Auto-discovery scheduler ─────────────────────────────────────


class TestAutoDiscoveryScheduler:
    async def test_should_rescan_auto_discover_kbs_only(
        self,
        svc,
        tmp_kb_root,
        db,
        tmp_path,
    ):
        auto_root = tmp_path / "auto_kb"
        auto_root.mkdir()
        (auto_root / "file1.txt").write_text("auto", encoding="utf-8")

        manual_root = tmp_path / "manual_kb"
        manual_root.mkdir()
        (manual_root / "file2.txt").write_text("manual", encoding="utf-8")

        auto_kb = await _create_kb_and_settle(
            svc,
            name="AutoKB",
            root_path=str(auto_root),
            auto_discover=True,
        )
        manual_kb = await _create_kb_and_settle(
            svc,
            name="ManualKB",
            root_path=str(manual_root),
            auto_discover=False,
        )

        # Add new files after initial scan
        (auto_root / "new_auto.txt").write_text("new", encoding="utf-8")
        (manual_root / "new_manual.txt").write_text("new", encoding="utf-8")

        # Simulate scheduler's run_once — only rescan auto_discover KBs
        ds = DocumentDatastore(db)
        kbs = await ds.list_kbs("local-test-owner")
        auto_kbs = [kb for kb in kbs if kb.auto_discover]
        assert len(auto_kbs) == 1
        assert auto_kbs[0].id == auto_kb.id

        for kb in auto_kbs:
            await svc.rescan_kb(kb.id)
            await _drain(svc)

        auto_docs = await svc.list_documents(kb_id=auto_kb.id)
        manual_docs = await svc.list_documents(kb_id=manual_kb.id)

        auto_names = {d.filename for d in auto_docs}
        manual_names = {d.filename for d in manual_docs}

        assert "new_auto.txt" in auto_names
        assert "new_manual.txt" not in manual_names

"""Reindex dispatch and shared-scope contribution.

Both seams exist so a multi-process, multi-user deployment can change *who
parses* and *what a caller may read* without forking the docs service. The
tests that matter are the negative ones: the OSS default must be untouched,
and neither seam may weaken an authorization the service already makes.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from valuz_agent.infra.database import Base
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.docs.models import DocumentRecordRow
from valuz_agent.modules.docs.service import DocumentLibraryService
from valuz_agent.ports.docs_dispatch import NoopReindexDispatcher, no_extra_documents
from valuz_agent.ports.docs_runtime import SearchResult
from valuz_agent.ports.extensions import ext

OWNER = "user-owner"
CALLER = "user-caller"


class _Runtime:
    def __init__(self) -> None:
        self.scope: list[str] = []

    async def search(self, query, doc_scope_ids, top_k=5, doc_paths=None):  # type: ignore[no-untyped-def]
        self.scope = list(doc_scope_ids)
        return [SearchResult(document_id=d, score=1.0, snippet="x") for d in doc_scope_ids]

    async def health(self):  # type: ignore[no-untyped-def]
        from valuz_agent.ports.docs_runtime import DocsHealthSnapshot

        return DocsHealthSnapshot(provider_id="test", status="healthy")

    @property
    def provider_id(self) -> str:
        return "test"


@pytest.fixture(autouse=True)
def _restore():
    yield
    ext.docs_reindex_dispatcher = NoopReindexDispatcher()
    ext.docs_scope_contributor = no_extra_documents


@pytest_asyncio.fixture()
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    await session.close()
    await engine.dispose()


async def _doc(db, owner: str, status: str = "ready") -> str:
    doc = DocumentRecordRow(
        user_id=owner,
        kb_id="kb-1",
        kb_folder_id="",
        relative_path=f"{owner}.md",
        source_path=f"/tmp/{owner}.md",
        source_filename="a.md",
        title="a",
        mime_type="text/markdown",
        file_size_bytes=1,
        content_hash="h",
        status=status,
    )
    db.add(doc)
    await db.commit()
    return doc.id


def _svc(db, runtime):
    return DocumentLibraryService(
        datastore=DocumentDatastore(db),
        parser=object(),
        docs_runtime=runtime,
        event_bus=EventBus(),
    )


# ── dispatch ─────────────────────────────────────────────────────────────


def test_default_dispatcher_declines():
    """Declining is what keeps the in-process thread the OSS behavior."""
    assert NoopReindexDispatcher().dispatch("u1", ["d1"], "t1") is False


def test_a_bound_dispatcher_takes_the_documents(monkeypatch):
    taken: list[tuple[str, list[str], str]] = []

    class _Dispatcher:
        def dispatch(self, user_id, doc_ids, task_id):  # type: ignore[no-untyped-def]
            taken.append((user_id, list(doc_ids), task_id))
            return True

    ext.docs_reindex_dispatcher = _Dispatcher()
    spawned: list[int] = []
    monkeypatch.setattr("threading.Thread", lambda *a, **k: spawned.append(1) or _NeverStart())

    DocumentLibraryService._schedule_background_reindex(
        _svc(None, _Runtime()), ["d1", "d2"], "task-1", "owner-9"
    )

    assert taken == [("owner-9", ["d1", "d2"], "task-1")]
    assert spawned == [], "a handled dispatch must not also spawn the thread"


class _NeverStart:
    def start(self) -> None:  # pragma: no cover - guard, must not run
        raise AssertionError("thread started despite a handled dispatch")


def test_a_failing_dispatcher_falls_back_instead_of_stranding(monkeypatch):
    """Documents left neither dispatched nor parsed would sit queued forever."""

    class _Broken:
        def dispatch(self, user_id, doc_ids, task_id):  # type: ignore[no-untyped-def]
            raise RuntimeError("queue unreachable")

    ext.docs_reindex_dispatcher = _Broken()
    started: list[int] = []

    class _Thread:
        def __init__(self, *a, **k) -> None:  # type: ignore[no-untyped-def]
            pass

        def start(self) -> None:
            started.append(1)

    monkeypatch.setattr("threading.Thread", _Thread)

    DocumentLibraryService._schedule_background_reindex(
        _svc(None, _Runtime()), ["d1"], "task-1", "owner-9"
    )

    assert started == [1]


# ── scope contribution ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_contributed_documents_are_added_not_substituted(db):
    """A member of a shared library keeps their own documents.

    This is the difference from the pre-authorized branch, which replaces the
    scope: contributing must never cost the caller access to their own.
    """
    mine = await _doc(db, CALLER)
    theirs = await _doc(db, OWNER)
    svc = _svc(db, _Runtime())

    async def contributor(user_id):  # type: ignore[no-untyped-def]
        return [(OWNER, theirs)]

    ext.docs_scope_contributor = contributor
    # Give the caller their own document through a project binding, so the
    # union has something on both sides.
    await svc.update_project_bindings(
        CALLER, "proj", [{"binding_kind": "document", "target_id": mine}]
    )

    hits = await svc.search_docs(CALLER, "proj", "q")

    assert {h.document_id for h in hits} == {mine, theirs}


@pytest.mark.asyncio
async def test_a_contributed_document_is_still_re_authorized(db):
    """The host says who may read; the service still says whether the row is
    readable — a contributed id for a non-ready document stays out."""
    draft = await _doc(db, OWNER, status="processing")
    svc = _svc(db, _Runtime())

    async def contributor(user_id):  # type: ignore[no-untyped-def]
        return [(OWNER, draft)]

    ext.docs_scope_contributor = contributor
    assert await svc.search_docs(CALLER, "proj", "q") == []


@pytest.mark.asyncio
async def test_a_locked_research_scope_is_not_widened(db):
    """Its exactness is the whole point."""
    locked = await _doc(db, CALLER)
    other = await _doc(db, OWNER)
    runtime = _Runtime()
    svc = _svc(db, runtime)

    async def contributor(user_id):  # type: ignore[no-untyped-def]
        return [(OWNER, other)]

    ext.docs_scope_contributor = contributor
    hits = await svc.search_docs(CALLER, "proj", "q", authorized_document_ids=[locked])

    assert [h.document_id for h in hits] == [locked]


@pytest.mark.asyncio
async def test_a_failing_contributor_leaves_the_caller_their_own_scope(db):
    """A shared library briefly invisible is recoverable; a dead search is not."""
    svc = _svc(db, _Runtime())

    async def broken(user_id):  # type: ignore[no-untyped-def]
        raise RuntimeError("shared-access backend down")

    ext.docs_scope_contributor = broken
    assert await svc.search_docs(CALLER, "proj", "q") == []


@pytest.mark.asyncio
async def test_default_contributes_nothing(db):
    assert await no_extra_documents(CALLER) == ()

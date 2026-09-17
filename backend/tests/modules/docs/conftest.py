"""Shared fixtures for the docs-module tests.

These live in a conftest rather than in a test module on purpose.
``test_preview_window.py`` used to borrow them with
``pytest_plugins = ["tests.modules.docs.test_kb_e2e"]``, which promotes that
module to a **session-wide plugin**: its autouse data-dir patch then applied to
every test collected after it, anywhere in the suite, pointing
``fs_registry.data_dir()`` at a tmp directory nothing had created. Around 45
tests under ``ports/``, ``providers/``, ``modules/skills/``, ``modules/tasks/``
and elsewhere failed that way in a full run while passing in isolation. Sharing
through a conftest keeps the fixtures reusable without granting anything
session-wide.

``isolate_data_dir`` is deliberately **not** autouse. Making it autouse here
would be the same bug one scope smaller: ``test_cross_owner_doc_scope.py``
needs ``data_dir()`` to genuinely differ per owner, and a patch that ignores
``user_id`` would collapse the owner boundary those tests exist to prove.
Modules that want the isolation opt in explicitly::

    pytestmark = pytest.mark.usefixtures("isolate_data_dir")
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from valuz_agent.infra.database import Base
from valuz_agent.infra.eventbus import EventBus
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.docs.service import DocumentLibraryService

# ── Fakes ────────────────────────────────────────────────────────────


class FakeParser:
    def parse_sync(self, file_path: str, options=None):
        from valuz_agent.ports.parser_backend import ParseResult

        return ParseResult(
            markdown=f"Parsed: {file_path}",
            metadata={"engine": "fake"},
        )


class FakeDocsRuntime:
    def __init__(self) -> None:
        self.preview_dir = None
        self.runtime_id = None

    def search_sync(self, query, doc_scope_ids, top_k=5, doc_paths=None):
        return []

    async def search(self, query, doc_scope_ids, top_k=5, doc_paths=None):
        return []


# ── Database ─────────────────────────────────────────────────────────


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


# ── Filesystem ───────────────────────────────────────────────────────


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


@pytest.fixture()
def isolate_data_dir(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Point VALUZ_DATA_DIR at a tmp local root so preview writes do not
    touch the real data_dir.

    Opt in per module with ``pytest.mark.usefixtures`` — see this file's
    docstring for why it is not autouse.
    """
    from valuz_agent.infra import fs_registry

    monkeypatch.setattr(fs_registry.fs_registry, "data_dir", lambda user_id: tmp_path / "_assets")


# ── Service ──────────────────────────────────────────────────────────


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

    async def _inline_reindex(
        doc_ids: list[str], task_id: str, user_id: str = "local-test-owner"
    ) -> None:
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
def make_service():
    """The service builder itself, for tests that need a custom parser.

    Handed out as a fixture rather than imported from this conftest —
    importing a conftest module by name is what pytest's fixture injection
    exists to avoid.
    """
    return _make_service


@pytest.fixture()
def svc(db, session_factory):
    return _make_service(db, session_factory)


@pytest.fixture()
def svc_with_root(db, tmp_kb_root, session_factory):
    return _make_service(db, session_factory), tmp_kb_root

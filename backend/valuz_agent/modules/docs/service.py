from __future__ import annotations

import logging
import mimetypes
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.docs.datastore import DocumentDatastore
from valuz_agent.modules.docs.errors import (
    DocumentNotFound,
    ImportTaskNotFound,
    KbNotFound,
    KbRootDuplicated,
    KbRootInaccessible,
)
from valuz_agent.modules.docs.models import (
    DocumentImportTaskRow,
    DocumentRecordRow,
    KbFolderRow,
    KnowledgeBaseRow,
    ProjectKbBindingRow,
)
from valuz_agent.ports.docs_runtime import DocsRuntimePort
from valuz_agent.ports.parser_backend import ParserBackend, ParseResult

logger = logging.getLogger(__name__)


# Reverse map ``DocumentRecordRow.parser_mode`` (a low-level engine
# name stamped by the parser backend on success) back to the
# high-level plugin id used by the router. ``light_local`` advertises
# several internal engines (one per extension family); cloud plugins
# emit their plugin id verbatim as the engine.
#
# Used by ``_run_rescan`` to compare "engine that last parsed this doc"
# against "engine the router would pick today" and decide whether to
# requeue. Unknown / missing engine values map to ``None`` and the
# caller treats that as "skip" (no requeue) — better to leave a doc
# alone than thrash it on every rescan tick.
_ENGINE_TO_PLUGIN_ID: dict[str, str] = {
    # light_local internal engines (one per file-kind handler).
    "rapidocr": "light_local",
    "pymupdf4llm": "light_local",
    "markitdown": "light_local",
    "html_to_markdown": "light_local",
    "plain_text": "light_local",
    # Cloud plugins — engine name == plugin id by construction
    # (see plugins/parser/{mineru,paddleocr}/*).
    "mineru": "mineru",
    "paddleocr": "paddleocr",
}


def _engine_to_plugin_id(engine: str | None) -> str | None:
    """Translate a stored ``parser_mode`` value to the plugin id the
    router uses. Returns ``None`` when the mapping is unknown."""
    if not engine:
        return None
    return _ENGINE_TO_PLUGIN_ID.get(engine)


SUPPORTED_EXTS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".csv",
    ".pptx",
    ".html",
    ".txt",
    ".xml",
    ".json",
    ".md",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
    ".bmp",
    ".webp",
}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


# ── Value Objects ─────────────────────────────────────────────────────


@dataclass
class KbListItem:
    id: str
    name: str
    root_path: str
    parser_routing: str
    document_count: int
    status: str  # all_ready | has_processing | has_missing
    created_at: int | None = None


@dataclass
class KbDetail(KbListItem):
    auto_discover: bool = False
    last_full_scan_at: int | None = None


@dataclass
class DocumentListItem:
    id: str
    filename: str
    title: str | None
    status: str
    chunk_count: int
    file_size_bytes: int
    mime_type: str | None
    kb_id: str | None = None
    kb_folder_id: str | None = None
    relative_path: str | None = None
    created_at: int | None = None


@dataclass
class ParserAttempt:
    plugin_id: str
    error: str
    occurred_at: str
    ok: bool = False


@dataclass
class DocumentDetail(DocumentListItem):
    source_path: str | None = None
    parser_mode: str | None = None
    docs_runtime_id: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    parser_attempts: list[ParserAttempt] = field(default_factory=list)


@dataclass
class DocSearchHit:
    document_id: str
    filename: str
    score: float
    snippet: str
    page_ref: str | None = None
    chunk_ref: str | None = None


@dataclass
class ImportTaskItemError:
    doc_id: str
    filename: str
    plugin_id: str
    error: str
    occurred_at: str


@dataclass
class ImportTaskResult:
    task_id: str
    task_type: str
    status: str
    total_items: int
    processed_items: int
    failed_items: int
    kb_id: str | None = None
    workspace_id: str | None = None
    created_at: int | None = None
    errors: list[ImportTaskItemError] = field(default_factory=list)


@dataclass(frozen=True)
class DocScopeBoundNode:
    kind: str  # kb | folder | document
    id: str
    name: str
    path: str | None
    bound_directly: bool
    document_count: int
    children: tuple[DocScopeBoundNode, ...] = ()


@dataclass(frozen=True)
class DocScopeTreeView:
    knowledge_bases: tuple[DocScopeBoundNode, ...]
    total_documents: int


@dataclass
class KbTreeNode:
    id: str
    name: str
    relative_path: str
    kind: str  # folder | document
    status: str
    document_count: int = 0
    children: list[KbTreeNode] = field(default_factory=list)


# ── Row → VO mappers ─────────────────────────────────────────────────


def _row_to_list_item(row: DocumentRecordRow) -> DocumentListItem:
    return DocumentListItem(
        id=row.id,
        filename=row.source_filename,
        title=row.title,
        status=row.status,
        chunk_count=row.chunk_count,
        file_size_bytes=row.file_size_bytes,
        mime_type=row.mime_type,
        kb_id=row.kb_id,
        kb_folder_id=row.kb_folder_id,
        relative_path=row.relative_path,
        created_at=row.created_at,
    )


def _decode_parser_attempts(raw: str | None) -> list[ParserAttempt]:
    if not raw:
        return []
    import json as _json

    try:
        data = _json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[ParserAttempt] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        error_val = str(entry.get("error") or "")
        # Backward-compat: if the stored row pre-dates the ``ok`` field,
        # infer success from an empty error string.
        ok_val = bool(entry["ok"]) if "ok" in entry else (error_val == "")
        out.append(
            ParserAttempt(
                plugin_id=str(entry.get("plugin_id") or ""),
                error=error_val,
                occurred_at=str(entry.get("occurred_at") or ""),
                ok=ok_val,
            )
        )
    return out


def _decode_task_errors(raw: str | None) -> list[ImportTaskItemError]:
    if not raw:
        return []
    import json as _json

    try:
        data = _json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[ImportTaskItemError] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        out.append(
            ImportTaskItemError(
                doc_id=str(entry.get("doc_id") or ""),
                filename=str(entry.get("filename") or ""),
                plugin_id=str(entry.get("plugin_id") or ""),
                error=str(entry.get("error") or ""),
                occurred_at=str(entry.get("occurred_at") or ""),
            )
        )
    return out


def _row_to_detail(row: DocumentRecordRow) -> DocumentDetail:
    return DocumentDetail(
        id=row.id,
        filename=row.source_filename,
        title=row.title,
        status=row.status,
        chunk_count=row.chunk_count,
        file_size_bytes=row.file_size_bytes,
        mime_type=row.mime_type,
        kb_id=row.kb_id,
        kb_folder_id=row.kb_folder_id,
        relative_path=row.relative_path,
        created_at=row.created_at,
        source_path=row.source_path,
        parser_mode=row.parser_mode,
        docs_runtime_id=row.docs_runtime_id,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        parser_attempts=_decode_parser_attempts(getattr(row, "parser_attempts_json", None)),
    )


def _task_to_result(row: DocumentImportTaskRow) -> ImportTaskResult:
    return ImportTaskResult(
        task_id=row.id,
        task_type=row.task_type,
        status=row.status,
        total_items=row.total_items,
        processed_items=row.processed_items,
        failed_items=row.failed_items,
        kb_id=row.kb_id,
        workspace_id=row.workspace_id,
        created_at=row.created_at,
        errors=_decode_task_errors(getattr(row, "errors_json", None)),
    )


# ── Service ───────────────────────────────────────────────────────────


class DocumentLibraryService:
    def __init__(
        self,
        datastore: DocumentDatastore,
        parser: ParserBackend,
        docs_runtime: DocsRuntimePort,
        event_bus: EventBus,
        scan_state_dir: Path | None = None,
        session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        self._ds = datastore
        self._parser = parser
        self._docs_rt = docs_runtime
        self._bus = event_bus
        self._scan_state_dir = scan_state_dir
        # Used by ``_schedule_background_reindex`` to open a fresh async DB
        # session on the worker thread (the request's ``datastore`` session
        # closes when the HTTP handler returns). The background runners host
        # their own event loop via ``asyncio.run`` and open the session with
        # ``async_unit_of_work``; ``session_factory`` is retained only for
        # tests that inject an ``AsyncSession`` factory bound to their own
        # in-memory engine. ``None`` → the runner uses ``async_unit_of_work``.
        self._session_factory = session_factory

    def _snapshot_routing_for_kinds(self) -> dict[str, str]:
        """Snapshot ``{kind → plugin_id}`` using whatever effective
        routing the parser would apply at parse time.

        Asks the router for ``expected_plugin_id_for_kind(kind)`` if
        it exposes one (production ``ParserRouter`` does); for any
        other ``ParserBackend`` shape (tests pass in stubs) returns
        an empty dict so the rescan loop's mismatch trigger no-ops.
        """
        probe = getattr(self._parser, "expected_plugin_id_for_kind", None)
        if not callable(probe):
            return {}
        try:
            return {
                kind: probe(kind)
                for kind in ("pdf", "image", "office", "spreadsheet", "web", "text")
            }
        except Exception:  # noqa: BLE001 — routing snapshot is best-effort
            logger.exception("rescan: failed to snapshot routing for kinds")
            return {}

    # ── KB lifecycle ──────────────────────────────────────────────────

    async def create_kb(
        self,
        name: str,
        root_path: str,
        parser_routing: str = "local_only",
        auto_discover: bool = False,
    ) -> KbDetail:
        root = Path(root_path).expanduser().resolve()
        if not root.is_dir():
            raise KbRootInaccessible()
        root_str = str(root)
        if await self._ds.kb_root_path_exists(root_str):
            raise KbRootDuplicated()

        kb = KnowledgeBaseRow(
            id=uuid.uuid4().hex,
            name=name,
            root_path=root_str,
            parser_routing=parser_routing,
            auto_discover=auto_discover,
        )
        await self._ds.create_kb(kb)
        self._bus.publish("kb.created", kb_id=kb.id)
        # Kick off the initial scan in a background thread so the
        # HTTP response returns immediately. The rescan diff handles
        # the empty-DB case (every folder + file looks "new") just as
        # well as the steady-state diff, so there's no need for a
        # separate ``_initial_scan`` code path.
        await self.start_rescan_kb(kb.id)
        return await self._kb_to_detail(kb)

    async def list_kbs(self) -> list[KbListItem]:
        rows = await self._ds.list_kbs()
        return [await self._kb_to_list_item(r) for r in rows]

    async def get_kb(self, kb_id: str) -> KbDetail:
        row = await self._ds.get_kb(kb_id)
        if not row:
            raise KbNotFound()
        return await self._kb_to_detail(row)

    async def update_kb(
        self,
        kb_id: str,
        name: str | None = None,
        parser_routing: str | None = None,
    ) -> KbDetail:
        row = await self._ds.get_kb(kb_id)
        if not row:
            raise KbNotFound()
        if name is not None:
            row.name = name
        if parser_routing is not None:
            row.parser_routing = parser_routing
        await self._ds.update_kb(row)
        return await self._kb_to_detail(row)

    async def delete_kb(self, kb_id: str) -> None:
        row = await self._ds.get_kb(kb_id)
        if not row:
            raise KbNotFound()
        await self._ds.delete_kb(kb_id)
        self._bus.publish("kb.deleted", kb_id=kb_id)

    # ── KB tree view ──────────────────────────────────────────────────

    async def get_kb_tree(self, kb_id: str, folder_id: str | None = None) -> list[KbTreeNode]:
        folders = await self._ds.list_folders(kb_id, parent_folder_id=folder_id)

        nodes: list[KbTreeNode] = []
        for f in folders:
            doc_count = await self._ds.count_docs_in_folder_subtree(
                kb_id,
                f.id,
            )
            nodes.append(
                KbTreeNode(
                    id=f.id,
                    name=f.display_name,
                    relative_path=f.relative_path,
                    kind="folder",
                    status=f.status,
                    document_count=doc_count,
                )
            )

        if folder_id is not None:
            folder_docs = await self._ds.list_documents(
                kb_id=kb_id,
                kb_folder_id=folder_id,
            )
        else:
            all_docs = await self._ds.list_documents(kb_id=kb_id)
            all_folder_ids = {f.id for f in await self._ds.list_all_folders(kb_id)}
            folder_docs = [
                d for d in all_docs if not d.kb_folder_id or d.kb_folder_id not in all_folder_ids
            ]

        for d in folder_docs:
            nodes.append(
                KbTreeNode(
                    id=d.id,
                    name=d.source_filename,
                    relative_path=d.relative_path,
                    kind="document",
                    status=d.status,
                )
            )
        return nodes

    # ── Initial scan ──────────────────────────────────────────────────

    # ── Rescan ────────────────────────────────────────────────────────

    async def rescan_kb(self, kb_id: str) -> ImportTaskResult:
        """Rescan a KB end-to-end (diff + reindex dispatch) — used by the
        auto-discovery scheduler (already off the HTTP request path), and as
        the inner work loop of ``start_rescan_kb``. See ``_run_rescan`` for the
        three-phase diff algorithm.

        HTTP / user-triggered rescans should call ``start_rescan_kb``
        instead so the request returns immediately on large libraries.
        """
        kb = await self._ds.get_kb(kb_id)
        if not kb:
            raise KbNotFound()
        task = await self._create_rescan_task(kb_id)
        await self._run_rescan(kb, task)
        return _task_to_result(task)

    async def start_rescan_kb(self, kb_id: str) -> ImportTaskResult:
        """Kick off a rescan in a background thread and return the
        task row immediately. The HTTP layer + ``create_kb`` use this
        so the dialog doesn't freeze on large directories — the
        filesystem walk + per-file inserts can take many seconds on
        a folder with thousands of files, and the subsequent parsing
        (cloud OCR especially) can take minutes per document.

        Progress is observable via ``/v1/docs/tasks/{task_id}`` on
        the returned task row.
        """
        kb = await self._ds.get_kb(kb_id)
        if not kb:
            raise KbNotFound()
        task = await self._create_rescan_task(kb_id)
        self._schedule_background_rescan(kb_id, task.id)
        return _task_to_result(task)

    async def _create_rescan_task(self, kb_id: str) -> DocumentImportTaskRow:
        task = DocumentImportTaskRow(
            id=uuid.uuid4().hex,
            task_type="rescan",
            kb_id=kb_id,
            status="processing",
        )
        await self._ds.create_import_task(task)
        return task

    async def _run_rescan(self, kb: KnowledgeBaseRow, task: DocumentImportTaskRow) -> None:
        """Diff the on-disk tree against the indexed tree and converge.

        Walks ``kb.root_path`` once, then in three phases:

        1. **Folder tree upsert** — every directory present on disk is
           ensured to exist in ``valuz_kb_folder`` with the correct
           ``parent_folder_id``; previously-missing folders flip back to
           ``active``. Folders no longer present on disk flip to
           ``missing`` (soft-delete preserves any binding rows that point
           at them).

           Iteration order is sorted by depth so each parent exists in
           ``folder_map`` before its child needs to look one up — without
           this, freshly-created subdirectories would land with
           ``parent_folder_id = NULL`` and the tree view would render
           them at the root.

        2. **Document state sync** — known docs that reappear on disk
           flip out of ``missing``; known docs that vanish flip into
           ``missing``. While we have the row in hand, also repair any
           ``kb_folder_id`` left empty by an older buggy rescan path
           (pre-fix this was set to ``""`` when the parent folder didn't
           yet exist).

        3. **New file ingestion** — files we've never seen are inserted
           with ``kb_folder_id`` looked up from ``folder_map`` (now
           guaranteed to contain every folder that exists on disk).

        The 5-minute auto-discovery cron (`scheduler.py`) calls this on
        every tick, so each phase must be idempotent and tolerant of
        partial-write races.
        """
        kb_id = kb.id
        root = Path(kb.root_path)

        current_files: set[str] = set()
        current_dirs: set[str] = set()
        if root.is_dir():
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                rel = os.path.relpath(dirpath, root)
                if rel != ".":
                    current_dirs.add(rel)
                for fname in filenames:
                    if fname.startswith("."):
                        continue
                    file_rel = os.path.relpath(os.path.join(dirpath, fname), root)
                    current_files.add(file_rel)

        # ── Phase 1: folder tree upsert ──
        # Index existing rows by relative_path so we can decide create vs
        # update without N round-trips; folder_map collects every active
        # folder's id keyed by relative_path so phase 2/3 can look up
        # parent ids without DB hits.
        existing_by_path: dict[str, KbFolderRow] = {
            f.relative_path: f for f in await self._ds.list_all_folders(kb_id)
        }
        folder_map: dict[str, str] = {}

        # Sort by depth (number of separators) then lexicographically so
        # parents are processed before their children — this is the
        # invariant that makes ``parent_folder_id`` lookups always succeed.
        for dir_rel in sorted(current_dirs, key=lambda p: (p.count(os.sep), p)):
            parent_rel = os.path.dirname(dir_rel)
            parent_id = folder_map.get(parent_rel) if parent_rel else None

            existing = existing_by_path.get(dir_rel)
            if existing is None:
                row = KbFolderRow(
                    id=uuid.uuid4().hex,
                    kb_id=kb_id,
                    parent_folder_id=parent_id,
                    relative_path=dir_rel,
                    display_name=os.path.basename(dir_rel),
                    status="active",
                )
                await self._ds.create_folder(row)
                folder_map[dir_rel] = row.id
            else:
                changed = False
                if existing.status != "active":
                    existing.status = "active"
                    changed = True
                # Repair stale parent links — a previous rescan may have
                # left parent_folder_id NULL because the parent didn't
                # exist yet at the time.
                if existing.parent_folder_id != parent_id:
                    existing.parent_folder_id = parent_id
                    changed = True
                if changed:
                    await self._ds.update_folder(existing)
                folder_map[dir_rel] = existing.id

        # Folders no longer present on disk → mark missing (soft).
        for path, row in existing_by_path.items():
            if path not in current_dirs and row.status != "missing":
                row.status = "missing"
                await self._ds.update_folder(row)

        # ── Phase 2: document state sync ──
        #
        # Three requeue triggers per present-on-disk doc:
        #
        # 1. ``status == "missing"`` — the file reappeared after we
        #    previously soft-deleted it. Pre-existing behaviour.
        # 2. ``status == "failed"`` — the last parse attempt failed.
        #    Rescan is the natural place to retry: configuration may
        #    have changed (new credentials, model downloaded) and the
        #    user expects "重新扫描" to pick that up.
        # 3. ``status == "ready"`` AND the engine recorded on the doc
        #    (``parser_mode``) maps to a different plugin id than what
        #    the router would pick for this kind today. Catches the
        #    "I switched from MinerU to PaddleOCR for PDFs" case: the
        #    doc is on disk and was parsed cleanly once, but the user
        #    expects new content with the new engine. Routing decision
        #    lives in ``ParserRouter.expected_plugin_id_for_kind`` so
        #    docs service doesn't duplicate the by_kind / primary_id /
        #    ready-gate logic.
        from valuz_agent.modules.parser.router import classify

        kind_to_plugin = self._snapshot_routing_for_kinds()

        all_docs = await self._ds.list_documents(kb_id=kb_id)
        new_count = 0
        for doc in all_docs:
            if doc.relative_path in current_files:
                changed = False
                requeue = False
                # Trigger 1: file reappeared on disk after being missing.
                if doc.status == "missing":
                    requeue = True
                # Trigger 2: previous parse failed — retry with the
                # current effective engine.
                elif doc.status == "failed":
                    requeue = True
                # Trigger 3: ready doc whose parser_mode no longer
                # matches the routing's pick. Only requeue when BOTH
                # sides resolve to a known plugin id; an unknown
                # parser_mode (e.g. ``"unknown"`` from a legacy row)
                # leaves the doc alone to avoid loops.
                elif doc.status == "ready":
                    actual_plugin = _engine_to_plugin_id(doc.parser_mode)
                    expected_plugin = kind_to_plugin.get(classify(doc.relative_path))
                    if (
                        actual_plugin is not None
                        and expected_plugin is not None
                        and actual_plugin != expected_plugin
                    ):
                        requeue = True
                        logger.info(
                            "rescan: doc %s parser_mode=%s (plugin=%s) "
                            "differs from current routing plugin=%s — "
                            "queuing for re-parse",
                            doc.id,
                            doc.parser_mode,
                            actual_plugin,
                            expected_plugin,
                        )

                if requeue:
                    doc.status = "queued"
                    doc.discovery_source = "rescan"
                    doc.last_error_code = None
                    doc.last_error_message = None
                    changed = True
                    new_count += 1

                # Repair empty kb_folder_id from older buggy rescans —
                # phase 1 has populated folder_map for every present dir.
                expected_folder_id = folder_map.get(os.path.dirname(doc.relative_path), "")
                if doc.kb_folder_id != expected_folder_id:
                    doc.kb_folder_id = expected_folder_id
                    changed = True
                if changed:
                    await self._ds.update(doc)
                current_files.discard(doc.relative_path)
            else:
                if doc.status not in ("missing", "deleted"):
                    doc.status = "missing"
                    await self._ds.update(doc)

        # ── Phase 3: new file ingestion ──
        for file_rel in current_files:
            ext = Path(file_rel).suffix.lower()
            if ext not in SUPPORTED_EXTS:
                continue
            file_path = str(root / file_rel)
            try:
                stat = os.stat(file_path)
            except OSError:
                continue
            if stat.st_size > MAX_FILE_SIZE:
                continue
            dir_rel = os.path.dirname(file_rel)
            folder_id = folder_map.get(dir_rel, "") if dir_rel else ""
            mime, _ = mimetypes.guess_type(file_rel)
            doc = DocumentRecordRow(
                id=uuid.uuid4().hex,
                kb_id=kb_id,
                kb_folder_id=folder_id,
                relative_path=file_rel,
                source_path=file_path,
                source_filename=os.path.basename(file_rel),
                title=Path(file_rel).stem,
                mime_type=mime,
                file_size_bytes=stat.st_size,
                discovery_source="rescan",
                status="queued",
            )
            await self._ds.create(doc)
            new_count += 1

        await self._update_folder_counts(kb_id)
        task.total_items = new_count
        task.status = "completed"
        await self._ds.update_import_task(task)
        kb.last_full_scan_at = now_ms()
        await self._ds.update_kb(kb)

        queued_ids = await self._ds.list_doc_ids_by_kb(kb_id, status="queued")
        if queued_ids:
            # Reindex in a background thread so the rescan HTTP request
            # returns immediately. Cloud parsers (MinerU/PaddleOCR) can
            # take minutes per document via async-poll; running them
            # inline would freeze the UI's "重新扫描" button and burn
            # one of FastAPI's threadpool slots for the whole duration.
            # Progress is observable via /v1/docs/tasks/{task_id} on the
            # ``reindex`` task created here.
            reindex_task = DocumentImportTaskRow(
                id=uuid.uuid4().hex,
                task_type="reindex",
                status="processing",
                total_items=len(queued_ids),
            )
            await self._ds.create_import_task(reindex_task)
            self._schedule_background_reindex(queued_ids, reindex_task.id)

        self._bus.publish("kb.rescanned", kb_id=kb_id)
        return _task_to_result(task)

    def _schedule_background_rescan(self, kb_id: str, task_id: str) -> None:
        """Spawn a daemon thread that runs the rescan diff + reindex
        with a fresh DB session. Mirrors ``_schedule_background_reindex``
        — the request thread has already returned by the time this runs,
        so we can't share the request's ``datastore``.

        The thread has no event loop of its own, so it hosts one via
        ``asyncio.run`` to drive the now-async service against an
        ``async_unit_of_work`` session.
        """
        import asyncio
        import threading

        parser = self._parser
        docs_rt = self._docs_rt
        bus = self._bus
        scan_state_dir = self._scan_state_dir
        session_factory = self._session_factory

        async def _arun() -> None:
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.docs.datastore import DocumentDatastore

            try:
                async with async_unit_of_work(commit=False) as db:
                    local_service = DocumentLibraryService(
                        datastore=DocumentDatastore(db),
                        parser=parser,
                        docs_runtime=docs_rt,
                        event_bus=bus,
                        scan_state_dir=scan_state_dir,
                        session_factory=session_factory,
                    )
                    kb = await local_service._ds.get_kb(kb_id)
                    task = await local_service._ds.get_import_task(task_id)
                    if kb is None or task is None:
                        logger.warning(
                            "background rescan skipped: kb=%s task=%s missing",
                            kb_id,
                            task_id,
                        )
                        return
                    await local_service._run_rescan(kb, task)
            except Exception:
                logger.exception("background rescan failed for kb=%s", kb_id)
                # Best-effort failure marker so the UI's task poller
                # doesn't hang on ``processing`` forever.
                try:
                    async with async_unit_of_work(commit=False) as fail_db:
                        failed_ds = DocumentDatastore(fail_db)
                        failed_task = await failed_ds.get_import_task(task_id)
                        if failed_task is not None and failed_task.status == "processing":
                            failed_task.status = "failed"
                            await failed_ds.update_import_task(failed_task)
                except Exception:
                    logger.exception("could not mark rescan task as failed")

        def _runner() -> None:
            asyncio.run(_arun())

        threading.Thread(target=_runner, name="docs-bg-rescan", daemon=True).start()

    def _schedule_background_reindex(self, doc_ids: list[str], task_id: str) -> None:
        """Spawn a daemon thread that reindexes ``doc_ids`` using a
        fresh DB session (the request's session is closed when the
        HTTP handler returns).

        ``task_id`` is the already-created ``DocumentImportTaskRow`` id so the
        background thread can update ``processed_items`` / ``failed_items`` /
        ``status`` in real time and callers can poll
        ``GET /v1/docs/tasks/{task_id}`` for live progress.

        The thread has no event loop of its own, so it hosts one via
        ``asyncio.run`` to drive the now-async service against an
        ``async_unit_of_work`` session.
        """
        import asyncio
        import threading

        # Snapshot the dependencies that ARE thread-safe to share.
        # ``self._parser`` is the process-wide ``ParserRouter`` (with
        # its own scheduler + secret store); ``self._docs_rt`` is the
        # in-memory ``EmbeddedDocsRuntime``; ``self._bus`` is the
        # in-process event bus. None hold per-request state.
        parser = self._parser
        docs_rt = self._docs_rt
        bus = self._bus
        scan_state_dir = self._scan_state_dir
        session_factory = self._session_factory

        async def _arun() -> None:
            from valuz_agent.infra.db import async_unit_of_work
            from valuz_agent.modules.docs.datastore import DocumentDatastore

            try:
                async with async_unit_of_work(commit=False) as db:
                    local_service = DocumentLibraryService(
                        datastore=DocumentDatastore(db),
                        parser=parser,
                        docs_runtime=docs_rt,
                        event_bus=bus,
                        scan_state_dir=scan_state_dir,
                        session_factory=session_factory,
                    )
                    task = await local_service._ds.get_import_task(task_id)
                    if task is None:
                        logger.error("background reindex: task %s not found", task_id)
                        return
                    await local_service._run_reindex_loop(doc_ids, task)
            except Exception:
                logger.exception("background reindex failed")
                # Best-effort: flip task to failed so the UI doesn't
                # show a stuck "processing" state forever.
                try:
                    async with async_unit_of_work(commit=False) as fail_db:
                        fail_ds = DocumentDatastore(fail_db)
                        t = await fail_ds.get_import_task(task_id)
                        if t is not None and t.status == "processing":
                            t.status = "failed"
                            await fail_ds.update_import_task(t)
                except Exception:
                    logger.exception("could not mark reindex task as failed")

        def _runner() -> None:
            asyncio.run(_arun())

        threading.Thread(target=_runner, name="docs-bg-reindex", daemon=True).start()

    # ── Document CRUD ─────────────────────────────────────────────────

    async def list_documents(
        self, query: str | None = None, status: str | None = None, kb_id: str | None = None
    ) -> list[DocumentListItem]:
        rows = await self._ds.list_documents(query=query, status=status, kb_id=kb_id)
        return [_row_to_list_item(r) for r in rows]

    async def get_document(self, doc_id: str) -> DocumentDetail:
        row = await self._ds.get_by_id(doc_id)
        if not row:
            raise DocumentNotFound()
        return _row_to_detail(row)

    async def delete_document(self, doc_id: str) -> None:
        row = await self._ds.get_by_id(doc_id)
        if not row:
            raise DocumentNotFound()
        folder_id = row.kb_folder_id
        await self._ds.delete(doc_id)
        if folder_id:
            await self._update_folder_counts(row.kb_id)
        self._bus.publish("doc.deleted", document_id=doc_id)

    async def get_document_preview(self, doc_id: str) -> str:
        row = await self._ds.get_by_id(doc_id)
        if not row:
            raise DocumentNotFound()
        if row.preview_text_path:
            p = Path(row.preview_text_path)
            if p.exists():
                return p.read_text(encoding="utf-8")
        return ""

    async def reindex_documents(self, document_ids: list[str]) -> ImportTaskResult:
        """Create a reindex task and dispatch the per-doc parse loop to a
        background thread.  Returns immediately with a ``processing`` task so
        the HTTP request is not blocked by potentially slow cloud parsers.
        Progress is observable via ``GET /v1/docs/tasks/{task_id}``."""
        task = DocumentImportTaskRow(
            id=uuid.uuid4().hex,
            task_type="reindex",
            status="processing",
            total_items=len(document_ids),
        )
        await self._ds.create_import_task(task)
        self._schedule_background_reindex(document_ids, task.id)
        return _task_to_result(task)

    async def _run_reindex_loop(self, document_ids: list[str], task: DocumentImportTaskRow) -> None:
        """Execute the per-doc parse loop and update *task* in place.  Must be
        called from the background thread opened by
        ``_schedule_background_reindex`` so that it runs against the thread's
        own DB session.  Never call on the request event loop — the per-doc
        parse can block for many seconds."""
        import json as _json

        task_errors: list[dict[str, str]] = []

        for doc_id in document_ids:
            row = await self._ds.get_by_id(doc_id)
            if not row:
                task.failed_items += 1
                await self._ds.update_import_task(task)
                continue
            if row.status == "missing":
                task.failed_items += 1
                await self._ds.update_import_task(task)
                continue
            row.status = "processing"
            await self._ds.update(row)

            result = self._parser_parse_sync(row.source_path)

            # ── Record per-plugin attempt history on the doc + task ──
            # ``fallback_from`` is set by ``ParserRouter`` when it
            # demoted the user's chosen plugin after a runtime error;
            # ``fallback_error`` carries the original message. Persist
            # both so the UI can show "MinerU failed: 200 pages — auto
            # fell back to LightLocal" even when the doc ends up ready.
            attempts: list[dict[str, object]] = []
            fallback_from = result.metadata.get("fallback_from")
            fallback_error = result.metadata.get("fallback_error")
            if fallback_from:
                attempt_record: dict[str, object] = {
                    "plugin_id": fallback_from,
                    "error": fallback_error or "<unknown>",
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "ok": False,
                }
                attempts.append(attempt_record)
                task_errors.append(
                    {
                        "doc_id": doc_id,
                        "filename": row.source_filename,
                        "plugin_id": str(attempt_record["plugin_id"]),
                        "error": str(attempt_record["error"]),
                        "occurred_at": str(attempt_record["occurred_at"]),
                    }
                )

            if "error" in result.metadata and result.metadata["error"]:
                # Final plugin also failed (no further fallback). Record
                # that attempt too so the doc panel shows both attempts
                # (e.g. mineru failed → light_local failed).
                final_plugin = result.metadata.get("plugin_id") or result.metadata.get(
                    "engine", "unknown"
                )
                final_err = str(result.metadata.get("error") or "")
                final_record: dict[str, object] = {
                    "plugin_id": final_plugin,
                    "error": final_err,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "ok": False,
                }
                attempts.append(final_record)
                task_errors.append(
                    {
                        "doc_id": doc_id,
                        "filename": row.source_filename,
                        "plugin_id": str(final_record["plugin_id"]),
                        "error": str(final_record["error"]),
                        "occurred_at": str(final_record["occurred_at"]),
                    }
                )
                row.status = "failed"
                row.last_error_code = "PARSE_ERROR"
                row.last_error_message = final_err
                row.parser_attempts_json = _json.dumps(attempts)
                await self._ds.update(row)
                task.failed_items += 1
                await self._ds.update_import_task(task)
            else:
                # Record the successful parse attempt so the UI's
                # "解析记录" panel is never empty, even for docs that
                # parsed cleanly on the first try.
                success_plugin = result.metadata.get("plugin_id") or result.metadata.get(
                    "engine", "unknown"
                )
                success_record: dict[str, object] = {
                    "plugin_id": success_plugin,
                    "error": "",
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "ok": True,
                }
                attempts.append(success_record)
                preview_path = self._save_preview(
                    row.id,
                    row.source_filename,
                    result.markdown,
                )
                row.status = "ready"
                row.parser_mode = result.metadata.get("engine", "unknown")
                row.preview_text_path = preview_path
                row.chunk_count = max(1, len(result.markdown) // 1000)
                row.last_error_code = None
                row.last_error_message = None
                # Persist the full attempts list (fallback failures +
                # final success) so the doc panel shows the complete
                # parse history.
                row.parser_attempts_json = _json.dumps(attempts)
                await self._ds.update(row)
                task.processed_items += 1
                await self._ds.update_import_task(task)

        if task_errors:
            task.errors_json = _json.dumps(task_errors)
        task.status = "completed"
        await self._ds.update_import_task(task)

    async def get_import_task(self, task_id: str) -> ImportTaskResult:
        row = await self._ds.get_import_task(task_id)
        if not row:
            raise ImportTaskNotFound()
        return _task_to_result(row)

    # ── Search ────────────────────────────────────────────────────────

    async def search_docs(
        self,
        workspace_id: str,
        query: str,
        top_k: int = 5,
        folder_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
    ) -> list[DocSearchHit]:
        scope_ids = await self.resolve_doc_scope(workspace_id)
        if not scope_ids:
            return []

        if folder_ids:
            folder_doc_ids: set[str] = set()
            for fid in folder_ids:
                folder_doc_ids.update(await self._ds.list_doc_ids_by_folder_subtree(fid))
            scope_ids = [d for d in scope_ids if d in folder_doc_ids]

        if document_ids:
            scope_ids = [d for d in scope_ids if d in set(document_ids)]

        if not scope_ids:
            return []

        doc_paths: dict[str, str] = {}
        doc_names: dict[str, str] = {}
        for did in scope_ids:
            row = await self._ds.get_by_id(did)
            if row:
                doc_names[did] = row.source_filename
                if row.preview_text_path:
                    doc_paths[did] = row.preview_text_path

        from valuz_agent.integrations.docs_embedded import EmbeddedDocsRuntime

        if isinstance(self._docs_rt, EmbeddedDocsRuntime):
            results = self._docs_rt.search_sync(
                query,
                scope_ids,
                top_k,
                doc_paths=doc_paths or None,
            )
        else:
            results = await self._docs_rt.search(query, scope_ids, top_k)

        return [
            DocSearchHit(
                document_id=r.document_id,
                filename=doc_names.get(r.document_id, ""),
                score=r.score,
                snippet=r.snippet,
                page_ref=r.page_ref,
                chunk_ref=r.chunk_ref,
            )
            for r in results
        ]

    # ── Project binding (D3 minimal cover) ────────────────────────────

    async def list_project_bindings(self, workspace_id: str) -> list[ProjectKbBindingRow]:
        return await self._ds.list_bindings(workspace_id)

    async def update_project_bindings(
        self,
        workspace_id: str,
        bindings: list[dict[str, str]],
    ) -> list[ProjectKbBindingRow]:
        rows = [
            ProjectKbBindingRow(
                workspace_id=workspace_id,
                binding_kind=b["binding_kind"],
                target_id=b["target_id"],
            )
            for b in bindings
        ]
        minimized = await self._minimize_bindings(rows)
        await self._ds.set_bindings(workspace_id, minimized)
        self._bus.publish("workspace.bindings.changed", workspace_id=workspace_id)
        return await self._ds.list_bindings(workspace_id)

    async def remove_project_bindings(self, workspace_id: str) -> None:
        await self._ds.remove_all_bindings(workspace_id)

    async def count_project_bindings(self, workspace_id: str) -> int:
        return await self._ds.count_bindings(workspace_id)

    # ── Scope resolution ──────────────────────────────────────────────

    async def resolve_doc_scope(self, workspace_id: str) -> list[str]:
        bindings = await self._ds.list_bindings(workspace_id)
        doc_ids: set[str] = set()
        for b in bindings:
            if b.binding_kind == "kb":
                doc_ids.update(await self._ds.list_doc_ids_by_kb(b.target_id, status="ready"))
            elif b.binding_kind == "folder":
                doc_ids.update(
                    await self._ds.list_doc_ids_by_folder_subtree(b.target_id, status="ready")
                )
            elif b.binding_kind == "document":
                row = await self._ds.get_by_id(b.target_id)
                if row and row.status == "ready":
                    doc_ids.add(b.target_id)
        return list(doc_ids)

    async def resolve_preview_paths(self, doc_ids: list[str]) -> dict[str, str]:
        """Return {doc_id: preview_text_path} for docs that have a preview file."""
        result: dict[str, str] = {}
        for did in doc_ids:
            row = await self._ds.get_by_id(did)
            if row and row.preview_text_path and Path(row.preview_text_path).exists():
                result[did] = row.preview_text_path
        return result

    async def build_doc_scope_tree(self, workspace_id: str) -> DocScopeTreeView:
        bindings = await self._ds.list_bindings(workspace_id)
        if not bindings:
            return DocScopeTreeView(knowledge_bases=(), total_documents=0)

        kb_ids: set[str] = set()
        bound_folder_ids: set[str] = set()
        bound_doc_ids: set[str] = set()

        for b in bindings:
            if b.binding_kind == "kb":
                kb_ids.add(b.target_id)
            elif b.binding_kind == "folder":
                folder = await self._ds.get_folder(b.target_id)
                if folder:
                    kb_ids.add(folder.kb_id)
                    bound_folder_ids.add(b.target_id)
            elif b.binding_kind == "document":
                doc = await self._ds.get_by_id(b.target_id)
                if doc:
                    kb_ids.add(doc.kb_id)
                    bound_doc_ids.add(b.target_id)

        total = 0
        kb_nodes: list[DocScopeBoundNode] = []
        for kb_id in kb_ids:
            kb = await self._ds.get_kb(kb_id)
            if not kb:
                continue
            is_kb_bound = any(b.binding_kind == "kb" and b.target_id == kb_id for b in bindings)

            folder_children = await self._build_scope_folder_children(
                kb_id,
                parent_folder_id=None,
                is_kb_bound=is_kb_bound,
                bound_folder_ids=bound_folder_ids,
                bound_doc_ids=bound_doc_ids,
            )
            doc_count = len(await self._ds.list_doc_ids_by_kb(kb_id, status="ready"))
            total += doc_count
            kb_nodes.append(
                DocScopeBoundNode(
                    kind="kb",
                    id=kb_id,
                    name=kb.name,
                    path=None,
                    bound_directly=is_kb_bound,
                    document_count=doc_count,
                    children=tuple(folder_children),
                )
            )

        return DocScopeTreeView(
            knowledge_bases=tuple(kb_nodes),
            total_documents=total,
        )

    async def _build_scope_folder_children(
        self,
        kb_id: str,
        parent_folder_id: str | None,
        is_kb_bound: bool,
        bound_folder_ids: set[str],
        bound_doc_ids: set[str],
    ) -> list[DocScopeBoundNode]:
        nodes: list[DocScopeBoundNode] = []
        folders = await self._ds.list_folders(kb_id, parent_folder_id=parent_folder_id)
        for f in folders:
            in_scope = is_kb_bound or f.id in bound_folder_ids
            if not in_scope:
                has_bound_descendant = any(
                    did in bound_doc_ids
                    for did in await self._ds.list_doc_ids_by_folder_subtree(f.id)
                )
                if not has_bound_descendant:
                    continue
            sub_children = await self._build_scope_folder_children(
                kb_id,
                parent_folder_id=f.id,
                is_kb_bound=is_kb_bound or f.id in bound_folder_ids,
                bound_folder_ids=bound_folder_ids,
                bound_doc_ids=bound_doc_ids,
            )
            doc_count = await self._ds.count_docs_in_folder_subtree(kb_id, f.id)
            nodes.append(
                DocScopeBoundNode(
                    kind="folder",
                    id=f.id,
                    name=f.display_name,
                    path=f.relative_path,
                    bound_directly=f.id in bound_folder_ids,
                    document_count=doc_count,
                    children=tuple(sub_children),
                )
            )

        if parent_folder_id is not None:
            docs = await self._ds.list_documents(kb_id=kb_id, kb_folder_id=parent_folder_id)
        else:
            all_docs = await self._ds.list_documents(kb_id=kb_id)
            all_folder_ids = {f.id for f in await self._ds.list_all_folders(kb_id)}
            docs = [
                d for d in all_docs if not d.kb_folder_id or d.kb_folder_id not in all_folder_ids
            ]

        for d in docs:
            if d.status != "ready":
                continue
            in_scope = is_kb_bound or d.id in bound_doc_ids
            if not in_scope:
                continue
            nodes.append(
                DocScopeBoundNode(
                    kind="document",
                    id=d.id,
                    name=d.source_filename,
                    path=d.relative_path,
                    bound_directly=d.id in bound_doc_ids,
                    document_count=0,
                )
            )

        return nodes

    # ── Health ────────────────────────────────────────────────────────

    async def get_docs_health(self) -> dict[str, object]:
        rows = await self._ds.list_documents()
        total = len(rows)
        ready = sum(1 for r in rows if r.status == "ready")
        processing = sum(1 for r in rows if r.status in ("processing", "queued"))
        failed = sum(1 for r in rows if r.status == "failed")
        missing = sum(1 for r in rows if r.status == "missing")
        return {
            "status": "healthy" if total > 0 else "unavailable",
            "total_documents": total,
            "ready_count": ready,
            "processing_count": processing,
            "failed_count": failed,
            "missing_count": missing,
        }

    # ── Internal helpers ──────────────────────────────────────────────

    async def _minimize_bindings(
        self, bindings: list[ProjectKbBindingRow]
    ) -> list[ProjectKbBindingRow]:
        kb_bindings = [b for b in bindings if b.binding_kind == "kb"]
        kb_bound_ids = {b.target_id for b in kb_bindings}

        folder_bindings: list[ProjectKbBindingRow] = []
        for b in bindings:
            if b.binding_kind == "folder" and not await self._folder_covered_by_kb(
                b.target_id, kb_bound_ids
            ):
                folder_bindings.append(b)

        covered_folder_ids: set[str] = set()
        for fb in folder_bindings:
            desc = await self._ds.list_descendant_folder_ids(
                await self._get_folder_kb_id(fb.target_id), fb.target_id
            )
            covered_folder_ids.add(fb.target_id)
            covered_folder_ids.update(desc)

        doc_bindings: list[ProjectKbBindingRow] = []
        for b in bindings:
            if (
                b.binding_kind == "document"
                and not await self._doc_covered_by_kb(b.target_id, kb_bound_ids)
                and not await self._doc_covered_by_folder(b.target_id, covered_folder_ids)
            ):
                doc_bindings.append(b)

        return kb_bindings + folder_bindings + doc_bindings

    async def _folder_covered_by_kb(self, folder_id: str, kb_ids: set[str]) -> bool:
        folder = await self._ds.get_folder(folder_id)
        return folder is not None and folder.kb_id in kb_ids

    async def _doc_covered_by_kb(self, doc_id: str, kb_ids: set[str]) -> bool:
        doc = await self._ds.get_by_id(doc_id)
        return doc is not None and doc.kb_id in kb_ids

    async def _doc_covered_by_folder(self, doc_id: str, folder_ids: set[str]) -> bool:
        doc = await self._ds.get_by_id(doc_id)
        if not doc:
            return False
        return doc.kb_folder_id in folder_ids

    async def _get_folder_kb_id(self, folder_id: str) -> str:
        folder = await self._ds.get_folder(folder_id)
        return folder.kb_id if folder else ""

    async def _update_folder_counts(self, kb_id: str) -> None:
        folders = await self._ds.list_all_folders(kb_id)
        for folder in folders:
            direct = len(await self._ds.list_documents(kb_id=kb_id, kb_folder_id=folder.id))
            descendant = len(await self._ds.list_doc_ids_by_folder_subtree(folder.id))
            if folder.document_count != direct or folder.descendant_document_count != descendant:
                folder.document_count = direct
                folder.descendant_document_count = descendant
                await self._ds.update_folder(folder)

    async def _kb_to_list_item(self, row: KnowledgeBaseRow) -> KbListItem:
        doc_count = await self._ds.count_docs_by_kb(row.id)
        docs = await self._ds.list_documents(kb_id=row.id)
        has_processing = await self._ds.has_active_kb_task(row.id) or any(
            d.status in ("queued", "processing", "indexing") for d in docs
        )
        has_missing = any(d.status == "missing" for d in docs)
        if has_processing:
            status = "has_processing"
        elif has_missing:
            status = "has_missing"
        else:
            status = "all_ready"
        return KbListItem(
            id=row.id,
            name=row.name,
            root_path=row.root_path,
            parser_routing=row.parser_routing,
            document_count=doc_count,
            status=status,
            created_at=row.created_at,
        )

    async def _kb_to_detail(self, row: KnowledgeBaseRow) -> KbDetail:
        item = await self._kb_to_list_item(row)
        return KbDetail(
            id=item.id,
            name=item.name,
            root_path=item.root_path,
            parser_routing=item.parser_routing,
            document_count=item.document_count,
            status=item.status,
            created_at=item.created_at,
            auto_discover=row.auto_discover,
            last_full_scan_at=row.last_full_scan_at,
        )

    def _save_preview(self, doc_id: str, source_filename: str, markdown: str) -> str:
        from valuz_agent.integrations.docs_embedded import sanitize_preview_filename

        preview_dir = getattr(self._docs_rt, "preview_dir", None)
        if preview_dir is None:
            preview_dir = Path.home() / ".valuz" / "app" / "docs" / "preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        safe_name = sanitize_preview_filename(source_filename)
        preview_path = preview_dir / safe_name
        if preview_path.exists():
            preview_path = preview_dir / f"{doc_id}_{safe_name}"
        preview_path.write_text(markdown, encoding="utf-8")
        return str(preview_path)

    def _parser_parse_sync(self, file_path: str) -> ParseResult:
        # Fast path: any backend that exposes ``parse_sync`` is invoked
        # directly without an event loop. ``LightLocalParser`` is the
        # production case; in-memory test fakes (``FakeParser``) also
        # implement ``parse_sync`` and benefit from the same shortcut
        # — using ``hasattr`` keeps the service decoupled from concrete
        # classes (was an ``isinstance(LightLocalParser)`` check before,
        # which forced every alternative parser through the async path).
        if hasattr(self._parser, "parse_sync"):
            return self._parser.parse_sync(file_path)

        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            return ParseResult(
                markdown="*Cannot run async parser in sync context*",
                metadata={"error": "async_not_supported"},
            )
        return asyncio.run(self._parser.parse(file_path))

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.docs.models import (
    DocumentImportTaskRow,
    DocumentRecordRow,
    KbFolderRow,
    KnowledgeBaseRow,
    ProjectKbBindingRow,
)

logger = logging.getLogger(__name__)


class DocumentDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── KnowledgeBase ─────────────────────────────────────────────────

    async def create_kb(self, row: KnowledgeBaseRow) -> KnowledgeBaseRow:
        self._db.add(row)
        await async_commit_with_retry(self._db, where="docs.create_kb")
        return row

    async def get_kb(self, kb_id: str) -> KnowledgeBaseRow | None:
        return await self._db.get(KnowledgeBaseRow, kb_id)

    async def list_kbs(self) -> list[KnowledgeBaseRow]:
        return list(
            (
                await self._db.execute(
                    select(KnowledgeBaseRow).order_by(KnowledgeBaseRow.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def update_kb(self, row: KnowledgeBaseRow) -> KnowledgeBaseRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="docs.update_kb")
        return row

    async def delete_kb(self, kb_id: str) -> None:
        await self._db.execute(
            delete(ProjectKbBindingRow).where(
                ProjectKbBindingRow.binding_kind == "kb",
                ProjectKbBindingRow.target_id == kb_id,
            )
        )

        folder_ids = [
            r[0]
            for r in (await self._db.execute(select(KbFolderRow.id).filter_by(kb_id=kb_id))).all()
        ]
        if folder_ids:
            await self._db.execute(
                delete(ProjectKbBindingRow).where(
                    ProjectKbBindingRow.binding_kind == "folder",
                    ProjectKbBindingRow.target_id.in_(folder_ids),
                )
            )

        doc_ids = [
            r[0]
            for r in (
                await self._db.execute(select(DocumentRecordRow.id).filter_by(kb_id=kb_id))
            ).all()
        ]
        if doc_ids:
            await self._db.execute(
                delete(ProjectKbBindingRow).where(
                    ProjectKbBindingRow.binding_kind == "document",
                    ProjectKbBindingRow.target_id.in_(doc_ids),
                )
            )

        await self._db.execute(delete(DocumentRecordRow).where(DocumentRecordRow.kb_id == kb_id))
        await self._db.execute(delete(KbFolderRow).where(KbFolderRow.kb_id == kb_id))
        await self._db.execute(delete(KnowledgeBaseRow).where(KnowledgeBaseRow.id == kb_id))
        await async_commit_with_retry(self._db, where="docs.delete_kb")

    async def kb_root_path_exists(self, root_path: str, exclude_kb_id: str | None = None) -> bool:
        stmt = select(KnowledgeBaseRow).filter_by(root_path=root_path)
        if exclude_kb_id:
            stmt = stmt.where(KnowledgeBaseRow.id != exclude_kb_id)
        return (await self._db.execute(stmt)).scalars().first() is not None

    # ── KbFolder ──────────────────────────────────────────────────────

    async def create_folder(self, row: KbFolderRow) -> KbFolderRow:
        self._db.add(row)
        await async_commit_with_retry(self._db, where="docs.create_folder")
        return row

    async def get_folder(self, folder_id: str) -> KbFolderRow | None:
        return await self._db.get(KbFolderRow, folder_id)

    async def get_folder_by_path(self, kb_id: str, relative_path: str) -> KbFolderRow | None:
        return (
            (
                await self._db.execute(
                    select(KbFolderRow).filter_by(kb_id=kb_id, relative_path=relative_path)
                )
            )
            .scalars()
            .first()
        )

    async def list_folders(
        self, kb_id: str, parent_folder_id: str | None = None
    ) -> list[KbFolderRow]:
        stmt = select(KbFolderRow).filter_by(kb_id=kb_id)
        if parent_folder_id is not None:
            stmt = stmt.filter_by(parent_folder_id=parent_folder_id)
        else:
            stmt = stmt.where(KbFolderRow.parent_folder_id.is_(None))
        stmt = stmt.order_by(KbFolderRow.display_name)
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_all_folders(self, kb_id: str) -> list[KbFolderRow]:
        return list(
            (
                await self._db.execute(
                    select(KbFolderRow).filter_by(kb_id=kb_id).order_by(KbFolderRow.relative_path)
                )
            )
            .scalars()
            .all()
        )

    async def update_folder(self, row: KbFolderRow) -> KbFolderRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="docs.update_folder")
        return row

    async def list_descendant_folder_ids(self, kb_id: str, folder_id: str) -> list[str]:
        folder = await self.get_folder(folder_id)
        if not folder:
            return []
        prefix = folder.relative_path + "/"
        rows = (
            await self._db.execute(
                select(KbFolderRow.id).where(
                    KbFolderRow.kb_id == kb_id, KbFolderRow.relative_path.startswith(prefix)
                )
            )
        ).all()
        return [r[0] for r in rows]

    # ── DocumentRecord ────────────────────────────────────────────────

    async def list_documents(
        self,
        query: str | None = None,
        status: str | None = None,
        kb_id: str | None = None,
        kb_folder_id: str | None = None,
    ) -> list[DocumentRecordRow]:
        stmt = select(DocumentRecordRow)
        if kb_id:
            stmt = stmt.filter_by(kb_id=kb_id)
        if kb_folder_id:
            stmt = stmt.filter_by(kb_folder_id=kb_folder_id)
        if status:
            stmt = stmt.filter_by(status=status)
        if query:
            stmt = stmt.where(DocumentRecordRow.source_filename.ilike(f"%{query}%"))
        stmt = stmt.order_by(DocumentRecordRow.created_at.desc())
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_by_id(self, doc_id: str) -> DocumentRecordRow | None:
        return await self._db.get(DocumentRecordRow, doc_id)

    async def get_by_relative_path(
        self, kb_id: str, relative_path: str
    ) -> DocumentRecordRow | None:
        return (
            (
                await self._db.execute(
                    select(DocumentRecordRow).filter_by(kb_id=kb_id, relative_path=relative_path)
                )
            )
            .scalars()
            .first()
        )

    async def create(self, row: DocumentRecordRow) -> DocumentRecordRow:
        self._db.add(row)
        await async_commit_with_retry(self._db, where="docs.create")
        return row

    async def update(self, row: DocumentRecordRow) -> DocumentRecordRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="docs.update")
        return row

    async def delete(self, doc_id: str) -> None:
        await self._db.execute(
            delete(ProjectKbBindingRow).where(
                ProjectKbBindingRow.binding_kind == "document",
                ProjectKbBindingRow.target_id == doc_id,
            )
        )
        await self._db.execute(delete(DocumentRecordRow).where(DocumentRecordRow.id == doc_id))
        await async_commit_with_retry(self._db, where="docs.delete")

    async def list_doc_ids_by_kb(self, kb_id: str, status: str = "ready") -> list[str]:
        rows = (
            await self._db.execute(
                select(DocumentRecordRow.id).filter_by(kb_id=kb_id, status=status)
            )
        ).all()
        return [r[0] for r in rows]

    async def list_doc_ids_by_folder_subtree(
        self, folder_id: str, status: str = "ready"
    ) -> list[str]:
        folder = await self.get_folder(folder_id)
        if not folder:
            return []
        prefix = folder.relative_path + "/"
        rows = (
            await self._db.execute(
                select(DocumentRecordRow.id).where(
                    DocumentRecordRow.kb_id == folder.kb_id,
                    DocumentRecordRow.status == status,
                    (
                        (DocumentRecordRow.kb_folder_id == folder_id)
                        | DocumentRecordRow.relative_path.startswith(prefix)
                    ),
                )
            )
        ).all()
        return [r[0] for r in rows]

    async def list_docs_by_ids(
        self,
        doc_ids: list[str],
        status: str | None = None,
    ) -> list[DocumentRecordRow]:
        if not doc_ids:
            return []
        stmt = select(DocumentRecordRow).where(DocumentRecordRow.id.in_(doc_ids))
        if status:
            stmt = stmt.filter_by(status=status)
        return list((await self._db.execute(stmt)).scalars().all())

    async def count_docs_by_kb(self, kb_id: str) -> int:
        return (
            await self._db.execute(select(func.count(DocumentRecordRow.id)).filter_by(kb_id=kb_id))
        ).scalar() or 0

    async def count_docs_in_folder_subtree(self, kb_id: str, folder_id: str) -> int:
        folder = await self.get_folder(folder_id)
        if not folder:
            return 0
        prefix = folder.relative_path + "/"
        return (
            await self._db.execute(
                select(func.count(DocumentRecordRow.id)).where(
                    DocumentRecordRow.kb_id == kb_id,
                    (
                        (DocumentRecordRow.kb_folder_id == folder_id)
                        | DocumentRecordRow.relative_path.startswith(prefix)
                    ),
                )
            )
        ).scalar() or 0

    # ── DocumentImportTask ────────────────────────────────────────────

    async def create_import_task(self, row: DocumentImportTaskRow) -> DocumentImportTaskRow:
        self._db.add(row)
        await async_commit_with_retry(self._db, where="docs.create_import_task")
        return row

    async def get_import_task(self, task_id: str) -> DocumentImportTaskRow | None:
        return await self._db.get(DocumentImportTaskRow, task_id)

    async def update_import_task(self, row: DocumentImportTaskRow) -> DocumentImportTaskRow:
        await self._db.merge(row)
        await async_commit_with_retry(self._db, where="docs.update_import_task")
        return row

    async def has_active_kb_task(self, kb_id: str) -> bool:
        return (
            await self._db.execute(
                select(DocumentImportTaskRow.id).where(
                    DocumentImportTaskRow.kb_id == kb_id,
                    DocumentImportTaskRow.status.in_(("queued", "processing")),
                )
            )
        ).scalars().first() is not None

    # ── ProjectKbBinding ──────────────────────────────────────────────

    async def list_bindings(self, workspace_id: str) -> list[ProjectKbBindingRow]:
        return list(
            (
                await self._db.execute(
                    select(ProjectKbBindingRow).filter_by(workspace_id=workspace_id)
                )
            )
            .scalars()
            .all()
        )

    async def set_bindings(self, workspace_id: str, bindings: list[ProjectKbBindingRow]) -> None:
        await self._db.execute(
            delete(ProjectKbBindingRow).where(ProjectKbBindingRow.workspace_id == workspace_id)
        )
        now = now_ms()
        for b in bindings:
            if b.created_at is None:
                b.created_at = now
            self._db.add(b)
        await async_commit_with_retry(self._db, where="docs.set_bindings")

    async def count_bindings(self, workspace_id: str) -> int:
        return (
            await self._db.execute(
                select(func.count())
                .select_from(ProjectKbBindingRow)
                .filter_by(workspace_id=workspace_id)
            )
        ).scalar() or 0

    async def remove_all_bindings(self, workspace_id: str) -> None:
        await self._db.execute(
            delete(ProjectKbBindingRow).where(ProjectKbBindingRow.workspace_id == workspace_id)
        )
        await async_commit_with_retry(self._db, where="docs.remove_all_bindings")

    async def delete_bindings_by_kb(self, kb_id: str) -> None:
        await self._db.execute(
            delete(ProjectKbBindingRow).where(
                ProjectKbBindingRow.binding_kind == "kb",
                ProjectKbBindingRow.target_id == kb_id,
            )
        )
        await async_commit_with_retry(self._db, where="docs.delete_bindings_by_kb")

    async def delete_bindings_by_folder(self, folder_id: str) -> None:
        await self._db.execute(
            delete(ProjectKbBindingRow).where(
                ProjectKbBindingRow.binding_kind == "folder",
                ProjectKbBindingRow.target_id == folder_id,
            )
        )
        await async_commit_with_retry(self._db, where="docs.delete_bindings_by_folder")

    async def delete_bindings_by_document(self, document_id: str) -> None:
        await self._db.execute(
            delete(ProjectKbBindingRow).where(
                ProjectKbBindingRow.binding_kind == "document",
                ProjectKbBindingRow.target_id == document_id,
            )
        )
        await async_commit_with_retry(self._db, where="docs.delete_bindings_by_document")

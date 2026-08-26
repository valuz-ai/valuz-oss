"""Owner-scoped persistence for document research artifacts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import async_commit_with_retry
from valuz_agent.modules.citations.models import DocumentSummaryArtifactRow


class DocumentResearchDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_summary(
        self,
        user_id: str,
        *,
        document_id: str,
        document_version: str,
        profile: str,
        prompt_revision: str,
        policy_revision: str,
    ) -> DocumentSummaryArtifactRow | None:
        return (
            (
                await self._db.execute(
                    select(DocumentSummaryArtifactRow).where(
                        DocumentSummaryArtifactRow.user_id == user_id,
                        DocumentSummaryArtifactRow.document_id == document_id,
                        DocumentSummaryArtifactRow.document_version == document_version,
                        DocumentSummaryArtifactRow.profile == profile,
                        DocumentSummaryArtifactRow.prompt_revision == prompt_revision,
                        DocumentSummaryArtifactRow.policy_revision == policy_revision,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def latest_summary(
        self,
        user_id: str,
        *,
        document_id: str,
        profile: str,
    ) -> DocumentSummaryArtifactRow | None:
        return (
            (
                await self._db.execute(
                    select(DocumentSummaryArtifactRow)
                    .where(
                        DocumentSummaryArtifactRow.user_id == user_id,
                        DocumentSummaryArtifactRow.document_id == document_id,
                        DocumentSummaryArtifactRow.profile == profile,
                    )
                    .order_by(DocumentSummaryArtifactRow.updated_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

    async def save_summary(
        self,
        user_id: str,
        row: DocumentSummaryArtifactRow,
    ) -> DocumentSummaryArtifactRow:
        row.user_id = user_id
        merged = await self._db.merge(row)
        await async_commit_with_retry(self._db, where="document_research.save_summary")
        return merged

    async def claim_new_summary(
        self,
        user_id: str,
        row: DocumentSummaryArtifactRow,
    ) -> tuple[DocumentSummaryArtifactRow, bool]:
        """Atomically claim one immutable summary cache key.

        Summary generation happens after the pending row is committed, so two
        application workers can both observe an empty cache before either
        inserts it.  The unique cache index is the cross-process arbiter: the
        winner owns generation and the loser rolls back its failed INSERT,
        reloads the winner's row, and polls that canonical artifact instead of
        starting a duplicate model run.
        """

        row.user_id = user_id
        self._db.add(row)
        try:
            await async_commit_with_retry(
                self._db,
                where="document_research.claim_new_summary",
            )
        except IntegrityError:
            await self._db.rollback()
            existing = await self.get_summary(
                user_id,
                document_id=row.document_id,
                document_version=row.document_version,
                profile=row.profile,
                prompt_revision=row.prompt_revision,
                policy_revision=row.policy_revision,
            )
            if existing is None:
                # A unique-conflict without a visible canonical row means the
                # transaction did not fail on this cache identity.  Preserve
                # the original database error rather than hiding corruption.
                raise
            return existing, False
        return row, True


__all__ = ["DocumentResearchDatastore"]

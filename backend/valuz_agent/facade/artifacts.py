"""Exact, owner-scoped Artifact metadata reads; no file access or implicit export.

Revision IDs are the canonical version tokens. Metadata availability is not a
claim that file bytes are reachable: content preview uses the normal file API
and its permissions. An archived identity or missing file keeps its provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from valuz_agent.modules.artifacts.models import (
    ArtifactContentRow,
    ArtifactHeadRow,
    ArtifactRevisionRow,
    ArtifactRow,
)


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    id: str
    project_id: str
    worktree: str
    kind: str
    display_name: str
    archived_at: int | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class ArtifactRevisionRef:
    id: str
    artifact_id: str
    parent_revision_id: str | None
    version_no: int
    source_session_id: str | None
    source_tool_call_id: str | None
    file_name: str
    file_format: str | None
    mime_type: str | None
    schema_version: str | None
    renderer_version: str | None
    content_hash: str
    status: str
    created_at: int
    storage_kind: str
    byte_size: int


class ArtifactLibrary:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    def _query(self, user_id: str) -> Select[Any]:
        artifact = ArtifactRow.__table__
        revision = ArtifactRevisionRow.__table__
        head = ArtifactHeadRow.__table__
        content = ArtifactContentRow.__table__
        head_revision = ArtifactRevisionRow.__table__.alias("head_revision")
        return (
            select(
                *(
                    artifact.c[field.name].label(f"artifact_{field.name}")
                    for field in fields(ArtifactRef)
                ),
                *(
                    revision.c[field.name].label(f"revision_{field.name}")
                    for field in fields(ArtifactRevisionRef)
                    if field.name not in {"storage_kind", "byte_size"}
                ),
                content.c.storage_kind.label("revision_storage_kind"),
                content.c.byte_size.label("revision_byte_size"),
            )
            .select_from(artifact)
            .join(revision, revision.c.artifact_id == artifact.c.id)
            .join(head, head.c.artifact_id == artifact.c.id)
            .join(head_revision, head_revision.c.id == head.c.revision_id)
            .join(content, content.c.id == revision.c.content_id)
            .where(
                artifact.c.user_id == user_id,
                revision.c.user_id == user_id,
                head.c.user_id == user_id,
                head_revision.c.user_id == user_id,
                head_revision.c.artifact_id == artifact.c.id,
                head_revision.c.version_no == head.c.version_no,
                content.c.user_id == user_id,
                content.c.content_hash == revision.c.content_hash,
                # A persisted but not yet published generation is not a version.
                or_(
                    revision.c.version_no < head.c.version_no,
                    and_(
                        revision.c.version_no == head.c.version_no,
                        revision.c.id == head.c.revision_id,
                    ),
                ),
            )
            .limit(1)
        )

    async def _read(self, statement: Select[Any]) -> tuple[ArtifactRef, ArtifactRevisionRef] | None:
        with self._db.no_autoflush:
            row = (await self._db.execute(statement)).mappings().one_or_none()
        if row is None:
            return None
        return (
            ArtifactRef(
                **{field.name: row[f"artifact_{field.name}"] for field in fields(ArtifactRef)}
            ),
            ArtifactRevisionRef(
                **{
                    field.name: row[f"revision_{field.name}"]
                    for field in fields(ArtifactRevisionRef)
                }
            ),
        )

    async def get(
        self, user_id: str, artifact_id: str, *, revision_id: str | None = None
    ) -> tuple[ArtifactRef, ArtifactRevisionRef] | None:
        """Read current head or an exact immutable revision belonging to this identity.

        Artifact name/archive status are current metadata even for an old
        revision. Missing or unrelated revisions never fall back to head.
        """
        _require_identity(user_id, artifact_id)
        if revision_id is not None:
            _require_identity(user_id, revision_id)
        statement = self._query(user_id).where(ArtifactRow.id == artifact_id)
        return await self._read(
            statement.where(
                ArtifactRevisionRow.id == revision_id
                if revision_id is not None
                else ArtifactRevisionRow.id == ArtifactHeadRow.revision_id
            )
        )

    async def get_revision(
        self, user_id: str, revision_id: str
    ) -> tuple[ArtifactRef, ArtifactRevisionRef] | None:
        """Read one published revision and its owned parent identity by revision ID."""
        _require_identity(user_id, revision_id)
        return await self._read(self._query(user_id).where(ArtifactRevisionRow.id == revision_id))


def _require_identity(user_id: str, resource_id: str) -> None:
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id must be non-empty")
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ValueError("resource_id must be non-empty")


__all__ = ["ArtifactLibrary", "ArtifactRef", "ArtifactRevisionRef"]

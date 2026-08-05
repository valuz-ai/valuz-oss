"""Artifact reads that are not scoped to one conversation.

``GET /v1/sessions/{id}/artifacts`` answers "what did this conversation
produce". These answer the two questions it cannot:

* what does this workspace currently hold — the deliverables themselves, at
  their latest version, however many sessions it took to get there;
* how did one of them get here — its version history.

Both are read-only. Deliveries happen through the ``deliver_artifacts`` tool,
where the owner boundary, the snapshot and the head compare-and-set live.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope
from valuz_agent.modules.artifacts.models import REVISION_STATUS_READY
from valuz_agent.modules.files.uri import build_valuz_file_uri

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


class RevisionItem(BaseModel):
    id: str
    version_no: int
    file_name: str
    file_path: str
    # Empty when the version's bytes are gone (a removed worktree, or a legacy
    # row whose file was already missing when it was migrated). The client shows
    # the version but cannot open it.
    ref: str
    file_size: int
    mime_type: str | None = None
    status: str
    source_session_id: str | None = None
    created_at: int


class ArtifactSummary(BaseModel):
    id: str
    display_name: str
    kind: str
    version_no: int
    updated_at: int
    current: RevisionItem


class ArtifactListResponse(BaseModel):
    items: list[ArtifactSummary]
    total: int


class RevisionListResponse(BaseModel):
    artifact_id: str
    display_name: str
    items: list[RevisionItem]


def _revision_item(revision, content) -> RevisionItem:  # type: ignore[no-untyped-def]
    path = revision.abs_path or ""
    openable = revision.status == REVISION_STATUS_READY and bool(path)
    return RevisionItem(
        id=revision.id,
        version_no=revision.version_no,
        file_name=revision.file_name,
        file_path=path,
        ref=build_valuz_file_uri(path) if openable else "",
        file_size=content.byte_size if content else 0,
        mime_type=content.mime_type if content else None,
        status=revision.status,
        source_session_id=revision.source_session_id,
        created_at=revision.created_at,
    )


@router.get("")
async def list_scope_artifacts(
    project_id: str = Query(..., description="Project whose deliverables to list."),
    worktree: str = Query(
        "",
        description=(
            "Worktree name, or empty for the project's own working directory. "
            "Deliverables are scoped to the directory they were delivered into."
        ),
    ),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> ArtifactListResponse:
    """The workspace's deliverables, most recently updated first.

    Scoped by worktree as well as project: a worktree is an independent line of
    work, and its deliverables live in its own directory. Listing them together
    would mix two sets of files that cannot both be open at once.
    """
    scope = Scope(user_id=user_id, project_id=project_id, worktree=worktree)
    ds = ArtifactDatastore(db)
    rows = await ds.list_scope_heads(scope, limit=limit)
    total = await ds.count_scope_artifacts(scope)
    # One query for every content, not one per row: this list is up to ``limit``
    # long and is fetched every time the panel opens.
    contents = await ds.get_contents(user_id, [rev.content_id for _a, _h, rev in rows])

    items: list[ArtifactSummary] = []
    for artifact, head, revision in rows:
        content = contents.get(revision.content_id)
        items.append(
            ArtifactSummary(
                id=artifact.id,
                display_name=artifact.display_name,
                kind=artifact.kind,
                version_no=head.version_no,
                updated_at=head.updated_at,
                current=_revision_item(revision, content),
            )
        )
    return ArtifactListResponse(items=items, total=total)


@router.get("/{artifact_id}/revisions")
async def list_artifact_revisions(
    artifact_id: str,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> RevisionListResponse:
    """One deliverable's history, oldest first.

    Every version keeps its own path, so the client can open any of them — this
    is what "delivering does not overwrite" means in practice.
    """
    ds = ArtifactDatastore(db)
    artifact = await ds.get_artifact(user_id, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id!r} not found")

    revisions = await ds.list_revisions(user_id, artifact_id)
    contents = await ds.get_contents(user_id, [rev.content_id for rev in revisions])
    items = [_revision_item(rev, contents.get(rev.content_id)) for rev in revisions]
    return RevisionListResponse(
        artifact_id=artifact.id, display_name=artifact.display_name, items=items
    )

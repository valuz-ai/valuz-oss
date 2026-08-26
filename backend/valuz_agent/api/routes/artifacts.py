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

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope
from valuz_agent.modules.artifacts.models import (
    REVISION_STATUS_READY,
    ArtifactBindingRow,
)
from valuz_agent.modules.artifacts.service import BindStatus, bind_host_revision
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
        mime_type=revision.mime_type,
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


class HostRevisionItem(RevisionItem):
    """A revision in a host's cross-artifact version history."""

    artifact_id: str


class HostRevisionListResponse(BaseModel):
    items: list[HostRevisionItem]


@router.get("/hosts/revisions")
async def list_host_revisions(
    host_type: str,
    host_id: str,
    slot: str = "main",
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> HostRevisionListResponse:
    """A host slot's FULL version history, oldest first — across artifacts.

    Generations for a host share one stable document name but may live in
    different artifacts (a regeneration from another conversation's scope
    forked a lineage before deliveries followed the binding). The per-artifact
    listing hides every lineage except the bound one; this one shows them all,
    so a version switcher can offer pages the user made from any conversation.
    Entries carry their ``artifact_id`` — version numbers restart per artifact
    and only order entries within one.
    """
    from valuz_agent.modules.genui.tools import host_document_file_name

    file_name = host_document_file_name(host_type, host_id, slot or "main")
    ds = ArtifactDatastore(db)
    revisions = await ds.list_revisions_by_file_name(user_id, file_name)
    contents = await ds.get_contents(user_id, [rev.content_id for rev in revisions])
    items = [
        HostRevisionItem(
            artifact_id=rev.artifact_id,
            **_revision_item(rev, contents.get(rev.content_id)).model_dump(),
        )
        for rev in revisions
    ]
    return HostRevisionListResponse(items=items)


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


class HostBinding(BaseModel):
    """What a host slot is showing, with enough of the revision to render it."""

    host_type: str
    host_id: str
    slot: str
    artifact_id: str
    artifact_revision_id: str
    version_no: int
    file_name: str
    mime_type: str | None = None
    #: The bound document itself — inline when the delivery carried it, read
    #: back from the revision's file otherwise. ``None`` only when the bytes
    #: are genuinely gone (deleted worktree) or too large to be a document.
    content: str | None = None
    ref: str = ""
    updated_at: int


class BindRequest(BaseModel):
    host_type: str
    host_id: str
    slot: str = "main"
    artifact_revision_id: str
    #: What the caller believed was bound. A mismatch is a 409 rather than a
    #: silent overwrite — somebody else adopted something since it last looked.
    expected_revision_id: str | None = None
    #: Deliberate override after the user has been shown the conflict.
    force: bool = False


#: Upper bound for reading a file-stored document back into the binding
#: response. Generously above anything the delivery path accepts; a file
#: bigger than this is not a renderable document and null is honest.
_BOUND_DOCUMENT_MAX_BYTES = 4 * 1024 * 1024


def _read_bound_document(path: str) -> str | None:
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > _BOUND_DOCUMENT_MAX_BYTES:
            return None
        return p.read_text(encoding="utf-8")
    except OSError:
        return None
    except UnicodeDecodeError:
        return None


async def _binding_response(
    ds: ArtifactDatastore, user_id: str, binding: ArtifactBindingRow
) -> HostBinding:
    revision = await ds.get_revision(user_id, binding.artifact_revision_id)
    content = (
        await ds.get_content(user_id, revision.content_id) if revision is not None else None
    )
    path = (revision.abs_path or "") if revision is not None else ""
    inline = content.content_inline if content is not None else None
    if inline is None and path:
        # File-stored content. A revision does not have to arrive through the
        # content-delivery path to get bound — an agent that recovers a page by
        # writing the document to a file and delivering THAT ends up here, and
        # serving ``null`` for it blanked the whole workbench (content intact
        # on disk the entire time). The host renders from the document either
        # way, so read it back; the delivery cap bounds how big it can be.
        inline = await asyncio.to_thread(_read_bound_document, path)
    return HostBinding(
        host_type=binding.host_type,
        host_id=binding.host_id,
        slot=binding.slot,
        artifact_id=binding.artifact_id,
        artifact_revision_id=binding.artifact_revision_id,
        version_no=revision.version_no if revision is not None else 0,
        file_name=revision.file_name if revision is not None else "",
        mime_type=revision.mime_type if revision is not None else None,
        content=inline,
        ref=build_valuz_file_uri(path) if path else "",
        updated_at=binding.updated_at,
    )


class RevisionContent(BaseModel):
    revision_id: str
    version_no: int
    #: The document, inline or read back from the revision's file — ``None``
    #: only when the bytes are genuinely gone.
    content: str | None = None


@router.get("/revisions/{revision_id}/content")
async def get_revision_content(
    revision_id: str,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> RevisionContent:
    """One version's document, without touching any binding.

    This is what lets a host BROWSE a version — look at it before deciding to
    use it. Reads exactly like the binding response: inline copy first, the
    revision's file otherwise.
    """
    ds = ArtifactDatastore(db)
    revision = await ds.get_revision(user_id, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail=f"Revision {revision_id!r} not found")
    content = await ds.get_content(user_id, revision.content_id)
    inline = content.content_inline if content is not None else None
    if inline is None and revision.abs_path:
        inline = await asyncio.to_thread(_read_bound_document, revision.abs_path)
    return RevisionContent(
        revision_id=revision.id, version_no=revision.version_no, content=inline
    )


@router.get("/bindings")
async def get_host_binding(
    host_type: str = Query(..., description="Host family, e.g. finance.company-research."),
    host_id: str = Query(..., description="Which host of that family."),
    slot: str = Query("main", description="Which surface on that host."),
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> HostBinding:
    """What this host slot is currently showing."""
    ds = ArtifactDatastore(db)
    binding = await ds.get_binding(user_id, host_type, host_id, slot)
    if binding is None:
        raise HTTPException(status_code=404, detail="no binding for this host slot")
    return await _binding_response(ds, user_id, binding)


@router.put("/bindings")
async def bind_host_slot(
    body: BindRequest,
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> HostBinding:
    """Adopt one exact revision on a host slot (409 on a stale expectation)."""
    result = await bind_host_revision(
        db,
        user_id,
        host_type=body.host_type,
        host_id=body.host_id,
        slot=body.slot,
        artifact_revision_id=body.artifact_revision_id,
        expected_revision_id=body.expected_revision_id,
        check_expected=not body.force,
    )
    if result.status is BindStatus.UNKNOWN_REVISION:
        raise HTTPException(status_code=404, detail="no such revision")
    if result.status is BindStatus.STALE:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "this host slot moved since you last read it",
                "current_revision_id": result.current_revision_id,
            },
        )
    await db.commit()
    ds = ArtifactDatastore(db)
    binding = await ds.get_binding(user_id, body.host_type, body.host_id, body.slot)
    if binding is None:  # pragma: no cover — just written above
        raise HTTPException(status_code=500, detail="binding vanished after write")
    return await _binding_response(ds, user_id, binding)


@router.delete("/bindings", status_code=204)
async def unbind_host_slot(
    host_type: str = Query(...),
    host_id: str = Query(...),
    slot: str = Query("main"),
    db: AsyncSession = Depends(get_async_session),
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Clear a host slot. The revisions themselves are untouched."""
    await ArtifactDatastore(db).delete_binding(user_id, host_type, host_id, slot)
    await db.commit()

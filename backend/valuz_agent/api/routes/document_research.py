"""Document Research Workspace API.

The routes expose versioned citation-bearing summaries and owner-scoped,
single-document child sessions. Answers themselves continue to flow through
the ordinary sessions/messages/SSE APIs.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.api.deps import (
    get_current_user_id,
    get_document_service,
    get_session_service,
)
from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.citations.datastore import DocumentResearchDatastore
from valuz_agent.modules.citations.research import DocumentResearchService
from valuz_agent.modules.docs.errors import DocumentNotFound
from valuz_agent.modules.docs.service import DocumentLibraryService
from valuz_agent.modules.sessions.service import SessionService

router = APIRouter(prefix="/v1/document-research", tags=["document-research"])


class ResearchSessionRequest(BaseModel):
    document_id: str
    origin_session_id: str | None = None
    origin_message_id: str | None = None


class GenerateSummaryRequest(BaseModel):
    profile: Literal["brief", "detailed"] = "brief"
    origin_session_id: str | None = None
    origin_message_id: str | None = None
    force: bool = False


class ShareResearchMessageRequest(BaseModel):
    research_session_id: str
    source_message_id: str | None = None


def _service(
    *,
    documents: DocumentLibraryService,
    sessions: SessionService,
    db: AsyncSession,
) -> DocumentResearchService:
    return DocumentResearchService(
        documents=documents,
        sessions=sessions,
        datastore=DocumentResearchDatastore(db),
    )


@router.post("/sessions", status_code=201)
async def create_or_restore_research_session(
    body: ResearchSessionRequest,
    user_id: str = Depends(get_current_user_id),
    documents: DocumentLibraryService = Depends(get_document_service),
    sessions: SessionService = Depends(get_session_service),
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    try:
        value = await _service(
            documents=documents,
            sessions=sessions,
            db=db,
        ).get_or_create_session(
            user_id,
            document_id=body.document_id,
            origin_session_id=body.origin_session_id,
            origin_message_id=body.origin_message_id,
        )
    except DocumentNotFound as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return asdict(value)


@router.get("/documents/{document_id}/summary")
async def get_document_summary(
    document_id: str,
    profile: Literal["brief", "detailed"] = Query("brief"),
    user_id: str = Depends(get_current_user_id),
    documents: DocumentLibraryService = Depends(get_document_service),
    sessions: SessionService = Depends(get_session_service),
    db: AsyncSession = Depends(get_async_session),
) -> dict | None:
    try:
        value = await _service(
            documents=documents,
            sessions=sessions,
            db=db,
        ).get_summary(
            user_id,
            document_id=document_id,
            profile=profile,
        )
    except DocumentNotFound as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    return asdict(value) if value is not None else None


@router.post("/documents/{document_id}/summary")
async def generate_document_summary(
    document_id: str,
    body: GenerateSummaryRequest,
    user_id: str = Depends(get_current_user_id),
    documents: DocumentLibraryService = Depends(get_document_service),
    sessions: SessionService = Depends(get_session_service),
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    try:
        value = await _service(
            documents=documents,
            sessions=sessions,
            db=db,
        ).generate_summary(
            user_id,
            document_id=document_id,
            profile=body.profile,
            origin_session_id=body.origin_session_id,
            origin_message_id=body.origin_message_id,
            force=body.force,
        )
    except DocumentNotFound as exc:
        raise HTTPException(status_code=404, detail="Document not found") from exc
    return asdict(value)


@router.post("/share")
async def share_research_message_to_origin(
    body: ShareResearchMessageRequest,
    user_id: str = Depends(get_current_user_id),
    documents: DocumentLibraryService = Depends(get_document_service),
    sessions: SessionService = Depends(get_session_service),
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    try:
        value = await _service(
            documents=documents,
            sessions=sessions,
            db=db,
        ).share_to_origin(
            user_id,
            research_session_id=body.research_session_id,
            source_message_id=body.source_message_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return asdict(value)

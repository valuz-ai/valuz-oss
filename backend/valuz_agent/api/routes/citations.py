"""Owner-scoped citation resolution.

The request carries identities only. Source metadata, locator, and document
identity are reloaded from the canonical message row so a client cannot swap a
visible citation onto another owner's file or submit a forged path/bbox.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from valuz_agent.adapters import kernel_client
from valuz_agent.api.deps import get_current_user_id, get_document_service
from valuz_agent.modules.citations.resolver import (
    LocalCitationDocumentResolver,
    stable_external_document_id,
)
from valuz_agent.modules.docs.errors import DocumentNotFound
from valuz_agent.modules.docs.service import DocumentLibraryService
from valuz_agent.ports.citation_documents import get_citation_document_resolver

router = APIRouter(prefix="/v1/citations", tags=["citations"])


class ResolveCitationRequest(BaseModel):
    session_id: str
    message_id: str
    citation_id: str


class ResolveCitationResponse(BaseModel):
    document: dict[str, Any] | None
    effective_locator: dict[str, Any] | None = None
    status: Literal["ready", "stale", "missing", "forbidden", "degraded"]
    fallback_reason: str | None = None
    canonical_url: str | None = None


def _canonical_citation(message: Any, citation_id: str) -> dict[str, Any]:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    bundle = metadata.get("citation_bundle")
    if not isinstance(bundle, dict) or bundle.get("version") != 1:
        raise HTTPException(status_code=404, detail="Citation not found")
    citations = bundle.get("citations")
    if not isinstance(citations, list):
        raise HTTPException(status_code=404, detail="Citation not found")
    matches = [
        item
        for item in citations
        if isinstance(item, dict) and item.get("citationId") == citation_id
    ]
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail="Citation not found")
    return matches[0]


@router.post("/resolve", response_model=ResolveCitationResponse)
async def resolve_citation(
    body: ResolveCitationRequest,
    user_id: str = Depends(get_current_user_id),
    document_service: DocumentLibraryService = Depends(get_document_service),
) -> ResolveCitationResponse:
    message = await kernel_client.get_message(user_id, body.message_id)
    if message is None or message.session_id != body.session_id:
        # Deliberately identical for another owner, wrong session, and missing
        # message: the resolver must not become an existence oracle.
        raise HTTPException(status_code=404, detail="Citation not found")

    citation = _canonical_citation(message, body.citation_id)
    source = citation.get("source")
    if not isinstance(source, dict):
        raise HTTPException(status_code=404, detail="Citation not found")
    evidence = citation.get("evidence")
    if not isinstance(evidence, dict):
        raise HTTPException(status_code=404, detail="Citation not found")
    locator = citation.get("locator")
    if locator is not None and not isinstance(locator, dict):
        locator = None

    edition_resolver = get_citation_document_resolver()
    local_resolver = LocalCitationDocumentResolver(document_service)
    try:
        resolved = (
            await edition_resolver.resolve(
                owner_user_id=user_id,
                source=source,
                evidence=evidence,
                locator=locator,
            )
            if edition_resolver is not None
            else None
        )
    except DocumentNotFound as exc:
        raise HTTPException(status_code=404, detail="Citation document unavailable") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Citation document forbidden") from exc

    canonical_url = (
        source.get("canonicalUrl") if isinstance(source.get("canonicalUrl"), str) else None
    )
    if resolved is None and not source.get("documentId"):
        if not canonical_url:
            return ResolveCitationResponse(
                document=None,
                status="degraded",
                fallback_reason="citation_has_no_readable_document",
            )
        return ResolveCitationResponse(
            document={
                "id": stable_external_document_id(source),
                "title": source.get("title") or canonical_url,
                "source": {
                    "name": source.get("organization")
                    or source.get("author")
                    or source.get("providerId")
                    or "",
                },
                "render": {"kind": "external", "url": canonical_url},
                "originalUrl": canonical_url,
            },
            effective_locator=locator,
            status="degraded",
            fallback_reason="external_reader_unavailable",
            canonical_url=canonical_url,
        )
    if resolved is None:
        resolved = await local_resolver.resolve(
            owner_user_id=user_id,
            source=source,
            evidence=evidence,
            locator=locator,
        )

    return ResolveCitationResponse(
        document=resolved.document or None,
        effective_locator=resolved.effective_locator,
        status=resolved.status,  # type: ignore[arg-type]
        fallback_reason=resolved.fallback_reason,
        canonical_url=canonical_url,
    )


__all__ = ["router"]

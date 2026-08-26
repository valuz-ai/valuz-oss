"""OSS citation resolver backed by the owner-scoped document library."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from valuz_agent.modules.docs.service import DocumentLibraryService
from valuz_agent.ports.citation_documents import ResolvedCitationDocument
from valuz_agent.ports.file_address import get_file_address_resolver

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _normalized_hash(value: str | None) -> str | None:
    if not value:
        return None
    return value.removeprefix("sha256:")


def _markdown_chunks(markdown: str) -> list[dict[str, Any]]:
    """Produce stable, safe reading blocks from parser preview markdown.

    IDs derive from source line ranges instead of list positions, so inserting
    an unrelated block does not renumber all preceding anchors. Rich markdown
    stays plain text here; document HTML/PDF renderers still receive the source
    file and use this list only as a locator fallback.
    """

    chunks: list[dict[str, Any]] = []
    paragraph: list[str] = []
    paragraph_start = 0

    def flush(end_line: int) -> None:
        nonlocal paragraph, paragraph_start
        text = "\n".join(paragraph).strip()
        if text:
            chunks.append(
                {
                    "id": f"line:{paragraph_start}-{end_line}",
                    "type": "paragraph",
                    "text": text,
                }
            )
        paragraph = []
        paragraph_start = 0

    for line_number, raw in enumerate(markdown.splitlines(), start=1):
        stripped = raw.strip()
        heading = _HEADING_RE.match(stripped)
        if heading:
            flush(line_number - 1)
            chunks.append(
                {
                    "id": f"line:{line_number}",
                    "type": "heading",
                    "text": heading.group(2).strip(),
                }
            )
            continue
        if not stripped:
            flush(line_number - 1)
            continue
        if not paragraph:
            paragraph_start = line_number
        paragraph.append(raw)
    flush(len(markdown.splitlines()))
    return chunks


def _stale_locator(locator: dict[str, Any] | None) -> dict[str, Any] | None:
    """Discard version-sensitive fast paths while retaining safe fallbacks."""

    if not locator:
        return None
    kind = locator.get("kind")
    if kind == "pdf":
        return {key: value for key, value in locator.items() if key in {"kind", "page", "quote"}}
    if kind == "html":
        return {
            key: value
            for key, value in locator.items()
            if key in {"kind", "chunkId", "elementId", "quote"}
        }
    if kind == "chunk" and locator.get("quote"):
        return {"kind": "chunk", "quote": locator["quote"]}
    return locator


class LocalCitationDocumentResolver:
    """Resolve citations that point at the built-in document library."""

    def __init__(self, service: DocumentLibraryService) -> None:
        self._service = service

    async def resolve(
        self,
        *,
        owner_user_id: str,
        source: dict[str, Any],
        locator: dict[str, Any] | None,
        evidence: dict[str, Any] | None = None,
    ) -> ResolvedCitationDocument:
        del evidence
        document_id = source.get("documentId")
        if not isinstance(document_id, str) or not document_id:
            return ResolvedCitationDocument(
                document={},
                effective_locator=None,
                status="degraded",
                fallback_reason="citation_has_no_document",
            )

        detail = await self._service.get_document(owner_user_id, document_id)
        preview = await self._service.get_document_preview(owner_user_id, document_id)
        chunks = _markdown_chunks(preview)

        stored_hash = _normalized_hash(detail.content_hash)
        cited_hash = _normalized_hash(
            source.get("documentVersion")
            if isinstance(source.get("documentVersion"), str)
            else None
        )
        stale = bool(stored_hash and cited_hash and stored_hash != cited_hash)
        effective_locator = _stale_locator(locator) if stale else locator

        mime_type = detail.mime_type or "text/plain"
        render: dict[str, Any]
        if detail.source_path and mime_type in {"application/pdf", "text/html"}:
            address = await get_file_address_resolver().to_address(
                owner_user_id=owner_user_id,
                abs_path=Path(detail.source_path).resolve(),
            )
            render = {
                "kind": "file",
                "mimeType": mime_type,
                "address": {
                    "kind": address.kind,
                    "absPath": str(address.abs_path) if address.abs_path is not None else None,
                    "url": address.url,
                    "expiresAt": address.expires_at,
                },
            }
        else:
            render = {"kind": "chunks", "chunks": chunks}

        title = detail.title or detail.filename
        document = {
            "id": detail.id,
            "title": title,
            "source": {
                "name": source.get("organization")
                or source.get("author")
                or source.get("providerId")
                or "",
            },
            "render": render,
            # Locator indexes are independent of the primary render format.
            # PDF/HTML readers use these safe text blocks for quote fallback.
            "chunks": chunks,
            "documentVersion": f"sha256:{stored_hash}" if stored_hash else None,
            "originalUrl": source.get("canonicalUrl"),
        }
        return ResolvedCitationDocument(
            document=document,
            effective_locator=effective_locator,
            status="stale" if stale else "ready",
            fallback_reason="document_version_changed" if stale else None,
        )


def stable_external_document_id(source: dict[str, Any]) -> str:
    identity = "|".join(
        str(source.get(key) or "") for key in ("providerId", "sourceId", "canonicalUrl")
    )
    return f"external:{hashlib.sha256(identity.encode()).hexdigest()[:24]}"


__all__ = ["LocalCitationDocumentResolver", "stable_external_document_id"]

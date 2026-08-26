"""Port for resolving a persisted citation into a currently readable document.

The route calling this port owns authentication. It loads the canonical
``CitationRef`` from the owner-scoped message row and passes the explicit
``owner_user_id`` here; clients never submit a trusted source, path, or
locator. Implementations may exchange a stable document identity for a
short-lived storage URL, but must never return connector credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ResolvedCitationDocument:
    document: dict[str, Any]
    effective_locator: dict[str, Any] | None
    status: str  # ready | stale | missing | forbidden | degraded
    fallback_reason: str | None = None


class CitationDocumentResolverPort(Protocol):
    async def resolve(
        self,
        *,
        owner_user_id: str,
        source: dict[str, Any],
        evidence: dict[str, Any],
        locator: dict[str, Any] | None,
    ) -> ResolvedCitationDocument | None:
        """Resolve one canonical citation for the current owner.

        Return ``None`` when this resolver does not own the source provider.
        The host then falls through to the OSS document-library resolver.
        """
        ...


def get_citation_document_resolver() -> CitationDocumentResolverPort | None:
    from valuz_agent.ports.extensions import ext

    return ext.citation_document_resolver


def set_citation_document_resolver(port: CitationDocumentResolverPort | None) -> None:
    """Bind an edition resolver. ``None`` restores the OSS document-library path."""
    from valuz_agent.ports.extensions import ext

    ext.citation_document_resolver = port


__all__ = [
    "CitationDocumentResolverPort",
    "ResolvedCitationDocument",
    "get_citation_document_resolver",
    "set_citation_document_resolver",
]

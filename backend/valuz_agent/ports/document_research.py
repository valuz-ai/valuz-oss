"""Edition seam for document research over connector-owned documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ResolvedResearchDocument:
    """Minimal document contract needed by summary and locked Q&A sessions."""

    id: str
    title: str
    filename: str
    document_version: str
    provider_id: str
    mcp_server_names: tuple[str, ...]
    source_category: str | None = None
    organization: str | None = None


@dataclass(frozen=True)
class ResolvedResearchSummary:
    content: str
    citation_bundle: dict[str, Any]
    quality_policy: dict[str, Any] | None = None


class DocumentResearchProviderPort(Protocol):
    async def resolve_document(
        self,
        *,
        owner_user_id: str,
        document_id: str,
    ) -> ResolvedResearchDocument | None:
        """Return a connector-owned document, or ``None`` when not owned."""

    async def get_summary(
        self,
        *,
        owner_user_id: str,
        document: ResolvedResearchDocument,
        profile: str,
    ) -> ResolvedResearchSummary | None:
        """Return the provider's summary for a resolved document."""


__all__ = [
    "DocumentResearchProviderPort",
    "ResolvedResearchDocument",
    "ResolvedResearchSummary",
]

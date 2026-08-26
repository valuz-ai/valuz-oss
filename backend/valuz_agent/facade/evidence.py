"""Stable, read-only access to canonical message Citation Evidence.

Long-lived domains may promote Evidence from a persisted assistant message,
but must never trust Source/Locator payloads supplied by a browser or model.
This facade re-reads the owner-scoped kernel message and seals the exact
CitationBundle entry for a later proposal/confirm flow.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from valuz_agent.adapters import kernel_client


@dataclass(frozen=True, slots=True)
class MessageEvidenceRef:
    message_id: str
    citation_id: str
    assistant_segment_index: int | None = None
    claim_id: str | None = None
    claim_location: dict[str, Any] | None = None
    provenance_region_id: str | None = None
    expected_citation_hash: str | None = None


@dataclass(frozen=True, slots=True)
class SealedMessageEvidence:
    message_id: str
    citation_id: str
    source: dict[str, Any]
    evidence: dict[str, Any]
    locator: dict[str, Any] | None
    annotations: dict[str, Any]
    citation_hash: str
    bundle_version: int

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "evidence": self.evidence,
            "locator": self.locator,
            "annotations": self.annotations,
        }


def canonical_citation_hash(citation: dict[str, Any]) -> str:
    payload = {
        "source": citation.get("source") if isinstance(citation.get("source"), dict) else {},
        "evidence": (
            citation.get("evidence") if isinstance(citation.get("evidence"), dict) else {}
        ),
        "locator": citation.get("locator") if isinstance(citation.get("locator"), dict) else None,
        "annotations": (
            citation.get("annotations") if isinstance(citation.get("annotations"), dict) else {}
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class MessageEvidenceLibrary:
    async def seal(
        self,
        owner_user_id: str,
        reference: MessageEvidenceRef,
    ) -> SealedMessageEvidence:
        message = await kernel_client.get_message(owner_user_id, reference.message_id)
        if message is None:
            raise LookupError("message_evidence_message_not_found")
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        bundle = metadata.get("citation_bundle")
        if not isinstance(bundle, dict) or bundle.get("version") != 1:
            raise LookupError("message_evidence_bundle_not_found")
        citations = bundle.get("citations")
        if not isinstance(citations, list):
            raise LookupError("message_evidence_bundle_invalid")
        citation = next(
            (
                item
                for item in citations
                if isinstance(item, dict) and item.get("citationId") == reference.citation_id
            ),
            None,
        )
        if citation is None:
            raise LookupError("message_evidence_citation_not_found")
        citation_hash = canonical_citation_hash(citation)
        if (
            reference.expected_citation_hash is not None
            and reference.expected_citation_hash != citation_hash
        ):
            raise ValueError("message_evidence_citation_hash_changed")
        source = citation.get("source")
        evidence = citation.get("evidence")
        if not isinstance(source, dict) or not isinstance(evidence, dict):
            raise ValueError("message_evidence_citation_incomplete")
        locator = citation.get("locator")
        annotations = citation.get("annotations")
        return SealedMessageEvidence(
            message_id=reference.message_id,
            citation_id=reference.citation_id,
            source=dict(source),
            evidence=dict(evidence),
            locator=dict(locator) if isinstance(locator, dict) else None,
            annotations=dict(annotations) if isinstance(annotations, dict) else {},
            citation_hash=citation_hash,
            bundle_version=1,
        )


__all__ = [
    "MessageEvidenceLibrary",
    "MessageEvidenceRef",
    "SealedMessageEvidence",
    "canonical_citation_hash",
]

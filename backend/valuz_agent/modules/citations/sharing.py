"""Safe citation serialization for public or cross-principal sharing.

Seeing an assistant message never grants access to its source document.  A
caller that creates a public artifact must explicitly pass the source ids its
server-side sharing policy has approved; every other citation keeps its stable
display identity but loses the snapshot, locator, document identity, and URL.
"""

from __future__ import annotations

import copy
from typing import Any


def serialize_citation_bundle_for_share(
    bundle: dict[str, Any],
    *,
    public_source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return a redacted copy suitable for an untrusted viewer.

    ``public_source_ids`` must come from an authorization policy, not from the
    requesting client.  The fail-closed default redacts every source.
    """

    result = copy.deepcopy(bundle)
    if result.get("version") != 1 or not isinstance(result.get("citations"), list):
        return {"version": 1, "citations": []}
    allowed = public_source_ids or set()
    for citation in result["citations"]:
        if not isinstance(citation, dict):
            continue
        source = citation.get("source")
        source = source if isinstance(source, dict) else {}
        source_id = source.get("sourceId")
        if isinstance(source_id, str) and source_id in allowed:
            # A resolver still reauthorizes on click.  This branch merely
            # permits a policy-approved public snapshot in the share payload.
            continue
        citation_id = citation.get("citationId")
        captured_at = _captured_at(citation.get("evidence"))
        citation["source"] = {
            "sourceId": f"restricted:{citation_id or 'source'}",
            "providerId": "restricted",
            "sourceType": "document",
            "title": "Restricted source",
            "retrievedAt": captured_at,
        }
        citation["evidence"] = {
            "kind": "text",
            "quote": "",
            "snippet": "",
            "capturedAt": captured_at,
        }
        citation.pop("locator", None)
        citation["resolutionStatus"] = "forbidden"
        annotations = citation.get("annotations")
        annotations = annotations if isinstance(annotations, dict) else {}
        annotations.pop("provenance", None)
        annotations["sharing"] = {"restricted": True}
        citation["annotations"] = annotations

    integrity = result.get("integrity")
    if isinstance(integrity, dict):
        integrity["missingLocatorCitationIds"] = [
            citation.get("citationId")
            for citation in result["citations"]
            if isinstance(citation, dict)
            and citation.get("resolutionStatus") == "forbidden"
            and isinstance(citation.get("citationId"), str)
        ]
    return result


def _captured_at(value: Any) -> str:
    if isinstance(value, dict):
        captured_at = value.get("capturedAt") or value.get("calculatedAt")
        if isinstance(captured_at, str) and captured_at:
            return captured_at
    return "1970-01-01T00:00:00Z"


__all__ = ["serialize_citation_bundle_for_share"]

from __future__ import annotations

import json

from valuz_agent.modules.citations.sharing import (
    serialize_citation_bundle_for_share,
)


def _bundle() -> dict:
    return {
        "version": 1,
        "citations": [
            {
                "citationId": "cit_private",
                "source": {
                    "sourceId": "private-doc",
                    "providerId": "documents",
                    "documentId": "doc-secret",
                    "documentVersion": "sha256:secret",
                    "sourceType": "document",
                    "title": "Confidential filing",
                    "organization": "Secret Corp",
                    "retrievedAt": "2026-07-31T00:00:00Z",
                    "canonicalUrl": "https://private.invalid/signed?token=secret",
                },
                "evidence": {
                    "kind": "text",
                    "quote": "Confidential revenue was 120.",
                    "snippet": "Confidential revenue was 120.",
                    "capturedAt": "2026-07-31T00:00:00Z",
                },
                "locator": {
                    "kind": "pdf",
                    "page": 17,
                    "quote": {"exact": "Confidential revenue was 120."},
                },
                "annotations": {
                    "provenance": {"toolName": "document_fetch"},
                    "quality": {"status": "passed"},
                },
            }
        ],
        "integrity": {
            "status": "passed",
            "unknownCitationIds": [],
            "unusedCitationIds": [],
            "missingLocatorCitationIds": [],
            "repairAttempts": 0,
            "policyRevision": "citation-v1",
        },
    }


def test_public_share_redacts_private_snapshot_locator_and_url() -> None:
    original = _bundle()
    shared = serialize_citation_bundle_for_share(original)
    serialized = json.dumps(shared)

    assert shared["citations"][0]["citationId"] == "cit_private"
    assert shared["citations"][0]["resolutionStatus"] == "forbidden"
    assert shared["citations"][0]["annotations"] == {
        "quality": {"status": "passed"},
        "sharing": {"restricted": True},
    }
    assert "locator" not in shared["citations"][0]
    assert shared["integrity"]["missingLocatorCitationIds"] == ["cit_private"]
    for secret in (
        "doc-secret",
        "sha256:secret",
        "token=secret",
        "Confidential revenue",
        "Secret Corp",
    ):
        assert secret not in serialized
    assert original == _bundle()


def test_explicitly_public_source_keeps_snapshot_but_does_not_grant_access() -> None:
    original = _bundle()
    shared = serialize_citation_bundle_for_share(
        original,
        public_source_ids={"private-doc"},
    )

    assert shared == original

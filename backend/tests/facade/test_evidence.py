from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from valuz_agent.facade.evidence import (
    MessageEvidenceLibrary,
    MessageEvidenceRef,
    canonical_citation_hash,
)


def _citation() -> dict:
    return {
        "citationId": "cit-1",
        "source": {
            "providerId": "docs",
            "sourceId": "doc-1",
            "sourceType": "document",
            "documentVersion": "sha256:v1",
            "title": "Annual report",
        },
        "evidence": {
            "kind": "text",
            "quote": "Revenue increased 20%.",
            "capturedAt": "2026-08-20T00:00:00Z",
        },
        "locator": {"kind": "pdf", "page": 12, "quote": "Revenue increased"},
        "annotations": {"temporal": {"period": "FY2026"}},
    }


async def test_seal_reloads_canonical_owner_scoped_message_metadata() -> None:
    citation = _citation()
    message = SimpleNamespace(metadata={"citation_bundle": {"version": 1, "citations": [citation]}})
    with patch(
        "valuz_agent.facade.evidence.kernel_client.get_message",
        AsyncMock(return_value=message),
    ) as get_message:
        sealed = await MessageEvidenceLibrary().seal(
            "owner-1",
            MessageEvidenceRef(message_id="m1", citation_id="cit-1"),
        )

    get_message.assert_awaited_once_with("owner-1", "m1")
    assert sealed.source["sourceId"] == "doc-1"
    assert sealed.locator == citation["locator"]
    assert sealed.citation_hash == canonical_citation_hash(citation)


async def test_seal_rejects_client_hash_when_message_citation_changed() -> None:
    message = SimpleNamespace(
        metadata={"citation_bundle": {"version": 1, "citations": [_citation()]}}
    )
    with patch(
        "valuz_agent.facade.evidence.kernel_client.get_message",
        AsyncMock(return_value=message),
    ):
        with pytest.raises(ValueError, match="citation_hash_changed"):
            await MessageEvidenceLibrary().seal(
                "owner-1",
                MessageEvidenceRef(
                    message_id="m1",
                    citation_id="cit-1",
                    expected_citation_hash="sha256:old",
                ),
            )


async def test_seal_does_not_accept_missing_message_or_client_source_payload() -> None:
    with patch(
        "valuz_agent.facade.evidence.kernel_client.get_message",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(LookupError, match="message_not_found"):
            await MessageEvidenceLibrary().seal(
                "another-owner",
                MessageEvidenceRef(message_id="m1", citation_id="cit-1"),
            )

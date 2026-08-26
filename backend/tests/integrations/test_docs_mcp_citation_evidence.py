from __future__ import annotations

from types import SimpleNamespace

from valuz_agent.integrations.docs_mcp_server import (
    _locator_for_search_hit,
    _search_hit_to_result,
)


def _hit(**overrides: object) -> SimpleNamespace:
    values = {
        "document_id": "doc-1",
        "filename": "Report.pdf",
        "score": 3.0,
        "snippet": "Revenue increased by 12%.",
        "page_ref": "page 12",
        "chunk_ref": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _detail(**overrides: object) -> SimpleNamespace:
    values = {
        "title": "Annual Report",
        "filename": "Report.pdf",
        "content_hash": "abc123",
        "mime_type": "application/pdf",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_doc_search_result_includes_trusted_pdf_evidence_envelope() -> None:
    result = _search_hit_to_result(
        _hit(),
        detail=_detail(),
        session_id="session-1",
        captured_at="2026-07-30T10:00:00Z",
    )

    evidence = result["_valuz_evidence"]
    assert evidence["evidenceHandle"].startswith("ev_")
    assert evidence["source"] == {
        "sourceId": "doc-1",
        "providerId": "valuz-project-docs",
        "documentId": "doc-1",
        "sourceType": "document",
        "title": "Annual Report",
        "retrievedAt": "2026-07-30T10:00:00Z",
        "documentVersion": "sha256:abc123",
        "mimeType": "application/pdf",
    }
    assert evidence["evidence"]["quote"] == "Revenue increased by 12%."
    assert evidence["locator"] == {
        "kind": "pdf",
        "page": 12,
        "quote": {"exact": "Revenue increased by 12%."},
    }


def test_evidence_handle_is_deterministic_for_same_snapshot() -> None:
    kwargs = {
        "detail": _detail(),
        "session_id": "session-1",
        "captured_at": "2026-07-30T10:00:00Z",
    }

    first = _search_hit_to_result(_hit(), **kwargs)
    second = _search_hit_to_result(_hit(), **kwargs)

    assert first["_valuz_evidence"]["evidenceHandle"] == second["_valuz_evidence"]["evidenceHandle"]


def test_missing_detail_preserves_legacy_result_without_untrusted_envelope() -> None:
    result = _search_hit_to_result(
        _hit(),
        detail=None,
        session_id="session-1",
        captured_at="2026-07-30T10:00:00Z",
    )

    assert result["document_id"] == "doc-1"
    assert "_valuz_evidence" not in result


def test_locator_falls_back_to_chunk_or_html_quote() -> None:
    assert _locator_for_search_hit(
        _hit(page_ref=None, chunk_ref="chunk-9"),
        mime_type="text/plain",
    ) == {
        "kind": "chunk",
        "chunkId": "chunk-9",
        "quote": {"exact": "Revenue increased by 12%."},
    }
    assert _locator_for_search_hit(
        _hit(page_ref=None, chunk_ref=None),
        mime_type="text/html",
    ) == {
        "kind": "html",
        "quote": {"exact": "Revenue increased by 12%."},
    }

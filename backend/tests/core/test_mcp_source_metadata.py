from __future__ import annotations

import hashlib
import json

from mcp.types import CallToolResult, TextContent
from src.core.citation import (
    EvidenceRegistry,
    compact_citation_tool_content,
    private_citation_tool_content,
)
from src.core.mcp_source_metadata import (
    MCP_SOURCE_METADATA_KEY,
    MCP_SOURCE_TRANSPORT_KEY,
    adapt_mcp_source_result,
    unwrap_mcp_source_transport,
    wrap_mcp_result_metadata_for_transport,
)


def _hash(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _descriptor(
    target: object,
    *,
    tool_name: str,
    resources: list[dict],
) -> dict:
    return {
        "version": 1,
        "provider": {
            "id": "reportify",
            "name": "Reportify",
            "adapterRevision": "reportify-mcp-source-v1",
        },
        "operation": {"toolName": tool_name, "operationId": f"{tool_name}_op"},
        "result": {
            "target": "structuredContent",
            "hash": {"algorithm": "sha256", "value": _hash(target)},
            "capturedAt": "2026-08-03T00:00:00Z",
        },
        "resources": resources,
    }


def test_transport_preserves_meta_and_restores_original_structured_content() -> None:
    structured = {"data": [{"ticker": "600519", "revenue": 1}]}
    descriptor = _descriptor(structured, tool_name="income_statement", resources=[])
    result = CallToolResult(
        content=[TextContent(type="text", text=json.dumps(structured))],
        structuredContent=structured,
        _meta={MCP_SOURCE_METADATA_KEY: descriptor, "trace": "kept-on-server"},
    )

    wrapped = wrap_mcp_result_metadata_for_transport(result, server_name="reportify")

    assert wrapped.content == result.content
    assert wrapped.structuredContent is not None
    assert MCP_SOURCE_TRANSPORT_KEY in wrapped.structuredContent
    artifact = {"structured_content": wrapped.structuredContent, "existing": True}
    actual_descriptor, actual_structured, restored = unwrap_mcp_source_transport(artifact)
    assert actual_descriptor == descriptor
    assert actual_structured == structured
    assert restored == {"structured_content": structured, "existing": True}


def test_discovery_metadata_stays_non_citable_retrieval_input() -> None:
    payload = {
        "docs": [
            {
                "doc_id": f"doc-{index}",
                "title": f"Annual report {index}",
                "summary": f"Revenue was {index}00.",
                "url": f"https://example.com/doc-{index}",
            }
            for index in range(20)
        ]
    }
    descriptor = _descriptor(
        payload,
        tool_name="reports_search",
        resources=[
            {
                "resourceId": "reports-search-results",
                "kind": "document-discovery",
                "authority": "discovery-only",
                "rootPointer": "",
                "itemsPointer": "/docs",
                "mapping": {
                    "sourceId": "/doc_id",
                    "title": "/title",
                    "summary": "/summary",
                    "url": "/url",
                    "fetch": {
                        "toolName": "document_fetch",
                        "argumentFromItem": {"doc_id": "/doc_id"},
                    },
                },
            }
        ],
    )

    adapted = adapt_mcp_source_result(
        "unused model block",
        tool_name="reports_search",
        descriptor=descriptor,
        structured_content=payload,
    )

    assert adapted is not None
    assert adapted.discovery_only is True
    assert adapted.citable is False
    assert adapted.evidence_count == 0
    assert adapted.model_content == payload
    assert "_valuz_evidence" not in adapted.model_content


def test_document_chunks_create_direct_evidence_with_pdf_locator() -> None:
    payload = {
        "doc_id": "doc-annual-report",
        "title": "2024 Annual Report",
        "url": "https://example.com/report.pdf",
        "document_version": "v-2024",
        "chunks": [
            {
                "id": "chunk-8",
                "content": "Revenue was CNY 174.144 billion in 2024.",
                "metadata": {
                    "document_page": 8,
                    "bbox": {"left": 60, "top": 80, "right": 300, "bottom": 160},
                    "width": 600,
                    "height": 800,
                },
            }
        ],
        "total_chunks": 10,
        "chunk_offset": 0,
        "next_chunk_offset": 1,
    }
    descriptor = _descriptor(
        payload,
        tool_name="document_fetch",
        resources=[
            {
                "resourceId": "document-fetch-chunks",
                "kind": "document-chunks",
                "authority": "authoritative",
                "rootPointer": "",
                "document": {
                    "scope": "resource",
                    "sourceId": "/doc_id",
                    "documentId": "/doc_id",
                    "documentVersion": "/document_version",
                    "title": "/title",
                    "url": "/url",
                },
                "itemsPointer": "/chunks",
                "mapping": {
                    "chunkId": "/id",
                    "text": "/content",
                    "page": "/metadata/document_page",
                    "bbox": "/metadata/bbox",
                    "pageWidth": "/metadata/width",
                    "pageHeight": "/metadata/height",
                },
            }
        ],
    )

    adapted = adapt_mcp_source_result(
        [],
        tool_name="mcp__reportify__document_fetch",
        descriptor=descriptor,
        structured_content=payload,
    )
    assert adapted is not None and adapted.citable
    envelope = adapted.model_content["_valuz_evidence"][0]
    assert envelope["evidence"]["quote"] == payload["chunks"][0]["content"]
    assert envelope["locator"] == {
        "kind": "pdf",
        "page": 8,
        "chunkId": "chunk-8",
        "quote": {"exact": payload["chunks"][0]["content"]},
        "coordinateSpace": "viewport-normalized-v1",
        "rects": [{"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.1}],
    }

    compacted = compact_citation_tool_content(adapted.model_content)
    private = private_citation_tool_content(adapted.model_content)
    assert compacted is not None and private is not None
    assert compacted["chunks"][0]["evidenceHandle"] == envelope["evidenceHandle"]
    assert payload["chunks"][0]["content"] in json.dumps(compacted, ensure_ascii=False)
    registry = EvidenceRegistry()
    assert registry.register_tool_projection(compacted, private, trusted_private=True) == 1
    assert registry.resolve(envelope["evidenceHandle"]) is not None


def test_document_published_at_is_not_substituted_for_document_version() -> None:
    payload = {
        "chunks": [
            {
                "id": "chunk-1",
                "content": "Stable cited text.",
                "doc": {
                    "doc_id": "doc-1",
                    "title": "Issuer filing",
                    "url": "https://example.com/doc-1.pdf",
                    "published_at": 1_785_364_320_000,
                },
            }
        ]
    }
    descriptor = _descriptor(
        payload,
        tool_name="kb_search",
        resources=[
            {
                "resourceId": "kb-search-chunks",
                "kind": "document-chunks",
                "authority": "authoritative",
                "rootPointer": "",
                "document": {
                    "scope": "item",
                    "sourceId": "/doc/doc_id",
                    "documentId": "/doc/doc_id",
                    "title": "/doc/title",
                    "url": "/doc/url",
                    "publishedAt": "/doc/published_at",
                },
                "itemsPointer": "/chunks",
                "mapping": {"chunkId": "/id", "text": "/content"},
            }
        ],
    )

    adapted = adapt_mcp_source_result(
        [],
        tool_name="mcp__reportify__kb_search",
        descriptor=descriptor,
        structured_content=payload,
    )

    assert adapted is not None and adapted.citable
    source = adapted.model_content["_valuz_evidence"][0]["source"]
    assert source["publishedAt"] == "2026-07-29T22:32:00Z"
    assert "documentVersion" not in source


def test_document_summary_creates_direct_evidence_without_copying_model_content() -> None:
    summary = (
        "Top Models Weekly usage. 1. Alpha 8.25T tokens. "
        "2. Beta 7.31T tokens. 3. Gamma 5.26T tokens."
    )
    payload = {
        "doc_id": "doc-live-ranking",
        "title": "Live model ranking",
        "url": "https://example.com/rankings",
        "category": "webpages",
        "summary": summary,
        "chunks": [
            {
                "id": "chunk-intro",
                "content": "Live rankings based on real usage.",
            }
        ],
    }
    descriptor = _descriptor(
        payload,
        tool_name="document_fetch",
        resources=[
            {
                "resourceId": "document-fetch-summary",
                "kind": "document-summary",
                "authority": "derived",
                "rootPointer": "",
                "document": {
                    "scope": "resource",
                    "sourceId": "/doc_id",
                    "documentId": "/doc_id",
                    "title": "/title",
                    "url": "/url",
                    "providerCategory": "/category",
                },
                "textPointer": "/summary",
                "locator": {
                    "kind": "external",
                    "fragment": "provider-summary",
                },
            }
        ],
    )

    adapted = adapt_mcp_source_result(
        [],
        tool_name="mcp__reportify__document_fetch",
        descriptor=descriptor,
        structured_content=payload,
    )

    assert adapted is not None and adapted.citable
    assert adapted.resource_kinds == frozenset({"document-summary"})
    envelope = adapted.model_content["_valuz_evidence"][0]
    assert envelope["source"] == {
        "sourceId": "doc-live-ranking",
        "documentId": "doc-live-ranking",
        "providerId": "reportify",
        "sourceType": "document",
        "sourceCategory": "webpages",
        "title": "Live model ranking",
        "retrievedAt": "2026-08-03T00:00:00Z",
        "canonicalUrl": "https://example.com/rankings",
    }
    assert envelope["evidence"]["quote"] == summary
    assert envelope["locator"] == {
        "kind": "external",
        "fragment": "provider-summary",
    }

    compacted = compact_citation_tool_content(adapted.model_content)
    private = private_citation_tool_content(adapted.model_content)
    assert compacted is not None and private is not None
    assert compacted["summary"] == summary
    assert compacted["evidenceHandle"] == envelope["evidenceHandle"]
    assert compacted["citationLink"] == f"[source](evidence://{envelope['evidenceHandle']})"
    assert "_valuz_evidence" not in compacted
    assert json.dumps(compacted, ensure_ascii=False).count(summary) == 1
    registry = EvidenceRegistry()
    assert registry.register_tool_projection(compacted, private, trusted_private=True) == 1
    assert registry.resolve(envelope["evidenceHandle"]) is not None


def test_raw_document_metadata_cannot_elevate_provider_summary() -> None:
    payload = {
        "doc_id": "doc-annual-report",
        "original_url": "https://example.com/annual-report.pdf",
        "summary": "The complete original document body.",
    }
    descriptor = _descriptor(
        payload,
        tool_name="document_raw_content",
        resources=[
            {
                "resourceId": "document-fetch-summary",
                "kind": "document-summary",
                "authority": "derived",
                "rootPointer": "",
                "document": {
                    "scope": "resource",
                    "sourceId": "/doc_id",
                    "documentId": "/doc_id",
                    "url": "/original_url",
                },
                "textPointer": "/summary",
                "locator": {
                    "kind": "external",
                    "fragment": "provider-summary",
                },
            }
        ],
    )

    adapted = adapt_mcp_source_result(
        [],
        tool_name="document_raw_content",
        descriptor=descriptor,
        structured_content=payload,
    )

    assert adapted is not None
    assert adapted.citable is False
    assert adapted.evidence_count == 0
    assert "_valuz_evidence" not in adapted.model_content


def test_raw_document_metadata_cannot_turn_the_whole_body_into_one_chunk() -> None:
    payload = {
        "doc_id": "doc-annual-report",
        "original_url": "https://example.com/annual-report.pdf",
        "content": "The complete original document body.",
    }
    descriptor = _descriptor(
        payload,
        tool_name="document_raw_content",
        resources=[
            {
                "resourceId": "document-raw-content",
                "kind": "document-chunks",
                "authority": "authoritative",
                "rootPointer": "",
                "document": {
                    "scope": "resource",
                    "sourceId": "/doc_id",
                    "documentId": "/doc_id",
                    "url": "/original_url",
                },
                "itemsPointer": "",
                "mapping": {"chunkId": "/doc_id", "text": "/content"},
            }
        ],
    )

    adapted = adapt_mcp_source_result(
        [],
        tool_name="document_raw_content",
        descriptor=descriptor,
        structured_content=payload,
    )

    assert adapted is not None
    assert adapted.citable is False
    assert adapted.evidence_count == 0
    assert "_valuz_evidence" not in adapted.model_content


def test_chunk_source_title_falls_back_to_hostname_not_document_id() -> None:
    payload = {
        "doc_id": "W13341981828044806",
        "title": "",
        "url": "https://www.news.cn/tech/example.html",
        "chunks": [{"id": "chunk-1", "content": "A short real chunk."}],
    }
    descriptor = _descriptor(
        payload,
        tool_name="document_fetch",
        resources=[
            {
                "resourceId": "document-fetch-chunks",
                "kind": "document-chunks",
                "authority": "authoritative",
                "rootPointer": "",
                "document": {
                    "scope": "resource",
                    "sourceId": "/doc_id",
                    "documentId": "/doc_id",
                    "title": "/title",
                    "url": "/url",
                },
                "itemsPointer": "/chunks",
                "mapping": {"chunkId": "/id", "text": "/content"},
            }
        ],
    )

    adapted = adapt_mcp_source_result(
        [],
        tool_name="document_fetch",
        descriptor=descriptor,
        structured_content=payload,
    )

    assert adapted is not None and adapted.citable
    source = adapted.model_content["_valuz_evidence"][0]["source"]
    assert source["title"] == "news.cn"
    assert source["documentId"] not in source["title"]


def test_large_structured_result_registers_one_collection_and_materializes_one_address() -> None:
    rows = [
        {
            "ticker": f"T{index:04d}",
            "fiscal_year": 2024,
            "period": "FY",
            "revenue": index * 1_000_000,
            "currency": "CNY",
        }
        for index in range(1_000)
    ]
    payload = {"data": rows, "total": len(rows)}
    descriptor = _descriptor(
        payload,
        tool_name="company_income_statement",
        resources=[
            {
                "resourceId": "reportify-company-income-statement",
                "kind": "structured-collection",
                "authority": "authoritative",
                "rootPointer": "/data",
                "itemsPointer": "/data",
                "dataset": {
                    "id": "reportify.company_income_statement",
                    "revision": "v1",
                    "sourceCategory": "structured_financials",
                },
                "identity": {"fields": ["/ticker", "/fiscal_year", "/period"]},
                "semantics": {
                    "entity": {"ticker": "/ticker"},
                    "period": {"fiscalYear": "/fiscal_year", "period": "/period"},
                    "unit": {"currency": "/currency"},
                    "metric": {
                        "mode": "field-name",
                        "valueRoots": [""],
                        "excludedFields": [
                            "/ticker",
                            "/fiscal_year",
                            "/period",
                            "/currency",
                        ],
                    },
                },
                "addressing": {
                    "mode": "json-pointer",
                    "allowedPathRoots": ["/data"],
                    "fieldSchemaRef": {
                        "id": "reportify.company_income_statement",
                        "revision": "v1",
                    },
                },
            }
        ],
    )

    adapted = adapt_mcp_source_result(
        [],
        tool_name="company_income_statement",
        descriptor=descriptor,
        structured_content=payload,
    )
    assert adapted is not None and adapted.citable
    assert len(adapted.model_content["_valuz_evidence"]) == 1
    collection = adapted.model_content["_valuz_evidence"][0]
    assert collection["kind"] == "structured-evidence-collection"
    assert "T0999" not in json.dumps(collection, ensure_ascii=False)

    compacted = compact_citation_tool_content(adapted.model_content)
    private = private_citation_tool_content(adapted.model_content)
    assert compacted is not None and private is not None
    assert len(compacted["data"]) == 1_000
    assert "_valuz_evidence" not in compacted
    assert compacted["_valuz_evidence_hint"]["collectionHandle"] == collection["collectionHandle"]

    registry = EvidenceRegistry()
    assert registry.register_tool_projection(compacted, private, trusted_private=True) == 1
    record = registry.materialize_reference(
        collection["collectionHandle"],
        "#/data/37/revenue",
    )
    assert record is not None
    assert record.evidence["value"] == 37_000_000
    assert record.evidence["entityId"] == "T0037"
    assert record.evidence["period"] == "2024 FY"
    assert record.evidence["currency"] == "CNY"
    assert record.evidence["recordKey"] == "T0037|2024|FY"


def test_tampered_business_result_rejects_metadata() -> None:
    original = {"data": [{"ticker": "600519", "revenue": 100}]}
    descriptor = _descriptor(
        original,
        tool_name="income_statement",
        resources=[
            {
                "resourceId": "income",
                "kind": "operational",
                "authority": "non-citable",
                "rootPointer": "",
            }
        ],
    )
    tampered = {"data": [{"ticker": "600519", "revenue": 999}]}

    assert (
        adapt_mcp_source_result(
            [],
            tool_name="income_statement",
            descriptor=descriptor,
            structured_content=tampered,
        )
        is None
    )


def test_factor_collection_materializes_formula_bound_metric() -> None:
    payload = {
        "metadata": {"formula": "MA(CLOSE, 20)", "as_of": "2026-08-03"},
        "datas": [
            {
                "date": "2026-08-03",
                "symbol": "MRVL",
                "factor_value": 203.69,
            }
        ],
    }
    descriptor = _descriptor(
        payload,
        tool_name="factors_compute",
        resources=[
            {
                "resourceId": "reportify-factors-compute",
                "kind": "structured-collection",
                "authority": "derived",
                "rootPointer": "/datas",
                "itemsPointer": "/datas",
                "dataset": {
                    "id": "reportify.factors_compute",
                    "revision": "v1",
                    "sourceCategory": "derived_analytics",
                },
                "identity": {"fields": ["/symbol", "/date"]},
                "semantics": {
                    "entity": {"symbol": "/symbol"},
                    "asOf": {"date": "/date"},
                    "metric": {
                        "mode": "field-map",
                        "fields": {"/factor_value": "moving_average_20"},
                    },
                },
                "addressing": {
                    "mode": "json-pointer",
                    "allowedPathRoots": ["/datas"],
                    "fieldSchemaRef": {
                        "id": "reportify.factors_compute",
                        "revision": "v1",
                    },
                },
            }
        ],
    )

    adapted = adapt_mcp_source_result(
        [],
        tool_name="mcp__reportify__factors_compute",
        descriptor=descriptor,
        structured_content=payload,
    )
    assert adapted is not None and adapted.citable
    compacted = compact_citation_tool_content(adapted.model_content)
    private = private_citation_tool_content(adapted.model_content)
    assert compacted is not None and private is not None
    assert compacted["_valuz_evidence_hint"]["metricFields"] == {
        "/factor_value": "moving_average_20"
    }

    registry = EvidenceRegistry()
    assert registry.register_tool_projection(compacted, private, trusted_private=True) == 1
    factor = registry.materialize_reference(
        compacted["_valuz_evidence_hint"]["collectionHandle"],
        "#/datas/0/factor_value",
    )
    assert factor is not None
    assert factor.evidence["metric"] == "moving_average_20"
    assert factor.evidence["value"] == 203.69
    assert factor.evidence["entityId"] == "MRVL"
    assert factor.evidence["asOf"] == "2026-08-03"

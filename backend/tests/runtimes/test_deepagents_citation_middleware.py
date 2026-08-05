"""DeepAgents citation evidence compaction tests."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from src.core.citation import EvidenceRegistry
from src.core.mcp_source_metadata import MCP_SOURCE_TRANSPORT_KEY
from src.runtimes.deepagents.middleware import (
    CitationEvidenceCompactionMiddleware,
    ToolErrorTolerantMiddleware,
    _canonical_metric_for_factor_formula,
    citation_artifact_content,
)


async def test_citation_evidence_is_compacted_for_model_and_preserved_privately() -> None:
    envelope = {
        "evidenceHandle": "ev_revenue_12345678",
        "source": {
            "sourceId": "financials:600519",
            "providerId": "valuz-stock",
            "sourceType": "dataset",
            "title": "Company income statement · 600519",
            "retrievedAt": "2026-08-01T08:00:00Z",
        },
        "evidence": {
            "kind": "structured-data",
            "datasetId": "financials",
            "toolName": "income_statement",
            "recordKey": "600519|2024 FY",
            "field": "total_revenue.operating_revenue",
            "metric": "operating_revenue",
            "value": 170_899_152_276,
            "unit": "CNY",
            "period": "2024 FY",
            "capturedAt": "2026-08-01T08:00:00Z",
        },
    }
    full_payload = {
        "_valuz_evidence": [envelope],
        "data": [{"total_revenue": {"operating_revenue": 170_899_152_276}}],
    }
    original_content = [{"type": "text", "text": json.dumps(full_payload)}]
    original = ToolMessage(
        content=original_content,
        tool_call_id="call-1",
        name="income_statement",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        cast(Any, object()),
        handler,
    )

    assert isinstance(result, ToolMessage)
    compact_text = result.content[0]["text"]
    compact = json.loads(compact_text)
    assert compact["data"] == full_payload["data"]
    assert "_valuz_evidence" not in compact
    hint = compact["_valuz_evidence_hint"]
    assert hint["collectionHandle"].startswith("evc_legacy_")
    assert hint["contentRoot"] == "/data"
    assert hint["citationTemplate"].endswith("#{json-pointer}")
    assert "capturedAt" not in compact_text
    private_content = citation_artifact_content(result)
    assert private_content is not None
    private_items = json.loads(private_content)["_valuz_evidence"]
    assert len(private_items) == 1
    assert private_items[0]["kind"] == "structured-evidence-collection"
    assert private_items[0]["collectionHandle"] == hint["collectionHandle"]
    assert "data" not in json.loads(private_content)


async def test_large_nested_legacy_result_compacts_before_filesystem_eviction() -> None:
    source = {
        "sourceId": "index-constituents:000905",
        "providerId": "valuz-stock",
        "sourceType": "dataset",
        "title": "Index constituents · 000905",
        "retrievedAt": "2026-08-03T05:00:00Z",
    }
    rows = [{"market": "cn", "symbol": f"{position:06d}"} for position in range(1_000)]
    payload = {
        "data": {"items": rows},
        "_valuz_evidence": [
            {
                "evidenceHandle": f"ev_constituent_{position:08d}",
                "source": source,
                "evidence": {
                    "kind": "structured-data",
                    "datasetId": "index-constituents",
                    "toolName": "index_constituents",
                    "recordKey": f"000905|{row['symbol']}",
                    "entityId": row["symbol"],
                    "field": "market",
                    "metric": "market",
                    "value": row["market"],
                    "unit": "",
                    "capturedAt": "2026-08-03T05:00:00Z",
                },
            }
            for position, row in enumerate(rows)
        ],
    }
    original_content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    original = ToolMessage(
        content=original_content,
        tool_call_id="large-index",
        name="index_constituents",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "large-index",
                    "name": "index_constituents",
                    "args": {"index": "000905"},
                }
            },
        )(),
    )
    started = time.perf_counter()
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        request,
        handler,
    )
    elapsed = time.perf_counter() - started

    assert isinstance(result, ToolMessage)
    assert elapsed < 3.0
    assert len(str(result.content)) < len(original_content) / 4
    private_content = citation_artifact_content(result)
    assert private_content is not None
    private_items = json.loads(private_content)["_valuz_evidence"]
    assert len(private_items) == 1
    assert private_items[0]["kind"] == "structured-evidence-collection"


async def test_calculation_rejects_value_that_does_not_match_collection_address() -> None:
    middleware = CitationEvidenceCompactionMiddleware()
    payload = {
        "data": [
            {
                "fiscal_year": "2025",
                "period": "annual",
                "total_revenue": {"operating_revenue": 168_838_102_515},
            },
            {
                "fiscal_year": "2024",
                "period": "annual",
                "total_revenue": {"operating_revenue": 170_899_152_276},
            },
        ],
        "_valuz_evidence": [
            {
                "evidenceHandle": "ev_revenue_2025_12345678",
                "source": {
                    "sourceId": "financials:600519",
                    "providerId": "valuz-stock",
                    "sourceType": "dataset",
                    "title": "Company income statement · 600519",
                    "retrievedAt": "2026-08-02T08:00:00Z",
                },
                "evidence": {
                    "kind": "structured-data",
                    "datasetId": "financials",
                    "toolName": "income_statement",
                    "recordKey": "600519|2025 FY",
                    "field": "operating_revenue",
                    "metric": "operating_revenue",
                    "value": 168_838_102_515,
                    "unit": "CNY",
                    "period": "2025 FY",
                    "capturedAt": "2026-08-02T08:00:00Z",
                },
            }
        ],
    }
    statement_request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "statement",
                    "name": "income_statement",
                    "args": {"symbol": "600519", "period": "annual", "limit": 2},
                }
            },
        )(),
    )

    async def statement_handler(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=[{"type": "text", "text": json.dumps(payload)}],
            tool_call_id="statement",
            name="income_statement",
        )

    statement = await middleware.awrap_tool_call(statement_request, statement_handler)
    assert isinstance(statement, ToolMessage)
    hint = json.loads(statement.content[0]["text"])["_valuz_evidence_hint"]
    address = f"{hint['collectionHandle']}#/data/0/total_revenue/operating_revenue"
    calculation_request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "calculation",
                    "name": "citation_calculate",
                    "args": {
                        "expression": "current / prior",
                        "inputs": [
                            {
                                "name": "current",
                                "value": 170_899_152_276,
                                "evidenceHandle": address,
                            }
                        ],
                        "unit": "%",
                    },
                }
            },
        )(),
    )
    calculation_called = False

    async def calculation_handler(_request: ToolCallRequest) -> ToolMessage:
        nonlocal calculation_called
        calculation_called = True
        return ToolMessage(content="unexpected", tool_call_id="calculation")

    rejected = await middleware.awrap_tool_call(calculation_request, calculation_handler)

    assert isinstance(rejected, ToolMessage)
    assert rejected.status == "error"
    assert "evidence mismatch" in str(rejected.content)
    assert "2025 FY" in str(rejected.content)
    assert calculation_called is False


async def test_indexed_chunks_gain_evidence_before_deepagents_compaction() -> None:
    original = ToolMessage(
        content=json.dumps(
            {
                "chunks": [
                    {
                        "id": "chunk-1",
                        "content": "Demand continues to exceed available supply.",
                        "metadata": {"document_page": 9},
                        "doc": {
                            "doc_id": "msft-q1",
                            "title": "Microsoft FY2026 Q1 transcript",
                            "url": "https://reportify.cn/transcripts/msft-q1",
                            "category": "transcripts",
                        },
                    }
                ]
            }
        ),
        tool_call_id="kb-chunk",
        name="kb_search",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "kb-chunk",
                    "name": "kb_search",
                    "args": {"doc_ids": ["msft-q1"], "query": "AI demand"},
                }
            },
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        request,
        handler,
    )

    assert isinstance(result, ToolMessage)
    compacted = json.loads(str(result.content))
    assert compacted["chunks"][0]["evidenceHandle"].startswith("ev_chunk_")
    assert "Demand continues to exceed available supply." in compacted["chunks"][0]["content"]
    private = citation_artifact_content(result)
    assert private is not None
    registry = EvidenceRegistry()
    assert registry.register_tool_result(private, trusted_private=True) == 1


async def test_compaction_does_not_register_out_of_scope_indexed_chunks() -> None:
    original = ToolMessage(
        content=json.dumps(
            {
                "chunks": [
                    {
                        "id": "wrong",
                        "content": "Unrelated operating cash flow was 29,833.",
                        "doc": {"doc_id": "nvda-q2", "title": "NVIDIA Q2"},
                    }
                ]
            }
        ),
        tool_call_id="kb-scope",
        name="kb_search",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "kb-scope",
                    "name": "kb_search",
                    "args": {"doc_id": "sk-q2", "query": "operating cash flow"},
                }
            },
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        request,
        handler,
    )

    assert isinstance(result, ToolMessage)
    payload = json.loads(str(result.content))
    assert payload["chunks"] == []
    assert citation_artifact_content(result) is None


async def test_document_text_evidence_preserves_selected_chunks_and_aligns_handles() -> None:
    envelopes = [
        {
            "evidenceHandle": f"ev_transcript_{index:08d}",
            "source": {
                "sourceId": "doc-1",
                "providerId": "reportify",
                "sourceType": "document",
                "title": "Earnings call transcript",
                "retrievedAt": "2026-08-01T08:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": f"chunk {index} " + "detail " * 300,
                "snippet": f"chunk {index} " + "detail " * 300,
            },
            "locator": {"kind": "chunk", "chunkId": f"chunk-{index}"},
        }
        for index in range(100)
    ]
    full_payload = {
        "doc_id": "doc-1",
        "title": "Earnings call transcript",
        "chunks": [{"id": f"chunk-{index}", "text": "detail " * 500} for index in range(100)],
        "metadatas": [{"chunk": index} for index in range(100)],
        "_valuz_evidence": envelopes,
    }
    original_content = [{"type": "text", "text": json.dumps(full_payload)}]
    original = ToolMessage(
        content=original_content,
        tool_call_id="call-document",
        name="document_fetch",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        cast(Any, object()),
        handler,
    )

    assert isinstance(result, ToolMessage)
    compacted = json.loads(result.content[0]["text"])
    assert len(compacted["chunks"]) == 100
    assert len(compacted["metadatas"]) == 100
    assert "_valuz_evidence" not in compacted
    assert compacted["chunks"][0]["evidenceHandle"] == "ev_transcript_00000000"
    assert compacted["chunks"][-1]["evidenceHandle"] == "ev_transcript_00000099"
    assert compacted["_valuz_compaction"] == {
        "evidenceReturned": 100,
        "evidenceShown": 100,
        "bulkTextOmitted": False,
        "modelContentPreserved": True,
    }
    private_content = citation_artifact_content(result)
    assert private_content is not None
    assert len(json.loads(private_content)["_valuz_evidence"]) == 100


async def test_document_table_compaction_keeps_headers_and_trailing_rows() -> None:
    table = (
        "| 销售模式 | 营业收入 | 同比 |\n"
        "| --- | --- | --- |\n"
        + "| 中间行 | 1 | 2 |\n" * 80
        + "| 批发代理 | 95,768,511,021.23 | 19.73 |\n"
        + "| 直销 | 74,843,327,030.79 | 11.32 |"
    )
    payload = {
        "chunks": [{"text": table}],
        "_valuz_evidence": [
            {
                "evidenceHandle": "ev_channel_table_12345678",
                "source": {
                    "sourceId": "doc-1",
                    "providerId": "reportify",
                    "sourceType": "document",
                    "title": "Annual report",
                    "retrievedAt": "2026-08-02T08:00:00Z",
                },
                "evidence": {"kind": "text", "quote": table},
            }
        ],
    }
    original = ToolMessage(
        content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        tool_call_id="call-document-table",
        name="document_fetch",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        cast(Any, object()),
        handler,
    )

    compacted = json.loads(result.content[0]["text"])
    assert compacted["chunks"][0]["text"] == table
    assert compacted["chunks"][0]["evidenceHandle"] == ("ev_channel_table_12345678")
    assert "_valuz_evidence" not in compacted


async def test_document_compaction_keeps_trusted_boundary_context() -> None:
    payload = {
        "chunks": [{"text": "main chunk"}],
        "_valuz_evidence": [
            {
                "evidenceHandle": "ev_azure_boundary_12345678",
                "source": {
                    "sourceId": "doc-1",
                    "providerId": "reportify",
                    "sourceType": "document",
                    "title": "Microsoft earnings call",
                    "retrievedAt": "2026-08-02T08:00:00Z",
                },
                "evidence": {
                    "kind": "text",
                    "prefix": (
                        "In Azure and other cloud services, revenue grew 40% "
                        "and 39% in constant currency."
                    ),
                    "quote": "Azure AI services revenue was generally in line with expectations.",
                    "suffix": "Demand again exceeded supply across workloads.",
                },
            }
        ],
    }
    original = ToolMessage(
        content=[{"type": "text", "text": json.dumps(payload)}],
        tool_call_id="call-document-boundary",
        name="document_fetch",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        cast(Any, object()),
        handler,
    )

    compacted = json.loads(result.content[0]["text"])
    excerpt = compacted["_valuz_evidence"][0]["excerpt"]
    assert "revenue grew 40%" in excerpt
    assert "Azure AI services revenue" in excerpt
    assert "Demand again exceeded supply" in excerpt


async def test_non_citation_tool_result_is_unchanged() -> None:
    original = ToolMessage(content="plain result", tool_call_id="call-1", name="plain")

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        cast(Any, object()),
        handler,
    )

    assert result is original
    assert citation_artifact_content(result) is None


async def test_reportify_mcp_metadata_builds_lazy_collection_without_per_field_evidence() -> None:
    payload = {
        "data": [
            {
                "ticker": "600519",
                "fiscal_year": 2024,
                "period": "FY",
                "revenue": 174_144_000_000,
                "currency": "CNY",
            }
        ]
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    descriptor = {
        "version": 1,
        "provider": {"id": "reportify", "name": "Reportify"},
        "operation": {"toolName": "company_income_statement"},
        "result": {
            "target": "structuredContent",
            "hash": {"algorithm": "sha256", "value": digest},
            "capturedAt": "2026-08-03T00:00:00Z",
        },
        "resources": [
            {
                "resourceId": "income-statement",
                "kind": "structured-collection",
                "authority": "authoritative",
                "rootPointer": "/data",
                "itemsPointer": "/data",
                "dataset": {
                    "id": "reportify.company_income_statement",
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
                },
            }
        ],
    }
    original = ToolMessage(
        content=[{"type": "text", "text": json.dumps(payload)}],
        artifact={
            "structured_content": {
                MCP_SOURCE_TRANSPORT_KEY: {
                    "serverName": "reportify",
                    "descriptor": descriptor,
                    "hasStructuredContent": True,
                    "structuredContent": payload,
                }
            }
        },
        tool_call_id="call-reportify",
        name="company_income_statement",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "call-reportify",
                    "name": "company_income_statement",
                    "args": {},
                }
            },
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    visible_payload = json.loads(result.content)
    assert visible_payload["data"] == payload["data"]
    assert "_valuz_evidence" not in visible_payload
    hint = visible_payload["_valuz_evidence_hint"]
    assert hint["collectionHandle"].startswith("evc_mcp_")
    assert result.artifact["structured_content"] == payload
    private = citation_artifact_content(result)
    assert private is not None
    private_payload = json.loads(private)
    assert len(private_payload["_valuz_evidence"]) == 1
    assert private_payload["_valuz_evidence"][0]["kind"] == "structured-evidence-collection"


async def test_reportify_discovery_metadata_stays_non_citable() -> None:
    payload = {
        "docs": [
            {
                "doc_id": f"d{index}",
                "title": f"Report {index}",
                "summary": f"Revenue {index}00",
            }
            for index in range(12)
        ]
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    descriptor = {
        "version": 1,
        "provider": {"id": "reportify"},
        "operation": {"toolName": "reports_search"},
        "result": {
            "target": "structuredContent",
            "hash": {"algorithm": "sha256", "value": digest},
            "capturedAt": "2026-08-03T00:00:00Z",
        },
        "resources": [
            {
                "resourceId": "search",
                "kind": "document-discovery",
                "authority": "discovery-only",
                "rootPointer": "",
                "itemsPointer": "/docs",
                "mapping": {"sourceId": "/doc_id", "title": "/title"},
            }
        ],
    }
    original = ToolMessage(
        content="wire content",
        artifact={
            "structured_content": {
                MCP_SOURCE_TRANSPORT_KEY: {
                    "descriptor": descriptor,
                    "hasStructuredContent": True,
                    "structuredContent": payload,
                }
            }
        },
        tool_call_id="call-search",
        name="reports_search",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {"tool_call": {"id": "call-search", "name": "reports_search", "args": {}}},
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    visible_payload = json.loads(result.content)
    assert visible_payload["docs"] == payload["docs"]
    assert "evidenceHandle" not in visible_payload["docs"][0]
    assert "_valuz_evidence_hint" not in visible_payload
    assert "_valuz_discovery" not in visible_payload
    assert citation_artifact_content(result) is None
    registry = EvidenceRegistry()
    assert registry.register_tool_result(result.content, tool_name="reports_search") == 0
    assert registry.rejected_count == 0


async def test_reportify_document_summary_metadata_survives_transport_as_one_evidence() -> None:
    summary = "Alpha used 8.25T tokens; Beta used 7.31T tokens this week."
    payload = {
        "doc_id": "openrouter-ranking",
        "title": "OpenRouter model rankings",
        "url": "https://openrouter.ai/rankings",
        "category": "webpages",
        "summary": summary,
        "chunks": [
            {
                "id": "intro",
                "content": "Live model rankings based on real usage.",
            }
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    descriptor = {
        "version": 1,
        "provider": {"id": "reportify", "name": "Reportify"},
        "operation": {"toolName": "document_fetch"},
        "result": {
            "target": "structuredContent",
            "hash": {"algorithm": "sha256", "value": digest},
            "capturedAt": "2026-08-04T00:00:00Z",
        },
        "resources": [
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
                    "providerCategory": "/category",
                },
                "itemsPointer": "/chunks",
                "mapping": {"chunkId": "/id", "text": "/content"},
            },
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
            },
        ],
    }
    original = ToolMessage(
        content=[{"type": "text", "text": json.dumps(payload)}],
        artifact={
            "structured_content": {
                MCP_SOURCE_TRANSPORT_KEY: {
                    "serverName": "reportify",
                    "descriptor": descriptor,
                    "hasStructuredContent": True,
                    "structuredContent": payload,
                }
            }
        },
        tool_call_id="call-document-fetch",
        name="document_fetch",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "call-document-fetch",
                    "name": "document_fetch",
                    "args": {"doc_id": "openrouter-ranking"},
                }
            },
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    visible = json.loads(result.content)
    assert visible["summary"] == summary
    assert visible["evidenceHandle"].startswith("ev_mcp_")
    assert visible["citationLink"] == f"[source](evidence://{visible['evidenceHandle']})"
    assert "_valuz_evidence" not in visible
    private = citation_artifact_content(result)
    assert private is not None
    private_payload = json.loads(private)
    evidence = private_payload["_valuz_evidence"]
    assert len(evidence) == 2
    summary_evidence = next(item for item in evidence if item["evidence"]["quote"] == summary)
    assert summary_evidence["evidenceHandle"] == visible["evidenceHandle"]
    assert summary_evidence["locator"] == {
        "kind": "external",
        "fragment": "provider-summary",
    }


async def test_kb_search_exact_chunks_survive_stale_non_citable_metadata() -> None:
    payload = {
        "chunks": [
            {
                "id": "chunk-7",
                "content": "MLCC 2025-2028E revenue CAGR is 24%.",
                "metadata": {"document_page": 7},
                "doc": {
                    "doc_id": "report-1",
                    "title": "Asian MLCC Industry",
                    "url": "https://reportify.cn/reports/report-1",
                    "category": "global_research",
                },
            }
        ]
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    stale_descriptor = {
        "version": 1,
        "provider": {"id": "reportify"},
        "operation": {"toolName": "kb_search"},
        "result": {
            "target": "structuredContent",
            "hash": {"algorithm": "sha256", "value": digest},
            "capturedAt": "2026-08-04T00:00:00Z",
        },
        "resources": [
            {
                "resourceId": "stale-search",
                "kind": "document-discovery",
                "authority": "discovery-only",
                "rootPointer": "",
                "itemsPointer": "/chunks",
                "mapping": {"sourceId": "/id", "title": "/id"},
            }
        ],
    }
    original = ToolMessage(
        content=[{"type": "text", "text": json.dumps(payload)}],
        artifact={
            "structured_content": {
                MCP_SOURCE_TRANSPORT_KEY: {
                    "descriptor": stale_descriptor,
                    "hasStructuredContent": True,
                    "structuredContent": payload,
                }
            }
        },
        tool_call_id="call-kb-search",
        name="kb_search",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {"tool_call": {"id": "call-kb-search", "name": "kb_search", "args": {}}},
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    visible = json.loads(result.content)
    assert visible["chunks"][0]["content"] == payload["chunks"][0]["content"]
    assert visible["chunks"][0]["evidenceHandle"].startswith("ev_chunk_")
    assert visible["chunks"][0]["citationLink"].startswith("[source](evidence://ev_chunk_")
    private = citation_artifact_content(result)
    assert private is not None
    private_payload = json.loads(private)
    evidence = private_payload["_valuz_evidence"][0]
    assert evidence["source"]["documentId"] == "report-1"
    assert evidence["locator"]["chunkId"] == "chunk-7"


async def test_discovery_search_summaries_preserve_provider_content_and_order() -> None:
    payload = {
        "docs": [
            {
                "doc_id": f"W{index}",
                "title": f"Document {index}",
                "summary": "detail " * 500,
            }
            for index in range(12)
        ]
    }
    original = ToolMessage(
        content=[{"type": "text", "text": json.dumps(payload)}],
        tool_call_id="call-1",
        name="news_search",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    emitted: list[tuple[str, str | None, Any, str]] = []

    async def emit_artifact(
        join_id: str,
        tool_name: str | None,
        model_content: Any,
        citation_content: str,
    ) -> None:
        emitted.append((join_id, tool_name, model_content, citation_content))

    result = await CitationEvidenceCompactionMiddleware(
        citation_artifact_emitter=emit_artifact
    ).awrap_tool_call(
        cast(Any, object()),
        handler,
    )

    assert isinstance(result, ToolMessage)
    compacted = json.loads(result.content[0]["text"])
    assert len(compacted["docs"]) == 12
    assert compacted["docs"][0]["summary"] == payload["docs"][0]["summary"]
    assert compacted["docs"][0]["evidenceHandle"].startswith("ev_summary_")
    assert "_valuz_discovery" not in compacted
    private_content = citation_artifact_content(result)
    assert private_content is not None
    assert len(emitted) == 1
    assert emitted[0][0] == "call-1"
    assert emitted[0][1] == "news_search"
    assert emitted[0][2] == result.content
    assert emitted[0][3] == private_content
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_result(
            private_content,
            tool_name="news_search",
            trusted_private=True,
        )
        == 12
    )


async def test_discovery_compaction_uses_request_name_when_tool_message_omits_it() -> None:
    original = ToolMessage(
        content=json.dumps({"docs": [{"doc_id": "W1", "title": "One", "summary": "x" * 2_000}]}),
        tool_call_id="call-1",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {"tool_call": {"id": "call-1", "name": "webpage_search"}},
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    compacted = json.loads(str(result.content))
    assert "_valuz_discovery" not in compacted
    assert len(compacted["docs"][0]["summary"]) == 2_000
    assert compacted["docs"][0]["evidenceHandle"].startswith("ev_summary_")
    assert citation_artifact_content(result) is not None


async def test_transcript_discovery_does_not_filter_secondary_company_mentions() -> None:
    payload = {
        "docs": [
            {
                "doc_id": "iren-q1",
                "title": "Iris Energy (IREN) - 2026 Q1 - Earnings Call Transcript",
                "summary": "Iris Energy signed a contract with Microsoft.",
                "companies": [
                    {
                        "name": "Iris Energy",
                        "stocks": [{"symbol": "US:IREN"}],
                    },
                    {
                        "name": "Microsoft",
                        "stocks": [{"symbol": "US:MSFT"}],
                    },
                ],
            },
            {
                "doc_id": "msft-q1",
                "title": "Microsoft(MSFT) - 2026 Q1 - Earnings Call Transcript",
                "summary": "Azure revenue grew in the quarter.",
                "companies": [
                    {
                        "name": "Microsoft",
                        "stocks": [{"symbol": "US:MSFT"}],
                    }
                ],
            },
        ]
    }
    original = ToolMessage(
        content=[{"type": "text", "text": json.dumps(payload)}],
        tool_call_id="call-msft",
        name="conferences_search",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "call-msft",
                    "name": "conferences_search",
                    "args": {"symbols": ["US:MSFT"]},
                }
            },
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        request,
        handler,
    )

    compacted = json.loads(result.content[0]["text"])
    assert compacted["docs"] == payload["docs"]
    assert "_valuz_discovery" not in compacted
    assert citation_artifact_content(result) is None


async def test_transcript_discovery_preserves_duplicate_provider_rows() -> None:
    common = {
        "title": "Microsoft(MSFT) - 2026 Q1 - Earnings Call Transcript",
        "summary": "Azure revenue grew 40% in the quarter.",
        "companies": [
            {
                "name": "Microsoft",
                "stocks": [{"symbol": "US:MSFT"}],
            }
        ],
        "metadata": {"fiscal_year": "2026", "fiscal_quarter": "Q1"},
    }
    original = ToolMessage(
        content=json.dumps(
            {
                "docs": [
                    {**common, "doc_id": "msft-q1-first"},
                    {**common, "doc_id": "msft-q1-duplicate"},
                ]
            }
        ),
        tool_call_id="call-msft",
        name="conferences_search",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "call-msft",
                    "name": "conferences_search",
                    "args": {"symbols": ["US:MSFT"]},
                }
            },
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(request, handler)

    compacted = json.loads(str(result.content))
    assert [doc["doc_id"] for doc in compacted["docs"]] == [
        "msft-q1-first",
        "msft-q1-duplicate",
    ]
    assert compacted["docs"][0]["summary"] == common["summary"]
    assert "_valuz_discovery" not in compacted
    assert citation_artifact_content(result) is None


async def test_discovery_compaction_handles_json_encoded_mcp_content_blocks() -> None:
    nested = json.dumps(
        [
            {
                "type": "text",
                "text": json.dumps(
                    {"docs": [{"doc_id": "W1", "title": "One", "summary": "x" * 2_000}]}
                ),
            }
        ]
    )
    original = ToolMessage(content=nested, tool_call_id="call-1")

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {"tool_call": {"id": "call-1", "name": "news_search"}},
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(request, handler)

    outer = json.loads(str(result.content))
    inner = json.loads(outer[0]["text"])
    assert "_valuz_discovery" not in inner
    assert len(inner["docs"][0]["summary"]) == 2_000
    assert inner["docs"][0]["evidenceHandle"].startswith("ev_summary_")
    assert citation_artifact_content(result) is not None


async def test_discovery_summary_handles_survive_nested_compaction() -> None:
    original = ToolMessage(
        content=json.dumps(
            {
                "docs": [
                    {
                        "doc_id": "W1",
                        "title": "Stable result",
                        "summary": "DRAM prices rose 90% to 95%. " * 100,
                        "url": "https://example.com/dram",
                    }
                ]
            }
        ),
        tool_call_id="call-1",
        name="news_search",
    )
    middleware = CitationEvidenceCompactionMiddleware()

    async def first_handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    first = await middleware.awrap_tool_call(cast(Any, object()), first_handler)
    first_handle = json.loads(str(first.content))["docs"][0]["evidenceHandle"]

    async def second_handler(_request: ToolCallRequest) -> ToolMessage:
        return first

    second = await middleware.awrap_tool_call(cast(Any, object()), second_handler)
    second_handle = json.loads(str(second.content))["docs"][0]["evidenceHandle"]

    assert second_handle == first_handle
    private_content = citation_artifact_content(second)
    assert private_content is not None
    registry = EvidenceRegistry()
    assert registry.register_tool_result(private_content, trusted_private=True) == 1


async def test_financial_status_only_result_gets_addressable_collection() -> None:
    status_envelope = {
        "evidenceHandle": "ev_status_12345678",
        "source": {
            "sourceId": "reportify:600519",
            "providerId": "valuz-stock",
            "sourceType": "tool-result",
            "title": "Revenue breakdown",
            "retrievedAt": "2026-08-02T00:00:00Z",
        },
        "evidence": {
            "kind": "structured-data",
            "datasetId": "reportify",
            "toolName": "revenue_breakdown",
            "field": "status",
            "value": 200,
            "capturedAt": "2026-08-02T00:00:00Z",
        },
    }
    payload = {
        "status": 200,
        "data": {
            "currency": "CNY",
            "list": [
                {
                    "fiscal_year": 2024,
                    "period": "FY",
                    "product": [
                        {
                            "name": "茅台酒",
                            "revenue": 145_928_075_955.31,
                            "gross_profit_rate": 0.9406,
                        }
                    ],
                }
            ],
        },
        "_valuz_evidence": [status_envelope],
    }
    original = ToolMessage(
        content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        tool_call_id="toolu-revenue",
        name="revenue_breakdown",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "toolu-revenue",
                    "name": "revenue_breakdown",
                    "args": {"symbol": "600519"},
                }
            },
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(request, handler)

    compacted = json.loads(result.content[0]["text"])
    assert compacted["data"] == payload["data"]
    assert "_valuz_evidence" not in compacted
    hint = compacted["_valuz_evidence_hint"]
    assert hint["collectionHandle"].startswith("evc_tool_")
    private_content = citation_artifact_content(result)
    assert private_content is not None
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_projection(
            result.content,
            private_content,
            tool_name="revenue_breakdown",
            trusted_private=True,
        )
        == 1
    )
    assert registry.collection_count == 1
    revenue = registry.materialize_reference(
        hint["collectionHandle"],
        "#/data/list/0/product/0/revenue",
    )
    gross_margin = registry.materialize_reference(
        hint["collectionHandle"],
        "#/data/list/0/product/0/gross_profit_rate",
    )
    assert revenue is not None
    assert revenue.evidence["value"] == 145_928_075_955.31
    assert revenue.evidence["unit"] == "CNY"
    assert gross_margin is not None
    assert gross_margin.evidence["value"] == 94.06
    assert gross_margin.evidence["unit"] == "percent"


async def test_factor_series_result_gets_one_addressable_collection() -> None:
    payload = {
        "metadata": {
            "formula": "MA(CLOSE, 20)",
            "as_of": "2026-08-03",
            "coverage": {"start": "2026-05-06", "end": "2026-08-03", "rows": 2},
        },
        "datas": [
            {
                "date": "2026-08-03",
                "symbol": "MRVL",
                "name": "Marvell Technology",
                "close": 193.775,
                "factor_value": 203.69,
            },
            {
                "date": "2026-07-31",
                "symbol": "MRVL",
                "name": "Marvell Technology",
                "close": 187.56,
                "factor_value": 206.47,
            },
        ],
    }
    original = ToolMessage(
        content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        tool_call_id="toolu-factor",
        name="factors_compute",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "toolu-factor",
                    "name": "factors_compute",
                    "args": {
                        "symbols": ["MRVL"],
                        "market": "us",
                        "formula": "MA(CLOSE, 20)",
                    },
                }
            },
        )(),
    )
    middleware = CitationEvidenceCompactionMiddleware()
    result = await middleware.awrap_tool_call(request, handler)

    compacted = json.loads(result.content[0]["text"])
    assert compacted["datas"] == payload["datas"]
    hint = compacted["_valuz_evidence_hint"]
    assert hint["contentRoot"] == "/datas"
    assert hint["metricMode"] == "field-map"
    assert hint["metricFields"] == {"/factor_value": "moving_average_20"}
    private_content = citation_artifact_content(result)
    assert private_content is not None
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_projection(
            result.content,
            private_content,
            tool_name="factors_compute",
            trusted_private=True,
        )
        == 1
    )
    assert registry.collection_count == 1
    factor = registry.materialize_reference(
        hint["collectionHandle"],
        "#/datas/0/factor_value",
    )
    assert factor is not None
    assert factor.evidence["entityId"] == "MRVL"
    assert factor.evidence["asOf"] == "2026-08-03"
    assert factor.evidence["metric"] == "moving_average_20"
    assert factor.evidence["value"] == 203.69

    async def second_handler(_request: ToolCallRequest) -> ToolMessage:
        return result

    repeated = await middleware.awrap_tool_call(request, second_handler)
    repeated_hint = json.loads(repeated.content[0]["text"])["_valuz_evidence_hint"]
    assert repeated_hint["collectionHandle"] == hint["collectionHandle"]
    assert citation_artifact_content(repeated) == private_content


def test_factor_formula_metric_mapping_is_bounded_and_deterministic() -> None:
    assert _canonical_metric_for_factor_formula("MA(CLOSE, 20)") == "moving_average_20"
    assert _canonical_metric_for_factor_formula("ma(close,250)") == "moving_average_250"
    assert _canonical_metric_for_factor_formula("PS_TTM()") == "price_to_sales_ttm"
    assert _canonical_metric_for_factor_formula("PS()") == "price_to_sales"
    assert _canonical_metric_for_factor_formula("RSI(14)") == "rsi_14"
    assert _canonical_metric_for_factor_formula("CUSTOM(USER_INPUT)") is None


async def test_market_quote_items_get_one_addressable_collection() -> None:
    payload = {
        "as_of": "2026-08-03",
        "coverage": {"start": "2026-07-31", "end": "2026-08-03", "rows": 2},
        "status": 200,
        "data": {
            "items": [
                {
                    "symbol": "MRVL",
                    "stock_name": "Marvell Technology",
                    "stock_price": 187.56,
                    "stock_change_percent": -0.0321,
                    "date": "2026-07-31T00:00:00-04:00",
                },
                {
                    "symbol": "MRVL",
                    "stock_name": "Marvell Technology",
                    "stock_price": 193.775,
                    "stock_change_percent": 0.0331,
                    "date": "2026-08-03T00:00:00-04:00",
                },
            ]
        },
    }
    original = ToolMessage(
        content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        tool_call_id="toolu-quote",
        name="stock_quote",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "toolu-quote",
                    "name": "stock_quote",
                    "args": {"symbol": "MRVL"},
                }
            },
        )(),
    )
    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(request, handler)

    compacted = json.loads(result.content[0]["text"])
    hint = compacted["_valuz_evidence_hint"]
    assert hint["contentRoot"] == "/data"
    private_content = citation_artifact_content(result)
    assert private_content is not None
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_projection(
            result.content,
            private_content,
            tool_name="stock_quote",
            trusted_private=True,
        )
        == 1
    )
    quote = registry.materialize_reference(
        hint["collectionHandle"],
        "#/data/items/1/stock_price",
    )
    change = registry.materialize_reference(
        hint["collectionHandle"],
        "#/data/items/1/stock_change_percent",
    )
    assert quote is not None
    assert quote.evidence["entityId"] == "MRVL"
    assert quote.evidence["asOf"] == "2026-08-03T00:00:00-04:00"
    assert quote.evidence["value"] == 193.775
    assert change is not None
    assert change.evidence["value"] == 3.31
    assert change.evidence["unit"] == "percent"


async def test_grep_over_raw_document_returns_traceable_focused_evidence() -> None:
    middleware = CitationEvidenceCompactionMiddleware()
    raw_payload = {
        "doc_id": "doc-annual-report",
        "title": "Annual report",
        "url": "https://reportify.cn/financials/doc-annual-report",
        "file_url": "https://files.example/report.pdf",
        "content": (
            "主营业务分销售模式\n"
            "| 渠道 | 营业收入（元） | 同比 |\n"
            "| 直销 | 74,843,327,030.79 | 11.32% |\n"
            "| 批发代理 | 95,768,511,021.23 | 19.73% |\n"
        ),
    }
    raw_result = ToolMessage(
        content=[{"type": "text", "text": json.dumps(raw_payload, ensure_ascii=False)}],
        tool_call_id="toolu-raw-document",
        name="document_raw_content",
    )

    async def raw_handler(_request: ToolCallRequest) -> ToolMessage:
        return raw_result

    raw_request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "toolu-raw-document",
                    "name": "document_raw_content",
                    "args": {"doc_id": "doc-annual-report"},
                }
            },
        )(),
    )
    await middleware.awrap_tool_call(raw_request, raw_handler)

    grep_result = ToolMessage(
        content=("/large_tool_results/toolu-raw-document:\n  1: stored document matched pattern"),
        tool_call_id="toolu-grep",
        name="grep",
    )

    async def grep_handler(_request: ToolCallRequest) -> ToolMessage:
        return grep_result

    grep_request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "toolu-grep",
                    "name": "grep",
                    "args": {"pattern": "直销", "path": "/large_tool_results"},
                }
            },
        )(),
    )
    result = await middleware.awrap_tool_call(grep_request, grep_handler)

    visible = json.loads(str(result.content))
    assert "74,843,327,030.79" in visible["matches"]
    assert visible["_valuz_evidence"][0]["evidenceHandle"].startswith("ev_grep_")
    private_content = citation_artifact_content(result)
    assert private_content is not None
    registry = EvidenceRegistry()
    assert registry.register_tool_result(private_content, trusted_private=True) == 1


async def test_namespaced_grep_matches_pdf_line_breaks_in_cached_raw_document() -> None:
    middleware = CitationEvidenceCompactionMiddleware()
    raw_payload = {
        "doc_id": "doc-pdf-line-wrap",
        "title": "Wrapped annual report",
        "url": "https://reportify.cn/financials/doc-pdf-line-wrap",
        "file_url": "https://files.example/wrapped-report.pdf",
        "content": (
            "五、报告期内主要经营情况\n"
            "年度内公司实现营业总收入 1,741.44 亿元，同比增长 15.66%；归属\n"
            "于上市公司股东的净利润 862.28 亿元，同比增长 15.38%。\n"
        ),
    }
    raw_result = ToolMessage(
        content=[{"type": "text", "text": json.dumps(raw_payload, ensure_ascii=False)}],
        tool_call_id="toolu-wrapped-raw",
        name="mcp__reportify__document_raw_content",
    )

    async def raw_handler(_request: ToolCallRequest) -> ToolMessage:
        return raw_result

    def request(call_id: str, name: str, args: dict[str, Any]) -> Any:
        return cast(
            Any,
            type(
                "Request",
                (),
                {"tool_call": {"id": call_id, "name": name, "args": args}},
            )(),
        )

    await middleware.awrap_tool_call(
        request(
            "toolu-wrapped-raw",
            "mcp__reportify__document_raw_content",
            {"doc_id": "doc-pdf-line-wrap"},
        ),
        raw_handler,
    )
    grep_result = ToolMessage(
        content="No matches found",
        tool_call_id="toolu-wrapped-grep",
        name="builtin__grep",
    )

    async def grep_handler(_request: ToolCallRequest) -> ToolMessage:
        return grep_result

    focused = await middleware.awrap_tool_call(
        request(
            "toolu-wrapped-grep",
            "builtin__grep",
            {
                "pattern": "归属于上市公司股东的净利润",
                "path": "/large_tool_results/toolu-wrapped-raw",
            },
        ),
        grep_handler,
    )

    visible = json.loads(str(focused.content))
    assert "862.28" in visible["matches"]
    private_content = citation_artifact_content(focused)
    assert private_content is not None
    evidence = json.loads(private_content)["_valuz_evidence"][0]
    assert "归属\n于上市公司股东的净利润" in evidence["evidence"]["quote"]


async def test_grep_reuses_unique_registered_chunk_instead_of_external_locator() -> None:
    middleware = CitationEvidenceCompactionMiddleware()

    def request(call_id: str, name: str, args: dict[str, Any]) -> Any:
        return cast(
            Any,
            type(
                "Request",
                (),
                {"tool_call": {"id": call_id, "name": name, "args": args}},
            )(),
        )

    indexed_payload = {
        "chunks": [
            {
                "id": "chunk-page-8",
                "content": (
                    "年度内公司实现营业总收入 1,741.44 亿元，同比增长 15.66%；"
                    "归属于上市公司股东的净利润 862.28 亿元，同比增长 15.38%。"
                ),
                "metadata": {"document_page": 8},
                "doc": {
                    "doc_id": "doc-located",
                    "title": "Located annual report",
                    "category": "financials",
                    "url": "https://reportify.cn/financials/doc-located",
                },
            }
        ]
    }
    indexed_result = ToolMessage(
        content=json.dumps(indexed_payload, ensure_ascii=False),
        tool_call_id="toolu-indexed",
        name="kb_search",
    )

    async def indexed_handler(_request: ToolCallRequest) -> ToolMessage:
        return indexed_result

    await middleware.awrap_tool_call(
        request("toolu-indexed", "kb_search", {"doc_id": "doc-located"}),
        indexed_handler,
    )

    raw_payload = {
        "doc_id": "doc-located",
        "title": "Located annual report",
        "url": "https://reportify.cn/financials/doc-located",
        "file_url": "https://files.example/located.pdf",
        "content": (
            "年度内公司实现营业总收入 1,741.44 亿元，同比增长 15.66%；归属\n"
            "于上市公司股东的净利润 862.28 亿元，同比增长 15.38%。"
        ),
    }
    raw_result = ToolMessage(
        content=json.dumps(raw_payload, ensure_ascii=False),
        tool_call_id="toolu-located-raw",
        name="document_raw_content",
    )

    async def raw_handler(_request: ToolCallRequest) -> ToolMessage:
        return raw_result

    await middleware.awrap_tool_call(
        request(
            "toolu-located-raw",
            "document_raw_content",
            {"doc_id": "doc-located"},
        ),
        raw_handler,
    )
    grep_result = ToolMessage(
        content="No matches found",
        tool_call_id="toolu-located-grep",
        name="grep",
    )

    async def grep_handler(_request: ToolCallRequest) -> ToolMessage:
        return grep_result

    focused = await middleware.awrap_tool_call(
        request(
            "toolu-located-grep",
            "grep",
            {
                "pattern": "归属于上市公司股东的净利润",
                "path": "/large_tool_results/toolu-located-raw",
            },
        ),
        grep_handler,
    )

    private_content = citation_artifact_content(focused)
    assert private_content is not None
    evidence = json.loads(private_content)["_valuz_evidence"][0]
    assert evidence["evidenceHandle"].startswith("ev_chunk_")
    assert evidence["locator"] == {
        "kind": "pdf",
        "page": 8,
        "chunkId": "chunk-page-8",
        "quote": {"exact": indexed_payload["chunks"][0]["content"]},
    }


async def test_non_citable_raw_metadata_still_feeds_focused_grep_evidence() -> None:
    middleware = CitationEvidenceCompactionMiddleware()

    def metadata_result(
        *,
        tool_name: str,
        payload: dict[str, Any],
        resources: list[dict[str, Any]],
        call_id: str,
    ) -> ToolMessage:
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        descriptor = {
            "version": 1,
            "provider": {"id": "reportify"},
            "operation": {"toolName": tool_name},
            "result": {
                "target": "structuredContent",
                "hash": {"algorithm": "sha256", "value": digest},
                "capturedAt": "2026-08-03T00:00:00Z",
            },
            "resources": resources,
        }
        return ToolMessage(
            content=[{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            artifact={
                "structured_content": {
                    MCP_SOURCE_TRANSPORT_KEY: {
                        "descriptor": descriptor,
                        "hasStructuredContent": True,
                        "structuredContent": payload,
                    }
                }
            },
            tool_call_id=call_id,
            name=tool_name,
        )

    discovery_payload = {"docs": [{"doc_id": "doc-annual-report", "title": "Annual report"}]}
    discovery = metadata_result(
        tool_name="reports_search",
        payload=discovery_payload,
        resources=[
            {
                "resourceId": "search",
                "kind": "document-discovery",
                "authority": "discovery-only",
                "rootPointer": "",
                "itemsPointer": "/docs",
                "mapping": {"sourceId": "/doc_id", "title": "/title"},
            }
        ],
        call_id="toolu-discovery",
    )
    raw_payload = {
        "doc_id": "doc-annual-report",
        "original_url": "https://reportify.cn/financials/doc-annual-report",
        "content": (
            "主营业务分销售模式\n"
            "| 渠道 | 营业收入（元） | 同比 |\n"
            "| 直销 | 74,843,327,030.79 | 11.32% |\n"
            "| 批发代理 | 95,768,511,021.23 | 19.73% |\n"
        ),
    }
    raw = metadata_result(
        tool_name="document_raw_content",
        payload=raw_payload,
        resources=[
            {
                "resourceId": "document-raw-content",
                "kind": "operational",
                "authority": "non-citable",
                "rootPointer": "",
            }
        ],
        call_id="toolu-raw-document",
    )

    async def discovery_handler(_request: ToolCallRequest) -> ToolMessage:
        return discovery

    async def raw_handler(_request: ToolCallRequest) -> ToolMessage:
        return raw

    def request(call_id: str, name: str, args: dict[str, Any]) -> Any:
        return cast(
            Any,
            type(
                "Request",
                (),
                {"tool_call": {"id": call_id, "name": name, "args": args}},
            )(),
        )

    await middleware.awrap_tool_call(
        request("toolu-discovery", "reports_search", {}),
        discovery_handler,
    )
    raw_result = await middleware.awrap_tool_call(
        request(
            "toolu-raw-document",
            "document_raw_content",
            {"doc_id": "doc-annual-report"},
        ),
        raw_handler,
    )
    assert citation_artifact_content(raw_result) is None
    assert "evidenceHandle" not in str(raw_result.content)

    grep_result = ToolMessage(
        content="/large_tool_results/toolu-raw-document:\n  1: stored document matched pattern",
        tool_call_id="toolu-grep",
        name="grep",
    )

    async def grep_handler(_request: ToolCallRequest) -> ToolMessage:
        return grep_result

    focused = await middleware.awrap_tool_call(
        request(
            "toolu-grep",
            "grep",
            {"pattern": "直销", "path": "/large_tool_results"},
        ),
        grep_handler,
    )
    private_content = citation_artifact_content(focused)
    assert private_content is not None
    evidence = json.loads(private_content)["_valuz_evidence"][0]
    assert evidence["source"]["title"] == "Annual report"
    assert "74,843,327,030.79" in evidence["evidence"]["quote"]
    assert evidence["locator"]["kind"] == "external"


async def test_repeated_document_not_found_is_returned_without_host_short_circuit() -> None:
    middleware = ToolErrorTolerantMiddleware()
    calls = 0

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        nonlocal calls
        calls += 1
        raise RuntimeError("HTTP error 404: Report is not found")

    request = cast(
        Any,
        type(
            "Request",
            (),
            {"tool_call": {"id": "call-1", "name": "document_fetch"}},
        )(),
    )
    first = await middleware.awrap_tool_call(request, handler)
    second = await middleware.awrap_tool_call(request, handler)
    third = await middleware.awrap_tool_call(request, handler)

    assert isinstance(first, ToolMessage) and first.status == "error"
    assert isinstance(second, ToolMessage) and second.status == "error"
    assert isinstance(third, ToolMessage) and third.status == "error"
    assert calls == 3
    assert "HTTP error 404" in str(third.content)

"""DeepAgents citation evidence compaction tests."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from src.core.citation import EvidenceRegistry
from src.core.citation_research_budget import (
    CitationResearchBudget,
    is_stable_general_knowledge_query,
)
from src.core.mcp_source_metadata import MCP_SOURCE_TRANSPORT_KEY
from src.runtimes.deepagents.middleware import (
    CitationEvidenceCompactionMiddleware,
    ResearchToolBudgetMiddleware,
    ToolErrorTolerantMiddleware,
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


async def test_singular_kb_document_scope_is_normalized_before_provider_call() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(None, None)
    handled_args: list[dict[str, Any]] = []

    class Request:
        def __init__(self, tool_call: dict[str, Any]) -> None:
            self.tool_call = tool_call

        def override(self, **updates: Any) -> Request:
            return Request(updates.get("tool_call", self.tool_call))

    async def handler(request: ToolCallRequest) -> ToolMessage:
        handled_args.append(dict(request.tool_call["args"]))
        return ToolMessage(
            content=json.dumps({"chunks": []}),
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    await middleware.awrap_tool_call(
        cast(
            ToolCallRequest,
            Request(
                {
                    "id": "kb-singular",
                    "name": "kb_search",
                    "args": {"doc_id": "sk-q2", "query": "operating cash flow"},
                }
            ),
        ),
        handler,
    )

    assert handled_args == [{"query": "operating cash flow", "doc_ids": ["sk-q2"]}]


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


async def test_reportify_discovery_metadata_exposes_only_citable_metadata_collection() -> None:
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
    assert "evidenceHandle" not in visible_payload["docs"][0]
    hint = visible_payload["_valuz_evidence_hint"]
    assert hint["collectionHandle"].startswith("evc_projection_")
    assert hint["allowedItemPaths"] == ["/doc_id", "/title"]
    assert visible_payload["_valuz_discovery"]["citationEvidence"] == (
        "original-indexed-chunk-required"
    )
    assert visible_payload["_valuz_discovery"]["originalDocumentPreferred"] is True
    private = citation_artifact_content(result)
    assert private is not None
    descriptor = json.loads(private)["_valuz_evidence"][0]
    assert descriptor["addressing"]["allowedItemPaths"] == ["/doc_id", "/title"]
    assert descriptor["collectionHandle"] == hint["collectionHandle"]
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_projection(
            result.content,
            private,
            tool_name="reports_search",
            trusted_private=True,
        )
        == 1
    )
    assert registry.rejected_count == 0
    assert (
        registry.materialize_reference(
            hint["collectionHandle"],
            "#/docs/3/title",
        )
        is not None
    )


async def test_discovery_search_summaries_are_bounded_for_model_history() -> None:
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

    result = await CitationEvidenceCompactionMiddleware().awrap_tool_call(
        cast(Any, object()),
        handler,
    )

    assert isinstance(result, ToolMessage)
    compacted = json.loads(result.content[0]["text"])
    assert len(compacted["docs"]) == 4
    assert len(compacted["docs"][0]["summary"]) <= 361
    assert compacted["docs"][0]["evidenceHandle"].startswith("ev_summary_")
    assert compacted["_valuz_discovery"] == {
        "returned": 12,
        "shown": 4,
        "filteredOut": 0,
        "duplicatesRemoved": 0,
        "summariesTruncated": True,
        "citationEvidence": "summary-fallback",
        "originalDocumentPreferred": True,
    }
    private_content = citation_artifact_content(result)
    assert private_content is not None
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_result(
            private_content,
            tool_name="news_search",
            trusted_private=True,
        )
        == 4
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
    assert compacted["_valuz_discovery"]["citationEvidence"] == "summary-fallback"
    assert len(compacted["docs"][0]["summary"]) <= 361
    assert citation_artifact_content(result) is not None


async def test_transcript_discovery_excludes_secondary_company_mentions() -> None:
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
    assert [doc["doc_id"] for doc in compacted["docs"]] == ["msft-q1"]
    assert "summary" not in compacted["docs"][0]
    assert compacted["_valuz_discovery"]["filteredOut"] == 1
    assert compacted["_valuz_discovery"]["duplicatesRemoved"] == 0
    assert compacted["_valuz_discovery"]["citationEvidence"] == ("original-indexed-chunk-required")
    assert citation_artifact_content(result) is None


async def test_transcript_discovery_collapses_duplicate_issuer_period_rows() -> None:
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
    assert [doc["doc_id"] for doc in compacted["docs"]] == ["msft-q1-first"]
    assert "summary" not in compacted["docs"][0]
    assert compacted["_valuz_discovery"]["filteredOut"] == 0
    assert compacted["_valuz_discovery"]["duplicatesRemoved"] == 1
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
    assert inner["_valuz_discovery"]["citationEvidence"] == "summary-fallback"
    assert len(inner["docs"][0]["summary"]) <= 361
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


async def test_document_discovery_calls_are_bounded_per_agent_turn() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(None, None)
    calls = 0

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal calls
        calls += 1
        return ToolMessage(
            content="candidate",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    results: list[ToolMessage] = []
    for index in range(7):
        request = cast(
            Any,
            type(
                "Request",
                (),
                {"tool_call": {"id": f"call-{index}", "name": "news_search"}},
            )(),
        )
        result = await middleware.awrap_tool_call(request, handler)
        assert isinstance(result, ToolMessage)
        results.append(result)

    assert calls == 6
    assert results[-1].status == "error"
    assert "budget for this turn is exhausted" in str(results[-1].content)

    middleware.before_agent(None, None)
    reset_request = cast(
        Any,
        type(
            "Request",
            (),
            {"tool_call": {"id": "call-reset", "name": "webpage_search"}},
        )(),
    )
    reset_result = await middleware.awrap_tool_call(reset_request, handler)
    assert isinstance(reset_result, ToolMessage)
    assert reset_result.status != "error"
    assert calls == 7


async def test_annual_statement_limit_reaches_oldest_requested_year() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(
        {"messages": [HumanMessage(content="查询 2024 年和 2023 年营业收入并计算同比增速")]},
        None,
    )
    seen_args: list[dict[str, Any]] = []

    class Request:
        def __init__(self, tool_call: dict[str, Any]) -> None:
            self.tool_call = tool_call

        def override(self, **updates: Any) -> Request:
            return Request(updates.get("tool_call", self.tool_call))

    async def handler(request: ToolCallRequest) -> ToolMessage:
        seen_args.append(cast(dict[str, Any], request.tool_call["args"]))
        return ToolMessage(
            content="statement",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    await middleware.awrap_tool_call(
        cast(
            ToolCallRequest,
            Request(
                {
                    "id": "annual-statement",
                    "name": "income_statement",
                    "args": {"symbol": "600519", "period": "annual", "limit": 2},
                }
            ),
        ),
        handler,
    )

    assert len(seen_args) == 1
    assert seen_args[0]["limit"] >= 3


def test_indexed_document_search_has_an_independent_bounded_budget() -> None:
    budget = CitationResearchBudget()

    for _ in range(4):
        assert budget.allow_discovery().allowed is True
    for index in range(6):
        assert budget.allow_indexed_document_search([f"transcript-{index}"]).allowed is True

    assert budget.discovery_calls == 4
    exhausted = budget.allow_indexed_document_search(["transcript-6"])
    assert exhausted.allowed is False
    assert exhausted.code == "indexed-document-search-budget-exhausted"
    assert budget.has_research_activity is True


async def test_complete_document_blocks_redundant_raw_reload_and_refetch() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(None, None)
    calls: list[str] = []

    async def handler(request: ToolCallRequest) -> ToolMessage:
        calls.append(request.tool_call["name"])
        return ToolMessage(
            content=json.dumps(
                {
                    "doc_id": "doc-complete",
                    "total_chunks": 44,
                    "chunk_offset": 0,
                    "next_chunk_offset": None,
                    "_valuz_evidence": [],
                }
            ),
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    fetch_request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "fetch-complete",
                    "name": "document_fetch",
                    "args": {"doc_id": "doc-complete", "chunk_limit": 60},
                }
            },
        )(),
    )
    fetch_result = await middleware.awrap_tool_call(fetch_request, handler)
    assert isinstance(fetch_result, ToolMessage)
    assert fetch_result.status != "error"
    assert isinstance(fetch_result.content, list)
    coverage_note = str(fetch_result.content[-1]["text"])
    assert "reached this document's final chunk" in coverage_note
    assert "do not mention them" in coverage_note

    raw_request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "raw-redundant",
                    "name": "document_raw_content",
                    "args": {"doc_id": "doc-complete"},
                }
            },
        )(),
    )
    raw_result = await middleware.awrap_tool_call(raw_request, handler)
    assert isinstance(raw_result, ToolMessage)
    assert raw_result.status == "error"
    assert "already read through its final indexed chunk" in str(raw_result.content)

    refetch_request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "fetch-redundant",
                    "name": "document_fetch",
                    "args": {"doc_id": "doc-complete", "chunk_limit": 60},
                }
            },
        )(),
    )
    refetch_result = await middleware.awrap_tool_call(refetch_request, handler)
    assert isinstance(refetch_result, ToolMessage)
    assert refetch_result.status == "error"
    assert calls == ["document_fetch"]


async def test_complete_document_registers_document_level_coverage_evidence() -> None:
    full_payload = {
        "doc_id": "doc-complete",
        "total_chunks": 44,
        "chunk_offset": 0,
        "next_chunk_offset": None,
        "_valuz_evidence": [
            {
                "evidenceHandle": "ev_text_coverage_12345678",
                "source": {
                    "sourceId": "doc-complete",
                    "documentId": "doc-complete",
                    "documentVersion": "sha256:complete",
                    "providerId": "valuz-search",
                    "sourceType": "document",
                    "title": "Complete transcript",
                    "retrievedAt": "2026-08-02T08:00:00Z",
                },
                "evidence": {
                    "kind": "text",
                    "quote": "The final indexed paragraph.",
                    "snippet": "The final indexed paragraph.",
                    "capturedAt": "2026-08-02T08:00:00Z",
                },
            }
        ],
    }
    original = ToolMessage(
        content=[{"type": "text", "text": json.dumps(full_payload)}],
        tool_call_id="fetch-complete",
        name="document_fetch",
    )
    compaction = CitationEvidenceCompactionMiddleware()
    budget = ResearchToolBudgetMiddleware()
    budget.before_agent(None, None)
    request = cast(
        Any,
        type(
            "Request",
            (),
            {
                "tool_call": {
                    "id": "fetch-complete",
                    "name": "document_fetch",
                    "args": {"doc_id": "doc-complete", "chunk_limit": 60},
                }
            },
        )(),
    )

    async def original_handler(_request: ToolCallRequest) -> ToolMessage:
        return original

    async def budget_handler(budget_request: ToolCallRequest) -> ToolMessage:
        result = await budget.awrap_tool_call(budget_request, original_handler)
        assert isinstance(result, ToolMessage)
        return result

    # Middleware wraps in declared order: compaction receives the result after
    # the research budget marks the final document window complete.
    result = await compaction.awrap_tool_call(request, budget_handler)

    assert isinstance(result, ToolMessage)
    private = citation_artifact_content(result)
    assert private is not None
    payload = json.loads(private)
    if isinstance(payload, list):
        payload = json.loads(payload[0]["text"])
    coverage = next(
        item
        for item in payload["_valuz_evidence"]
        if item.get("evidence", {}).get("field") == "document_coverage_complete"
    )
    assert coverage["evidence"]["value"] is True
    assert coverage["evidence"]["basis"] == "full-document"
    assert f"evidence://{coverage['evidenceHandle']}" in str(result.content[-1]["text"])


async def test_hidden_repair_with_candidate_catalog_cannot_restart_research() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(
        {
            "messages": [
                HumanMessage(
                    content=(
                        "Repair now.\n\nRestricted repair context (JSON):\n"
                        '{"candidateEvidence":[{"evidenceHandle":"ev_test_12345678"}]}'
                    )
                )
            ]
        },
        None,
    )
    calls = 0

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal calls
        calls += 1
        return ToolMessage(
            content="unexpected",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    request = cast(
        Any,
        type(
            "Request",
            (),
            {"tool_call": {"id": "repair-search", "name": "conferences_search"}},
        )(),
    )
    result = await middleware.awrap_tool_call(request, handler)

    assert calls == 0
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "already has a registered evidence catalogue" in str(result.content)


async def test_citation_mode_keeps_evidence_retrieval_in_lead_agent() -> None:
    middleware = ResearchToolBudgetMiddleware(lead_owned_evidence=True)
    middleware.before_agent(None, None)
    calls = 0

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal calls
        calls += 1
        return ToolMessage(
            content="unexpected delegated result",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    request = cast(
        Any,
        type(
            "Request",
            (),
            {"tool_call": {"id": "delegate-1", "name": "task", "args": {}}},
        )(),
    )
    result = await middleware.awrap_tool_call(request, handler)

    assert calls == 0
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "lead agent to own the evidence catalogue" in str(result.content)


async def test_company_lookup_does_not_consume_document_discovery_budget() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(None, None)
    calls = 0

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal calls
        calls += 1
        return ToolMessage(
            content="company",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    for index in range(10):
        request = cast(
            Any,
            type(
                "Request",
                (),
                {"tool_call": {"id": f"company-{index}", "name": "company_search"}},
            )(),
        )
        result = await middleware.awrap_tool_call(request, handler)
        assert isinstance(result, ToolMessage)
        assert result.status != "error"

    assert calls == 10


async def test_broad_transcript_discovery_expands_candidate_window() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(None, None)
    seen_args: list[dict[str, Any]] = []

    class Request:
        def __init__(self, tool_call: dict[str, Any]) -> None:
            self.tool_call = tool_call

        def override(self, **updates: Any) -> Request:
            return Request(updates.get("tool_call", self.tool_call))

    async def handler(request: ToolCallRequest) -> ToolMessage:
        seen_args.append(cast(dict[str, Any], request.tool_call["args"]))
        return ToolMessage(
            content=json.dumps({"docs": []}),
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    await middleware.awrap_tool_call(
        cast(
            ToolCallRequest,
            Request(
                {
                    "id": "broad-transcripts",
                    "name": "conferences_search",
                    "args": {"symbols": ["US:MSFT"], "num": 8},
                }
            ),
        ),
        handler,
    )

    assert seen_args == [{"symbols": ["US:MSFT"], "num": 20}]


async def test_document_fetch_chunk_size_and_call_count_are_bounded() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(None, None)
    seen_limits: list[int] = []

    class Request:
        def __init__(self, tool_call: dict[str, Any]) -> None:
            self.tool_call = tool_call

        def override(self, **updates: Any) -> Request:
            return Request(updates.get("tool_call", self.tool_call))

    async def handler(request: ToolCallRequest) -> ToolMessage:
        seen_limits.append(cast(dict[str, Any], request.tool_call["args"])["chunk_limit"])
        return ToolMessage(
            content="document",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    results: list[ToolMessage] = []
    for index in range(4):
        request = cast(
            ToolCallRequest,
            Request(
                {
                    "id": f"document-{index}",
                    "name": "document_fetch",
                    "args": {"doc_id": f"doc-{index}", "chunk_limit": 100},
                }
            ),
        )
        result = await middleware.awrap_tool_call(request, handler)
        assert isinstance(result, ToolMessage)
        results.append(result)

    assert seen_limits == [60] * 3
    assert results[-1].status == "error"
    assert "fetch budget for this turn is exhausted" in str(results[-1].content)


async def test_document_fetch_blocks_repeated_distant_offset_guessing() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(None, None)
    handled_offsets: list[int] = []

    class Request:
        def __init__(self, tool_call: dict[str, Any]) -> None:
            self.tool_call = tool_call

        def override(self, **updates: Any) -> Request:
            return Request(updates.get("tool_call", self.tool_call))

    async def handler(request: ToolCallRequest) -> ToolMessage:
        handled_offsets.append(cast(dict[str, Any], request.tool_call["args"])["chunk_offset"])
        return ToolMessage(
            content="document",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    results: list[ToolMessage] = []
    for index, offset in enumerate((0, 80, 160)):
        result = await middleware.awrap_tool_call(
            cast(
                ToolCallRequest,
                Request(
                    {
                        "id": f"document-{index}",
                        "name": "document_fetch",
                        "args": {
                            "doc_id": "doc-1",
                            "chunk_offset": offset,
                            "chunk_limit": 12,
                        },
                    }
                ),
            ),
            handler,
        )
        assert isinstance(result, ToolMessage)
        results.append(result)

    assert handled_offsets == [0]
    assert results[-1].status == "error"
    assert "full-text/raw-content" in str(results[-1].content)


async def test_transcript_uses_one_indexed_search_and_blocks_original_reads() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(None, None)
    handled_tools: list[str] = []
    handled_args: list[dict[str, Any]] = []

    class Request:
        def __init__(self, tool_call: dict[str, Any]) -> None:
            self.tool_call = tool_call

        def override(self, **updates: Any) -> Request:
            return Request(updates.get("tool_call", self.tool_call))

    async def handler(request: ToolCallRequest) -> ToolMessage:
        handled_tools.append(str(request.tool_call["name"]))
        handled_args.append(cast(dict[str, Any], request.tool_call["args"]))
        return ToolMessage(
            content="document",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    async def discovery_handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=json.dumps({"docs": [{"doc_id": "doc-1"}]}),
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    await middleware.awrap_tool_call(
        cast(
            ToolCallRequest,
            Request(
                {
                    "id": "discovery-1",
                    "name": "conferences_search",
                    "args": {"symbols": ["US:MSFT"]},
                }
            ),
        ),
        discovery_handler,
    )

    raw_result = await middleware.awrap_tool_call(
        cast(
            ToolCallRequest,
            Request(
                {
                    "id": "raw-1",
                    "name": "document_raw_content",
                    "args": {"doc_id": "doc-1"},
                }
            ),
        ),
        handler,
    )
    first_search = await middleware.awrap_tool_call(
        cast(
            ToolCallRequest,
            Request(
                {
                    "id": "kb-1",
                    "name": "kb_search",
                    "args": {"doc_ids": ["doc-1"], "query": "AI demand capex"},
                }
            ),
        ),
        handler,
    )
    repeated_search = await middleware.awrap_tool_call(
        cast(
            ToolCallRequest,
            Request(
                {
                    "id": "kb-2",
                    "name": "kb_search",
                    "args": {"doc_ids": ["doc-1"], "query": "supply constraint"},
                }
            ),
        ),
        handler,
    )
    fetch_result = await middleware.awrap_tool_call(
        cast(
            ToolCallRequest,
            Request(
                {
                    "id": "fetch-1",
                    "name": "document_fetch",
                    "args": {"doc_id": "doc-1", "chunk_limit": 60},
                }
            ),
        ),
        handler,
    )

    assert raw_result.status == "error"
    assert "exactly one kb_search" in str(raw_result.content)
    assert first_search.status != "error"
    assert repeated_search.status == "error"
    assert "already had its one targeted indexed search" in str(repeated_search.content)
    assert fetch_result.status == "error"
    assert "Use the returned chunks" in str(fetch_result.content)
    assert handled_tools == ["kb_search"]
    assert handled_args == [{"doc_ids": ["doc-1"], "query": "AI demand capex", "num": 10}]


async def test_filing_fetch_blocks_adjacent_sequential_window() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(None, None)

    class Request:
        def __init__(self, tool_call: dict[str, Any]) -> None:
            self.tool_call = tool_call

        def override(self, **updates: Any) -> Request:
            return Request(updates.get("tool_call", self.tool_call))

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="document",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )

    first = await middleware.awrap_tool_call(
        cast(
            ToolCallRequest,
            Request(
                {
                    "id": "filing-0",
                    "name": "document_fetch",
                    "args": {"doc_id": "filing-1", "chunk_offset": 0},
                }
            ),
        ),
        handler,
    )
    second = await middleware.awrap_tool_call(
        cast(
            ToolCallRequest,
            Request(
                {
                    "id": "filing-60",
                    "name": "document_fetch",
                    "args": {"doc_id": "filing-1", "chunk_offset": 60},
                }
            ),
        ),
        handler,
    )

    assert first.status != "error"
    assert second.status == "error"
    assert "Do not page sequentially" in str(second.content)


async def test_research_model_loop_is_bounded_after_discovery() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(None, None)
    middleware._research_budget.discovery_calls = 1
    calls = 0

    seen_tools: list[list[Any] | None] = []

    async def handler(request: Any) -> AIMessage:
        nonlocal calls
        calls += 1
        seen_tools.append(getattr(request, "tools", None))
        return AIMessage(content="最终回答")

    class Request:
        def __init__(
            self,
            *,
            messages: list[Any] | None = None,
            tools: list[Any] | None = None,
            tool_choice: Any = "auto",
        ) -> None:
            self.messages = messages or [HumanMessage(content="请检索并回答")]
            self.state = {"messages": self.messages}
            self.tools = tools
            self.tool_choice = tool_choice

        def override(self, **updates: Any) -> Request:
            return Request(
                messages=updates.get("messages", self.messages),
                tools=updates.get("tools", self.tools),
                tool_choice=updates.get("tool_choice", self.tool_choice),
            )

    request = Request()
    for _ in range(10):
        await middleware.awrap_model_call(request, handler)
    capped = await middleware.awrap_model_call(request, handler)
    second_capped = await middleware.awrap_model_call(request, handler)
    fallback = await middleware.awrap_model_call(request, handler)

    assert calls == 12
    assert isinstance(capped, AIMessage)
    assert capped.content == "最终回答"
    assert isinstance(second_capped, AIMessage)
    assert second_capped.content == "最终回答"
    assert isinstance(fallback, AIMessage)
    assert "缩小查询范围" in str(fallback.content)
    assert seen_tools[-2:] == [[], []]


async def test_non_research_model_loop_is_not_bounded_by_research_budget() -> None:
    middleware = ResearchToolBudgetMiddleware()
    middleware.before_agent(None, None)
    calls = 0

    async def handler(_request: Any) -> object:
        nonlocal calls
        calls += 1
        return object()

    for _ in range(12):
        await middleware.awrap_model_call(cast(Any, object()), handler)

    assert calls == 12


def test_stable_general_knowledge_scope_is_conservative() -> None:
    assert is_stable_general_knowledge_query("ROE 是什么意思？计算公式是什么？请用通俗语言解释。")
    assert is_stable_general_knowledge_query(
        "What is free cash flow? Explain the formula in plain language."
    )
    assert is_stable_general_knowledge_query(
        "ROE 是什么意思？为什么银行和制造业不能直接用同一个 ROE 阈值比较？"
        "用通俗语言回答，不需要查询具体公司数据。"
    )
    assert not is_stable_general_knowledge_query("请查询贵州茅台 2024 年 ROE 并引用年报。")
    assert not is_stable_general_knowledge_query(
        "What is Microsoft's current ROE? Cite the latest filing."
    )


async def test_stable_general_knowledge_turn_disables_tools_for_model() -> None:
    middleware = ResearchToolBudgetMiddleware()
    state = {
        "messages": [HumanMessage(content="ROE 是什么意思？计算公式是什么？请用通俗语言解释。")]
    }
    middleware.before_agent(state, None)
    seen: dict[str, Any] = {}

    async def handler(request: Any) -> AIMessage:
        seen["tools"] = request.tools
        seen["tool_choice"] = request.tool_choice
        seen["last_message"] = request.messages[-1].content
        return AIMessage(content="直接回答")

    class Request:
        def __init__(
            self,
            *,
            messages: list[Any] | None = None,
            tools: list[Any] | None = None,
            tool_choice: Any = "auto",
        ) -> None:
            self.messages = messages or list(state["messages"])
            self.tools = tools if tools is not None else [object()]
            self.tool_choice = tool_choice

        def override(self, **updates: Any) -> Request:
            return Request(
                messages=updates.get("messages", self.messages),
                tools=updates.get("tools", self.tools),
                tool_choice=updates.get("tool_choice", self.tool_choice),
            )

    result = await middleware.awrap_model_call(Request(), handler)

    assert result.content == "直接回答"
    assert seen["tools"] == []
    assert seen["tool_choice"] is None
    assert "without tools" in str(seen["last_message"])


async def test_repeated_document_not_found_is_short_circuited() -> None:
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
    assert calls == 2
    assert "Do not call document_fetch again" in str(third.content)

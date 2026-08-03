"""Claude Agent keeps full citation envelopes private from model history."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import hashlib
import json

import valuz_agent.boot.kernel  # noqa: F401
from claude_agent_sdk import ToolResultBlock
from claude_agent_sdk import UserMessage as SdkUserMessage

from src.core.agent_config import AgentConfig
from src.core.citation import EvidenceRegistry
from src.core.types import Session
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime


class _RecordingSink:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


def _raw_document_result() -> dict:
    return {
        "chunks": [
            {
                "content": "bulk transcript " * 1_000,
                "_valuz_evidence": {
                    "evidenceHandle": "ev_msft_q1_12345678",
                    "source": {
                        "sourceId": "msft-q1",
                        "providerId": "valuz-search",
                        "sourceType": "document",
                        "title": "Microsoft FY2026 Q1 transcript",
                        "retrievedAt": "2026-08-02T00:00:00Z",
                    },
                    "evidence": {
                        "kind": "text",
                        "quote": "Azure revenue grew 40% and 39% in constant currency.",
                        "snippet": "Azure revenue grew 40% and 39% in constant currency.",
                        "capturedAt": "2026-08-02T00:00:00Z",
                    },
                    "locator": {"kind": "pdf", "page": 5},
                },
            }
        ]
    }


def _raw_long_document_result() -> dict:
    evidence = []
    for index in range(80):
        evidence.append(
            {
                "evidenceHandle": f"ev_msft_q1_{index:08d}",
                "source": {
                    "sourceId": "msft-q1",
                    "providerId": "valuz-search",
                    "sourceType": "document",
                    "title": "Microsoft FY2026 Q1 transcript",
                    "retrievedAt": "2026-08-02T00:00:00Z",
                },
                "evidence": {
                    "kind": "text",
                    "quote": f"Chunk {index}: " + ("bounded excerpt " * 100),
                    "capturedAt": "2026-08-02T00:00:00Z",
                },
                "locator": {"kind": "chunk", "chunkId": str(index)},
            }
        )
    return {
        "content": "complete transcript " * 10_000,
        "_valuz_evidence": evidence,
    }


async def test_post_tool_hook_compacts_model_output_and_keeps_private_sidecar() -> None:
    sink = _RecordingSink()
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", sink)
    hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]
    raw = _raw_document_result()

    output = await hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {"doc_id": "msft-q1"},
            "tool_response": raw,
        },
        "tool-1",
        None,  # type: ignore[arg-type]
    )

    compacted = output["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert "bulk transcript" in json.dumps(compacted)
    envelope = compacted["chunks"][0]["_valuz_evidence"][0]
    assert envelope["evidenceHandle"] == "ev_msft_q1_12345678"
    assert envelope["excerpt"] == "Azure revenue grew 40% and 39% in constant currency."
    assert "bulk transcript" not in runtime._citation_tool_result_sidecars["tool-1"]
    assert "ev_msft_q1_12345678" in runtime._citation_tool_result_sidecars["tool-1"]


async def test_post_tool_hook_bounds_filing_evidence_but_keeps_private_sidecar() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]

    output = await hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {"doc_id": "msft-q1"},
            "tool_response": _raw_long_document_result(),
        },
        "tool-long",
        None,  # type: ignore[arg-type]
    )

    compacted = output["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert compacted["_valuz_compaction"] == {
        "evidenceReturned": 80,
        "evidenceShown": 80,
        "bulkTextOmitted": False,
        "modelContentPreserved": True,
    }
    assert compacted["_valuz_evidence"][-1]["evidenceHandle"] == "ev_msft_q1_00000079"
    assert len(compacted["_valuz_evidence"][-1]["excerpt"]) == 700
    assert "complete transcript" in json.dumps(compacted)
    assert "ev_msft_q1_00000079" in runtime._citation_tool_result_sidecars["tool-long"]


async def test_post_tool_hook_standardizes_indexed_chunks_into_evidence() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]

    output = await hook(
        {
            "tool_name": "mcp__valuz-search__kb_search",
            "tool_input": {"doc_ids": ["msft-q1"], "query": "AI demand"},
            "tool_response": {
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
            },
        },
        "kb-chunk",
        None,  # type: ignore[arg-type]
    )

    compacted = output["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert compacted["chunks"][0]["evidenceHandle"].startswith("ev_chunk_")
    assert compacted["chunks"][0]["content"] == ("Demand continues to exceed available supply.")
    sidecar = json.loads(runtime._citation_tool_result_sidecars["kb-chunk"])
    assert sidecar["_valuz_evidence"][0]["locator"]["page"] == 9


async def test_post_tool_hook_builds_document_evidence_from_mcp_result_meta() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]
    payload = {
        "doc_id": "msft-q1",
        "title": "Microsoft FY2026 Q1 transcript",
        "url": "https://reportify.cn/transcripts/msft-q1",
        "document_version": "v1",
        "chunks": [
            {
                "id": "chunk-1",
                "content": "Demand continues to exceed available supply.",
                "metadata": {"document_page": 9},
            }
        ],
        "total_chunks": 1,
        "chunk_offset": 0,
        "next_chunk_offset": None,
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    raw_result = {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "structuredContent": payload,
        "_meta": {
            "cn.valuz/citation-source": {
                "version": 1,
                "provider": {"id": "reportify", "name": "Reportify"},
                "operation": {"toolName": "document_fetch"},
                "result": {
                    "target": "structuredContent",
                    "hash": {"algorithm": "sha256", "value": digest},
                    "capturedAt": "2026-08-03T00:00:00Z",
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
                            "documentVersion": "/document_version",
                            "title": "/title",
                            "url": "/url",
                        },
                        "itemsPointer": "/chunks",
                        "mapping": {
                            "chunkId": "/id",
                            "text": "/content",
                            "page": "/metadata/document_page",
                        },
                    }
                ],
            }
        },
    }

    output = await hook(
        {
            "tool_name": "mcp__reportify__document_fetch",
            "tool_input": {"doc_id": "msft-q1"},
            "tool_response": raw_result,
        },
        "mcp-meta-document",
        None,  # type: ignore[arg-type]
    )

    compacted = output["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert compacted["chunks"][0]["content"] == payload["chunks"][0]["content"]
    assert compacted["chunks"][0]["evidenceHandle"].startswith("ev_mcp_")
    sidecar = json.loads(runtime._citation_tool_result_sidecars["mcp-meta-document"])
    assert sidecar["_valuz_evidence"][0]["source"]["providerId"] == "reportify"
    assert sidecar["_valuz_evidence"][0]["locator"]["page"] == 9


async def test_post_tool_hook_rebases_filtered_discovery_collection() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]
    payload = {
        "docs": [
            {
                "doc_id": f"report-{index}",
                "title": f"Kioxia report {index}",
                "summary": f"Research summary {index}",
            }
            for index in range(12)
        ]
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    raw_result = {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "structuredContent": payload,
        "_meta": {
            "cn.valuz/citation-source": {
                "version": 1,
                "provider": {"id": "reportify", "name": "Reportify"},
                "operation": {"toolName": "reports_search"},
                "result": {
                    "target": "structuredContent",
                    "hash": {"algorithm": "sha256", "value": digest},
                    "capturedAt": "2026-08-03T00:00:00Z",
                },
                "resources": [
                    {
                        "resourceId": "reports-search",
                        "kind": "document-discovery",
                        "authority": "discovery-only",
                        "rootPointer": "",
                        "itemsPointer": "/docs",
                        "mapping": {
                            "sourceId": "/doc_id",
                            "title": "/title",
                            "summary": "/summary",
                        },
                    }
                ],
            }
        },
    }

    output = await hook(
        {
            "tool_name": "mcp__reportify__reports_search",
            "tool_input": {"query": "Kioxia"},
            "tool_response": raw_result,
        },
        "mcp-meta-discovery",
        None,  # type: ignore[arg-type]
    )

    compacted = output["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert len(compacted["docs"]) == 4
    assert "_valuz_evidence" not in compacted
    hint = compacted["_valuz_evidence_hint"]
    private = runtime._citation_tool_result_sidecars["mcp-meta-discovery"]
    descriptor = json.loads(private)["_valuz_evidence"][0]
    assert descriptor["collectionHandle"] == hint["collectionHandle"]
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_projection(
            compacted,
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


async def test_post_tool_hook_keeps_larger_transcript_window_visible() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]
    await hook(
        {
            "tool_name": "mcp__valuz-search__conferences_search",
            "tool_input": {"symbols": ["US:MSFT"]},
            "tool_response": {"docs": [{"doc_id": "msft-q1", "category": "transcripts"}]},
        },
        "discovery",
        None,  # type: ignore[arg-type]
    )

    output = await hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {"doc_id": "msft-q1"},
            "tool_response": _raw_long_document_result(),
        },
        "tool-transcript",
        None,  # type: ignore[arg-type]
    )

    compacted = output["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert compacted["_valuz_compaction"]["evidenceShown"] == 80
    assert compacted["_valuz_evidence"][-1]["evidenceHandle"] == "ev_msft_q1_00000079"


async def test_post_tool_hook_keeps_late_prose_visible_within_long_chunk() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]
    raw = _raw_document_result()
    envelope = raw["chunks"][0]["_valuz_evidence"]
    envelope["evidence"]["snippet"] = (
        "Dynamics segment context "
        * 30
        + "In Azure and other cloud services, revenue grew 40% and 39% "
        "in constant currency."
    )

    output = await hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {"doc_id": "msft-q1"},
            "tool_response": raw,
        },
        "tool-prose",
        None,  # type: ignore[arg-type]
    )

    excerpt = output["hookSpecificOutput"]["updatedMCPToolOutput"]["chunks"][0]["_valuz_evidence"][
        0
    ]["excerpt"]
    assert "\n…\n" in excerpt
    assert "omitted" not in excerpt
    assert "revenue grew 40% and 39%" in excerpt


async def test_transcript_uses_one_indexed_search_and_blocks_original_reads() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    hooks = runtime._map_hooks()
    post_hook = hooks["PostToolUse"][0].hooks[0]
    pre_hook = hooks["PreToolUse"][0].hooks[0]

    await post_hook(
        {
            "tool_name": "mcp__valuz-search__conferences_search",
            "tool_input": {"symbols": ["US:MSFT"]},
            "tool_response": {"docs": [{"doc_id": "msft-q1", "category": "transcripts"}]},
        },
        "discovery",
        None,  # type: ignore[arg-type]
    )
    raw_before_search = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__document_raw_content",
            "tool_input": {"doc_id": "msft-q1"},
        },
        "raw-before-search",
        None,  # type: ignore[arg-type]
    )
    first_search = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__kb_search",
            "tool_input": {"doc_ids": ["msft-q1"], "query": "AI demand capex"},
        },
        "kb-first",
        None,  # type: ignore[arg-type]
    )
    search_result = await post_hook(
        {
            "tool_name": "mcp__valuz-search__kb_search",
            "tool_input": {
                "doc_ids": ["msft-q1"],
                "query": "AI demand capex",
            },
            "tool_response": _raw_document_result(),
        },
        "kb-first",
        None,  # type: ignore[arg-type]
    )
    repeated_search = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__kb_search",
            "tool_input": {"doc_ids": ["msft-q1"], "query": "supply constraint"},
        },
        "kb-repeat",
        None,  # type: ignore[arg-type]
    )
    fetch_after_search = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {"doc_id": "msft-q1", "chunk_limit": 60},
        },
        "fetch-after-search",
        None,  # type: ignore[arg-type]
    )
    unrelated_raw = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__document_raw_content",
            "tool_input": {"doc_id": "another-doc"},
        },
        "raw-2",
        None,  # type: ignore[arg-type]
    )

    assert raw_before_search["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        "exactly one kb_search"
        in raw_before_search["hookSpecificOutput"]["permissionDecisionReason"]
    )
    assert "continue_" not in first_search
    assert first_search["hookSpecificOutput"]["updatedInput"]["num"] == 10
    assert "one targeted indexed search" in search_result["hookSpecificOutput"]["additionalContext"]
    assert repeated_search["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        "already had its one targeted indexed search"
        in repeated_search["hookSpecificOutput"]["permissionDecisionReason"]
    )
    assert fetch_after_search["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        "Use the returned chunks"
        in fetch_after_search["hookSpecificOutput"]["permissionDecisionReason"]
    )
    assert "continue_" not in unrelated_raw


async def test_transcript_discovery_prioritizes_one_original_per_period() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]
    docs = []
    for quarter in ("Q4", "Q3", "Q2", "FY", "Q1"):
        docs.extend(
            [
                {
                    "doc_id": f"{quarter}-slides",
                    "title": f"Issuer FY2026 {quarter} Earnings Call Presentation",
                    "metadata": {
                        "fiscal_year": "2026",
                        "fiscal_quarter": quarter,
                        "report_type": 3,
                    },
                },
                {
                    "doc_id": f"{quarter}-call",
                    "title": f"Issuer FY2026 {quarter} Earnings Call Transcript",
                    "metadata": {
                        "fiscal_year": "2026",
                        "fiscal_quarter": quarter,
                        "report_type": 2,
                    },
                },
            ]
        )

    result = await hook(
        {
            "tool_name": "mcp__valuz-search__conferences_search",
            "tool_input": {},
            "tool_response": {
                "docs": docs,
                # Discovery adapters can already attach summary handles. The
                # period-aware projection must still run before generic
                # evidence compaction or slide decks consume the four rows.
                "_valuz_evidence": [{"evidenceHandle": "ev_discovery_12345678"}],
            },
        },
        "discover-periods",
        None,  # type: ignore[arg-type]
    )

    visible = result["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert [document["doc_id"] for document in visible["docs"]] == [
        "Q4-call",
        "Q3-call",
        "Q2-call",
        "Q1-call",
    ]


async def test_broad_transcript_discovery_expands_candidate_window_once() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    pre_hook = runtime._map_hooks()["PreToolUse"][0].hooks[0]

    broad = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__conferences_search",
            "tool_input": {"symbols": ["US:MSFT"], "num": 8},
        },
        "broad-search",
        None,  # type: ignore[arg-type]
    )
    exact_quarter = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__conferences_search",
            "tool_input": {
                "symbols": ["US:MSFT"],
                "fiscal_year": "2026",
                "fiscal_quarter": "Q2",
                "num": 4,
            },
        },
        "quarter-search",
        None,  # type: ignore[arg-type]
    )

    assert broad["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert broad["hookSpecificOutput"]["updatedInput"]["num"] == 20
    assert "updatedInput" not in exact_quarter.get("hookSpecificOutput", {})


async def test_document_fetch_budget_blocks_fourth_call() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    pre_hook = runtime._map_hooks()["PreToolUse"][0].hooks[0]

    for index in range(3):
        allowed = await pre_hook(
            {
                "tool_name": "mcp__valuz-search__document_fetch",
                "tool_input": {
                    "doc_id": f"filing-{index}",
                    "chunk_offset": 0,
                    "chunk_limit": 60,
                },
            },
            f"fetch-{index}",
            None,  # type: ignore[arg-type]
        )
        assert "continue_" not in allowed

    blocked = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {
                "doc_id": "filing-4",
                "chunk_offset": 0,
                "chunk_limit": 60,
            },
        },
        "fetch-4",
        None,  # type: ignore[arg-type]
    )

    assert blocked["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "budget" in blocked["hookSpecificOutput"]["permissionDecisionReason"]
    assert "continue_" not in blocked


async def test_document_discovery_budget_hard_denies_seventh_call() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    pre_hook = runtime._map_hooks()["PreToolUse"][0].hooks[0]

    for index in range(6):
        allowed = await pre_hook(
            {
                "tool_name": "mcp__valuz-search__filings_search",
                "tool_input": {"query": f"query-{index}"},
            },
            f"search-{index}",
            None,  # type: ignore[arg-type]
        )
        assert "continue_" not in allowed
        assert "hookSpecificOutput" not in allowed

    blocked = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__filings_search",
            "tool_input": {"query": "query-7"},
        },
        "search-7",
        None,  # type: ignore[arg-type]
    )

    assert blocked["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "discovery budget" in blocked["hookSpecificOutput"]["permissionDecisionReason"]


async def test_filing_fetch_blocks_distant_and_adjacent_windows() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    hooks = runtime._map_hooks()
    pre_hook = hooks["PreToolUse"][0].hooks[0]
    post_hook = hooks["PostToolUse"][0].hooks[0]

    first = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {"doc_id": "filing-1", "chunk_offset": 0, "chunk_limit": 60},
        },
        "fetch-0",
        None,  # type: ignore[arg-type]
    )
    assert "continue_" not in first
    await post_hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {"doc_id": "filing-1", "chunk_offset": 0, "chunk_limit": 60},
            "tool_response": {
                "doc_id": "filing-1",
                "total_chunks": 300,
                "chunk_offset": 0,
                "next_chunk_offset": 60,
                "_valuz_evidence": _raw_long_document_result()["_valuz_evidence"][:60],
            },
        },
        "fetch-0",
        None,  # type: ignore[arg-type]
    )

    distant = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {"doc_id": "filing-1", "chunk_offset": 240, "chunk_limit": 60},
        },
        "fetch-240",
        None,  # type: ignore[arg-type]
    )
    adjacent = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {"doc_id": "filing-1", "chunk_offset": 60, "chunk_limit": 60},
        },
        "fetch-60",
        None,  # type: ignore[arg-type]
    )

    assert distant["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "distant chunk offset" in distant["hookSpecificOutput"]["permissionDecisionReason"]
    assert adjacent["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Do not page sequentially" in adjacent["hookSpecificOutput"]["permissionDecisionReason"]


async def test_post_tool_hook_filters_secondary_and_duplicate_transcripts() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]
    msft = {
        "title": "Microsoft(MSFT) - 2026 Q1 - Earnings Call Transcript",
        "summary": "Azure details " * 100,
        "companies": [{"name": "Microsoft", "stocks": [{"symbol": "US:MSFT"}]}],
        "metadata": {"fiscal_year": "2026", "fiscal_quarter": "Q1"},
    }
    iren = {
        "doc_id": "iren-q1",
        "title": "Iris Energy (IREN) - 2026 Q1 - Earnings Call Transcript",
        "summary": "Microsoft is a customer.",
        "companies": [
            {"name": "Iris Energy", "stocks": [{"symbol": "US:IREN"}]},
            {"name": "Microsoft", "stocks": [{"symbol": "US:MSFT"}]},
        ],
    }

    output = await hook(
        {
            "tool_name": "mcp__valuz-search__conferences_search",
            "tool_input": {"symbols": ["US:MSFT"]},
            "tool_response": {
                "docs": [
                    iren,
                    {**msft, "doc_id": "msft-first"},
                    {**msft, "doc_id": "msft-duplicate"},
                ]
            },
        },
        "search-1",
        None,  # type: ignore[arg-type]
    )

    compacted = output["hookSpecificOutput"]["updatedMCPToolOutput"]
    assert [doc["doc_id"] for doc in compacted["docs"]] == ["msft-first"]
    assert "summary" not in compacted["docs"][0]
    assert compacted["_valuz_discovery"]["filteredOut"] == 1
    assert compacted["_valuz_discovery"]["duplicatesRemoved"] == 1
    assert compacted["_valuz_discovery"]["citationEvidence"] == ("original-indexed-chunk-required")


async def test_raw_document_grep_returns_traceable_focused_evidence() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    post_hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]
    raw_text = (
        "主营业务分销售模式\n"
        "| 渠道 | 营业收入（元） | 同比 |\n"
        "| 直销 | 74,843,327,030.79 | 11.32% |\n"
        "| 批发代理 | 95,768,511,021.23 | 19.73% |\n"
    )
    await post_hook(
        {
            "tool_name": "mcp__valuz-search__document_raw_content",
            "tool_input": {"doc_id": "annual-report"},
            "tool_response": {
                "doc_id": "annual-report",
                "title": "Annual report",
                "url": "https://reportify.cn/financials/annual-report",
                "content": raw_text,
            },
        },
        "raw-document-call",
        None,  # type: ignore[arg-type]
    )

    result = await post_hook(
        {
            "tool_name": "Grep",
            "tool_input": {
                "pattern": "直销|批发代理",
                "path": "/large_tool_results/raw-document-call",
            },
            "tool_response": "2:| 直销 |...\n3:| 批发代理 |...",
        },
        "grep-call",
        None,  # type: ignore[arg-type]
    )

    visible = json.loads(result["hookSpecificOutput"]["updatedToolOutput"])
    assert "74,843,327,030.79" in visible["matches"]
    assert visible["_valuz_evidence"][0]["evidenceHandle"].startswith("ev_grep_")
    sidecar = json.loads(runtime._citation_tool_result_sidecars["grep-call"])
    assert sidecar["_valuz_evidence"][0]["source"]["documentId"] == "annual-report"


async def test_raw_document_resolves_requested_fields_without_full_scan() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    runtime._citation_user_query = (
        "请根据年度报告，分别列出审计意见、营业总收入和营业收入，并逐项引用原文。"
    )
    post_hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]

    result = await post_hook(
        {
            "tool_name": "mcp__valuz-search__document_raw_content",
            "tool_input": {"doc_id": "annual-report"},
            "tool_response": {
                "doc_id": "annual-report",
                "title": "Annual report",
                "url": "https://reportify.cn/financials/annual-report",
                "content": (
                    "天健会计师事务所出具了标准无保留意见的审计报告。\n"
                    "年度内公司实现营业总收入 1,741.44 亿元。\n"
                    "营业收入 170,899,152,276.34 元。"
                ),
            },
        },
        "raw-targeted",
        None,  # type: ignore[arg-type]
    )

    visible = json.loads(result["hookSpecificOutput"]["updatedMCPToolOutput"])
    assert [row["requestedField"] for row in visible["targetedEvidence"]] == [
        "审计意见",
        "营业总收入",
        "营业收入",
    ]
    assert "scan the full document" in visible["nextAction"]
    sidecar = json.loads(runtime._citation_tool_result_sidecars["raw-targeted"])
    assert len(sidecar["_valuz_evidence"]) == 3


async def test_raw_document_bash_grep_returns_traceable_focused_evidence() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    post_hook = runtime._map_hooks()["PostToolUse"][0].hooks[0]
    await post_hook(
        {
            "tool_name": "mcp__valuz-search__document_raw_content",
            "tool_input": {"doc_id": "annual-report"},
            "tool_response": {
                "doc_id": "annual-report",
                "title": "Annual report",
                "url": "https://reportify.cn/financials/annual-report",
                "content": "年度内公司实现营业总收入 1,741.44 亿元。",
            },
        },
        "toolu-raw-document",
        None,  # type: ignore[arg-type]
    )

    result = await post_hook(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    'grep -n "营业总收入\\|营业收入" '
                    '"/tmp/tool-results/toolu-raw-document.txt" | head -80'
                )
            },
            "tool_response": "1:年度内公司实现营业总收入 1,741.44 亿元。",
        },
        "bash-grep-call",
        None,  # type: ignore[arg-type]
    )

    visible = json.loads(result["hookSpecificOutput"]["updatedToolOutput"])
    assert "1,741.44" in visible["matches"]
    assert visible["_valuz_evidence"][0]["evidenceHandle"].startswith("ev_grep_")
    sidecar = json.loads(runtime._citation_tool_result_sidecars["bash-grep-call"])
    assert sidecar["_valuz_evidence"][0]["source"]["documentId"] == "annual-report"


async def test_filing_fetch_rejects_small_window_before_spending_budget() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    pre_hook = runtime._map_hooks()["PreToolUse"][0].hooks[0]

    rejected = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {"doc_id": "filing-1", "chunk_offset": 0, "chunk_limit": 10},
        },
        "fetch-small",
        None,  # type: ignore[arg-type]
    )
    allowed = await pre_hook(
        {
            "tool_name": "mcp__valuz-search__document_fetch",
            "tool_input": {"doc_id": "filing-1", "chunk_offset": 0, "chunk_limit": 60},
        },
        "fetch-bounded",
        None,  # type: ignore[arg-type]
    )

    assert rejected["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "chunk_limit=60" in rejected["hookSpecificOutput"]["permissionDecisionReason"]
    assert "continue_" not in allowed


async def test_tool_result_event_replays_private_sidecar_for_registry() -> None:
    sink = _RecordingSink()
    agent = AgentConfig(id="a", name="a")
    runtime = ClaudeAgentRuntime(agent, "", sink)
    raw = _raw_document_result()
    runtime._citation_tool_result_sidecars["tool-1"] = json.dumps(raw)
    compacted = {
        "chunks": [
            {
                "_valuz_evidence": [
                    {
                        "evidenceHandle": "ev_msft_q1_12345678",
                        "kind": "text",
                        "excerpt": "Azure revenue grew 40%.",
                    }
                ]
            }
        ]
    }

    await runtime._handle_message(
        Session(id="s", agent_config=agent, cwd="/tmp"),
        SdkUserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tool-1",
                    content=json.dumps(compacted),
                )
            ]
        ),
    )

    event = sink.events[-1]
    assert event.type == "tool_result"
    assert "bulk transcript" not in event.data["content"]
    assert "bulk transcript" in event.data["_citation_content"]
    assert "tool-1" not in runtime._citation_tool_result_sidecars


def test_disabled_citation_mode_does_not_install_internal_hook() -> None:
    runtime = ClaudeAgentRuntime(AgentConfig(id="a", name="a"), "", _RecordingSink())
    runtime._citation_compaction_enabled = False

    assert runtime._map_hooks() is None

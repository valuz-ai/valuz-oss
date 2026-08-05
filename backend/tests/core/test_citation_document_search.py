import json

from src.core.citation import EvidenceRegistry, compact_citation_tool_content
from src.core.citation_document_search import (
    augment_indexed_document_evidence,
    constrain_indexed_document_scope,
    request_search_terms,
    targeted_document_evidence,
)


def test_indexed_chunks_are_standardized_into_traceable_evidence() -> None:
    result = augment_indexed_document_evidence(
        {
            "chunks": [
                {
                    "id": "chunk-42",
                    "content": "Customer demand continues to exceed available supply.",
                    "metadata": {"document_page": 17},
                    "doc": {
                        "doc_id": "msft-q4",
                        "title": "Microsoft FY2026 Q4 Earnings Call Transcript",
                        "url": "https://reportify.cn/transcripts/msft-q4",
                        "category": "transcripts",
                        "published_at": 1_785_364_320_000,
                    },
                }
            ]
        },
        tool_name="mcp__valuz-search__kb_search",
        captured_at="2026-08-02T00:00:00Z",
    )

    assert isinstance(result, dict)
    evidence = result["_valuz_evidence"][0]
    assert evidence["evidenceHandle"].startswith("ev_chunk_")
    assert evidence["source"]["documentId"] == "msft-q4"
    assert evidence["evidence"]["quote"] == (
        "Customer demand continues to exceed available supply."
    )
    assert evidence["locator"] == {
        "kind": "pdf",
        "page": 17,
        "chunkId": "chunk-42",
        "quote": {"exact": "Customer demand continues to exceed available supply."},
    }
    registry = EvidenceRegistry()
    assert registry.register_tool_result(result, tool_name="kb_search") == 1


def test_indexed_chunk_without_title_uses_readable_host_fallback() -> None:
    result = augment_indexed_document_evidence(
        {
            "chunks": [
                {
                    "id": "chunk-untitled",
                    "content": "A stable indexed passage.",
                    "doc": {
                        "doc_id": "W13341981828044806",
                        "url": "https://www.news.cn/tech/example.html",
                    },
                }
            ]
        },
        tool_name="kb_search",
        captured_at="2026-08-03T00:00:00Z",
    )

    assert result is not None
    source = result["_valuz_evidence"][0]["source"]
    assert source["title"] == "news.cn"
    assert source["documentId"] not in source["title"]


def test_existing_connector_evidence_is_not_rewritten() -> None:
    content = {"chunks": [], "_valuz_evidence": [{"evidenceHandle": "ev_existing_1"}]}

    assert (
        augment_indexed_document_evidence(
            content,
            tool_name="kb_search",
            captured_at="2026-08-02T00:00:00Z",
        )
        is None
    )


def test_indexed_document_scope_discards_global_search_leakage() -> None:
    result = constrain_indexed_document_scope(
        {
            "chunks": [
                {
                    "id": "wanted",
                    "content": "Requested source passage.",
                    "doc": {"doc_id": "sk-q2"},
                },
                {
                    "id": "wrong",
                    "content": "Unrelated operating cash flow.",
                    "doc": {"doc_id": "nvda-q2"},
                },
            ]
        },
        document_ids=("sk-q2",),
    )

    assert [chunk["id"] for chunk in result["chunks"]] == ["wanted"]
    assert result["_valuz_scope"] == {
        "documentIds": ["sk-q2"],
        "discardedOutOfScopeChunks": 1,
    }


def test_transcript_boilerplate_does_not_consume_evidence_slots() -> None:
    common_doc = {
        "doc_id": "msft-q4",
        "title": "Microsoft FY2026 Q4 Earnings Call Transcript",
        "category": "transcripts",
    }
    result = augment_indexed_document_evidence(
        {
            "chunks": [
                {
                    "id": "participants",
                    "content": (
                        "Company Participants\nAmy Hood - CFO\n"
                        "Conference Call Participants\nAnalyst"
                    ),
                    "doc": common_doc,
                },
                {
                    "id": "business",
                    "content": "We added another gigawatt of capacity this quarter.",
                    "doc": common_doc,
                },
            ]
        },
        tool_name="kb_search",
        captured_at="2026-08-02T00:00:00Z",
    )

    assert isinstance(result, dict)
    assert len(result["_valuz_evidence"]) == 1
    assert result["_valuz_evidence"][0]["locator"]["chunkId"] == "business"


def test_indexed_chunk_compaction_is_idempotent_for_model_excerpt() -> None:
    augmented = augment_indexed_document_evidence(
        {
            "chunks": [
                {
                    "id": "chunk-42",
                    "content": "Customer demand continues to exceed available supply.",
                    "doc": {
                        "doc_id": "msft-q4",
                        "title": "Microsoft FY2026 Q4 Earnings Call Transcript",
                    },
                }
            ]
        },
        tool_name="kb_search",
        captured_at="2026-08-02T00:00:00Z",
    )
    assert augmented is not None

    once = compact_citation_tool_content(augmented)
    assert once is not None
    # ``None`` means the already-projected value contains no trusted envelope
    # left to compact; callers preserve their input in that case.
    twice = compact_citation_tool_content(once) or once

    assert twice == once
    expected = "Customer demand continues to exceed available supply."
    assert once["chunks"][0]["content"] == expected
    assert once["chunks"][0]["evidenceHandle"].startswith("ev_chunk_")
    assert twice["chunks"][0]["content"] == expected


def test_extracts_enumerated_requested_fields_without_finance_vocabulary() -> None:
    assert request_search_terms(
        "请根据贵州茅台2024年年度报告，分别列出审计意见、营业总收入和营业收入，"
        "并逐项引用对应的年度报告原文。"
    ) == ("审计意见", "营业总收入", "营业收入")


def test_complex_channel_request_extracts_entities_and_metrics() -> None:
    assert request_search_terms(
        "请根据年度报告，只用两行列出直销渠道和批发代理渠道的本期销售收入"
        "（同时给出元和亿元）及同比增幅，并逐行引用原文。"
    ) == (
        "直销渠道",
        "批发代理渠道的本期销售收入",
        "批发代理渠道",
        "本期销售收入",
        "同比增幅",
    )


def test_targeted_search_prefers_numeric_table_over_earlier_definition() -> None:
    raw = {
        "doc_id": "annual-report",
        "title": "Annual report",
        "url": "https://reportify.cn/financials/annual-report",
        "content": (
            "直销渠道指自营平台，批发代理渠道指社会经销商。\n"
            + ("经营情况说明。\n" * 30)
            + "| 渠道 | 本期销售收入（元） | 同比增幅 |\n"
            "| 直销 | 74,843,327,030.79 | 11.32% |\n"
            "| 批发代理 | 95,768,511,021.23 | 19.73% |\n"
        ),
    }
    result = targeted_document_evidence(
        raw,
        terms=("直销渠道", "批发代理渠道", "同比增幅"),
        captured_at="2026-08-02T00:00:00Z",
    )

    assert result is not None
    visible, envelopes = result
    rows = json.loads(visible)["targetedEvidence"]
    assert "74,843,327,030.79" in rows[0]["matches"]
    assert "95,768,511,021.23" in rows[1]["matches"]
    assert "74,843,327,030.79" in envelopes[0]["evidence"]["quote"]
    assert "95,768,511,021.23" in envelopes[1]["evidence"]["quote"]

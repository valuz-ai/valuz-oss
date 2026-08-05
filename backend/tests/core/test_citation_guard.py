"""Citation evidence registry and final-answer guard."""

from __future__ import annotations

import hashlib
import json
import time

from src.core.citation import (
    CitationGuard,
    EvidenceRegistry,
    compact_citation_tool_content,
    private_citation_tool_content,
)
from src.core.claim_audit import ClaimCandidate
from src.core.claim_evidence_resolution import (
    EvidenceCandidate,
    SemanticVerificationRequest,
    SemanticVerificationResult,
)


def _item(
    handle: str = "ev_revenue_2025",
    *,
    locator: dict | None = None,
) -> dict:
    item = {
        "evidenceHandle": handle,
        "source": {
            "sourceId": "doc-1",
            "providerId": "valuz-project-docs",
            "documentId": "doc-1",
            "documentVersion": "sha256:abc",
            "sourceType": "document",
            "mimeType": "application/pdf",
            "title": "Annual Report",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Revenue increased by 12%.",
            "snippet": "Revenue increased by 12%.",
            "capturedAt": "2026-07-30T10:00:00Z",
            "contentHash": "sha256:abc",
        },
    }
    if locator is not None:
        item["locator"] = locator
    return item


def _registry(*items: dict) -> EvidenceRegistry:
    registry = EvidenceRegistry()
    payload = [{"snippet": "visible to model", "_valuz_evidence": item} for item in items]
    assert registry.register_tool_result(
        json.dumps(payload), tool_name="valuz_docs/doc_search"
    ) == len(items)
    return registry


def test_registry_accepts_nested_valid_envelope_and_first_writer_wins() -> None:
    registry = _registry(_item(locator={"kind": "pdf", "page": 12}))
    replacement = _item(locator={"kind": "pdf", "page": 99})

    assert (
        registry.register_tool_result(
            {"result": {"_valuz_evidence": replacement}},
            tool_name="untrusted/second",
        )
        == 0
    )
    record = registry.get("ev_revenue_2025")
    assert record is not None
    assert record.locator == {"kind": "pdf", "page": 12}
    assert record.tool_name == "valuz_docs/doc_search"


def test_guard_accepts_unique_evidence_digest_when_model_rewrites_prefix() -> None:
    digest = "8407070380061a6f4dcd6e90"
    item = _item(f"ev_grep_{digest}", locator={"kind": "pdf", "page": 7})
    registry = _registry(item)

    result = CitationGuard(
        registry,
        message_id="message-1",
        user_prompt="cite the source",
        policy_available=True,
    ).finalize(f"Revenue increased by 12% [1](evidence://ev_rpt_{digest}).")

    assert result.bundle is not None
    assert len(result.bundle["citations"]) == 1
    assert result.bundle["integrity"]["unknownCitationIds"] == []
    assert "citation://cit_" in result.text


def test_guard_accepts_unique_mcp_chunk_id_alias() -> None:
    item = _item(
        "ev_mcp_8407070380061a6f4dcd6e90",
        locator={"kind": "pdf", "page": 7, "chunkId": "829938771212395"},
    )
    registry = _registry(item)

    resolved = registry.resolve("ev_mcp_829938771212395")
    assert resolved is not None
    assert resolved.handle == "ev_mcp_8407070380061a6f4dcd6e90"

    result = CitationGuard(
        registry,
        message_id="message-mcp-chunk-alias",
        user_prompt="cite the source",
        policy_available=True,
    ).finalize(
        "Revenue increased by 12% "
        "[source](evidence://ev_mcp_829938771212395)."
    )

    assert result.bundle is not None
    assert len(result.bundle["citations"]) == 1
    citation = result.bundle["citations"][0]
    assert citation["locator"]["chunkId"] == "829938771212395"
    assert citation["annotations"]["binding"]["evidenceHandle"] == (
        "ev_mcp_8407070380061a6f4dcd6e90"
    )
    assert result.bundle["integrity"]["unknownCitationIds"] == []


def test_registry_does_not_guess_ambiguous_mcp_chunk_id_alias() -> None:
    first = _item(
        "ev_mcp_first_8407070380061a6f4dcd6e90",
        locator={"kind": "pdf", "page": 7, "chunkId": "829938771212395"},
    )
    second = _item(
        "ev_mcp_second_9407070380061a6f4dcd6e90",
        locator={"kind": "pdf", "page": 8, "chunkId": "829938771212395"},
    )
    second["source"].update({"sourceId": "doc-2", "documentId": "doc-2"})
    registry = _registry(first, second)

    assert registry.resolve("ev_mcp_829938771212395") is None


def test_guard_rebases_external_excerpt_to_unique_later_located_chunk() -> None:
    external = _item(
        "ev_grep_external_12345678",
        locator={"kind": "external", "fragment": "Revenue"},
    )
    external["evidence"]["quote"] = (
        "Earlier context.\nRevenue increased by 12%.\nLater context."
    )
    external["evidence"]["snippet"] = external["evidence"]["quote"]
    located = _item(
        "ev_chunk_located_12345678",
        locator={"kind": "pdf", "page": 12, "chunkId": "chunk-12"},
    )
    registry = _registry(external, located)

    result = CitationGuard(
        registry,
        message_id="message-late-locator",
        user_prompt="cite the source",
        policy_available=True,
        verification_enabled=False,
    ).finalize(
        "Revenue increased by 12% "
        "[source](evidence://ev_grep_external_12345678)."
    )

    assert result.bundle is not None
    assert len(result.bundle["citations"]) == 1
    citation = result.bundle["citations"][0]
    assert citation["annotations"]["binding"]["evidenceHandle"] == (
        "ev_chunk_located_12345678"
    )
    assert citation["locator"] == {
        "kind": "pdf",
        "page": 12,
        "chunkId": "chunk-12",
    }
    projection = result.bundle["projection"]["evidenceHandleToCitationId"]
    assert projection["ev_grep_external_12345678"] == citation["citationId"]


def test_guard_keeps_external_excerpt_when_multiple_located_chunks_match() -> None:
    external = _item(
        "ev_grep_ambiguous_12345678",
        locator={"kind": "external", "fragment": "increased"},
    )
    external["evidence"]["quote"] = (
        "Revenue increased by 12%.\nOperating margin increased by 3 points."
    )
    external["evidence"]["snippet"] = external["evidence"]["quote"]
    first = _item(
        "ev_chunk_first_12345678",
        locator={"kind": "pdf", "page": 12, "chunkId": "chunk-12"},
    )
    second = _item(
        "ev_chunk_second_12345678",
        locator={"kind": "pdf", "page": 13, "chunkId": "chunk-13"},
    )
    second["evidence"]["quote"] = "Operating margin increased by 3 points."
    second["evidence"]["snippet"] = second["evidence"]["quote"]
    registry = _registry(external, first, second)

    result = CitationGuard(
        registry,
        message_id="message-ambiguous-locator",
        user_prompt="cite the source",
        policy_available=True,
        verification_enabled=False,
    ).finalize(
        "Revenue increased by 12% "
        "[source](evidence://ev_grep_ambiguous_12345678)."
    )

    assert result.bundle is not None
    citation = result.bundle["citations"][0]
    assert citation["annotations"]["binding"]["evidenceHandle"] == (
        "ev_grep_ambiguous_12345678"
    )
    assert citation["locator"]["kind"] == "external"


def test_projection_guard_keeps_runtime_body_and_does_not_extract_claims(monkeypatch) -> None:
    registry = _registry(
        _item(
            "ev_projection_12345678",
            locator={"kind": "pdf", "page": 12},
        )
    )
    original = (
        "Revenue increased by 12% "
        "[source](evidence://ev_projection_12345678)."
    )

    def fail_if_called(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("Citation-only projection must not split claims")

    monkeypatch.setattr("src.core.citation.bind_claims_to_evidence", fail_if_called)
    result = CitationGuard(
        registry,
        message_id="message-projection",
        user_prompt="cite the source",
        policy_available=True,
        verification_enabled=False,
    ).finalize_projection(original)

    assert result.text == original
    assert result.bundle is not None
    assert "quality" not in result.bundle
    citation = result.bundle["citations"][0]
    assert citation["annotations"]["binding"]["evidenceHandle"] == (
        "ev_projection_12345678"
    )
    assert result.bundle["integrity"]["unknownCitationIds"] == []


def test_registry_decodes_json_nested_in_mcp_text_content_blocks() -> None:
    envelope_json = json.dumps(
        {"result": {"_valuz_evidence": _item()}},
        ensure_ascii=False,
    )
    mcp_result = {
        "content": [
            {
                "type": "text",
                "text": envelope_json,
            }
        ]
    }
    registry = EvidenceRegistry()

    assert (
        registry.register_tool_result(
            json.dumps(mcp_result, ensure_ascii=False),
            tool_name="valuz-search/document_fetch",
        )
        == 1
    )
    record = registry.get("ev_revenue_2025")
    assert record is not None
    assert record.tool_name == "valuz-search/document_fetch"


def test_registry_preserves_structured_semantic_dimensions() -> None:
    item = _item("ev_dimensions_12345678")
    item["source"].update({"sourceType": "dataset"})
    item["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "company_income_statement",
        "recordKey": "600519|2024 FY",
        "entityId": "600519",
        "entityName": "贵州茅台",
        "field": "operating_revenue",
        "metric": "operating_revenue",
        "value": 174_144_000_000,
        "unit": "CNY",
        "currency": "CNY",
        "scale": 1,
        "period": "2024 FY",
        "scope": "consolidated",
        "basis": "reported",
        "capturedAt": "2026-08-01T08:00:00Z",
    }

    record = _registry(item).get("ev_dimensions_12345678")

    assert record is not None
    assert record.evidence["entityId"] == "600519"
    assert record.evidence["metric"] == "operating_revenue"
    assert record.evidence["scope"] == "consolidated"
    assert record.evidence["basis"] == "reported"


def test_structured_batch_registers_one_collection_and_materializes_used_address() -> None:
    item = _item("ev_legacy_revenue_12345678")
    item["source"].update({"sourceType": "dataset"})
    item["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "company_income_statement",
        "recordKey": "600519|2024 FY",
        "entityId": "600519",
        "field": "data[0].operating_revenue",
        "metric": "operating_revenue",
        "value": 174_144_000_000,
        "unit": "CNY",
        "period": "2024 FY",
        "capturedAt": "2026-08-01T08:00:00Z",
    }
    raw = {
        "data": [
            {
                "symbol": "600519",
                "fiscal_year": 2024,
                "period": "FY",
                "operating_revenue": 174_144_000_000,
            }
        ],
        "_valuz_evidence": [item],
    }

    visible = compact_citation_tool_content(raw)
    private = private_citation_tool_content(raw)

    assert visible is not None
    assert private is not None
    assert visible["data"] == raw["data"]
    assert "_valuz_evidence" not in visible
    hint = visible["_valuz_evidence_hint"]
    assert hint["citationTemplate"].endswith("#{json-pointer}")
    private_payload = json.loads(private)
    assert len(private_payload["_valuz_evidence"]) == 1
    assert private_payload["_valuz_evidence"][0]["kind"] == ("structured-evidence-collection")

    registry = EvidenceRegistry()
    assert (
        registry.register_tool_projection(
            visible,
            private,
            tool_name="company_income_statement",
            trusted_private=True,
        )
        == 1
    )
    assert registry.collection_count == 1
    assert len(registry) == 0

    address = f"evidence://{hint['collectionHandle']}#/data/0/operating_revenue"
    result = CitationGuard(
        registry,
        message_id="msg-collection",
        user_prompt="Cite operating revenue",
        policy_available=True,
        verification_enabled=False,
    ).finalize(f"Operating revenue was CNY 174144000000 [source]({address}).")

    assert "citation://" in result.text
    assert result.bundle is not None
    assert result.bundle["integrity"]["evidenceCollectionCount"] == 1
    assert result.bundle["integrity"]["evidenceMaterializedCount"] == 1
    assert len(result.bundle["citations"]) == 1
    assert result.bundle["citations"][0]["evidence"]["metric"] == "operating_revenue"


def test_native_reportify_collection_materializes_only_addressed_field() -> None:
    data = [
        {
            "symbol": "600519",
            "fiscal_year": 2024,
            "fiscal_quarter": "FY",
            "end_date": "2024-12-31",
            "currency": "CNY",
            "scale": "yuan",
            "operating_revenue": 170_899_152_276,
            "net_profit": 86_228_146_422,
        }
    ]
    raw_hash = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    collection_handle = "evc_rpt_native_12345678"
    raw = {
        "data": data,
        "_valuz_evidence": [
            {
                "version": 1,
                "kind": "structured-evidence-collection",
                "collectionHandle": collection_handle,
                "source": {
                    "sourceId": "reportify-financial-income-statement:600519",
                    "providerId": "valuz-stock",
                    "sourceType": "dataset",
                    "sourceCategory": "structured_financials",
                    "title": "Company income statement · 600519",
                    "retrievedAt": "2026-08-03T08:00:00Z",
                },
                "common": {
                    "datasetId": "reportify-financial-income-statement",
                    "toolName": "company_income_statement",
                    "entityId": "600519",
                    "currency": "CNY",
                    "scale": "yuan",
                    "capturedAt": "2026-08-03T08:00:00Z",
                },
                "addressing": {
                    "mode": "json-pointer",
                    "contentRoot": "/data",
                    "identityFields": [],
                    "fieldSchemaRef": {
                        "schemaId": "reportify-financial-income-statement",
                        "revision": "1",
                    },
                    "allowedPathRoots": ["/data"],
                },
                "contentHash": (f"sha256:{hashlib.sha256(raw_hash.encode('utf-8')).hexdigest()}"),
                "sparseOverrides": [
                    {
                        "selector": {"path": "/data/0/operating_revenue"},
                        "unit": "CNY yuan",
                    }
                ],
            }
        ],
    }

    visible = compact_citation_tool_content(raw)
    private = private_citation_tool_content(raw)

    assert visible is not None and private is not None
    assert visible["data"] == data
    assert visible["_valuz_evidence_hint"]["collectionHandle"] == collection_handle
    assert len(json.loads(private)["_valuz_evidence"]) == 1
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_projection(
            visible,
            private,
            tool_name="company_income_statement",
            trusted_private=True,
        )
        == 1
    )
    assert registry.collection_count == 1
    assert len(registry) == 0

    revenue = registry.materialize_reference(
        collection_handle,
        "#/data/0/operating_revenue",
    )

    assert revenue is not None
    assert len(registry) == 1
    assert revenue.evidence["entityId"] == "600519"
    assert revenue.evidence["period"] == "2024 FY"
    assert revenue.evidence["metric"] == "operating_revenue"
    assert revenue.evidence["value"] == 170_899_152_276
    assert revenue.evidence["unit"] == "CNY yuan"
    assert revenue.evidence["scale"] == "yuan"


def test_guard_recovers_unknown_collection_handle_only_from_unique_valid_pointer() -> None:
    data = [
        {
            "symbol": "600519",
            "fiscal_year": 2024,
            "fiscal_quarter": "FY",
            "end_date": "2024-12-31",
            "currency": "CNY",
            "net_profit": 86_228_146_422,
        }
    ]
    raw = {
        "data": data,
        "_valuz_evidence": [
            {
                "evidenceHandle": "ev_net_profit_2024_12345678",
                "source": {
                    "sourceId": "financials:600519",
                    "providerId": "valuz-stock",
                    "sourceType": "dataset",
                    "title": "Company income statement · 600519",
                    "retrievedAt": "2026-08-05T08:00:00Z",
                },
                "evidence": {
                    "kind": "structured-data",
                    "datasetId": "financials",
                    "toolName": "income_statement",
                    "recordKey": "600519|2024 FY",
                    "entityId": "600519",
                    "field": "net_profit",
                    "metric": "net_profit",
                    "value": 86_228_146_422,
                    "unit": "CNY",
                    "period": "2024 FY",
                    "capturedAt": "2026-08-05T08:00:00Z",
                },
            }
        ],
    }
    visible = compact_citation_tool_content(raw)
    private = private_citation_tool_content(raw)
    assert visible is not None and private is not None

    registry = EvidenceRegistry()
    assert registry.register_tool_projection(visible, private, trusted_private=True) == 1
    result = CitationGuard(
        registry,
        message_id="msg-unique-address-recovery",
        user_prompt="查询 2024 年净利润",
        policy_available=True,
        verification_enabled=False,
    ).finalize(
        "2024 年净利润为 86228146422 CNY "
        "[source](evidence://evc_model_typo_12345678#/data/0/net_profit)。"
    )

    assert result.bundle is not None
    assert len(result.bundle["citations"]) == 1
    assert result.bundle["citations"][0]["evidence"]["metric"] == "net_profit"
    assert result.bundle["integrity"]["unknownCitationIds"] == []
    assert "evc_model_typo_12345678" not in result.text


def test_registry_does_not_guess_unknown_collection_when_pointer_is_ambiguous() -> None:
    registry = EvidenceRegistry()
    for symbol, value in (("600519", 86_228_146_422), ("000858", 32_430_000_000)):
        raw = {
            "data": [{"symbol": symbol, "fiscal_year": 2024, "net_profit": value}],
            "_valuz_evidence": [
                {
                    "evidenceHandle": f"ev_net_profit_{symbol}_12345678",
                    "source": {
                        "sourceId": f"financials:{symbol}",
                        "providerId": "valuz-stock",
                        "sourceType": "dataset",
                        "title": f"Company income statement · {symbol}",
                        "retrievedAt": "2026-08-05T08:00:00Z",
                    },
                    "evidence": {
                        "kind": "structured-data",
                        "datasetId": "financials",
                        "toolName": "income_statement",
                        "recordKey": f"{symbol}|2024 FY",
                        "entityId": symbol,
                        "field": "net_profit",
                        "metric": "net_profit",
                        "value": value,
                        "unit": "CNY",
                        "period": "2024 FY",
                        "capturedAt": "2026-08-05T08:00:00Z",
                    },
                }
            ],
        }
        visible = compact_citation_tool_content(raw)
        private = private_citation_tool_content(raw)
        assert visible is not None and private is not None
        assert registry.register_tool_projection(visible, private, trusted_private=True) == 1

    assert (
        registry.materialize_reference(
            "evc_model_typo_12345678",
            "#/data/0/net_profit",
        )
        is None
    )
    assert len(registry) == 0


def test_materialized_structured_period_prefers_fiscal_quarter_over_frequency() -> None:
    item = _item("ev_legacy_quarter_revenue_12345678")
    item["source"].update({"sourceType": "dataset"})
    item["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "company_income_statement",
        "recordKey": "SNDK|2026 Q3",
        "entityId": "SNDK",
        "field": "operating_revenue",
        "metric": "operating_revenue",
        "value": 5_950_000_000,
        "unit": "USD",
        "capturedAt": "2026-08-03T08:00:00Z",
    }
    raw = {
        "data": [
            {
                "symbol": "SNDK",
                "fiscal_year": 2026,
                "fiscal_quarter": "Q3",
                "period": "quarterly",
                "operating_revenue": 5_950_000_000,
            }
        ],
        "_valuz_evidence": [item],
    }

    visible = compact_citation_tool_content(raw)
    private = private_citation_tool_content(raw)

    assert visible is not None and private is not None
    hint = visible["_valuz_evidence_hint"]
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_projection(
            visible,
            private,
            tool_name="company_income_statement",
            trusted_private=True,
        )
        == 1
    )
    result = CitationGuard(
        registry,
        message_id="msg-quarter-period",
        user_prompt="Cite quarterly revenue",
        policy_available=True,
        verification_enabled=False,
    ).finalize(
        "2026 Q3 revenue was USD 5950000000 "
        f"[source](evidence://{hint['collectionHandle']}#/data/0/operating_revenue)."
    )

    assert result.bundle is not None
    assert result.bundle["citations"][0]["evidence"]["period"] == "2026 Q3"


def test_multi_period_legacy_batch_collapses_repeated_fields_into_one_collection() -> None:
    source = {
        "sourceId": "financials:600519",
        "providerId": "valuz-stock",
        "sourceType": "dataset",
        "title": "Company income statement · 600519",
        "retrievedAt": "2026-08-02T08:00:00Z",
    }
    rows = [
        {
            "symbol": "600519",
            "fiscal_year": "2024",
            "fiscal_quarter": "FY",
            "operating_revenue": 170_899_152_276,
        },
        {
            "symbol": "600519",
            "fiscal_year": "2023",
            "fiscal_quarter": "FY",
            "operating_revenue": 147_693_604_994,
        },
    ]
    evidence = [
        {
            "evidenceHandle": f"ev_revenue_{year}_12345678",
            "source": source,
            "evidence": {
                "kind": "structured-data",
                "datasetId": "financials",
                "toolName": "company_income_statement",
                "recordKey": f"600519|{year} FY",
                "entityId": "600519",
                "field": "operating_revenue",
                "metric": "operating_revenue",
                "value": value,
                "unit": "CNY",
                "period": f"{year} FY",
                "capturedAt": "2026-08-02T08:00:00Z",
            },
        }
        for year, value in (
            ("2024", 170_899_152_276),
            ("2023", 147_693_604_994),
        )
    ]
    raw = {"data": rows, "_valuz_evidence": evidence}

    visible = compact_citation_tool_content(raw)
    private = private_citation_tool_content(raw)

    assert visible is not None and private is not None
    assert "_valuz_evidence" not in visible
    assert visible["data"] == rows
    hint = visible["_valuz_evidence_hint"]
    private_payload = json.loads(private)
    assert len(private_payload["_valuz_evidence"]) == 1
    assert private_payload["_valuz_evidence"][0]["kind"] == ("structured-evidence-collection")

    registry = EvidenceRegistry()
    registry.register_tool_projection(visible, private, trusted_private=True)
    assert registry.collection_count == 1
    assert len(registry) == 0
    result = CitationGuard(
        registry,
        message_id="msg-multi-period-collection",
        user_prompt="Compare revenue",
        policy_available=True,
        verification_enabled=False,
    ).finalize(
        "2024 revenue was CNY 170899152276 "
        f"[source](evidence://{hint['collectionHandle']}#/data/0/operating_revenue); "
        "2023 revenue was CNY 147693604994 "
        f"[source](evidence://{hint['collectionHandle']}#/data/1/operating_revenue)."
    )
    assert result.bundle is not None
    assert result.bundle["integrity"]["evidenceCollectionCount"] == 1
    # The two cited values plus two period candidates are materialized for the
    # two actual claims; none of the other row fields become Evidence.
    assert result.bundle["integrity"]["evidenceRegisteredCount"] == 4
    assert len(result.bundle["integrity"]["unusedCitationIds"]) == 2
    assert len(result.bundle["citations"]) == 2


def test_large_nested_legacy_batch_is_indexed_once_and_collapses_to_collection() -> None:
    source = {
        "sourceId": "index-constituents:000905",
        "providerId": "valuz-stock",
        "sourceType": "dataset",
        "title": "Index constituents · 000905",
        "retrievedAt": "2026-08-03T05:00:00Z",
    }
    rows = [{"market": "cn", "symbol": f"{position:06d}"} for position in range(1_000)]
    evidence = [
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
    ]
    raw = {"data": {"items": rows}, "_valuz_evidence": evidence}

    started = time.perf_counter()
    visible = compact_citation_tool_content(raw)
    private = private_citation_tool_content(raw)
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0
    assert visible is not None and private is not None
    hint = visible["_valuz_evidence_hint"]
    assert len(json.loads(private)["_valuz_evidence"]) == 1
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_projection(
            visible,
            private,
            tool_name="index_constituents",
            trusted_private=True,
        )
        == 1
    )
    record = registry.materialize_reference(
        hint["collectionHandle"],
        "#/data/items/999/market",
    )
    assert record is not None
    assert record.evidence["value"] == "cn"


def test_calculation_citation_moves_from_period_cell_to_matching_result_cell() -> None:
    def structured(handle: str, value: int, period: str) -> dict:
        item = _item(handle)
        item["source"]["sourceType"] = "dataset"
        item["evidence"] = {
            "kind": "structured-data",
            "datasetId": "financials",
            "toolName": "company_income_statement",
            "recordKey": f"600519|{period}",
            "entityId": "600519",
            "field": "operating_revenue",
            "metric": "operating_revenue",
            "value": value,
            "unit": "CNY",
            "period": period,
            "capturedAt": "2026-08-02T08:00:00Z",
        }
        return item

    current = structured("ev_current_revenue_12345678", 170_899_152_276, "2024 FY")
    prior = structured("ev_prior_revenue_12345678", 147_693_604_994, "2023 FY")
    calculation = _item("ev_calc_yoy_12345678")
    calculation["source"]["sourceType"] = "dataset"
    calculation["evidence"] = {
        "kind": "calculation",
        "toolName": "citation_calculate",
        "expression": "((current - prior) / prior) * 100",
        "inputs": [
            {
                "name": "current",
                "citationId": current["evidenceHandle"],
                "value": 170_899_152_276,
                "unit": "CNY",
            },
            {
                "name": "prior",
                "citationId": prior["evidenceHandle"],
                "value": 147_693_604_994,
                "unit": "CNY",
            },
        ],
        "result": "15.71",
        "unit": "%",
        "metric": "revenue_growth",
        "period": "2024 FY vs 2023 FY",
        "calculatedAt": "2026-08-02T08:00:00Z",
    }
    registry = _registry(current, prior, calculation)

    result = CitationGuard(
        registry,
        message_id="msg-calculation-cell",
        user_prompt="Show the calculated growth",
        policy_available=True,
        verification_enabled=False,
    ).finalize(
        "| 指标 | 数值 | 单位 | 期间 |\n"
        "|---|---:|---|---|\n"
        "| 同比增速 | +15.71% | — | 2024 vs 2023 "
        "[source](evidence://ev_calc_yoy_12345678) |"
    )

    row = result.text.splitlines()[-1].split("|")
    assert "citation://" in row[2]
    assert "citation://" not in row[4]
    assert result.bundle is not None
    assert len(result.bundle["citations"]) == 3


def test_calculation_inputs_resolve_structured_collection_addresses() -> None:
    source = {
        "sourceId": "financials:600519",
        "providerId": "valuz-stock",
        "sourceType": "dataset",
        "title": "Company income statement · 600519",
        "retrievedAt": "2026-08-02T08:00:00Z",
    }
    rows = [
        {
            "symbol": "600519",
            "fiscal_year": "2024",
            "fiscal_quarter": "FY",
            "operating_revenue": 170_899_152_276,
        },
        {
            "symbol": "600519",
            "fiscal_year": "2023",
            "fiscal_quarter": "FY",
            "operating_revenue": 147_693_604_994,
        },
    ]
    raw = {
        "data": rows,
        "_valuz_evidence": [
            {
                "evidenceHandle": f"ev_revenue_{year}_12345678",
                "source": source,
                "evidence": {
                    "kind": "structured-data",
                    "datasetId": "financials",
                    "toolName": "company_income_statement",
                    "recordKey": f"600519|{year} FY",
                    "entityId": "600519",
                    "field": "operating_revenue",
                    "metric": "operating_revenue",
                    "value": value,
                    "unit": "CNY",
                    "period": f"{year} FY",
                    "capturedAt": "2026-08-02T08:00:00Z",
                },
            }
            for year, value in (
                ("2024", 170_899_152_276),
                ("2023", 147_693_604_994),
            )
        ],
    }
    visible = compact_citation_tool_content(raw)
    private = private_citation_tool_content(raw)
    assert visible is not None and private is not None
    collection_handle = visible["_valuz_evidence_hint"]["collectionHandle"]
    current_address = f"{collection_handle}#/data/0/operating_revenue"
    prior_address = f"{collection_handle}#/data/1/operating_revenue"

    registry = EvidenceRegistry()
    assert registry.register_tool_projection(visible, private, trusted_private=True) == 1
    calculation = _item("ev_calc_collection_inputs_12345678")
    calculation["source"].update(
        {
            "sourceId": "calculation-growth",
            "providerId": "valuz-calculation",
            "sourceType": "tool-result",
            "title": "Calculation",
        }
    )
    calculation["evidence"] = {
        "kind": "calculation",
        "toolName": "runtime.calculation",
        "expression": "((current - prior) / prior) * 100",
        "inputs": [
            {
                "name": "current",
                "citationId": current_address,
                "value": "170899152276",
            },
            {
                "name": "prior",
                "citationId": prior_address,
                "value": "147693604994",
            },
        ],
        "result": "15.71",
        "unit": "%",
        "rounding": "2dp",
        "calculatedAt": "2026-08-02T08:00:00Z",
        "metric": "revenue_growth",
        "period": "2024 FY vs 2023 FY",
    }
    assert registry.register_tool_result({"_valuz_evidence": calculation}) == 1

    result = CitationGuard(
        registry,
        message_id="msg-collection-calculation",
        user_prompt="Compare and calculate revenue growth",
        policy_available=True,
        verification_enabled=True,
    ).finalize(
        "2024 revenue was CNY 170899152276.\n"
        "2023 revenue was CNY 147693604994.\n"
        "Revenue growth was 15.71% "
        "[source](evidence://ev_calc_collection_inputs_12345678)."
    )

    assert result.bundle is not None
    citations = result.bundle["citations"]
    calculation_citation = next(
        citation for citation in citations if citation["evidence"]["kind"] == "calculation"
    )
    assert [item["citationId"] for item in calculation_citation["evidence"]["inputs"]] == [
        citations[0]["citationId"],
        citations[1]["citationId"],
    ]
    assert result.bundle["integrity"]["unknownCitationIds"] == []
    assert result.bundle["quality"]["status"] == "passed", result.bundle["quality"]
    # The two calculation inputs are materialized first. Claim candidate
    # discovery also addresses the two fiscal-year scope values used in the
    # prose, but it must not expand the rest of either statement row.
    assert result.bundle["integrity"]["evidenceMaterializedCount"] == 4


def test_claim_audit_materializes_only_matching_collection_fields_for_missing_binding() -> None:
    source = {
        "sourceId": "financials:600519",
        "providerId": "valuz-stock",
        "sourceType": "dataset",
        "title": "Company income statement · 600519",
        "retrievedAt": "2026-08-01T08:00:00Z",
    }
    raw = {
        "data": [
            {
                "symbol": "600519",
                "fiscal_year": 2024,
                "period": "FY",
                "operating_revenue": 174_144_000_000,
                "net_profit": 86_228_000_000,
            }
        ],
        "_valuz_evidence": [
            {
                "evidenceHandle": "ev_legacy_revenue_87654321",
                "source": source,
                "evidence": {
                    "kind": "structured-data",
                    "datasetId": "financials",
                    "toolName": "company_income_statement",
                    "recordKey": "600519|2024 FY",
                    "entityId": "600519",
                    "field": "data[0].operating_revenue",
                    "metric": "operating_revenue",
                    "value": 174_144_000_000,
                    "unit": "CNY",
                    "period": "2024 FY",
                    "capturedAt": "2026-08-01T08:00:00Z",
                },
            },
            {
                "evidenceHandle": "ev_legacy_profit_87654321",
                "source": source,
                "evidence": {
                    "kind": "structured-data",
                    "datasetId": "financials",
                    "toolName": "company_income_statement",
                    "recordKey": "600519|2024 FY",
                    "entityId": "600519",
                    "field": "data[0].net_profit",
                    "metric": "net_profit",
                    "value": 86_228_000_000,
                    "unit": "CNY",
                    "period": "2024 FY",
                    "capturedAt": "2026-08-01T08:00:00Z",
                },
            },
        ],
    }
    visible = compact_citation_tool_content(raw)
    private = private_citation_tool_content(raw)
    assert visible is not None and private is not None
    registry = EvidenceRegistry()
    registry.register_tool_projection(visible, private, trusted_private=True)

    result = CitationGuard(
        registry,
        message_id="msg-audit-address",
        user_prompt="Cite the operating revenue",
        policy_available=True,
        verification_enabled=False,
    ).finalize("Operating revenue was CNY 174144000000 in 2024.")

    assert "citation://" in result.text
    assert result.bundle is not None
    assert len(result.bundle["citations"]) == 1
    assert result.bundle["citations"][0]["evidence"]["metric"] == "operating_revenue"
    assert result.bundle["integrity"]["evidenceMaterializedCount"] == 2
    assert result.bundle["integrity"]["evidenceRegisteredCount"] == 2


def test_cross_source_collection_addresses_materialize_as_distinct_citations() -> None:
    registry = EvidenceRegistry()
    addresses: list[str] = []
    for suffix, provider in (("primary", "valuz-stock"), ("filing", "exchange")):
        raw = {
            "data": {"symbol": "600519", "operating_revenue": 174_144_000_000},
            "_valuz_evidence": [
                {
                    "evidenceHandle": f"ev_{suffix}_revenue_12345678",
                    "source": {
                        "sourceId": f"financials:{suffix}:600519",
                        "providerId": provider,
                        "sourceType": "dataset",
                        "title": f"Income statement · {suffix}",
                        "retrievedAt": "2026-08-01T08:00:00Z",
                    },
                    "evidence": {
                        "kind": "structured-data",
                        "datasetId": f"financials:{suffix}",
                        "toolName": "company_income_statement",
                        "recordKey": "600519|2024 FY",
                        "entityId": "600519",
                        "field": "data.operating_revenue",
                        "metric": "operating_revenue",
                        "value": 174_144_000_000,
                        "unit": "CNY",
                        "period": "2024 FY",
                        "capturedAt": "2026-08-01T08:00:00Z",
                    },
                }
            ],
        }
        visible = compact_citation_tool_content(raw)
        private = private_citation_tool_content(raw)
        assert visible is not None and private is not None
        registry.register_tool_projection(visible, private, trusted_private=True)
        addresses.append(
            f"evidence://{visible['_valuz_evidence_hint']['collectionHandle']}"
            "#/data/operating_revenue"
        )

    result = CitationGuard(
        registry,
        message_id="msg-cross-source",
        user_prompt="Cross-check operating revenue",
        policy_available=True,
        verification_enabled=False,
    ).finalize(
        "Operating revenue was CNY 174144000000 "
        f"[primary]({addresses[0]}) [corroborating]({addresses[1]})."
    )

    assert result.bundle is not None
    assert len(result.bundle["citations"]) == 2
    assert {item["source"]["providerId"] for item in result.bundle["citations"]} == {
        "valuz-stock",
        "exchange",
    }
    assert result.bundle["integrity"]["evidenceCollectionCount"] == 2
    assert result.bundle["integrity"]["evidenceMaterializedCount"] == 2


def test_registry_ignores_malformed_and_non_json_results() -> None:
    registry = EvidenceRegistry()

    assert registry.register_tool_result("not json") == 0
    assert (
        registry.register_tool_result(
            {"_valuz_evidence": {"evidenceHandle": "bad", "source": {}, "evidence": {}}}
        )
        == 0
    )
    assert len(registry) == 0
    assert registry.rejected_count == 1
    assert registry.had_evidence_activity is True


def test_registry_reports_oversized_evidence_payload_instead_of_silent_drop() -> None:
    registry = EvidenceRegistry()
    registry._MAX_TOOL_RESULT_CHARS = 32

    assert registry.register_tool_result(json.dumps({"_valuz_evidence": _item()})) == 0
    assert registry.rejected_count == 1
    assert registry.overflow_reasons == ("tool_result_invalid_or_oversized",)


def test_unrelated_rejected_tool_payload_does_not_degrade_valid_final_citation() -> None:
    registry = _registry(_item(locator={"kind": "pdf", "page": 1}))
    registry._MAX_TOOL_RESULT_CHARS = 32
    assert registry.register_tool_result(json.dumps({"_valuz_evidence": _item()})) == 0

    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Use the report",
        policy_available=True,
    )
    result = guard.finalize("Revenue [report](evidence://ev_revenue_2025).")

    assert result.bundle is not None
    assert result.bundle["integrity"]["status"] == "passed"
    assert result.bundle["integrity"]["evidenceRejectedCount"] == 1


def test_complete_document_coverage_marker_is_internal_not_citation_evidence() -> None:
    coverage = _item("ev_doc_coverage_12345678")
    coverage["evidence"] = {
        "kind": "structured-data",
        "datasetId": "document:doc-1",
        "toolName": "document_fetch",
        "recordKey": "doc-1:complete",
        "field": "document_coverage_complete",
        "metric": "document_coverage_complete",
        "value": True,
        "basis": "full-document",
        "capturedAt": "2026-08-02T08:00:00Z",
    }

    registry = EvidenceRegistry()
    assert (
        registry.register_tool_result(
            {"_valuz_evidence": coverage},
            tool_name="document_fetch",
        )
        == 0
    )
    assert registry.rejected_count == 0
    assert list(registry.values()) == []


def test_complete_document_coverage_does_not_auto_bind_when_verification_is_absent() -> None:
    coverage = _item("ev_doc_coverage_auto_12345678")
    coverage["evidence"] = {
        "kind": "structured-data",
        "datasetId": "document:doc-1",
        "toolName": "document_fetch",
        "recordKey": "doc-1:complete",
        "field": "document_coverage_complete",
        "metric": "document_coverage_complete",
        "value": True,
        "basis": "full-document",
        "capturedAt": "2026-08-02T08:00:00Z",
    }
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_result(
            {"_valuz_evidence": coverage},
            tool_name="document_fetch",
        )
        == 0
    )

    result = CitationGuard(
        registry,
        message_id="msg-coverage-auto",
        user_prompt="What was not disclosed?",
        policy_available=False,
        verification_enabled=False,
    ).finalize("AI 服务贡献百分点：原文未披露具体数字。")

    assert result.text == "AI 服务贡献百分点：原文未披露具体数字。"
    assert result.bundle is None


def test_registry_rejects_oversized_snapshots_and_locator_geometry() -> None:
    oversized = _item(locator={"kind": "pdf", "page": 1})
    oversized["evidence"]["quote"] = "x" * 32_001
    too_many_rects = _item(
        "ev_rects_12345678",
        locator={
            "kind": "pdf",
            "page": 1,
            "rects": [{"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1} for _ in range(129)],
        },
    )
    registry = EvidenceRegistry()

    assert registry.register_tool_result({"_valuz_evidence": [oversized, too_many_rects]}) == 0
    assert len(registry) == 0


def test_projection_registration_readiness_is_idempotent() -> None:
    registry = EvidenceRegistry()
    projection = {
        "_valuz_evidence": [
            _item("ev_ready_12345678", locator={"kind": "chunk", "chunkId": "chunk-1"})
        ]
    }

    assert registry.register_tool_projection(projection, projection, trusted_private=True) == 1
    assert registry.projection_is_registered(projection, trusted_private=True) is True
    assert registry.register_tool_projection(projection, projection, trusted_private=True) == 0
    assert registry.projection_is_registered(projection, trusted_private=True) is True


def test_registry_never_persists_signed_urls_paths_or_unknown_locator_fields() -> None:
    item = _item(
        locator={
            "kind": "pdf",
            "page": 12,
            "url": "https://private.invalid/file?token=secret",
            "absPath": "/Users/private/report.pdf",
        }
    )
    item["source"]["canonicalUrl"] = "https://private.invalid/report.pdf?X-Amz-Signature=secret"
    item["source"]["fileUrl"] = "https://private.invalid/file?token=secret"
    item["evidence"]["rawPayload"] = {"api_key": "secret"}

    registry = _registry(item)
    record = registry.get("ev_revenue_2025")

    assert record is not None
    assert "canonicalUrl" not in record.source
    assert "fileUrl" not in record.source
    assert "rawPayload" not in record.evidence
    assert record.locator == {"kind": "pdf", "page": 12}


def test_registry_rejects_evidence_outside_locked_document_scope() -> None:
    registry = EvidenceRegistry(allowed_document_ids={"doc-1"})
    allowed = _item("ev_allowed_12345678")
    outside = _item("ev_outside_12345678")
    outside["source"] = {
        **outside["source"],
        "sourceId": "doc-2",
        "documentId": "doc-2",
    }
    dataset = _item("ev_dataset_12345678")
    dataset["source"] = {
        **dataset["source"],
        "sourceId": "dataset-1",
        "sourceType": "dataset",
    }

    assert (
        registry.register_tool_result(
            {"_valuz_evidence": [allowed, outside, dataset]},
        )
        == 1
    )
    assert registry.get("ev_allowed_12345678") is not None
    assert registry.get("ev_outside_12345678") is None
    assert registry.get("ev_dataset_12345678") is None


def test_guard_binds_known_handle_and_builds_bundle_from_registry() -> None:
    registry = _registry(_item(locator={"kind": "pdf", "page": 12}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="What changed?",
        policy_available=True,
    )

    result = guard.finalize("Revenue increased [Annual Report](evidence://ev_revenue_2025).")

    assert "evidence://" not in result.text
    assert "citation://cit_" in result.text
    assert result.bundle is not None
    assert result.bundle["integrity"] == {
        "status": "passed",
        "unknownCitationIds": [],
        "unusedCitationIds": [],
        "missingLocatorCitationIds": [],
        "repairAttempts": 0,
        "policyRevision": "citation-v1",
        "evidenceRegisteredCount": 1,
        "evidenceCollectionCount": 0,
        "evidenceAddressRequestedCount": 0,
        "evidenceMaterializedCount": 0,
        "evidenceMaterializationRejectedCount": 0,
        "evidenceRejectedCount": 0,
        "evidenceOverflowReasons": [],
    }
    citation = result.bundle["citations"][0]
    assert citation["source"]["title"] == "Annual Report"
    assert citation["evidence"]["quote"] == "Revenue increased by 12%."
    assert citation["locator"] == {"kind": "pdf", "page": 12}


def test_guard_can_render_citations_without_running_quality_verification() -> None:
    registry = _registry(_item(locator={"kind": "pdf", "page": 12}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Use the report",
        policy_available=True,
        verification_enabled=False,
    )

    result = guard.finalize("Revenue increased [Annual Report](evidence://ev_revenue_2025).")

    assert result.bundle is not None
    assert result.bundle["integrity"]["status"] == "passed"
    assert "quality" not in result.bundle
    assert "citation://" in result.text


def test_disabled_guard_removes_protocol_links_without_rendering_indices() -> None:
    registry = _registry(_item(locator={"kind": "pdf", "page": 12}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Use the report",
        policy_available=False,
        enabled=False,
        verification_enabled=False,
    )

    result = guard.finalize("Revenue increased [Annual Report](evidence://ev_revenue_2025).")

    assert result.bundle is None
    assert result.text == "Revenue increased Annual Report."


def test_guard_normalizes_protocol_link_label_to_numeric_marker() -> None:
    registry = _registry(_item(locator={"kind": "pdf", "page": 12}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Use the report",
        policy_available=True,
    )

    result = guard.finalize("Revenue increased [source](evidence://ev_revenue_2025).")

    assert "[source]" not in result.text
    assert "[1](citation://cit_" in result.text


def test_strict_guard_does_not_require_citation_for_educational_formula() -> None:
    guard = CitationGuard(
        EvidenceRegistry(),
        message_id="msg-1",
        user_prompt="ROE 是什么意思？怎么计算？",
        policy_available=True,
        quality_policy={"mode": "strict-domain", "config": {}},
        force_required=True,
    )

    result = guard.finalize(
        "ROE（Return on Equity）衡量股东权益创造净利润的效率。\n\n"
        "ROE = 净利润 / 平均股东权益 × 100%。"
    )

    assert result.bundle is None
    assert "ROE" in result.text


def test_strict_guard_respects_explicit_request_not_to_cite() -> None:
    guard = CitationGuard(
        EvidenceRegistry(),
        message_id="msg-1",
        user_prompt="ROE是什么意思？只解释定义和公式，不要引用外部资料。",
        policy_available=True,
        quality_policy={"mode": "strict-domain", "config": {}},
        force_required=True,
    )

    result = guard.finalize(
        "**净资产收益率（ROE，Return on Equity）**\n\n"
        "衡量公司利用股东权益创造净利润的效率。\n\n"
        "ROE = 净利润 / 平均股东权益 × 100%。"
    )

    assert result.bundle is None


def test_guard_normalizes_fallback_marker_without_reporting_repair() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Summarize the document",
        policy_available=True,
    )

    result = guard.finalize("Revenue increased [[evidence:ev_revenue_2025]].")

    assert result.bundle is not None
    assert result.bundle["integrity"]["status"] == "passed"
    assert result.bundle["integrity"]["repairAttempts"] == 0
    assert "citation://cit_" in result.text


def test_guard_normalizes_bare_numbered_claims_from_trusted_source_list() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Give me numbered citations",
        policy_available=True,
    )

    result = guard.finalize(
        "Revenue was 100 USD [1]. Profit was 20 USD [1].\n\n"
        "Sources:\n"
        "[1] [Annual Report](evidence://ev_revenue_2025)"
    )

    assert result.bundle is not None
    citation_id = result.bundle["citations"][0]["citationId"]
    assert f"100 USD [1](citation://{citation_id})" in result.text
    assert f"20 USD [1](citation://{citation_id})" in result.text
    assert "Sources:" not in result.text
    assert result.text.count(f"citation://{citation_id}") == 2
    assert result.bundle["integrity"]["status"] == "passed"
    assert result.bundle["integrity"]["repairAttempts"] == 0


def test_guard_removes_redundant_chinese_source_section_and_divider() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请使用引用",
        policy_available=True,
    )

    result = guard.finalize(
        "营收增长 [年报](evidence://ev_revenue_2025)。\n\n"
        "---\n\n"
        "**来源：**\n\n"
        "[1] [年报](evidence://ev_revenue_2025)"
    )

    assert result.bundle is not None
    assert "来源" not in result.text
    assert "\n---" not in result.text
    assert result.text.count("citation://") == 1


def test_guard_removes_redundant_inline_data_source_note() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="比较两家公司并引用年报",
        policy_available=True,
    )

    result = guard.finalize(
        "| 公司 | 营业收入 |\n"
        "|---|---:|\n"
        "| 示例公司 | 100 [年报](evidence://ev_revenue_2025) |\n\n"
        "数据来源：示例公司年度报告 [年报](evidence://ev_revenue_2025)。"
    )

    assert "数据来源" not in result.text
    assert result.text.count("citation://") == 1


def test_guard_preserves_partial_source_section_with_external_links() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请使用引用",
        policy_available=True,
    )

    result = guard.finalize(
        "营收增长 [1]，渠道占比下降 [2]。\n\n"
        "**来源：**\n"
        "[1] [年报](evidence://ev_revenue_2025)\n"
        "[2] [研报](https://example.com/report)"
    )

    assert "来源" in result.text
    assert "https://example.com/report" in result.text


def test_guard_does_not_guess_ambiguous_numbered_source_bindings() -> None:
    registry = _registry(
        _item(locator={"kind": "chunk", "chunkId": "chunk-1"}),
        _item(
            "ev_other_12345678",
            locator={"kind": "chunk", "chunkId": "chunk-2"},
        ),
    )
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Give me numbered citations",
        policy_available=True,
    )

    result = guard.finalize(
        "Revenue was 100 USD [1].\n\n"
        "Sources:\n"
        "[1] [Annual Report](evidence://ev_revenue_2025)\n"
        "[1] [Other Report](evidence://ev_other_12345678)"
    )

    assert "100 USD [1]." in result.text
    assert "100 USD [1](citation://" not in result.text


def test_guard_never_promotes_unknown_model_minted_source() -> None:
    guard = CitationGuard(
        EvidenceRegistry(),
        message_id="msg-1",
        user_prompt="请给出引用",
        policy_available=True,
    )

    result = guard.finalize(
        "Claim [fake](evidence://ev_fake_12345678) and [also fake](citation://cit_model_minted)."
    )

    assert result.text == "Claim fake and also fake."
    assert result.bundle is not None
    assert result.bundle["citations"] == []
    assert result.bundle["integrity"]["status"] == "degraded"
    assert result.bundle["integrity"]["unknownCitationIds"] == [
        "ev_fake_12345678",
        "cit_model_minted",
    ]


def test_guard_strips_truncated_protocol_prefix_without_dropping_limitation_text() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请给出引用",
        policy_available=True,
    )

    result = guard.finalize(
        "收入为 100。[source](evidence:原文未披露其他数字 [source](evidence://ev_revenue_2025)。"
    )

    assert "evidence:" not in result.text
    assert "source" not in result.text
    assert "原文未披露其他数字" in result.text
    assert "citation://" in result.text
    assert result.bundle is not None
    assert result.bundle["integrity"]["status"] == "degraded"
    assert result.bundle["integrity"]["malformedProtocolBindingCount"] == 1


def test_guard_drops_contradictory_generic_limitation_from_cited_value_cell() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="只输出 Markdown 表格并引用",
        policy_available=True,
    )

    result = guard.finalize(
        "| 公司 | 营业收入 |\n"
        "|---|---:|\n"
        "| SK海力士 | 79.3187万亿韩元 [source](evidence:原文未披露具体数字 "
        "[source](evidence://ev_revenue_2025) |"
    )

    assert "79.3187万亿韩元" in result.text
    assert "原文未披露具体数字" not in result.text
    assert "evidence:" not in result.text
    assert "citation://" in result.text


def test_guard_rejects_canonical_ids_when_replayed_as_runtime_input() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请给出引用",
        policy_available=True,
    )
    sealed = guard.finalize("Revenue increased by 12% [source](evidence://ev_revenue_2025).")

    assert sealed.bundle is not None
    assert sealed.bundle["integrity"]["unknownCitationIds"] == []

    replayed = guard.finalize(sealed.text)

    assert replayed.bundle is not None
    assert replayed.text != sealed.text
    assert replayed.bundle["integrity"]["unknownCitationIds"]
    assert replayed.bundle["citations"] == []


def test_guard_removes_protocol_source_placeholders_without_rewriting_prose() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请列出有引用的数据",
        policy_available=True,
    )

    result = guard.finalize(
        "收入同比增长 12%。[1](evidence://ev_revenue_2025) source\n\n"
        "The primary source is the annual report."
    )

    assert "citation://cit_" in result.text
    assert "12%。[1](citation://" in result.text
    assert ") source" not in result.text
    assert "The primary source is the annual report." in result.text


def test_guard_removes_legacy_reportify_summary_source_link() -> None:
    guard = CitationGuard(
        EvidenceRegistry(),
        message_id="msg-1",
        user_prompt="请列出十家公司并引用来源",
        policy_available=True,
    )

    result = guard.finalize("泛微网络利润同比增长42%。[source](:1239333165953323008:summary)")

    assert result.text == "泛微网络利润同比增长42%。"
    assert "source" not in result.text
    assert ":summary" not in result.text


def test_guard_removes_non_navigable_relative_source_link() -> None:
    guard = CitationGuard(
        EvidenceRegistry(),
        message_id="msg-1",
        user_prompt="请列出十家公司并引用来源",
        policy_available=True,
    )

    result = guard.finalize(
        "万兴科技 AI 原生收入同比增长90%。[source](.cn/reports/1239333165953323008)"
    )

    assert result.text == "万兴科技 AI 原生收入同比增长90%。"


def test_guard_moves_citation_out_of_a_split_grouped_number() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请引用年报原文",
        policy_available=True,
    )

    result = guard.finalize(
        "营业收入为 170,899,152,27 [source](evidence://ev_revenue_2025)6.34 元。"
    )

    assert "170,899,152,276.34 元" in result.text
    assert "170,899,152,27 " not in result.text
    assert result.text.index("citation://") > result.text.index("170,899,152,276.34")


def test_guard_moves_citation_out_of_a_split_decimal_fraction() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请引用年报原文",
        policy_available=True,
    )

    result = guard.finalize(
        "营业收入为 170,899,152,276. [source](evidence://ev_revenue_2025)34 元。"
    )

    assert "170,899,152,276.34 元" in result.text
    assert "276. " not in result.text
    assert result.text.index("citation://") > result.text.index("170,899,152,276.34")


def test_guard_removes_protocol_source_placeholders_from_markdown_table_cells() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请列出有引用的数据",
        policy_available=True,
    )

    result = guard.finalize(
        "| 指标 | 数值 |\n"
        "| --- | --- |\n"
        "| 营业收入 | 100亿元 [1](evidence://ev_revenue_2025) source |\n"
        "| 说明 | The primary source is the annual report. |"
    )

    assert "citation://cit_" in result.text
    assert ") source |" not in result.text
    assert "The primary source is the annual report." in result.text


def test_guard_moves_citation_after_table_boundary_into_last_cell() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请用表格列出数据和计算公式",
        policy_available=True,
    )

    result = guard.finalize(
        "| 项目 | 数值 | 计算公式 |\n"
        "| --- | ---: | --- |\n"
        "| 营业收入 | 100亿元 | 100 ÷ 100 |"
        "[1](evidence://ev_revenue_2025)"
    )

    data_row = result.text.splitlines()[2]
    assert data_row.endswith(" |")
    assert data_row.count("|") == 4
    assert "|[" not in data_row
    assert "citation://cit_" in data_row


def test_guard_folds_trailing_citation_only_overflow_cell_into_last_declared_cell() -> None:
    registry = _registry(_item(locator={"kind": "chunk", "chunkId": "chunk-1"}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请用表格列出数据和计算公式",
        policy_available=True,
    )

    result = guard.finalize(
        "| 项目 | 原始金额 | 折合亿元 |\n"
        "| --- | ---: | ---: |\n"
        "| 2026 Q1 营业收入 | 10,285,128,726 | 102.85 亿元 |"
        "[1](evidence://ev_revenue_2025) |"
    )

    data_row = result.text.splitlines()[2]
    assert data_row.endswith(" |")
    assert data_row.count("|") == 4
    assert "102.85 亿元 [1](citation://cit_" in data_row


def test_guard_focuses_long_text_preview_on_the_cited_table_row() -> None:
    item = _item(locator={"kind": "chunk", "chunkId": "chunk-1"})
    item["evidence"] = {
        "kind": "text",
        "quote": (
            "| 其他分部 | 100 | 2.0 |\n" * 60
            + "| 直销 | 74,843,327,030.79 | 11.32 |\n"
            + "| 批发代理 | 95,768,511,021.23 | 19.73 |"
        ),
        "snippet": "| 其他分部 | 100 | 2.0 |" * 20,
        "capturedAt": "2026-07-30T10:00:00Z",
    }
    registry = _registry(item)
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="请给出直销收入并引用",
        policy_available=True,
    )

    result = guard.finalize(
        "直销营业收入为74,843,327,030.79元，同比增长11.32% [1](evidence://ev_revenue_2025)。"
    )

    assert result.bundle is not None
    evidence = result.bundle["citations"][0]["evidence"]
    assert "直销 | 74,843,327,030.79 | 11.32" in evidence["snippet"]
    assert len(evidence["quote"]) > len(evidence["snippet"])


def test_guard_drops_unknown_protocol_label_instead_of_publishing_source() -> None:
    guard = CitationGuard(
        EvidenceRegistry(),
        message_id="msg-1",
        user_prompt="请给出引用",
        policy_available=True,
    )

    result = guard.finalize("结论。[source](evidence://ev_unknown_12345678)")

    assert result.text == "结论。"
    assert result.bundle is not None
    assert result.bundle["integrity"]["unknownCitationIds"] == ["ev_unknown_12345678"]


def test_guard_drops_unknown_numeric_citation_labels_without_leaking_digits() -> None:
    guard = CitationGuard(
        EvidenceRegistry(),
        message_id="msg-1",
        user_prompt="answer with citations",
        policy_available=True,
    )

    result = guard.finalize(
        "Claim [1](evidence://W11111111)[2](evidence://W22222222)[3](evidence://W33333333)."
    )

    assert result.text == "Claim."
    assert "123" not in result.text
    assert result.bundle is not None
    assert result.bundle["integrity"]["unknownCitationIds"] == [
        "W11111111",
        "W22222222",
        "W33333333",
    ]


def test_guard_marks_missing_document_locator_and_unused_evidence() -> None:
    unrelated = _item(
        "ev_other_12345678",
        locator={"kind": "chunk", "chunkId": "chunk-other"},
    )
    unrelated["evidence"]["quote"] = "Operating margin increased by 3 points."
    unrelated["evidence"]["snippet"] = unrelated["evidence"]["quote"]
    registry = _registry(_item(), unrelated)
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Use the report",
        policy_available=True,
    )

    result = guard.finalize("[report](evidence://ev_revenue_2025)")

    assert result.bundle is not None
    integrity = result.bundle["integrity"]
    assert integrity["status"] == "degraded"
    assert len(integrity["missingLocatorCitationIds"]) == 1
    assert len(integrity["unusedCitationIds"]) == 1


def test_guard_does_not_add_bundle_to_ordinary_chat() -> None:
    guard = CitationGuard(
        EvidenceRegistry(),
        message_id="msg-1",
        user_prompt="你好",
        policy_available=True,
    )

    result = guard.finalize("你好！")

    assert result.text == "你好！"
    assert result.bundle is None


def test_guard_requires_citations_for_locked_document_research_without_evidence() -> None:
    guard = CitationGuard(
        EvidenceRegistry(allowed_document_ids={"doc-1"}),
        message_id="msg-1",
        user_prompt="What is revenue?",
        policy_available=True,
        force_required=True,
    )

    result = guard.finalize("Revenue was 100.")

    assert result.bundle is not None
    assert result.bundle["citations"] == []
    assert result.bundle["integrity"]["status"] == "degraded"


def test_guard_fails_closed_when_required_skill_is_unavailable() -> None:
    registry = _registry(_item(locator={"kind": "pdf", "page": 1}))
    guard = CitationGuard(
        registry,
        message_id="msg-1",
        user_prompt="Use evidence",
        policy_available=False,
    )

    result = guard.finalize("[report](evidence://ev_revenue_2025)")

    assert result.bundle is not None
    assert result.bundle["integrity"]["status"] == "degraded"


def test_guard_promotes_calculation_input_handles_to_canonical_dependencies() -> None:
    left = _item(
        "ev_left_12345678",
        locator={"kind": "chunk", "chunkId": "left"},
    )
    left["source"]["sourceType"] = "dataset"
    left["source"].pop("documentId")
    left["source"].pop("documentVersion")
    left["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "stock.income_statement",
        "recordKey": "issuer:2025",
        "field": "current",
        "value": 120,
        "unit": "USDm",
        "period": "FY2025",
        "capturedAt": "2026-07-30T10:00:00Z",
    }
    right = json.loads(json.dumps(left))
    right["evidenceHandle"] = "ev_right_12345678"
    right["source"]["sourceId"] = "dataset-2"
    right["evidence"]["recordKey"] = "issuer:2024"
    right["evidence"]["field"] = "prior"
    right["evidence"]["value"] = 100
    calculation = _item("ev_calculation_12345678")
    calculation["source"]["sourceId"] = "runtime-calc"
    calculation["source"]["providerId"] = "runtime"
    calculation["source"]["sourceType"] = "tool-result"
    calculation["source"].pop("documentId")
    calculation["source"].pop("documentVersion")
    calculation["evidence"] = {
        "kind": "calculation",
        "expression": "((current / prior) - 1) * 100",
        "inputs": [
            {
                "name": "current",
                "citationId": "ev_left_12345678",
                "value": 120,
                "unit": "USDm",
            },
            {
                "name": "prior",
                "citationId": "ev_right_12345678",
                "value": 100,
                "unit": "USDm",
            },
        ],
        "result": 20,
        "unit": "%",
        "rounding": "2dp",
        "calculatedAt": "2026-07-30T10:00:00Z",
        "entityId": "issuer-1",
        "entityName": "Issuer",
        "metric": "revenue_growth_rate",
        "period": "FY2025",
        "scope": "consolidated",
        "basis": "reported",
    }
    registry = _registry(left, right, calculation)
    guard = CitationGuard(
        registry,
        message_id="msg-calc",
        user_prompt="Calculate growth with citations",
        policy_available=True,
    )

    result = guard.finalize("Growth was 20% [calculation](evidence://ev_calculation_12345678).")

    assert result.bundle is not None
    citations = {
        citation["source"]["sourceId"]: citation for citation in result.bundle["citations"]
    }
    calculation_citation = citations["runtime-calc"]
    assert calculation_citation["evidence"]["metric"] == "revenue_growth_rate"
    assert calculation_citation["evidence"]["entityId"] == "issuer-1"
    assert calculation_citation["evidence"]["scope"] == "consolidated"
    input_ids = [item["citationId"] for item in calculation_citation["evidence"]["inputs"]]
    assert input_ids == [
        citations["doc-1"]["citationId"],
        citations["dataset-2"]["citationId"],
    ]
    assert result.bundle["integrity"]["unknownCitationIds"] == []
    assert result.bundle["integrity"]["unusedCitationIds"] == []


def test_guard_auto_binds_one_unique_structured_candidate_without_model_repair() -> None:
    margin = _item("ev_margin_12345678")
    margin["source"] = {
        "sourceId": "financials:600519:2024",
        "providerId": "market-data",
        "sourceType": "dataset",
        "title": "Financial data",
        "retrievedAt": "2026-08-01T08:00:00Z",
    }
    margin["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "company_income_statement",
        "recordKey": "600519|2024 FY",
        "field": "gross_margin",
        "value": 23.5,
        "unit": "%",
        "period": "2024 FY",
        "capturedAt": "2026-08-01T08:00:00Z",
    }
    registry = _registry(margin)
    guard = CitationGuard(
        registry,
        message_id="msg-auto-bind",
        user_prompt="What was gross margin?",
        policy_available=True,
    )

    result = guard.finalize("Gross margin was 23.5% in 2024.")

    assert result.bundle is not None
    assert result.text.count("citation://") == 1
    assert result.bundle["integrity"]["repairAttempts"] == 0
    assert result.bundle["quality"]["claims"][0]["status"] == "auto-bound"
    assert result.bundle["quality"]["metrics"]["claimAutoBoundCount"] == 1


def test_guard_combines_user_thresholds_with_unique_structured_values() -> None:
    price = _item("ev_price_12345678")
    price["source"] = {
        "sourceId": "quote:MRVL",
        "providerId": "market-data",
        "sourceType": "dataset",
        "title": "MRVL stock quote",
        "retrievedAt": "2026-08-03T10:00:00Z",
    }
    price["evidence"] = {
        "kind": "structured-data",
        "datasetId": "stock_quote",
        "toolName": "stock_quote",
        "recordKey": "MRVL|2026-08-03",
        "entityId": "MRVL",
        "field": "price",
        "metric": "stock_price",
        "value": 193.775,
        "unit": "USD",
        "asOf": "2026-08-03",
        "capturedAt": "2026-08-03T10:00:00Z",
    }
    ma20 = json.loads(json.dumps(price))
    ma20["evidenceHandle"] = "ev_ma20_12345678"
    ma20["source"]["sourceId"] = "factor:MRVL:MA20"
    ma20["source"]["title"] = "MRVL MA20"
    ma20["evidence"].update(
        {
            "datasetId": "factors_compute",
            "recordKey": "MRVL|MA20|2026-08-03",
            "field": "/datas/0/factor_value",
            "metric": "moving_average_20",
            "value": 203.69,
        }
    )
    policy = {
        "mode": "strict-domain",
        "config": {
            "semantics": {
                "metric_ontology": {
                    "metrics": {
                        "stock_price": {
                            "aliases": ["current price", "stock price"],
                            "fields": ["price"],
                        },
                        "moving_average_20": {
                            "aliases": ["MA20"],
                            "fields": ["moving_average_20"],
                        },
                    }
                },
                "unit_ontology": {
                    "units": {"usd": {"canonical": "USD", "aliases": ["USD", "$"], "scale": 1}}
                },
            },
            "rules": {"factual_claim": {"citation_required": True}},
        },
    }
    guard = CitationGuard(
        _registry(price, ma20),
        message_id="msg-user-threshold-composite",
        user_prompt="Use a stop-loss threshold of 160 USD.",
        policy_available=True,
        quality_policy=policy,
    )

    result = guard.finalize(
        "The stop-loss threshold is 160 USD, current price is 193.78 USD, and MA20 is 203.69 USD."
    )

    assert result.bundle is not None
    assert result.text.count("citation://") == 2
    assert result.bundle["quality"]["metrics"]["unsourcedClaimCount"] == 0
    assert result.bundle["quality"]["metrics"]["unverifiedClaimCount"] == 0


def test_guard_prefers_canonical_metric_over_agreeing_structured_mirrors() -> None:
    price = _item("ev_price_direct_12345678")
    price["source"] = {
        "sourceId": "quote:MRVL",
        "providerId": "market-data",
        "sourceType": "dataset",
        "title": "MRVL stock quote",
        "retrievedAt": "2026-08-03T10:00:00Z",
    }
    price["evidence"] = {
        "kind": "structured-data",
        "datasetId": "stock_quote",
        "toolName": "stock_quote",
        "recordKey": "MRVL|2026-08-03",
        "entityId": "MRVL",
        "field": "/data/items/9/stock_price",
        "metric": "stock_price",
        "value": 193.775,
        "asOf": "2026-08-03",
        "capturedAt": "2026-08-03T10:00:00Z",
    }
    mirrors = []
    for period in (20, 60, 120, 250):
        mirror = json.loads(json.dumps(price))
        mirror["evidenceHandle"] = f"ev_price_ma{period}_12345678"
        mirror["source"]["sourceId"] = f"factor:MRVL:MA{period}"
        mirror["source"]["title"] = f"MRVL MA{period}"
        mirror["evidence"].update(
            {
                "datasetId": "factors_compute",
                "toolName": "factors_compute",
                "field": "/datas/0/close",
                "metric": "close",
            }
        )
        mirrors.append(mirror)
    policy = {
        "mode": "strict-domain",
        "config": {
            "semantics": {
                "metric_ontology": {
                    "metrics": {
                        "stock_price": {
                            "aliases": ["current price", "stock price", "现价"],
                            "fields": ["stock_price", "close"],
                        }
                    }
                },
                "unit_ontology": {
                    "units": {
                        "usd": {
                            "canonical": "USD",
                            "aliases": ["USD", "$"],
                            "scale": 1,
                        }
                    }
                },
            },
            "rules": {"factual_claim": {"citation_required": True}},
        },
    }
    result = CitationGuard(
        _registry(price, *mirrors),
        message_id="msg-canonical-price-over-mirrors",
        user_prompt="Use a stop-loss threshold of 160 USD.",
        policy_available=True,
        quality_policy=policy,
    ).finalize("Current price was $193.78, above the $160 stop-loss threshold.")

    assert result.bundle is not None
    assert result.text.count("citation://") == 1
    assert len(result.bundle["citations"]) == 1
    assert result.bundle["citations"][0]["evidence"]["metric"] == "stock_price"
    assert result.bundle["quality"]["metrics"]["unsourcedClaimCount"] == 0
    assert result.bundle["quality"]["metrics"]["unverifiedClaimCount"] == 0


def test_guard_auto_binds_rounded_collection_values_and_collapses_duplicate_factor_paths() -> None:
    data = [
        {
            "date": "2026-08-03",
            "symbol": "MRVL",
            "close": 193.775,
            "factor_value": 203.69,
            "indicators": {"ma(close, 20)": 203.69},
        }
    ]
    raw_hash = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    collection_handle = "evc_factor_ma20_12345678"
    raw = {
        "datas": data,
        "_valuz_evidence": [
            {
                "version": 1,
                "kind": "structured-evidence-collection",
                "collectionHandle": collection_handle,
                "source": {
                    "sourceId": "reportify.factors_compute:MRVL:MA20:2026-08-03",
                    "providerId": "reportify",
                    "sourceType": "dataset",
                    "sourceCategory": "derived_analytics",
                    "title": "Reportify · factors_compute",
                    "retrievedAt": "2026-08-03T10:00:00Z",
                },
                "common": {
                    "datasetId": "reportify.factors_compute",
                    "toolName": "factors_compute",
                    "capturedAt": "2026-08-03T10:00:00Z",
                },
                "addressing": {
                    "mode": "json-pointer",
                    "contentRoot": "/datas",
                    "itemsPointer": "/datas",
                    "identityFields": ["/symbol", "/date"],
                    "allowedPathRoots": ["/datas"],
                },
                "semantics": {
                    "entity": {"symbol": "/symbol"},
                    "asOf": {"date": "/date"},
                    "metric": {
                        "mode": "field-map",
                        "fields": {"/factor_value": "moving_average_20"},
                    },
                },
                "contentHash": (f"sha256:{hashlib.sha256(raw_hash.encode('utf-8')).hexdigest()}"),
            }
        ],
    }
    visible = compact_citation_tool_content(raw)
    private = private_citation_tool_content(raw)
    assert visible is not None and private is not None
    registry = EvidenceRegistry()
    assert (
        registry.register_tool_projection(
            visible,
            private,
            tool_name="factors_compute",
            trusted_private=True,
        )
        == 1
    )
    policy = {
        "mode": "strict-domain",
        "config": {
            "semantics": {
                "metric_ontology": {
                    "metrics": {
                        "stock_price": {
                            "aliases": ["current price", "stock price"],
                            "fields": ["close"],
                        },
                        "moving_average_20": {
                            "aliases": ["MA20", "ma(close, 20)"],
                            "fields": ["moving_average_20", "factor_value"],
                        },
                    }
                },
                "unit_ontology": {
                    "units": {
                        "usd": {
                            "canonical": "USD",
                            "aliases": ["USD", "$"],
                            "scale": 1,
                        }
                    }
                },
            },
            "rules": {"factual_claim": {"citation_required": True}},
        },
    }
    result = CitationGuard(
        registry,
        message_id="msg-rounded-factor-collection",
        user_prompt="Show the current price and MA20.",
        policy_available=True,
        quality_policy=policy,
    ).finalize("Current price was $193.78.\n\nMA20 was $203.69.")

    assert result.bundle is not None
    assert result.text.count("citation://") == 2
    assert len(result.bundle["citations"]) == 2
    assert {citation["evidence"]["metric"] for citation in result.bundle["citations"]} == {
        "close",
        "moving_average_20",
    }
    assert result.bundle["quality"]["metrics"]["unsourcedClaimCount"] == 0
    assert result.bundle["quality"]["metrics"]["unverifiedClaimCount"] == 0


def test_guard_auto_binds_composite_claim_to_multiple_document_excerpts() -> None:
    quotes = (
        "Microsoft AI capacity will grow by more than 80%。",
        "Microsoft shortened dock-to-live time by 20%。",
        "Microsoft Copilot throughput improved by 4 倍。",
    )
    items = []
    for index, quote in enumerate(quotes, start=1):
        item = _item(f"ev_msft_q{index}_12345678", locator={"kind": "pdf", "page": index})
        item["source"].update(
            {
                "sourceId": f"msft-q{index}",
                "documentId": f"msft-q{index}",
                "title": f"Microsoft FY2026 Q{index} earnings call",
            }
        )
        item["evidence"].update({"quote": quote, "snippet": quote})
        items.append(item)
    guard = CitationGuard(
        _registry(*items),
        message_id="msg-composite-auto-bind",
        user_prompt="请引用微软电话会原文分析。",
        policy_available=True,
        force_required=True,
    )

    result = guard.finalize(
        "Microsoft AI 容量增长 80%，dock-to-live 缩短 20%，Copilot 吞吐提升 4 倍。"
    )

    assert result.bundle is not None
    assert result.text.count("citation://") == 3
    assert len(result.bundle["citations"]) == 3
    assert result.bundle["quality"]["claims"][0]["status"] == "auto-bound"
    assert result.bundle["quality"]["metrics"]["unsourcedClaimCount"] == 0
    assert result.bundle["quality"]["metrics"]["unverifiedClaimCount"] == 0


def test_guard_audits_equivalent_recap_binding_as_transitive_support() -> None:
    handle = "ev_msft_q3_throughput_12345678"
    item = _item(handle, locator={"kind": "pdf", "page": 8})
    item["source"].update(
        {
            "sourceId": "msft-q3",
            "documentId": "msft-q3",
            "title": "Microsoft FY2026 Q3 earnings call",
        }
    )
    item["evidence"].update(
        {
            "quote": (
                "Fairwater came online six weeks ahead of schedule and delivered "
                "a 40% improvement in inference throughput."
            ),
            "snippet": (
                "Fairwater came online six weeks ahead of schedule and delivered "
                "a 40% improvement in inference throughput."
            ),
        }
    )
    guard = CitationGuard(
        _registry(item),
        message_id="msg-equivalent-recap",
        user_prompt="请按季度引用电话会原文并提供对比表。",
        policy_available=True,
        force_required=True,
    )

    result = guard.finalize(
        "### FY2026 Q3\n\n"
        f"Fairwater 提前六周投产，推理吞吐量提升40% "
        f"[source](evidence://{handle})。\n\n"
        "| 维度 | Q3 |\n"
        "|---|---|\n"
        "| 核心优化指标 | 推理吞吐量（+40%） |"
    )

    assert result.bundle is not None
    assert result.text.count("citation://") == 2
    assert len(result.bundle["citations"]) == 1
    citation = result.bundle["citations"][0]
    assert citation["annotations"]["binding"]["equivalentClaimIds"]
    required_claims = [
        claim for claim in result.bundle["quality"]["claims"] if claim["citationRequired"]
    ]
    assert [claim["status"] for claim in required_claims] == ["passed", "auto-bound"]
    assert required_claims[1]["bindings"][0]["supportStatus"] == "equivalent-claim"
    assert result.bundle["quality"]["metrics"]["unsourcedClaimCount"] == 0
    assert result.bundle["quality"]["metrics"]["unverifiedClaimCount"] == 0


def test_guard_rebinds_one_wrong_sibling_field_to_unique_exact_evidence() -> None:
    wrong = _item("ev_end_date_12345678")
    wrong["source"].update({"sourceType": "dataset", "sourceId": "financials:2025"})
    wrong["source"].pop("documentId")
    wrong["source"].pop("documentVersion")
    wrong["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "company_income_statement",
        "recordKey": "issuer|FY2025",
        "field": "end_date",
        "metric": "end_date",
        "value": "2025-12-31",
        "period": "FY2025",
        "capturedAt": "2026-08-01T08:00:00Z",
    }
    revenue = json.loads(json.dumps(wrong))
    revenue["evidenceHandle"] = "ev_revenue_exact_12345678"
    revenue["evidence"].update(
        {
            "field": "revenue",
            "metric": "revenue",
            "value": 120,
            "unit": "USDm",
        }
    )
    guard = CitationGuard(
        _registry(wrong, revenue),
        message_id="msg-rebind",
        user_prompt="What was revenue?",
        policy_available=True,
    )

    result = guard.finalize(
        "FY2025 revenue was 120 USDm [source](evidence://ev_end_date_12345678)."
    )

    assert result.bundle is not None
    assert "ev_end_date_12345678" not in result.text
    assert len(result.bundle["citations"]) == 1
    citation = result.bundle["citations"][0]
    assert citation["evidence"]["field"] == "revenue"
    assert citation["annotations"]["binding"]["autoReboundClaimIds"]
    assert result.bundle["quality"]["claims"][0]["status"] == "auto-bound"


def test_guard_rebinds_calculation_inputs_to_unique_value_and_unit_fields() -> None:
    template = _item("ev_wrong_current_12345678")
    template["source"].update({"sourceType": "dataset", "sourceId": "financials"})
    template["source"].pop("documentId")
    template["source"].pop("documentVersion")
    template["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "company_income_statement",
        "recordKey": "issuer|FY2025",
        "field": "end_date",
        "metric": "end_date",
        "value": "2025-12-31",
        "period": "FY2025",
        "capturedAt": "2026-08-01T08:00:00Z",
    }
    wrong_prior = json.loads(json.dumps(template))
    wrong_prior["evidenceHandle"] = "ev_wrong_prior_12345678"
    wrong_prior["evidence"].update(
        {"recordKey": "issuer|FY2024", "value": "2024-12-31", "period": "FY2024"}
    )
    current = json.loads(json.dumps(template))
    current["evidenceHandle"] = "ev_revenue_current_12345678"
    current["evidence"].update(
        {"field": "revenue", "metric": "revenue", "value": 120, "unit": "USDm"}
    )
    prior = json.loads(json.dumps(wrong_prior))
    prior["evidenceHandle"] = "ev_revenue_prior_12345678"
    prior["evidence"].update(
        {"field": "revenue", "metric": "revenue", "value": 100, "unit": "USDm"}
    )
    calculation = _item("ev_growth_calculation_12345678")
    calculation["source"].update(
        {"sourceId": "runtime-calc", "providerId": "runtime", "sourceType": "tool-result"}
    )
    calculation["source"].pop("documentId")
    calculation["source"].pop("documentVersion")
    calculation["evidence"] = {
        "kind": "calculation",
        "expression": "(current - prior) / prior * 100",
        "inputs": [
            {
                "name": "current",
                "citationId": "ev_wrong_current_12345678",
                "value": 120,
                "unit": "USDm",
            },
            {
                "name": "prior",
                "citationId": "ev_wrong_prior_12345678",
                "value": 100,
                "unit": "USDm",
            },
        ],
        "result": 20,
        "unit": "%",
        "rounding": "2dp",
        "calculatedAt": "2026-08-01T08:00:00Z",
    }
    guard = CitationGuard(
        _registry(template, wrong_prior, current, prior, calculation),
        message_id="msg-calc-rebind",
        user_prompt="Calculate growth with citations",
        policy_available=True,
    )

    result = guard.finalize(
        "Growth was 20% [calculation](evidence://ev_growth_calculation_12345678)."
    )

    assert result.bundle is not None
    calculation_citation = next(
        item for item in result.bundle["citations"] if item["evidence"]["kind"] == "calculation"
    )
    revenue_citations = {
        item["evidence"].get("period"): item
        for item in result.bundle["citations"]
        if item["evidence"].get("field") == "revenue"
    }
    assert [item["citationId"] for item in calculation_citation["evidence"]["inputs"]] == [
        revenue_citations["FY2025"]["citationId"],
        revenue_citations["FY2024"]["citationId"],
    ]
    assert len(calculation_citation["annotations"]["binding"]["calculationInputAutoBindings"]) == 2


def test_guard_uses_calculation_dependencies_to_disambiguate_equal_input_values() -> None:
    wrong = _item("ev_wrong_input_12345678")
    wrong["source"].update({"sourceType": "dataset", "sourceId": "financials"})
    wrong["source"].pop("documentId")
    wrong["source"].pop("documentVersion")
    wrong["evidence"] = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "company_income_statement",
        "recordKey": "issuer|FY2025",
        "field": "finance_type",
        "value": "non_financial",
        "period": "FY2025",
        "capturedAt": "2026-08-01T08:00:00Z",
    }
    operating = json.loads(json.dumps(wrong))
    operating["evidenceHandle"] = "ev_operating_revenue_12345678"
    operating["evidence"].update(
        {"field": "operating_revenue", "metric": "operating_revenue", "value": 120, "unit": "USDm"}
    )
    total = json.loads(json.dumps(operating))
    total["evidenceHandle"] = "ev_total_revenue_12345678"
    total["evidence"].update({"field": "total_revenue", "metric": "total_revenue"})
    calculation = _item("ev_growth_calculation_12345678")
    calculation["source"].update(
        {"sourceId": "runtime-calc", "providerId": "runtime", "sourceType": "tool-result"}
    )
    calculation["source"].pop("documentId")
    calculation["source"].pop("documentVersion")
    calculation["evidence"] = {
        "kind": "calculation",
        "expression": "current",
        "inputs": [
            {
                "name": "current",
                "citationId": "ev_wrong_input_12345678",
                "value": 120,
                "unit": "USDm",
            }
        ],
        "result": 120,
        "unit": "USDm",
        "metric": "revenue_growth",
        "rounding": "0dp",
        "calculatedAt": "2026-08-01T08:00:00Z",
    }
    policy = {
        "mode": "strict-domain",
        "config": {
            "semantics": {
                "metric_ontology": {
                    "metrics": {
                        "revenue_growth": {"aliases": ["revenue growth"]},
                        "operating_revenue": {
                            "aliases": ["operating revenue"],
                            "fields": ["operating_revenue"],
                        },
                        "total_revenue": {
                            "aliases": ["total revenue"],
                            "fields": ["total_revenue"],
                        },
                    }
                },
                "unit_ontology": {
                    "units": {
                        "usd_million": {
                            "canonical": "USD",
                            "aliases": ["USDm"],
                            "scale": 1_000_000,
                        }
                    }
                },
                "calculation_dependencies": {"revenue_growth": ["operating_revenue"]},
            }
        },
    }
    guard = CitationGuard(
        _registry(wrong, operating, total, calculation),
        message_id="msg-calc-semantic-rebind",
        user_prompt="Calculate revenue growth with citations",
        policy_available=True,
        quality_policy=policy,
    )

    result = guard.finalize(
        "Revenue growth was 120 USDm [calculation](evidence://ev_growth_calculation_12345678)."
    )

    assert result.bundle is not None
    calculation_citation = next(
        item for item in result.bundle["citations"] if item["evidence"]["kind"] == "calculation"
    )
    input_id = calculation_citation["evidence"]["inputs"][0]["citationId"]
    selected = next(item for item in result.bundle["citations"] if item["citationId"] == input_id)
    assert selected["evidence"]["field"] == "operating_revenue"


def test_guard_does_not_auto_bind_ambiguous_structured_candidates() -> None:
    candidates = []
    for handle in ("ev_margin_first_12345678", "ev_margin_second_12345678"):
        item = _item(handle)
        item["source"] = {
            "sourceId": f"financials:{handle}",
            "providerId": "market-data",
            "sourceType": "dataset",
            "title": "Financial data",
            "retrievedAt": "2026-08-01T08:00:00Z",
        }
        item["evidence"] = {
            "kind": "structured-data",
            "datasetId": "financials",
            "toolName": "company_income_statement",
            "recordKey": "600519|2024 FY",
            "field": "gross_margin",
            "value": 23.5,
            "unit": "%",
            "period": "2024 FY",
            "capturedAt": "2026-08-01T08:00:00Z",
        }
        candidates.append(item)
    guard = CitationGuard(
        _registry(*candidates),
        message_id="msg-ambiguous",
        user_prompt="What was gross margin?",
        policy_available=True,
    )

    result = guard.finalize("Gross margin was 23.5% in 2024.")

    assert result.bundle is not None
    assert result.bundle["citations"] == []
    assert "citation://" not in result.text
    assert result.bundle["quality"]["claims"][0]["status"] == "unverified"
    assert "claim_evidence_ambiguous" in {
        issue["code"] for issue in result.bundle["quality"]["issues"]
    }


class _GuardSemanticVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[ClaimCandidate, tuple[EvidenceCandidate, ...]]] = []
        self.batch_calls = 0

    def verify_batch(
        self,
        requests: tuple[SemanticVerificationRequest, ...],
    ) -> dict[str, SemanticVerificationResult]:
        self.batch_calls += 1
        results: dict[str, SemanticVerificationResult] = {}
        for request in requests:
            claim = request.claim
            candidates = request.candidates
            self.calls.append((claim, candidates))
            results[claim.claim_id] = SemanticVerificationResult(
                verdict="entailed",
                evidence_handles=tuple(candidate.handle for candidate in candidates),
                confidence=0.98,
                covered_parts=(claim.exact,),
                verifier_revision="guard-semantic-test-v1",
            )
        return results


def test_guard_uses_bounded_semantic_verifier_for_bound_paraphrase() -> None:
    item = _item("ev_product_mix_paraphrase")
    item["evidence"].update(
        {
            "quote": "The richer product mix was the principal driver of margin expansion.",
            "snippet": "The richer product mix was the principal driver of margin expansion.",
        }
    )
    verifier = _GuardSemanticVerifier()
    guard = CitationGuard(
        _registry(item),
        message_id="msg-semantic-paraphrase",
        user_prompt="Explain the profitability improvement with a citation.",
        policy_available=True,
        semantic_verifier=verifier,
    )

    result = guard.finalize(
        "Premium products improved profitability "
        "[source](evidence://ev_product_mix_paraphrase)."
    )

    assert len(verifier.calls) >= 1
    assert result.bundle is not None
    assert result.bundle["quality"]["metrics"]["unverifiedClaimCount"] == 0
    assert result.bundle["quality"]["claims"][0]["status"] in {"passed", "auto-bound"}


def test_guard_batches_all_message_semantic_claims_in_one_verifier_call() -> None:
    first = _item("ev_product_mix_batch")
    first["evidence"].update(
        {
            "quote": "A richer mix of premium products was the main margin tailwind.",
            "snippet": "A richer mix of premium products was the main margin tailwind.",
        }
    )
    second = _item("ev_demand_batch")
    second["evidence"].update(
        {
            "quote": "Order intake and the backlog remained resilient through the quarter.",
            "snippet": "Order intake and the backlog remained resilient through the quarter.",
        }
    )
    verifier = _GuardSemanticVerifier()
    guard = CitationGuard(
        _registry(first, second),
        message_id="msg-semantic-batch",
        user_prompt="Summarize profitability and demand with citations.",
        policy_available=True,
        semantic_verifier=verifier,
    )

    result = guard.finalize(
        "Premium products improved profitability "
        "[source](evidence://ev_product_mix_batch). "
        "Demand remained strong "
        "[source](evidence://ev_demand_batch)."
    )

    assert verifier.batch_calls == 1
    assert len(verifier.calls) == 2
    assert result.bundle is not None
    assert result.bundle["quality"]["metrics"]["unverifiedClaimCount"] == 0

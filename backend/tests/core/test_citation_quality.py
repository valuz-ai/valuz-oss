from __future__ import annotations

from src.core.citation_quality import evaluate_citation_quality
from src.core.claim_audit import MAX_CLAIMS_PER_ANSWER
from src.core.claim_evidence_resolution import (
    SemanticVerificationRequest,
    SemanticVerificationResult,
)


def _policy() -> dict:
    return {
        "policy_id": "test-quality",
        "revision": "test-v1",
        "mode": "strict-domain",
        "layers": [
            {
                "layer": "oss",
                "policy_id": "oss-citation-baseline",
                "revision": "citation-baseline-v2",
                "status": "active",
            },
            {
                "layer": "distribution",
                "policy_id": "test-quality",
                "revision": "test-v1",
                "status": "active",
            },
        ],
        "config": {
            "source_tiers": [
                {
                    "id": "P1",
                    "authority": "primary",
                    "match": {
                        "source_types": ["dataset"],
                        "tools": ["stock.*"],
                    },
                },
                {
                    "id": "P4",
                    "authority": "secondary",
                    "match": {
                        "source_types": ["web"],
                        "tools": ["search.news*"],
                    },
                },
                {
                    "id": "P2",
                    "authority": "issuer",
                    "match": {
                        "any": [
                            {"source_categories": ["filings"]},
                            {"tools": ["search.filings_search"]},
                        ]
                    },
                },
            ],
            "rules": {
                "factual_claim": {"citation_required": True},
                "numeric_claim": {
                    "require_unit": True,
                    "require_period_or_as_of": True,
                    "require_value_in_answer": True,
                },
                "derived_value": {
                    "require_calculation_evidence": True,
                    "require_unit": True,
                    "require_compatible_units": True,
                },
                "low_tier_critical_claim": {
                    "require_cross_check": True,
                    "low_tiers": ["P4"],
                    "cross_check_tiers": ["P1"],
                },
                "time_boundary": {
                    "forbid_extrapolation": True,
                    "require_coverage": True,
                },
            },
            "failure": {"publish_on_degraded": "draft_only"},
        },
    }


def _integrity() -> dict:
    return {
        "status": "passed",
        "unknownCitationIds": [],
        "unusedCitationIds": [],
        "missingLocatorCitationIds": [],
        "repairAttempts": 0,
        "policyRevision": "citation-v1",
    }


def _structured(citation_id: str = "cit_revenue") -> dict:
    return {
        "citationId": citation_id,
        "source": {
            "sourceId": "dataset-1",
            "providerId": "market-data",
            "sourceType": "dataset",
            "title": "Income statement",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "structured-data",
            "datasetId": "financials",
            "toolName": "stock.income_statement",
            "field": "revenue",
            "value": 120,
            "unit": "USDm",
            "period": "FY2025",
            "asOf": "2025-12-31",
            "capturedAt": "2026-07-30T10:00:00Z",
            "coverage": {
                "start": "2025-01-01",
                "end": "2025-12-31",
            },
        },
    }


def test_policy_passes_structured_data_and_adds_stable_annotations() -> None:
    bundle = {
        "version": 1,
        "citations": [_structured()],
        "integrity": _integrity(),
    }

    result = evaluate_citation_quality(
        "Revenue was 120 USDm [source](citation://cit_revenue).",
        bundle,
        _policy(),
    )

    assert result["quality"]["status"] == "passed"
    assert result["quality"]["publishStatus"] == "ready"
    assert [layer["layer"] for layer in result["quality"]["policyLayers"]] == [
        "oss",
        "distribution",
    ]
    assert result["quality"]["metrics"]["tierCounts"] == {"P1": 1}
    assert result["citations"][0]["annotations"]["quality"] == {
        "policyId": "test-quality",
        "policyRevision": "test-v1",
        "tier": "P1",
        "authority": "primary",
        "status": "passed",
        "label": "P1",
    }
    assert "annotations" not in bundle["citations"][0]


def test_retrieval_progress_message_is_a_quality_noop() -> None:
    result = evaluate_citation_quality(
        (
            "搜索结果只覆盖到 Q4 FY2026（MSFT）和 Q2 FY2026（Alphabet），"
            "都属于同一个季度期间。需要找到各自上一季度的电话会。"
        ),
        {
            "version": 1,
            "citations": [],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert result["quality"]["issues"] == []
    assert result["quality"]["metrics"]["claimCitationRequiredCount"] == 0
    assert result["quality"]["metrics"]["unverifiedClaimCount"] == 0


def test_search_result_fact_remains_auditable() -> None:
    result = evaluate_citation_quality(
        "搜索显示，公司收入增长 20%。",
        {
            "version": 1,
            "citations": [],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert result["quality"]["metrics"]["claimCitationRequiredCount"] == 1
    assert result["quality"]["metrics"]["claimUnsupportedCount"] == 1
    assert {issue["code"] for issue in result["quality"]["issues"]} == {
        "numeric_claim_without_citation"
    }


def test_same_field_address_for_different_entities_is_not_a_source_conflict() -> None:
    citations = []
    for citation_id, entity_id, value in (
        ("cit_sndk", "SNDK", 5_950_000_000),
        ("cit_mu", "MU", 41_456_000_000),
    ):
        citation = _structured(citation_id)
        citation["source"]["sourceId"] = f"financials:{entity_id}"
        citation["evidence"].update(
            {
                "recordKey": "/data/0/revenue",
                "field": "/data/0/revenue",
                "metric": "revenue",
                "entityId": entity_id,
                "value": value,
                "unit": "USD",
                "period": "2026 quarterly",
                "asOf": "2026-05-28",
            }
        )
        citations.append(citation)

    result = evaluate_citation_quality(
        "SNDK revenue was 5950000000 USD [source](citation://cit_sndk). "
        "MU revenue was 41456000000 USD [source](citation://cit_mu).",
        {
            "version": 1,
            "citations": citations,
            "integrity": _integrity(),
        },
        _policy(),
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert "structured_source_conflict" not in codes


def test_same_generic_factor_field_for_different_metrics_is_not_a_conflict() -> None:
    citations = []
    answer_parts = []
    for citation_id, metric, label, value in (
        ("cit_ma20", "moving_average_20", "MA20", 203.69),
        ("cit_ma60", "moving_average_60", "MA60", 228.58),
        ("cit_ma120", "moving_average_120", "MA120", 168.89),
        ("cit_ma250", "moving_average_250", "MA250", 123.64),
    ):
        citation = _structured(citation_id)
        citation["source"]["sourceId"] = f"factor-result:{metric}"
        citation["evidence"].update(
            {
                "datasetId": "factors_compute",
                "recordKey": "/datas/0/factor_value",
                "field": "/datas/0/factor_value",
                "metric": metric,
                "entityId": "MRVL",
                "value": value,
                "unit": "USD",
                "period": "2026-08-03",
                "asOf": "2026-08-03",
            }
        )
        citations.append(citation)
        answer_parts.append(f"{label} was {value} USD [{label}](citation://{citation_id}).")

    result = evaluate_citation_quality(
        " ".join(answer_parts),
        {
            "version": 1,
            "citations": citations,
            "integrity": _integrity(),
        },
        _policy(),
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert "structured_source_conflict" not in codes
    assert "cross_source_value_conflict" not in codes


def test_structured_value_accepts_rounding_when_evidence_unit_is_missing() -> None:
    citation = _structured("cit_price")
    citation["evidence"].update(
        {
            "field": "stock_price",
            "metric": "stock_price",
            "value": 193.775,
            "unit": "",
            "period": "2026-08-03",
            "asOf": "2026-08-03",
        }
    )

    result = evaluate_citation_quality(
        "The stock price was 193.78 [source](citation://cit_price).",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert "structured_value_not_present_in_answer" not in codes
    assert "claim_evidence_mismatch" not in codes
    assert "numeric_unit_missing" in codes


def test_policy_degrades_missing_unit_period_and_out_of_range_date() -> None:
    citation = _structured()
    citation["evidence"].pop("unit")
    citation["evidence"].pop("period")
    citation["evidence"]["asOf"] = "2026-01-02"
    citation["annotations"] = {
        "provenance": {"coverage": {"start": "2025-01-01", "end": "2025-12-31"}}
    }

    result = evaluate_citation_quality(
        "Revenue was 121 [source](citation://cit_revenue).",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert {
        "numeric_unit_missing",
        "structured_value_not_present_in_answer",
        "evidence_after_coverage",
    } <= codes
    assert result["quality"]["status"] == "degraded"
    assert result["quality"]["publishStatus"] == "draft-only"


def test_policy_detects_uncited_financial_number_without_requiring_snapshot_coverage() -> None:
    citation = _structured()
    citation["evidence"].pop("coverage")
    result = evaluate_citation_quality(
        ("Revenue was 120 USDm [source](citation://cit_revenue). Margin was 23.5%."),
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert "numeric_claim_without_citation" in codes
    assert "evidence_coverage_missing" not in codes
    numeric_issue = next(
        issue
        for issue in result["quality"]["issues"]
        if issue["code"] == "numeric_claim_without_citation"
    )
    assert numeric_issue["claim"] == {"exact": "Margin was 23.5%."}
    assert result["quality"]["metrics"]["unsourcedClaimCount"] == 1
    claims = {claim["exact"]: claim for claim in result["quality"]["claims"]}
    assert claims["Revenue was 120 USDm."]["status"] == "passed"
    assert "evidence_coverage_missing" not in claims["Revenue was 120 USDm."]["issueCodes"]
    assert claims["Margin was 23.5%."]["status"] == "unsupported"
    assert {
        key: claims["Margin was 23.5%."]["location"][key]
        for key in ("kind", "blockIndex", "start", "end")
    } == {
        "kind": "text",
        "blockIndex": 0,
        "start": 22,
        "end": 39,
    }


def test_undeclared_coverage_window_is_unknown_not_an_issue() -> None:
    """No producer emits a coverage window, so absence cannot be a finding.

    Sampling real runs found dated evidence everywhere and declared coverage
    nowhere. Asserting a problem from that absence attached a "verify against
    the original" note to essentially every dated citation, which is the
    unknown-as-conflict collapse the design rules out.
    """

    citation = _structured()
    citation["evidence"].pop("coverage")

    result = evaluate_citation_quality(
        "Revenue trend from 2024-01-01 to 2025-12-31 was stable [source](citation://cit_revenue).",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert "evidence_coverage_missing" not in codes


def test_point_in_time_indicator_interpretation_does_not_require_range_coverage() -> None:
    citation = _structured()
    citation["evidence"].update(
        {
            "metric": "moving_average_20",
            "field": "moving_average_20",
            "value": 203.69,
            "unit": "USD",
            "asOf": "2026-08-03",
        }
    )
    citation["evidence"].pop("coverage", None)

    result = evaluate_citation_quality(
        "Price is below MA20 (203.69 USD), so the short-term trend is weak "
        "[source](citation://cit_revenue).",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "evidence_coverage_missing" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_table_citation_coverage_ignores_uncited_reasoning_cells() -> None:
    price = _structured("cit_price")
    price["evidence"].update(
        {
            "metric": "stock_price",
            "field": "stock_price",
            "value": 193.775,
            "unit": "USD",
            "asOf": "2026-08-03",
        }
    )
    price["evidence"].pop("coverage", None)
    ma20 = _structured("cit_ma20")
    ma20["evidence"].update(
        {
            "metric": "moving_average_20",
            "field": "factor_value",
            "value": 203.69,
            "unit": "USD",
            "asOf": "2026-08-03",
        }
    )
    ma20["evidence"].pop("coverage", None)
    answer = "\n".join(
        (
            "| Rule | Current value | Conclusion |",
            "|---|---:|---|",
            "| Price vs MA20 | $193.775 [p](citation://cit_price) < "
            "$203.69 [m](citation://cit_ma20) | Price is below MA20; short-term trend is weak |",
        )
    )

    result = evaluate_citation_quality(
        answer,
        {
            "version": 1,
            "citations": [price, ma20],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "evidence_coverage_missing" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_false_numeric_comparison_is_a_confirmed_claim_conflict() -> None:
    ma20 = _structured("cit_ma20")
    ma20["evidence"].update(
        {
            "metric": "moving_average_20",
            "field": "moving_average_20",
            "value": 203.69,
            "unit": "USD",
        }
    )
    ma60 = _structured("cit_ma60")
    ma60["evidence"].update(
        {
            "metric": "moving_average_60",
            "field": "moving_average_60",
            "value": 228.58,
            "unit": "USD",
        }
    )

    result = evaluate_citation_quality(
        "MA20 203.69 USD > MA60 228.58 USD [a](citation://cit_ma20) [b](citation://cit_ma60).",
        {
            "version": 1,
            "citations": [ma20, ma60],
            "integrity": _integrity(),
        },
        _policy(),
    )

    comparison_issue = next(
        issue
        for issue in result["quality"]["issues"]
        if issue["code"] == "numeric_comparison_false"
    )
    assert comparison_issue["severity"] == "degraded"
    assert comparison_issue["claim"]["exact"].startswith("MA20 203.69 USD >")


def test_true_numeric_comparison_does_not_create_a_conflict() -> None:
    price = _structured("cit_price")
    price["evidence"].update(
        {
            "metric": "stock_price",
            "field": "stock_price",
            "value": 193.78,
            "unit": "USD",
        }
    )

    result = evaluate_citation_quality(
        "Price 193.78 USD > stop 160 USD [source](citation://cit_price).",
        {
            "version": 1,
            "citations": [price],
            "integrity": _integrity(),
        },
        _policy(),
        user_prompt="The stop is 160 USD.",
    )

    assert "numeric_comparison_false" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_explicit_binding_is_verified_even_when_claim_came_from_user_prompt() -> None:
    citation = _structured("cit_user_claim")
    citation["evidence"].update(
        {
            "entityName": "Example Co",
            "metric": "revenue",
            "field": "revenue",
            "value": 120,
            "unit": "USDm",
            "period": "FY2025",
        }
    )
    answer = "Example Co FY2025 revenue was 999 USDm [source](citation://cit_user_claim)."

    result = evaluate_citation_quality(
        answer,
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
        user_prompt=answer,
    )

    claim = result["quality"]["claims"][0]
    assert claim["citationRequired"] is False
    assert claim["status"] == "unverified"
    assert "claim_evidence_conflict" in claim["issueCodes"]
    assert claim["bindings"] == [
        {
            "citationId": "cit_user_claim",
            "role": "conflicting",
            "supportStatus": "contradicted",
        }
    ]


def test_complete_component_evidence_does_not_degrade_one_compound_claim() -> None:
    policy = _policy()
    policy["config"]["semantics"] = {
        "metric_ontology": {
            "metrics": {
                "operating_cash_flow": {
                    "aliases": ["经营性现金流", "经营现金流"],
                    "fields": ["operating_cash_flow"],
                },
                "free_cash_flow": {
                    "aliases": ["自由现金流", "FCF"],
                    "fields": ["free_cash_flow"],
                },
                "operating_cash_flow_growth": {
                    "aliases": ["经营性现金流环比", "经营现金流环比增速"],
                    "fields": ["operating_cash_flow_growth_rate"],
                },
                "free_cash_flow_growth": {
                    "aliases": ["自由现金流环比", "自由现金流环比增速"],
                    "fields": ["free_cash_flow_growth_rate"],
                },
            }
        },
        "unit_ontology": {
            "units": {
                "usd": {"canonical": "USD", "aliases": ["USD"], "scale": 1},
                "usd_hundred_million": {
                    "canonical": "USD",
                    "aliases": ["亿美元"],
                    "scale": 100_000_000,
                },
                "percentage": {
                    "canonical": "percent",
                    "aliases": ["%"],
                    "scale": 1,
                },
            }
        },
        "calculation_dependencies": {
            "operating_cash_flow_growth": ["operating_cash_flow"],
            "free_cash_flow_growth": ["free_cash_flow"],
        },
    }

    def structured(citation_id: str, metric: str, value: int, period: str) -> dict:
        item = _structured(citation_id)
        item["evidence"].update(
            {
                "field": metric,
                "metric": metric,
                "value": value,
                "unit": "USD",
                "period": period,
            }
        )
        return item

    current_ocf = structured("cit_ocf", "operating_cash_flow", 50_344_000_000, "2027 Q1")
    prior_ocf = structured("cit_ocf_prior", "operating_cash_flow", 36_188_000_000, "2026 Q4")
    current_fcf = structured("cit_fcf", "free_cash_flow", 48_587_000_000, "2027 Q1")
    prior_fcf = structured("cit_fcf_prior", "free_cash_flow", 34_904_000_000, "2026 Q4")

    def calculation(
        citation_id: str,
        metric: str,
        result: str,
        current: dict,
        prior: dict,
    ) -> dict:
        return {
            "citationId": citation_id,
            "source": {
                "sourceId": citation_id,
                "providerId": "calculation",
                "sourceType": "dataset",
                "title": "Calculation",
                "retrievedAt": "2026-08-10T00:00:00Z",
            },
            "evidence": {
                "kind": "calculation",
                "toolName": "stock.calculation",
                "expression": "(q1 / q4 - 1) * 100",
                "result": result,
                "unit": "%",
                "rounding": "1dp",
                "metric": metric,
                "period": "FY2027 Q1 vs FY2026 Q4",
                "calculatedAt": "2026-08-10T00:00:00Z",
                "inputs": [
                    {
                        "name": "q1",
                        "citationId": current["citationId"],
                        "value": current["evidence"]["value"],
                        "unit": "USD",
                    },
                    {
                        "name": "q4",
                        "citationId": prior["citationId"],
                        "value": prior["evidence"]["value"],
                        "unit": "USD",
                    },
                ],
            },
        }

    ocf_growth = calculation(
        "cit_ocf_growth",
        "operating_cash_flow_growth",
        "39.1",
        current_ocf,
        prior_ocf,
    )
    fcf_growth = calculation(
        "cit_fcf_growth",
        "free_cash_flow_growth",
        "39.2",
        current_fcf,
        prior_fcf,
    )
    result = evaluate_citation_quality(
        (
            "经营性现金流 503 亿美元（+39.1%）和自由现金流 486 亿美元（+39.2%）"
            "[1](citation://cit_ocf)[2](citation://cit_ocf_growth)"
            "[3](citation://cit_fcf)[4](citation://cit_fcf_growth)。"
        ),
        {
            "version": 1,
            "citations": [
                current_ocf,
                prior_ocf,
                current_fcf,
                prior_fcf,
                ocf_growth,
                fcf_growth,
            ],
            "integrity": _integrity(),
        },
        policy,
    )

    claim = result["quality"]["claims"][0]
    assert claim["status"] == "passed", result["quality"]
    assert "claim_evidence_conflict" not in claim["issueCodes"]
    assert all(
        citation["annotations"]["quality"]["status"] == "passed"
        for citation in result["citations"]
        if citation["citationId"]
        in {
            "cit_ocf",
            "cit_ocf_growth",
            "cit_fcf",
            "cit_fcf_growth",
        }
    )

    wrong = structured("cit_wrong", "free_cash_flow", 9_900_000_000, "2027 Q1")
    contradicted = evaluate_citation_quality(
        (
            "经营性现金流 503 亿美元（+39.1%）和自由现金流 486 亿美元（+39.2%）"
            "[1](citation://cit_ocf)[2](citation://cit_ocf_growth)"
            "[3](citation://cit_fcf)[4](citation://cit_fcf_growth)"
            "[5](citation://cit_wrong)。"
        ),
        {
            "version": 1,
            "citations": [
                current_ocf,
                prior_ocf,
                current_fcf,
                prior_fcf,
                ocf_growth,
                fcf_growth,
                wrong,
            ],
            "integrity": _integrity(),
        },
        policy,
    )

    contradicted_claim = contradicted["quality"]["claims"][0]
    assert contradicted_claim["status"] == "degraded"
    assert "structured_source_conflict" in contradicted_claim["issueCodes"]
    conflict = next(
        issue
        for issue in contradicted["quality"]["issues"]
        if issue["code"] == "structured_source_conflict"
    )
    assert set(conflict["citationIds"]) == {"cit_fcf", "cit_wrong"}


def test_unselected_compound_claim_does_not_treat_one_valid_component_as_conflict() -> None:
    policy = _policy()
    policy["config"]["rules"]["claim_audit"] = {
        "selection_enabled": True,
        "max_selected_claims": 0,
        "max_selected_claims_per_group": 0,
    }
    policy["config"]["source_tiers"][0]["match"] = {"source_types": ["dataset"]}
    policy["config"]["semantics"] = {
        "metric_ontology": {
            "metrics": {
                "long_term_debt": {
                    "aliases": ["长期债务"],
                    "fields": ["long_term_debt"],
                },
                "total_liabilities": {
                    "aliases": ["总负债"],
                    "fields": ["total_liabilities"],
                },
                "total_assets": {
                    "aliases": ["总资产"],
                    "fields": ["total_assets"],
                },
                "debt_to_assets": {
                    "aliases": ["资产负债率"],
                    "fields": ["debt_to_assets"],
                },
            }
        },
        "unit_ontology": {
            "units": {
                "usd_hundred_million": {
                    "canonical": "USD",
                    "aliases": ["亿美元"],
                    "scale": 100_000_000,
                },
                "percentage": {"canonical": "percent", "aliases": ["%"], "scale": 1},
            }
        },
        "calculation_dependencies": {
            "debt_to_assets": ["total_liabilities", "total_assets"],
        },
    }
    liabilities = _structured("cit_liabilities")
    liabilities["evidence"].update(
        {"metric": "total_liabilities", "field": "total_liabilities", "value": 64_000}
    )
    assets = _structured("cit_assets")
    assets["evidence"].update(
        {"metric": "total_assets", "field": "total_assets", "value": 259_474}
    )
    ratio = {
        "citationId": "cit_ratio",
        "source": {
            "sourceId": "calculation-ratio",
            "providerId": "calculation",
            "sourceType": "dataset",
            "title": "Calculation",
            "retrievedAt": "2026-08-11T00:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "toolName": "runtime.calculation",
            "expression": "(liabilities / assets) * 100",
            "result": 24.7,
            "unit": "%",
            "rounding": "1dp",
            "metric": "FY2027Q1 资产负债率",
            "inputs": [
                {"name": "liabilities", "citationId": "cit_liabilities", "value": 64_000},
                {"name": "assets", "citationId": "cit_assets", "value": 259_474},
            ],
        },
        "annotations": {"provenance": {"toolName": "stock.calculation"}},
    }

    result = evaluate_citation_quality(
        "长期债务 74.7 亿美元几乎未变，资产负债率仅 24.7% [计算](citation://cit_ratio)。",
        {
            "version": 1,
            "citations": [liabilities, assets, ratio],
            "integrity": _integrity(),
        },
        policy,
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert "claim_evidence_conflict" not in codes
    assert next(
        citation for citation in result["citations"] if citation["citationId"] == "cit_ratio"
    )["annotations"]["quality"]["status"] == "passed", result["quality"]["issues"]


def test_compound_base_value_and_growth_calculation_are_local_components() -> None:
    policy = _policy()
    policy["config"]["source_tiers"][0]["match"] = {"source_types": ["dataset"]}
    policy["config"]["semantics"] = {
        "metric_ontology": {
            "metrics": {
                "net_profit": {
                    "aliases": ["净利润"],
                    "fields": ["net_profit"],
                },
                "net_profit_growth": {
                    "aliases": ["净利润环比增长率"],
                    "fields": ["net_profit_growth"],
                },
            }
        },
        "unit_ontology": {
            "units": {
                "usd": {"canonical": "USD", "aliases": ["USD"], "scale": 1},
                "usd_hundred_million": {
                    "canonical": "USD",
                    "aliases": ["亿美元"],
                    "scale": 100_000_000,
                },
                "percentage": {"canonical": "percent", "aliases": ["%"], "scale": 1},
            }
        },
        "calculation_dependencies": {"net_profit_growth": ["net_profit"]},
    }
    current = _structured("cit_net_profit")
    current["evidence"].update(
        {
            "field": "net_profit",
            "metric": "net_profit",
            "value": 58_321_000_000,
            "unit": "USD",
            "period": "2027 Q1",
        }
    )
    prior = _structured("cit_net_profit_prior")
    prior["evidence"].update(
        {
            "field": "net_profit",
            "metric": "net_profit",
            "value": 42_960_000_000,
            "unit": "USD",
            "period": "2026 Q4",
        }
    )
    growth = {
        "citationId": "cit_net_profit_growth",
        "source": {
            "sourceId": "calculation-net-profit-growth",
            "providerId": "calculation",
            "sourceType": "dataset",
            "title": "Calculation",
            "retrievedAt": "2026-08-11T00:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "toolName": "stock.calculation",
            "expression": "(current / prior - 1) * 100",
            "result": 35.8,
            "unit": "%",
            "rounding": "1dp",
            "metric": "净利润环比增长率",
            "inputs": [
                {
                    "name": "current",
                    "citationId": "cit_net_profit",
                    "value": 58_321_000_000,
                    "unit": "USD",
                },
                {
                    "name": "prior",
                    "citationId": "cit_net_profit_prior",
                    "value": 42_960_000_000,
                    "unit": "USD",
                },
            ],
        },
    }

    result = evaluate_citation_quality(
        (
            "推动净利润环比 +35.8% 至 583 亿美元"
            "[增长](citation://cit_net_profit_growth)"
            "[净利润](citation://cit_net_profit)。"
        ),
        {
            "version": 1,
            "citations": [current, prior, growth],
            "integrity": _integrity(),
        },
        policy,
    )

    assert "claim_evidence_conflict" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }
    assert all(
        citation["annotations"]["quality"]["status"] == "passed"
        for citation in result["citations"]
        if citation["citationId"] in {"cit_net_profit", "cit_net_profit_growth"}
    ), result["quality"]["issues"]


def test_policy_audits_non_numeric_external_facts_and_dates() -> None:
    result = evaluate_citation_quality(
        "The company was founded in 1999. Alice is the CEO.",
        {
            "version": 1,
            "citations": [],
            "integrity": _integrity(),
        },
        _policy(),
    )

    claims = result["quality"]["claims"]
    assert [claim["exact"] for claim in claims] == [
        "The company was founded in 1999.",
        "Alice is the CEO.",
    ]
    assert all(claim["citationRequired"] for claim in claims)
    assert all(claim["status"] == "unsupported" for claim in claims)
    assert result["quality"]["metrics"]["unsourcedClaimCount"] == 2


def test_risk_bounded_audit_only_gates_selected_critical_claims() -> None:
    policy = _policy()
    policy["config"]["rules"]["claim_audit"] = {
        "selection_enabled": True,
        "max_selected_claims": 1,
        "max_selected_claims_per_group": 1,
        "minimum_supported_ratio": 0.6,
        "critical_kinds": ["financial-fact", "numeric-fact", "date-fact"],
        "prioritize_explicit_user_request": True,
        "prioritize_existing_bindings": True,
    }

    result = evaluate_citation_quality(
        (
            "Revenue was 120 USDm [source](citation://cit_revenue). "
            "The company was founded in 1999. Alice is the CEO."
        ),
        {
            "version": 1,
            "citations": [_structured()],
            "integrity": _integrity(),
        },
        policy,
        user_prompt="What was revenue?",
    )

    claims = result["quality"]["claims"]
    selected = [claim for claim in claims if claim["auditSelected"]]
    not_selected = [claim for claim in claims if not claim["auditSelected"]]
    assert len(selected) == 1
    assert selected[0]["exact"].startswith("Revenue was 120 USDm")
    assert selected[0]["auditPriority"] == "critical"
    assert selected[0]["status"] == "passed"
    assert all(claim["status"] == "not-selected" for claim in not_selected)
    assert result["quality"]["auditOutcome"] == "passed"
    assert result["quality"]["metrics"]["criticalClaimSelectedCount"] == 1
    assert result["quality"]["metrics"]["optionalClaimObservedCount"] == len(not_selected)
    assert not {
        "claim_without_citation",
        "date_claim_without_citation",
        "numeric_claim_without_citation",
    }.intersection(issue["code"] for issue in result["quality"]["issues"])


def test_unselected_structured_claim_still_runs_basic_conflict_check() -> None:
    policy = _policy()
    policy["config"]["rules"]["claim_audit"] = {
        "selection_enabled": True,
        "max_selected_claims": 1,
        "max_selected_claims_per_group": 1,
        "minimum_supported_ratio": 0.6,
        "critical_kinds": ["financial-fact", "numeric-fact"],
        "prioritize_explicit_user_request": True,
        "prioritize_existing_bindings": True,
    }
    revenue = _structured("cit_revenue")
    profit = _structured("cit_profit")
    profit["evidence"].update({"field": "profit", "metric": "profit", "value": 20})

    result = evaluate_citation_quality(
        (
            "Revenue was 120 USDm [source](citation://cit_revenue).\n"
            "Profit was 999 USDm [source](citation://cit_profit)."
        ),
        {
            "version": 1,
            "citations": [revenue, profit],
            "integrity": _integrity(),
        },
        policy,
        user_prompt="What was revenue?",
    )

    profit_claim = next(
        claim for claim in result["quality"]["claims"] if claim["exact"].startswith("Profit")
    )
    assert profit_claim["auditSelected"] is False
    assert profit_claim["status"] == "unverified"
    assert profit_claim["issueCodes"] == ["claim_evidence_conflict"]
    assert profit_claim["bindings"] == [
        {
            "citationId": "cit_profit",
            "role": "conflicting",
            "supportStatus": "contradicted",
        }
    ]
    assert result["quality"]["metrics"]["criticalClaimSelectedCount"] == 1
    assert result["quality"]["metrics"]["criticalConfirmedConflictCount"] == 0
    conflict = next(
        issue for issue in result["quality"]["issues"] if issue["code"] == "claim_evidence_conflict"
    )
    assert conflict["severity"] == "degraded"


def test_unselected_unbound_comparison_does_not_run_basic_conflict_check() -> None:
    policy = _policy()
    policy["config"]["rules"]["claim_audit"] = {
        "selection_enabled": True,
        "max_selected_claims": 1,
        "max_selected_claims_per_group": 1,
        "minimum_supported_ratio": 0.6,
        "critical_kinds": ["financial-fact", "numeric-fact"],
        "prioritize_existing_bindings": True,
    }

    result = evaluate_citation_quality(
        "Revenue was 120 USDm [source](citation://cit_revenue).\n"
        "A separate estimate says 2 is greater than 3.",
        {
            "version": 1,
            "citations": [_structured("cit_revenue")],
            "integrity": _integrity(),
        },
        policy,
    )

    unselected = next(
        claim for claim in result["quality"]["claims"] if "greater than" in claim["exact"]
    )
    assert unselected["auditSelected"] is False
    assert unselected["status"] == "not-selected"
    assert "numeric_comparison_false" not in unselected["issueCodes"]


def test_unresolved_critical_claim_is_partial_without_gating_optional_claims() -> None:
    policy = _policy()
    policy["config"]["rules"]["claim_audit"] = {
        "selection_enabled": True,
        "max_selected_claims": 1,
        "max_selected_claims_per_group": 1,
        "minimum_supported_ratio": 0.6,
        "critical_kinds": ["financial-fact", "numeric-fact", "date-fact"],
        "prioritize_explicit_user_request": True,
    }

    result = evaluate_citation_quality(
        "Revenue was 120 USDm. The company was founded in 1999.",
        {
            "version": 1,
            "citations": [],
            "integrity": _integrity(),
        },
        policy,
        user_prompt="What was revenue?",
    )

    assert result["quality"]["auditOutcome"] == "partial"
    assert result["quality"]["metrics"]["criticalClaimSelectedCount"] == 1
    assert result["quality"]["metrics"]["criticalClaimUnresolvedCount"] == 1
    assert result["quality"]["metrics"]["optionalClaimObservedCount"] == 1
    assert (
        len(
            [
                issue
                for issue in result["quality"]["issues"]
                if issue["code"]
                in {"numeric_claim_without_citation", "date_claim_without_citation"}
            ]
        )
        == 1
    )


def test_confirmed_conflict_only_escalates_when_claim_is_selected() -> None:
    policy = _policy()
    policy["config"]["rules"]["claim_audit"] = {
        "selection_enabled": True,
        "max_selected_claims": 1,
        "max_selected_claims_per_group": 1,
        "minimum_supported_ratio": 0.6,
        "critical_kinds": ["financial-fact"],
        "prioritize_explicit_user_request": True,
        "prioritize_existing_bindings": True,
    }

    result = evaluate_citation_quality(
        "Revenue was 999 USDm [source](citation://cit_revenue).",
        {
            "version": 1,
            "citations": [_structured()],
            "integrity": _integrity(),
        },
        policy,
        user_prompt="What was revenue?",
    )

    assert result["quality"]["auditOutcome"] == "needs-review"
    assert result["quality"]["metrics"]["criticalConfirmedConflictCount"] == 1
    conflict = next(
        issue for issue in result["quality"]["issues"] if issue["code"] == "claim_evidence_conflict"
    )
    assert conflict["severity"] == "degraded"


def test_semantic_verifier_receives_only_selected_critical_claims() -> None:
    class RecordingVerifier:
        def __init__(self) -> None:
            self.requests: tuple[SemanticVerificationRequest, ...] = ()

        def verify_batch(
            self,
            requests: tuple[SemanticVerificationRequest, ...],
        ) -> dict[str, SemanticVerificationResult]:
            self.requests = requests
            return {
                request.claim.claim_id: SemanticVerificationResult(
                    verdict="entailed",
                    evidence_handles=tuple(candidate.handle for candidate in request.candidates),
                    confidence=0.99,
                    verifier_revision="test-selected-only",
                )
                for request in requests
            }

    def citation(citation_id: str, quote: str) -> dict:
        return {
            "citationId": citation_id,
            "source": {
                "sourceId": citation_id,
                "providerId": "documents",
                "sourceType": "document",
                "title": "Document",
                "retrievedAt": "2026-08-05T00:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": quote,
                "snippet": quote,
                "capturedAt": "2026-08-05T00:00:00Z",
            },
        }

    policy = _policy()
    policy["config"]["rules"]["claim_audit"] = {
        "selection_enabled": True,
        "max_selected_claims": 1,
        "max_selected_claims_per_group": 1,
        "minimum_supported_ratio": 0.6,
        "critical_kinds": ["document-claim"],
        "prioritize_explicit_user_request": True,
        "prioritize_existing_bindings": True,
    }
    verifier = RecordingVerifier()

    evaluate_citation_quality(
        (
            "Customer demand exceeds supply [1](citation://cit_demand). "
            "The office opened in Paris [2](citation://cit_office)."
        ),
        {
            "version": 1,
            "citations": [
                citation("cit_demand", "Orders remain above available capacity."),
                citation("cit_office", "A Paris workplace began operations."),
            ],
            "integrity": _integrity(),
        },
        policy,
        user_prompt="What is the demand situation?",
        semantic_verifier=verifier,
    )

    assert len(verifier.requests) == 1
    assert "Customer demand" in verifier.requests[0].claim.exact


def test_semantic_verifier_keeps_one_batch_when_budget_doubles_to_48() -> None:
    class RecordingVerifier:
        def __init__(self) -> None:
            self.calls: list[tuple[SemanticVerificationRequest, ...]] = []

        def verify_batch(
            self,
            requests: tuple[SemanticVerificationRequest, ...],
        ) -> dict[str, SemanticVerificationResult]:
            self.calls.append(requests)
            return {
                request.claim.claim_id: SemanticVerificationResult(
                    verdict="entailed",
                    evidence_handles=tuple(candidate.handle for candidate in request.candidates),
                    confidence=0.99,
                    verifier_revision="test-budget-48",
                )
                for request in requests
            }

    citations: list[dict] = []
    answer_parts: list[str] = []
    for index in range(50):
        citation_id = f"cit_fact_{index}"
        citations.append(
            {
                "citationId": citation_id,
                "source": {
                    "sourceId": citation_id,
                    "providerId": "documents",
                    "sourceType": "document",
                    "title": f"Document {index}",
                    "retrievedAt": "2026-08-10T00:00:00Z",
                },
                "evidence": {
                    "kind": "text",
                    "quote": f"Underlying source statement {index}.",
                    "capturedAt": "2026-08-10T00:00:00Z",
                },
            }
        )
        answer_parts.append(
            f"Company fact {index} remains material [source](citation://{citation_id})."
        )
    policy = _policy()
    policy["config"]["rules"]["claim_audit"] = {
        "selection_enabled": True,
        "max_selected_claims": 48,
        "max_selected_claims_per_group": 12,
        "critical_kinds": ["document-claim"],
        "prioritize_existing_bindings": True,
    }
    verifier = RecordingVerifier()

    result = evaluate_citation_quality(
        "\n\n".join(answer_parts),
        {
            "version": 1,
            "citations": citations,
            "integrity": _integrity(),
        },
        policy,
        semantic_verifier=verifier,
    )

    assert len(verifier.calls) == 1
    assert len(verifier.calls[0]) == 48
    assert result["quality"]["metrics"]["criticalClaimSelectedCount"] == 48


def test_unique_safe_partial_binding_does_not_create_claim_support_warning() -> None:
    citation = _structured("cit_market_cap")
    citation["evidence"].update(
        {
            "field": "market_cap",
            "metric": "market_cap",
            "entityId": "NVDA",
            "value": 5_424_535_160_000,
            "unit": "",
            "asOf": "2026-08-10",
        }
    )
    citation["evidence"].pop("period", None)
    citation["evidence"].pop("coverage", None)
    policy = _policy()
    policy["config"]["rules"]["numeric_claim"]["require_unit"] = False
    policy["config"]["semantics"] = {
        "metric_ontology": {
            "metrics": {
                "market_cap": {
                    "aliases": ["市值", "market cap"],
                    "fields": ["market_cap"],
                    "period_role": "as-of",
                }
            }
        },
        "unit_ontology": {
            "units": {"usd": {"canonical": "USD", "aliases": ["USD", "$"], "scale": 1}}
        },
    }

    result = evaluate_citation_quality(
        "NVDA 市值约为 ~$5.42万亿 [source](citation://cit_market_cap).",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        policy,
    )

    claim = result["quality"]["claims"][0]
    assert claim["status"] == "passed"
    assert "claim_partially_supported" not in claim["issueCodes"]


def test_extra_non_supporting_citation_does_not_degrade_supported_claim() -> None:
    def text_citation(citation_id: str, quote: str) -> dict:
        return {
            "citationId": citation_id,
            "source": {
                "sourceId": citation_id,
                "providerId": "documents",
                "sourceType": "document",
                "sourceCategory": "filings",
                "documentId": "doc-1",
                "title": "Annual report",
                "retrievedAt": "2026-07-30T10:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": quote,
                "snippet": "",
                "capturedAt": "2026-07-30T10:00:00Z",
            },
        }

    result = evaluate_citation_quality(
        "- Revenue grew [source](citation://cit_revenue)\n"
        "- Profit rose [source](citation://cit_revenue)"
        "[source](citation://cit_profit)",
        {
            "version": 1,
            "citations": [
                text_citation("cit_revenue", "Revenue grew from the filing."),
                text_citation("cit_profit", "Profit rose from the filing."),
            ],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert result["quality"]["status"] == "passed"
    assert result["quality"]["metrics"]["claimBoundCount"] == 2
    assert all(claim["status"] == "passed" for claim in result["quality"]["claims"])


def test_policy_rejects_real_structured_citation_with_wrong_field_semantics() -> None:
    citation = _structured()
    citation["evidence"]["field"] = "fiscal_year"
    citation["evidence"]["value"] = 2025
    citation["evidence"]["unit"] = "year"

    result = evaluate_citation_quality(
        "Revenue was 2025 USDm [source](citation://cit_revenue).",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    claim = result["quality"]["claims"][0]
    assert claim["status"] == "unverified"
    assert claim["bindings"][0]["supportStatus"] == "not-found"
    assert "claim_evidence_mismatch" in claim["issueCodes"]
    assert "claim_evidence_mismatch" in {issue["code"] for issue in result["quality"]["issues"]}


def test_policy_distinguishes_explicit_evidence_conflict_from_unmatched_support() -> None:
    citation = _structured()
    citation["evidence"]["entityId"] = "600519"

    result = evaluate_citation_quality(
        "000858 revenue was 120 USDm [source](citation://cit_revenue).",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    claim = result["quality"]["claims"][0]
    assert claim["status"] == "unverified"
    assert claim["bindings"][0]["supportStatus"] == "contradicted"
    assert "claim_evidence_conflict" in claim["issueCodes"]
    assert "claim_evidence_mismatch" not in claim["issueCodes"]
    assert "claim_evidence_conflict" in {issue["code"] for issue in result["quality"]["issues"]}


def test_text_chunk_numeric_miss_is_advisory_not_a_confirmed_conflict() -> None:
    citation = {
        "citationId": "cit_product_table",
        "source": {
            "sourceId": "annual-report",
            "providerId": "documents",
            "sourceType": "document",
            "documentId": "annual-report",
            "title": "Annual report",
            "retrievedAt": "2026-08-01T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": (
                "茅台酒营业收入145,928,075,955.31元，比上年增长15.28%；"
                "系列酒营业收入24,683,762,096.71元，比上年增长19.65%。"
            ),
            "snippet": "",
            "capturedAt": "2026-08-01T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "2023年茅台酒收入1,265.89亿元 [source](citation://cit_product_table)。",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    claim = result["quality"]["claims"][0]
    assert claim["status"] == "unverified"
    assert "claim_evidence_mismatch" in claim["issueCodes"]
    assert "claim_evidence_conflict" not in claim["issueCodes"]


def test_explicit_text_source_period_mismatch_is_a_concrete_conflict() -> None:
    citation = {
        "citationId": "cit_msft_q4",
        "source": {
            "sourceId": "msft-q4",
            "providerId": "reportify",
            "sourceType": "document",
            "title": "Microsoft (MSFT) - FY2026 Q4 - Earnings Call Transcript",
            "retrievedAt": "2026-08-03T08:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "We added another gigawatt of capacity this quarter.",
            "capturedAt": "2026-08-03T08:00:00Z",
        },
    }
    result = evaluate_citation_quality(
        "Q2 — 当季新增产能：单季约 1 GW [1](citation://cit_msft_q4)。",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    claim = result["quality"]["claims"][0]
    assert "claim_source_period_conflict" in claim["issueCodes"]
    issue = next(
        item
        for item in result["quality"]["issues"]
        if item["code"] == "claim_source_period_conflict"
    )
    assert issue["severity"] == "degraded"


def test_fiscal_period_and_call_date_do_not_create_false_period_conflict() -> None:
    citation = {
        "citationId": "cit_msft_q1",
        "source": {
            "sourceId": "msft-q1",
            "providerId": "reportify",
            "sourceType": "document",
            "title": "Microsoft (MSFT) - FY2026 Q1 - Earnings Call Transcript",
            "retrievedAt": "2026-08-05T08:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "FY2026 Q1 当季资本开支为 349 亿美元。",
            "capturedAt": "2026-08-05T08:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "FY26 Q1（2025-10-29）— 当季资本开支为 349 亿美元 [source](citation://cit_msft_q1)。",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    claim = result["quality"]["claims"][0]
    assert "claim_source_period_conflict" not in claim["issueCodes"]


def test_cross_language_paraphrase_is_not_reported_as_evidence_mismatch() -> None:
    citation = {
        "citationId": "cit_transcript",
        "source": {
            "sourceId": "transcript-q1",
            "providerId": "documents",
            "sourceType": "document",
            "title": "Earnings call transcript",
            "retrievedAt": "2026-08-01T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": (
                "Management said it is seeing increasing demand and diffusion "
                "of its artificial intelligence platform."
            ),
            "snippet": "",
            "capturedAt": "2026-08-01T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "管理层表示人工智能平台的需求正在持续增长 [source](citation://cit_transcript)。",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    claim = result["quality"]["claims"][0]
    assert claim["status"] == "unverified"
    assert "claim_translation_not_verified" in claim["issueCodes"]
    assert "claim_evidence_mismatch" not in claim["issueCodes"]
    assert result["quality"]["metrics"]["claimSemanticMismatchCount"] == 0


def test_policy_recomputes_calculation_and_checks_input_provenance() -> None:
    left = _structured("cit_left")
    left["evidence"]["field"] = "current"
    left["evidence"]["value"] = 120
    right = _structured("cit_right")
    right["evidence"]["field"] = "prior"
    right["evidence"]["value"] = 100
    calculation = {
        "citationId": "cit_growth",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Growth calculation",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "(current / prior) - 1",
            "inputs": [
                {
                    "name": "current",
                    "citationId": "cit_left",
                    "value": 120,
                    "unit": "USDm",
                },
                {
                    "name": "prior",
                    "citationId": "cit_right",
                    "value": 100,
                    "unit": "USDm",
                },
            ],
            "result": 0.25,
            "unit": "%",
            "rounding": "2dp",
            "calculatedAt": "2026-07-30T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "Values were 120 and 100; growth was 25%.",
        {
            "version": 1,
            "citations": [left, right, calculation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "calculation_result_mismatch" in {issue["code"] for issue in result["quality"]["issues"]}
    assert result["quality"]["layers"]["L4"] == "degraded"


def test_displayed_formula_inherits_the_adjacent_calculation_evidence() -> None:
    """A formula and its result are one calculation presentation, not two sources."""
    policy = _policy()
    policy["config"]["source_tiers"][0]["match"]["source_types"].append("tool-result")
    policy["config"]["source_tiers"][0]["match"]["tools"].append("runtime.calculation")
    current = _structured("cit_current")
    current["evidence"].update({"field": "current_revenue", "value": 120, "period": "FY2025"})
    prior = _structured("cit_prior")
    prior["evidence"].update({"field": "prior_revenue", "value": 100, "period": "FY2024"})
    calculation = {
        "citationId": "cit_growth",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Growth calculation",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "toolName": "runtime.calculation",
            "expression": "((current - prior) / prior) * 100",
            "inputs": [
                {
                    "name": "current",
                    "citationId": "cit_current",
                    "value": 120,
                    "unit": "USDm",
                },
                {
                    "name": "prior",
                    "citationId": "cit_prior",
                    "value": 100,
                    "unit": "USDm",
                },
            ],
            "result": 20,
            "unit": "%",
            "rounding": "2dp",
            "calculatedAt": "2026-07-30T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "\n".join(
            [
                "2025 revenue: 120 USDm [current](citation://cit_current)",
                "2024 revenue: 100 USDm [prior](citation://cit_prior)",
                "",
                "Calculation formula: (120 - 100) / 100",
                "",
                "Growth rate: 20% [calculation](citation://cit_growth)",
            ]
        ),
        {
            "version": 1,
            "citations": [current, prior, calculation],
            "integrity": _integrity(),
        },
        policy,
    )

    formula = next(
        claim for claim in result["quality"]["claims"] if "Calculation formula" in claim["exact"]
    )
    assert formula["citationIds"] == ["cit_growth"]
    assert formula["status"] == "auto-bound", (formula, result["quality"]["issues"])
    assert formula["issueCodes"] == []
    assert result["quality"]["status"] == "passed", result["quality"]["issues"]


def test_scaled_formula_inherits_adjacent_calculation_evidence() -> None:
    """One shared display scale must not break formula/result grouping."""
    policy = _policy()
    policy["config"]["source_tiers"][0]["match"]["source_types"].append("tool-result")
    policy["config"]["source_tiers"][0]["match"]["tools"].append("runtime.calculation")
    profit = _structured("cit_profit")
    profit["evidence"].update({"field": "net_profit", "value": 86_228_146_422, "unit": "CNY"})
    revenue = _structured("cit_revenue")
    revenue["evidence"].update(
        {"field": "operating_revenue", "value": 170_899_152_276, "unit": "CNY"}
    )
    calculation = {
        "citationId": "cit_margin",
        "source": {
            "sourceId": "calculation-margin",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Margin calculation",
            "retrievedAt": "2026-08-08T01:07:14Z",
        },
        "evidence": {
            "kind": "calculation",
            "toolName": "runtime.calculation",
            "expression": "profit / revenue * 100",
            "inputs": [
                {
                    "name": "profit",
                    "citationId": "cit_profit",
                    "value": 86_228_146_422,
                    "unit": "CNY",
                },
                {
                    "name": "revenue",
                    "citationId": "cit_revenue",
                    "value": 170_899_152_276,
                    "unit": "CNY",
                },
            ],
            "result": "50.46",
            "unit": "%",
            "rounding": "2dp",
            "calculatedAt": "2026-08-08T01:07:14Z",
            "metric": "归母净利率",
        },
    }

    result = evaluate_citation_quality(
        "\n".join(
            [
                "计算公式：归母净利率 = 归母净利润 ÷ 营业收入 = 862.28 ÷ 1,708.99",
                "",
                "归母净利率 = 50.46% [calculation](citation://cit_margin)",
            ]
        ),
        {
            "version": 1,
            "citations": [profit, revenue, calculation],
            "integrity": _integrity(),
        },
        policy,
    )

    formula = next(claim for claim in result["quality"]["claims"] if "计算公式" in claim["exact"])
    assert formula["citationIds"] == ["cit_margin"]
    assert formula["status"] == "auto-bound", (formula, result["quality"]["issues"])
    assert formula["issueCodes"] == []
    assert result["quality"]["status"] == "passed", result["quality"]["issues"]


def test_derived_claim_requires_calculation_evidence_not_only_input_citations() -> None:
    current = _structured("cit_current")
    current["evidence"]["field"] = "current_revenue"
    current["evidence"]["value"] = 120
    prior = _structured("cit_prior")
    prior["evidence"]["field"] = "prior_revenue"
    prior["evidence"]["value"] = 100

    result = evaluate_citation_quality(
        ("Revenue growth was 20% [current](citation://cit_current) [prior](citation://cit_prior)."),
        {
            "version": 1,
            "citations": [current, prior],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "derived_claim_without_calculation_evidence" in {
        issue["code"] for issue in result["quality"]["issues"]
    }
    assert result["quality"]["publishStatus"] == "draft-only"


def test_low_tier_claim_without_primary_cross_check_is_unverified() -> None:
    news = {
        "citationId": "cit_news",
        "source": {
            "sourceId": "news-1",
            "providerId": "news",
            "sourceType": "web",
            "title": "News",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Management may cut guidance.",
            "snippet": "Management may cut guidance.",
            "capturedAt": "2026-07-30T10:00:00Z",
        },
        "annotations": {"provenance": {"toolName": "search.news_search"}},
    }

    result = evaluate_citation_quality(
        "Guidance may fall [news](citation://cit_news).",
        {
            "version": 1,
            "citations": [news],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert result["quality"]["status"] == "unverified"
    assert result["quality"]["publishStatus"] == "ready"
    assert result["quality"]["issues"][0]["code"] == "low_tier_without_cross_check"
    assert result["citations"][0]["annotations"]["quality"]["status"] == "unverified"


def test_calculation_text_input_must_contain_the_claimed_value() -> None:
    text_input = {
        "citationId": "cit_text",
        "source": {
            "sourceId": "filing-1",
            "providerId": "documents",
            "sourceType": "document",
            "sourceCategory": "filings",
            "title": "Issuer filing",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Revenue increased during the period.",
            "snippet": "Revenue increased during the period.",
            "capturedAt": "2026-07-30T10:00:00Z",
        },
        "annotations": {"provenance": {"toolName": "search.filings_search"}},
    }
    calculation = {
        "citationId": "cit_calc",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Calculation",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "revenue / 2",
            "inputs": [
                {
                    "name": "revenue",
                    "citationId": "cit_text",
                    "value": 120,
                    "unit": "USDm",
                }
            ],
            "result": 60,
            "unit": "USDm",
            "rounding": "0dp",
            "calculatedAt": "2026-07-30T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "Half-year revenue was 60 USDm [calc](citation://cit_calc).",
        {
            "version": 1,
            "citations": [text_input, calculation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "calculation_input_text_value_unverified" in {
        issue["code"] for issue in result["quality"]["issues"]
    }
    assert result["quality"]["publishStatus"] == "draft-only"


def test_calculation_accepts_user_input_when_value_is_in_task_prompt() -> None:
    price = _structured("cit_price")
    price["evidence"].update(
        {
            "metric": "stock_price",
            "field": "stock_price",
            "value": 193.775,
            "unit": "USD",
            "asOf": "2026-08-03",
        }
    )
    calculation = {
        "citationId": "cit_calc",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Calculation",
            "retrievedAt": "2026-08-04T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "((price / cost) - 1) * 100",
            "inputs": [
                {
                    "name": "price",
                    "citationId": "cit_price",
                    "value": "193.775",
                    "unit": "USD",
                },
                {
                    "name": "cost",
                    "origin": "user-input",
                    "value": "150",
                    "unit": "USD",
                },
            ],
            "result": "29.2",
            "unit": "%",
            "rounding": "1dp",
            "metric": "return_since_cost",
            "calculatedAt": "2026-08-04T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "The gain since cost is 29.2% [calc](citation://cit_calc).",
        {
            "version": 1,
            "citations": [price, calculation],
            "integrity": _integrity(),
        },
        _policy(),
        user_prompt="The position cost is 150 USD.",
    )

    assert "calculation_user_input_not_found" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_calculation_rejects_claimed_user_input_missing_from_task_prompt() -> None:
    calculation = {
        "citationId": "cit_calc",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Calculation",
            "retrievedAt": "2026-08-04T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "cost * 1.2",
            "inputs": [
                {
                    "name": "cost",
                    "origin": "user-input",
                    "value": "150",
                    "unit": "USD",
                }
            ],
            "result": "180",
            "unit": "USD",
            "rounding": "0dp",
            "metric": "price_threshold",
            "calculatedAt": "2026-08-04T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "The threshold is 180 USD [calc](citation://cit_calc).",
        {
            "version": 1,
            "citations": [calculation],
            "integrity": _integrity(),
        },
        _policy(),
        user_prompt="Use my existing rule.",
    )

    assert "calculation_user_input_not_found" in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_calculation_accepts_scaled_structured_input_units() -> None:
    policy = _policy()
    policy["config"]["semantics"] = {
        "unit_ontology": {
            "units": {
                "yuan": {"canonical": "CNY", "aliases": ["元", "CNY"], "scale": 1},
                "hundred-million": {
                    "canonical": "CNY",
                    "aliases": ["亿元"],
                    "scale": 100_000_000,
                },
            }
        }
    }
    source = _structured("cit_source")
    source["evidence"].update(
        {
            "value": 174_144_069_958.25,
            "unit": "CNY",
            "field": "operating_revenue",
        }
    )
    calculation = {
        "citationId": "cit_calc",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Display conversion",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "current",
            "inputs": [
                {
                    "name": "current",
                    "citationId": "cit_source",
                    "value": 1741.44,
                    "unit": "亿元",
                }
            ],
            "result": 1741.44,
            "unit": "亿元",
            "rounding": "2dp",
            "calculatedAt": "2026-07-30T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "营业收入为 1,741.44 亿元 [calc](citation://cit_calc).",
        {
            "version": 1,
            "citations": [source, calculation],
            "integrity": _integrity(),
        },
        policy,
    )
    codes = {issue["code"] for issue in result["quality"]["issues"]}

    assert "calculation_input_value_mismatch" not in codes
    assert "calculation_input_unit_mismatch" not in codes


def test_calculation_accepts_a_verified_calculation_as_an_input() -> None:
    current = _structured("cit_current")
    current["evidence"].update({"field": "q1", "value": 10, "unit": "USDm"})
    prior = _structured("cit_prior")
    prior["evidence"].update({"field": "q2", "value": 20, "unit": "USDm"})
    market_cap = _structured("cit_market_cap")
    market_cap["evidence"].update(
        {"field": "market_cap", "metric": "market_cap", "value": 585, "unit": "USDm"}
    )
    ttm = {
        "citationId": "cit_ttm",
        "source": {
            "sourceId": "calculation-ttm",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "TTM calculation",
            "retrievedAt": "2026-08-03T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "metric": "ttm_revenue",
            "expression": "q1 + q2",
            "inputs": [
                {"name": "q1", "citationId": "cit_current", "value": 10, "unit": "USDm"},
                {"name": "q2", "citationId": "cit_prior", "value": 20, "unit": "USDm"},
            ],
            "result": 30,
            "unit": "USDm",
            "rounding": "0dp",
            "calculatedAt": "2026-08-03T10:00:00Z",
        },
    }
    ratio = {
        "citationId": "cit_ratio",
        "source": {
            "sourceId": "calculation-ratio",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "PS calculation",
            "retrievedAt": "2026-08-03T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "metric": "price_to_sales_ttm",
            "expression": "market_cap / ttm_revenue",
            "inputs": [
                {
                    "name": "market_cap",
                    "citationId": "cit_market_cap",
                    "value": 585,
                    "unit": "USDm",
                },
                {
                    "name": "ttm_revenue",
                    "citationId": "cit_ttm",
                    "value": 30,
                    "unit": "USDm",
                },
            ],
            "result": 19.5,
            "unit": "x",
            "rounding": "1dp",
            "calculatedAt": "2026-08-03T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "TTM PS was 19.5x [calc](citation://cit_ratio).",
        {
            "version": 1,
            "citations": [current, prior, market_cap, ttm, ratio],
            "integrity": _integrity(),
        },
        _policy(),
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert "calculation_input_evidence_unsupported" not in codes
    assert "calculation_input_value_mismatch" not in codes
    assert "calculation_input_unit_mismatch" not in codes
    assert "calculation_result_not_present_in_answer" not in codes


def test_calculation_does_not_invent_a_unit_conflict_when_source_unit_is_missing() -> None:
    source = _structured("cit_source")
    source["evidence"].update({"field": "market_cap", "value": 120, "unit": ""})
    calculation = {
        "citationId": "cit_calc",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Calculation",
            "retrievedAt": "2026-08-03T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "market_cap",
            "inputs": [
                {
                    "name": "market_cap",
                    "citationId": "cit_source",
                    "value": 120,
                    "unit": "USDm",
                }
            ],
            "result": 120,
            "unit": "USDm",
            "rounding": "0dp",
            "calculatedAt": "2026-08-03T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "Market cap was 120 USDm [calc](citation://cit_calc).",
        {
            "version": 1,
            "citations": [source, calculation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert "numeric_unit_missing" in codes
    assert "calculation_input_unit_mismatch" not in codes


def test_user_supplied_threshold_is_not_reported_as_requiring_external_citation() -> None:
    result = evaluate_citation_quality(
        "The stop-loss threshold is 160 USD.",
        {
            "version": 1,
            "citations": [],
            "integrity": _integrity(),
        },
        _policy(),
        user_prompt="Use a stop-loss threshold of 160 USD.",
    )

    assert result["quality"]["metrics"]["claimCitationRequiredCount"] == 0
    assert result["quality"]["metrics"]["unsourcedClaimCount"] == 0
    assert result["quality"]["claims"][0]["citationRequired"] is False
    assert result["quality"]["claims"][0]["status"] == "passed"


def test_structured_preflight_preserves_markdown_table_header_unit() -> None:
    policy = _policy()
    policy["config"]["semantics"] = {
        "metric_ontology": {
            "metrics": {
                "operating_revenue": {
                    "aliases": ["营业收入"],
                    "fields": ["operating_revenue"],
                }
            }
        },
        "unit_ontology": {
            "units": {
                "yuan": {"canonical": "CNY", "aliases": ["CNY", "元"], "scale": 1},
                "hundred-million": {
                    "canonical": "CNY",
                    "aliases": ["亿元"],
                    "scale": 100_000_000,
                },
            }
        },
    }
    citation = _structured()
    citation["evidence"].update(
        {
            "field": "operating_revenue",
            "value": 170_899_152_276,
            "unit": "CNY",
            "period": "2024 FY",
            "entityName": "贵州茅台",
        }
    )
    result = evaluate_citation_quality(
        "| 公司 | 营业收入（亿元） |\n"
        "|---|---:|\n"
        "| 贵州茅台 | 1,708.99 [1](citation://cit_revenue) |",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        policy,
    )

    assert "structured_value_not_present_in_answer" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }
    assert result["quality"]["status"] == "passed"


def test_structured_preflight_inherits_unit_from_short_table_preface() -> None:
    policy = _policy()
    policy["config"]["semantics"] = {
        "metric_ontology": {
            "metrics": {
                "operating_revenue": {
                    "aliases": ["营业收入", "营收"],
                    "fields": ["revenue", "operating_revenue"],
                }
            }
        },
        "unit_ontology": {
            "units": {
                "usd": {"canonical": "USD", "aliases": ["USD", "美元"], "scale": 1},
                "usd-hundred-million": {
                    "canonical": "USD",
                    "aliases": ["亿美元"],
                    "scale": 100_000_000,
                },
            }
        },
    }
    citation = _structured()
    citation["evidence"].update(
        {
            "field": "revenue",
            "metric": "operating_revenue",
            "value": 81_615_000_000,
            "unit": "USD",
            "period": "2027 Q1",
            "entityId": "NVDA",
        }
    )
    result = evaluate_citation_quality(
        "> 财年截止日：FY2027 Q1 = 2026-04-26；金额单位：亿美元。\n\n"
        "| 项目 | FY2027 Q1 |\n"
        "|---|---:|\n"
        "| 营收 | 816.15 [1](citation://cit_revenue) |",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        policy,
    )

    assert "structured_value_not_present_in_answer" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_structured_preflight_inherits_unit_from_sibling_period_cell() -> None:
    policy = _policy()
    policy["config"]["semantics"] = {
        "metric_ontology": {
            "metrics": {
                "operating_revenue": {
                    "aliases": ["营业收入"],
                    "fields": ["revenue", "operating_revenue"],
                }
            }
        },
        "unit_ontology": {
            "units": {
                "usd": {"canonical": "USD", "aliases": ["USD", "美元"], "scale": 1},
                "usd-million": {
                    "canonical": "USD",
                    "aliases": ["百万美元", "USD million"],
                    "scale": 1_000_000,
                },
            }
        },
    }
    citation = _structured()
    citation["source"]["title"] = "Company income statement · SNDK"
    citation["evidence"].update(
        {
            "field": "revenue",
            "metric": "operating_revenue",
            "value": 5_950_000_000,
            "unit": "USD",
            "period": "2026 Q3",
            "entityId": "SNDK",
            "entityName": "闪迪",
        }
    )
    result = evaluate_citation_quality(
        "| 公司 | 报告期 | 营业收入 |\n"
        "|---|---|---:|\n"
        "| 闪迪 | FY2026 Q3；单位：百万美元 | "
        "5,950 [1](citation://cit_revenue) |",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        policy,
    )
    codes = {issue["code"] for issue in result["quality"]["issues"]}

    assert "structured_value_not_present_in_answer" not in codes
    assert "claim_evidence_mismatch" not in codes
    assert result["quality"]["status"] == "passed"


def test_structured_preflight_accepts_usd_hundred_million_alias() -> None:
    policy = _policy()
    policy["config"]["semantics"] = {
        "metric_ontology": {
            "metrics": {
                "operating_revenue": {
                    "aliases": ["营业收入"],
                    "fields": ["operating_revenue"],
                }
            }
        },
        "unit_ontology": {
            "units": {
                "usd": {"canonical": "USD", "aliases": ["USD"], "scale": 1},
                "usd_hundred_million": {
                    "canonical": "USD",
                    "aliases": ["亿美元"],
                    "scale": 100_000_000,
                },
            }
        },
    }
    citation = _structured()
    citation["evidence"].update(
        {
            "field": "operating_revenue",
            "value": 5_950_000_000,
            "unit": "USD",
            "period": "FY2025",
            "entityName": "美光科技",
        }
    )

    result = evaluate_citation_quality(
        "美光科技 FY2025 营业收入为 59.50 亿美元 [1](citation://cit_revenue)。",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        policy,
    )

    assert "structured_value_not_present_in_answer" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_company_source_mismatch_is_a_concrete_entity_conflict() -> None:
    sandisk = {
        "citationId": "cit_sandisk",
        "source": {
            "sourceId": "sandisk-filing",
            "providerId": "filings",
            "sourceType": "document",
            "title": "Sandisk (SNDK) FY2025 annual report",
            "retrievedAt": "2026-08-01T08:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Sandisk revenue was 7.2 billion USD.",
            "capturedAt": "2026-08-01T08:00:00Z",
        },
    }
    micron = {
        "citationId": "cit_micron",
        "source": {
            "sourceId": "micron-filing",
            "providerId": "filings",
            "sourceType": "document",
            "title": "Micron Technology (MU) FY2025 annual report",
            "retrievedAt": "2026-08-01T08:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Micron revenue was 37.4 billion USD.",
            "capturedAt": "2026-08-01T08:00:00Z",
        },
    }
    answer = (
        "| 公司 | 营业收入 |\n"
        "|---|---:|\n"
        "| 闪迪（SNDK） | 72 亿美元 [1](citation://cit_micron) |\n"
        "| 美光科技（MU） | 374 亿美元 [2](citation://cit_micron) |"
    )

    result = evaluate_citation_quality(
        answer,
        {
            "version": 1,
            "citations": [sandisk, micron],
            "integrity": _integrity(),
        },
        _policy(),
    )

    conflicts = [
        issue
        for issue in result["quality"]["issues"]
        if issue["code"] == "claim_source_entity_conflict"
    ]
    assert len(conflicts) == 1
    assert conflicts[0]["citationIds"] == ["cit_micron"]
    assert "闪迪" in conflicts[0]["claim"]["exact"]


def test_inherited_presentation_entity_cannot_create_a_hard_conflict() -> None:
    citation = _structured("cit_nvidia")
    citation["source"]["title"] = "NVIDIA (NVDA) FY2025 income statement"
    citation["evidence"].update(
        {
            "entityId": "NVDA",
            "entityName": "NVIDIA",
            "period": "FY2025",
        }
    )
    answer = (
        "**Broadcom (AVGO) results follow**\n\n"
        "Revenue was 120 USDm [source](citation://cit_nvidia)."
    )

    result = evaluate_citation_quality(
        answer,
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    revenue_claim = next(
        claim for claim in result["quality"]["claims"] if "Revenue" in claim["exact"]
    )
    assert "claim_source_entity_conflict" not in revenue_claim["issueCodes"]


def test_opaque_structured_ticker_is_unknown_not_cross_company_conflict() -> None:
    citation = _structured("cit_sandisk")
    citation["source"]["title"] = "Company income statement · SNDK"
    citation["evidence"].update(
        {
            "entityId": "SNDK",
            "metric": "operating_revenue",
            "period": "FY2026 Q3",
            "value": 5_950_000_000,
            "unit": "USD",
        }
    )
    answer = (
        "| 公司 | 报告期 | 营业收入 |\n"
        "|---|---|---:|\n"
        "| 闪迪 | FY2026 Q3 | 59.50亿美元 [1](citation://cit_sandisk) |"
    )

    result = evaluate_citation_quality(
        answer,
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
        entity_aliases={"闪迪": ("闪迪",)},
    )

    assert "claim_source_entity_conflict" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_unlinked_translated_company_name_is_unknown_not_cross_company_conflict() -> None:
    citation = {
        "citationId": "cit_msft_q1",
        "source": {
            "sourceId": "msft-q1",
            "providerId": "reportify",
            "sourceType": "document",
            "title": "Microsoft (MSFT) - FY2026 Q1 - Earnings Call Transcript",
            "retrievedAt": "2026-08-03T08:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Microsoft Cloud revenue surpassed $49 billion, up 26% year-over-year.",
            "capturedAt": "2026-08-03T08:00:00Z",
        },
    }
    result = evaluate_citation_quality(
        "微软云收入超过490亿美元，同比增长26% [1](citation://cit_msft_q1)。",
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        _policy(),
        entity_aliases={"微软": ("微软",)},
    )

    assert "claim_source_entity_conflict" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_metric_acronym_in_parentheses_is_not_a_company_identifier() -> None:
    microsoft = {
        "citationId": "cit_msft",
        "source": {
            "sourceId": "msft-q3",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Microsoft (MSFT) FY2026 Q3 earnings call transcript",
            "retrievedAt": "2026-08-02T08:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Our AI business surpassed $37 billion ARR, up 123%.",
            "capturedAt": "2026-08-02T08:00:00Z",
        },
    }
    result = evaluate_citation_quality(
        "Microsoft AI 业务年化收入（ARR）突破 370 亿美元，同比增长 123% [1](citation://cit_msft)。",
        {
            "version": 1,
            "citations": [microsoft],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "claim_source_entity_conflict" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_configured_metric_acronym_is_not_inferred_as_company_identifier() -> None:
    citation = _structured("cit_fcf")
    citation["source"]["title"] = "NVIDIA (NVDA) cash flow statement"
    citation["evidence"].update(
        {
            "entityId": "NVDA",
            "entityName": "NVIDIA",
            "field": "free_cash_flow",
            "metric": "free_cash_flow",
            "value": 485.87,
            "unit": "USDm",
            "period": "FY2027 Q1",
        }
    )
    policy = _policy()
    policy["config"]["semantics"] = {
        "metric_ontology": {
            "metrics": {
                "free_cash_flow": {
                    "aliases": ["自由现金流", "free cash flow", "FCF"],
                    "fields": ["free_cash_flow"],
                }
            }
        }
    }
    answer = (
        "NVIDIA（NVDA）\n\n"
        "| 项目 | FY2027 Q1 |\n"
        "|---|---:|\n"
        "| 自由现金流（FCF） | 485.87 USDm [1](citation://cit_fcf) |"
    )

    result = evaluate_citation_quality(
        answer,
        {
            "version": 1,
            "citations": [citation],
            "integrity": _integrity(),
        },
        policy,
    )

    assert "claim_source_entity_conflict" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_speaker_role_in_parentheses_is_not_a_company_identifier() -> None:
    microsoft = {
        "citationId": "cit_msft",
        "source": {
            "sourceId": "msft-q1",
            "providerId": "reportify",
            "sourceType": "document",
            "title": "Microsoft (MSFT) FY2026 Q1 earnings call transcript",
            "retrievedAt": "2026-08-03T08:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": (
                "Satya Nadella said we will increase our total AI capacity by over 80% this year."
            ),
            "capturedAt": "2026-08-03T08:00:00Z",
        },
    }
    answer = (
        "微软（MSFT）FY2026 Q1 电话会：Satya Nadella（CEO）表示，"
        "本年 AI 总容量将增加超过 80% "
        "[1](citation://cit_msft)。随后 Amy Hood（CFO）补充了资本开支安排。"
    )

    result = evaluate_citation_quality(
        answer,
        {
            "version": 1,
            "citations": [microsoft],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "claim_source_entity_conflict" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_calculation_does_not_compare_entity_name_to_ticker_id() -> None:
    source = _structured("cit_source")
    source["evidence"].update(
        {
            "value": 120,
            "unit": "USDm",
            "entityId": "600519",
        }
    )
    calculation = {
        "citationId": "cit_calc",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Calculation",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "current",
            "inputs": [
                {
                    "name": "current",
                    "citationId": "cit_source",
                    "value": 120,
                    "unit": "USDm",
                }
            ],
            "result": 120,
            "unit": "USDm",
            "rounding": "0dp",
            "calculatedAt": "2026-07-30T10:00:00Z",
            "entityName": "贵州茅台",
        },
    }

    result = evaluate_citation_quality(
        "Revenue was 120 USDm [calc](citation://cit_calc).",
        {
            "version": 1,
            "citations": [source, calculation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "calculation_input_entity_mismatch" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_calculation_inputs_cover_dimension_not_repeated_on_result() -> None:
    policy = _policy()
    policy["config"]["source_tiers"][0]["match"]["source_types"].append("tool-result")
    policy["config"]["source_tiers"][0]["match"]["tools"].append("runtime.calculation")
    policy["config"]["semantics"] = {
        "metric_ontology": {
            "metrics": {
                "net_margin": {
                    "aliases": ["归母净利率"],
                    "fields": ["net_margin"],
                }
            }
        },
        "dimensions": {
            "basis": {
                "attributable": ["归母", "attributable to parent"],
            }
        },
    }
    profit = _structured("cit_profit")
    profit["evidence"].update(
        {
            "field": "parent_net_profit",
            "metric": "parent_net_profit",
            "value": 120,
            "unit": "USDm",
            "basis": "attributable",
        }
    )
    revenue = _structured("cit_revenue")
    revenue["evidence"].update(
        {
            "field": "revenue",
            "metric": "revenue",
            "value": 200,
            "unit": "USDm",
        }
    )
    calculation = {
        "citationId": "cit_calc",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Calculation",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "toolName": "runtime.calculation",
            "expression": "(profit / revenue) * 100",
            "inputs": [
                {
                    "name": "profit",
                    "citationId": "cit_profit",
                    "value": 120,
                    "unit": "USDm",
                },
                {
                    "name": "revenue",
                    "citationId": "cit_revenue",
                    "value": 200,
                    "unit": "USDm",
                },
            ],
            "result": 60,
            "unit": "%",
            "rounding": "2dp",
            "calculatedAt": "2026-07-30T10:00:00Z",
            "metric": "net_margin",
            # ``basis`` is intentionally omitted: the structured profit input
            # already carries the attributable-to-parent basis.
        },
    }

    result = evaluate_citation_quality(
        "归母净利率 = 120 USDm / 200 USDm = 60% [calc](citation://cit_calc).",
        {
            "version": 1,
            "citations": [profit, revenue, calculation],
            "integrity": _integrity(),
        },
        policy,
    )

    codes = {issue["code"] for issue in result["quality"]["issues"]}
    assert "claim_partially_supported" not in codes
    assert result["quality"]["status"] == "passed", result["quality"]["issues"]


def test_calculation_text_input_matches_comma_formatted_number() -> None:
    text_input = {
        "citationId": "cit_text",
        "source": {
            "sourceId": "filing-1",
            "providerId": "documents",
            "sourceType": "document",
            "sourceCategory": "filings",
            "title": "Issuer filing",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "归属于母公司所有者的净利润 86,228,146,421.62 元。",
            "capturedAt": "2026-07-30T10:00:00Z",
        },
        "annotations": {"provenance": {"toolName": "search.filings_search"}},
    }
    calculation = {
        "citationId": "cit_calc",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Calculation",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "profit",
            "inputs": [
                {
                    "name": "profit",
                    "citationId": "cit_text",
                    "value": 86228146421.62,
                    "unit": "CNY",
                }
            ],
            "result": 86228146421.62,
            "unit": "CNY",
            "rounding": "2dp",
            "calculatedAt": "2026-07-30T10:00:00Z",
        },
    }

    result = evaluate_citation_quality(
        "归母净利润为 86,228,146,421.62 CNY [calc](citation://cit_calc).",
        {
            "version": 1,
            "citations": [text_input, calculation],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "calculation_input_text_value_unverified" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_calculation_result_matches_number_adjacent_to_chinese_text() -> None:
    source = _structured("cit_source")
    source["evidence"].update(
        {
            "field": "net_profit_growth",
            "value": 15.38,
            "unit": "%",
            "period": "FY2024",
        }
    )
    calculation = {
        "citationId": "cit_calc",
        "source": {
            "sourceId": "calculation-1",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Calculation",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "growth",
            "inputs": [
                {
                    "name": "growth",
                    "citationId": "cit_source",
                    "value": 15.38,
                    "unit": "%",
                }
            ],
            "result": 15.38,
            "unit": "%",
            "rounding": "2dp",
            "calculatedAt": "2026-07-30T10:00:00Z",
        },
    }
    policy = _policy()
    policy["config"]["rules"]["derived_value"]["require_result_in_answer"] = True

    result = evaluate_citation_quality(
        "归母净利润同比增长15.38% [计算](citation://cit_calc)。",
        {
            "version": 1,
            "citations": [source, calculation],
            "integrity": _integrity(),
        },
        policy,
    )

    assert "calculation_result_not_present_in_answer" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_negative_calculation_result_matches_unicode_minus_and_decline_wording() -> None:
    source = _structured("cit_source")
    source["evidence"].update(
        {
            "field": "net_profit_growth",
            "value": -1.54,
            "unit": "%",
            "period": "FY2024",
        }
    )
    calculation = {
        "citationId": "cit_calc",
        "source": {
            "sourceId": "calculation-negative",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Calculation",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "expression": "growth",
            "inputs": [
                {
                    "name": "growth",
                    "citationId": "cit_source",
                    "value": -1.54,
                    "unit": "%",
                }
            ],
            "result": -1.54,
            "unit": "%",
            "rounding": "2dp",
            "calculatedAt": "2026-07-30T10:00:00Z",
        },
    }
    policy = _policy()
    policy["config"]["rules"]["derived_value"]["require_result_in_answer"] = True

    for answer in (
        "归母净利润同比为−1.54% [计算](citation://cit_calc)。",
        "归母净利润同比下降1.54% [计算](citation://cit_calc)。",
    ):
        result = evaluate_citation_quality(
            answer,
            {
                "version": 1,
                "citations": [source, calculation],
                "integrity": _integrity(),
            },
            policy,
        )
        assert "calculation_result_not_present_in_answer" not in {
            issue["code"] for issue in result["quality"]["issues"]
        }


def test_calculation_result_accepts_a_less_precise_display_rounding() -> None:
    source = _structured("cit_margin_source")
    source["evidence"].update(
        {
            "field": "gross_margin",
            "metric": "gross_margin",
            "value": 74.93,
            "unit": "%",
            "period": "FY2027 Q1",
        }
    )
    calculation = {
        "citationId": "cit_margin_calc",
        "source": {
            "sourceId": "calculation-margin",
            "providerId": "runtime",
            "sourceType": "tool-result",
            "title": "Calculation",
            "retrievedAt": "2026-08-11T10:00:00Z",
        },
        "evidence": {
            "kind": "calculation",
            "metric": "gross_margin",
            "expression": "margin",
            "inputs": [
                {
                    "name": "margin",
                    "citationId": "cit_margin_source",
                    "value": 74.93,
                    "unit": "%",
                }
            ],
            "result": 74.93,
            "unit": "%",
            "rounding": "2dp",
            "calculatedAt": "2026-08-11T10:00:00Z",
        },
    }
    policy = _policy()
    policy["config"]["rules"]["derived_value"]["require_result_in_answer"] = True

    result = evaluate_citation_quality(
        "FY2027 Q1 毛利率为 74.9% [计算](citation://cit_margin_calc)。",
        {
            "version": 1,
            "citations": [source, calculation],
            "integrity": _integrity(),
        },
        policy,
    )

    assert "calculation_result_not_present_in_answer" not in {
        issue["code"] for issue in result["quality"]["issues"]
    }


def test_document_category_tiers_fetched_chunk_not_generic_fetch_tool() -> None:
    filing = {
        "citationId": "cit_filing",
        "source": {
            "sourceId": "filing-1",
            "providerId": "valuz-search",
            "sourceType": "document",
            "sourceCategory": "filings",
            "title": "Issuer filing",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Revenue was 120.",
            "snippet": "Revenue was 120.",
            "capturedAt": "2026-07-30T10:00:00Z",
        },
        "annotations": {"provenance": {"toolName": "document_fetch"}},
    }

    result = evaluate_citation_quality(
        "Revenue was 120 [filing](citation://cit_filing).",
        {
            "version": 1,
            "citations": [filing],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert result["quality"]["status"] == "passed"
    assert result["quality"]["metrics"]["tierCounts"] == {"P2": 1}


def test_low_tier_requires_cross_check_on_same_claim_not_elsewhere() -> None:
    news = {
        "citationId": "cit_news",
        "source": {
            "sourceId": "news-1",
            "providerId": "news",
            "sourceType": "web",
            "title": "News",
            "retrievedAt": "2026-07-30T10:00:00Z",
        },
        "evidence": {
            "kind": "text",
            "quote": "Guidance may fall.",
            "snippet": "Guidance may fall.",
            "capturedAt": "2026-07-30T10:00:00Z",
        },
        "annotations": {"provenance": {"toolName": "search.news_search"}},
    }
    primary = _structured("cit_primary")

    result = evaluate_citation_quality(
        (
            "Guidance may fall [news](citation://cit_news). "
            "Revenue was 120 [data](citation://cit_primary)."
        ),
        {
            "version": 1,
            "citations": [news, primary],
            "integrity": _integrity(),
        },
        _policy(),
    )

    assert "low_tier_without_cross_check" in {
        issue["code"] for issue in result["quality"]["issues"]
    }

    checked = evaluate_citation_quality(
        ("Guidance may fall [news](citation://cit_news) [data](citation://cit_primary)."),
        {
            "version": 1,
            "citations": [news, primary],
            "integrity": _integrity(),
        },
        _policy(),
    )
    assert "low_tier_without_cross_check" not in {
        issue["code"] for issue in checked["quality"]["issues"]
    }


def test_quality_bundle_exposes_claim_audit_truncation() -> None:
    answer = "\n".join(
        f"- Company {index} reported revenue of {index + 1} USD."
        for index in range(MAX_CLAIMS_PER_ANSWER + 1)
    )

    result = evaluate_citation_quality(
        answer,
        {"version": 1, "citations": [], "integrity": _integrity()},
        _policy(),
    )

    assert result["quality"]["metrics"]["claimAuditTruncated"] is True
    assert "claim_audit_truncated" in {issue["code"] for issue in result["quality"]["issues"]}

"""Markdown-aware atomic claim extraction and conservative evidence matching."""

from __future__ import annotations

from src.core.claim_audit import (
    MAX_CLAIMS_PER_ANSWER,
    ClaimCandidate,
    _claim_amounts,
    auto_bind_composite_text_claims,
    auto_bind_unique_claims,
    bind_claims_to_evidence,
    calculation_formula_matches_evidence,
    extract_claims,
    extract_claims_with_status,
    match_available_evidence,
    propagate_equivalent_claim_bindings,
    structured_value_present,
    text_components_cover_claim,
    verify_evidence_support,
)

_FINANCE_SEMANTICS = {
    "metric_ontology": {
        "metrics": {
            "operating_revenue": {
                "aliases": ["营业收入", "销售收入", "revenue", "operating revenue"],
                "fields": ["operating_revenue"],
            },
            "net_profit": {
                "aliases": ["净利润", "net profit"],
                "fields": ["net_profit"],
            },
            "revenue_growth": {
                "aliases": ["营业收入同比增长", "营业收入同比增速", "revenue growth"],
                "fields": ["operating_revenue_growth_rate"],
            },
            "audit_opinion": {
                "aliases": ["审计意见"],
                "fields": ["audit_opinion_type"],
            },
            "reporting_period": {
                "aliases": ["报告期", "财年"],
                "fields": ["fiscal_year"],
            },
            "filing_date": {
                "aliases": ["申报日期", "出具日期"],
                "fields": ["filing_date"],
                "date_role": "publication",
            },
            "capital_expenditure": {
                "aliases": ["资本支出", "资本开支", "capex", "capital expenditure"],
                "fields": ["capital_expenditure"],
                "value_transform": "absolute",
            },
        }
    },
    "unit_ontology": {
        "units": {
            "yuan": {"canonical": "CNY", "aliases": ["元", "CNY"], "scale": 1},
            "ten-thousand": {
                "canonical": "CNY",
                "aliases": ["万元"],
                "scale": 10_000,
            },
            "hundred-million": {
                "canonical": "CNY",
                "aliases": ["亿元"],
                "scale": 100_000_000,
            },
            "percentage": {
                "canonical": "percent",
                "aliases": ["%"],
                "scale": 1,
            },
            "usd_hundred_million": {
                "canonical": "USD",
                "aliases": ["亿美元", "USD 100m"],
                "scale": 100_000_000,
            },
            "usd": {
                "canonical": "USD",
                "aliases": ["美元", "USD"],
                "scale": 1,
            },
            "usd_million": {
                "canonical": "USD",
                "aliases": ["百万美元", "USD million", "USDm"],
                "scale": 1_000_000,
            },
            "usd_billion": {
                "canonical": "USD",
                "aliases": ["十亿美元", "USD billion", "USD bn"],
                "scale": 1_000_000_000,
            },
            "krw": {
                "canonical": "KRW",
                "aliases": ["韩元", "KRW"],
                "scale": 1,
            },
            "krw_million": {
                "canonical": "KRW",
                "aliases": [
                    "百万韩元",
                    "KRW million",
                    "KRWm",
                    "million KRW",
                    "millions of Korean won",
                    "In millions of Korean won",
                ],
                "scale": 1_000_000,
            },
            "krw_hundred_million": {
                "canonical": "KRW",
                "aliases": ["亿韩元", "KRW 100m"],
                "scale": 100_000_000,
            },
            "krw_trillion": {
                "canonical": "KRW",
                "aliases": ["万亿韩元", "KRW trillion"],
                "scale": 1_000_000_000_000,
            },
        }
    },
    "dimensions": {
        "scope": {
            "consolidated": ["合并", "consolidated"],
            "segment": ["分部", "segment"],
        },
        "basis": {},
    },
    "calculation_dependencies": {"revenue_growth": ["operating_revenue"]},
}


def _structured_record(
    handle: str,
    *,
    field: str,
    value: int | float,
    period: str = "2024 FY",
) -> dict:
    return {
        "evidenceHandle": handle,
        "source": {
            "sourceId": f"source-{handle}",
            "providerId": "test-data",
            "sourceType": "dataset",
            "title": "Financial data",
            "retrievedAt": "2026-08-01T08:00:00Z",
        },
        "evidence": {
            "kind": "structured-data",
            "datasetId": "financials",
            "toolName": "company_income_statement",
            "recordKey": "600519|2024 FY",
            "field": field,
            "value": value,
            "unit": "%",
            "period": period,
            "capturedAt": "2026-08-01T08:00:00Z",
        },
    }


def test_extracts_every_claim_when_one_sentence_is_already_cited() -> None:
    claims = extract_claims(
        "Revenue was 120 USD [source](citation://cit_revenue). Margin was 23.5%.",
        mode="required-on-evidence",
    )

    assert [claim.exact for claim in claims] == [
        "Revenue was 120 USD.",
        "Margin was 23.5%.",
    ]
    assert claims[0].attached_citation_ids == ("cit_revenue",)
    assert claims[1].attached_citation_ids == ()
    assert all(claim.citation_required for claim in claims)
    assert {key: claims[0].location[key] for key in ("kind", "blockIndex", "start", "end")} == {
        "kind": "text",
        "blockIndex": 0,
        "start": 0,
        "end": 20,
    }
    assert claims[0].location["sourceStart"] == 0
    assert claims[0].location["sourceEnd"] > claims[0].location["end"]


def test_text_quote_supports_verbatim_claim_after_markdown_label() -> None:
    claim = extract_claims(
        "**审计意见：** 天健会计师事务所（特殊普通合伙）为本公司出具了标准无保留意见的审计报告。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": ("三、 天健会计师事务所(特殊普通合伙)为本公司出具了标准无保留意见的审计报告。"),
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_attaches_citation_written_after_terminal_punctuation() -> None:
    claims = extract_claims(
        "营业总收入为 174,144,069,958.25 元。 [来源](citation://cit_revenue) 下一项为说明。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert claims[0].exact == "营业总收入为 174,144,069,958.25 元。"
    assert claims[0].attached_citation_ids == ("cit_revenue",)
    assert claims[1].attached_citation_ids == ()


def test_document_locator_numbers_are_not_business_amounts() -> None:
    amounts = _claim_amounts(
        "同比增幅来自第9页：直销11.32%，批发代理19.73%。",
        _FINANCE_SEMANTICS,
    )

    assert [amount[0] for amount in amounts] == ["11.32", "19.73"]


def test_unicode_minus_and_directional_decline_normalize_to_negative_amount() -> None:
    unicode_minus = _claim_amounts("同比增长率为−1.54%。", _FINANCE_SEMANTICS)
    directional = _claim_amounts("同比下降 1.54%。", _FINANCE_SEMANTICS)

    assert str(unicode_minus[0][2]) == str(directional[0][2]) == "-1.54"


def test_period_only_label_is_not_invented_as_a_metric() -> None:
    claim = extract_claims(
        "2026Q1：10,285,128,726 元",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]

    assert claim.normalized["period"] == "2026 Q1"
    assert "metric" not in claim.normalized


def test_redundant_source_attribution_footer_is_presentation() -> None:
    claims = extract_claims(
        "以上数据均引自贵州茅台《2024年年度报告》。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert len(claims) == 1
    assert claims[0].kind == "presentation"
    assert claims[0].citation_required is False

    intro = extract_claims(
        "以下三项均引自贵州茅台2024年年度报告原文。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )
    assert intro[0].kind == "presentation"
    assert intro[0].citation_required is False


def test_preserves_business_value_wrapped_by_citation_link() -> None:
    claims = extract_claims(
        (
            "贵州茅台 2024 年营业收入 "
            "[1,709 亿元](citation://cit_revenue)，"
            "同比增速为 [+15.71%](citation://cit_growth)。"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.exact for claim in claims] == [
        "贵州茅台 2024 年营业收入 1,709 亿元，",
        "同比增速为 +15.71%。",
    ]
    assert claims[0].attached_citation_ids == ("cit_revenue",)
    assert claims[1].attached_citation_ids == ("cit_growth",)
    assert claims[0].normalized["valueBase"] == "170900000000"
    assert claims[1].normalized["metric"] == "revenue_growth"


def test_calculation_metric_accepts_edition_alias() -> None:
    claim = extract_claims(
        "2024 年营业收入同比增速为 +15.71%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "calculation",
        "expression": "((current - prior) / prior) * 100",
        "inputs": [],
        "result": 15.71,
        "unit": "%",
        "metric": "营业收入同比增速",
        "period": "2024 FY",
        "calculatedAt": "2026-08-01T08:00:00Z",
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_structured_metric_prefers_exact_mixed_language_alias_over_short_machine_tokens() -> None:
    semantics = {
        "metric_ontology": {
            "metrics": {
                "ttm_revenue": {
                    "aliases": ["TTM 营业收入"],
                    "fields": ["ttm_revenue"],
                },
                "price_to_sales_ttm": {
                    "aliases": ["TTM 市销率"],
                    "fields": ["price_to_sales_ttm"],
                },
            }
        },
        "unit_ontology": {
            "units": {
                "cny": {"canonical": "CNY", "aliases": ["CNY"], "scale": 1},
            }
        },
    }
    claim = ClaimCandidate(
        claim_id="claim-mixed-alias",
        exact="TTM 市销率 in 2024 was 123 CNY.",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "TTM 市销率",
            "period": "2024 FY",
            "value": "123",
            "unit": "CNY",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 34},
        semantic_text="TTM 市销率 in 2024 was 123 CNY.",
        insertion_offset=34,
    )
    evidence = {
        "kind": "structured-data",
        "metric": "price_to_sales_ttm",
        "period": "2024 FY",
        "value": 123,
        "unit": "CNY",
    }

    assert verify_evidence_support(claim, evidence, semantics=semantics).status == "supported"


def test_structured_unit_accepts_policy_currency_symbol_alias() -> None:
    semantics = {
        "unit_ontology": {
            "units": {
                "usd": {
                    "canonical": "USD",
                    "aliases": ["美元", "USD", "$", "US$"],
                    "scale": 1,
                }
            }
        }
    }
    claim = ClaimCandidate(
        claim_id="claim-symbol-unit",
        exact="Policy amount in 2024 was 2 $.",
        segment_index=0,
        kind="structured-fact",
        citation_required=True,
        attached_citation_ids=(),
        normalized={
            "metric": "policy_unit_usd",
            "period": "2024 FY",
            "value": "2",
            "unit": "$",
        },
        location={"kind": "fixture", "blockIndex": 0, "start": 0, "end": 30},
        semantic_text="Policy amount in 2024 was 2 $.",
        insertion_offset=30,
    )
    evidence = {
        "kind": "structured-data",
        "metric": "policy_unit_usd",
        "period": "2024 FY",
        "value": 2,
        "unit": "USD",
    }

    assert verify_evidence_support(claim, evidence, semantics=semantics).status == "supported"


def test_calculation_metric_accepts_table_header_base_metric_dependency() -> None:
    claims = extract_claims(
        (
            "| 期间 | 营业收入 |\n"
            "| --- | --- |\n"
            "| 同比增速 | +15.71% [来源](citation://cit_growth) |"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )
    claim = next(item for item in claims if "+15.71%" in item.exact)
    evidence = {
        "kind": "calculation",
        "expression": "((current - prior) / prior) * 100",
        "inputs": [
            {"name": "current", "citationId": "cit_current", "value": "170899152276"},
            {"name": "prior", "citationId": "cit_prior", "value": "147693604994"},
        ],
        "result": 15.71,
        "unit": "%",
        "metric": "营业收入同比增速",
        "period": "2024 FY vs 2023 FY",
        "calculatedAt": "2026-08-01T08:00:00Z",
    }

    assert claim.normalized["metric"] == "operating_revenue"
    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_structural_metric_heading_scopes_generic_amount_table_column() -> None:
    claims = extract_claims(
        (
            "**贵州茅台营业收入（单位：亿元，人民币）**\n\n"
            "| 期间 | 金额（亿元） |\n"
            "| --- | --- |\n"
            "| 2024 年度（FY2024） | 1,708.99 [来源](citation://cit_revenue) |"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )
    claim = next(item for item in claims if "1,708.99" in item.exact)
    evidence = {
        "kind": "structured-data",
        "datasetId": "financials",
        "toolName": "income_statement",
        "recordKey": "600519|2024 FY",
        "entityId": "600519",
        "field": "operating_revenue",
        "metric": "operating_revenue",
        "value": 170_899_152_276,
        "unit": "CNY",
        "period": "2024 annual",
        "capturedAt": "2026-08-02T08:00:00Z",
    }

    assert claim.normalized["metric"] == "operating_revenue"
    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_calculation_normalizes_the_result_instead_of_the_first_input_amount() -> None:
    claim = extract_claims(
        "营业收入同比增长 = (1,708.99亿元 - 1,476.94亿元) / 1,476.94亿元 = 15.71%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "calculation",
        "expression": "((current - prior) / prior) * 100",
        "inputs": [],
        "result": 15.71,
        "unit": "%",
        "metric": "营业收入同比增长",
        "calculatedAt": "2026-08-01T08:00:00Z",
    }

    assert claim.normalized["value"] == "15.71"
    assert claim.normalized["unitBase"] == "percent"
    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_carries_metric_into_period_value_shorthand() -> None:
    claims = extract_claims(
        (
            "2024 年营业收入为 1,708.99 亿元 "
            "[来源](citation://cit_current)，"
            "2023 年为 1,476.94 亿元 [来源](citation://cit_prior)。"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.normalized.get("metric") for claim in claims] == [
        "operating_revenue",
        "operating_revenue",
    ]
    assert [claim.normalized.get("period") for claim in claims] == [
        "2024 FY",
        "2023 FY",
    ]


def test_extracts_dates_and_non_numeric_facts_but_not_reasoning() -> None:
    claims = extract_claims(
        "The company was founded in 1999. Alice is the CEO. This may improve execution.",
        mode="required-on-evidence",
    )

    by_exact = {claim.exact: claim for claim in claims}
    assert by_exact["The company was founded in 1999."].kind == "date-fact"
    assert by_exact["The company was founded in 1999."].citation_required is True
    assert by_exact["Alice is the CEO."].kind == "document-claim"
    assert by_exact["Alice is the CEO."].citation_required is True
    assert by_exact["This may improve execution."].kind == "reasoning"
    assert by_exact["This may improve execution."].citation_required is False


def test_strict_domain_allows_explicit_empty_search_result_without_citation() -> None:
    not_found = extract_claims(
        "未找到符合条件的相关资料。",
        mode="strict-domain",
    )
    english_not_found = extract_claims(
        "No matching documents were found.",
        mode="strict-domain",
    )
    mixed = extract_claims(
        "未找到相关资料，但公司成立于 1999 年。",
        mode="strict-domain",
    )

    assert not_found == []
    assert len(english_not_found) == 1
    assert english_not_found[0].citation_required is False
    assert len(mixed) == 1
    assert mixed[0].kind == "date-fact"
    assert mixed[0].citation_required is True


def test_strict_domain_does_not_flag_section_titles_or_user_facing_limitations() -> None:
    claims = extract_claims(
        "**2. 营业总收入 与 营业收入**\n\n"
        "贵州茅台 2024 年度三项查询结果如下：\n\n"
        "部分结果的来源定位不完整，相关内容暂时无法核验。\n\n"
        "如需进一步确认，建议查阅贵州茅台 2024 年年度报告原文。",
        mode="strict-domain",
    )

    assert [claim.exact for claim in claims] == [
        "贵州茅台 2024 年度三项查询结果如下：",
        "如需进一步确认，建议查阅贵州茅台 2024 年年度报告原文。",
    ]
    assert [claim.kind for claim in claims] == ["presentation", "reasoning"]
    assert all(claim.citation_required is False for claim in claims)


def test_current_source_coverage_note_is_excluded_from_claim_audit() -> None:
    claims = extract_claims(
        "当前来源未包含具体数字。",
        mode="strict-domain",
    )

    assert claims == []


def test_period_and_unit_banner_with_vertical_separator_is_presentation() -> None:
    claims = extract_claims(
        "报告期：2025 财年（截至 2025 年 12 月 31 日）｜单位：人民币千元",
        mode="strict-domain",
    )

    assert len(claims) == 1
    assert claims[0].kind == "presentation"
    assert claims[0].citation_required is False


def test_current_material_unavailable_is_a_local_limitation_not_a_source_claim() -> None:
    claims = extract_claims(
        "扣非净利润：当前资料未披露。",
        mode="strict-domain",
    )

    assert len(claims) == 1
    assert claims[0].kind == "limitation"
    assert claims[0].citation_required is False


def test_report_non_disclosure_remains_a_source_claim() -> None:
    claims = extract_claims(
        "年度报告未披露扣非净利润。",
        mode="strict-domain",
    )

    assert len(claims) == 1
    assert claims[0].citation_required is True


def test_bold_only_subsection_labels_are_context_not_claims() -> None:
    claims = extract_claims(
        "**AI 算力需求**\n\n"
        "需求持续增长。\n\n"
        "**资本开支 / 基础设施扩张**\n"
        "单季度新增近 1 GW。\n\n"
        "**供需约束 / 效率提升**\n\n"
        "**营业收入增长 26%**",
        mode="strict-domain",
    )

    assert [claim.exact for claim in claims] == [
        "需求持续增长。",
        "单季度新增近 1 GW。",
        "营业收入增长 26%",
    ]


def test_strict_domain_does_not_flag_assistant_progress_narration() -> None:
    claims = extract_claims(
        "数据已充分。现在整合所有来源，撰写综合报告。",
        mode="strict-domain",
    )

    assert claims == []


def test_strict_domain_does_not_flag_english_progress_narration() -> None:
    claims = extract_claims(
        "All data retrieved. Now compiling the requested report.",
        mode="strict-domain",
    )

    assert claims
    assert all(claim.kind == "presentation" for claim in claims)
    assert all(claim.citation_required is False for claim in claims)


def test_strict_domain_does_not_flag_runtime_retrieval_progress_messages() -> None:
    messages = (
        "Now let me run targeted searches on all four transcripts for the CFO sections.\n\n"
        "I now need the financial sections with capex numbers, "
        "so I will fetch the later chunks.\n\n"
        "I now have all the evidence needed from all four transcripts. Let me build the table.\n\n"
        "非IFRS调整净利润已取到。现在查商誉余额。\n\n"
        "找到了年度财报。现在获取年报原文中的具体表述。"
    )

    claims = extract_claims(messages, mode="strict-domain")

    assert claims
    assert all(claim.kind == "presentation" for claim in claims)
    assert all(claim.citation_required is False for claim in claims)


def test_strict_domain_does_not_flag_chinese_search_progress_with_future_action() -> None:
    claims = extract_claims(
        "搜索到了两份报告，现在继续读取下一份。",
        mode="strict-domain",
    )

    assert claims == []


def test_strict_domain_does_not_flag_named_tool_progress_narration() -> None:
    claims = extract_claims(
        "现在用 kb_search 定位原文段落。",
        mode="strict-domain",
    )

    assert claims
    assert all(claim.kind == "presentation" for claim in claims)
    assert all(claim.citation_required is False for claim in claims)


def test_strict_domain_does_not_flag_search_coverage_status_as_domain_fact() -> None:
    claims = extract_claims(
        (
            "搜索结果只覆盖到 Q4 FY2026（MSFT）和 Q2 FY2026（Alphabet），"
            "都属于同一个季度期间。需要找到各自上一季度的电话会。"
        ),
        mode="strict-domain",
    )

    assert claims
    assert all(claim.kind == "presentation" for claim in claims)
    assert all(claim.citation_required is False for claim in claims)


def test_strict_domain_does_not_flag_retrieved_corpus_count_as_domain_fact() -> None:
    claims = extract_claims(
        "已获得全部4份电话会摘要。现在精确提取各主题的原文证据。",
        mode="strict-domain",
    )

    assert claims
    assert all(claim.kind == "presentation" for claim in claims)
    assert all(claim.citation_required is False for claim in claims)


def test_search_result_prefix_does_not_hide_a_factual_numeric_claim() -> None:
    claims = extract_claims(
        "搜索显示，公司收入增长 20%。",
        mode="strict-domain",
    )

    assert len(claims) == 1
    assert claims[0].kind == "financial-fact"
    assert claims[0].citation_required is True


def test_imperative_source_read_preamble_is_process_text_not_an_external_claim() -> None:
    claims = extract_claims(
        "用年度财报文件（doc_id `1246596091500171264`）获取两个指标的原文数据。",
        mode="strict-domain",
    )

    assert len(claims) == 1
    assert claims[0].kind == "presentation"
    assert claims[0].citation_required is False


def test_strict_domain_does_not_treat_coverage_review_conclusions_as_claims() -> None:
    claims = extract_claims(
        (
            "The response is complete and accurate. Nothing was omitted.\n\n"
            "The response fully satisfies the original request: "
            "all requested fields are present.\n\n"
            "No omissions or corrections are needed."
        ),
        mode="strict-domain",
    )

    assert claims
    assert all(claim.kind == "presentation" for claim in claims)
    assert all(claim.citation_required is False for claim in claims)


def test_exact_metric_calculation_supports_a_plain_display_value() -> None:
    semantics = {
        "metric_ontology": {
            "metrics": {
                "price_to_sales_ttm": {
                    "aliases": ["TTM PS"],
                    "fields": ["price_to_sales_ttm"],
                }
            }
        },
        "unit_ontology": {
            "units": {"multiple": {"canonical": "multiple", "aliases": ["x", "倍"], "scale": 1}}
        },
    }
    claim = extract_claims(
        "TTM PS was 19.47x.",
        mode="strict-domain",
        semantics=semantics,
    )[0]
    support = verify_evidence_support(
        claim,
        {
            "evidence": {
                "kind": "calculation",
                "metric": "price_to_sales_ttm",
                "expression": "market_cap / ttm_revenue",
                "inputs": [
                    {"name": "market_cap", "citationId": "cit_cap", "value": 169.7},
                    {"name": "ttm_revenue", "citationId": "cit_revenue", "value": 8.717},
                ],
                "result": 19.47,
                "unit": "x",
                "period": "2026-08-03",
            }
        },
        semantics=semantics,
    )

    assert support.status == "supported"


def test_presentation_preface_is_separate_from_the_following_factual_claim() -> None:
    claims = extract_claims(
        "本报告按产品品类横向呈现，三家公司在该品类下均是供给方。",
        mode="strict-domain",
    )

    assert [claim.exact for claim in claims] == ["三家公司在该品类下均是供给方。"]
    assert claims[0].kind == "document-claim"
    assert claims[0].citation_required is True


def test_document_original_text_label_is_presentation_not_a_date_claim() -> None:
    claims = extract_claims(
        "合并利润表（2024年1—12月）原文：",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert len(claims) == 1
    assert claims[0].kind == "presentation"
    assert claims[0].citation_required is False


def test_plain_period_context_label_is_not_an_independent_date_claim() -> None:
    claims = extract_claims(
        "贵州茅台 2024 年全年：\n\n"
        "- **营业收入**：1,708.99 亿元 [1](citation://cit_revenue)\n"
        "- **归母净利润**：862.28 亿元 [2](citation://cit_profit)",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.exact for claim in claims] == [
        "贵州茅台 2024 年全年：",
        "营业收入：1,708.99 亿元",
        "归母净利润：862.28 亿元",
    ]
    assert claims[0].kind == "presentation"
    assert claims[0].citation_required is False
    assert all(claim.citation_required for claim in claims[1:])


def test_period_and_unit_banner_is_presentation_context() -> None:
    claims = extract_claims(
        "**期间：2024 年度（FY 2024），单位：亿元人民币**\n\n"
        "- 营业收入：1,708.99 亿元 [1](citation://cit_revenue)",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.exact for claim in claims] == [
        "期间：2024 年度（FY 2024），单位：亿元人民币",
        "营业收入：1,708.99 亿元",
    ]
    assert claims[0].kind == "presentation"
    assert claims[0].citation_required is False
    assert claims[1].citation_required is True


def test_appended_period_metadata_does_not_replace_primary_structured_metric() -> None:
    claims = extract_claims(
        (
            "**170,899,152,276**，单位：**人民币元（CNY）**，"
            "期间：**2024 财年（截至 2024-12-31）** "
            "[1](citation://cit_revenue)"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert len(claims) == 1
    assert "metric" not in claims[0].normalized


def test_company_period_title_with_explicit_date_range_is_presentation_context() -> None:
    claims = extract_claims(
        "**贵州茅台 2024 年度（2024-01-01 至 2024-12-31）**\n\n"
        "| 指标 | 金额（亿元，人民币） |\n"
        "|---|---:|\n"
        "| 营业收入 | 1,708.99 [1](citation://cit_revenue) |",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.exact for claim in claims] == [
        "贵州茅台 2024 年度（2024-01-01 至 2024-12-31）",
        "营业收入 — 金额（亿元，人民币）: 1,708.99",
    ]


def test_company_period_title_with_year_inherited_date_range_is_presentation_context() -> None:
    claims = extract_claims(
        "**海吉亚医疗（HK:06078）2025年度财报（报告期：2025年1月1日—12月31日）**\n\n"
        "- **商誉**（截至2025年12月31日账面值）：人民币 **34.41亿元** "
        "[1](citation://cit_goodwill)",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.exact for claim in claims] == [
        "海吉亚医疗（HK:06078）2025年度财报（报告期：2025年1月1日—12月31日）",
        "商誉（截至2025年12月31日账面值）：人民币 34.41亿元",
    ]
    assert claims[0].kind == "presentation"
    assert claims[0].citation_required is False
    assert claims[1].citation_required is True
    assert claims[0].kind == "presentation"
    assert claims[0].citation_required is False
    assert claims[1].citation_required is True


def test_independently_cited_comma_clauses_are_atomic_claims() -> None:
    claims = extract_claims(
        "2024 年度审计意见为无保留意见 [a](citation://cit_a)，"
        "报告期为全年 [b](citation://cit_b)，"
        "申报日期为 2025-04-03 [c](citation://cit_c)。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.attached_citation_ids for claim in claims] == [
        ("cit_a",),
        ("cit_b",),
        ("cit_c",),
    ]
    assert [claim.normalized.get("period") for claim in claims] == [
        "2024 FY",
        "2024 FY",
        "2024 FY",
    ]


def test_each_comma_clause_keeps_its_explicit_period_and_infers_derived_metric() -> None:
    claims = extract_claims(
        "2024 年营业收入为 1,708.99 亿元 [a](citation://cit_a)，"
        "2023 年营业收入为 1,476.94 亿元 [b](citation://cit_b)，"
        "2024 年同比增速为 15.71% [c](citation://cit_c)。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.normalized.get("period") for claim in claims] == [
        "2024 FY",
        "2023 FY",
        "2024 FY",
    ]
    assert [claim.normalized.get("metric") for claim in claims] == [
        "operating_revenue",
        "operating_revenue",
        "revenue_growth",
    ]


def test_citation_clause_split_ignores_internal_commas_before_the_binding() -> None:
    claims = extract_claims(
        "2024 年营业收入为 170,899,152,276.34 元，较上年同期增长 15.71% "
        "[表格](citation://cit_table)，利润表对此亦予以披露 "
        "[分析](citation://cit_analysis)。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.attached_citation_ids for claim in claims] == [
        ("cit_table",),
    ]
    assert claims[0].exact == ("2024 年营业收入为 170,899,152,276.34 元，较上年同期增长 15.71%，")


def test_text_evidence_supports_scaled_financial_values_and_percentages() -> None:
    claim = extract_claims(
        "2024 年营业收入为 1,708.99 亿元，同比增长 15.71%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": ("营业收入 | 170,899,000,000 元 | 2024 年；本期比上年同期增长 15.71%。"),
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_list_and_table_claims_have_stable_structural_locations() -> None:
    answer = """# Summary

- Revenue was 120 USD.
- Revenue was 120 USD.

| Metric | 2024 |
|---|---:|
| Revenue | 120 USD |
| Profit | 20 USD |

```python
year = 2024
```
"""

    first = extract_claims(answer, mode="strict-domain")
    second = extract_claims(answer, mode="strict-domain")

    assert [claim.claim_id for claim in first] == [claim.claim_id for claim in second]
    repeated = [claim for claim in first if claim.exact == "Revenue was 120 USD."]
    assert len(repeated) == 2
    assert repeated[0].claim_id != repeated[1].claim_id
    assert repeated[0].location["kind"] == "list-item"
    assert repeated[0].location["itemIndex"] == 0
    assert repeated[1].location["itemIndex"] == 1
    table_claims = [claim for claim in first if claim.location["kind"] == "table-cell"]
    assert [claim.exact for claim in table_claims] == [
        "Revenue — 2024: 120 USD",
        "Profit — 2024: 20 USD",
    ]
    assert {
        key: table_claims[0].location[key]
        for key in ("kind", "blockIndex", "rowIndex", "columnIndex")
    } == {
        "kind": "table-cell",
        "blockIndex": 1,
        "rowIndex": 0,
        "columnIndex": 1,
    }
    assert table_claims[0].location["sourceEnd"] > table_claims[0].location["sourceStart"]
    assert all("year = 2024" not in claim.exact for claim in first)


def test_strict_domain_audits_text_only_factual_table_cells() -> None:
    claims = extract_claims(
        "| 公司 | 核心产品系列 |\n|---|---|\n| 三星电子 | DRAM、NAND Flash、HBM、企业级 SSD |",
        mode="strict-domain",
    )

    assert [claim.exact for claim in claims] == [
        "三星电子 — 核心产品系列: DRAM、NAND Flash、HBM、企业级 SSD"
    ]
    assert claims[0].citation_required is True


def test_table_context_numbers_do_not_replace_the_cell_value() -> None:
    claims = extract_claims(
        "## 过去半年（2026年2月—8月）涨价情况\n\n"
        "| 产品类别 | 涨价幅度（2026年2月至今） | 关键数据来源 |\n"
        "|---|---|---|\n"
        "| HBM3合约价（Q1 2026） | +50%–55%（东北证券研报数据） | "
        "[报告](citation://cit_hbm) |",
        mode="strict-domain",
    )

    table_claims = [claim for claim in claims if claim.location["kind"] == "table-cell"]
    assert len(table_claims) == 1
    assert table_claims[0].exact == (
        "HBM3合约价（Q1 2026） — 涨价幅度（2026年2月至今）: +50%–55%（东北证券研报数据）"
    )
    assert table_claims[0].normalized["value"] == "50"
    assert table_claims[0].normalized["unit"] == "%"
    assert table_claims[0].attached_citation_ids == ("cit_hbm",)


def test_key_data_source_table_column_is_not_a_second_business_claim() -> None:
    claims = extract_claims(
        "| 产品 | 涨幅 | 关键数据来源 |\n"
        "|---|---:|---|\n"
        "| 企业级SSD | +80% | TrendForce [来源](citation://cit_ssd) |",
        mode="strict-domain",
    )

    assert [claim.exact for claim in claims] == ["企业级SSD — 涨幅: +80%"]
    assert claims[0].attached_citation_ids == ("cit_ssd",)


def test_two_quarterly_sources_jointly_cover_two_reported_ranges() -> None:
    claim = extract_claims(
        "| 产品 | Q2 合约价环比涨幅 |\n"
        "|---|---:|\n"
        "| NAND Flash（含企业级 SSD） | +55%～+60%；另据一说 +70%～+75% |",
        mode="strict-domain",
    )[0]
    evidence_items = [
        {
            "source": {"sourceId": "half-year", "title": "2026 上半年存储周期"},
            "evidence": {
                "kind": "text",
                "quote": "二季度 NAND Flash 合约价环比上涨55%至60%。",
            },
        },
        {
            "source": {"sourceId": "may-news", "title": "存储芯片短缺持续"},
            "evidence": {
                "kind": "text",
                "quote": "2026年二季度NAND闪存合约价格将环比上涨70%-75%。",
            },
        },
    ]

    assert text_components_cover_claim(claim, evidence_items) is True


def test_two_period_years_are_not_treated_as_business_amounts() -> None:
    claim = extract_claims(
        "以 64GB RDIMM 服务器内存为例，合约价从 2025 年 Q4 的 "
        "450 美元飙升至 2026 年 Q1 的 900 美元以上。",
        mode="strict-domain",
    )[0]
    citation = {
        "source": {"title": "内存价格持续大涨"},
        "evidence": {
            "kind": "text",
            "quote": (
                "以服务器级内存为例，64GB RDIMM合约价从2025年第四季度的"
                "450美元，飙升至2026年第一季度的900美元以上。"
            ),
        },
    }

    assert verify_evidence_support(claim, citation).status == "supported"


def test_one_citation_in_value_cell_does_not_leak_across_the_same_table_row() -> None:
    claims = extract_claims(
        "| 项目 | 2024年度（元） | 同比增幅 |\n"
        "|---|---:|---:|\n"
        "| 营业收入 | 170,899,152,276.34 | "
        "+15.71% [年报](citation://cit_row) |",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert len(claims) == 2
    assert claims[0].attached_citation_ids == ()
    assert claims[1].attached_citation_ids == ("cit_row",)


def test_cited_table_period_cell_remains_nonfactual_context() -> None:
    claims = extract_claims(
        "| 字段 | 数值 | 单位 | 报告期 |\n"
        "|---|---:|---|---|\n"
        "| 归属于上市公司股东的净利润 | 862.28 | 亿元 | "
        "2024年度（FY2024） [source](evidence://ev_parent_profit) |",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    period_claim = next(claim for claim in claims if "报告期:" in claim.exact)

    assert period_claim.kind == "presentation"
    assert period_claim.citation_required is False
    assert period_claim.attached_evidence_handles == ("ev_parent_profit",)


def test_parenthetical_reporting_context_stays_with_the_numeric_claim() -> None:
    claims = extract_claims(
        "营业收入（2024年年报，报告期：2024-12-31）："
        "**1,708.99 亿元** [source](evidence://ev_revenue)",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert len(claims) == 1
    assert claims[0].exact == (
        "营业收入（2024年年报，报告期：2024-12-31）：1,708.99 亿元"
    )
    assert claims[0].kind == "financial-fact"
    assert claims[0].normalized["metric"] == "operating_revenue"
    assert claims[0].normalized["period"] == "2024 FY"
    assert claims[0].attached_evidence_handles == ("ev_revenue",)


def test_ranked_table_uses_named_entity_column_for_value_claims() -> None:
    answer = (
        "| 排名 | 模型 | 本周用量 | 周环比（vs 7.13—7.19） |\n"
        "|---:|---|---:|---:|\n"
        "| 1 | MiMo-V2.5 | 10.5T | +12% |"
    )
    claims = extract_claims(
        answer,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    usage_claim = next(claim for claim in claims if claim.location.get("columnIndex") == 2)
    growth_claim = next(claim for claim in claims if claim.location.get("columnIndex") == 3)
    assert usage_claim.exact == "MiMo-V2.5 — 本周用量: 10.5T"
    assert growth_claim.exact == "MiMo-V2.5 — 周环比（vs 7.13—7.19）: +12%"

    evidence = {
        "source": {"title": "OpenRouter weekly ranking"},
        "evidence": {
            "kind": "text",
            "quote": "MiMo-V2.5以10.5T Token居首，份额环比增长12.2%。",
        },
    }
    assert (
        verify_evidence_support(usage_claim, evidence, semantics=_FINANCE_SEMANTICS).status
        == "supported"
    )
    assert (
        verify_evidence_support(growth_claim, evidence, semantics=_FINANCE_SEMANTICS).status
        == "supported"
    )
    bound = bind_claims_to_evidence(
        answer,
        [
            {
                "evidenceHandle": "ev_openrouter_mimo",
                **evidence,
            }
        ],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )
    assert bound.auto_bound_claim_handles[growth_claim.claim_id] == ("ev_openrouter_mimo",)
    assert "+12% [source](evidence://ev_openrouter_mimo)" in bound.text


def test_table_visual_placeholders_are_not_factual_claims() -> None:
    claims = extract_claims(
        "| 指标 | Q1 FY26 | Q2 FY26 | Q3 FY26 |\n"
        "|---|---:|---:|---:|\n"
        "| 单季新增容量 | — | ~1 GW | N/A |",
        mode="strict-domain",
    )

    assert [claim.exact for claim in claims] == ["单季新增容量 — Q2 FY26: ~1 GW"]
    assert claims[0].normalized["period"] == "2026 Q2"


def test_fiscal_shorthand_rejects_cross_quarter_text_binding() -> None:
    claim = extract_claims(
        "| 指标 | Q4 FY26 |\n|---|---:|\n| 单季新增容量 | ~1 GW |",
        mode="strict-domain",
    )[0]
    q2_evidence = {
        "source": {"title": "Microsoft FY2026 Q2 earnings call transcript"},
        "evidence": {
            "kind": "text",
            "quote": "We added nearly 1 GW of total capacity this quarter alone.",
        },
    }

    assert claim.normalized["period"] == "2026 Q4"
    assert verify_evidence_support(claim, q2_evidence).status == "contradicted"


def test_fiscal_shorthand_after_table_prefix_matches_same_period_document() -> None:
    claim = extract_claims(
        (
            "| 维度 | Microsoft FY26 Q3 |\n"
            "|---|---|\n"
            "| 资产类型拆分 | This quarter, roughly 2/3 of CapEx was for "
            "short-lived assets. |"
        ),
        mode="strict-domain",
    )[0]
    evidence = {
        "source": {"title": "Microsoft(MSFT) - 2026 Q3 - Earnings Call Transcript"},
        "evidence": {
            "kind": "text",
            "quote": "This quarter, roughly 2/3 of CapEx was for short-lived assets.",
        },
    }

    assert claim.normalized["period"] == "2026 Q3"
    assert verify_evidence_support(claim, evidence).status == "supported"


def test_unique_fiscal_year_scopes_abbreviated_quarter_table_columns() -> None:
    claims = extract_claims(
        "## FY2026 产能变化\n\n"
        "| 指标 | Q2（1月） | Q4（7月） |\n"
        "|---|---:|---:|\n"
        "| 单季新增容量 | 1 GW | 1 GW |",
        mode="strict-domain",
    )

    assert [claim.normalized["period"] for claim in claims] == [
        "2026 Q2",
        "2026 Q4",
    ]


def test_explicit_table_cell_period_overrides_multi_period_intro_context() -> None:
    claims = extract_claims(
        (
            "## 覆盖范围：Microsoft FY2026 Q3、FY2026 Q4；Alphabet 2026 Q1、2026 Q2\n\n"
            "| 维度 | Microsoft FY2026 Q4（2026-07-29） | Alphabet 2026 Q2（2026-07-22） |\n"
            "|---|---|---|\n"
            "| 当季资本开支 | $41 billion | $44.9 billion |"
        ),
        mode="strict-domain",
    )

    table_claims = [claim for claim in claims if claim.location["kind"] == "table-cell"]
    assert [claim.normalized["period"] for claim in table_claims] == [
        "2026 Q4",
        "2026 Q2",
    ]


def test_claims_inherit_period_context_from_markdown_headings() -> None:
    answer = """# 贵州茅台

## 2024 年

- 营业收入为 1,708.99 亿元。

| 指标 | 数值 |
|---|---:|
| 净利润 | 862.28 亿元 |
"""

    claims = extract_claims(answer, mode="strict-domain", semantics=_FINANCE_SEMANTICS)

    assert [claim.exact for claim in claims] == [
        "营业收入为 1,708.99 亿元。",
        "净利润 — 数值: 862.28 亿元",
    ]
    assert [claim.normalized["period"] for claim in claims] == ["2024 FY", "2024 FY"]
    revenue = _structured_record(
        "ev_heading_revenue_12345678",
        field="operating_revenue",
        value=170_899_000_000,
    )
    revenue["evidence"]["unit"] = "CNY"
    revenue["evidence"]["entityName"] = "贵州茅台"

    assert (
        match_available_evidence(
            claims[0],
            [revenue],
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "exact"
    )

    without_heading = extract_claims(
        "营业收入为 1,708.99 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    assert (
        verify_evidence_support(
            without_heading,
            revenue["evidence"],
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )


def test_structured_cny_value_matches_markdown_table_header_unit() -> None:
    answer = "| 公司 | 营业收入（亿元） |\n|---|---:|\n| 贵州茅台 | 1,708.99 |"
    claim = extract_claims(
        answer,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    revenue = _structured_record(
        "ev_table_revenue_12345678",
        field="operating_revenue",
        value=170_899_152_276,
    )
    revenue["evidence"].update({"unit": "CNY", "entityName": "贵州茅台"})

    assert claim.exact == "贵州茅台 — 营业收入（亿元）: 1,708.99"
    assert (
        verify_evidence_support(
            claim,
            revenue,
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )
    assert structured_value_present(
        170_899_152_276,
        "CNY",
        answer,
        field="operating_revenue",
        semantics=_FINANCE_SEMANTICS,
    )


def test_structured_value_inherits_unit_from_sibling_period_cell() -> None:
    answer = (
        "| 公司 | 报告期 | 营业收入 | 净利润 |\n"
        "|---|---|---:|---:|\n"
        "| 闪迪 | FY2026 Q3；单位：百万美元 | 5,950 | 3,615 |"
    )
    claims = extract_claims(
        answer,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )
    revenue = next(claim for claim in claims if "营业收入" in claim.exact)

    assert revenue.normalized["unit"] == "百万美元"
    assert revenue.normalized["unitBase"] == "USD"
    assert (
        verify_evidence_support(
            revenue,
            {
                "source": {"title": "Company income statement · SNDK"},
                "evidence": {
                    "kind": "structured-data",
                    "entityId": "SNDK",
                    "entityName": "闪迪",
                    "field": "revenue",
                    "metric": "operating_revenue",
                    "value": 5_950_000_000,
                    "unit": "USD",
                    "period": "2026 Q3",
                },
            },
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )
    assert structured_value_present(
        5_950_000_000,
        "USD",
        answer,
        field="revenue",
        metric="operating_revenue",
        semantics=_FINANCE_SEMANTICS,
    )


def test_calculation_inherits_entity_and_period_from_presentation_preface() -> None:
    claims = extract_claims(
        "贵州茅台 2024 年全年财务数据如下：\n\n"
        "营业收入同比增长 = (170.9 / 147.7 - 1) * 100 = 15.71% "
        "[计算](citation://cit_calculation)",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )
    calculation = claims[-1]
    evidence = {
        "kind": "calculation",
        "expression": "(current / prior - 1) * 100",
        "inputs": [],
        "result": 15.71,
        "unit": "%",
        "entityName": "贵州茅台",
        "metric": "营业收入同比增长",
        "period": "2024 FY",
        "calculatedAt": "2026-08-01T08:00:00Z",
    }

    assert "贵州茅台 2024 年全年" in calculation.semantic_text
    assert (
        verify_evidence_support(calculation, evidence, semantics=_FINANCE_SEMANTICS).status
        == "supported"
    )


def test_text_evidence_treats_q1_and_q1_ytd_as_the_same_reporting_period() -> None:
    claim = extract_claims(
        "青岛啤酒 2026 年一季度营业收入为 10,285,128,726 元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    citation = {
        "source": {
            "title": "青岛啤酒 2026 Q1 财务报表",
            "period": "2026 Q1",
        },
        "evidence": {
            "kind": "text",
            "quote": "营业收入 10,285,128,726 元",
            "period": "2026 Q1",
        },
    }

    assert claim.normalized["period"] == "2026 Q1 YTD"
    assert (
        verify_evidence_support(claim, citation, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_calculation_formula_with_all_inputs_is_direct_support() -> None:
    claim = extract_claims(
        "营业收入同比增长 = (10,285,128,726 / 10,445,537,525 - 1) × 100 = -1.54%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "calculation",
        "expression": "(current / prior - 1) * 100",
        "inputs": [
            {"name": "current", "value": 10_285_128_726, "unit": "CNY"},
            {"name": "prior", "value": 10_445_537_525, "unit": "CNY"},
        ],
        "result": -1.54,
        "unit": "%",
        "entityName": "青岛啤酒",
        "metric": "营业收入同比增长",
        "period": "2026 Q1",
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_latex_formula_is_safely_recomputed_against_calculation_evidence() -> None:
    evidence = {
        "kind": "calculation",
        "expression": "((current - prior) / prior) * 100",
        "inputs": [
            {"name": "current", "value": 170_899_152_276, "unit": "CNY"},
            {"name": "prior", "value": 147_693_604_994, "unit": "CNY"},
        ],
        "result": "15.71",
        "unit": "%",
        "rounding": "2dp",
    }
    formula = (
        r"$$\frac{170{,}899{,}152{,}276 - 147{,}693{,}604{,}994}"
        r"{147{,}693{,}604{,}994} \times 100\%$$"
    )

    assert calculation_formula_matches_evidence(formula, evidence) is True
    assert (
        calculation_formula_matches_evidence(
            formula.replace(" - ", " + "),
            evidence,
        )
        is False
    )


def test_markdown_sources_heading_stops_claim_audit() -> None:
    claims = extract_claims(
        "Revenue was 120 USD.\n\n## Sources\n\n- Publisher is Example Corp.",
        mode="strict-domain",
    )

    assert [claim.exact for claim in claims] == ["Revenue was 120 USD."]


def test_short_english_change_statements_are_claims() -> None:
    claims = extract_claims("- Revenue grew.\n- Profit fell.", mode="strict-domain")

    assert [claim.exact for claim in claims] == ["Revenue grew.", "Profit fell."]
    assert all(claim.citation_required for claim in claims)


def test_claim_extraction_is_bounded_and_reports_truncation() -> None:
    answer = "\n".join(
        f"- Company {index} reported revenue of {index + 1} USD."
        for index in range(MAX_CLAIMS_PER_ANSWER + 5)
    )

    claims, truncated = extract_claims_with_status(answer, mode="strict-domain")

    assert len(claims) == MAX_CLAIMS_PER_ANSWER
    assert truncated is True


def test_matcher_auto_binds_only_one_semantically_exact_candidate() -> None:
    claim = extract_claims(
        "Gross margin was 23.5% in 2024.",
        mode="strict-domain",
    )[0]
    exact = _structured_record(
        "ev_margin_12345678",
        field="gross_margin",
        value=23.5,
    )
    wrong_field = _structured_record(
        "ev_tax_12345678",
        field="tax_rate",
        value=23.5,
    )

    result = match_available_evidence(claim, [exact, wrong_field])

    assert result.status == "exact"
    assert result.handles == ("ev_margin_12345678",)


def test_auto_bind_does_not_guess_for_explicitly_attributed_claim() -> None:
    answer = (
        "微软管理层披露 Azure 增长率通常包含“AI services contributed approximately 16 points”。"
    )
    unrelated = {
        "evidenceHandle": "ev_iren_12345678",
        "source": {
            "sourceId": "iren-q1",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Iris Energy (IREN) - Earnings Call Transcript",
        },
        "evidence": {
            "kind": "text",
            "quote": "AI services contributed approximately 16 points.",
        },
    }

    result = auto_bind_unique_claims(
        answer,
        [unrelated],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text == answer
    assert result.claim_handles == {}


def test_auto_bind_accepts_attribution_when_named_speaker_is_in_exact_chunk() -> None:
    answer = "FY2026 Q2，Satya Nadella 表示公司本季度单季新增近 1 GW 总容量。"
    evidence = {
        "evidenceHandle": "ev_msft_q2_capacity_12345678",
        "source": {
            "sourceId": "msft-2026-q2",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Microsoft(MSFT) - 2026 Q2 - Earnings Call Transcript",
        },
        "evidence": {
            "kind": "text",
            "prefix": "Satya Nadella",
            "quote": "All up, we added nearly 1 GW of total capacity this quarter alone.",
        },
    }

    result = auto_bind_unique_claims(
        answer,
        [evidence],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text.count("evidence://ev_msft_q2_capacity_12345678") == 1
    assert len(result.claim_handles) == 1


def test_paragraph_terminal_handles_bound_only_supported_preceding_claims() -> None:
    revenue_a = {
        "evidenceHandle": "ev_company_a_revenue_12345678",
        "source": {
            "sourceId": "company-a-report",
            "providerId": "docs",
            "sourceType": "document",
            "title": "甲公司 2025 年报",
        },
        "evidence": {
            "kind": "text",
            "quote": "甲公司2025年营业收入为100亿元。",
        },
    }
    revenue_b = {
        "evidenceHandle": "ev_company_b_revenue_12345678",
        "source": {
            "sourceId": "company-b-report",
            "providerId": "docs",
            "sourceType": "document",
            "title": "乙公司 2025 年报",
        },
        "evidence": {
            "kind": "text",
            "quote": "乙公司2025年营业收入为100亿元。",
        },
    }
    answer = (
        "甲公司2025年营业收入为100亿元。业务保持稳定 "
        "[source](evidence://ev_company_a_revenue_12345678)。\n\n"
        "乙公司2025年营业收入为100亿元。业务仍在调整 "
        "[source](evidence://ev_company_b_revenue_12345678)。"
    )

    result = bind_claims_to_evidence(
        answer,
        [revenue_a, revenue_b],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    first, second = result.text.split("\n\n")
    assert first.count("evidence://ev_company_a_revenue_12345678") == 2
    assert "evidence://ev_company_b_revenue_12345678" not in first
    assert second.count("evidence://ev_company_b_revenue_12345678") == 2
    assert "evidence://ev_company_a_revenue_12345678" not in second


def test_numbered_result_section_handles_are_candidates_without_sibling_spill() -> None:
    evidence_a = {
        "evidenceHandle": "ev_company_a_product_12345678",
        "source": {
            "sourceId": "company-a-report",
            "providerId": "docs",
            "sourceType": "document",
            "title": "甲公司 AI 产品报告",
        },
        "evidence": {
            "kind": "text",
            "quote": "甲公司AI办公产品已经规模落地，企业客户续约率提升20%。",
        },
    }
    evidence_b = {
        "evidenceHandle": "ev_company_b_product_12345678",
        "source": {
            "sourceId": "company-b-report",
            "providerId": "docs",
            "sourceType": "document",
            "title": "乙公司 AI 产品报告",
        },
        "evidence": {
            "kind": "text",
            "quote": "乙公司AI安全产品已经规模落地，企业客户续约率提升20%。",
        },
    }
    answer = (
        "### 1. 甲公司\n\n"
        "甲公司AI办公产品已经规模落地 "
        "[source](evidence://ev_company_a_product_12345678)。\n\n"
        "企业客户续约率提升20%。\n\n"
        "### 2. 乙公司\n\n"
        "乙公司AI安全产品已经规模落地 "
        "[source](evidence://ev_company_b_product_12345678)。\n\n"
        "企业客户续约率提升20%。"
    )

    result = bind_claims_to_evidence(
        answer,
        [evidence_a, evidence_b],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    first, second = result.text.split("### 2. 乙公司", 1)
    assert first.count("evidence://ev_company_a_product_12345678") == 2
    assert "evidence://ev_company_b_product_12345678" not in first
    assert second.count("evidence://ev_company_b_product_12345678") == 2
    assert "evidence://ev_company_a_product_12345678" not in second


def test_equivalent_recap_claim_reuses_verified_period_binding() -> None:
    handle = "ev_msft_q3_throughput_12345678"
    answer = (
        "### FY2026 Q3\n\n"
        f"Fairwater 提前六周投产，推理吞吐量提升40% "
        f"[source](evidence://{handle})。\n\n"
        "| 维度 | Q3 |\n"
        "|---|---|\n"
        "| 核心优化指标 | 推理吞吐量（+40%） |"
    )
    evidence = {
        "evidenceHandle": handle,
        "source": {
            "sourceId": "msft-2026-q3",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Microsoft FY2026 Q3 earnings call",
        },
        "evidence": {
            "kind": "text",
            "quote": "Fairwater 提前六周投产，推理吞吐量提升40%。",
        },
    }

    result = propagate_equivalent_claim_bindings(
        answer,
        [evidence],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text.count(f"evidence://{handle}") == 2
    assert len(result.claim_handles) == 1


def test_equivalent_text_recap_reuses_verified_period_binding() -> None:
    handle = "ev_msft_q2_diffusion_12345678"
    answer = (
        "### FY2026 Q2\n\n"
        f"微软仍处于 AI 扩散的早期阶段，TAM 将大幅扩展 "
        f"[source](evidence://{handle})。\n\n"
        "| 维度 | Q2 |\n"
        "|---|---|\n"
        "| AI 算力需求定性 | AI 扩散早期阶段，TAM 大幅扩展 |"
    )
    evidence = {
        "evidenceHandle": handle,
        "source": {
            "sourceId": "msft-2026-q2",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Microsoft FY2026 Q2 earnings call",
        },
        "evidence": {
            "kind": "text",
            "quote": "微软仍处于 AI 扩散的早期阶段，TAM 将大幅扩展。",
        },
    }

    result = propagate_equivalent_claim_bindings(
        answer,
        [evidence],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text.count(f"evidence://{handle}") == 2
    assert len(result.claim_handles) == 1


def test_equivalent_recap_never_crosses_reporting_periods() -> None:
    handle = "ev_msft_q3_growth_12345678"
    answer = (
        "### FY2026 Q3\n\n"
        f"推理吞吐量提升40% [source](evidence://{handle})。\n\n"
        "### FY2026 Q4\n\n"
        "推理吞吐量提升40%。"
    )
    evidence = {
        "evidenceHandle": handle,
        "source": {
            "sourceId": "msft-2026-q3",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Microsoft FY2026 Q3 earnings call",
        },
        "evidence": {"kind": "text", "quote": "推理吞吐量提升40%。"},
    }

    result = propagate_equivalent_claim_bindings(
        answer,
        [evidence],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text.count(f"evidence://{handle}") == 1
    assert result.claim_handles == {}


def test_equivalent_recap_never_crosses_canonical_metrics() -> None:
    handle = "ev_revenue_growth_12345678"
    answer = (
        f"### 2024 FY\n\n营业收入同比增长40% [source](evidence://{handle})。\n\n净利润同比增长40%。"
    )
    evidence = {
        "evidenceHandle": handle,
        "source": {"sourceId": "annual-report", "sourceType": "document"},
        "evidence": {"kind": "text", "quote": "营业收入同比增长40%。"},
    }

    result = propagate_equivalent_claim_bindings(
        answer,
        [evidence],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text.count(f"evidence://{handle}") == 1
    assert result.claim_handles == {}


def test_period_scoped_si_quantity_matches_spelled_out_source_unit() -> None:
    semantics = {
        "unit_ontology": {
            "units": {
                "gigawatt": {
                    "canonical": "watt",
                    "aliases": ["GW", "gigawatt", "gigawatts", "吉瓦"],
                    "scale": 1_000_000_000,
                }
            }
        }
    }
    answer = "FY2026 Q4 单季新增容量为 1 GW。"
    evidence = {
        "evidenceHandle": "ev_msft_q4_capacity_12345678",
        "source": {
            "sourceId": "msft-2026-q4",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Microsoft(MSFT) - 2026 Q4 - Earnings Call Transcript",
        },
        "evidence": {
            "kind": "text",
            "quote": "All up, we added another gigawatt of capacity this quarter.",
        },
    }

    claim = extract_claims(answer, mode="strict-domain", semantics=semantics)[0]
    assert verify_evidence_support(claim, evidence, semantics=semantics).status == "supported"

    result = auto_bind_unique_claims(
        answer,
        [evidence],
        mode="strict-domain",
        semantics=semantics,
    )
    assert result.text.count("evidence://ev_msft_q4_capacity_12345678") == 1


def test_same_period_multi_amount_vector_bridges_localized_text() -> None:
    handle = "ev_msft_q4_results_12345678"
    answer = (
        "### FY2026 Q4\n\n"
        "全财年营收突破3310亿美元，同比增长18%；微软云收入达到2140亿美元，同比增长27%。"
    )
    evidence = {
        "evidenceHandle": handle,
        "source": {
            "sourceId": "msft-2026-q4",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Microsoft FY2026 Q4 earnings call",
        },
        "evidence": {
            "kind": "text",
            "quote": (
                "Annual revenue surpassed $331 billion, up 18%. "
                "Microsoft Cloud surpassed $214 billion, up 27%."
            ),
        },
    }

    result = auto_bind_unique_claims(
        answer,
        [evidence],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text.count(f"evidence://{handle}") == 2
    assert len(result.claim_handles) == 2


def test_multi_amount_vector_never_crosses_reporting_periods() -> None:
    handle = "ev_msft_q3_results_12345678"
    answer = "### FY2026 Q4\n\n全财年营收突破3310亿美元，同比增长18%。"
    evidence = {
        "evidenceHandle": handle,
        "source": {
            "sourceId": "msft-2026-q3",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Microsoft FY2026 Q3 earnings call",
        },
        "evidence": {
            "kind": "text",
            "quote": "Annual revenue surpassed $331 billion, up 18%.",
        },
    }

    result = auto_bind_unique_claims(
        answer,
        [evidence],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text == answer
    assert result.claim_handles == {}


def test_one_same_period_currency_value_is_not_a_source_fingerprint() -> None:
    handle = "ev_msft_q4_revenue_12345678"
    answer = "### FY2026 Q4\n\n全财年营收突破3310亿美元。"
    evidence = {
        "evidenceHandle": handle,
        "source": {
            "sourceId": "msft-2026-q4",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Microsoft FY2026 Q4 earnings call",
        },
        "evidence": {
            "kind": "text",
            "quote": "Annual revenue surpassed $331 billion.",
        },
    }

    result = auto_bind_unique_claims(
        answer,
        [evidence],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text == answer
    assert result.claim_handles == {}


def test_outflow_metric_can_display_authoritative_negative_value_as_magnitude() -> None:
    answer = "FY2026 Q1 资本支出为 193.94 亿美元。"
    evidence = {
        "evidenceHandle": "ev_msft_q1_capex_12345678",
        "source": {
            "sourceId": "msft-cashflow-q1",
            "providerId": "valuz-stock",
            "sourceType": "dataset",
            "title": "Company cash flow statement · MSFT",
        },
        "evidence": {
            "kind": "structured-data",
            "datasetId": "cashflow",
            "toolName": "company_cashflow_statement",
            "recordKey": "MSFT|2026 Q1",
            "entityId": "MSFT",
            "field": "cash_at_end_of_period.capital_expenditure",
            "metric": "capital_expenditure",
            "value": -19_394_000_000,
            "unit": "USD",
            "period": "2026 Q1",
        },
    }

    claim = extract_claims(
        answer,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    assert (
        verify_evidence_support(
            claim,
            evidence,
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )


def test_auto_bind_negative_disclosure_with_unique_quoted_anchor() -> None:
    answer = (
        'AI 服务贡献百分点：电话会原文中，Amy Hood 提到"Azure AI services '
        'revenue was generally…"后未披露具体贡献百分点。'
    )
    evidence = {
        "evidenceHandle": "ev_msft_ai_12345678",
        "source": {
            "sourceId": "msft-2026-q1",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Microsoft FY2026 Q1 Earnings Call Transcript",
        },
        "evidence": {
            "kind": "text",
            "prefix": "Results were ahead of expectations.",
            "quote": (
                "Azure AI services revenue was generally in line with expectations, "
                "while demand continued to exceed available supply."
            ),
            "suffix": "We continue to invest in capacity.",
        },
    }

    result = auto_bind_unique_claims(
        answer,
        [evidence],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text.count("evidence://ev_msft_ai_12345678") == 1
    claim = next(iter(extract_claims(answer, mode="strict-domain", semantics=_FINANCE_SEMANTICS)))
    assert result.claim_handles == {claim.claim_id: "ev_msft_ai_12345678"}


def test_negative_disclosure_does_not_bind_document_coverage_marker() -> None:
    answer = "AI 服务贡献百分点：原文未披露具体数字。"
    coverage = {
        "evidenceHandle": "ev_doc_coverage_12345678",
        "source": {
            "sourceId": "msft-2026-q1",
            "documentId": "msft-2026-q1",
            "providerId": "valuz-search",
            "sourceType": "document",
            "title": "Microsoft FY2026 Q1 Earnings Call Transcript",
        },
        "evidence": {
            "kind": "structured-data",
            "datasetId": "document:msft-2026-q1",
            "toolName": "document_fetch",
            "field": "document_coverage_complete",
            "metric": "document_coverage_complete",
            "value": True,
            "basis": "full-document",
        },
    }

    result = auto_bind_unique_claims(
        answer,
        [coverage],
        mode="required-on-evidence",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text == answer
    assert result.claim_handles == {}


def test_auto_bind_negative_disclosure_does_not_choose_between_complete_documents() -> None:
    records = []
    for index in range(2):
        records.append(
            {
                "evidenceHandle": f"ev_doc_coverage_{index}_12345678",
                "source": {},
                "evidence": {
                    "kind": "structured-data",
                    "datasetId": f"document:doc-{index}",
                    "toolName": "document_fetch",
                    "field": "document_coverage_complete",
                    "value": True,
                    "basis": "full-document",
                },
            }
        )

    result = auto_bind_unique_claims(
        "原文未披露具体数字。",
        records,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text == "原文未披露具体数字。"
    assert result.claim_handles == {}


def test_composite_auto_bind_does_not_guess_for_explicitly_attributed_claim() -> None:
    answer = (
        "微软管理层披露 Azure 增长率通常包含“AI services contributed approximately 16 points”。"
    )
    records = [
        {
            "evidenceHandle": f"ev_iren_{index}_12345678",
            "source": {
                "sourceId": f"iren-{index}",
                "providerId": "valuz-search",
                "sourceType": "document",
                "title": f"Iris Energy source {index}",
            },
            "evidence": {
                "kind": "text",
                "quote": "AI services contributed approximately 16 points.",
            },
        }
        for index in range(2)
    ]

    result = auto_bind_composite_text_claims(
        answer,
        records,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text == answer
    assert result.claim_handles == {}


def test_matcher_never_guesses_between_equally_exact_candidates() -> None:
    claim = extract_claims(
        "Gross margin was 23.5% in 2024.",
        mode="strict-domain",
    )[0]
    first = _structured_record(
        "ev_margin_first_12345678",
        field="gross_margin",
        value=23.5,
    )
    second = _structured_record(
        "ev_margin_second_12345678",
        field="gross_margin",
        value=23.5,
    )

    result = match_available_evidence(claim, [first, second])

    assert result.status == "ambiguous"
    assert result.handles == (
        "ev_margin_first_12345678",
        "ev_margin_second_12345678",
    )


def test_matcher_does_not_report_different_scopes_as_same_point_conflict() -> None:
    claim = extract_claims(
        "Gross margin was 23.5% in 2024.",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    consolidated = _structured_record(
        "ev_margin_consolidated_12345678",
        field="gross_margin",
        value=23.5,
    )
    consolidated["evidence"]["scope"] = "consolidated"
    segment = _structured_record(
        "ev_margin_segment_12345678",
        field="gross_margin",
        value=25.0,
    )
    segment["evidence"]["scope"] = "segment"

    result = match_available_evidence(
        claim,
        [consolidated, segment],
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.status == "exact"
    assert result.handles == ("ev_margin_consolidated_12345678",)


def test_finance_semantics_match_metric_period_and_scaled_unit() -> None:
    claim = extract_claims(
        "贵州茅台（600519）2024 年营业收入为 1,741.44 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    correct = _structured_record(
        "ev_revenue_12345678",
        field="operating_revenue",
        value=174_144_000_000,
    )
    correct["evidence"].update({"unit": "CNY", "entityId": "600519", "period": "2024 FY"})
    wrong_metric = _structured_record(
        "ev_profit_12345678",
        field="net_profit",
        value=174_144_000_000,
    )
    wrong_metric["evidence"].update({"unit": "CNY", "entityId": "600519", "period": "2024 FY"})
    broader_metric = _structured_record(
        "ev_total_revenue_12345678",
        field="total_revenue",
        value=174_144_000_000,
    )
    broader_metric["evidence"].update(
        {
            "metric": "total_revenue",
            "unit": "CNY",
            "entityId": "600519",
            "period": "2024 FY",
        }
    )

    result = match_available_evidence(
        claim,
        [correct, wrong_metric, broader_metric],
        semantics=_FINANCE_SEMANTICS,
    )

    assert claim.normalized == {
        "value": "1741.44",
        "unit": "亿元",
        "valueBase": "174144000000",
        "unitBase": "CNY",
        "period": "2024 FY",
        "metric": "operating_revenue",
    }
    assert result.status == "exact"
    assert result.handles == ("ev_revenue_12345678",)


def test_structured_evidence_with_a_different_ticker_is_contradicted() -> None:
    claim = extract_claims(
        "000858 在 2024 年营业收入为 1,708.99 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = _structured_record(
        "ev_wrong_entity_12345678",
        field="operating_revenue",
        value=170_899_000_000,
    )
    evidence["evidence"].update({"unit": "CNY", "entityId": "600519"})

    assert (
        verify_evidence_support(
            claim,
            evidence["evidence"],
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "contradicted"
    )


def test_plain_six_digit_value_is_not_mistaken_for_a_ticker() -> None:
    claim = extract_claims(
        "Employee count was 123456 in 2024.",
        mode="strict-domain",
    )[0]
    evidence = _structured_record(
        "ev_employee_count_12345678",
        field="employee_count",
        value=123456,
    )
    evidence["evidence"].update({"unit": "", "entityId": "600519", "entityName": "Kweichow"})

    assert verify_evidence_support(claim, evidence["evidence"]).status == "supported"


def test_finance_semantics_accept_display_rounding_and_prove_wrong_value_conflict() -> None:
    claim = extract_claims(
        "贵州茅台（600519）2024 年营业收入为 1,708.99 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    rounded = _structured_record(
        "ev_revenue_rounded_12345678",
        field="total_revenue.operating_revenue",
        value=170_899_152_276,
    )
    rounded["evidence"].update(
        {
            "metric": "operating_revenue",
            "unit": "CNY",
            "entityId": "600519",
            "period": "2024 FY",
        }
    )
    wrong = _structured_record(
        "ev_revenue_wrong_12345678",
        field="total_revenue.operating_revenue",
        value=170_798_000_000,
    )
    wrong["evidence"].update(
        {
            "metric": "operating_revenue",
            "unit": "CNY",
            "entityId": "600519",
            "period": "2024 FY",
        }
    )

    assert (
        verify_evidence_support(
            claim,
            rounded,
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )
    conflict = verify_evidence_support(
        claim,
        wrong,
        semantics=_FINANCE_SEMANTICS,
    )
    assert conflict.status == "contradicted"
    assert conflict.reason == "value-conflict"


def test_reportify_nine_month_period_matches_chinese_ytd_claim() -> None:
    claim = extract_claims(
        "贵州茅台（600519）2024 年前三季度营业收入为 1,000 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = _structured_record(
        "ev_q3_ytd_revenue_12345678",
        field="operating_revenue",
        value=100_000_000_000,
        period="2024 Q3 (9 months)",
    )
    evidence["evidence"].update({"unit": "CNY", "entityId": "600519"})

    assert claim.normalized["period"] == "2024 Q3 YTD"
    assert (
        match_available_evidence(
            claim,
            [evidence],
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "exact"
    )


def test_chunk_period_overrides_broader_document_title_period() -> None:
    claim = extract_claims(
        "2026 Q1 PC DRAM 合约价环比上涨 110%～115%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "source": {
            "title": "Memory Pricing Tracker: 2Q26 forecast - 20260402",
        },
        "evidence": {
            "kind": "text",
            "quote": (
                "PC DRAM pricing rose 110-115% qoq in 1Q26, and "
                "2Q26 pricing is forecast to increase by 40-45% qoq."
            ),
        },
    }

    assert claim.normalized["period"] == "2026 Q1"
    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_chunk_without_claim_period_still_rejects_title_period_conflict() -> None:
    claim = extract_claims(
        "2026 Q1 PC DRAM 合约价环比上涨 110%～115%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "source": {"title": "Memory Pricing Tracker: 2Q26 - 20260402"},
        "evidence": {
            "kind": "text",
            "quote": "PC DRAM pricing rose 110-115% qoq.",
        },
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status
        == "contradicted"
    )


def test_calculation_without_metric_does_not_match_empty_chinese_alias_token() -> None:
    claim = extract_claims(
        "growth was 20%.",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    calculation = {
        "kind": "calculation",
        "expression": "((current / prior) - 1) * 100",
        "inputs": [],
        "result": 20,
        "unit": "%",
        "calculatedAt": "2026-08-01T08:00:00Z",
    }

    assert (
        verify_evidence_support(claim, calculation, semantics=_FINANCE_SEMANTICS).status
        == "supported"
    )


def test_finance_semantics_split_independent_metric_clauses() -> None:
    claims = extract_claims(
        "2024 年营业收入为 1 亿元，净利润为 2 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.exact for claim in claims] == [
        "2024 年营业收入为 1 亿元，",
        "净利润为 2 亿元。",
    ]
    assert [claim.normalized["metric"] for claim in claims] == [
        "operating_revenue",
        "net_profit",
    ]


def test_text_quote_matching_ignores_pdf_line_wrap_spacing_in_chinese() -> None:
    claim = extract_claims(
        "我们认为，财务报表公允反映了贵州茅台公司2024年度的经营成果。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": "我们认为，财务报表公允反映了贵州茅台公司2024\n年度的经营成果。",
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_markdown_line_break_is_an_atomic_claim_boundary() -> None:
    claims = extract_claims(
        "直销同比增长 11.32%  \n批发代理收入 957.69 亿元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.exact for claim in claims] == [
        "直销同比增长 11.32%",
        "批发代理收入 957.69 亿元。",
    ]


def test_compact_line_label_scopes_every_claim_on_only_that_line() -> None:
    claims = extract_claims(
        (
            "直销；本期销售收入：748.43 亿元；同比增幅：+11.32%  \n"
            "批发代理；本期销售收入：957.69 亿元；同比增幅：+19.73%"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert "直销" in claims[1].semantic_text
    assert "批发代理" not in claims[1].semantic_text
    assert "批发代理" in claims[3].semantic_text
    assert "直销" not in claims[3].semantic_text
    assert (
        verify_evidence_support(
            claims[1],
            {
                "kind": "text",
                "quote": (
                    "| 销售模式 | 营业收入 | 营业收入比上年增减（%） |\n"
                    "| 直销 | 74,843,327,030.79 | 11.32 |"
                ),
            },
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )


def test_text_evidence_snippet_is_verified_as_trusted_local_context() -> None:
    claim = extract_claims(
        "直销渠道本期销售收入为 74,843,327,030.79 元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": "渠道类型 本期销售收入 上期销售收入",
        "snippet": "| 直销 | 74,843,327,030.79 | 11.32 |",
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_text_quote_matching_allows_attribution_before_exact_quote() -> None:
    claim = extract_claims(
        (
            '年报重要提示第三条原文："天健会计师事务所(特殊普通合伙)'
            '为本公司出具了标准无保留意见的审计报告。"'
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": ("三、 天健会计师事务所(特殊普通合伙)为本公司出具了标准无保留意见的审计报告。"),
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_text_numeric_support_accepts_equivalent_currency_conversions() -> None:
    claim = extract_claims(
        ("2024年度营业收入为17,089,915.23万元，即170,899,152,300元，约1,708.99亿元。"),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": "2024 年度，营业收入为人民币 17,089,915.23 万元。",
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_text_table_evidence_supports_a_claim_with_an_equivalent_display_value() -> None:
    claim = extract_claims(
        ("直销渠道：2024年本期销售收入 74,843,327,030.79 元（约 748.43 亿元），同比增长 11.32%。"),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": ("| 按销售渠道 | 金额 | 同比 |\n| 直销 | 74,843,327,030.79 | 11.32 |"),
        "prefix": "渠道类型 本期销售收入 上期销售收入 本期销售量 上期销售量",
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_text_table_base_unit_values_support_rounded_display_only_claim() -> None:
    claims = extract_claims(
        "直销渠道：营业收入 748.43亿元，同比 +11.32%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )
    evidence = {
        "kind": "text",
        "quote": (
            "| 按销售渠道 | 金额 | 同比 |\n"
            "| 直销 | 74,843,327,030.79 | 11.32 |\n"
            "| 批发代理 | 95,768,511,021.23 | 19.73 |"
        ),
    }

    assert len(claims) == 1
    assert (
        verify_evidence_support(claims[0], evidence, semantics=_FINANCE_SEMANTICS).status
        == "supported"
    )


def test_text_table_with_currency_and_percent_columns_supports_both_values() -> None:
    claim = extract_claims(
        "茅台酒收入 1,459.28亿元，同比+15.28%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": (
            "单位：元 币种：人民币\n"
            "| 分产品 | 营业收入 | 毛利率（%） | 营业收入比上年增减（%） |\n"
            "| --- | --- | --- | --- |\n"
            "| 茅台酒 | 145,928,075,955.31 | 94.06 | 15.28 |"
        ),
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_text_table_inherited_million_krw_unit_supports_scaled_display_value() -> None:
    correct_claim = extract_claims(
        "SK海力士 2026 Q1 营业收入为52.576287万亿韩元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    wrong_claim = extract_claims(
        "SK海力士 2026 Q1 营业收入为52,576,287亿韩元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": (
            "Three-month periods ended March 31, 2026 and 2025\n"
            "(In millions of Korean won, except per share data)\n"
            "Revenue 52,576,287 17,639,092\n"
            "Gross profit 41,679,414 10,997,539"
        ),
    }

    assert (
        verify_evidence_support(
            correct_claim,
            evidence,
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )
    assert (
        verify_evidence_support(
            wrong_claim,
            evidence,
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "contradicted"
    )


def test_text_table_cell_header_unit_matches_mixed_unit_filing_excerpt() -> None:
    claims = extract_claims(
        (
            "| 产品 | 2024年收入（亿元） | 同比增速 |\n"
            "| --- | --- | --- |\n"
            "| 茅台酒 | 1,459.28 | +15.28% [来源](citation://cit_product) |"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )
    evidence = {
        "kind": "text",
        "quote": (
            "单位：万元 币种：人民币\n"
            "产品档次 产量（吨） 同比（%） 销量（吨） 同比（%） "
            "销售收入 同比（%）\n"
            "茅台酒 56,271.99 -1.63 46,412.95 10.22 "
            "14,592,807.60 15.28 贵州茅台酒"
        ),
    }

    assert [claim.exact for claim in claims] == [
        "茅台酒 — 2024年收入（亿元）: 1,459.28",
        "茅台酒 — 同比增速: +15.28%",
    ]
    assert all(
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
        for claim in claims
    )


def test_text_table_cell_header_unit_matches_base_currency_filing_excerpt() -> None:
    claims = extract_claims(
        (
            "| 渠道 | 2024年收入（亿元） | 同比增速 |\n"
            "| --- | --- | --- |\n"
            "| 直销 | 748.43 | +11.32% [来源](citation://cit_channel) |"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )
    evidence = {
        "kind": "text",
        "quote": (
            "| 按销售渠道 | 营业收入 | 营业收入比上年增减（%） |\n"
            "| --- | --- | --- |\n"
            "| 直销 | 74,843,327,030.79 | 11.32 |"
        ),
    }

    assert all(
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
        for claim in claims
    )


def test_same_document_duplicate_chunks_choose_the_tightest_supported_excerpt() -> None:
    claim = extract_claims(
        "DRAM 合约价上涨 58–63%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    shared_source = {
        "providerId": "valuz-search",
        "sourceType": "document",
        "documentId": "trendforce-2026-q2",
        "title": "TrendForce DRAM pricing update",
    }
    broad = {
        "evidenceHandle": "ev_broad_chunk_12345678",
        "source": shared_source,
        "evidence": {
            "kind": "text",
            "quote": "DRAM 合约价上涨 58–63%，NAND Flash 合约价上涨 81–86%。",
        },
    }
    focused = {
        "evidenceHandle": "ev_focused_chunk_12345678",
        "source": shared_source,
        "evidence": {"kind": "text", "quote": "DRAM 合约价上涨 58–63%。"},
    }

    match = match_available_evidence(
        claim,
        [broad, focused],
        semantics=_FINANCE_SEMANTICS,
    )

    assert match.status == "exact"
    assert match.handles == ("ev_focused_chunk_12345678",)


def test_text_document_period_metadata_rejects_cross_quarter_binding() -> None:
    claim = extract_claims(
        "Microsoft 2026 Q3 revenue was 65.6 USDm.",
        mode="strict-domain",
    )[0]
    citation = {
        "source": {
            "providerId": "valuz-search",
            "sourceType": "document",
            "documentId": "msft-fy2026-q1",
            "title": "Microsoft FY2026 Q1 earnings call transcript",
        },
        "evidence": {
            "kind": "text",
            "quote": "Microsoft revenue was 65.6 USDm.",
        },
    }

    assert verify_evidence_support(claim, citation).status == "contradicted"


def test_currency_prefix_billion_matches_localized_hundred_million_usd() -> None:
    claim = extract_claims(
        "Microsoft Cloud 季度收入超过 540 亿美元，同比增长 29%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    citation = {
        "source": {
            "title": "Microsoft FY2026 Q3 earnings call transcript",
        },
        "evidence": {
            "kind": "text",
            "quote": ("Microsoft Cloud exceeded $54 billion in revenue, up 29% year-over-year."),
        },
    }

    assert (
        verify_evidence_support(
            claim,
            citation,
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )


def test_annual_report_title_q4_does_not_contradict_full_year_quote() -> None:
    claim = extract_claims(
        "2024年度营业总收入为1,741.44亿元，同比增长15.66%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    citation = {
        "source": {
            "providerId": "valuz-search",
            "sourceType": "document",
            "documentId": "600519-2024",
            "title": "贵州茅台(600519) - 2024 Q4 - 年度财报",
        },
        "evidence": {
            "kind": "text",
            "quote": ("年度内公司实现营业总收入 1,741.44 亿元，同比增长 15.66%。"),
        },
    }

    assert (
        verify_evidence_support(
            claim,
            citation,
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )


def test_publication_date_does_not_conflict_with_filing_reporting_period() -> None:
    claim = extract_claims(
        "审计报告出具日期为2025年4月1日。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    citation = {
        "source": {
            "providerId": "valuz-search",
            "sourceType": "document",
            "documentId": "600519-2024",
            "title": "贵州茅台(600519) - 2024 Q4 - 年度财报",
        },
        "evidence": {
            "kind": "text",
            "quote": "审计报告出具日期为二〇二五年四月一日。",
        },
    }

    support = verify_evidence_support(
        claim,
        citation,
        semantics=_FINANCE_SEMANTICS,
    )
    assert support.status != "contradicted"


def test_table_claims_inherit_a_dedicated_source_column_binding() -> None:
    answer = (
        "| Metric | 2024 | Source |\n"
        "|---|---:|---|\n"
        "| Revenue | 120 USD | [1](evidence://ev_revenue_table_12345678) |"
    )

    claims = extract_claims(answer, mode="strict-domain")

    assert [claim.exact for claim in claims] == ["Revenue — 2024: 120 USD"]
    assert claims[0].attached_evidence_handles == ("ev_revenue_table_12345678",)


def test_generic_market_quote_with_all_numbers_is_supported_without_metric_ontology() -> None:
    claim = extract_claims(
        (
            "据 TrendForce 数据，2026年Q2通用DRAM合约价环比Q1上涨 58–63%，"
            "NAND Flash合约价上涨 81–86%。"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": (
            "TrendForce：2026 年第二季（Q2）通用 DRAM 合约价预计较第一季（Q1）"
            "上涨 58–63%；NAND Flash 合约价预计上涨\n81–86%。"
        ),
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status == "supported"
    )


def test_generic_numeric_quote_does_not_match_unrelated_subject_with_same_values() -> None:
    claim = extract_claims(
        "DRAM 合约价上涨 58–63%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    evidence = {
        "kind": "text",
        "quote": "航空客运量同比上涨 58–63%。",
    }

    assert (
        verify_evidence_support(claim, evidence, semantics=_FINANCE_SEMANTICS).status != "supported"
    )


def test_composite_text_auto_bind_covers_cross_document_numeric_claim() -> None:
    answer = "Microsoft AI 容量增长 80%，dock-to-live 缩短 20%，Copilot 吞吐提升 4 倍。"
    records = [
        {
            "evidenceHandle": f"ev_msft_q{index}_12345678",
            "source": {
                "sourceId": f"msft-q{index}",
                "providerId": "valuz-search",
                "sourceType": "document",
                "title": f"Microsoft FY2026 Q{index} earnings call",
            },
            "evidence": {
                "kind": "text",
                "quote": quote,
            },
        }
        for index, quote in enumerate(
            (
                "Microsoft AI capacity will grow by more than 80%。",
                "Microsoft shortened dock-to-live time by 20%。",
                "Microsoft Copilot throughput improved by 4 倍。",
            ),
            start=1,
        )
    ]

    result = auto_bind_composite_text_claims(
        answer,
        records,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text.count("evidence://") == 3
    assert len(next(iter(result.claim_handles.values()))) == 3


def test_composite_text_auto_bind_keeps_two_agreeing_summary_sources() -> None:
    answer = "通用 DRAM 2026 年第一季度合约价环比上涨 90%～95%。"
    records = [
        {
            "evidenceHandle": f"ev_dram_summary_{index}_12345678",
            "source": {
                "sourceId": f"dram-source-{index}",
                "providerId": "valuz-search",
                "sourceType": "web",
                "title": title,
            },
            "evidence": {
                "kind": "text",
                "quote": quote,
            },
        }
        for index, (title, quote) in enumerate(
            (
                (
                    "TrendForce memory outlook",
                    "2026年第一季度通用 DRAM 合约价环比上涨 90%～95%。",
                ),
                (
                    "Memory market report",
                    "通用 DRAM 在 2026 Q1 的合约价环比涨幅为 90%～95%。",
                ),
                (
                    "Repeated market summary",
                    "2026 Q1 通用 DRAM 合约价格环比上涨 90%～95%。",
                ),
            ),
            start=1,
        )
    ]

    result = auto_bind_composite_text_claims(
        answer,
        records,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text.count("evidence://") == 2
    assert next(iter(result.claim_handles.values())) == (
        "ev_dram_summary_1_12345678",
        "ev_dram_summary_2_12345678",
    )


def test_composite_text_auto_bind_requires_every_claim_amount() -> None:
    answer = "Microsoft AI 容量增长 80%，dock-to-live 缩短 20%，Copilot 吞吐提升 4 倍。"
    records = [
        {
            "evidenceHandle": "ev_msft_q1_12345678",
            "source": {
                "sourceId": "msft-q1",
                "providerId": "valuz-search",
                "sourceType": "document",
            },
            "evidence": {
                "kind": "text",
                "quote": "Microsoft AI capacity will grow by more than 80%。",
            },
        },
        {
            "evidenceHandle": "ev_msft_q2_12345678",
            "source": {
                "sourceId": "msft-q2",
                "providerId": "valuz-search",
                "sourceType": "document",
            },
            "evidence": {
                "kind": "text",
                "quote": "Microsoft shortened dock-to-live time by 20%。",
            },
        },
    ]

    result = auto_bind_composite_text_claims(
        answer,
        records,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text == answer
    assert result.claim_handles == {}


def test_single_page_number_never_supports_or_auto_binds_a_numeric_claim() -> None:
    answer = "贵州茅台 2024 年营业收入为 1,741.44 亿元。"
    claim = extract_claims(
        answer,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    record = {
        "evidenceHandle": "ev_page_number_12345678",
        "source": {"sourceId": "document-1", "sourceType": "document"},
        "evidence": {"kind": "text", "quote": "2", "page": 2},
    }

    assert (
        verify_evidence_support(
            claim,
            record["evidence"],
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "not-found"
    )
    assert (
        auto_bind_unique_claims(
            answer,
            [record],
            mode="strict-domain",
            semantics=_FINANCE_SEMANTICS,
        ).text
        == answer
    )


def test_finance_clause_split_ignores_numeric_thousands_separator() -> None:
    claims = extract_claims(
        ("贵州茅台（600519）2024 年营业收入为 1,708.99 亿元，归母净利润为 862.28 亿元。"),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.exact for claim in claims] == [
        "贵州茅台（600519）2024 年营业收入为 1,708.99 亿元，",
        "归母净利润为 862.28 亿元。",
    ]
    assert [claim.normalized["metric"] for claim in claims] == [
        "operating_revenue",
        "net_profit",
    ]


def test_auto_bind_keeps_each_citation_before_its_clause_punctuation() -> None:
    revenue = _structured_record(
        "ev_revenue_clause_12345678",
        field="operating_revenue",
        value=170_899_152_276,
    )
    revenue["evidence"].update({"unit": "CNY", "period": "2024 FY"})
    profit = _structured_record(
        "ev_profit_clause_12345678",
        field="net_profit",
        value=89_334_728_026,
    )
    profit["evidence"].update({"unit": "CNY", "period": "2024 FY"})

    result = auto_bind_unique_claims(
        "2024 年营业收入为 1,708.99 亿元，净利润为 893.35 亿元。",
        [revenue, profit],
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )
    rebound = extract_claims(
        result.text,
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert result.text.count("evidence://") == 2
    assert [claim.attached_evidence_handles for claim in rebound] == [
        ("ev_revenue_clause_12345678",),
        ("ev_profit_clause_12345678",),
    ]


def test_prior_year_table_column_overrides_current_annual_report_title() -> None:
    claim = extract_claims(
        "2023年归属于上市公司股东的净利润为74,734,000,000.00元。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    citation = {
        "source": {
            "providerId": "valuz-search",
            "sourceType": "document",
            "documentId": "600519-2024",
            "title": "贵州茅台(600519) - 2024 Q4 - 年度财报",
        },
        "evidence": {
            "kind": "text",
            "quote": (
                "项目 | 2024年 | 2023年 | 增减变动幅度\n"
                "归属于上市公司股东的净利润 | 86,228,146,421.62 | "
                "74,734,000,000.00 | 15.38%"
            ),
        },
    }

    assert (
        verify_evidence_support(
            claim,
            citation,
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )


def test_reported_growth_cell_uses_metric_dependency_and_comparison_header() -> None:
    claim = extract_claims(
        "营业收入同比增长15.71%。",
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )[0]
    citation = {
        "kind": "text",
        "quote": (
            "项目 | 2024年 | 2023年 | 本期比上年同期增减(%)\n"
            "营业收入 | 170,899,152,276.34 | 147,693,604,994.14 | 15.71"
        ),
    }

    assert (
        verify_evidence_support(
            claim,
            citation,
            semantics=_FINANCE_SEMANTICS,
        ).status
        == "supported"
    )


def test_educational_definition_formula_and_hypothetical_are_not_external_claims() -> None:
    claims = extract_claims(
        (
            "ROE（Return on Equity，净资产收益率）衡量公司用股东权益创造净利润的效率。\n\n"
            "$$ROE = \\frac{净利润}{平均股东权益} \\times 100\\%$$\n\n"
            "平均股东权益 = （期初股东权益 + 期末股东权益）÷ 2。\n\n"
            "示例：某公司净利润 10 亿元，平均股东权益 50 亿元，则 ROE 为 20%。"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.kind for claim in claims] == [
        "definition",
        "reasoning",
        "reasoning",
        "reasoning",
    ]
    assert all(not claim.citation_required for claim in claims)


def test_plain_language_analogy_and_hypothetical_scope_need_no_external_source() -> None:
    claims = extract_claims(
        (
            "通俗理解：股东每投入 1 元钱，公司一年能赚回多少钱。\n\n"
            "- 股东权益：资产总额减去负债后属于股东的那部分，通常取期初与期末的平均值\n\n"
            "**举个例子**\n\n"
            "某公司股东投入了 10 亿元，当年赚了 2 亿元净利润。\n\n"
            "意思是：股东每投 100 元，这一年赚回了 20 元。"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert [claim.kind for claim in claims] == [
        "reasoning",
        "definition",
        "reasoning",
        "reasoning",
    ]
    assert all(not claim.citation_required for claim in claims)


def test_definition_heading_without_verb_is_not_an_external_claim() -> None:
    claims = extract_claims(
        (
            "**净资产收益率（ROE，Return on Equity）**\n\n"
            "衡量公司利用股东权益创造净利润的效率。\n\n"
            "$$ROE = \\frac{\\text{净利润}}{\\text{平均股东权益}} \\times 100\\%$$"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    assert claims[0].kind == "definition"
    assert all(not claim.citation_required for claim in claims)


def test_rule_table_threshold_and_decision_cells_are_reasoning_not_external_facts() -> None:
    claims = extract_claims(
        (
            "| 规则 | 阈值 | 当前状态 | 触发？ |\n"
            "|---|---:|---|---|\n"
            "| 固定止损线 | $160 | 现价 $193.78 > $160 | ✅ 未触发 |\n"
            "| 价格 vs MA120 | 需站上 | $193.78 > $168.89 | ✅ 站上 |"
        ),
        mode="strict-domain",
        semantics=_FINANCE_SEMANTICS,
    )

    by_exact = {claim.exact: claim for claim in claims}
    assert by_exact["固定止损线 — 阈值: $160"].kind == "reasoning"
    assert by_exact["固定止损线 — 触发？: ✅ 未触发"].kind == "reasoning"
    assert by_exact["价格 vs MA120 — 阈值: 需站上"].kind == "reasoning"
    assert by_exact["价格 vs MA120 — 触发？: ✅ 站上"].kind == "reasoning"
    assert all(
        not claim.citation_required
        for claim in by_exact.values()
        if "阈值:" in claim.exact or "触发？:" in claim.exact
    )
    assert by_exact["固定止损线 — 当前状态: 现价 $193.78 > $160"].citation_required
    assert by_exact["价格 vs MA120 — 当前状态: $193.78 > $168.89"].citation_required

from __future__ import annotations

from src.core.task_coverage import (
    TaskCoverageTracker,
    build_task_retrieval_plan,
    parse_task_contract,
    task_contract_prompt,
    task_coverage_improves,
)


def _finance_like_policy() -> dict:
    return {
        "revision": "effective-test-v1",
        "config": {
            "semantics": {
                "metric_ontology": {
                    "metrics": {
                        "operating_revenue": {
                            "aliases": ["营业收入", "营收", "revenue"],
                            "fields": ["operating_revenue"],
                        },
                        "gross_margin": {
                            "aliases": ["毛利率", "gross margin"],
                            "fields": ["gross_margin"],
                        },
                        "operating_margin": {
                            "aliases": ["营业利润率", "运营利润率", "operating margin"],
                            "fields": ["operating_margin"],
                        },
                        "gross_profit": {
                            "aliases": ["毛利润", "毛利", "gross profit"],
                            "fields": ["gross_profit"],
                        },
                        "operating_cash_flow": {
                            "aliases": ["经营现金流", "operating cash flow"],
                            "fields": ["operating_cash_flow"],
                        },
                        "net_profit": {
                            "aliases": ["净利润", "net profit", "net income"],
                            "fields": ["net_profit", "net_income"],
                        },
                        "audit_opinion": {
                            "aliases": ["审计意见", "audit opinion"],
                            "fields": ["audit_opinion_type"],
                            "value_aliases": {
                                "unqualified_opinion": ["无保留意见", "标准无保留意见"]
                            },
                        },
                        "total_operating_revenue": {
                            "aliases": ["营业总收入", "total operating revenue"],
                            "fields": ["total_operating_revenue"],
                        },
                        "parent_net_profit": {
                            "aliases": ["归母净利润", "attributable net profit"],
                            "fields": ["parent_net_profit"],
                        },
                        "roe": {
                            "aliases": ["ROE", "净资产收益率", "return on equity"],
                            "fields": ["roe"],
                        },
                        "reporting_period": {
                            "aliases": ["报告期", "财年", "reporting period"],
                            "fields": ["fiscal_year"],
                        },
                        "deducted_parent_net_profit": {
                            "aliases": ["扣非净利润", "扣非归母净利润"],
                            "fields": ["deducted_parent_net_profit"],
                        },
                        "goodwill": {
                            "aliases": ["商誉", "goodwill", "商誉账面价值"],
                            "fields": ["goodwill"],
                        },
                        "goodwill_impairment": {
                            "aliases": ["商誉减值", "goodwill impairment"],
                            "fields": ["goodwill_impairment"],
                        },
                        "intangible_assets": {
                            "aliases": ["无形资产", "intangible assets"],
                            "fields": ["intangible_assets"],
                        },
                    }
                },
                "calculation_dependencies": {
                    "gross_margin": ["gross_profit", "operating_revenue"],
                },
            },
            "task_coverage": {
                "contract": {
                    "dimension_ontology": {
                        "dimensions": {
                            "sales_channel": {
                                "aliases": ["渠道", "销售渠道", "channel"],
                                "members": {
                                    "direct": {"aliases": ["直销", "direct sales"]},
                                    "wholesale_agency": {
                                        "aliases": ["批发代理", "wholesale agency"]
                                    },
                                },
                            }
                        }
                    },
                    "topic_ontology": {
                        "topics": {
                            "ai_compute_demand": {
                                "aliases": [
                                    "AI 算力需求",
                                    "AI 基础设施需求",
                                    "AI demand",
                                    "infrastructure demand",
                                ]
                            },
                            "capital_expenditure": {
                                "aliases": [
                                    "资本开支",
                                    "资本支出",
                                    "capital expenditure",
                                    "capex",
                                    "capacity expansion",
                                    "gigawatt of capacity",
                                    "扩充基础设施",
                                ]
                            },
                            "supply_constraints": {
                                "aliases": [
                                    "供需约束",
                                    "供应约束",
                                    "supply constraint",
                                    "capacity constraint",
                                ]
                            },
                            "price_trend": {"aliases": ["价格趋势", "价格走势", "走势"]},
                            "industry_fundamentals": {"aliases": ["行业基本面", "基本面"]},
                            "technical_directions": {"aliases": ["技术方向", "相关方向"]},
                            "candidate_companies": {"aliases": ["候选公司", "相关公司"]},
                            "trend_analysis": {"aliases": ["趋势", "变化趋势"]},
                            "drivers": {"aliases": ["驱动", "主要驱动", "驱动因素"]},
                        }
                    },
                },
                "retrieval": {
                    "content_mappings": [
                        {
                            "id": "discovery",
                            "role": "candidate",
                            "tool_patterns": ["*earnings_search", "*conferences_search"],
                        },
                        {
                            "id": "content",
                            "role": "content",
                            "coverage_text": "result",
                            "tool_patterns": ["*kb_search"],
                        },
                    ],
                    "identity_mappings": [
                        {
                            "id": "company-identity",
                            "tool_patterns": ["*company_search"],
                            "query_fields": ["query"],
                            "result_fields": [
                                "symbol",
                                "ticker",
                                "name",
                                "full_name",
                                "chinese_name",
                                "english_name",
                                "synonyms",
                            ],
                        }
                    ],
                    "candidate_selection": [
                        "compatible-scope",
                        "inspect-beyond-first-partial-candidate",
                    ],
                    "source_constraints": ["same-entity", "same-period"],
                },
            },
        },
    }


def test_expands_multi_entity_latest_report_matrix() -> None:
    contract = parse_task_contract(
        "对比闪迪、三星电子、美光科技、SK 海力士最近一期已发布财报的"
        "营业收入、毛利率和经营现金流，只输出 Markdown 表格。",
        policy_snapshot=_finance_like_policy(),
    )

    structured = [item for item in contract.requirements if item.kind == "structured-slot"]
    assert len(structured) == 12
    assert {item.slots.get("entityName") for item in structured} == {
        "闪迪",
        "三星电子",
        "美光科技",
        "SK 海力士",
    }
    assert {item.slots["metric"] for item in structured} == {
        "operating_revenue",
        "gross_margin",
        "operating_cash_flow",
    }
    assert all(item.selectors["period"]["kind"] == "latest-published" for item in structured)
    assert contract.enforceable is True
    context = task_contract_prompt(contract)
    assert "inspect additional candidates" in context
    assert "闪迪 / latest-published / operating_revenue" in context


def test_multi_entity_per_company_row_instruction_keeps_financial_metrics() -> None:
    contract = parse_task_contract(
        "对比闪迪、三星电子、美光科技、SK 海力士最近一期完整财报的营业收入、"
        "净利润和经营现金流。严格只输出一个 Markdown 表格，每家公司一行，"
        "并注明各自报告期和单位。",
        policy_snapshot=_finance_like_policy(),
    )

    structured = [item for item in contract.requirements if item.kind == "structured-slot"]
    assert len(structured) == 12
    assert {item.slots["metric"] for item in structured} == {
        "operating_revenue",
        "net_profit",
        "operating_cash_flow",
    }
    output = next(item for item in contract.requirements if item.kind == "output-shape")
    assert output.slots["requiredColumns"] == [
        "entity",
        "period",
        "operating_revenue",
        "net_profit",
        "operating_cash_flow",
    ]
    assert "exactTableRowCount" not in output.slots


def test_explicit_table_columns_override_inferred_period_column_and_enforce_shape() -> None:
    prompt = (
        "请比较贵州茅台和五粮液 2024 年营业收入与归母净利润。严格只输出一个 "
        "Markdown 表格，恰好 2 行公司数据和 3 列：公司、营业收入、归母净利润；"
        "统一使用亿元，不要输出合计。"
    )
    contract = parse_task_contract(prompt, policy_snapshot=_finance_like_policy())
    output = next(item for item in contract.requirements if item.kind == "output-shape")

    assert output.slots["requiredColumns"] == [
        "entity",
        "operating_revenue",
        "parent_net_profit",
    ]
    assert output.slots["exactTableRowCount"] == 2
    assert output.slots["exactTableColumnCount"] == 3
    assert "period" not in output.slots["requiredColumns"]
    assert not [item for item in contract.requirements if item.kind == "calculation"]

    tracker = TaskCoverageTracker(contract, policy_snapshot=_finance_like_policy())
    valid = """| 公司 | 营业收入 | 归母净利润 |
|---|---:|---:|
| 贵州茅台 | 1,708.99亿元 | 862.28亿元 |
| 五粮液 | 891.75亿元 | 318.53亿元 |"""
    extra_period = """| 公司 | 期间 | 营业收入 | 归母净利润 |
|---|---|---:|---:|
| 贵州茅台 | 2024 | 1,708.99亿元 | 862.28亿元 |
| 五粮液 | 2024 | 891.75亿元 | 318.53亿元 |"""

    valid_output = next(
        row for row in tracker.evaluate(valid)["requirements"] if row["kind"] == "output-shape"
    )
    invalid_output = next(
        row
        for row in tracker.evaluate(extra_period)["requirements"]
        if row["kind"] == "output-shape"
    )
    assert valid_output["answerStatus"] == "fulfilled"
    assert invalid_output["answerStatus"] == "missing"
    assert invalid_output["reasonCodes"] == ["exact-table-column-count-mismatch"]


def test_unscoped_metric_explanation_does_not_create_retrieval_or_repair_contract() -> None:
    contract = parse_task_contract(
        "ROE 是什么意思？请用通俗语言解释它反映什么、常见计算方式，"
        "以及为什么不能只看一个年度。不要查询任何公司或实时数据。",
        policy_snapshot=_finance_like_policy(),
    )

    assert contract.task_type == "open-research"
    assert contract.requirements == ()
    assert contract.enforceable is False
    assert task_contract_prompt(contract) == ""


def test_general_industry_metric_comparison_is_not_parsed_as_company_entities() -> None:
    contract = parse_task_contract(
        "银行和制造业为什么不能直接用同一个 ROE 阈值比较？用通俗语言回答。",
        policy_snapshot=_finance_like_policy(),
    )

    assert contract.task_type == "open-research"
    assert contract.declared_scope.get("entities") is None
    assert contract.requirements == ()
    assert contract.enforceable is False


def test_leading_subjects_are_kept_without_absorbing_output_instructions() -> None:
    prompts = {
        "贵州茅台2024年度归属于上市公司股东的净利润是多少？同比增长多少？请引用年度报告原文。": [
            "贵州茅台"
        ],
        "特斯拉最新一季财报或电话会中，Optimus 人形机器人相关交流要点是什么？"
        "只总结文件中明确说过的内容，每条标注出处，不要推测。": ["特斯拉"],
        "青岛啤酒2026年一季报营业收入是多少？相对2025年一季报同比变化多少？"
        "请列出原始数字和计算过程。": ["青岛啤酒"],
        "检查 MRVL 最新收盘价、TTM PS 和均线状态，分别判断固定止损线、"
        "相对成本涨幅线和多头排列是否触发；逐条报告当前值、阈值、差距和状态。": ["MRVL"],
    }

    for prompt, expected in prompts.items():
        contract = parse_task_contract(prompt, policy_snapshot=_finance_like_policy())
        assert contract.declared_scope.get("entities") == expected


def test_context_subject_is_kept_without_treating_quality_dimensions_as_entities() -> None:
    contract = parse_task_contract(
        "根据微软过去四个季度的财务数据，分别检查收入质量（应收账款增速与收入增速）、"
        "盈利质量（经营现金流/净利润）、负债风险和存货风险。逐季列出输入、公式、"
        "结果和判定。",
        policy_snapshot=_finance_like_policy(),
    )

    assert contract.declared_scope.get("entities") == ["微软"]


def test_index_numbers_are_not_misread_as_year_boundaries() -> None:
    contract = parse_task_contract(
        "根据 8 月 1 日的数据，计算中证500、中证1000、中证2000、沪深300四大指数的"
        "估值情况和行业覆盖情况。",
        policy_snapshot=_finance_like_policy(),
    )

    assert contract.declared_scope.get("entities") == [
        "中证500",
        "中证1000",
        "中证2000",
        "沪深300",
    ]


def test_entity_lists_drop_classifier_suffixes_and_keep_all_named_companies() -> None:
    contract = parse_task_contract(
        "请列出美光科技、三星电子、SK海力士三家存储龙头公司的核心产品，以及过去半年的涨价幅度。",
        policy_snapshot=_finance_like_policy(),
    )

    assert contract.declared_scope.get("entities") == [
        "美光科技",
        "三星电子",
        "SK海力士",
    ]


def test_for_company_context_is_a_subject_but_screening_outputs_are_not() -> None:
    shareholder_contract = parse_task_contract(
        "为同飞股份股东交流会拟定 10 个问题，覆盖近期股价表现、经营情况、定增进度、"
        "海外客户拓展和未来业绩展望，并提供详尽数据支持。",
        policy_snapshot=_finance_like_policy(),
    )
    screening_contract = parse_task_contract(
        "对 A 股全量股票执行筛选，只输出同时满足全部条件的股票，并列出每个条件的输入和是否通过。",
        policy_snapshot=_finance_like_policy(),
    )

    assert shareholder_contract.declared_scope.get("entities") == ["同飞股份"]
    assert screening_contract.declared_scope.get("entities", []) == []


def test_tool_names_document_periods_and_classification_labels_are_not_entities() -> None:
    prompts = {
        "请使用 valuz-stock 的 income_statement 查询贵州茅台 2024 年和 2023 年"
        "营业收入，并用程序化计算 2024 年同比增速。": ["贵州茅台"],
        "总结 2026 年已经发布的中报业绩及业绩预告，提炼对投资有用的信息。": [],
        "判断微软更接近周期型、脉冲型、资产困境型、成长型、稳态现金牛还是收费站型，"
        "给出证据；先判断简化 PE/PS 方法是否适用。无法判断 AI 资本开支持续性时"
        "明确保留不确定性。": ["微软"],
        "拆解北方稀土各业务板块的收入，并计算公司过去四个季度营业收入的单季同比增速。": [
            "北方稀土"
        ],
        "研究海外大厂资本开支落地后 Token 产量会增加多少，从供需关系分析未来"
        "是否会供过于求以及何时平衡。": [],
    }

    for prompt, expected in prompts.items():
        contract = parse_task_contract(prompt, policy_snapshot=_finance_like_policy())
        assert contract.declared_scope.get("entities", []) == expected

    calculated = parse_task_contract(next(iter(prompts)), policy_snapshot=_finance_like_policy())
    assert [item for item in calculated.requirements if item.kind == "calculation"]


def test_direct_recommendation_count_is_enforceable_and_requires_visible_items() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "推荐 10 家国内 A 股 AI 应用公司，并简述各自核心产品。",
        policy_snapshot=policy,
    )

    output = next(item for item in contract.requirements if item.kind == "output-shape")
    assert output.slots["exactItemCount"] == 10
    assert contract.enforceable is True

    short_result = TaskCoverageTracker(contract, policy_snapshot=policy).evaluate(
        "数据已充分。现在整合所有来源，撰写综合报告。"
    )
    short_output = next(
        item for item in short_result["requirements"] if item["kind"] == "output-shape"
    )
    assert short_output["answerStatus"] == "missing"
    assert short_output["reasonCodes"] == ["exact-item-count-mismatch"]

    complete = "\n\n".join(
        f"### {index}. 公司 {index}\n\n核心产品：AI 应用 {index}。" for index in range(1, 11)
    )
    complete_result = TaskCoverageTracker(contract, policy_snapshot=policy).evaluate(complete)
    complete_output = next(
        item for item in complete_result["requirements"] if item["kind"] == "output-shape"
    )
    assert complete_output["answerStatus"] == "fulfilled"


def test_scoped_metric_calculation_still_creates_a_strict_contract() -> None:
    contract = parse_task_contract(
        "计算贵州茅台 2024 年 ROE，并列出公式和结果。",
        policy_snapshot=_finance_like_policy(),
    )

    assert contract.task_type == "calculation"
    assert any(item.kind == "structured-slot" for item in contract.requirements)
    assert any(item.kind == "calculation" for item in contract.requirements)
    assert contract.enforceable is True


def test_policy_dimension_members_expand_breakdown_slots_and_preserve_requested_order() -> None:
    prompt = (
        "请根据贵州茅台 2024 年报，只列出直销和批发代理两个渠道的营业收入。"
        "严格只输出一个 Markdown 表格，恰好 2 行数据和 2 列：渠道、营业收入；"
        "不要输出合计。"
    )
    policy = _finance_like_policy()
    contract = parse_task_contract(prompt, policy_snapshot=policy)
    structured = [item for item in contract.requirements if item.kind == "structured-slot"]

    assert len(structured) == 2
    assert [item.slots["dimensions"]["sales_channel"] for item in structured] == [
        "direct",
        "wholesale_agency",
    ]
    assert not [item for item in contract.requirements if item.kind == "calculation"]
    output = next(item for item in contract.requirements if item.kind == "output-shape")
    assert [item["member"] for item in output.slots["orderedDimensionMembers"]] == [
        "direct",
        "wholesale_agency",
    ]

    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    requested_order = """| 渠道 | 营业收入 |
|---|---:|
| 直销 | 748.43亿元 |
| 批发代理 | 957.69亿元 |"""
    reversed_order = """| 渠道 | 营业收入 |
|---|---:|
| 批发代理 | 957.69亿元 |
| 直销 | 748.43亿元 |"""

    assert tracker.evaluate(requested_order)["status"] == "partial"
    reversed_result = tracker.evaluate(reversed_order)
    output_row = next(
        row for row in reversed_result["requirements"] if row["kind"] == "output-shape"
    )
    assert output_row["answerStatus"] == "missing"
    assert output_row["reasonCodes"] == ["requested-table-row-order-mismatch"]

    patched, requirement_ids = tracker.patch_ordered_table_rows(
        reversed_order,
        reversed_result,
    )
    assert patched == requested_order
    assert requirement_ids == (output.requirement_id,)
    patched_output = next(
        row for row in tracker.evaluate(patched)["requirements"] if row["kind"] == "output-shape"
    )
    assert patched_output["answerStatus"] == "fulfilled"


def test_metric_clause_and_output_modifiers_are_not_parsed_as_entities() -> None:
    prompt = (
        "分析海吉亚医疗2025年财报，只列出扣非净利润和商誉金额两个数字，"
        "注明报告期和单位；不要生成图表、Dashboard或扩展分析。"
    )

    contract = parse_task_contract(prompt)

    assert contract.declared_scope["entities"] == ["海吉亚医疗"]
    assert not [item for item in contract.requirements if item.kind == "comparison"]
    assert {
        item.slots["metric"] for item in contract.requirements if item.kind == "structured-slot"
    } == {"扣非净利润", "商誉金额"}


def test_single_company_breakdown_fields_are_not_misparsed_as_entities() -> None:
    prompt = (
        "请查找贵州茅台 2024 年年报，只用表格列出营业总收入、茅台酒收入、"
        "系列酒收入、直销收入和批发代理收入，并计算各自占营业总收入的比例。"
        "注明报告期、单位和计算公式。"
    )
    contract = parse_task_contract(prompt, policy_snapshot=_finance_like_policy())

    assert contract.declared_scope["entities"] == ["贵州茅台"]
    assert not [item for item in contract.requirements if item.kind == "comparison"]
    assert [
        item.slots["metric"] for item in contract.requirements if item.kind == "structured-slot"
    ] == [
        "total_operating_revenue",
        "茅台酒收入",
        "系列酒收入",
        "直销收入",
        "批发代理收入",
    ]
    calculation = next(item for item in contract.requirements if item.kind == "calculation")
    assert calculation.slots["formulaRequired"] is True
    assert calculation.description == ("show the requested calculation result and explicit formula")
    output = next(item for item in contract.requirements if item.kind == "output-shape")
    assert "requiredColumns" not in output.slots
    assert output.slots["requiredMetadata"] == ["period", "unit"]


def test_requested_calculation_formula_is_checked_separately_from_result() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "计算贵州茅台 2024 年营业收入占比，并注明计算公式。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)

    missing_formula = next(
        row
        for row in tracker.evaluate("营业收入占比为 83.80%。")["requirements"]
        if row["kind"] == "calculation"
    )
    with_formula = next(
        row
        for row in tracker.evaluate(
            "营业收入占比为 83.80%。计算公式：子项收入 ÷ 营业总收入 × 100%。"
        )["requirements"]
        if row["kind"] == "calculation"
    )
    with_table_formula = next(
        row
        for row in tracker.evaluate(
            "| 项目 | 占比 | 计算公式 |\n|---|---:|---|\n| 营业收入 | 83.80% | 1459.28 ÷ 1741.44 |"
        )["requirements"]
        if row["kind"] == "calculation"
    )

    assert missing_formula["answerStatus"] == "missing"
    assert missing_formula["reasonCodes"] == ["calculation-formula-missing"]
    assert with_formula["answerStatus"] == "fulfilled"
    assert with_table_formula["answerStatus"] == "fulfilled"


def test_tracker_restores_requested_formula_column_from_calculation_attempts() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "请查找贵州茅台 2024 年年报，只用表格列出营业总收入、茅台酒收入、"
        "系列酒收入，并计算各自占营业总收入的比例。注明报告期、单位和计算公式。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "citation_calculate",
        {
            "expression": "moutai / total",
            "inputs": [
                {"name": "moutai", "value": 145928075955.31},
                {"name": "total", "value": 174144069958.25},
            ],
            "unit": "%",
            "metric": "茅台酒收入占营业总收入比例",
        },
        {"result": "83.80", "unit": "%"},
    )
    answer = (
        "**报告期：2024 财年**\n\n"
        "| 项目 | 金额（亿元） | 占营业总收入比例 |\n"
        "|---|---:|---:|\n"
        "| 营业总收入 | 1,741.44 | — |\n"
        "| 茅台酒收入 | 1,459.28 | 83.80% |\n"
        "| 系列酒收入 | 246.84 | 14.17% |"
    )
    audit = tracker.evaluate(answer)

    patched, requirement_ids = tracker.patch_required_calculation_formula(answer, audit)

    assert "| 项目 | 金额（亿元） | 占营业总收入比例 | 计算公式 |" in patched
    assert "145928075955.31 ÷ 174144069958.25 × 100%" in patched
    assert "| 系列酒收入 | 246.84 | 14.17% | — |" in patched
    assert len(requirement_ids) == 1
    calculation = next(
        row for row in tracker.evaluate(patched)["requirements"] if row["kind"] == "calculation"
    )
    assert calculation["answerStatus"] == "fulfilled"


def test_exact_requested_fields_exclude_negated_substitutes_and_output_modifiers() -> None:
    prompt = (
        "请查阅海吉亚医疗 2025 年度财报，只列出扣非净利润和商誉金额两个数字，"
        "并注明报告期和单位。不要用商誉减值或无形资产替代商誉；"
        "不要生成图表、Dashboard或扩展分析。"
    )

    contract = parse_task_contract(prompt, policy_snapshot=_finance_like_policy())
    metrics = [
        item.slots["metric"] for item in contract.requirements if item.kind == "structured-slot"
    ]

    assert metrics == ["deducted_parent_net_profit", "goodwill"]
    output = next(item for item in contract.requirements if item.kind == "output-shape")
    assert output.slots["requiredMetadata"] == ["period", "unit"]


def test_tracker_restores_one_explicit_reporting_period_without_inventing_unit() -> None:
    prompt = (
        "请查阅海吉亚医疗 2025 年度财报，只列出扣非净利润和商誉金额两个数字，并注明报告期和单位。"
    )
    policy = _finance_like_policy()
    contract = parse_task_contract(prompt, policy_snapshot=policy)
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    answer = "- **扣非净利润**：当前资料未披露\n- **商誉账面值**：3,441,128千元"
    audit = tracker.evaluate(answer)

    patched, requirement_ids = tracker.patch_required_metadata(answer, audit)

    assert patched.startswith("**报告期：2025 财年**")
    assert "单位：" not in patched
    assert len(requirement_ids) == 1
    patched_output = next(
        row for row in tracker.evaluate(patched)["requirements"] if row["kind"] == "output-shape"
    )
    assert patched_output["answerStatus"] == "fulfilled"


def test_negated_metric_substitutes_do_not_expand_an_unstructured_request() -> None:
    prompt = "查询甲公司2025年商誉，不要用商誉减值或无形资产替代商誉。"

    contract = parse_task_contract(prompt, policy_snapshot=_finance_like_policy())
    metrics = [
        item.slots["metric"] for item in contract.requirements if item.kind == "structured-slot"
    ]

    assert metrics == ["goodwill"]


def test_document_scope_entity_excludes_trailing_citation_instruction() -> None:
    contract = parse_task_contract(
        "请根据贵州茅台2024年年度报告，分别列出审计意见、营业总收入和营业收入，"
        "并逐项引用对应的年度报告原文。",
        policy_snapshot=_finance_like_policy(),
    )

    assert contract.declared_scope["entities"] == ["贵州茅台"]
    assert {
        item.slots["metric"] for item in contract.requirements if item.kind == "structured-slot"
    } == {"audit_opinion", "total_operating_revenue", "operating_revenue"}
    assert all(
        item.slots.get("entityName") == "贵州茅台"
        for item in contract.requirements
        if item.kind == "structured-slot"
    )


def test_single_document_period_scope_applies_to_all_prose_slots() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "请根据贵州茅台2024年年度报告，分别列出审计意见、营业总收入和营业收入，"
        "并逐项引用对应的年度报告原文。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)

    audit = tracker.evaluate(
        "以下均引自贵州茅台2024年年度报告原文：\n\n"
        "**一、审计意见**\n\n> 标准无保留意见。\n\n"
        "**二、营业总收入**\n\n> 营业总收入 174,144,069,958.25 元。\n\n"
        "**三、营业收入**\n\n> 营业收入 170,899,152,276.34 元。"
    )

    structured = [item for item in audit["requirements"] if item["kind"] == "structured-slot"]
    assert {item["answerStatus"] for item in structured} == {"fulfilled"}


def test_policy_topic_ontology_maps_capex_to_capacity_language() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近一个季度电话会中管理层对资本开支的表述，并按季度引用。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "conferences_search",
        {"symbols": ["US:MSFT"]},
        [{"id": "msft-q4", "title": "Microsoft FY2026 Q4 Earnings Call"}],
    )
    tracker.record_tool_result(
        "kb_search",
        {"doc_ids": ["msft-q4"], "query": "资本开支"},
        {
            "title": "Microsoft FY2026 Q4 Earnings Call",
            "chunks": ["We added another gigawatt of capacity this quarter."],
        },
    )

    audit = tracker.evaluate("## FY2026 Q4\n公司本季度新增一吉瓦算力容量，用于持续扩充基础设施。")
    topic = next(row for row in audit["requirements"] if row["kind"] == "topic")

    assert topic["retrievalStatus"] == "available"
    assert topic["modelInputStatus"] == "visible"
    assert topic["answerStatus"] == "fulfilled"


def test_published_period_modifier_keeps_entity_and_expands_topic_matrix() -> None:
    contract = parse_task_contract(
        "请对比微软最近四个已披露季度的业绩电话会中管理层对 AI 算力需求、资本开支和"
        "供应约束的表述。请按季度分节，每个季度分别覆盖三个主题，并逐项引用该季度电话会原文。",
        policy_snapshot=_finance_like_policy(),
    )

    topics = [item for item in contract.requirements if item.kind == "topic"]
    assert contract.declared_scope["entities"] == ["微软"]
    assert len(topics) == 12
    assert {item.slots["topic"] for item in topics} == {
        "ai_compute_demand",
        "capital_expenditure",
        "supply_constraints",
    }
    assert {item.slots["periodOrdinal"] for item in topics} == {0, 1, 2, 3}
    assert {item.policy_refs for item in topics} == {
        ("task_coverage.contract.topic_ontology.topics.ai_compute_demand",),
        ("task_coverage.contract.topic_ontology.topics.capital_expenditure",),
        ("task_coverage.contract.topic_ontology.topics.supply_constraints",),
    }


def test_single_entity_key_value_table_matches_only_requested_metric_rows() -> None:
    policy = _finance_like_policy()
    metrics = policy["config"]["semantics"]["metric_ontology"]["metrics"]
    metrics["deducted_parent_net_profit"] = {
        "aliases": ["扣非净利润", "扣非归母净利润"],
        "fields": ["deducted_parent_net_profit"],
    }
    metrics["goodwill"] = {
        "aliases": ["商誉", "商誉账面价值"],
        "fields": ["goodwill"],
    }
    policy["config"]["task_coverage"]["retrieval"]["content_mappings"].append(
        {
            "id": "full-document",
            "role": "content",
            "coverage_text": "result",
            "coverage_scope": "full-document",
            "tool_patterns": ["*document_raw_content*"],
        }
    )
    contract = parse_task_contract(
        "分析海吉亚医疗2025年财报，只列出扣非净利润和商誉两个数字。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "document_raw_content",
        {"query": "海吉亚医疗 2025 扣非净利润 商誉"},
        "海吉亚医疗2025年度非IFRS经调整净利润455,480千元。",
    )
    answer = """**海吉亚医疗（2025年度）**

| 项目 | 金额（人民币） |
|---|---:|
| 非IFRS经调整净利润 | 455,480千元 [1](citation://adjusted) |
| 商誉账面值 | 3,441,128千元 [2](citation://goodwill) |
"""

    citation_bundle = {
        "citations": [
            {"citationId": "adjusted", "resolutionStatus": "ready"},
            {"citationId": "goodwill", "resolutionStatus": "ready"},
        ]
    }
    audit = tracker.evaluate(answer, citation_bundle=citation_bundle)
    rows = {
        row["description"].rsplit(" / ", 1)[-1]: row
        for row in audit["requirements"]
        if row["kind"] == "structured-slot"
    }

    assert rows["goodwill"]["answerStatus"] == "fulfilled"
    assert rows["goodwill"]["reasonCodes"] == [
        "requested-key-value-table-row-present",
        "value-present",
    ]
    assert "citation:goodwill" in rows["goodwill"]["attemptIds"]
    assert rows["deducted_parent_net_profit"]["answerStatus"] == "missing"
    assert "citation:adjusted" not in rows["deducted_parent_net_profit"]["attemptIds"]
    revision_prompt = tracker.revision_prompt(audit, answer, "原始请求")
    assert "A related or adjacent metric is not the requested metric" in revision_prompt

    patched, patched_ids = tracker.patch_unavailable_table_slots(answer, audit)
    assert "非IFRS经调整净利润" not in patched
    assert "| 扣非净利润 | 当前资料未披露 |" in patched
    assert "| 商誉账面值 | 3,441,128千元 [2](citation://goodwill) |" in patched
    assert patched_ids == (
        next(
            item.requirement_id
            for item in contract.requirements
            if item.slots.get("metric") == "deducted_parent_net_profit"
        ),
    )
    assert tracker.evaluate(patched, citation_bundle=citation_bundle)["status"] == "complete"


def test_single_entity_heading_scopes_adjacent_reporting_period_line() -> None:
    policy = _finance_like_policy()
    metrics = policy["config"]["semantics"]["metric_ontology"]["metrics"]
    metrics["goodwill"] = {"aliases": ["商誉"], "fields": ["goodwill"]}
    metrics["reporting_period"] = {
        "aliases": ["报告期", "财年", "reporting period"],
        "fields": ["reporting_period"],
    }
    contract = parse_task_contract(
        "分析海吉亚医疗2025年财报，只列出商誉，注明报告期。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    answer = """**海吉亚医疗（2025年度财报）**
报告期：截至2025年12月31日止年度

| 项目 | 金额 |
|---|---:|
| 商誉 | 3,441,128千元 |
"""

    audit = tracker.evaluate(answer)
    output = next(row for row in audit["requirements"] if row["kind"] == "output-shape")

    assert output["answerStatus"] == "fulfilled"
    assert not [
        row for row in audit["requirements"] if row["description"].endswith("reporting_period")
    ]


def test_exact_bullet_list_removes_adjacent_metric_and_keeps_supported_item() -> None:
    policy = _finance_like_policy()
    metrics = policy["config"]["semantics"]["metric_ontology"]["metrics"]
    metrics["deducted_parent_net_profit"] = {
        "aliases": ["扣非净利润", "扣非归母净利润"],
        "fields": ["deducted_parent_net_profit"],
    }
    metrics["goodwill"] = {
        "aliases": ["商誉", "商誉账面价值"],
        "fields": ["goodwill"],
    }
    policy["config"]["task_coverage"]["retrieval"]["content_mappings"].append(
        {
            "id": "full-document",
            "role": "content",
            "coverage_text": "result",
            "coverage_scope": "full-document",
            "tool_patterns": ["*document_raw_content*"],
        }
    )
    contract = parse_task_contract(
        "分析海吉亚医疗2025年财报，只列出扣非净利润和商誉两个数字。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "document_raw_content",
        {"query": "海吉亚医疗 2025 扣非净利润 商誉"},
        "海吉亚医疗2025年度非IFRS经调整净利润455,480千元；商誉账面值3,441,128千元。",
    )
    answer = """**海吉亚医疗（2025年度）**

- **非IFRS经调整净利润**：455,480千元
- **商誉账面值**：3,441,128千元 [2](citation://goodwill)
"""
    bundle = {"citations": [{"citationId": "goodwill", "resolutionStatus": "ready"}]}

    audit = tracker.evaluate(answer, citation_bundle=bundle)
    rows = {
        row["description"].rsplit(" / ", 1)[-1]: row
        for row in audit["requirements"]
        if row["kind"] == "structured-slot"
    }
    assert rows["goodwill"]["answerStatus"] == "fulfilled"
    assert rows["deducted_parent_net_profit"]["answerStatus"] == "missing"

    patched, patched_ids = tracker.patch_unavailable_table_slots(answer, audit)

    assert "非IFRS经调整净利润" not in patched
    assert "- **扣非净利润**：当前资料未披露" in patched
    assert "- **商誉账面值**：3,441,128千元 [2](citation://goodwill)" in patched
    assert len(patched_ids) == 1
    assert tracker.evaluate(patched, citation_bundle=bundle)["status"] == "complete"


def test_expands_four_period_topic_matrix() -> None:
    contract = parse_task_contract(
        "总结微软最近四个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=_finance_like_policy(),
    )

    topics = [item for item in contract.requirements if item.kind == "topic"]
    assert len(topics) == 12
    assert {item.slots["periodOrdinal"] for item in topics} == {0, 1, 2, 3}
    assert {item.slots["topic"] for item in topics} == {
        "ai_compute_demand",
        "capital_expenditure",
        "supply_constraints",
    }
    assert all(item.slots["entityName"] == "微软" for item in topics)
    assert not [item for item in contract.requirements if item.kind == "structured-slot"]
    output = next(item for item in contract.requirements if item.kind == "output-shape")
    assert output.slots["periodGroupCount"] == 4


def test_retrieval_plan_groups_structured_fields_by_entity_and_period() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "对比甲公司和乙公司最近一期完整财报的营业收入、净利润和经营现金流，只输出 Markdown 表格。",
        policy_snapshot=policy,
    )

    plan = build_task_retrieval_plan(contract, policy_snapshot=policy)

    assert len(plan.steps) == 2
    assert {step.entity_ids for step in plan.steps} == {("甲公司",), ("乙公司",)}
    assert all(step.strategy == "structured-fetch" for step in plan.steps)
    assert all(
        step.requested_parts == ("operating_revenue", "net_profit", "operating_cash_flow")
        for step in plan.steps
    )
    assert all(step.periods == ("latest-complete-before-as-of",) for step in plan.steps)
    assert all(step.max_attempts == 2 for step in plan.steps)


def test_retrieval_plan_groups_topics_per_relative_period() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近四个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=policy,
    )

    plan = build_task_retrieval_plan(contract, policy_snapshot=policy)

    assert len(plan.steps) == 4
    assert all(step.entity_ids == ("微软",) for step in plan.steps)
    assert all(step.strategy == "document-discovery" for step in plan.steps)
    assert {step.periods for step in plan.steps} == {
        ("latest-published:1",),
        ("latest-published:2",),
        ("latest-published:3",),
        ("latest-published:4",),
    }
    assert all(
        step.requested_parts == ("ai_compute_demand", "capital_expenditure", "supply_constraints")
        for step in plan.steps
    )


def test_task_contract_prompt_includes_bounded_retrieval_plan() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "对比甲公司和乙公司最近一期财报的营业收入和净利润，只输出 Markdown 表格。",
        policy_snapshot=policy,
    )
    plan = build_task_retrieval_plan(contract, policy_snapshot=policy)

    prompt = task_contract_prompt(contract, retrieval_plan=plan)

    assert "Retrieval plan (host-generated and bounded)" in prompt
    assert "strategy" not in prompt
    assert prompt.count("structured-fetch") == 2
    assert "parts=operating_revenue,net_profit" in prompt


def test_single_markdown_table_contract_rejects_duplicate_tables() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "对比甲公司和乙公司营业收入，严格只输出一个 Markdown 表格。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    answer = """| 公司 | 营业收入 |
|---|---:|
| 甲公司 | 100 |
| 乙公司 | 90 |

| 公司 | 营业收入 |
|---|---:|
| 甲公司 | 100 |
| 乙公司 | 90 |
"""

    audit = tracker.evaluate(answer)
    output = next(row for row in audit["requirements"] if row["kind"] == "output-shape")

    assert output["answerStatus"] == "missing"
    assert output["reasonCodes"] == ["exact-table-count-mismatch"]


def test_retrieval_plan_progress_is_auditable_per_scope() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "对比甲公司和乙公司最近一期财报的营业收入和净利润，只输出 Markdown 表格。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "income_statement",
        {"company": "甲公司", "period": "2025 Q1"},
        "甲公司 2025 Q1 营业收入100，净利润20。",
    )
    answer = """| 公司 | 报告期 | 营业收入 | 净利润 |
|---|---|---:|---:|
| 甲公司 | 2025 Q1 | 100 | 20 |
| 乙公司 | 2025 Q1 | 90 | 18 |
"""

    audit = tracker.evaluate(answer)
    progress = audit["retrievalPlanProgress"]

    assert audit["retrievalPlan"]["plannerRevision"] == "task-retrieval-planner-v1"
    assert progress["coveredStepCount"] == 1
    assert progress["pendingStepCount"] == 1
    assert progress["exhaustedStepCount"] == 0


def test_tracker_separates_discovery_content_and_answer_coverage() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "对比甲公司和乙公司最近一期已发布财报的营业收入和经营现金流，只输出 Markdown 表格。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "earnings_search",
        {"companies": ["甲公司", "乙公司"]},
        {"documents": ["甲公司财报", "乙公司财报"]},
    )
    tracker.record_tool_result(
        "kb_search",
        {"doc": "甲公司财报", "query": "营业收入 经营现金流"},
        "甲公司营业收入 100，经营现金流 20。",
    )
    draft = """| 公司 | 报告期 | 营业收入 | 经营现金流 |
|---|---|---:|---:|
| 甲公司 | 2025 Q1 | 100 | 20 |
| 乙公司 | 2025 Q1 | 90 | — |
"""

    audit = tracker.evaluate(draft)
    by_description = {row["description"]: row for row in audit["requirements"]}
    assert (
        by_description["甲公司 / latest-published / operating_revenue"]["retrievalStatus"]
        == "available"
    )
    assert (
        by_description["乙公司 / latest-published / operating_cash_flow"]["retrievalStatus"]
        == "partial"
    )
    assert (
        by_description["乙公司 / latest-published / operating_cash_flow"]["answerStatus"]
        == "missing"
    )
    assert audit["status"] == "partial"
    assert tracker.should_request_revision(audit) is True
    revision_prompt = tracker.revision_prompt(
        audit,
        draft,
        "原始请求",
        candidate_evidence=(
            {
                "evidenceHandle": "ev_company_a_cash_flow",
                "sourceTitle": "甲公司财报",
                "kind": "text",
                "quote": "甲公司经营现金流 20。",
            },
        ),
    )
    assert "do not call any tool for answerPatchOnly" in revision_prompt
    assert "only evidence visible to this fresh revision runtime" in revision_prompt
    assert "ev_company_a_cash_flow" in revision_prompt


def test_visible_structured_gap_uses_bounded_local_cell_patch() -> None:
    policy = _finance_like_policy()
    prompt = "只用 Markdown 表格列出甲公司2025年营业收入。"
    contract = parse_task_contract(prompt, policy_snapshot=policy)
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "kb_search",
        {"doc": "甲公司2025年报", "query": "营业收入"},
        "甲公司2025年营业收入为100亿元。",
    )
    draft = """| 公司 | 报告期 | 营业收入 |
|---|---|---:|
| 甲公司 | 2025年 | — |
"""
    audit = tracker.evaluate(draft)

    assert tracker.uses_local_patch_protocol(audit) is True
    prompt_text = tracker.revision_prompt(
        audit,
        draft,
        prompt,
        candidate_evidence=(
            {
                "evidenceHandle": "ev_company_a_revenue",
                "sourceTitle": "甲公司2025年报",
                "kind": "text",
                "quote": "甲公司2025年营业收入为100亿元。",
            },
        ),
    )
    assert '"version":"task-coverage-patch-v1"' in prompt_text
    assert "Do not rewrite the answer" in prompt_text

    result = tracker.apply_local_revision_patch(
        baseline_text=draft,
        response_text=(
            '{"version":"task-coverage-patch-v1","patches":['
            '{"requirementId":"'
            + next(
                row["requirementId"]
                for row in audit["requirements"]
                if row["kind"] == "structured-slot"
            )
            + '","replacementText":"100亿元",'
            '"evidenceHandles":["ev_company_a_revenue"]}]}'
        ),
        audit=audit,
        allowed_evidence_handles=("ev_company_a_revenue",),
    )

    assert result.accepted is True
    assert result.text is not None
    assert "| 甲公司 | 2025年 | 100亿元 [source](evidence://ev_company_a_revenue) |" in (
        result.text
    )


def test_local_cell_patch_rejects_incomplete_requirement_set() -> None:
    policy = _finance_like_policy()
    prompt = "只用 Markdown 表格列出甲公司2025年营业收入和经营现金流。"
    contract = parse_task_contract(prompt, policy_snapshot=policy)
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "kb_search",
        {"doc": "甲公司2025年报", "query": "营业收入 经营现金流"},
        "甲公司2025年营业收入100亿元，经营现金流20亿元。",
    )
    draft = """| 公司 | 报告期 | 营业收入 | 经营现金流 |
|---|---|---:|---:|
| 甲公司 | 2025年 | — | — |
"""
    audit = tracker.evaluate(draft)
    requirement_id = next(
        row["requirementId"] for row in audit["requirements"] if row["kind"] == "structured-slot"
    )

    result = tracker.apply_local_revision_patch(
        baseline_text=draft,
        response_text=(
            '{"version":"task-coverage-patch-v1","patches":['
            f'{{"requirementId":"{requirement_id}","replacementText":"100亿元",'
            '"evidenceHandles":["ev_company_a"]}]}'
        ),
        audit=audit,
        allowed_evidence_handles=("ev_company_a",),
    )

    assert result.accepted is False
    assert result.code == "incomplete-patch-set"


def test_complete_looking_answer_without_turn_evidence_requests_retrieval_revision() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "只用 Markdown 表格列出甲公司2025年营业收入。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    answer = """| 公司 | 报告期 | 营业收入 |
|---|---|---:|
| 甲公司 | 2025年 | 100亿元 |
"""

    baseline = tracker.evaluate(answer)

    assert baseline["status"] == "partial"
    revenue = next(row for row in baseline["requirements"] if row["kind"] == "structured-slot")
    assert revenue["answerStatus"] == "fulfilled"
    assert revenue["modelInputStatus"] == "not-visible"
    assert tracker.should_request_revision(baseline) is True
    revision_prompt = tracker.revision_prompt(baseline, answer, "原始请求")
    assert revenue["requirementId"] in revision_prompt
    assert '"retrievalNeeded"' in revision_prompt

    tracker.record_tool_result(
        "kb_search",
        {"query": "甲公司 2025 营业收入"},
        "甲公司2025年营业收入为100亿元。",
    )
    candidate = tracker.evaluate(answer)

    assert candidate["status"] == "complete"
    assert task_coverage_improves(baseline, candidate) is True


def test_available_evidence_cannot_be_completed_by_undisclosed_placeholder() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "只用 Markdown 表格列出甲公司 2025 年营业收入。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "kb_search",
        {"query": "甲公司 2025 营业收入"},
        "甲公司 2025 年营业收入为 100 亿元。",
    )
    answer = """| 公司 | 报告期 | 营业收入 |
|---|---|---:|
| 甲公司 | 2025年 | 当前资料未披露 |
"""

    audit = tracker.evaluate(answer)
    revenue = next(row for row in audit["requirements"] if row["kind"] == "structured-slot")

    assert revenue["retrievalStatus"] == "available"
    assert revenue["modelInputStatus"] == "visible"
    assert revenue["answerStatus"] == "missing"
    assert revenue["reasonCodes"] == ["available-evidence-marked-unavailable"]
    assert revenue["remediation"] == "patch-answer"
    assert tracker.should_request_revision(audit) is True


def test_retrieval_revision_may_remove_an_unproven_baseline_value() -> None:
    baseline = {
        "requirements": [
            {
                "requirementId": "metric-1",
                "requiredness": "required",
                "retrievalStatus": "not-attempted",
                "modelInputStatus": "not-visible",
                "answerStatus": "fulfilled",
            }
        ]
    }
    candidate = {
        "requirements": [
            {
                "requirementId": "metric-1",
                "requiredness": "required",
                "retrievalStatus": "partial",
                "modelInputStatus": "visible",
                "answerStatus": "missing",
            }
        ]
    }

    assert task_coverage_improves(baseline, candidate) is True

    supported_baseline = {
        "requirements": [
            {
                "requirementId": "metric-1",
                "requiredness": "required",
                "retrievalStatus": "available",
                "modelInputStatus": "visible",
                "answerStatus": "fulfilled",
            }
        ]
    }
    assert task_coverage_improves(supported_baseline, candidate) is False


def test_period_topic_answer_requires_every_period_section() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近四个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    for quarter in range(1, 5):
        tracker.record_tool_result(
            "kb_search",
            {"doc": f"FY2025 Q{quarter}", "query": "AI 算力需求 资本开支 供需约束"},
            f"FY2025 Q{quarter}: AI 算力需求、资本开支与供应约束。",
        )
    complete = "\n\n".join(
        f"## FY2025 Q{quarter}\nAI 算力需求保持强劲；资本开支增加；供应约束仍然存在。"
        for quarter in range(1, 5)
    )
    partial = complete.rsplit("## FY2025 Q4", 1)[0]

    complete_audit = tracker.evaluate(complete)
    partial_audit = tracker.evaluate(partial)

    assert complete_audit["metrics"]["answerRequirementMissingCount"] == 0
    assert partial_audit["metrics"]["answerRequirementMissingCount"] >= 4
    assert task_coverage_improves(partial_audit, complete_audit) is True


def test_topic_unavailable_only_fulfills_its_local_period_subsection() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近四个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    answer = """## FY2026 Q1
**AI 算力需求**
AI 平台需求保持强劲。

**资本开支**
当前资料未取得具体资本开支金额。

## FY2026 Q2
AI 算力需求保持强劲；资本开支增加；供应约束仍然存在。

## FY2026 Q3
AI 算力需求保持强劲；资本开支增加；供应约束仍然存在。

## FY2026 Q4
AI 算力需求保持强劲；资本开支增加；供应约束仍然存在。
"""

    audit = tracker.evaluate(answer)
    rows = {row["description"]: row for row in audit["requirements"] if row["kind"] == "topic"}

    assert rows["微软 / relative period 1 / capital_expenditure"]["answerStatus"] == ("fulfilled")
    assert rows["微软 / relative period 1 / supply_constraints"]["answerStatus"] == ("missing")


def test_relative_topic_selector_resolves_each_discovered_period_ordinal() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近四个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "conferences_search",
        {"symbols": ["US:MSFT"], "sort_by": "date_desc"},
        {
            "documents": [
                {"id": "doc-q4", "title": "微软 Microsoft FY2026 Q4 Earnings Call"},
                {"id": "doc-q3", "title": "微软 Microsoft FY2026 Q3 Earnings Call"},
                {"id": "doc-q2", "title": "微软 Microsoft FY2026 Q2 Earnings Call"},
                {"id": "doc-q1", "title": "微软 Microsoft FY2026 Q1 Earnings Call"},
            ]
        },
    )
    answer = "\n\n".join(
        f"## FY2026 Q{quarter}\nAI 算力需求保持强劲；资本开支增加；供应约束仍然存在。"
        for quarter in (4, 3, 2, 1)
    )

    audit = tracker.evaluate(answer)
    topic_rows = [row for row in audit["requirements"] if row["kind"] == "topic"]
    periods_by_ordinal = {
        ordinal: {
            row["selectorResolution"]["period"]
            for row in topic_rows
            if f"relative period {ordinal}" in row["description"]
        }
        for ordinal in range(1, 5)
    }

    assert periods_by_ordinal == {
        1: {"2026-q4"},
        2: {"2026-q3"},
        3: {"2026-q2"},
        4: {"2026-q1"},
    }


def test_recent_quarter_selector_ignores_interleaved_annual_scope() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近四个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "conferences_search",
        {"company": "微软"},
        {
            "documents": [
                {"id": "doc-q4", "title": "Microsoft FY2026 Q4 Earnings Call"},
                {"id": "doc-q3", "title": "Microsoft FY2026 Q3 Earnings Call"},
                {"id": "doc-q2", "title": "Microsoft FY2026 Q2 Earnings Call"},
                {"id": "doc-fy", "title": "Microsoft 2025 FY Investor Event"},
                {"id": "doc-q1", "title": "Microsoft FY2026 Q1 Earnings Call"},
            ]
        },
    )

    audit = tracker.evaluate(
        "\n\n".join(
            f"## FY2026 Q{quarter}\nAI 算力需求；资本开支；供应约束。" for quarter in (4, 3, 2, 1)
        )
    )
    topic_rows = [row for row in audit["requirements"] if row["kind"] == "topic"]

    assert {
        ordinal: {
            row["selectorResolution"]["period"]
            for row in topic_rows
            if f"relative period {ordinal}" in row["description"]
        }
        for ordinal in range(1, 5)
    } == {
        1: {"2026-q4"},
        2: {"2026-q3"},
        3: {"2026-q2"},
        4: {"2026-q1"},
    }


def test_topic_answer_scope_matches_period_label_not_section_position() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近四个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "conferences_search",
        {"symbols": ["US:MSFT"], "sort_by": "date_desc"},
        {
            "documents": [
                {"id": "doc-q4", "title": "微软 Microsoft FY2026 Q4 Earnings Call"},
                {"id": "doc-q3", "title": "微软 Microsoft FY2026 Q3 Earnings Call"},
                {"id": "doc-q2", "title": "微软 Microsoft FY2026 Q2 Earnings Call"},
                {"id": "doc-q1", "title": "微软 Microsoft FY2026 Q1 Earnings Call"},
            ]
        },
    )
    # The model is free to present periods chronologically even though the
    # relative selector is latest-first.  Only Q1 deliberately omits the
    # supply topic, so the missing row must resolve to Q1 rather than ordinal 0.
    answer = """## FY2026 Q1
### AI 算力需求
AI 平台需求保持强劲。
### 资本开支
基础设施投资继续增加。

## FY2026 Q2
### AI 算力需求
AI 平台需求保持强劲。
### 资本开支
基础设施投资继续增加。
### 供需约束
供应约束仍然存在。

## FY2026 Q3
### AI 算力需求
AI 平台需求保持强劲。
### 资本开支
基础设施投资继续增加。
### 供需约束
供应约束仍然存在。

## FY2026 Q4
### AI 算力需求
AI 平台需求保持强劲。
### 资本开支
基础设施投资继续增加。
### 供需约束
供应约束仍然存在。
"""

    audit = tracker.evaluate(answer)
    supply_rows = {
        row["selectorResolution"]["period"]: row
        for row in audit["requirements"]
        if row["kind"] == "topic" and "supply_constraints" in row["description"]
    }

    assert supply_rows["2026-q1"]["answerStatus"] == "missing"
    assert supply_rows["2026-q4"]["answerStatus"] == "fulfilled"
    assert all(
        row["answerStatus"] == "fulfilled"
        for row in audit["requirements"]
        if row["kind"] == "topic" and "supply_constraints" not in row["description"]
    )


def test_unresolved_relative_period_does_not_reuse_latest_for_every_ordinal() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近四个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "conferences_search",
        {"query": "微软", "fiscal_quarter": "Q4"},
        {"documents": [{"id": "doc-q4", "title": "微软 Microsoft FY2026 Q4 Earnings Call"}]},
    )

    audit = tracker.evaluate("## FY2026 Q4\nAI 算力需求、资本开支和供应约束。")
    topic_rows = [row for row in audit["requirements"] if row["kind"] == "topic"]

    assert {
        row["selectorResolution"]["period"] for row in topic_rows if "selectorResolution" in row
    } == {"2026-q4"}
    assert sum("selectorResolution" in row for row in topic_rows) == 3


def test_answer_complete_with_unresolved_input_does_not_request_revision() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近四个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "conferences_search",
        {"symbols": ["US:MSFT"], "sort_by": "date_desc"},
        {
            "documents": [
                {"id": "doc-q4", "title": "微软 Microsoft FY2026 Q4 Earnings Call"},
                {"id": "doc-q3", "title": "微软 Microsoft FY2026 Q3 Earnings Call"},
                {"id": "doc-q2", "title": "微软 Microsoft FY2026 Q2 Earnings Call"},
                {"id": "doc-q1", "title": "微软 Microsoft FY2026 Q1 Earnings Call"},
            ]
        },
    )
    for quarter in (4, 3, 2, 1):
        tracker.record_tool_result(
            "kb_search",
            {
                "doc_ids": [f"doc-q{quarter}"],
                "query": "AI 算力需求 资本开支 供应约束",
            },
            {
                "title": f"Microsoft FY2026 Q{quarter} Earnings Call",
                "chunks": ["Semantically related management commentary."],
            },
        )
    answer = "\n\n".join(
        f"## FY2026 Q{quarter}\nAI 算力需求保持强劲；资本开支增加；供应约束仍然存在。"
        for quarter in (4, 3, 2, 1)
    )

    audit = tracker.evaluate(answer)

    assert audit["status"] == "partial"
    assert audit["metrics"]["answerRequirementMissingCount"] == 0
    assert tracker.should_request_revision(audit) is False


def test_complete_topic_answer_with_only_discovery_requests_targeted_revision() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近四个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "conferences_search",
        {"company": "微软"},
        {
            "documents": [
                {"id": "doc-q4", "title": "Microsoft FY2026 Q4 Earnings Call"},
                {"id": "doc-q3", "title": "Microsoft FY2026 Q3 Earnings Call"},
                {"id": "doc-q2", "title": "Microsoft FY2026 Q2 Earnings Call"},
                {"id": "doc-q1", "title": "Microsoft FY2026 Q1 Earnings Call"},
            ]
        },
    )
    answer = "\n\n".join(
        f"## FY2026 Q{quarter}\nAI 算力需求保持强劲；资本开支增加；供应约束仍然存在。"
        for quarter in (4, 3, 2, 1)
    )

    audit = tracker.evaluate(answer)

    assert audit["metrics"]["answerRequirementMissingCount"] == 0
    assert tracker.should_request_revision(audit) is True


def test_topic_retrieval_is_scoped_per_period_and_query_terms_are_not_evidence() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近四个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "conferences_search",
        {"symbols": ["US:MSFT"], "sort_by": "date_desc"},
        {
            "documents": [
                {"id": "doc-q4", "title": "Microsoft FY2026 Q4 Earnings Call"},
                {"id": "doc-q3", "title": "Microsoft FY2026 Q3 Earnings Call"},
                {"id": "doc-q2", "title": "Microsoft FY2026 Q2 Earnings Call"},
                {"id": "doc-q1", "title": "Microsoft FY2026 Q1 Earnings Call"},
            ]
        },
    )
    tracker.record_tool_result(
        "kb_search",
        {
            "doc_ids": ["doc-q4"],
            "query": "AI 算力需求 资本开支 供需约束",
        },
        {
            "title": "Microsoft FY2026 Q4 Earnings Call",
            "chunks": ["AI infrastructure demand remained strong."],
        },
    )

    answer = """## FY2026 Q4
AI 算力需求保持强劲；资本开支和供需约束在当前检索内容中未取得明确表述。

## FY2026 Q3
当前资料未覆盖。

## FY2026 Q2
当前资料未覆盖。

## FY2026 Q1
当前资料未覆盖。
"""
    audit = tracker.evaluate(answer)
    topics = [row for row in audit["requirements"] if row["kind"] == "topic"]
    q4_ai = next(row for row in topics if "period 1 / ai_compute_demand" in row["description"])
    q4_capex = next(row for row in topics if "period 1 / capital_expenditure" in row["description"])
    q3_ai = next(row for row in topics if "period 2 / ai_compute_demand" in row["description"])

    assert q4_ai["retrievalStatus"] == "available"
    assert q4_capex["retrievalStatus"] == "partial"
    assert q3_ai["retrievalStatus"] == "partial"


def test_generic_strict_field_contract_does_not_need_domain_ontology() -> None:
    contract = parse_task_contract("只列出扣非净利润和商誉金额两个数字。")

    structured = [item for item in contract.requirements if item.kind == "structured-slot"]
    assert [item.slots["metric"] for item in structured] == ["扣非净利润", "商誉金额"]
    assert contract.enforceable is True


def test_structured_request_does_not_prove_a_field_missing_from_result() -> None:
    policy = _finance_like_policy()
    policy["config"]["task_coverage"]["retrieval"]["content_mappings"].append(
        {
            "id": "structured",
            "role": "content",
            "coverage_text": "input-and-result",
            "tool_patterns": ["stock_*"],
        }
    )
    contract = parse_task_contract(
        "只列出甲公司最近一期财报的营业收入和经营现金流两个数字。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "stock_financial_metrics",
        {"company": "甲公司", "fields": ["营业收入", "经营现金流"]},
        {"company": "甲公司", "period": "2025 Q1", "营业收入": "100 亿元"},
    )

    audit = tracker.evaluate("甲公司营业收入：100 亿元\n甲公司经营现金流：当前资料未披露")
    rows = {row["description"]: row for row in audit["requirements"]}

    assert rows["甲公司 / latest-published / operating_revenue"]["retrievalStatus"] == "available"
    assert rows["甲公司 / latest-published / operating_cash_flow"]["retrievalStatus"] == "partial"


def test_structured_positional_values_materialize_returned_field_addresses() -> None:
    policy = _finance_like_policy()
    policy["config"]["task_coverage"]["retrieval"]["content_mappings"].append(
        {
            "id": "structured",
            "role": "content",
            "coverage_text": "input-and-result",
            "tool_patterns": ["stock_*"],
        }
    )
    contract = parse_task_contract(
        "只列出甲公司最近一期财报的营业收入和毛利率两个数字。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "stock_financial_metrics",
        {"company": "甲公司", "fields": ["营业收入", "毛利率"]},
        {"company": "甲公司", "period": "2025 Q1", "values": [100, 35.2]},
    )

    audit = tracker.evaluate("甲公司营业收入：100 亿元\n甲公司毛利率：35.2%")
    structured = [row for row in audit["requirements"] if row["kind"] == "structured-slot"]

    assert {row["retrievalStatus"] for row in structured} == {"available"}


def test_tracker_learns_entity_aliases_across_identity_lookup_chain() -> None:
    policy = _finance_like_policy()
    policy["config"]["task_coverage"]["retrieval"]["content_mappings"].append(
        {
            "id": "structured",
            "role": "content",
            "coverage_text": "input-and-result",
            "tool_patterns": ["*income_statement"],
        }
    )
    contract = parse_task_contract(
        "只列出闪迪最近一期财报的营业收入。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "company_search",
        {"query": "闪迪 SanDisk"},
        {"companies": [], "words": ["闪迪 SanDisk"]},
    )
    tracker.record_tool_result(
        "company_search",
        {"query": "SanDisk"},
        {
            "companies": [
                {
                    "symbol": "US:SNDK",
                    "ticker": "SNDK",
                    "name": "闪迪",
                    "english_name": "Sandisk Corporation",
                }
            ]
        },
    )
    tracker.record_tool_result(
        "company_income_statement",
        {"symbol": "SNDK", "period": "quarterly"},
        {
            "data": [
                {
                    "symbol": "SNDK",
                    "fiscal_year": "2026",
                    "fiscal_quarter": "Q3",
                    "revenue": 5_950_000_000,
                }
            ]
        },
    )

    audit = tracker.evaluate("闪迪营业收入：59.50 亿美元")
    row = next(item for item in audit["requirements"] if item["kind"] == "structured-slot")

    assert row["retrievalStatus"] == "available"
    assert row["modelInputStatus"] == "visible"
    aliases = tracker.entity_aliases_snapshot()["闪迪"]
    assert "SanDisk" in aliases
    assert "SNDK" in aliases


def test_tracker_accepts_policy_declared_inputs_for_derived_metric_coverage() -> None:
    policy = _finance_like_policy()
    policy["config"]["task_coverage"]["retrieval"]["content_mappings"].append(
        {
            "id": "structured",
            "role": "content",
            "coverage_text": "input-and-result",
            "tool_patterns": ["*income_statement"],
        }
    )
    contract = parse_task_contract(
        "只列出甲公司最近一期财报的毛利率。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "company_income_statement",
        {"company": "甲公司", "period": "quarterly"},
        {
            "company": "甲公司",
            "period": "2026 Q3",
            "revenue": 5_950_000_000,
            "gross_profit": 4_662_000_000,
        },
    )

    audit = tracker.evaluate("甲公司毛利率：78.4%")
    row = next(item for item in audit["requirements"] if item["kind"] == "structured-slot")

    assert row["retrievalStatus"] == "available"
    assert row["modelInputStatus"] == "visible"


def test_tracker_patches_only_unavailable_cells_in_requested_table() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "对比甲公司和乙公司最近一期财报的营业收入和经营现金流，只输出 Markdown 表格。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "kb_search",
        {"doc": "甲公司财报", "query": "营业收入 经营现金流"},
        "甲公司营业收入 100，经营现金流 20。",
    )
    tracker.record_tool_result(
        "kb_search",
        {"doc": "乙公司财报", "query": "营业收入 经营现金流"},
        "乙公司营业收入 90，文档未披露经营现金流。",
    )
    draft = """| 公司 | 报告期 | 营业收入 | 经营现金流 |
|---|---|---:|---:|
| 甲公司 | 2025 Q1 | 100 | 20 |
| 乙公司 | 2025 Q1 | 90 |
"""

    audit = tracker.evaluate(draft)
    patched, requirement_ids = tracker.patch_unavailable_table_slots(draft, audit)
    patched_audit = tracker.evaluate(patched)

    assert "| 乙公司 | 2025 Q1 | 90 | 当前资料未披露 |" in patched
    assert len(requirement_ids) == 1
    assert patched_audit["metrics"]["answerRequirementMissingCount"] == 0
    assert tracker.should_request_revision(patched_audit) is False


def test_partial_retrieval_keeps_scoped_content_attempt_for_deterministic_patch() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "只用 Markdown 表格列出乙公司最近一期财报的营业收入和经营现金流。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "earnings_search",
        {"company": "乙公司"},
        {"documents": [{"id": "doc-b", "title": "乙公司 2025 Q1 财报"}]},
    )
    tracker.record_tool_result(
        "kb_search",
        {"doc": "doc-b", "company": "乙公司", "query": "经营现金流"},
        {"title": "乙公司 2025 Q1 财报", "chunks": []},
    )
    draft = """| 公司 | 报告期 | 营业收入 | 经营现金流 |
|---|---|---:|---:|
| 乙公司 | 2025 Q1 | 90 |
"""

    audit = tracker.evaluate(draft)
    cash_flow = next(
        row for row in audit["requirements"] if row["description"].endswith("operating_cash_flow")
    )
    patched, requirement_ids = tracker.patch_unavailable_table_slots(draft, audit)

    assert cash_flow["retrievalStatus"] == "partial"
    assert len(cash_flow["attemptIds"]) == 2
    assert len(requirement_ids) == 1
    assert "| 乙公司 | 2025 Q1 | 90 | 当前资料未披露 |" in patched


def test_verified_citation_binding_proves_model_input_visibility() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "只列出闪迪最近一期财报的营业收入。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    answer = "闪迪营业收入：59.50 亿美元 [1](citation://cit_revenue)"
    bundle = {
        "quality": {
            "claims": [
                {
                    "exact": "闪迪 — 营业收入: 59.50 亿美元",
                    "status": "auto-bound",
                    "citationIds": ["cit_revenue"],
                    "issueCodes": [],
                }
            ]
        }
    }

    audit = tracker.evaluate(answer, citation_bundle=bundle)
    row = next(item for item in audit["requirements"] if item["kind"] == "structured-slot")

    assert row["retrievalStatus"] == "available"
    assert row["modelInputStatus"] == "visible"
    assert row["attemptIds"] == ["citation:cit_revenue"]


def test_ready_citation_in_requested_table_cell_proves_visibility_without_audit() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "对比闪迪和美光最近一期财报的营业收入和经营现金流，只输出 Markdown 表格。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    answer = (
        "| 公司 | 期间 | 营业收入 | 经营现金流 |\n"
        "|---|---|---:|---:|\n"
        "| 闪迪 | FY2026 Q3 | 59.50 亿美元 [1](citation://cit_sndk_revenue) | "
        "30.38 亿美元 [2](citation://cit_sndk_ocf) |\n"
        "| 美光 | FY2026 Q3 | 414.56 亿美元 [3](citation://cit_mu_revenue) | "
        "253.88 亿美元 [4](citation://cit_mu_ocf) |\n"
    )
    bundle = {
        "citations": [
            {"citationId": citation_id, "resolutionStatus": "ready"}
            for citation_id in (
                "cit_sndk_revenue",
                "cit_sndk_ocf",
                "cit_mu_revenue",
                "cit_mu_ocf",
            )
        ]
    }

    audit = tracker.evaluate(answer, citation_bundle=bundle)
    rows = [item for item in audit["requirements"] if item["kind"] == "structured-slot"]

    assert audit["status"] == "complete"
    assert all(row["retrievalStatus"] == "available" for row in rows)
    assert all(row["modelInputStatus"] == "visible" for row in rows)
    assert {attempt for row in rows for attempt in row["attemptIds"]} == {
        "citation:cit_sndk_revenue",
        "citation:cit_sndk_ocf",
        "citation:cit_mu_revenue",
        "citation:cit_mu_ocf",
    }


def test_ready_period_local_topic_citations_prove_visibility() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "总结微软最近一个季度电话会中管理层对 AI 算力需求、资本开支和"
        "供需约束的表述，并按季度引用。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "conferences_search",
        {"company": "微软"},
        {"documents": [{"id": "doc-q4", "title": "Microsoft FY2026 Q4 Earnings Call"}]},
    )
    answer = """## FY2026 Q4
### AI 算力需求
需求保持强劲 [1](citation://cit_ai)。
### 资本开支
基础设施投资增加 [2](citation://cit_capex)。
### 供应约束
供应约束仍然存在 [3](citation://cit_supply)。
"""
    bundle = {
        "citations": [
            {"citationId": citation_id, "resolutionStatus": "ready"}
            for citation_id in ("cit_ai", "cit_capex", "cit_supply")
        ]
    }

    audit = tracker.evaluate(answer, citation_bundle=bundle)
    rows = [item for item in audit["requirements"] if item["kind"] == "topic"]

    assert audit["status"] == "complete"
    assert all(row["retrievalStatus"] == "available" for row in rows)
    assert all(row["modelInputStatus"] == "visible" for row in rows)
    assert {
        attempt for row in rows for attempt in row["attemptIds"] if attempt.startswith("citation:")
    } == {
        "citation:cit_ai",
        "citation:cit_capex",
        "citation:cit_supply",
    }


def test_unreferenced_or_wrong_cell_citation_does_not_prove_visibility() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "只列出闪迪最近一期财报的营业收入和经营现金流。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    answer = """| 公司 | 营业收入 | 经营现金流 |
|---|---:|---:|
| 闪迪 | 59.50 亿美元 [1](citation://cit_revenue) | 30.38 亿美元 |
"""
    bundle = {
        "citations": [
            {"citationId": "cit_revenue", "resolutionStatus": "ready"},
            {"citationId": "cit_unreferenced", "resolutionStatus": "ready"},
        ]
    }

    audit = tracker.evaluate(answer, citation_bundle=bundle)
    rows = {
        item["description"]: item
        for item in audit["requirements"]
        if item["kind"] == "structured-slot"
    }

    assert rows["闪迪 / latest-published / operating_revenue"]["modelInputStatus"] == "visible"
    assert (
        rows["闪迪 / latest-published / operating_cash_flow"]["modelInputStatus"] == "not-visible"
    )


def test_full_document_scope_can_complete_explicit_unavailable_slot() -> None:
    policy = _finance_like_policy()
    policy["config"]["task_coverage"]["retrieval"]["content_mappings"].append(
        {
            "id": "full-document",
            "role": "content",
            "coverage_text": "result",
            "coverage_scope": "full-document",
            "tool_patterns": ["*document_raw_content*"],
        }
    )
    contract = parse_task_contract(
        "只列出 SK海力士最近一期财报的营业收入、毛利率和经营现金流。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "document_raw_content",
        {"doc_id": "sk-q2"},
        "SK海力士 FY2026 Q2 营业收入为79.32万亿韩元。",
    )
    answer = """| 公司 | 期间 | 营业收入 | 毛利率 | 经营现金流 |
|---|---|---:|---:|---:|
| SK海力士 | FY2026 Q2 | 79.32万亿韩元 | 未披露 | 未披露 |
"""

    audit = tracker.evaluate(answer)
    rows = [item for item in audit["requirements"] if item["kind"] == "structured-slot"]

    assert audit["status"] == "complete"
    assert all(row["modelInputStatus"] == "visible" for row in rows)
    missing_fields = [row for row in rows if row["retrievalStatus"] == "partial"]
    assert {row["description"].rsplit(" / ", 1)[-1] for row in missing_fields} == {
        "gross_margin",
        "operating_cash_flow",
    }


def test_partial_chunk_scope_does_not_prove_absent_metric_visible() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "只列出 SK海力士最近一期财报的营业收入和经营现金流。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "kb_search",
        {"doc_id": "sk-q2", "query": "营业收入"},
        "SK海力士 FY2026 Q2 营业收入为79.32万亿韩元。",
    )
    answer = "SK海力士营业收入79.32万亿韩元，经营现金流未披露。"

    audit = tracker.evaluate(answer)
    cash_flow = next(
        row for row in audit["requirements"] if row["description"].endswith("operating_cash_flow")
    )

    assert audit["status"] == "partial"
    assert cash_flow["modelInputStatus"] == "not-visible"


def test_out_of_scope_index_chunks_are_attempts_not_document_content() -> None:
    contract = parse_task_contract(
        "总结当前文档。",
        policy_snapshot=_finance_like_policy(),
        document_ids=("document-a",),
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=_finance_like_policy())
    tracker.record_tool_result(
        "kb_search",
        {"doc_id": "document-a", "query": "发布结果"},
        {
            "chunks": [
                {
                    "id": "wrong-chunk",
                    "content": "Unrelated document says the deployment failed.",
                    "doc": {"doc_id": "document-b", "title": "Other document"},
                }
            ]
        },
    )

    audit = tracker.evaluate("当前文档说明部署失败。")
    requirement = audit["requirements"][0]

    assert audit["status"] == "partial"
    assert requirement["retrievalStatus"] != "available"
    assert requirement["modelInputStatus"] != "visible"


def test_latest_published_selector_rejects_fallback_to_older_complete_period() -> None:
    policy = _finance_like_policy()
    policy["config"]["task_coverage"]["retrieval"]["content_mappings"].append(
        {
            "id": "full-document",
            "role": "content",
            "coverage_text": "result",
            "coverage_scope": "full-document",
            "tool_patterns": ["*document_raw_content*"],
        }
    )
    contract = parse_task_contract(
        "只列出 SK海力士最近一期已发布财报的营业收入、毛利率和经营现金流。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "company_search",
        {"query": "SK海力士"},
        {
            "companies": [
                {
                    "name": "SK海力士",
                    "english_name": "SK Hynix",
                    "symbol": "KR:000660",
                }
            ]
        },
    )
    tracker.record_tool_result(
        "earnings_search",
        {"symbols": ["KR:000660"]},
        {
            "documents": [
                {"doc_id": "sk-q2", "title": "SK Hynix FY2026 Q2 Results"},
                {"doc_id": "sk-q1", "title": "SK Hynix FY2026 Q1 Results"},
            ]
        },
    )
    tracker.record_tool_result(
        "document_raw_content",
        {"doc_id": "sk-q2"},
        "SK Hynix FY2026 Q2 revenue 79.32 trillion KRW; gross margin and "
        "operating cash flow not disclosed.",
    )
    tracker.record_tool_result(
        "document_raw_content",
        {"doc_id": "sk-q1"},
        "SK Hynix FY2026 Q1 revenue 52.58 trillion KRW, gross margin 79.3%, "
        "operating cash flow 26.33 trillion KRW.",
    )
    older_answer = """| 公司 | 期间 | 营业收入 | 毛利率 | 经营现金流 |
|---|---|---:|---:|---:|
| SK海力士 | FY2026 Q1 | 52.58万亿韩元 | 79.3% | 26.33万亿韩元 |
"""
    latest_answer = """| 公司 | 期间 | 营业收入 | 毛利率 | 经营现金流 |
|---|---|---:|---:|---:|
| SK海力士 | FY2026 Q2 | 79.32万亿韩元 | 未披露 | 未披露 |
"""

    older_audit = tracker.evaluate(older_answer)
    latest_audit = tracker.evaluate(latest_answer)

    assert older_audit["status"] == "partial"
    assert {
        row["selectorResolution"]["period"]
        for row in older_audit["requirements"]
        if row["kind"] == "structured-slot"
    } == {"2026-q2"}
    assert all(
        row["reasonCodes"] == ["latest-period-mismatch"]
        for row in older_audit["requirements"]
        if row["kind"] == "structured-slot"
    )
    assert latest_audit["status"] == "complete"


def test_full_document_inherits_exact_discovery_record_scope_when_externalized() -> None:
    policy = _finance_like_policy()
    policy["config"]["task_coverage"]["retrieval"]["content_mappings"].append(
        {
            "id": "full-document",
            "role": "content",
            "coverage_text": "result",
            "coverage_scope": "full-document",
            "tool_patterns": ["*document_raw_content*"],
        }
    )
    contract = parse_task_contract(
        "只列出 SK海力士最近一期已发布财报的营业收入、毛利率和经营现金流。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "earnings_search",
        {"symbols": ["KR:000660"]},
        {
            "docs": [
                {
                    "doc_id": "sk-q2",
                    "title": "SK海力士 - 2026 Q2 - Quarterly Results",
                    "metadata": {"fiscal_year": "2026", "fiscal_quarter": "Q2"},
                },
                {
                    "doc_id": "sk-q1",
                    "title": "SK海力士 - 2026 Q1 - Quarterly Report",
                    "metadata": {"fiscal_year": "2026", "fiscal_quarter": "Q1"},
                },
            ]
        },
    )
    tracker.record_tool_result(
        "document_raw_content",
        {"doc_id": "sk-q2"},
        "/large_tool_results/sk-q2",
    )
    answer = """| 公司 | 期间 | 营业收入 | 毛利率 | 经营现金流 |
|---|---|---:|---:|---:|
| SK海力士 | FY2026 Q2 | 79.32万亿韩元 | 未披露 | 未披露 |
"""

    audit = tracker.evaluate(answer)
    rows = [item for item in audit["requirements"] if item["kind"] == "structured-slot"]

    assert audit["status"] == "complete"
    assert {row["selectorResolution"]["period"] for row in rows} == {"2026-q2"}
    assert all(row["modelInputStatus"] == "visible" for row in rows)
    assert all(row["attemptIds"] == [row["attemptIds"][0]] for row in rows)
    assert {row["attemptIds"][0].split("_")[1] for row in rows} == {"2"}


def test_externalized_older_document_does_not_satisfy_latest_period_scope() -> None:
    policy = _finance_like_policy()
    policy["config"]["task_coverage"]["retrieval"]["content_mappings"].append(
        {
            "id": "full-document",
            "role": "content",
            "coverage_text": "result",
            "coverage_scope": "full-document",
            "tool_patterns": ["*document_raw_content*"],
        }
    )
    contract = parse_task_contract(
        "只列出 SK海力士最近一期已发布财报的营业收入和经营现金流。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "earnings_search",
        {"symbols": ["KR:000660"]},
        {
            "docs": [
                {"doc_id": "sk-q2", "title": "SK海力士 FY2026 Q2 Results"},
                {"doc_id": "sk-q1", "title": "SK海力士 FY2026 Q1 Report"},
            ]
        },
    )
    tracker.record_tool_result(
        "document_raw_content",
        {"doc_id": "sk-q1"},
        "/large_tool_results/sk-q1",
    )
    answer = """| 公司 | 期间 | 营业收入 | 经营现金流 |
|---|---|---:|---:|
| SK海力士 | FY2026 Q2 | 未披露 | 未披露 |
"""

    audit = tracker.evaluate(answer)
    rows = [item for item in audit["requirements"] if item["kind"] == "structured-slot"]

    assert audit["status"] == "partial"
    assert all(row["modelInputStatus"] == "not-visible" for row in rows)
    assert all(row["attemptIds"] == [row["attemptIds"][0]] for row in rows)
    assert {row["attemptIds"][0].split("_")[1] for row in rows} == {"1"}


def test_row_citation_proves_reporting_period_model_visibility() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "只列出闪迪最近一期已发布财报的营业收入，报告期和单位必须明确。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    answer = """| 公司 | 报告期 | 营业收入 |
|---|---|---:|
| 闪迪 | FY2026 Q3 | 59.50亿美元 [1](citation://cit_revenue) |
"""
    audit = tracker.evaluate(
        answer,
        citation_bundle={
            "citations": [
                {
                    "citationId": "cit_revenue",
                    "resolutionStatus": "ready",
                }
            ]
        },
    )
    reporting_period = next(
        row for row in audit["requirements"] if row["description"].endswith("reporting_period")
    )

    assert reporting_period["retrievalStatus"] == "available"
    assert reporting_period["modelInputStatus"] == "visible"
    assert reporting_period["attemptIds"] == ["citation:cit_revenue"]


def test_out_of_scope_indexed_chunks_do_not_prove_locked_document_field() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "只列出 SK海力士最近一期已发布财报的经营现金流。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "earnings_search",
        {"symbols": ["KR:000660"]},
        {
            "docs": [
                {
                    "doc_id": "sk-q2",
                    "title": "SK海力士 2026 Q2 Quarterly Results",
                }
            ]
        },
    )
    tracker.record_tool_result(
        "kb_search",
        {"doc_id": "sk-q2", "query": "SK海力士 经营现金流"},
        {
            "chunks": [
                {
                    "id": "wrong",
                    "content": "NVIDIA operating cash flow was 29,833 million USD.",
                    "doc": {"doc_id": "nvda-q2", "title": "NVIDIA Q2"},
                }
            ]
        },
    )

    audit = tracker.evaluate("SK海力士经营现金流：当前资料未披露。")
    cash_flow = next(
        row for row in audit["requirements"] if row["description"].endswith("operating_cash_flow")
    )

    assert cash_flow["retrievalStatus"] == "partial"
    assert cash_flow["modelInputStatus"] == "not-visible"


def test_unavailable_fallback_is_metric_local_within_shared_document() -> None:
    policy = _finance_like_policy()
    contract = parse_task_contract(
        "只列出 SK海力士最近一期已发布财报的净利润和经营现金流，只输出 Markdown 表格。",
        policy_snapshot=policy,
    )
    tracker = TaskCoverageTracker(contract, policy_snapshot=policy)
    tracker.record_tool_result(
        "document_raw_content",
        {"doc_id": "sk-q2"},
        ("SK海力士 2026 Q2 净利润为93.9226万亿韩元；经营现金流在当前材料中未披露。"),
    )
    answer = """| 公司 | 报告期 | 净利润 | 经营现金流 |
|---|---|---:|---:|
| SK海力士 | 2026 Q2 |  |  |
"""
    audit = tracker.evaluate(answer)
    patched, requirement_ids = tracker.patch_unavailable_table_slots(answer, audit)

    assert "| SK海力士 | 2026 Q2 |  | 当前资料未披露 |" in patched
    assert len(requirement_ids) == 1
    requirement = next(
        item for item in contract.requirements if item.requirement_id == requirement_ids[0]
    )
    assert requirement.slots.get("metric") == "operating_cash_flow"


def test_nominal_quantum_computing_topic_is_not_a_calculation_task() -> None:
    contract = parse_task_contract(
        "只参考当前公司汇报中涉及的量子计算方向，给出相关方向和候选公司。",
        policy_snapshot=_finance_like_policy(),
        document_ids=("current-report",),
    )

    assert contract.task_type == "document-qa"
    assert not [item for item in contract.requirements if item.kind == "calculation"]
    assert {item.slots.get("topic") for item in contract.requirements if item.kind == "topic"} == {
        "technical_directions",
        "candidate_companies",
    }
    assert contract.enforceable is True


def test_open_research_topics_are_derived_from_policy_ontology() -> None:
    contract = parse_task_contract(
        "分析 A 股有色金属 ETF 的走势和行业基本面。",
        policy_snapshot=_finance_like_policy(),
    )

    topics = [item for item in contract.requirements if item.kind == "topic"]
    assert {item.slots.get("topic") for item in topics} == {
        "price_trend",
        "industry_fundamentals",
    }
    assert all(item.policy_refs for item in topics)
    assert contract.enforceable is True


def test_multi_period_structured_query_expands_metric_period_matrix_and_topics_once() -> None:
    contract = parse_task_contract(
        "查询微软过去四个季度的单季毛利率和运营利润率，逐季列成表，解释趋势并拆解驱动。",
        policy_snapshot=_finance_like_policy(),
    )

    structured = [item for item in contract.requirements if item.kind == "structured-slot"]
    assert len(structured) == 8
    assert {item.slots.get("entityName") for item in structured} == {"微软"}
    assert {item.slots.get("metric") for item in structured} == {
        "gross_margin",
        "operating_margin",
    }
    assert {item.slots.get("periodOrdinal") for item in structured} == {0, 1, 2, 3}
    assert all(item.selectors["period"]["kind"] == "latest-published" for item in structured)
    assert all(item.selectors["period"]["granularity"] == "quarter" for item in structured)

    topics = [item for item in contract.requirements if item.kind == "topic"]
    assert {item.slots.get("topic") for item in topics} == {"trend_analysis", "drivers"}
    assert len(topics) == 2

    output = next(item for item in contract.requirements if item.kind == "output-shape")
    assert output.slots["format"] == "table"
    assert output.slots["periodGroupCount"] == 4


def test_generic_subject_parser_keeps_only_compared_companies() -> None:
    contract = parse_task_contract(
        "用最新完整财季检查中际旭创单季营收同比增速，并与新易盛同季数据比较；"
        "按用户给定的连续季度阈值判断红/黄/绿灯。逐项列出输入、阈值、当前状态"
        "和还需连续观察的季度数。",
        policy_snapshot=_finance_like_policy(),
    )

    assert contract.declared_scope.get("entities") == ["中际旭创", "新易盛"]
    assert not {
        "输入",
        "阈值",
        "当前状态",
        "还需连续观察",
    } & set(contract.declared_scope.get("entities", []))


def test_placeholder_subject_is_not_registered_as_a_literal_entity() -> None:
    contract = parse_task_contract(
        "列出目标公司最近两期的商誉余额，只要报告期、商誉和币种。",
        policy_snapshot=_finance_like_policy(),
    )

    assert contract.declared_scope.get("entities", []) == []
    assert all(
        item.slots.get("entityName") is None
        for item in contract.requirements
        if item.kind == "structured-slot"
    )

"""Tests for deterministic citation calculation evidence."""

from __future__ import annotations

import json

from src.core.citation import EvidenceRegistry
from src.core.tools import ExecContext

from valuz_agent.modules.citations.calculation_tool import (
    _citation_calculate_handler,
    build_citation_calculation_tool_defs,
)


async def test_calculation_tool_computes_and_emits_registered_evidence() -> None:
    result = await _citation_calculate_handler(
        {
            "expression": "((current / prior) - 1) * 100",
            "inputs": [
                {
                    "name": "current",
                    "value": "120",
                    "unit": "CNYm",
                    "evidenceHandle": "ev_current_12345678",
                },
                {
                    "name": "prior",
                    "value": "100",
                    "unit": "CNYm",
                    "evidenceHandle": "ev_prior_12345678",
                },
            ],
            "unit": "%",
            "decimalPlaces": 2,
            "metric": "revenue_growth",
            "period": "2025 FY",
        },
        ExecContext(session_id="s1"),
    )

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["result"] == "20.00"
    assert payload["evidenceHandle"].startswith("ev_calc_")
    evidence = payload["_valuz_evidence"]["evidence"]
    assert evidence["kind"] == "calculation"
    assert evidence["toolName"] == "runtime.calculation"
    assert evidence["inputs"][0]["citationId"] == "ev_current_12345678"
    registry = EvidenceRegistry()
    assert registry.register_tool_result(result.content, tool_name="citation_calculate") == 1


async def test_calculation_tool_rejects_code_and_unknown_names() -> None:
    result = await _citation_calculate_handler(
        {
            "expression": "__import__('os').system('echo unsafe')",
            "inputs": [
                {
                    "name": "current",
                    "value": 120,
                    "evidenceHandle": "ev_current_12345678",
                }
            ],
            "unit": "%",
        },
        ExecContext(session_id="s1"),
    )

    assert result.is_error is True
    assert "unsupported_expression" in result.content


async def test_calculation_tool_converts_unscaled_ratio_to_percentage_points() -> None:
    result = await _citation_calculate_handler(
        {
            "expression": "(current - prior) / prior",
            "inputs": [
                {
                    "name": "current",
                    "value": "170899152276",
                    "unit": "CNY",
                    "evidenceHandle": "ev_current_12345678",
                },
                {
                    "name": "prior",
                    "value": "147693604994",
                    "unit": "CNY",
                    "evidenceHandle": "ev_prior_12345678",
                },
            ],
            "unit": "%",
            "decimalPlaces": 2,
        },
        ExecContext(session_id="s1"),
    )

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["result"] == "15.71"
    assert payload["_valuz_evidence"]["evidence"]["expression"] == (
        "((current - prior) / prior) * 100"
    )


async def test_calculation_tool_accepts_unicode_identifier_names() -> None:
    result = await _citation_calculate_handler(
        {
            "expression": "(本周用量 - 上周用量) / 上周用量",
            "inputs": [
                {
                    "name": "本周用量",
                    "value": "8.25",
                    "unit": "T tokens",
                    "evidenceHandle": "ev_current_12345678",
                },
                {
                    "name": "上周用量",
                    "value": "3.94",
                    "unit": "T tokens",
                    "evidenceHandle": "ev_prior_12345678",
                },
            ],
            "unit": "%",
            "decimalPlaces": 1,
            "metric": "token_usage_growth",
        },
        ExecContext(session_id="s1"),
    )

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["result"] == "109.4"
    evidence = payload["_valuz_evidence"]["evidence"]
    assert [item["name"] for item in evidence["inputs"]] == ["本周用量", "上周用量"]


async def test_calculation_tool_preserves_structured_collection_addresses() -> None:
    current = "evc_income_current_12345678#/data/1/total_revenue/operating_revenue"
    prior = "evc_income_prior_12345678#/data/0/total_revenue/operating_revenue"
    result = await _citation_calculate_handler(
        {
            "expression": "(current - prior) / prior",
            "inputs": [
                {
                    "name": "current",
                    "value": "170899152276",
                    "unit": "CNY",
                    "evidenceHandle": current,
                },
                {
                    "name": "prior",
                    "value": "147693604994",
                    "unit": "CNY",
                    "evidenceHandle": prior,
                },
            ],
            "unit": "%",
            "decimalPlaces": 2,
        },
        ExecContext(session_id="s1"),
    )

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["result"] == "15.71"
    assert [item["citationId"] for item in payload["_valuz_evidence"]["evidence"]["inputs"]] == [
        current,
        prior,
    ]
    registry = EvidenceRegistry()
    assert registry.register_tool_result(result.content, tool_name="citation_calculate") == 1


async def test_calculation_tool_accepts_explicit_user_input_provenance() -> None:
    result = await _citation_calculate_handler(
        {
            "expression": "((price / cost) - 1)",
            "inputs": [
                {
                    "name": "price",
                    "value": "193.775",
                    "unit": "USD",
                    "evidenceHandle": "ev_price_12345678",
                },
                {
                    "name": "cost",
                    "value": "150",
                    "unit": "USD",
                    "origin": "user-input",
                },
            ],
            "unit": "%",
            "decimalPlaces": 1,
            "metric": "return_since_cost",
        },
        ExecContext(session_id="s1"),
    )

    assert result.is_error is False
    payload = json.loads(result.content)
    assert payload["result"] == "29.2"
    inputs = payload["_valuz_evidence"]["evidence"]["inputs"]
    assert inputs == [
        {
            "name": "price",
            "citationId": "ev_price_12345678",
            "value": "193.775",
            "unit": "USD",
        },
        {
            "name": "cost",
            "origin": "user-input",
            "value": "150",
            "unit": "USD",
        },
    ]
    registry = EvidenceRegistry()
    assert registry.register_tool_result(result.content, tool_name="citation_calculate") == 1


async def test_calculation_tool_rejects_unattributed_input() -> None:
    result = await _citation_calculate_handler(
        {
            "expression": "cost * 1.2",
            "inputs": [{"name": "cost", "value": "150", "unit": "USD"}],
            "unit": "USD",
        },
        ExecContext(session_id="s1"),
    )

    assert result.is_error is True
    assert "invalid_input_origin" in result.content


def test_calculation_tool_is_available_to_every_session() -> None:
    (tool,) = build_citation_calculation_tool_defs()
    assert tool.name == "citation_calculate"
    assert tool.read_only is True
    assert tool.handler is not None

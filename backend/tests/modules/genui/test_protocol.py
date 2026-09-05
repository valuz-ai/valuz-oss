"""A2UI v0.9.1 is the sole generated-UI wire protocol."""

from __future__ import annotations

import json

from valuz_agent.modules.genui.prompts import TOOL_DESCRIPTION
from valuz_agent.modules.genui.protocol import (
    OUTPUT_FORMAT,
    a2ui_instructions,
    a2ui_message_lines,
    build_a2ui_prompt,
    component_property_names,
    extract_a2ui_document,
    registered_component_data_contracts,
    registered_component_data_tool_guide,
    wrap_generated_ui,
)


def test_tool_description_states_when_to_call_it() -> None:
    assert "UI" in TOOL_DESCRIPTION and "chart" in TOOL_DESCRIPTION.lower()
    assert "information hierarchy" in TOOL_DESCRIPTION
    assert "not raw colors, CSS, theme tokens" in TOOL_DESCRIPTION
    assert "exactly one short sentence" in TOOL_DESCRIPTION
    assert "do not recap values" in TOOL_DESCRIPTION
    assert "do not call generate_ui again" in TOOL_DESCRIPTION


def test_prompt_splices_request_data_catalog_and_v091_contract() -> None:
    prompt = build_a2ui_prompt("a bar chart of Q1-Q4 sales", {"q1": 10})
    assert "REQUEST:" in prompt
    assert "a bar chart of Q1-Q4 sales" in prompt
    assert '"q1": 10' in prompt
    assert "A2UI v0.9.1" in prompt
    assert '"version":"v0.9.1"' in prompt
    assert "createSurface" in prompt
    assert "updateComponents" in prompt
    assert "Valuz A2UI component catalog" in prompt
    assert "MetricGroup" in prompt
    assert "TimeSeriesChart" in prompt
    assert 'not nested under "props"' in prompt
    assert "Do not create placeholder charts" in prompt
    assert "Query component data is planned and completed by generate_ui" in prompt
    assert "catalog-typed inline props" in prompt
    assert "Do not inline current" in prompt
    assert "frozen snapshot" in prompt
    assert "registered query component" in prompt
    assert "Do not\nauthor source ids, API URLs, dataRefs metadata" in prompt
    assert "Never create\nsurface-global /refs" in prompt


def test_prompt_can_send_only_agent_selected_component_schemas() -> None:
    prompt = build_a2ui_prompt(
        "show quarterly sales",
        component_names=["TextContent", "BarChart"],
    )

    assert "Stack(" in prompt
    assert "TextContent(" in prompt
    assert "BarChart(" in prompt
    assert "LineChart(" not in prompt
    assert "DataTable(" not in prompt


def test_prompt_keeps_live_execution_details_out_of_the_compiler_plan() -> None:
    prompt = build_a2ui_prompt(
        "Use QuoteStrip for the live quote",
        component_names=["QuoteStrip"],
        component_data=[
            {
                "component": "QuoteStrip",
                "params": {"symbol": "US:NVDA"},
                "inputs": ({
                    "key": "main",
                    "source": "finance.market.quote",
                    "shape": "FinanceMetricData",
                    "bindings": {"metrics": "metrics", "source": "source", "asOf": "asOf"},
                    "refresh_interval": 30,
                },),
            }
        ],
    )

    assert "PLANNED QUERY COMPONENTS" in prompt
    assert '"component": "QuoteStrip"' in prompt
    assert '"params": {"symbol": "US:NVDA"}' in prompt
    assert '"key": "main"' in prompt
    assert '"bindings": {"metrics": "metrics", "source": "source", "asOf": "asOf"}' in prompt
    assert "finance.market.quote" not in prompt
    assert "FinanceMetricData" not in prompt


def test_prompt_delegates_theme_and_pixels_to_the_host() -> None:
    prompt = " ".join(build_a2ui_prompt("create a compact investment workbench").split())

    assert "host already supplies the A2UI theme" in prompt
    assert "Do not encode those" in prompt
    assert "environment choices in the document" in prompt
    assert "custom CSS, raw colors, theme tokens" in prompt
    assert "Use component variants and semantic properties only" in prompt
    assert "not final pixels" in prompt


def test_prompt_selects_visuals_by_relationship_and_semantic_role() -> None:
    prompt = " ".join(build_a2ui_prompt("compare actual results with consensus").split())

    assert "Choose a chart only when the data relationship requires it" in prompt
    assert "Use series.role when a series has stable meaning" in prompt
    assert "Semantic roles override the palette" in prompt
    assert "Mathematical positive/negative is not market up/down" in prompt
    assert "Never invent a palette or color" in prompt


def test_session_instruction_and_output_format_name_the_stream() -> None:
    instructions = a2ui_instructions()
    assert "A2UI v0.9.1" in instructions
    assert "request's language" in instructions
    assert "reasoning or progress" in instructions
    assert "stop immediately" in instructions
    assert OUTPUT_FORMAT == "A2UI v0.9.1 JSON message stream"


def test_prompt_disambiguates_normalized_time_series_from_line_chart() -> None:
    prompt = build_a2ui_prompt("比较三只股票近一月归一化收益")

    assert "use TimeSeriesChart, not LineChart" in prompt
    assert 'chart series entries use "label", never "name"' in prompt
    assert '"referenceValue":100' in prompt


def test_prompt_uses_original_user_message_as_output_language_reference() -> None:
    prompt = build_a2ui_prompt(
        "Build a semiconductor comparison dashboard",
        language_reference="请生成半导体公司对比工作台",
    )

    assert "Agent-authored REQUEST may be a translation" in prompt
    assert "请生成半导体公司对比工作台" in prompt
    assert prompt.index("请生成半导体公司对比工作台") < prompt.index(
        "Build a semiconductor comparison dashboard"
    )


def test_host_edit_prompt_includes_complete_current_document() -> None:
    current = (
        '{"version":"v0.9.1","createSurface":{"surfaceId":"main"}}\n'
        '{"version":"v0.9.1","updateComponents":{"surfaceId":"main",'
        '"components":[{"id":"root","component":"Stack"}]}}'
    )
    prompt = build_a2ui_prompt(
        "replace only the earnings chart",
        current_document=current,
    )

    assert "CURRENT HOST DOCUMENT" in prompt
    assert current in prompt
    assert "complete replacement A2UI document, not a patch" in prompt
    assert "Preserve every current component" in prompt


def test_new_page_prompt_has_no_edit_contract() -> None:
    prompt = build_a2ui_prompt("create a new workbench")

    assert "CURRENT HOST DOCUMENT" not in prompt
    assert "EDIT CONTRACT" not in prompt


def test_registered_component_data_contracts_parse_param_types(monkeypatch) -> None:
    notes = (
        'COMPONENT_DATA_CONTRACT {"component":"TimeSeriesChart",'
        '"params":"{symbols: comma-separated symbols, rangeDays?: 2-3650, normalize?: boolean}",'
        '"inputs":[{"key":"prices","source":"finance.market.kline",'
        '"shape":"FinanceTimeSeriesData","bindings":{"data":"data","series":"series"},'
        '"paramMap":{"symbols":"symbols"},"refreshInterval":300}],'
        '"fixedProps":{"xKey":"date"}}\n'
        'COMPONENT_DATA_CONTRACT {"component":"MarketOverview",'
        '"params":"{commodity: oil|gold|silver}","inputs":[{"key":"main",'
        '"source":"finance.macro.commodities","shape":"FinanceMetricData",'
        '"bindings":{"metrics":"metrics"},"refreshInterval":300}]}\n'
        'COMPONENT_DATA_CONTRACT {"component":"CompanyResearchOverview",'
        '"params":"{symbol: prefixed}","inputs":['
        '{"key":"quote","source":"finance.market.quote","shape":"FinanceMetricData",'
        '"bindings":{"quoteMetrics":"metrics"},"paramMap":{"symbol":"symbol"}},'
        '{"key":"documents","source":"finance.company.docs","shape":"FinanceDocumentData",'
        '"bindings":{"documentItems":"items"},"paramMap":{"symbol":"symbol"}}]}'
    )
    monkeypatch.setattr(
        "valuz_agent.modules.genui.protocol.edition_catalog_text",
        lambda *a, **k: notes,
    )

    contracts = registered_component_data_contracts()

    assert contracts["TimeSeriesChart"]["required_params"] == ("symbols",)
    assert contracts["TimeSeriesChart"]["inputs"][0]["key"] == "prices"
    assert contracts["TimeSeriesChart"]["inputs"][0]["source"] == "finance.market.kline"
    assert contracts["TimeSeriesChart"]["inputs"][0]["bindings"] == {
        "data": "data", "series": "series"
    }
    assert contracts["TimeSeriesChart"]["inputs"][0]["param_map"] == {
        "symbols": "symbols"
    }
    assert contracts["TimeSeriesChart"]["fixed_props"] == {"xKey": "date"}
    assert contracts["TimeSeriesChart"]["param_specs"]["rangeDays"] == {
        "required": False,
        "description": "2-3650",
        "kind": "number",
        "minimum": 2.0,
        "maximum": 3650.0,
    }
    assert contracts["TimeSeriesChart"]["param_specs"]["normalize"]["kind"] == "boolean"
    assert contracts["MarketOverview"]["param_specs"]["commodity"]["enum"] == (
        "oil",
        "gold",
        "silver",
    )
    assert [
        value["key"] for value in contracts["CompanyResearchOverview"]["inputs"]
    ] == ["quote", "documents"]
    assert contracts["CompanyResearchOverview"]["inputs"][1]["param_map"] == {
        "symbol": "symbol"
    }
    guide = registered_component_data_tool_guide()
    assert "TimeSeriesChart {symbols: comma-separated symbols" in guide
    assert "rangeDays?: 2-3650" in guide
    assert "CompanyResearchOverview {symbol: prefixed}" in guide
    assert "finance.market.kline" not in guide
    assert "Do not pass source ids" in guide


def test_component_property_names_reads_edition_catalog(monkeypatch) -> None:
    catalog = (
        "  - QuoteStrip(title, subtitle, source, asOf, basis, metrics) — quote\n"
        "  - DocumentFeed(title, items, source, asOf) — documents"
    )
    monkeypatch.setattr(
        "valuz_agent.modules.genui.protocol.edition_catalog_text",
        lambda *a, **k: catalog,
    )

    assert component_property_names("QuoteStrip") == (
        "title",
        "subtitle",
        "source",
        "asOf",
        "basis",
        "metrics",
    )
    assert component_property_names("not valid") == ()


def test_wrap_generated_ui_puts_the_stream_in_the_client_envelope() -> None:
    content = '{"version":"v0.9.1","createSurface":{"surfaceId":"s1"}}'
    assert json.loads(wrap_generated_ui(content)) == {
        "protocol": "a2ui-json",
        "content": content,
    }


def test_pretty_printed_a2ui_messages_are_complete_not_truncated() -> None:
    pretty = """{
  "version": "v0.9.1",
  "createSurface": {"surfaceId": "main"}
}
{
  "version": "v0.9.1",
  "updateComponents": {
    "surfaceId": "main",
    "components": [{"id": "root", "component": "Stack"}]
  }
}"""

    lines, truncated = a2ui_message_lines(pretty)

    assert truncated is False
    assert len(lines) == 2
    assert "createSurface" in lines[0]
    assert "updateComponents" in lines[1]
    assert extract_a2ui_document(pretty) == "\n".join(lines)


def test_pretty_printed_a2ui_with_a_cut_tail_is_truncated() -> None:
    raw = """{
  "version": "v0.9.1",
  "createSurface": {"surfaceId": "main"}
}
{
  "version": "v0.9.1",
  "updateComponents": {"surfaceId": "main", "components": [
"""

    lines, truncated = a2ui_message_lines(raw)

    assert truncated is True
    assert len(lines) == 1
    assert "createSurface" in lines[0]


def test_extractor_drops_empty_root_reset_after_component_data() -> None:
    messages = [
        {"version": "v0.9.1", "createSurface": {"surfaceId": "main"}},
        {
            "version": "v0.9.1",
            "updateDataModel": {
                "surfaceId": "main",
                "path": "/data/quote",
                "value": {"source": "Reportify", "asOf": "2026-08-12"},
            },
        },
        {
            "version": "v0.9.1",
            "updateComponents": {
                "surfaceId": "main",
                "components": [{"id": "root", "component": "Stack"}],
            },
        },
        {
            "version": "v0.9.1",
            "updateDataModel": {"surfaceId": "main", "path": "/", "value": {}},
        },
    ]
    raw = "\n".join(json.dumps(message) for message in messages)
    expected = "\n".join(
        json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        for message in messages[:-1]
    )

    assert extract_a2ui_document(raw) == expected


def test_extractor_keeps_root_seed_when_it_is_the_data_payload() -> None:
    messages = [
        {"version": "v0.9.1", "createSurface": {"surfaceId": "main"}},
        {
            "version": "v0.9.1",
            "updateDataModel": {
                "surfaceId": "main",
                "path": "/",
                "value": {"title": "Market"},
            },
        },
        {
            "version": "v0.9.1",
            "updateComponents": {
                "surfaceId": "main",
                "components": [{"id": "root", "component": "Stack"}],
            },
        },
    ]
    raw = "\n".join(json.dumps(message) for message in messages)

    assert extract_a2ui_document(raw) == "\n".join(
        json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        for message in messages
    )


def test_param_grammar_ignores_prose_around_the_braces_and_pipes_inside_prose(monkeypatch) -> None:
    """The edition writes ``{symbol: exact prefixed stock|index|fund|crypto symbol,
    rangeDays?: 2-3650}; daily bars`` — the first name must not keep the brace,
    the range must survive the trailing brace, and a format description that
    merely contains ``|`` is not an enum."""
    notes = (
        'COMPONENT_DATA_CONTRACT {"component":"PriceVolumeChart",'
        '"params":"{symbol: exact prefixed stock|index|fund|crypto symbol, '
        'rangeDays?: 2-3650}; daily bars; legacy aliases readable",'
        '"inputs":[{"key":"main","source":"finance.market.kline","shape":"FinanceTimeSeriesData",'
        '"bindings":{"data":"data"},"paramMap":{"symbol":"symbol"},"refreshInterval":300}]}\n'
        'COMPONENT_DATA_CONTRACT {"component":"MarketOverview",'
        '"params":"{spx?: canonical index symbol, '
        'commodity?: oil|gold|gas|silver|platinum}; latest snapshots",'
        '"inputs":[{"key":"spx","source":"finance.market.index","shape":"FinanceMetricData",'
        '"bindings":{"spxMetrics":"metrics"},"paramMap":{"symbol":"spx"},"refreshInterval":60}]}'
    )
    monkeypatch.setattr(
        "valuz_agent.modules.genui.protocol.edition_catalog_text", lambda *a, **k: notes
    )

    contracts = registered_component_data_contracts()

    price = contracts["PriceVolumeChart"]
    assert set(price["param_specs"]) == {"symbol", "rangeDays"}
    assert price["required_params"] == ("symbol",)
    assert "enum" not in price["param_specs"]["symbol"]
    assert (
        price["param_specs"]["symbol"]["description"]
        == "exact prefixed stock|index|fund|crypto symbol"
    )
    assert price["param_specs"]["rangeDays"] == {
        "required": False,
        "description": "2-3650",
        "kind": "number",
        "minimum": 2.0,
        "maximum": 3650.0,
    }
    market = contracts["MarketOverview"]
    assert set(market["param_specs"]) == {"spx", "commodity"}
    assert "enum" not in market["param_specs"]["spx"]
    assert market["param_specs"]["commodity"]["enum"] == (
        "oil", "gold", "gas", "silver", "platinum",
    )

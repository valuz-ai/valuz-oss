"""The model sees exactly the A2UI component scope requested by a tool call."""

from __future__ import annotations

import valuz_agent.modules.genui.protocol as protocol
from valuz_agent.modules.genui.protocol import (
    GenUIComponentScope,
    a2ui_instructions,
    build_a2ui_catalog,
    build_a2ui_prompt,
    edition_catalog_text,
    normalize_component_scope,
    resolve_component_scope,
)
from valuz_agent.modules.genui.tools import _PARAMS

SCOPES: tuple[GenUIComponentScope, ...] = ("all", "atoms", "edition")


def test_default_and_invalid_values_use_the_whole_catalog() -> None:
    assert normalize_component_scope(None) == "all"
    assert normalize_component_scope({}) == "all"
    assert normalize_component_scope("bogus") == "all"


def test_supported_scope_names_are_explicit() -> None:
    assert normalize_component_scope("Edition") == "edition"
    assert normalize_component_scope("vertical") == "edition"
    assert normalize_component_scope("a2ui") == "atoms"
    assert normalize_component_scope("base") == "atoms"


def test_tool_advertises_the_scope_argument() -> None:
    components = _PARAMS["properties"]["components"]
    assert components["enum"] == list(SCOPES)
    assert components["default"] == "all"
    assert "components" not in _PARAMS["required"]


def test_base_scope_contains_layout_content_and_analytics() -> None:
    prompt = build_a2ui_prompt("revenue dashboard", None, "atoms")
    for name in ("Stack", "TextContent", "MetricGroup", "TimeSeriesChart"):
        assert name in prompt


def test_all_is_the_union_of_base_and_edition(monkeypatch) -> None:
    monkeypatch.setattr(
        protocol,
        "edition_catalog_text",
        lambda: "  - FinanceTile(title: string) — Finance extension.",
    )
    everything = build_a2ui_catalog("all")
    assert "FinanceTile" in everything
    assert "MetricGroup" in everything

    only_edition = build_a2ui_catalog("edition")
    assert "FinanceTile" in only_edition
    assert "MetricGroup" not in only_edition
    assert "Stack" in only_edition

    only_base = build_a2ui_catalog("atoms")
    assert "FinanceTile" not in only_base
    assert "MetricGroup" in only_base


def test_empty_edition_scope_widens_to_all() -> None:
    assert edition_catalog_text() == ""
    assert resolve_component_scope("edition") == "all"
    assert build_a2ui_prompt("chart", None, "edition") == build_a2ui_prompt(
        "chart", None, "all"
    )


def test_catalog_and_instructions_resolve_the_same_scope() -> None:
    for scope in SCOPES:
        resolved = resolve_component_scope(scope)
        assert build_a2ui_catalog(scope) == build_a2ui_catalog(resolved)
        assert a2ui_instructions(scope) == a2ui_instructions(resolved)


def test_every_scope_keeps_the_v091_message_contract() -> None:
    for scope in SCOPES:
        catalog = build_a2ui_catalog(scope)
        assert '{"id":"root","component":"Stack"' in catalog
        assert "{fallbacks}" not in catalog

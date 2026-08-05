"""The generated block catalog reaches the A2UI prompt.

The blocks live in the frontend package; this asset is how the model hears
about them. If the asset goes missing or stops being spliced in, nothing fails
loudly — the model simply never emits a block, and the renderer's support for
them sits unused. These tests are the loud failure.
"""

from importlib import resources

from valuz_agent.modules.genui.protocol import (
    A2UI_COMPONENT_CATALOG,
    build_a2ui_prompt,
)


def _asset() -> str:
    return (
        resources.files("valuz_agent.modules.genui")
        .joinpath("a2ui_block_catalog.txt")
        .read_text(encoding="utf-8")
    )


def test_block_catalog_asset_is_present_and_populated():
    text = _asset()
    assert len(text) > 200, "generated block catalog looks empty — run gen:openui-prompt"
    assert text.lstrip().startswith("- "), "catalog should be a list of components"


def test_catalog_includes_a_block_from_every_family():
    for name in ("MiniCardBlock", "StatsCard", "Citation", "ReportPage", "Mermaid"):
        assert name in A2UI_COMPONENT_CATALOG, f"{name} missing from the A2UI catalog"


def test_a2ui_prompt_carries_the_block_catalog():
    prompt = build_a2ui_prompt("show a KPI dashboard")
    assert "MiniCardBlock" in prompt
    assert "ReportPage" in prompt


def test_retired_components_are_gone_from_the_prompt():
    # FinanceMetric's renderer was deleted in favour of StatsCard. The name
    # still resolves for old payloads, but the model must stop being taught it.
    prompt = build_a2ui_prompt("show a valuation metric")
    assert "FinanceMetric" not in prompt
    assert "StatsCard" in prompt


def test_components_without_a_block_equivalent_are_kept():
    # MarketBreadth and DataList have no counterpart among the blocks; dropping
    # them while deduplicating would be a feature loss, not a cleanup.
    for name in ("MarketBreadth", "DataList", "MarketIndexGrid"):
        assert name in A2UI_COMPONENT_CATALOG

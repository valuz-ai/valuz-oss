"""The generated OSS A2UI catalog is the compiler's actual vocabulary."""

from importlib import resources

from valuz_agent.modules.genui.protocol import A2UI_COMPONENT_CATALOG, build_a2ui_prompt


def _asset() -> str:
    return (
        resources.files("valuz_agent.modules.genui")
        .joinpath("a2ui_component_catalog.txt")
        .read_text(encoding="utf-8")
    )


def test_component_catalog_asset_is_present_and_populated() -> None:
    text = _asset()
    assert len(text) > 1_000, "generated A2UI catalog looks empty — run gen:a2ui-catalog"
    assert text.lstrip().startswith("- ")


def test_catalog_includes_each_base_component_family() -> None:
    for name in (
        "Stack",
        "TextContent",
        "Input",
        "Button",
        "Metric",
        "DataTable",
        "Timeline",
        "TimeSeriesChart",
        "ComboChart",
        "NetworkGraph",
    ):
        assert name in A2UI_COMPONENT_CATALOG, f"{name} missing from the A2UI catalog"


def test_prompt_carries_only_the_new_a2ui_vocabulary() -> None:
    prompt = build_a2ui_prompt("show a KPI dashboard")
    assert "MetricGroup" in prompt
    assert "TimeSeriesChart" in prompt
    for retired in ("MiniCardBlock", "StatsCard", "ReportPage"):
        assert retired not in prompt

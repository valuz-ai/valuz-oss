"""genui prompt builder — pure function tests."""

from valuz_agent.modules.genui.prompts import (
    GENERATIVE_UI_INSTRUCTIONS,
    TOOL_DESCRIPTION,
    build_openui_prompt,
)


def test_build_prompt_splices_request_and_data():
    p = build_openui_prompt("a bar chart of Q1-Q4 sales", {"q1": 10, "q2": 20})
    assert "REQUEST:" in p
    assert "a bar chart of Q1-Q4 sales" in p
    assert '"q1": 10' in p
    # bundled library prompt is large
    assert len(p) > 500


def test_build_prompt_without_data():
    p = build_openui_prompt("just a table")
    assert "REQUEST:" in p
    assert "just a table" in p


def test_constants_are_set():
    assert "UI" in TOOL_DESCRIPTION and "chart" in TOOL_DESCRIPTION.lower()
    assert "OpenUI Lang" in GENERATIVE_UI_INSTRUCTIONS
    assert "do not nest Card components" in GENERATIVE_UI_INSTRUCTIONS
    assert "borderless sections" in GENERATIVE_UI_INSTRUCTIONS
    assert "never generate an application shell" in GENERATIVE_UI_INSTRUCTIONS
    assert "charts must render one per row" in GENERATIVE_UI_INSTRUCTIONS
    assert "use Tabs to switch" in GENERATIVE_UI_INSTRUCTIONS
    assert "mobile-first responsive layout" in GENERATIVE_UI_INSTRUCTIONS
    assert "do not force every module to occupy a full-width row" in GENERATIVE_UI_INSTRUCTIONS
    assert "Peer modules may share wrapping rows or responsive grids" in GENERATIVE_UI_INSTRUCTIONS
    assert "avoid narrow sidebars" in GENERATIVE_UI_INSTRUCTIONS
    assert "must wrap before they overflow" in GENERATIVE_UI_INSTRUCTIONS
    assert "Use this adaptive dashboard template" in GENERATIVE_UI_INSTRUCTIONS
    assert "compact, unframed header" in GENERATIVE_UI_INSTRUCTIONS
    assert "primary KPI metrics" in GENERATIVE_UI_INSTRUCTIONS
    assert "full-width primary trend" in GENERATIVE_UI_INSTRUCTIONS
    assert "balanced visual weight" in GENERATIVE_UI_INSTRUCTIONS
    assert "readable single-column flow" in GENERATIVE_UI_INSTRUCTIONS
    # The component roster used to be restated here in prose. It now lives in
    # the generated catalog (see test_a2ui_block_catalog.py) — describing
    # components in two places is how one of them ends up naming a component
    # that no longer exists, which is exactly what happened to FinanceMetric.
    # These instructions carry layout policy only.
    assert "MarketIndexGrid" not in GENERATIVE_UI_INSTRUCTIONS
    assert "FinanceMetric" not in GENERATIVE_UI_INSTRUCTIONS
    assert "Never invent, duplicate, or repeat metrics" in GENERATIVE_UI_INSTRUCTIONS
    assert "stack all major sections top-to-bottom" not in GENERATIVE_UI_INSTRUCTIONS
    assert (
        "Row layouts are allowed only for compact KPI chips/cards"
        not in GENERATIVE_UI_INSTRUCTIONS
    )

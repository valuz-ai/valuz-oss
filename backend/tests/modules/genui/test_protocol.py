"""A2UI v0.9.1 is the sole generated-UI wire protocol."""

from __future__ import annotations

import json

from valuz_agent.modules.genui.prompts import TOOL_DESCRIPTION
from valuz_agent.modules.genui.protocol import (
    OUTPUT_FORMAT,
    a2ui_instructions,
    build_a2ui_prompt,
    wrap_generated_ui,
)


def test_tool_description_states_when_to_call_it() -> None:
    assert "UI" in TOOL_DESCRIPTION and "chart" in TOOL_DESCRIPTION.lower()
    assert "information hierarchy" in TOOL_DESCRIPTION
    assert "not raw colors, CSS, theme tokens" in TOOL_DESCRIPTION


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
    assert "you MUST bind it" in prompt
    assert "initial seed" in prompt
    assert "frozen snapshot" in prompt
    assert "binding's initial seed" in prompt


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
    assert "A2UI v0.9.1" in a2ui_instructions()
    assert OUTPUT_FORMAT == "A2UI v0.9.1 JSON message stream"


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


def test_wrap_generated_ui_puts_the_stream_in_the_client_envelope() -> None:
    content = '{"version":"v0.9.1","createSurface":{"surfaceId":"s1"}}'
    assert json.loads(wrap_generated_ui(content)) == {
        "protocol": "a2ui-json",
        "content": content,
    }

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


def test_session_instruction_and_output_format_name_the_stream() -> None:
    assert "A2UI v0.9.1" in a2ui_instructions()
    assert OUTPUT_FORMAT == "A2UI v0.9.1 JSON message stream"


def test_wrap_generated_ui_puts_the_stream_in_the_client_envelope() -> None:
    content = '{"version":"v0.9.1","createSurface":{"surfaceId":"s1"}}'
    assert json.loads(wrap_generated_ui(content)) == {
        "protocol": "a2ui-json",
        "content": content,
    }

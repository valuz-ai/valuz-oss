"""GenUI protocol selection and payload tests."""

from __future__ import annotations

import json

from valuz_agent.infra.config import Settings
from valuz_agent.modules.genui.protocol import (
    build_prompt_for_protocol,
    normalize_genui_protocol,
    output_format_for_protocol,
    session_instructions_for_protocol,
    wrap_generated_ui,
)


def test_settings_defaults_generate_ui_to_a2ui():
    assert Settings().genui_protocol == "a2ui"


def test_settings_allows_openui_env_override(monkeypatch):
    monkeypatch.setenv("VALUZ_GENUI_PROTOCOL", "openui")

    assert Settings().genui_protocol == "openui"


def test_protocol_normalization_accepts_wire_aliases():
    assert normalize_genui_protocol("a2ui-json") == "a2ui"
    assert normalize_genui_protocol("A2UI") == "a2ui"
    assert normalize_genui_protocol("openui-lang") == "openui"
    assert normalize_genui_protocol("OpenUI") == "openui"


def test_a2ui_prompt_describes_message_stream_and_openui_catalog():
    prompt = build_prompt_for_protocol("a2ui", "sales dashboard", {"revenue": 12})

    assert "A2UI" in prompt
    assert "v0.9" in prompt
    assert "createSurface" in prompt
    assert "updateComponents" in prompt
    assert "OpenUI component catalog" in prompt
    assert "@a2ui/react" in prompt
    assert '"path":"/","value":{...}' in prompt
    assert '"text":"Revenue"' in prompt
    assert 'not nested under "props"' in prompt
    assert '"revenue": 12' in prompt
    assert "Valuz semantic components" in prompt
    assert "MarketIndexGrid" in prompt
    assert "MarketIndexCard" in prompt
    # FinanceMetric was retired in favour of the StatsCard block, which carries
    # the same label/value/delta/description shape. The name still resolves in
    # the renderer for older payloads, but the model is no longer taught it —
    # see test_a2ui_block_catalog.py.
    assert "StatsCard" in prompt
    assert "MarketBreadth" in prompt
    assert "DataList" in prompt
    # The row anatomy used to be spelled out in hand-written catalog prose.
    # It now comes from the DataList block's own description, which is
    # generated — so assert the substance rather than the retired wording.
    assert "leaderboards" in prompt
    assert "Do not create placeholder charts" in prompt


def test_a2ui_session_instruction_and_output_format_are_not_openui_lang():
    assert "A2UI" in session_instructions_for_protocol("a2ui")
    assert output_format_for_protocol("a2ui") == "A2UI v0.9 JSON message stream"


def test_wrap_generated_ui_keeps_openui_raw_and_wraps_a2ui():
    assert wrap_generated_ui("openui", "  root = Stack([])  ") == "root = Stack([])"

    wrapped = json.loads(
        wrap_generated_ui(
            "a2ui",
            '{"version":"v0.9","createSurface":{"surfaceId":"s1"}}',
        )
    )

    assert wrapped == {
        "protocol": "a2ui-json",
        "content": '{"version":"v0.9","createSurface":{"surfaceId":"s1"}}',
    }

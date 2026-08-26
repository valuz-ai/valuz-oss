"""Synthetic emission probe toolset for the PTC codegen golden.

Covers every output-shaping branch of ``tool_generator``: renamed keyword
params, enum → Literal, arrays with item types, nullable anyOf, defaults,
hostile quoting in descriptions, missing/false/true readOnlyHint, an
outputSchema-typed return, and a required param with no legal Python name.

The golden file pins the emitted bytes — a diff means the emission shape
moved, which must also move ``_GENERATOR_SALT`` (or be reverted).
"""

from __future__ import annotations

from typing import Any

PROBE_SERVER = "probe-data"

PROBE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_series",
        "description": (
            'Fetch a data series. Contains a hostile """ quote and a\n'
            "Returns:\n    dict: the series envelope with data rows"
        ),
        "annotations": {"readOnlyHint": True, "title": "Series"},
        "inputSchema": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "start date"},
                "kind": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly"],
                    "default": "daily",
                    "description": "sampling",
                },
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "comma symbols",
                },
                "limit": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "description": "row cap",
                },
            },
            "required": ["from", "symbols"],
        },
    },
    {
        "name": "list_items",
        "description": "List items.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {}},
        "outputSchema": {
            "type": "array",
            "items": {"type": "object"},
        },
    },
    {
        "name": "manage_things",
        "description": "Create or delete things.",
        "annotations": {"readOnlyHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {"action": {"type": "string"}},
            "required": ["action"],
        },
    },
    {
        "name": "no_hint_tool",
        "description": "No annotations at all.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "broken_required",
        "description": "A required param with no salvageable identifier.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            # An empty property name sanitizes to nothing at all — the only
            # truly unsalvageable case (every non-empty name maps to SOME
            # identifier, e.g. "???" -> "___").
            "properties": {"": {"type": "string"}},
            "required": [""],
        },
    },
]

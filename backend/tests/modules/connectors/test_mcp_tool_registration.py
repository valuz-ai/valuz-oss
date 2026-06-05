"""Guard: the connectors FastMCP server must register exactly the intended
tools — no more, no less.

Regression cover for a real bug: a private helper (`_recommended_requires`)
was inserted between an ``@_mcp.tool(...)`` decorator and
``list_recommended_mcp``, so the decorator bound to the helper and
``list_recommended_mcp`` silently stopped being a tool. ruff / mypy /
pytest didn't catch it because nothing asserted the registered tool set.
"""

from __future__ import annotations

import asyncio

import valuz_agent.integrations.connectors_mcp_server as m

_EXPECTED = {"create_mcp", "list_connected_mcp", "list_recommended_mcp"}


def _registered_tool_names() -> set[str]:
    return {t.name for t in asyncio.run(m._mcp.list_tools())}


def test_should_register_exactly_the_intended_connector_tools():
    assert _registered_tool_names() == _EXPECTED


def test_should_not_expose_private_helpers_as_tools():
    names = _registered_tool_names()
    # `_recommended_requires` is an internal helper, never an agent tool.
    assert "_recommended_requires" not in names
    assert not any(n.startswith("_") for n in names)

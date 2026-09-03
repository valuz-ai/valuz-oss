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

_EXPECTED = {
    "create_mcp",
    "list_connected_mcp",
    "list_recommended_mcp",
    # Management tools — the Connectors page operations, for the agent.
    "list_mcp",
    "get_mcp",
    "update_mcp",
    "delete_mcp",
    "enable_mcp",
    "disable_mcp",
    "test_mcp",
}


def _registered_tool_names() -> set[str]:
    return {t.name for t in asyncio.run(m._mcp.list_tools())}


def test_should_register_exactly_the_intended_connector_tools():
    assert _registered_tool_names() == _EXPECTED


def test_should_not_expose_private_helpers_as_tools():
    names = _registered_tool_names()
    # `_recommended_requires` is an internal helper, never an agent tool.
    assert "_recommended_requires" not in names
    assert not any(n.startswith("_") for n in names)


# ── create_mcp parameter shapes ──────────────────────────────────────────
#
# ``args`` and ``env`` were declared ``str`` carrying JSON, so a caller had to
# escape JSON inside a JSON tool call. Models get that wrong: a real stdio
# install produced ``"args": "-y pkg"`` — space-joined, which ``json.loads``
# rejects — and an ``"env":`` with no value at all, which never parsed as a
# tool call. Pin the declared shapes so they can't regress to strings.


def _create_mcp_properties() -> dict:
    tool = next(t for t in asyncio.run(m._mcp.list_tools()) if t.name == "create_mcp")
    return tool.inputSchema["properties"]


def _non_null_variant(prop: dict) -> dict:
    """The real type out of Pydantic's ``anyOf: [T, null]`` for an optional."""
    return next(v for v in prop["anyOf"] if v.get("type") != "null")


def test_should_declare_args_as_an_array_when_building_the_tool_schema():
    variant = _non_null_variant(_create_mcp_properties()["args"])
    assert variant["type"] == "array"
    assert variant["items"]["type"] == "string"


def test_should_declare_env_as_an_object_when_building_the_tool_schema():
    variant = _non_null_variant(_create_mcp_properties()["env"])
    assert variant["type"] == "object"
    assert variant["additionalProperties"]["type"] == "string"


def test_should_keep_args_when_given_a_native_array():
    assert m._as_args(["-y", "foo-mcp@latest"]) == ["-y", "foo-mcp@latest"]


def test_should_keep_env_when_given_a_native_object():
    assert m._as_env({"FOO_TOKEN": "t"}) == {"FOO_TOKEN": "t"}


def test_should_still_parse_args_when_given_the_old_json_string():
    assert m._as_args('["-y", "foo-mcp@latest"]') == ["-y", "foo-mcp@latest"]


def test_should_still_parse_env_when_given_the_old_json_string():
    assert m._as_env('{"FOO_TOKEN": "t"}') == {"FOO_TOKEN": "t"}


def test_should_report_absent_when_args_and_env_are_missing_or_empty():
    assert m._as_args(None) is None
    assert m._as_args("") is None
    assert m._as_env(None) is None
    assert m._as_env("") is None

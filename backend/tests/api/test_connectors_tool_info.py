"""Unit tests for the connector tool-info mapping.

``_tools_to_info`` maps the objects returned by an MCP server's
``list_tools()`` into the ``ToolInfo`` wire shape consumed by the
Connectors detail panel. It is a pure helper so we can pin its
behaviour without standing up a live MCP server.

Coverage:
  - name + description are carried through verbatim
  - a missing / None description survives as ``None``
  - entries without a usable name are dropped
"""

from __future__ import annotations

from dataclasses import dataclass

from valuz_agent.api.routes.connectors import ToolInfo, _tools_to_info


@dataclass
class _FakeTool:
    name: str | None
    description: str | None = None


class TestToolsToInfo:
    def test_should_carry_name_and_description_when_present(self) -> None:
        infos = _tools_to_info(
            [_FakeTool(name="search_threads", description="Searches email threads.")]
        )
        assert infos == [ToolInfo(name="search_threads", description="Searches email threads.")]

    def test_should_keep_none_description_when_tool_declares_none(self) -> None:
        infos = _tools_to_info([_FakeTool(name="ping")])
        assert len(infos) == 1
        assert infos[0].name == "ping"
        assert infos[0].description is None

    def test_should_drop_entries_without_a_name(self) -> None:
        infos = _tools_to_info([_FakeTool(name=None), _FakeTool(name="")])
        assert infos == []

    def test_should_map_multiple_tools_preserving_order(self) -> None:
        infos = _tools_to_info(
            [
                _FakeTool(name="a", description="first"),
                _FakeTool(name="b", description=None),
            ]
        )
        assert [(i.name, i.description) for i in infos] == [
            ("a", "first"),
            ("b", None),
        ]

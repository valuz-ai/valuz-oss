"""Round-trip: ``McpHttpServerConfig.tool_timeout_sec`` survives the store
converter, and old rows (no field) deserialize to ``None`` (backward compatible).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.adapters.sqlalchemy_store.converters import dict_to_mcp, mcp_to_dict
from src.core.types import McpHttpServerConfig


def test_tool_timeout_sec_round_trips() -> None:
    cfg = McpHttpServerConfig(name="harness", url="https://x/mcp", tool_timeout_sec=3600.0)
    d = mcp_to_dict(cfg)
    assert d["tool_timeout_sec"] == 3600.0
    back = dict_to_mcp(d)
    assert isinstance(back, McpHttpServerConfig)
    assert back.tool_timeout_sec == 3600.0


def test_tool_timeout_sec_omitted_when_none() -> None:
    cfg = McpHttpServerConfig(name="s", url="https://x/mcp")
    d = mcp_to_dict(cfg)
    assert "tool_timeout_sec" not in d  # keep the serialized shape lean
    assert dict_to_mcp(d).tool_timeout_sec is None


def test_legacy_row_without_field_deserializes_to_none() -> None:
    # A row persisted before the field existed has no key → None, not a crash.
    legacy = {"name": "s", "url": "https://x/mcp", "transport": "http", "headers": {}}
    assert dict_to_mcp(legacy).tool_timeout_sec is None


def test_tool_timeout_sec_crosses_kernel_http_schema() -> None:
    from app._validators import validate_mcp_servers
    from app.serializers import mcp_to_schema

    source = McpHttpServerConfig(
        name="reportify",
        url="https://mcp.reportify.cn/search/mcp",
        tool_timeout_sec=600.0,
    )

    schema = mcp_to_schema(source)
    assert schema.tool_timeout_sec == 600.0

    restored = validate_mcp_servers([schema])[0]
    assert isinstance(restored, McpHttpServerConfig)
    assert restored.tool_timeout_sec == 600.0

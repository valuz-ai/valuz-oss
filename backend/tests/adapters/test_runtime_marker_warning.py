"""The turn must say so when it is about to ship an unfilled marker."""

from __future__ import annotations

import logging
import sys

sys.path.insert(0, "kernel")

from src.core.runtime_context import runtime_context_marker
from src.core.types import McpHttpServerConfig

from valuz_agent.adapters.kernel_client import _warn_on_unfillable_markers


class _Session:
    def __init__(self, servers):
        self.mcp_servers = servers


def _docs_server(credential: str) -> McpHttpServerConfig:
    return McpHttpServerConfig(
        name="valuz_docs",
        url="https://host/_internal/mcp/docs/mcp",
        transport="http",
        headers={"X-Valuz-Internal": credential, "X-Valuz-Session-Id": "s1"},
    )


def test_an_unfilled_marker_is_named(caplog):
    """Otherwise it travels verbatim, every built-in MCP 403s, the runtime
    parks them, and the model says "No such tool available" — three layers
    from the cause, with nothing naming the marker."""
    marker = runtime_context_marker("commercial.execution")
    session = _Session([_docs_server(marker)])

    with caplog.at_level(logging.ERROR):
        _warn_on_unfillable_markers("s1", session, {"some.other.key": "v"})

    assert "commercial.execution" in caplog.text
    assert "valuz_docs" in caplog.text


def test_a_filled_marker_is_quiet(caplog):
    marker = runtime_context_marker("commercial.execution")
    session = _Session([_docs_server(marker)])

    with caplog.at_level(logging.ERROR):
        _warn_on_unfillable_markers("s1", session, {"commercial.execution": "vxe_real"})

    assert caplog.text == ""


def test_a_real_credential_is_not_mistaken_for_a_marker(caplog):
    """A legacy ``vzs_`` credential is a value, not a placeholder."""
    session = _Session([_docs_server("vzs_91vItUuZ1AscXCYsUtqQeSFdqIAN802ftOXir0X_frQ")])

    with caplog.at_level(logging.ERROR):
        _warn_on_unfillable_markers("s1", session, None)

    assert caplog.text == ""

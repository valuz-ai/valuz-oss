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


def test_an_unbound_contributor_is_named_too(caplog):
    """The most informative case used to be the one the check could not see.

    An unbound contributor returns ``None`` before the check ran, so a
    deployment whose sessions carry markers sent every one of them out
    unfilled in silence — which is exactly what happened.
    """
    marker = runtime_context_marker("commercial.execution")
    session = _Session([_docs_server(marker)])

    with caplog.at_level(logging.ERROR):
        _warn_on_unfillable_markers("s1", session, None)

    assert "no contributor supplied a value" in caplog.text
    assert "valuz_docs" in caplog.text


class _NoContributorSession:
    mcp_servers = ()


async def test_an_unbound_contributor_is_reported(caplog, monkeypatch):
    """The one fact that identifies this failure, and it is not in the session.

    Two earlier attempts hung the evidence off the session row and saw
    nothing: the built-in MCP servers carrying the markers live only in the
    kernel's copy, so the durable row this code can reach has neither.
    """
    from valuz_agent.adapters import kernel_client
    from valuz_agent.ports.runtime_turn_context import NoopRuntimeTurnContextContributor

    monkeypatch.setattr(
        kernel_client,
        "get_runtime_turn_context_contributor",
        lambda: NoopRuntimeTurnContextContributor(),
        raising=False,
    )
    monkeypatch.setattr(
        "valuz_agent.ports.runtime_turn_context.get_runtime_turn_context_contributor",
        lambda: NoopRuntimeTurnContextContributor(),
    )

    with caplog.at_level(logging.WARNING):
        result = await kernel_client._build_runtime_turn_context("u1", "s1")

    assert result is None
    assert "no runtime-context contributor is bound" in caplog.text
    assert "s1" in caplog.text

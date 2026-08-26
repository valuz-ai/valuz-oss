"""dsh reaches kernel ToolDefs (execute_code) through the /mcp/toolkit bridge.

P5 of the PTC plan: dsh consumes tools only through its composition, so the
runtime registers its toolkit on the mcp_bridge session registry at spawn and
the composition carries one extra ``dsh-mcp-client`` row pointing at the
kernel's ``/mcp/toolkit/{session_id}`` endpoint — the exact path codex uses.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.mcp_bridge import get_session_record, reset_registry_for_tests
from src.core.tools import ExecContext, ToolDef, ToolKit, ToolResult
from src.core.types import Session
from src.runtimes.deepseek_harness.composition import (
    KERNEL_TOOLKIT_SERVER_NAME,
    build_composition_rows,
)
from src.runtimes.deepseek_harness.runtime import DeepSeekHarnessRuntime


@pytest.fixture(autouse=True)
def _clean_bridge_registry():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


def _session() -> Session:
    return Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp/ws",
        runtime_provider="deepseek_harness",
        model="deepseek-v4-flash",
        user_id="u1",
    )


def _toolkit_with_execute_code() -> ToolKit:
    async def _handler(args: dict, ctx: ExecContext) -> ToolResult:
        return ToolResult(content="ok")

    toolkit = ToolKit()
    toolkit.register(ToolDef(name="execute_code", description="stub", handler=_handler))
    return toolkit


# -- composition row --------------------------------------------------------


def test_composition_carries_the_kernel_toolkit_row_when_asked(monkeypatch):
    monkeypatch.setenv("CODEX_TOOLKIT_BASE_URL", "http://127.0.0.1:18080")
    rows = build_composition_rows(
        _session(),
        workspace_root="/tmp/ws",
        skills_root=None,
        model_settings=None,
        kernel_toolkit=True,
    )
    row = next(r for r in rows if r["id"] == "kernel-toolkit")
    assert row["name"] == "@deepseek-ai/dsh-mcp-client"
    assert row["config"]["serverName"] == KERNEL_TOOLKIT_SERVER_NAME
    assert row["config"]["transport"] == "streamable-http"
    assert row["config"]["url"] == "http://127.0.0.1:18080/mcp/toolkit/s1"


def test_composition_default_has_no_kernel_toolkit_row():
    rows = build_composition_rows(
        _session(), workspace_root="/tmp/ws", skills_root=None, model_settings=None
    )
    assert not any(r["id"] == "kernel-toolkit" for r in rows)


# -- runtime registration ---------------------------------------------------


def _runtime(toolkit: ToolKit | None) -> DeepSeekHarnessRuntime:
    return DeepSeekHarnessRuntime(
        config=AgentConfig(id="a", name="a"),
        model="deepseek-v4-flash",
        event_sink=SimpleNamespace(),  # type: ignore[arg-type]
        toolkit=toolkit,
        workspace_root="/tmp/ws",
    )


async def test_register_publishes_and_close_unregisters():
    runtime = _runtime(_toolkit_with_execute_code())
    assert runtime._register_kernel_toolkit(_session()) is True
    record = get_session_record("s1")
    assert record is not None
    assert record.toolkit.get("execute_code") is not None
    assert record.exec_context.user_id == "u1"
    await runtime.close()
    assert get_session_record("s1") is None


async def test_empty_toolkit_registers_nothing():
    runtime = _runtime(None)
    assert runtime._register_kernel_toolkit(_session()) is False
    assert get_session_record("s1") is None
    await runtime.close()

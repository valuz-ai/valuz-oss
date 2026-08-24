"""Factory-side execute_code exposure + the PTC prompt-policy block pair."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.tool_registry import get_registered_tool, register_tool
from src.core.tools import ExecContext, ToolDef, ToolKit, ToolResult
from src.core.types import McpHttpServerConfig
from src.ptc.executor import EXECUTE_CODE_TOOL_NAME, maybe_expose_execute_code

from valuz_agent.adapters.system_prompt_builder import (
    PTC_POLICY_REVISION,
    ensure_ptc_system_policy,
    remove_ptc_system_policy,
)


@pytest.fixture()
def registered_tool():
    """Install a stand-in execute_code implementation; restore afterwards."""
    previous = get_registered_tool(EXECUTE_CODE_TOOL_NAME)

    async def _handler(args: dict, ctx: ExecContext) -> ToolResult:
        return ToolResult(content="ok")

    register_tool(ToolDef(name=EXECUTE_CODE_TOOL_NAME, description="stub", handler=_handler))
    yield
    if previous is not None:
        register_tool(previous)


def _session(*, ptc: bool) -> SimpleNamespace:
    metadata = {"ptc": {"servers": ["srv"]}} if ptc else {}
    return SimpleNamespace(
        metadata=metadata,
        mcp_servers=(McpHttpServerConfig(name="srv", url="https://x/mcp"),),
    )


def test_opted_in_session_gets_execute_code(registered_tool):
    toolkit = ToolKit()
    assert maybe_expose_execute_code(toolkit, _session(ptc=True)) is True
    assert toolkit.get(EXECUTE_CODE_TOOL_NAME) is not None


def test_session_without_opt_in_is_untouched(registered_tool):
    toolkit = ToolKit()
    assert maybe_expose_execute_code(toolkit, _session(ptc=False)) is False
    assert toolkit.get(EXECUTE_CODE_TOOL_NAME) is None


def test_missing_registration_is_a_no_op(monkeypatch):
    import src.ptc.executor as executor_mod
    from src.core import tool_registry

    monkeypatch.setattr(tool_registry, "get_registered_tool", lambda name: None)
    # The helper resolves through the registry module import inside it.
    toolkit = ToolKit()
    assert executor_mod.maybe_expose_execute_code(toolkit, _session(ptc=True)) is False


# -- policy block pair ------------------------------------------------------


def test_policy_block_installs_and_is_idempotent():
    once = ensure_ptc_system_policy("User rules.")
    twice = ensure_ptc_system_policy(once)
    assert once == twice
    assert once.startswith("User rules.")
    assert f'<ptc-policy revision="{PTC_POLICY_REVISION}">' in once


def test_policy_block_upgrade_replaces_old_revision():
    old = 'Before.\n\n<ptc-policy revision="ptc-v0">\nold text\n</ptc-policy>'
    upgraded = ensure_ptc_system_policy(old)
    assert "ptc-v0" not in upgraded
    assert PTC_POLICY_REVISION in upgraded
    assert upgraded.count("<ptc-policy") == 1


def test_policy_block_removal_keeps_user_text():
    text = ensure_ptc_system_policy("User rules.")
    assert remove_ptc_system_policy(text) == "User rules."
    assert remove_ptc_system_policy("no block") == "no block"

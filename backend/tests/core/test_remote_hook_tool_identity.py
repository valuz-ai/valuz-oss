"""Tool identity on the remote (dsh) hook path: a runtime that sees MCP and
kernel-toolkit tools under ``mcp__<server>__<tool>`` names reports them with
the same identity every other runtime's dispatch points use."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.hooks import SessionRef, hook_registry
from src.core.hooks.registry import SessionHooks
from src.core.hooks.remote import RemoteHookSession
from src.core.types import Session


def _remote(**kwargs: object) -> RemoteHookSession:
    session = Session(id="s", agent_config=AgentConfig(id="a", name="a"), cwd="/tmp")
    return RemoteHookSession(
        SessionHooks(hook_registry, SessionRef.from_session(session)),
        "deepseek_harness",
        **kwargs,  # type: ignore[arg-type]
    )


def test_mcp_names_are_mcp_tools() -> None:
    ref = _remote().tool_ref("mcp__upstream__echo", {"text": "hi"})
    assert (ref.source, ref.server, ref.name, ref.kind) == (
        "mcp",
        "upstream",
        "mcp__upstream__echo",
        "mcp",
    )


def test_the_kernel_toolkit_server_is_the_toolkit() -> None:
    remote = _remote(toolkit_servers=("harness_toolkit",))
    ref = remote.tool_ref("mcp__harness_toolkit__list_agents", {})
    assert (ref.source, ref.name) == ("toolkit", "list_agents")


def test_other_names_stay_the_runtimes_own() -> None:
    ref = _remote().tool_ref("bash", {"command": "ls"})
    assert (ref.source, ref.kind, ref.command) == ("native", "shell", "ls")

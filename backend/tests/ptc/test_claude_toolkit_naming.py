"""Claude runtime: kernel ToolDefs must not be shadowed by the host ``harness`` MCP.

Regression pin for the P2 field bug: the kernel's in-process SDK MCP server
used to be named ``harness`` — the same name the host's always-on toolkit
MCP claims in ``session.mcp_servers`` — so the proxy entry overwrote it and
every kernel ToolDef (execute_code) silently vanished on claude sessions.
The kernel server is now ``harness_toolkit`` (the codex bridge's name).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.tools import ExecContext, ToolDef, ToolKit, ToolResult
from src.core.types import McpHttpServerConfig, Session
from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime


def _toolkit_with_execute_code() -> ToolKit:
    async def _handler(args: dict, ctx: ExecContext) -> ToolResult:
        return ToolResult(content="ok")

    toolkit = ToolKit()
    toolkit.register(ToolDef(name="execute_code", description="stub", handler=_handler))
    return toolkit


def test_kernel_tools_survive_alongside_the_host_harness_server(tmp_path) -> None:
    agent = AgentConfig(id="a", name="a")
    runtime = ClaudeAgentRuntime(
        agent,
        "",
        SimpleNamespace(),  # type: ignore[arg-type]
        _toolkit_with_execute_code(),
    )
    session = Session(
        id="s",
        agent_config=agent,
        cwd=str(tmp_path),
        mcp_servers=(
            McpHttpServerConfig(
                name="harness",
                url="http://127.0.0.1:8000/_internal/mcp/toolkit/base/mcp",
            ),
        ),
    )

    options = runtime._build_options(session)

    assert isinstance(options.mcp_servers, dict)
    # Both worlds present: the host toolkit proxy under its own name…
    assert "harness" in options.mcp_servers
    # …and the kernel ToolDef server under the non-colliding bridge name.
    assert "harness_toolkit" in options.mcp_servers
    assert (
        options.mcp_servers["harness_toolkit"]["instance"]
        is not (options.mcp_servers["harness"]["instance"])
    )
    # Kernel tools are pre-approved under the new identity.
    assert "mcp__harness_toolkit__execute_code" in options.allowed_tools
    assert "mcp__harness__execute_code" not in options.allowed_tools

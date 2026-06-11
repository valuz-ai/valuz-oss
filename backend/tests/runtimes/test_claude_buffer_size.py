"""``_build_options`` must raise the Claude Agent SDK's stdout read buffer above
its 1 MB default, so a single large ``tool_result`` (big file read, MCP/connector
payload, base64 image) can't overflow the buffer and kill the SDK message reader
("Fatal error in message reader") mid-turn.

Regression for https://github.com/valuz-ai/valuz-oss/issues/74.
"""

from __future__ import annotations

from types import SimpleNamespace

# Side-effect import: puts the kernel ``src/`` on sys.path before any ``from
# src.*`` below resolves. Mirrors tests/runtimes/test_claude_advisor_env.py.
import kernel  # noqa: F401

# The SDK default we are fixing (``_DEFAULT_MAX_BUFFER_SIZE`` in
# claude_agent_sdk/_internal/transport/subprocess_cli.py).
_SDK_DEFAULT_BUFFER = 1 * 1024 * 1024


def _make_runtime():
    """A ``ClaudeAgentRuntime`` with the heavy SDK-touching ``__init__``
    bypassed — only the attributes/helpers ``_build_options`` reads are set."""
    from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime

    rt = object.__new__(ClaudeAgentRuntime)
    rt.workspace_root = None
    rt.model = None
    rt.model_provider = None
    rt.toolkit = SimpleNamespace(list_tools=lambda: [])
    rt.config = SimpleNamespace(callable_agents=None, max_turns=None, max_cost_usd=None)
    rt._fork_next_spawn = False
    # Helpers that touch the SDK / filesystem — stub to benign values.
    rt._build_mcp_tools = lambda: []
    rt._build_agents = lambda: {}
    rt._map_hooks = lambda: {}
    rt._build_system_prompt = lambda session: "system"
    rt._build_settings = lambda: None
    rt._build_sandbox_settings = lambda: None
    rt._build_model_provider_env = lambda: None
    return rt


def _make_session():
    return SimpleNamespace(
        mcp_servers=[],
        permission_mode="default",
        mode="default",
        model_settings=None,
        runtime_session_id=None,
    )


def test_build_options_raises_buffer_above_sdk_default() -> None:
    from src.runtimes.claude_agent.runtime import _MAX_BUFFER_SIZE

    # Guard the constant: it must exceed the SDK's 1 MB default.
    assert _MAX_BUFFER_SIZE > _SDK_DEFAULT_BUFFER

    # And it must actually be wired onto the options the SDK consumes.
    opts = _make_runtime()._build_options(_make_session())
    assert opts.max_buffer_size == _MAX_BUFFER_SIZE
    assert opts.max_buffer_size > _SDK_DEFAULT_BUFFER

"""Bare one-shot completion sessions strip runtime scaffolding (all 3 runtimes).

Ephemeral helper sessions (generative-UI, memory review) are created fresh per
call and make exactly one no-tool LLM round-trip, yet they inherited the full
agentic setup of the runtime that spawned them: claude_agent shipped the whole
``claude_code`` preset system prompt + every built-in tool schema (measured
~38s to first token on the same model that answered in ~2.4s stripped), codex
carried its plan/apply_patch/view_image tools, deepagents wrapped the call in
the deep-agent graph (base prompt + planning/filesystem tools + checkpointer).

The host stamps ``Session.metadata["bare_completion"] = True`` on these
sessions; each runtime reads it via ``src.core.types.is_bare_completion`` and
drops everything its SDK lets it drop.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.types import Session, is_bare_completion


def _session(*, bare: bool, instructions: str = "", runtime: str = "claude_agent") -> Session:
    return Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider=runtime,  # type: ignore[arg-type]
        instructions=instructions,
        metadata={"bare_completion": True} if bare else {},
    )


# --- marker -----------------------------------------------------------------


def test_is_bare_completion_truth_table() -> None:
    assert is_bare_completion(_session(bare=True))
    assert not is_bare_completion(_session(bare=False))
    # Malformed metadata never breaks dispatch.
    broken = SimpleNamespace(metadata=None)
    assert not is_bare_completion(broken)  # type: ignore[arg-type]


# --- claude_agent -----------------------------------------------------------


def _make_claude_runtime():  # noqa: ANN202
    """ClaudeAgentRuntime with the heavy SDK-touching ``__init__`` bypassed —
    mirrors tests/runtimes/test_claude_buffer_size.py."""
    from src.runtimes.claude_agent.runtime import ClaudeAgentRuntime

    rt = object.__new__(ClaudeAgentRuntime)
    rt.workspace_root = "/tmp"
    rt.model = "some-model"
    rt.model_provider = None
    rt.toolkit = SimpleNamespace(list_tools=lambda: [])
    rt.config = SimpleNamespace(callable_agents=None, max_turns=None, max_cost_usd=None)
    rt._fork_next_spawn = False
    rt._build_mcp_tools = lambda: []
    rt._build_agents = lambda: {}
    rt._map_hooks = lambda: {}
    rt._build_settings = lambda: '{"x":1}'
    rt._build_sandbox_settings = lambda: None
    # Mirrors the real signature — the runtime passes the session through so
    # per-session headers (X-Valuz-Session-Id) reach the provider env.
    rt._build_model_provider_env = lambda session=None: None
    return rt


def test_claude_bare_system_prompt_is_plain_instructions() -> None:
    rt = _make_claude_runtime()
    bare = rt._build_system_prompt(_session(bare=True, instructions="You render UI."))
    assert bare == "You render UI."  # plain string — no claude_code preset

    normal = rt._build_system_prompt(_session(bare=False, instructions="You render UI."))
    # SystemPromptPreset is a TypedDict — check shape, not isinstance.
    assert isinstance(normal, dict)
    assert normal["preset"] == "claude_code"
    assert normal["append"] == "You render UI."


def test_claude_bare_options_strip_tools_settings_and_mcp() -> None:
    rt = _make_claude_runtime()
    opts = rt._build_options(_session(bare=True, instructions="You render UI."))
    assert opts.tools == []  # every built-in tool disabled
    assert opts.setting_sources == []  # no CLAUDE.md / skills / settings scan
    assert opts.settings is None  # no harness settings layer
    assert opts.strict_mcp_config is True  # no ambient .mcp.json pickup


def test_claude_normal_options_keep_scaffolding() -> None:
    rt = _make_claude_runtime()
    opts = rt._build_options(_session(bare=False, instructions="You render UI."))
    assert opts.tools is None  # SDK default tool set untouched
    assert opts.setting_sources == ["project"]
    assert opts.settings == '{"x":1}'
    assert opts.strict_mcp_config is False


# --- codex ------------------------------------------------------------------


def test_codex_bare_overrides_strip_optional_tools() -> None:
    from src.runtimes.codex.runtime import _build_config_overrides

    ov = _build_config_overrides(_session(bare=True, runtime="codex"), None, "gpt-5.4-mini")
    assert "include_plan_tool=false" in ov
    assert "include_apply_patch_tool=false" in ov
    assert "include_view_image_tool=false" in ov
    # Subscription path (provider=None): web_search disabled by the bare branch.
    assert 'web_search="disabled"' in ov


def test_codex_normal_overrides_keep_tools() -> None:
    from src.runtimes.codex.runtime import _build_config_overrides

    ov = _build_config_overrides(_session(bare=False, runtime="codex"), None, "gpt-5.4-mini")
    assert not any(o.startswith("include_") for o in ov)
    assert 'web_search="disabled"' not in ov


def test_codex_bare_with_provider_emits_web_search_once() -> None:
    from src.core.types import ModelProvider
    from src.runtimes.codex.runtime import _build_config_overrides

    provider = ModelProvider(base_url="https://gw.example.com/v1", api_key="sk-x")
    ov = _build_config_overrides(_session(bare=True, runtime="codex"), provider, "m")
    assert ov.count('web_search="disabled"') == 1


# --- deepagents -------------------------------------------------------------


def test_deepagents_bare_graph_is_raw_model_client() -> None:
    from src.runtimes.deepagents.runtime import DeepAgentsRuntime

    sentinel = object()
    rt = object.__new__(DeepAgentsRuntime)
    rt._graph = None
    rt._build_model_client = lambda session: sentinel
    session = _session(bare=True, runtime="deepagents", instructions="You render UI.")

    graph = asyncio.run(rt._ensure_graph(session))
    # The bare branch returns the raw langchain chat model — it never builds
    # the deep-agent graph (base prompt, planning/filesystem tools,
    # checkpointer, HITL middleware would all raise on this stub runtime).
    assert graph is sentinel
    assert rt._graph is sentinel
    assert rt._cached_permission_mode == session.permission_mode

"""Claude plan mode is a BUILD-TIME lowering, not just a permissionMode flip.

Field bug: a plan-mode research session (a stock-analysis ask) sailed straight to
the full deliverable — the CLI's native plan mode only gates MUTATING
tools, read-only research is legitimately allowed, so a no-mutation task
never trips the gate; and ``execute_code`` (PTC) ran 5× because
``allowed_tools`` entries outrank the plan gate. The fix makes three
things build-time inputs of ``session.mode == "plan"``:

1. the system prompt carries the plan-mode discipline section;
2. ``execute_code`` is dropped from the toolkit allowlist;
3. mode transitions cold-reload the client (destroy + fork-next-spawn)
   instead of the live ``set_permission_mode`` mutator, so 1–2 stay in
   sync — and because plain ``--resume`` makes the CLI keep the resumed
   session's ORIGINAL permissionMode.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from src.core.agent_config import AgentConfig
from src.core.types import Session
from src.runtimes.claude_agent.runtime import PLAN_MODE_DISCIPLINE, ClaudeAgentRuntime


def _session(*, mode: str = "default", instructions: str = "") -> Session:
    return Session(
        id="s1",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp",
        runtime_provider="claude_agent",
        instructions=instructions,
        mode=mode,  # type: ignore[arg-type]
    )


def _make_runtime(tool_names: list[str] | None = None):  # noqa: ANN202
    """ClaudeAgentRuntime with the heavy SDK-touching ``__init__`` bypassed —
    mirrors tests/runtimes/test_bare_completion.py."""
    rt = object.__new__(ClaudeAgentRuntime)
    rt.workspace_root = "/tmp"
    rt.model = "some-model"
    rt.model_provider = None
    tools = [SimpleNamespace(name=n, handler=lambda: None) for n in (tool_names or [])]
    rt.toolkit = SimpleNamespace(list_tools=lambda: tools)
    rt.config = SimpleNamespace(callable_agents=None, max_turns=None, max_cost_usd=None)
    rt._fork_next_spawn = False
    rt._build_mcp_tools = lambda: []
    rt._build_agents = lambda: {}
    rt._map_hooks = lambda: {}
    rt._build_settings = lambda: '{"x":1}'
    rt._build_sandbox_settings = lambda: None
    rt._build_model_provider_env = lambda session=None: None
    return rt


# --- system prompt -----------------------------------------------------------


def test_plan_mode_appends_discipline_section() -> None:
    rt = _make_runtime()
    prompt = rt._build_system_prompt(_session(mode="plan"))
    assert isinstance(prompt, dict)
    assert prompt["preset"] == "claude_code"
    assert "<plan-mode-discipline>" in prompt["append"]


def test_plan_mode_discipline_stacks_on_instructions() -> None:
    rt = _make_runtime()
    prompt = rt._build_system_prompt(_session(mode="plan", instructions="You are X."))
    assert prompt["append"].startswith("You are X.")
    assert prompt["append"].endswith(PLAN_MODE_DISCIPLINE)


def test_default_mode_has_no_discipline_section() -> None:
    rt = _make_runtime()
    prompt = rt._build_system_prompt(_session(instructions="You are X."))
    assert prompt["append"] == "You are X."
    bare_default = rt._build_system_prompt(_session())
    assert "append" not in bare_default or not bare_default.get("append")


# --- toolkit allowlist -------------------------------------------------------


def test_plan_mode_drops_execute_code_from_allowlist() -> None:
    rt = _make_runtime(["execute_code", "memory_search"])
    opts = rt._build_options(_session(mode="plan"))
    assert "mcp__harness_toolkit__execute_code" not in opts.allowed_tools
    assert "mcp__harness_toolkit__memory_search" in opts.allowed_tools
    # Sanity: plan permissionMode rides the same build.
    assert opts.permission_mode == "plan"


def test_default_mode_keeps_execute_code_allowed() -> None:
    rt = _make_runtime(["execute_code", "memory_search"])
    opts = rt._build_options(_session())
    assert "mcp__harness_toolkit__execute_code" in opts.allowed_tools


# --- transition = cold reload ------------------------------------------------


def _make_transition_runtime(applied_mode: str):  # noqa: ANN202
    rt = object.__new__(ClaudeAgentRuntime)
    rt._client = SimpleNamespace()  # "live" client — must be torn down
    rt._applied_effort = None
    rt._applied_permission_mode = "full_access"
    rt._cached_permission_mode = "full_access"
    rt._applied_mode = applied_mode
    rt._built_with_plan_prompt = applied_mode == "plan"
    rt._fork_next_spawn = False
    destroyed: list[bool] = []

    async def _destroy() -> None:
        destroyed.append(True)
        rt._client = None

    rt._destroy_client = _destroy
    return rt, destroyed


async def test_plan_entry_cold_reloads_and_forks() -> None:
    rt, destroyed = _make_transition_runtime("default")
    await rt._reconcile_session_levers(_session(mode="plan"))
    assert destroyed == [True]
    assert rt._fork_next_spawn is True


async def test_plan_exit_cold_reloads_and_forks() -> None:
    rt, destroyed = _make_transition_runtime("plan")
    await rt._reconcile_session_levers(_session(mode="default"))
    assert destroyed == [True]
    assert rt._fork_next_spawn is True


async def test_no_transition_is_a_no_op() -> None:
    rt, destroyed = _make_transition_runtime("plan")
    session = _session(mode="plan")
    session.permission_mode = "full_access"
    await rt._reconcile_session_levers(session)
    assert destroyed == []


async def test_post_approve_prompt_drift_cold_reloads() -> None:
    # After a mid-turn ExitPlanMode approve the hook flips _applied_mode
    # to default but cannot rebuild the live client — the next turn's
    # reconcile must detect the stale plan-built prompt and cold-reload,
    # or the model keeps being told it's planning.
    rt, destroyed = _make_transition_runtime("default")
    rt._built_with_plan_prompt = True
    session = _session(mode="default")
    session.permission_mode = "full_access"
    await rt._reconcile_session_levers(session)
    assert destroyed == [True]
    assert rt._fork_next_spawn is True


# --- plan gate in the permission handler ------------------------------------


def _make_handler_runtime(
    *, applied_mode: str, cached_permission: str = "full_access", read_only: bool = False
):  # noqa: ANN202
    rt = object.__new__(ClaudeAgentRuntime)
    tdef = SimpleNamespace(read_only=read_only, permission=None)
    rt.toolkit = SimpleNamespace(get=lambda name: tdef if name == "execute_code" else None)
    rt._applied_mode = applied_mode
    rt._cached_permission_mode = cached_permission
    return rt


async def test_plan_mode_denies_mutating_toolkit_tools_even_under_full_access() -> None:
    rt = _make_handler_runtime(applied_mode="plan")
    result = await rt._permission_handler("mcp__harness_toolkit__execute_code", {}, None)
    assert type(result).__name__ == "PermissionResultDeny"
    assert "Plan mode is active" in result.message


async def test_plan_mode_still_allows_read_only_toolkit_tools() -> None:
    rt = _make_handler_runtime(applied_mode="plan", read_only=True)
    result = await rt._permission_handler("mcp__harness_toolkit__execute_code", {}, None)
    assert type(result).__name__ == "PermissionResultAllow"


async def test_approved_plan_releases_the_toolkit_gate() -> None:
    # _on_exit_plan_mode_approved flips _applied_mode to "default" —
    # same-turn execution passes the gate again.
    rt = _make_handler_runtime(applied_mode="default")
    result = await rt._permission_handler("mcp__harness_toolkit__execute_code", {}, None)
    assert type(result).__name__ == "PermissionResultAllow"


# --- rejection envelope ------------------------------------------------------


async def test_exit_plan_mode_reject_wraps_feedback_unambiguously() -> None:
    # Field case: the deny message IS the tool result the model reads.
    # The raw CJK feedback below ("write the results to a file"), passed
    # through alone, made the model announce the plan was approved and
    # start executing — the envelope must both flag the rejection and
    # round-trip the feedback verbatim.
    import asyncio

    class _Sink:
        async def emit(self, event: object) -> None:
            pass

    rt = object.__new__(ClaudeAgentRuntime)
    rt.workspace_root = "/tmp"
    rt.event_sink = _Sink()
    rt._pending_futures = {}

    async def _resolve_soon() -> None:
        while not rt._pending_futures:
            await asyncio.sleep(0)
        pid, future = next(iter(rt._pending_futures.items()))
        future.set_result(("reject", "结果写到文件中", None, None))

    resolver = asyncio.ensure_future(_resolve_soon())
    result = await rt._await_host_decision("ExitPlanMode", {"plan": "# P"}, None)
    await resolver
    assert type(result).__name__ == "PermissionResultDeny"
    assert "did NOT approve" in result.message
    assert "结果写到文件中" in result.message
    assert "ExitPlanMode again" in result.message

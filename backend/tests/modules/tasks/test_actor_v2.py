"""Unit tests for v2 actor-style dispatch (M10 附录 B).

Covers the mechanism in isolation — the mailbox channel and the actor loop's
turn/idle/await/shutdown control flow — without touching the kernel. The turn
runner and finalizer are stubbed so the loop's branching is what's exercised.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.mailbox import (
    InboxMsg,
    MailboxRegistry,
    mailbox_registry,
)
from valuz_agent.modules.tasks.orchestrator import (
    TaskOrchestrator,
    _credential_gap,
    _member_run_dir,
    collect_manifest,
)


def _fake_agent_config(**kw):
    """Real AgentConfig for resolver fakes — serializer needs full fields."""
    from src.core import AgentConfig  # type: ignore[import-not-found]

    kw.setdefault("id", "fake-agent")
    kw.setdefault("name", "fake")
    kw.setdefault("skills", tuple(kw.get("skills", ())))
    kw.setdefault("mcp_servers", ())
    kw.pop("metadata", None) if False else None
    allowed = {
        "id",
        "name",
        "model",
        "runtime_provider",
        "instructions",
        "tools",
        "callable_agents",
        "skills",
        "mcp_servers",
        "permission_mode",
        "max_turns",
        "max_cost_usd",
        "effort",
        "thinking",
        "metadata",
    }
    kw = {k: v for k, v in kw.items() if k in allowed}
    if isinstance(kw.get("skills"), list):
        kw["skills"] = tuple(kw["skills"])
    return AgentConfig(**kw)


def _async_member_get(source_agent_slug: str = "lead-agent"):
    """A fake ProjectMemberDatastore.get — async, since the real one is async
    (build_member_session awaits it)."""

    async def _get(user_id: str, ws: str, slug: str) -> SimpleNamespace:
        return SimpleNamespace(source_agent_slug=source_agent_slug)

    return _get


def _as_async(fn):
    """Wrap a sync callable as a coroutine fn for monkeypatching the async
    ``kernel_client`` facade (its methods are awaited by the code under test)."""

    async def _f(*args, **kwargs):
        return fn(*args, **kwargs)

    return _f


# ---------------------------------------------------------------------------
# MailboxRegistry
# ---------------------------------------------------------------------------


async def test_put_get_roundtrip_delivers_message() -> None:
    reg = MailboxRegistry()
    reg.register("s1")
    assert reg.put("s1", InboxMsg(kind="message", text="hello")) is True
    msg = await reg.get("s1", timeout=1.0)
    assert msg.kind == "message"
    assert msg.text == "hello"


async def test_put_to_unregistered_session_is_noop() -> None:
    reg = MailboxRegistry()
    assert reg.put("ghost", InboxMsg(kind="message", text="x")) is False


async def test_get_times_out_when_no_message() -> None:
    reg = MailboxRegistry()
    reg.register("s1")
    with pytest.raises(asyncio.TimeoutError):
        await reg.get("s1", timeout=0.05)


async def test_unregister_drops_inbox() -> None:
    reg = MailboxRegistry()
    reg.register("s1")
    reg.unregister("s1")
    assert reg.is_registered("s1") is False
    with pytest.raises(KeyError):
        await reg.get("s1", timeout=0.01)


# ---------------------------------------------------------------------------
# Actor loop control flow (turn runner + finalizer stubbed)
# ---------------------------------------------------------------------------


async def test_lead_loop_runs_turns_until_shutdown() -> None:
    """Lead loop: initial turn, wakes on a message turn, breaks on shutdown."""
    orch = TaskOrchestrator()
    prompts: list[str] = []
    finalized: list[tuple[str, str]] = []

    async def fake_turn(session_id: str, content: str) -> str:
        prompts.append(content)
        return "idle"

    async def fake_finalize(**kwargs: object) -> None:
        finalized.append((str(kwargs["session_id"]), str(kwargs["final_status"])))

    orch._run_turn_with_sink = fake_turn  # type: ignore[method-assign]
    orch._finalize_actor = fake_finalize  # type: ignore[method-assign]

    # Pre-load the inbox: a follow-up, then a shutdown. register() in the loop
    # is idempotent so these survive.
    mailbox_registry.register("lead-1")
    mailbox_registry.put("lead-1", InboxMsg(kind="message", text="follow-up"))
    mailbox_registry.put("lead-1", InboxMsg(kind="shutdown"))

    await asyncio.wait_for(
        orch.run_actor_loop(
            session_id="lead-1",
            initial_prompt="initial brief",
            role="lead",
            task_id="t1",
            project_id="w1",
        ),
        timeout=2.0,
    )

    # Turn 1 used the brief; turn 2 used the follow-up; shutdown ended the loop.
    assert prompts == ["initial brief", "follow-up"]
    assert finalized == [("lead-1", "idle")]
    # Inbox cleaned up.
    assert mailbox_registry.is_registered("lead-1") is False


async def test_member_loop_notifies_lead_and_self_reaps_on_ttl() -> None:
    """Member loop notifies its lead after each turn, then reaps on idle TTL."""
    orch = TaskOrchestrator()
    notified: list[tuple[str, str]] = []
    finalized: list[str] = []

    async def fake_turn(session_id: str, content: str) -> str:
        return "idle"

    async def fake_notify(session_id: str, status: str) -> None:
        notified.append((session_id, status))

    async def fake_finalize(**kwargs: object) -> None:
        finalized.append(str(kwargs["session_id"]))

    orch._run_turn_with_sink = fake_turn  # type: ignore[method-assign]
    orch._notify_lead_member_idle = fake_notify  # type: ignore[method-assign]
    orch._finalize_actor = fake_finalize  # type: ignore[method-assign]

    # No messages arrive → the member reaps via the (tiny) idle TTL.
    await asyncio.wait_for(
        orch.run_actor_loop(
            session_id="mem-1",
            initial_prompt="do the thing",
            role="subtask",
            task_id="t1",
            project_id="w1",
            idle_ttl=0.05,
        ),
        timeout=2.0,
    )

    assert notified == [("mem-1", "idle")]
    assert finalized == ["mem-1"]


async def test_terminal_turn_status_breaks_loop_immediately() -> None:
    orch = TaskOrchestrator()
    turns = 0

    async def fake_turn(session_id: str, content: str) -> str:
        nonlocal turns
        turns += 1
        return "terminated"

    async def fake_finalize(**kwargs: object) -> None:
        return None

    orch._run_turn_with_sink = fake_turn  # type: ignore[method-assign]
    orch._finalize_actor = fake_finalize  # type: ignore[method-assign]

    await asyncio.wait_for(
        orch.run_actor_loop(
            session_id="lead-x",
            initial_prompt="brief",
            role="lead",
            task_id="t1",
            project_id="w1",
        ),
        timeout=2.0,
    )
    # A terminal status must stop the loop after a single turn (no mailbox wait).
    assert turns == 1


# ---------------------------------------------------------------------------
# finish_task broadcast
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Credential pre-flight (_credential_gap)
# ---------------------------------------------------------------------------


def test_resolved_provider_has_no_gap() -> None:
    # A resolved model_provider is the single source of truth for credentials.
    sess = SimpleNamespace(model_provider=object(), runtime_provider="claude_agent")
    assert asyncio.run(_credential_gap(sess, "writer")) is None


def test_no_model_provider_reports_gap() -> None:
    # No resolved provider → clear reason (no env sniffing — creds are funnelled
    # through the provider system per backend/CLAUDE.md).
    sess = SimpleNamespace(model_provider=None, runtime_provider="claude_agent")
    gap = asyncio.run(_credential_gap(sess, "股票分析大师"))
    assert gap is not None
    assert "股票分析大师" in gap
    assert "model provider" in gap


# ---------------------------------------------------------------------------
# v2.1 — shared project cwd + mtime artifact attribution
# ---------------------------------------------------------------------------


def test_member_run_dir_defaults_to_project_cwd() -> None:
    from pathlib import Path

    # shared (default) and legacy "isolated" both → the project cwd itself.
    assert _member_run_dir("/proj", "t1", 1, "shared") == Path("/proj")
    assert _member_run_dir("/proj", "t1", 1, "isolated") == Path("/proj")


def test_build_member_session_injects_skill_scoping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under shared cwd, the member's bound skills are scoped via prompt."""
    from types import SimpleNamespace

    from valuz_agent.adapters import agent_resolver

    fake_agent = _fake_agent_config(
        id="kernel-agent-1",
        name="writer",
        instructions="be a writer",
        model="mimo-v2.5-pro",
        runtime_provider="claude_agent",
        skills=["skill-alpha", "skill-beta"],
        mcp_servers=(),
        permission_mode="full_access",
        metadata={},
    )
    fake_members = SimpleNamespace(get=_async_member_get())
    monkeypatch.setattr(
        agent_resolver, "_member_agent_config", _as_async(lambda _member, _ds: fake_agent)
    )
    # Hermetic: don't resolve skill slugs against the real skill-index DB — this
    # test only asserts the prompt-scoping block (built from the agent's own
    # ``skills`` list), not the materialised paths.
    monkeypatch.setattr(
        agent_resolver, "resolve_skill_slugs_to_paths", _as_async(lambda *a, **k: [])
    )

    session = asyncio.run(
        agent_resolver.build_member_session(
            project_id="w1",
            agent_slug="writer",
            members=fake_members,  # type: ignore[arg-type]
            is_lead=False,
            task_id="t1",
            run_dir="/proj",  # shared project cwd
            brief="## Goal\n\nwrite a file",
        )
    )
    assert session is not None
    assert session.cwd == "/proj"
    assert "## Your skills" in session.instructions
    assert "skill-alpha" in session.instructions
    assert "skill-beta" in session.instructions
    # Own skills are scoped: everything else in the cwd is to be ignored
    # (the always-on baseline skills are surfaced separately as "Shared").
    assert "Ignore any other skills" in session.instructions


def test_build_member_session_carries_agent_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent's reasoning-effort budget flows into session.model_settings."""
    from types import SimpleNamespace

    from valuz_agent.adapters import agent_resolver

    fake_agent = _fake_agent_config(
        id="kernel-agent-1",
        name="writer",
        instructions="be a writer",
        model="mimo-v2.5-pro",
        runtime_provider="claude_agent",
        skills=(),
        mcp_servers=(),
        permission_mode="full_access",
        effort="xhigh",
        metadata={},
    )
    fake_members = SimpleNamespace(get=_async_member_get())
    monkeypatch.setattr(
        agent_resolver, "_member_agent_config", _as_async(lambda _member, _ds: fake_agent)
    )

    session = asyncio.run(
        agent_resolver.build_member_session(
            project_id="w1",
            agent_slug="writer",
            members=fake_members,  # type: ignore[arg-type]
            is_lead=False,
            task_id="t1",
            run_dir="/proj",
            brief="## Goal\n\nwrite a file",
        )
    )
    assert session is not None
    assert session.model_settings is not None
    assert session.model_settings.effort == "xhigh"


def test_build_member_session_no_effort_leaves_model_settings_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No agent-level effort means model_settings stays unset (SDK default)."""
    from types import SimpleNamespace

    from valuz_agent.adapters import agent_resolver

    fake_agent = _fake_agent_config(
        id="kernel-agent-1",
        name="writer",
        instructions="be a writer",
        model="mimo-v2.5-pro",
        runtime_provider="claude_agent",
        skills=(),
        mcp_servers=(),
        permission_mode="full_access",
        effort=None,
        metadata={},
    )
    fake_members = SimpleNamespace(get=_async_member_get())
    monkeypatch.setattr(
        agent_resolver, "_member_agent_config", _as_async(lambda _member, _ds: fake_agent)
    )

    session = asyncio.run(
        agent_resolver.build_member_session(
            project_id="w1",
            agent_slug="writer",
            members=fake_members,  # type: ignore[arg-type]
            is_lead=False,
            task_id="t1",
            run_dir="/proj",
            brief="## Goal\n\nwrite a file",
        )
    )
    assert session is not None
    assert session.model_settings is None


def _fake_goal_mode_setup(monkeypatch: pytest.MonkeyPatch, runtime_provider: str) -> object:
    """Build a build_member_session call with a fake agent on ``runtime_provider``."""
    from types import SimpleNamespace

    from valuz_agent.adapters import agent_resolver

    fake_agent = _fake_agent_config(
        id="kernel-agent-1",
        name="writer",
        instructions="be a writer",
        model="mimo-v2.5-pro",
        runtime_provider=runtime_provider,
        skills=(),
        mcp_servers=(),
        permission_mode="full_access",
        effort=None,
        metadata={},
    )
    fake_members = SimpleNamespace(get=_async_member_get())
    monkeypatch.setattr(
        agent_resolver, "_member_agent_config", _as_async(lambda _member, _ds: fake_agent)
    )
    return agent_resolver, fake_members


def test_build_member_session_sets_goal_mode_for_claude_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should set session.mode='goal' when goal_mode=True on a claude_agent."""
    agent_resolver, fake_members = _fake_goal_mode_setup(monkeypatch, "claude_agent")
    session = asyncio.run(
        agent_resolver.build_member_session(
            project_id="w1",
            agent_slug="writer",
            members=fake_members,  # type: ignore[arg-type]
            is_lead=False,
            task_id="t1",
            run_dir="/proj",
            brief="## Goal\n\nwrite a file",
            goal_mode=True,
        )
    )
    assert session is not None
    assert session.mode == "goal"


def test_build_member_session_goal_mode_falls_back_to_default_for_deepagents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should leave session.mode='default' for deepagents (no native goal mode)."""
    agent_resolver, fake_members = _fake_goal_mode_setup(monkeypatch, "deepagents")
    session = asyncio.run(
        agent_resolver.build_member_session(
            project_id="w1",
            agent_slug="writer",
            members=fake_members,  # type: ignore[arg-type]
            is_lead=False,
            task_id="t1",
            run_dir="/proj",
            brief="## Goal\n\nwrite a file",
            goal_mode=True,
        )
    )
    assert session is not None
    assert session.mode == "default"


def test_build_member_session_default_when_goal_mode_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Should leave session.mode='default' when goal_mode is not requested."""
    agent_resolver, fake_members = _fake_goal_mode_setup(monkeypatch, "claude_agent")
    session = asyncio.run(
        agent_resolver.build_member_session(
            project_id="w1",
            agent_slug="writer",
            members=fake_members,  # type: ignore[arg-type]
            is_lead=False,
            task_id="t1",
            run_dir="/proj",
            brief="## Goal\n\nwrite a file",
        )
    )
    assert session is not None
    assert session.mode == "default"


async def test_collect_manifest_attributes_by_mtime(tmp_path: object) -> None:
    import os
    from pathlib import Path

    d = Path(str(tmp_path))
    old = d / "preexisting.txt"
    old.write_text("old")
    os.utime(old, (1000.0, 1000.0))  # mtime well before dispatch
    new = d / "member_output.txt"
    new.write_text("new")
    os.utime(new, (5000.0, 5000.0))  # mtime after dispatch

    # since_epoch between the two → only the member's post-dispatch file.
    m = await collect_manifest("s1", d, "idle", since_epoch=3000.0)
    paths = [a["path"] for a in m["artifacts"]]
    assert str(new) in paths
    assert str(old) not in paths

    # since_epoch=0 → include everything (worktree / private-dir behaviour).
    m_all = await collect_manifest("s1", d, "idle", since_epoch=0.0)
    paths_all = [a["path"] for a in m_all["artifacts"]]
    assert str(old) in paths_all and str(new) in paths_all


def test_broadcast_shutdown_signals_live_members() -> None:
    orch = TaskOrchestrator()
    orch._members.set_members("t1", {"m1", "m2"})
    mailbox_registry.register("m1")
    mailbox_registry.register("m2")

    orch._broadcast_shutdown("t1")

    assert mailbox_registry._boxes["m1"].get_nowait().kind == "shutdown"
    assert mailbox_registry._boxes["m2"].get_nowait().kind == "shutdown"
    # Task entry cleared so it cannot be broadcast to twice.
    assert not orch._members.live_members("t1")

    mailbox_registry.unregister("m1")
    mailbox_registry.unregister("m2")


# ---------------------------------------------------------------------------
# v3 — create_task launcher (M10 附录 E)
# ---------------------------------------------------------------------------


def test_ensure_orchestration_tools_adds_create_task() -> None:
    """A bare agent gains the create_task ToolDef; re-applying is a no-op."""
    from src.core import AgentConfig  # type: ignore[import-not-found]

    from valuz_agent.modules.tasks.dispatch_mcp import (
        CREATE_TASK_TOOL_NAME,
        ensure_orchestration_tools_on_agent,
    )

    bare = AgentConfig(id="a1", name="a1", tools=())
    patched = ensure_orchestration_tools_on_agent(bare)
    assert patched is not bare
    assert CREATE_TASK_TOOL_NAME in {t.name for t in patched.tools}

    # Idempotent: already-present → same object back.
    again = ensure_orchestration_tools_on_agent(patched)
    assert again is patched


def test_create_task_gate_rejects_task_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lead/subtask session may not spawn nested tasks; missing ws_id fails."""
    from src.core import ToolResult  # type: ignore[import-not-found]

    from valuz_agent.modules.tasks import dispatch_mcp

    def _sess(valuz: dict) -> SimpleNamespace:
        return SimpleNamespace(metadata={"valuz": valuz})

    ctx = SimpleNamespace(session_id="s1")

    # run_kind="lead" → rejected before any DB lookup.
    monkeypatch.setattr(
        dispatch_mcp.kernel_client,
        "get_session",
        _as_async(lambda _uid, _sid: _sess({"run_kind": "lead", "project_id": "w1"})),
    )
    res = asyncio.run(dispatch_mcp._check_orchestration_gate(ctx))  # type: ignore[arg-type]
    assert isinstance(res, ToolResult) and res.is_error

    # run_kind="subtask" → rejected.
    monkeypatch.setattr(
        dispatch_mcp.kernel_client,
        "get_session",
        _as_async(lambda _uid, _sid: _sess({"run_kind": "subtask", "project_id": "w1"})),
    )
    res = asyncio.run(dispatch_mcp._check_orchestration_gate(ctx))  # type: ignore[arg-type]
    assert isinstance(res, ToolResult) and res.is_error

    # plain conversation but no project_id → rejected.
    monkeypatch.setattr(
        dispatch_mcp.kernel_client,
        "get_session",
        _as_async(lambda _uid, _sid: _sess({"agent_slug": "x"})),
    )
    res = asyncio.run(dispatch_mcp._check_orchestration_gate(ctx))  # type: ignore[arg-type]
    assert isinstance(res, ToolResult) and res.is_error


def test_strip_dispatch_tools_removes_lead_only() -> None:
    """strip_dispatch_tools drops dispatch/finish_task but keeps create_task."""
    from src.core import AgentConfig  # type: ignore[import-not-found]

    from valuz_agent.modules.tasks.dispatch_mcp import (
        CREATE_TASK_TOOL_DECLARATION,
        DISPATCH_TOOL_DECLARATION,
        FINISH_TASK_TOOL_DECLARATION,
        strip_dispatch_tools,
    )

    agent = AgentConfig(
        id="a1",
        name="a1",
        tools=(
            DISPATCH_TOOL_DECLARATION,
            FINISH_TASK_TOOL_DECLARATION,
            CREATE_TASK_TOOL_DECLARATION,
        ),
    )
    stripped = strip_dispatch_tools(agent)
    names = {t.name for t in stripped.tools}
    assert "dispatch" not in names
    assert "finish_task" not in names
    assert "create_task" in names

    again = strip_dispatch_tools(stripped)
    assert again is stripped


async def test_materialize_lead_agent_builds_clone_without_tool_decls() -> None:
    """The lead clone keeps its deterministic identity stamp but carries no
    tool declarations — the dispatch surface rides the lead session's
    ``harness`` MCP entry (lead toolset of the host toolkit server)."""
    from src.core import AgentConfig  # type: ignore[import-not-found]

    from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator

    base = AgentConfig(id="base1", name="lead", tools=())
    orch = TaskOrchestrator()
    clone = await orch._materialize_lead_agent(base, dispatch_mode="async")
    assert clone.id == "base1__lead__async"
    assert tuple(clone.tools or ()) == ()


def test_send_to_member_rejects_cross_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_to_member refuses a target whose run belongs to a different task."""
    import asyncio as _asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from valuz_agent.modules.tasks import messaging

    other_run = SimpleNamespace(task_id="OTHER", project_id="w1")

    async def _get_run(_sid):
        return other_run

    monkeypatch.setattr(
        messaging,
        "TaskSessionDatastore",
        lambda _db: SimpleNamespace(get_run=_get_run),
    )

    @asynccontextmanager
    async def _fake_uow(*_a, **_k):
        yield SimpleNamespace()

    monkeypatch.setattr(messaging, "async_unit_of_work", _fake_uow)

    res = _asyncio.run(
        messaging.send_to_member(
            from_session_id="lead-T1",
            to_session_id="member-of-OTHER",
            text="hi",
            project_id="w1",
            task_id="T1",
        )
    )
    assert res["delivered"] is False
    assert "not a member of this task" in res["error"]


async def test_toolset_partition_matches_declaration_sets() -> None:
    """The host toolkit MCP server's toolsets are partitioned by the
    declaration name sets: ``base`` serves the conversation surface
    (launchers + observability + chat-plan, VALUZ-CHATPLAN S2), ``lead``
    serves the dispatch surface. The lead clone itself carries no tool
    declarations — its surface rides the session's ``harness`` MCP entry."""
    from src.core import AgentConfig  # type: ignore[import-not-found]

    from valuz_agent.modules.tasks.dispatch_mcp import (
        DISPATCH_TOOL_DECLARATIONS,
        ORCHESTRATION_TOOL_DECLARATIONS,
    )
    from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator

    names = {d.name for d in ORCHESTRATION_TOOL_DECLARATIONS}
    # Launcher + observability + VALUZ-CHATPLAN draft-mode tools all surfaced
    # on conversation (base-toolset) sessions so the chat-as-control-surface
    # flow works.
    assert names == {
        "list_members",
        "create_task",
        "list_tasks",
        "get_task",
        # VALUZ-CHATPLAN S2:
        "draft_task",
        "commit_task",
        "abandon_task",
        "plan_task",
        "modify_plan",
        "get_plan",
        # VALUZ-CHATPLAN S4:
        "inject_into_task",
        # Chat-side resume for paused/blocked tasks:
        "resume_task",
    }

    # Lead toolset: dispatch surface incl. the plan-write tools.
    lead_names = {d.name for d in DISPATCH_TOOL_DECLARATIONS}
    for kept in (
        "dispatch",
        "await_members",
        "send",
        "finish_task",
        "list_members",
        "review_subtask",
        "plan_task",
        "modify_plan",
        "get_plan",
    ):
        assert kept in lead_names, f"{kept} should be in the lead toolset"
    # Launcher / draft-mode tools are NOT in the lead toolset:
    for stripped in ("create_task", "list_tasks", "draft_task", "commit_task"):
        assert stripped not in lead_names, f"{stripped} should not be in lead toolset"

    # The clone is a pure identity stamp.
    clone = await TaskOrchestrator()._materialize_lead_agent(
        AgentConfig(id="a", name="a", tools=()), dispatch_mode="async"
    )
    assert tuple(clone.tools or ()) == ()


def test_build_member_session_carries_effort_for_deepagents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Effort is a per-agent opt-in and travels through even on deepagents.

    Most openai-compat backends accept reasoning_effort (mimo /v1 does); the
    deepseek-v4-flash 400 is a per-model constraint the user clears on that
    agent, not a reason to strip effort runtime-wide.
    """
    from types import SimpleNamespace

    from valuz_agent.adapters import agent_resolver

    fake_agent = _fake_agent_config(
        id="da-1",
        name="da-writer",
        instructions="be brief",
        model="mimo-v2.5-pro",
        runtime_provider="deepagents",
        skills=(),
        mcp_servers=(),
        permission_mode="full_access",
        effort="high",
        metadata={},
    )
    fake_members = SimpleNamespace(get=_async_member_get("da-1"))
    monkeypatch.setattr(
        agent_resolver, "_member_agent_config", _as_async(lambda _member, _ds: fake_agent)
    )

    session = asyncio.run(
        agent_resolver.build_member_session(
            project_id="w1",
            agent_slug="quickbot",
            members=fake_members,  # type: ignore[arg-type]
            is_lead=False,
            task_id="t1",
            run_dir="/proj",
            brief="goal",
        )
    )
    assert session is not None
    assert session.model_settings is not None
    assert session.model_settings.effort == "high"


# ---------------------------------------------------------------------------
# await_member_results (v0.14 — turn-内并行收集)
# ---------------------------------------------------------------------------


def _patch_await_deps(monkeypatch, key_by_session: dict[str, str]):
    """Stub the DB touches in await_member_results: get_run + _mark_in_review.

    ``await_member_results`` / ``_heartbeat_pending`` live in
    ``tasks/coordination.py`` (ADR-023 Step 3b); the orchestrator delegates to
    it, so stub the coordination module's DB seams.
    """
    from contextlib import asynccontextmanager

    from valuz_agent.modules.tasks import coordination as coord_mod

    class _FakeRunDs:
        def __init__(self, _db):
            pass

        async def get_run(self, sid):
            sk = key_by_session.get(sid)
            return SimpleNamespace(subtask_key=sk) if sk else None

        async def list_runs(self, _user_id, _task_id):
            # No in-flight runs in these unit tests → heartbeat is a no-op.
            return []

    @asynccontextmanager
    async def _fake_uow(*_a, **_k):
        yield SimpleNamespace()

    monkeypatch.setattr(coord_mod, "async_unit_of_work", _fake_uow)
    monkeypatch.setattr(coord_mod, "TaskSessionDatastore", _FakeRunDs)


@pytest.mark.asyncio
async def test_await_members_all_returns_when_all_keys_done(monkeypatch) -> None:
    """mode='all' with explicit keys returns once every key has a member_done."""
    _patch_await_deps(monkeypatch, {"sA": "A", "sB": "B"})
    orch = TaskOrchestrator()

    async def _noop_mark(**_kw):
        return None

    monkeypatch.setattr(planning, "mark_in_review", _noop_mark)
    lead = "lead-await-1"
    mailbox_registry.register(lead)
    try:
        mailbox_registry.put(
            lead, InboxMsg(kind="member_done", from_session="sA", payload={"summary": "a"})
        )
        mailbox_registry.put(
            lead, InboxMsg(kind="member_done", from_session="sB", payload={"summary": "b"})
        )
        res = await orch.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["A", "B"],
            mode="all",
            timeout_s=2,
        )
        assert res["collected"] == 2
        assert res["pending"] == []
        assert {r["subtask_key"] for r in res["results"]} == {"A", "B"}
    finally:
        mailbox_registry.unregister(lead)


@pytest.mark.asyncio
async def test_await_members_any_returns_on_first(monkeypatch) -> None:
    """mode='any' returns after the first member_done even if others pending."""
    _patch_await_deps(monkeypatch, {"sA": "A", "sB": "B"})
    orch = TaskOrchestrator()

    async def _noop_mark(**_kw):
        return None

    monkeypatch.setattr(planning, "mark_in_review", _noop_mark)
    lead = "lead-await-2"
    mailbox_registry.register(lead)
    try:
        mailbox_registry.put(lead, InboxMsg(kind="member_done", from_session="sA", payload={}))
        res = await orch.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["A", "B"],
            mode="any",
            timeout_s=2,
        )
        assert res["collected"] == 1
        assert res["results"][0]["subtask_key"] == "A"
    finally:
        mailbox_registry.unregister(lead)


@pytest.mark.asyncio
async def test_await_members_timeout_returns_partial_with_pending(monkeypatch) -> None:
    """On timeout, returns what arrived + lists the still-pending keys."""
    _patch_await_deps(monkeypatch, {"sA": "A"})
    orch = TaskOrchestrator()

    async def _noop_mark(**_kw):
        return None

    monkeypatch.setattr(planning, "mark_in_review", _noop_mark)
    lead = "lead-await-3"
    mailbox_registry.register(lead)
    try:
        mailbox_registry.put(lead, InboxMsg(kind="member_done", from_session="sA", payload={}))
        res = await orch.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["A", "B"],
            mode="all",
            timeout_s=0.2,
        )
        assert res["collected"] == 1
        assert res["pending"] == ["B"]
        assert res["timed_out"] is True
    finally:
        mailbox_registry.unregister(lead)

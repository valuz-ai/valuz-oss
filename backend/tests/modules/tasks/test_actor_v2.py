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
from valuz_agent.modules.tasks.manifest import collect_manifest
from valuz_agent.modules.tasks.orchestrator import TaskOrchestrator
from valuz_agent.modules.tasks.resolution import _credential_gap

LOCAL_USER_ID = "local-test-owner"


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
    assert reg.put("s1", InboxMsg(kind="text", text="hello")) is True
    msg = await reg.get("s1", timeout=1.0)
    assert msg.kind == "text"
    assert msg.text == "hello"


async def test_put_to_unregistered_session_is_noop() -> None:
    reg = MailboxRegistry()
    assert reg.put("ghost", InboxMsg(kind="text", text="x")) is False


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


async def test_lead_loop_runs_turns_until_shutdown(db_factory) -> None:
    """Lead loop: initial turn, wakes on a message turn, breaks on shutdown."""
    orch = TaskOrchestrator()
    prompts: list[str] = []
    finalized: list[tuple[str, str]] = []

    async def fake_turn(session_id: str, content: str, user_id: str | None = None) -> str:
        prompts.append(content)
        return "idle"

    async def fake_finalize(**kwargs: object) -> None:
        finalized.append((str(kwargs["session_id"]), str(kwargs["final_status"])))

    orch.actor.run_turn = fake_turn  # type: ignore[method-assign]
    orch.finalization.finalize_actor = fake_finalize  # type: ignore[method-assign]

    # Pre-load the inbox: a follow-up, then a shutdown. register() in the loop
    # is idempotent so these survive.
    mailbox_registry.register("lead-1")
    mailbox_registry.put("lead-1", InboxMsg(kind="text", text="follow-up"))
    mailbox_registry.put("lead-1", InboxMsg(kind="shutdown"))

    await asyncio.wait_for(
        orch.actor.run_actor_loop(
            session_id="lead-1",
            initial_prompt="initial brief",
            role="lead",
            task_id="t1",
            project_id="w1",
            user_id=LOCAL_USER_ID,
        ),
        timeout=2.0,
    )

    # Turn 1 used the brief; turn 2 used the follow-up; shutdown ended the loop.
    assert prompts == ["initial brief", "follow-up"]
    assert finalized == [("lead-1", "idle")]
    # The box outlives the loop by design: nothing drops one on the way out
    # any more. That race — a stale loop's teardown popping the box a resumed
    # loop was reading — is why the claim token existed, and it went away with
    # ownership. What matters is that the loop left nothing queued behind it.
    assert not mailbox_registry.has_pending("lead-1")


async def test_member_loop_notifies_lead_and_self_reaps_on_ttl(db_factory) -> None:
    """Member loop notifies its lead after each turn, then reaps on idle TTL."""
    orch = TaskOrchestrator()
    notified: list[tuple[str, str]] = []
    finalized: list[str] = []

    async def fake_turn(session_id: str, content: str, user_id: str | None = None) -> str:
        return "idle"

    async def fake_notify(session_id: str, status: str, user_id: str | None = None) -> None:
        notified.append((session_id, status))

    async def fake_finalize(**kwargs: object) -> None:
        finalized.append(str(kwargs["session_id"]))

    orch.actor.run_turn = fake_turn  # type: ignore[method-assign]
    orch.coordination.notify_lead_member_idle = fake_notify  # type: ignore[method-assign]
    orch.finalization.finalize_actor = fake_finalize  # type: ignore[method-assign]

    # No messages arrive → the member reaps via the (tiny) idle TTL.
    await asyncio.wait_for(
        orch.actor.run_actor_loop(
            session_id="mem-1",
            initial_prompt="do the thing",
            role="subtask",
            task_id="t1",
            project_id="w1",
            idle_ttl=0.05,
            user_id=LOCAL_USER_ID,
        ),
        timeout=2.0,
    )

    assert notified == [("mem-1", "idle")]
    assert finalized == ["mem-1"]


async def test_terminal_turn_status_breaks_loop_immediately(db_factory) -> None:
    orch = TaskOrchestrator()
    turns = 0

    async def fake_turn(session_id: str, content: str, user_id: str | None = None) -> str:
        nonlocal turns
        turns += 1
        return "terminated"

    async def fake_finalize(**kwargs: object) -> None:
        return None

    orch.actor.run_turn = fake_turn  # type: ignore[method-assign]
    orch.finalization.finalize_actor = fake_finalize  # type: ignore[method-assign]

    await asyncio.wait_for(
        orch.actor.run_actor_loop(
            session_id="lead-x",
            initial_prompt="brief",
            role="lead",
            task_id="t1",
            project_id="w1",
            user_id=LOCAL_USER_ID,
        ),
        timeout=2.0,
    )
    # A terminal status must stop the loop after a single turn (no mailbox wait).
    assert turns == 1


# ---------------------------------------------------------------------------
# _resolve_turn_status — classify from the authoritative run_turn ``message``
# ---------------------------------------------------------------------------


def test_resolve_turn_status_elevates_error_stop_reason() -> None:
    """A turn that failed at the API transport layer (SDK
    ResultMessage(is_error=True)) returns normally with an ``Error`` stop_reason
    on the ``message``. Elevate to 'terminated' so the loop breaks and the
    lead/member finalize treat it as a failure. Accept dict + attr stop_reason."""
    from valuz_agent.modules.tasks.actor_runner import _resolve_turn_status

    err_dict = SimpleNamespace(status="errored", stop_reason={"type": "error"})
    err_attr = SimpleNamespace(status="errored", stop_reason=SimpleNamespace(type="error"))
    assert _resolve_turn_status(err_dict) == "terminated"
    assert _resolve_turn_status(err_attr) == "terminated"


def test_resolve_turn_status_clean_turn_is_idle() -> None:
    from valuz_agent.modules.tasks.actor_runner import _resolve_turn_status

    assert _resolve_turn_status(None) == "idle"
    # Clean end_turn.
    assert _resolve_turn_status(SimpleNamespace(stop_reason={"type": "end_turn"})) == "idle"
    # No stop_reason on the message.
    assert _resolve_turn_status(SimpleNamespace(stop_reason=None)) == "idle"


def test_resolve_turn_status_interrupt_categories() -> None:
    """A cancellation error (user_interrupt / interrupted) is user/host intent,
    not a failure → 'interrupted'. Branch on ``stop_reason.category`` because
    ``message.status`` collapses a host 'interrupted' into 'errored'."""
    from valuz_agent.modules.tasks.actor_runner import _resolve_turn_status

    assert (
        _resolve_turn_status(
            SimpleNamespace(
                status="cancelled", stop_reason={"type": "error", "category": "user_interrupt"}
            )
        )
        == "interrupted"
    )
    # A host 'interrupted' surfaces as message.status='errored', but must still
    # resolve 'interrupted' off the category — NOT 'terminated'.
    assert (
        _resolve_turn_status(
            SimpleNamespace(
                status="errored", stop_reason={"type": "error", "category": "interrupted"}
            )
        )
        == "interrupted"
    )


def test_resolve_turn_status_ignores_a_stale_running_readback() -> None:
    """Regression: the classifier reads the authoritative ``message``, so a
    ``running`` that the lagging durable mirror might have shown is structurally
    impossible here — the message of a resolved turn is never 'running', and the
    status field is not even consulted. This is what stops the finalize clobber
    that stranded sessions at status='running'."""
    from valuz_agent.modules.tasks.actor_runner import _resolve_turn_status

    # Even if a caller handed a message-like object carrying a bogus
    # status='running', only the (clean) stop_reason drives the result → idle.
    assert (
        _resolve_turn_status(SimpleNamespace(status="running", stop_reason={"type": "end_turn"}))
        == "idle"
    )


# ---------------------------------------------------------------------------
# finish_task broadcast
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Credential pre-flight (_credential_gap)
# ---------------------------------------------------------------------------


def test_resolved_provider_has_no_gap() -> None:
    # A resolved model_provider is the single source of truth for credentials.
    sess = SimpleNamespace(model_provider=object(), runtime_provider="claude_agent")
    assert asyncio.run(_credential_gap(sess, "writer", user_id=LOCAL_USER_ID)) is None


def test_no_model_provider_reports_gap() -> None:
    # No resolved provider → clear reason (no env sniffing — creds are funnelled
    # through the provider system per backend/CLAUDE.md).
    sess = SimpleNamespace(model_provider=None, runtime_provider="claude_agent")
    gap = asyncio.run(_credential_gap(sess, "股票分析大师", user_id=LOCAL_USER_ID))
    assert gap is not None
    assert "股票分析大师" in gap
    assert "model provider" in gap


# ---------------------------------------------------------------------------
# v2.1 — shared project cwd + mtime artifact attribution
# ---------------------------------------------------------------------------


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
        agent_resolver, "_member_agent_config", _as_async(lambda _member, _ds, **_kw: fake_agent)
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


def test_build_member_session_freezes_memory_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The frozen memory snapshot lands in the session instructions at create
    time as a ``<memory>`` section (memory-system-design §8) — lead and members
    share the project memory, one copy per session, never per turn."""
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
        metadata={},
    )
    fake_members = SimpleNamespace(get=_async_member_get())
    monkeypatch.setattr(
        agent_resolver, "_member_agent_config", _as_async(lambda _member, _ds, **_kw: fake_agent)
    )
    seen: list[dict[str, object]] = []

    async def fake_memory_block(**kwargs: object) -> str:
        seen.append(kwargs)
        return "This is recalled memory — remembered context.\n\ntracks ACME earnings"

    monkeypatch.setattr(agent_resolver, "memory_instructions_block", fake_memory_block)

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
    assert "<memory>" in session.instructions
    assert "tracks ACME earnings" in session.instructions
    # Exactly one copy, and the render was asked for THIS project's memory.
    assert session.instructions.count("tracks ACME earnings") == 1
    assert seen and seen[0].get("project_id") == "w1"


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
        agent_resolver, "_member_agent_config", _as_async(lambda _member, _ds, **_kw: fake_agent)
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
        agent_resolver, "_member_agent_config", _as_async(lambda _member, _ds, **_kw: fake_agent)
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
        agent_resolver, "_member_agent_config", _as_async(lambda _member, _ds, **_kw: fake_agent)
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
    m = await collect_manifest("s1", d, "idle", since_epoch=3000.0, user_id=LOCAL_USER_ID)
    paths = [a["path"] for a in m["artifacts"]]
    assert str(new) in paths
    assert str(old) not in paths

    # since_epoch=0 → include everything (worktree / private-dir behaviour).
    m_all = await collect_manifest("s1", d, "idle", since_epoch=0.0, user_id=LOCAL_USER_ID)
    paths_all = [a["path"] for a in m_all["artifacts"]]
    assert str(old) in paths_all and str(new) in paths_all


def test_broadcast_shutdown_signals_live_members() -> None:
    orch = TaskOrchestrator()
    orch._members.set_members("t1", {"m1", "m2"})
    mailbox_registry.register("m1")
    mailbox_registry.register("m2")

    orch.coordination.broadcast_shutdown("t1")

    assert mailbox_registry._boxes["m1"].get_nowait().kind == "shutdown"
    assert mailbox_registry._boxes["m2"].get_nowait().kind == "shutdown"
    # Task entry cleared so it cannot be broadcast to twice.
    assert not orch._members.live_members("t1")

    mailbox_registry.unregister("m1")
    mailbox_registry.unregister("m2")


# ---------------------------------------------------------------------------
# v3 — create_task launcher (M10 附录 E)
# ---------------------------------------------------------------------------


def test_create_task_is_on_the_chat_toolset() -> None:
    """A project conversation must be able to launch a task.

    (Was ``test_ensure_orchestration_tools_adds_create_task``, which exercised
    the retired "declare tools on the AgentConfig" mechanism. Tool audience is
    now decided once by the two declaration tuples, which ``boot/steps.py``
    turns into the toolkit MCP server's base/lead toolsets — so asserting
    membership IS asserting what reaches a chat session.)
    """
    from valuz_agent.modules.tasks.tools.declarations import (
        CREATE_TASK_TOOL_NAME,
        ORCHESTRATION_TOOL_DECLARATIONS,
    )

    assert CREATE_TASK_TOOL_NAME in {d.name for d in ORCHESTRATION_TOOL_DECLARATIONS}


def test_create_task_gate_rejects_task_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lead/subtask session may not spawn nested tasks; missing ws_id fails."""
    from src.core import ToolResult  # type: ignore[import-not-found]

    from valuz_agent.adapters import kernel_client as kc_mod
    from valuz_agent.modules.tasks.tools import handlers as handlers_mod

    def _sess(valuz: dict) -> SimpleNamespace:
        return SimpleNamespace(metadata={"valuz": valuz})

    ctx = SimpleNamespace(session_id="s1", user_id="u1")

    # run_kind="lead" → rejected before any DB lookup.
    monkeypatch.setattr(
        kc_mod,
        "get_session",
        _as_async(lambda _uid, _sid: _sess({"run_kind": "lead", "project_id": "w1"})),
    )
    res = asyncio.run(handlers_mod._check_orchestration_gate(ctx))  # type: ignore[arg-type]
    assert isinstance(res, ToolResult) and res.is_error

    # run_kind="subtask" → rejected.
    monkeypatch.setattr(
        kc_mod,
        "get_session",
        _as_async(lambda _uid, _sid: _sess({"run_kind": "subtask", "project_id": "w1"})),
    )
    res = asyncio.run(handlers_mod._check_orchestration_gate(ctx))  # type: ignore[arg-type]
    assert isinstance(res, ToolResult) and res.is_error

    # plain conversation but no project_id → rejected.
    monkeypatch.setattr(
        kc_mod,
        "get_session",
        _as_async(lambda _uid, _sid: _sess({"agent_slug": "x"})),
    )
    res = asyncio.run(handlers_mod._check_orchestration_gate(ctx))  # type: ignore[arg-type]
    assert isinstance(res, ToolResult) and res.is_error


def test_lead_only_tools_never_reach_the_chat_toolset() -> None:
    """The lead/chat audience partition — the invariant that matters.

    (Was ``test_strip_dispatch_tools_removes_lead_only``, which tested the
    retired AgentConfig-mutating strip helper. The same guarantee now comes
    from the declaration tuples: ``boot/steps.py`` serves
    ORCHESTRATION_TOOL_DECLARATIONS as the toolkit MCP ``base`` toolset and
    DISPATCH_TOOL_DECLARATIONS as ``lead``, so a lead-only tool leaking into
    the chat tuple is exactly the old bug in its current form.)

    Execution-authority tools must be lead-only: a plain conversation that
    could ``dispatch`` or ``finish_task`` would act on a task it does not own.
    The deliberate overlap (list_members + the plan tools, which chat needs for
    draft tasks) is asserted explicitly so widening it stays a conscious act.
    """
    from valuz_agent.modules.tasks.tools.declarations import (
        DISPATCH_TOOL_DECLARATIONS,
        ORCHESTRATION_TOOL_DECLARATIONS,
    )

    lead = {d.name for d in DISPATCH_TOOL_DECLARATIONS}
    chat = {d.name for d in ORCHESTRATION_TOOL_DECLARATIONS}

    assert {
        "dispatch",
        "await_members",
        "send",
        "finish_task",
        "review_subtask",
        "stop_subtask",
        "update_deliverable",
    } <= lead - chat

    # Chat-only: launching / observing / talking to a task from the outside.
    assert {"create_task", "draft_task", "commit_task", "abandon_task"} <= chat - lead

    # The intentional overlap — per-call authority is enforced by tools/gate.py
    # (an active task's plan is lead-only; chat must inject_into_task instead).
    assert lead & chat == {"list_members", "plan_task", "modify_plan", "get_plan"}


async def test_materialize_lead_agent_builds_clone_without_tool_decls() -> None:
    """The lead clone keeps its deterministic identity stamp but carries no
    tool declarations — the dispatch surface rides the lead session's
    ``harness`` MCP entry (lead toolset of the host toolkit server)."""
    from src.core import AgentConfig  # type: ignore[import-not-found]

    from valuz_agent.modules.tasks.resolution import materialize_lead_clone

    clone = materialize_lead_clone(AgentConfig(id="base1", name="lead", tools=()))
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
            user_id=LOCAL_USER_ID,
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

    from valuz_agent.modules.tasks.resolution import materialize_lead_clone
    from valuz_agent.modules.tasks.tools.declarations import (
        DISPATCH_TOOL_DECLARATIONS,
        ORCHESTRATION_TOOL_DECLARATIONS,
    )

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
    clone = materialize_lead_clone(AgentConfig(id="a", name="a", tools=()))
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
        agent_resolver, "_member_agent_config", _as_async(lambda _member, _ds, **_kw: fake_agent)
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
            # A dispatched member has an ``active`` subtask run — model that so
            # await_member_results' precondition guard sees an in-flight member
            # for each simulated key. Keys absent here are treated as "never
            # dispatched" (heartbeat stays a no-op for them).
            return [
                SimpleNamespace(kind="subtask", subtask_key=sk, status="active", session_id=sid)
                for sid, sk in key_by_session.items()
            ]

    class _FakeTaskDs:
        def __init__(self, _db):
            pass

        async def get_task_by_project(self, _user_id, _project_id, _task_id):
            # Plan only feeds target-resolution (keys omitted) and the guard's
            # ready_keys hint; these tests pass keys explicitly and never trip
            # the guard, so an absent task (empty plan) is sufficient.
            return None

    @asynccontextmanager
    async def _fake_uow(*_a, **_k):
        yield SimpleNamespace()

    monkeypatch.setattr(coord_mod, "async_unit_of_work", _fake_uow)
    monkeypatch.setattr(coord_mod, "TaskSessionDatastore", _FakeRunDs)
    monkeypatch.setattr(coord_mod, "TaskDatastore", _FakeTaskDs)


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
        res = await orch.coordination.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["A", "B"],
            mode="all",
            timeout_s=2,
            user_id=LOCAL_USER_ID,
        )
        assert res["collected"] == 2
        assert res["pending"] == []
        assert {r["subtask_key"] for r in res["results"]} == {"A", "B"}
    finally:
        mailbox_registry.unregister(lead)


@pytest.mark.asyncio
async def test_await_members_puts_shutdown_back_for_the_actor_loop(monkeypatch) -> None:
    """Regression: a ``shutdown`` must survive being seen by await_members.

    ``await_member_results`` runs INSIDE the lead's turn and drains the very
    inbox the actor loop reads BETWEEN turns. It used to consume the shutdown
    and just break, so ``finish_task``/``stop_task``'s broadcast never reached
    the loop and the lead kept looping on a halted task. It must re-queue it.
    """
    _patch_await_deps(monkeypatch, {"sA": "A"})
    orch = TaskOrchestrator()

    async def _noop_mark(**_kw):
        return None

    monkeypatch.setattr(planning, "mark_in_review", _noop_mark)
    lead = "lead-await-shutdown"
    mailbox_registry.register(lead)
    try:
        mailbox_registry.put(lead, InboxMsg(kind="shutdown"))
        res = await orch.coordination.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["A"],
            mode="all",
            timeout_s=2,
            user_id=LOCAL_USER_ID,
        )
        # The wait ended immediately with nothing collected...
        assert res["collected"] == 0
        # ...and the shutdown is still queued for the actor loop to act on.
        assert mailbox_registry.has_pending(lead)
        assert (await mailbox_registry.get(lead, timeout=0.1)).kind == "shutdown"
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
        res = await orch.coordination.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["A", "B"],
            mode="any",
            timeout_s=2,
            user_id=LOCAL_USER_ID,
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
        res = await orch.coordination.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["A", "B"],
            mode="all",
            timeout_s=0.2,
            user_id=LOCAL_USER_ID,
        )
        assert res["collected"] == 1
        assert res["pending"] == ["B"]
        assert res["timed_out"] is True
    finally:
        mailbox_registry.unregister(lead)


@pytest.mark.asyncio
async def test_await_members_no_dispatched_returns_immediately(monkeypatch) -> None:
    """Guard: awaiting a key with no in-flight member returns at once, not after
    the full timeout (the "planned but never dispatched, then await" trap)."""
    # No simulated members → list_runs reports nothing active.
    _patch_await_deps(monkeypatch, {})
    orch = TaskOrchestrator()

    async def _noop_mark(**_kw):
        return None

    monkeypatch.setattr(planning, "mark_in_review", _noop_mark)
    lead = "lead-await-guard"
    mailbox_registry.register(lead)
    try:
        loop = asyncio.get_running_loop()
        start = loop.time()
        res = await orch.coordination.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["dev"],
            mode="all",
            timeout_s=30,  # would hang ~30s without the guard
            user_id=LOCAL_USER_ID,
        )
        elapsed = loop.time() - start
        assert elapsed < 1.0  # returned promptly, did not block on the timeout
        assert res["error"] == "no_dispatched_members"
        assert res["collected"] == 0
        assert res["pending"] == ["dev"]
        assert res["timed_out"] is False
    finally:
        mailbox_registry.unregister(lead)


@pytest.mark.asyncio
async def test_await_members_any_running_pending_gets_keep_waiting_hint(monkeypatch) -> None:
    """Pull-gap fix (Plan A): a mode='any' early return with a still-running
    member must carry the ``still_running`` + 'await again' hint — regardless of
    ``timed_out`` (which is only set for mode='all'). Previously the hint was
    gated on ``timed_out`` and never fired here, so the lead got a bare
    ``pending:[k] state:running`` and went silent instead of re-awaiting."""
    from valuz_agent.modules.tasks import coordination as coord_mod

    _patch_await_deps(monkeypatch, {"sA": "A"})

    async def _get_session(_uid, _sid):
        return SimpleNamespace(status="running")  # member genuinely in flight

    monkeypatch.setattr(coord_mod, "data_reader", lambda: SimpleNamespace(get_session=_get_session))
    orch = TaskOrchestrator()
    lead = "lead-await-running"
    mailbox_registry.register(lead)
    try:
        # No member_done queued → the member is still running when the short
        # window closes.
        res = await orch.coordination.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["A"],
            mode="any",
            timeout_s=0.2,
            user_id=LOCAL_USER_ID,
        )
        assert res["collected"] == 0
        assert res["pending"] == ["A"]
        assert res["timed_out"] is False  # mode='any' never sets timed_out
        assert res["still_running"] is True
        assert [p["state"] for p in res["pending_status"]] == ["running"]
        assert "await_members again" in res["hint"]
    finally:
        mailbox_registry.unregister(lead)


@pytest.mark.asyncio
async def test_await_members_clamps_window_to_max(monkeypatch) -> None:
    """A model-supplied timeout_s above _MAX_AWAIT_WINDOW_S is clamped — await
    parks for the window unit, not the requested value. This keeps a single call
    under the codex tool-call ceiling so a healthy wait is never aborted as a
    transport failure. Proven by shrinking the window and passing a huge
    timeout_s: the call must return in ~the window, not in ~9999s."""
    from valuz_agent.modules.tasks import coordination as coord_mod

    _patch_await_deps(monkeypatch, {"sA": "A"})
    monkeypatch.setattr(coord_mod, "_MAX_AWAIT_WINDOW_S", 0.2)  # tiny cap for the test

    async def _get_session(_uid, _sid):
        return SimpleNamespace(status="running")

    monkeypatch.setattr(coord_mod, "data_reader", lambda: SimpleNamespace(get_session=_get_session))
    orch = TaskOrchestrator()
    lead = "lead-await-clamp"
    mailbox_registry.register(lead)
    try:
        loop = asyncio.get_running_loop()
        start = loop.time()
        res = await orch.coordination.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["A"],
            mode="any",
            timeout_s=9999,  # would block ~forever if honored verbatim
            user_id=LOCAL_USER_ID,
        )
        elapsed = loop.time() - start
        assert elapsed < 1.0  # clamped to the 0.2s window, NOT 9999s
        assert res["pending"] == ["A"]
    finally:
        mailbox_registry.unregister(lead)


@pytest.mark.asyncio
async def test_inbox_notice_wrapper_surfaces_queued_member_done() -> None:
    """Pull-gap fix (Plan B): a lead tool called while a member_done sits in the
    mailbox gets an ``inbox_pending`` notice appended (non-consuming peek), so a
    completion that landed in the gap is surfaced at the next tool boundary."""
    import json

    from src.core import ToolResult  # type: ignore[import-not-found]

    from valuz_agent.modules.tasks.tools.handlers import _with_inbox_notice

    async def _inner(_args, _ctx):
        return ToolResult(content=json.dumps({"ok": True}))

    wrapped = _with_inbox_notice(_inner)
    lead = "lead-inbox-notice"
    ctx = SimpleNamespace(session_id=lead, user_id=LOCAL_USER_ID)

    mailbox_registry.register(lead)
    try:
        # Empty inbox → notice absent, envelope untouched.
        empty = await wrapped({}, ctx)
        assert "inbox_pending" not in json.loads(empty.content)

        # A queued member_done → notice appended, but the message is NOT consumed
        # (peek-only: it must still be there for await_members to collect).
        mailbox_registry.put(lead, InboxMsg(kind="member_done", from_session="sA", payload={}))
        got = await wrapped({}, ctx)
        payload = json.loads(got.content)
        assert payload["ok"] is True
        assert payload["inbox_pending"] is True
        assert "await_members" in payload["inbox_hint"]
        assert mailbox_registry.has_pending(lead)  # non-consuming — still queued
    finally:
        mailbox_registry.unregister(lead)


@pytest.mark.asyncio
async def test_inbox_notice_wrapper_leaves_errors_and_plaintext_alone() -> None:
    """The notice wrapper must never mutate an error result or a plain-text
    (non-JSON) envelope, even with mail queued."""
    from src.core import ToolResult  # type: ignore[import-not-found]

    from valuz_agent.modules.tasks.tools.handlers import _with_inbox_notice

    lead = "lead-inbox-passthrough"
    ctx = SimpleNamespace(session_id=lead, user_id=LOCAL_USER_ID)
    mailbox_registry.register(lead)
    mailbox_registry.put(lead, InboxMsg(kind="member_done", from_session="sA", payload={}))
    try:

        async def _err(_args, _ctx):
            return ToolResult(content="boom", is_error=True)

        async def _plain(_args, _ctx):
            return ToolResult(content="Task closed. Do not continue working.")

        err = await _with_inbox_notice(_err)({}, ctx)
        assert err.is_error is True and err.content == "boom"

        plain = await _with_inbox_notice(_plain)({}, ctx)
        assert plain.content == "Task closed. Do not continue working."
    finally:
        mailbox_registry.unregister(lead)


@pytest.mark.asyncio
async def test_a_cross_process_inject_preempts_an_in_flight_member_wait(monkeypatch) -> None:
    """A user instruction must interrupt the lead's wait, from any process.

    This wait runs INSIDE the lead's turn while members run for minutes, and
    the preempt below it exists so the user does not have to wait them out.
    That preempt read the in-process queue only. When delivery moved to the
    durable mailbox the producers stopped writing that queue, so the preempt
    went quiet: the instruction was accepted, stored, and then sat unread until
    every member finished — observed on qa at 97 seconds and counting.
    """
    _patch_await_deps(monkeypatch, {"sA": "A"})
    from valuz_agent.modules.tasks import coordination as coord_mod

    lead = "lead-await-durable"
    drained: list[str] = []

    class _FakeStore:
        @staticmethod
        async def drain(session_id: str):
            # Delivered once, as a real claim would be — a store that kept
            # handing back the same row would spin the lead.
            if drained:
                return []
            drained.append(session_id)
            return [
                InboxMsg(
                    kind="text",
                    text="<user-instruction>add the STAR market</user-instruction>",
                    from_session="chat-sess",
                    origin="user-inject",
                )
            ]

    monkeypatch.setattr(coord_mod, "mailbox_store", _FakeStore)
    orch = TaskOrchestrator()
    mailbox_registry.register(lead)
    try:
        # Nothing in this process's queue and the member never reports: without
        # the durable read this call can only end by timing out.
        res = await orch.coordination.await_member_results(
            lead_session_id=lead,
            project_id="w1",
            task_id="t1",
            keys=["A"],
            mode="all",
            timeout_s=30,
            user_id=LOCAL_USER_ID,
        )
    finally:
        mailbox_registry.unregister(lead)

    assert drained == [lead], "the durable inbox was never consulted"
    assert res.get("preempted_by_inject") is True, (
        "the lead must be released to act on the instruction, not left waiting "
        "for members that may run for minutes"
    )
    assert "add the STAR market" in res["user_inject"]["text"]
    assert res["collected"] == 0, "the member is still in flight; nothing was collected"

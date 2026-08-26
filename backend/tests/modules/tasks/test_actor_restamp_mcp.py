"""Turn primitives converge capabilities THROUGH ``run_turn``, never before it.

Two regressions are pinned here.

1. "A re-launched task lead has no orchestration tools": a lead/member session
   re-driven after a backend restart can carry a stale ``X-Valuz-Internal`` in
   its persisted ``mcp_servers``; the in-process gate 403s and the runtime parks
   the ``harness`` server in needsAuth, hiding dispatch / review_subtask /
   finish_task / await_members / send / get_plan. Every turn-driving primitive
   must re-stamp.

2. "Every external MCP call 401s in a resumed conversation": the re-stamp used
   to run BEFORE the turn's kernel was allocated, so on a scoped (sandbox)
   deployment it only ever reached the durable — the turn's freshly-seeded
   kernel kept the fossil credentials. The refresh must therefore be handed to
   ``kernel_client.run_turn`` as its ``pre_turn`` hook, which runs it after
   allocation. A primitive that awaits a refresher itself is the bug.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.modules.sessions import pre_turn, turn_driver
from valuz_agent.modules.tasks import actor_runner

LOCAL_USER_ID = "local-test-owner"


def _as_async(fn: Any) -> Any:
    async def _f(*a: Any, **k: Any) -> Any:
        return fn(*a, **k)

    return _f


class _Bus:
    def publish(self, *a: Any, **k: Any) -> None:  # event-bus stub
        pass


# ── restamp_always_on_mcp ───────────────────────────────────────────────


def test_restamp_calls_capabilities_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    import valuz_agent.modules.sessions.capabilities as caps

    monkeypatch.setattr(
        caps, "refresh_always_on_mcp_for_session", _as_async(lambda sid, *_: seen.append(sid))
    )
    asyncio.run(pre_turn.restamp_always_on_mcp("sess-1", LOCAL_USER_ID))
    assert seen == ["sess-1"]


def test_restamp_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import valuz_agent.modules.sessions.capabilities as caps

    async def _boom(_sid: str, _user_id: str | None = None) -> bool:
        raise RuntimeError("kernel down")

    monkeypatch.setattr(caps, "refresh_always_on_mcp_for_session", _boom)
    # Must not raise — a re-stamp failure can never block the turn.
    asyncio.run(pre_turn.restamp_always_on_mcp("sess-1", LOCAL_USER_ID))


# ── run_session_to_idle ─────────────────────────────────────────────────


def test_run_session_to_idle_hands_the_restamp_to_run_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        pre_turn,
        "restamp_always_on_mcp",
        _as_async(lambda _sid, *_: order.append("restamp")),
    )
    sess = SimpleNamespace(status="idle", metadata={"valuz": {"run_kind": "lead"}})
    monkeypatch.setattr(actor_runner.kernel_client, "get_session", _as_async(lambda *_: sess))

    async def _run_turn(*a: Any, pre_turn: Any = None, **k: Any) -> Any:
        # Stands in for the real facade: allocate, THEN converge, THEN run.
        order.append("allocate")
        if pre_turn is not None:
            await pre_turn()
        order.append("run_turn")
        return SimpleNamespace(id="m1", input_tokens=None, output_tokens=None)

    monkeypatch.setattr(actor_runner.kernel_client, "run_turn", _run_turn)
    # finalize hits the DB — stub it out (the primitive logs+continues anyway).
    import valuz_agent.modules.sessions.run_orchestrator as run_orch

    monkeypatch.setattr(run_orch, "_finalize_session", _as_async(lambda *a, **k: None))

    asyncio.run(turn_driver.run_session_to_idle("sess-1", "hi", _Bus(), user_id=LOCAL_USER_ID))

    # The re-stamp happens INSIDE the turn, after allocation — never before it.
    assert order == ["allocate", "restamp", "run_turn"]


def test_run_session_to_idle_forwards_a_caller_supplied_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chat paths pass the full ``chat_capability_hook``; it must win over
    the credential-only default."""
    seen: list[Any] = []
    sess = SimpleNamespace(status="idle", metadata={})
    monkeypatch.setattr(actor_runner.kernel_client, "get_session", _as_async(lambda *_: sess))

    async def _run_turn(*a: Any, pre_turn: Any = None, **k: Any) -> Any:
        seen.append(pre_turn)
        return SimpleNamespace(id="m1", input_tokens=None, output_tokens=None)

    monkeypatch.setattr(actor_runner.kernel_client, "run_turn", _run_turn)
    import valuz_agent.modules.sessions.run_orchestrator as run_orch

    monkeypatch.setattr(run_orch, "_finalize_session", _as_async(lambda *a, **k: None))

    async def _mine() -> None:
        return None

    asyncio.run(
        turn_driver.run_session_to_idle(
            "sess-1", "hi", _Bus(), pre_turn=_mine, user_id=LOCAL_USER_ID
        )
    )
    assert seen == [_mine]


# ── ActorRunner.run_turn ─────────────────────────────────────


def test_actor_loop_turn_hands_the_restamp_to_run_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr(
        pre_turn,
        "restamp_always_on_mcp",
        _as_async(lambda _sid, *_: order.append("restamp")),
    )

    async def _run_turn(*a: Any, pre_turn: Any = None, **k: Any) -> Any:
        order.append("allocate")
        if pre_turn is not None:
            await pre_turn()
        order.append("run_turn")

    monkeypatch.setattr(actor_runner.kernel_client, "run_turn", _run_turn)
    monkeypatch.setattr(
        actor_runner.kernel_client,
        "get_session",
        _as_async(lambda *_: SimpleNamespace(status="idle")),
    )

    runner = actor_runner.ActorRunner()
    status = asyncio.run(runner.run_turn("sess-1", "hi", user_id=LOCAL_USER_ID))

    assert status == "idle"
    assert order == ["allocate", "restamp", "run_turn"]

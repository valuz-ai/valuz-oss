"""Runtime reservation/release protocol with local synthetic guards only."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from src.core.hooks import (
    RUNTIME_CHECK,
    HookRegistry,
    RuntimeConstraints,
    SessionRef,
    runtime_support,
)


def setup(monkeypatch: Any, *, bound: bool = False, same_owner: bool = False) -> Any:
    registry = HookRegistry()
    runtime = SimpleNamespace(
        _hook_session_ref=SessionRef(
            session_id="synthetic",
            user_id="owner",
            runtime_provider="codex",
            execution_message_id="actual-turn" if bound else None,
        )
    )
    states: dict[str, dict[str, Any]] = {}
    unregister: dict[str, Any] = {}
    for name in ("first", "second"):
        state: dict[str, Any] = {"pending": {}, "calls": [], "failure": None}
        states[name] = state

        def handler_for(name: str, state: dict[str, Any]) -> Any:
            async def handler(ctx: Any, event: Any, next_: Any) -> RuntimeConstraints:
                phase = event.get("phase", "prepare")
                if phase == "prepare":
                    token = f"{name}-{len(state['calls'])}"
                    state["calls"].append(("prepare", token))
                    state["pending"][token] = event.session
                    return RuntimeConstraints(read_only=True, guard_nonce=token)
                token = event.get("guard_nonce")
                state["calls"].append(("release", token))
                assert token in state["pending"]
                assert state["pending"][token] == event.session
                if state["failure"] == "timeout":
                    await asyncio.Event().wait()
                if state["failure"] == "error":
                    raise RuntimeError("synthetic release unavailable")
                if state["failure"] == "deny":
                    return RuntimeConstraints(proceed=False)
                del state["pending"][token]
                return RuntimeConstraints()

            return handler

        unregister[name] = registry.register(
            RUNTIME_CHECK,
            handler_for(name, state),
            owner="shared" if same_owner else name,
            tier="builtin",
            required=True,
            fail_closed=True,
            budget_s=0.02,
        )
    monkeypatch.setattr(runtime_support, "hook_registry", registry)
    return registry, runtime, states, unregister


@pytest.mark.parametrize("bound", [False, True])
async def test_two_guards_get_their_own_frozen_tokens_and_release_idempotently(
    monkeypatch: Any, bound: bool
) -> None:
    registry, runtime, states, _ = setup(monkeypatch, bound=bound)

    async def ordinary(ctx: Any, event: Any, next_: Any) -> RuntimeConstraints:
        # Ordinary contributions do not have authority over release references.
        return RuntimeConstraints(
            guard_nonce="ordinary",
            guard_tokens={"first": {"invented": "ordinary"}},
        )

    registry.register(RUNTIME_CHECK, ordinary, owner="ordinary", tier="prepend")
    constraints = await runtime_support.runtime_constraints(runtime, "codex")
    assert set(constraints.guard_tokens) == {"first", "second"}
    for name, refs in constraints.guard_tokens.items():
        assert list(refs.values()) == [f"{name}-0"]
        with pytest.raises(TypeError):
            refs["changed"] = "other"
    with pytest.raises(TypeError):
        constraints.guard_tokens["other"] = {}
    assert await runtime_support.release_runtime_constraints(runtime, "codex")
    assert not runtime._runtime_constraints.guard_tokens
    assert runtime._runtime_constraints.guard_nonce is None
    assert all(not state["pending"] for state in states.values())
    before = [list(state["calls"]) for state in states.values()]
    assert await runtime_support.release_runtime_constraints(runtime, "codex")
    assert before == [state["calls"] for state in states.values()]


async def test_same_owner_independent_registrations_do_not_overwrite(monkeypatch: Any) -> None:
    _, runtime, states, _ = setup(monkeypatch, same_owner=True)
    result = await runtime_support.runtime_constraints(runtime, "codex")
    assert len(result.guard_tokens["shared"]) == 2
    assert await runtime_support.release_runtime_constraints(runtime, "codex")
    assert states["first"]["calls"][-1] == ("release", "first-0")
    assert states["second"]["calls"][-1] == ("release", "second-0")


@pytest.mark.parametrize("failure", ["error", "timeout", "deny"])
async def test_failed_release_keeps_only_unconfirmed_refs_and_retries(
    monkeypatch: Any, failure: str
) -> None:
    _, runtime, states, _ = setup(monkeypatch)
    await runtime_support.runtime_constraints(runtime, "codex")
    states["first"]["failure"] = failure
    assert not await runtime_support.release_runtime_constraints(runtime, "codex")
    assert set(runtime._runtime_constraints.guard_tokens) == {"first"}
    assert states["first"]["pending"] and not states["second"]["pending"]
    assert states["second"]["calls"] == [("prepare", "second-0"), ("release", "second-0")]
    states["first"]["failure"] = None
    assert await runtime_support.release_runtime_constraints(runtime, "codex")
    assert not states["first"]["pending"]
    assert len(states["second"]["calls"]) == 2


async def test_reregistered_owner_cannot_ack_an_old_registration(monkeypatch: Any) -> None:
    registry, runtime, states, unregister = setup(monkeypatch)
    await runtime_support.runtime_constraints(runtime, "codex")
    unregister["first"]()
    received: list[Any] = []

    async def replacement(ctx: Any, event: Any, next_: Any) -> RuntimeConstraints:
        received.append(event)
        return RuntimeConstraints()

    registry.register(
        RUNTIME_CHECK, replacement, owner="first", tier="builtin", required=True, fail_closed=True
    )
    assert not await runtime_support.release_runtime_constraints(runtime, "codex")
    assert received == []
    assert set(runtime._runtime_constraints.guard_tokens) == {"first"}
    assert states["first"]["pending"]


async def test_replacing_registry_cannot_reuse_matching_owner_and_seq(monkeypatch: Any) -> None:
    _, runtime, _, _ = setup(monkeypatch)
    await runtime_support.runtime_constraints(runtime, "codex")
    replacement = HookRegistry()
    received: list[Any] = []

    async def guard(ctx: Any, event: Any, next_: Any) -> RuntimeConstraints:
        received.append(event)
        return RuntimeConstraints()

    for owner in ("first", "second"):
        replacement.register(
            RUNTIME_CHECK, guard, owner=owner, tier="builtin", required=True, fail_closed=True
        )
    monkeypatch.setattr(runtime_support, "hook_registry", replacement)
    assert not await runtime_support.release_runtime_constraints(runtime, "codex")
    assert received == []
    assert len(runtime._runtime_constraints.guard_tokens) == 2


async def test_failed_old_release_uses_original_session_and_stops_new_prepare(
    monkeypatch: Any,
) -> None:
    _, runtime, states, _ = setup(monkeypatch, bound=True)
    await runtime_support.runtime_constraints(runtime, "codex")
    states["first"]["failure"] = "deny"
    runtime._hook_session_ref = SessionRef(
        session_id="next",
        user_id="other",
        runtime_provider="codex",
        execution_message_id="next-turn",
    )
    with pytest.raises(RuntimeError, match="release remains unconfirmed"):
        await runtime_support.runtime_constraints(runtime, "codex")
    assert len([call for call in states["first"]["calls"] if call[0] == "prepare"]) == 1
    states["first"]["failure"] = None
    assert await runtime_support.release_runtime_constraints(runtime, "codex")
    assert not states["first"]["pending"]


@pytest.mark.parametrize("failure", ["deny", "error", "cancel"])
async def test_startup_failure_releases_already_acquired_refs_without_sdk_execution(
    monkeypatch: Any, failure: str
) -> None:
    registry, runtime, states, unregister = setup(monkeypatch)
    unregister["second"]()

    async def failing(ctx: Any, event: Any, next_: Any) -> RuntimeConstraints:
        if failure == "error":
            raise RuntimeError("synthetic unavailable")
        if failure == "cancel":
            raise asyncio.CancelledError()
        return RuntimeConstraints(proceed=False)

    registry.register(
        RUNTIME_CHECK, failing, owner="failing", tier="builtin", required=True, fail_closed=True
    )
    sdk_calls = 0
    expected = asyncio.CancelledError if failure == "cancel" else RuntimeError
    with pytest.raises(expected):
        await runtime_support.runtime_constraints(runtime, "codex")
        sdk_calls += 1
    assert sdk_calls == 0 and not states["first"]["pending"]
    assert runtime._runtime_constraints.guard_tokens == {}


async def test_legacy_scalar_single_guard_remains_supported(monkeypatch: Any) -> None:
    _, runtime, states, unregister = setup(monkeypatch)
    unregister["second"]()
    await runtime_support.runtime_constraints(runtime, "codex")
    runtime._runtime_constraints = RuntimeConstraints(guard_nonce="first-0")
    assert await runtime_support.release_runtime_constraints(runtime, "codex")
    assert states["first"]["calls"][-1] == ("release", "first-0")
    assert await runtime_support.release_runtime_constraints(runtime, "codex")


async def test_denied_startup_keeps_failed_rollback_refs_and_prevents_another_prepare(
    monkeypatch: Any,
) -> None:
    registry, runtime, states, _ = setup(monkeypatch)
    states["first"]["failure"] = "error"

    async def deny(ctx: Any, event: Any, next_: Any) -> RuntimeConstraints:
        return RuntimeConstraints(proceed=False)

    registry.register(
        RUNTIME_CHECK, deny, owner="deny", tier="builtin", required=True, fail_closed=True
    )
    with pytest.raises(RuntimeError, match="unavailable or denied"):
        await runtime_support.runtime_constraints(runtime, "codex")
    assert set(runtime._runtime_constraints.guard_tokens) == {"first"}
    assert states["first"]["pending"] and not states["second"]["pending"]
    with pytest.raises(RuntimeError, match="release remains unconfirmed"):
        await runtime_support.runtime_constraints(runtime, "codex")
    assert len([call for call in states["first"]["calls"] if call[0] == "prepare"]) == 1
    states["first"]["failure"] = None
    assert await runtime_support.release_runtime_constraints(runtime, "codex")


async def test_legacy_ambiguous_scalar_is_retained_without_broadcast(monkeypatch: Any) -> None:
    _, runtime, states, _ = setup(monkeypatch)
    await runtime_support.runtime_constraints(runtime, "codex")
    runtime._runtime_constraints = RuntimeConstraints(guard_nonce="second-0")
    assert not await runtime_support.release_runtime_constraints(runtime, "codex")
    assert runtime._runtime_constraints.guard_nonce == "second-0"
    assert all(len(state["calls"]) == 1 for state in states.values())

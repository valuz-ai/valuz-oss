"""Actual Codex approval envelopes bind to typed SDK items, never prompt text."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from openai_codex.generated.v2_all import (
    ItemCompletedNotification,
    ItemStartedNotification,
    McpToolCallThreadItem,
    ThreadItem,
)
from openai_codex.models import Notification
from src.core.hooks import TOOL_CHECK, HookRegistry, RuntimeConstraints, SessionRef
from src.core.hooks.registry import SessionHooks
from src.runtimes.codex.approval_bridge import McpApprovalItem, decode_mcp_approval
from src.runtimes.codex.runtime import CodexRuntime

REF = SessionRef(
    session_id="owned",
    user_id="owner",
    runtime_provider="codex",
    execution_message_id="executing",
    tool_bindings={"local": "binding"},
)


def envelope() -> dict[str, Any]:
    return {
        "threadId": "thread",
        "turnId": "turn",
        "serverName": "local",
        "mode": "form",
        "_meta": {
            "codex_approval_kind": "mcp_tool_call",
            "tool_params": {"body": "synthetic"},
            "persist": ["session", "always"],
            "tool_description": "untrusted text",
        },
        "message": 'Allow an unrelated server to run tool "invented"?',
        "approved": True,
        "requestedSchema": {"type": "object", "properties": {}},
    }


def item(*, tool="send", ref=REF, item_id="opaque-item") -> McpApprovalItem:
    return McpApprovalItem(ref, "thread", "turn", item_id, "local", tool, {"body": "synthetic"})


def decode(params, items, ref=REF):
    return decode_mcp_approval(params, items, session=ref, thread_id="thread", turn_id="turn")


def test_stable_effect_has_actual_tool_final_args_and_opaque_item_only():
    result = decode(envelope(), [item()])
    assert result["tool"]["name"] == "mcp__local__send"
    assert result["input"] == {"body": "synthetic"}
    assert result["tool_use_id"] == "opaque-item"
    assert result["effect_boundary"] is False
    assert "threadId" not in result["input"] and "approved" not in result["input"]


@pytest.mark.parametrize("field", ["threadId", "turnId", "serverName"])
def test_changed_transport_binding_denies(field):
    params = envelope()
    params[field] = "other"
    assert decode(params, [item()]) is None


@pytest.mark.parametrize(
    "field", ["user_id", "session_id", "execution_message_id", "tool_bindings"]
)
def test_owner_session_execution_or_server_binding_cannot_reuse_item(field):
    value = {"local": "changed-binding"} if field == "tool_bindings" else "other"
    assert decode(envelope(), [item()], replace(REF, **{field: value})) is None


def test_missing_ambiguous_or_rewritten_args_denied_without_guessing_name():
    assert decode(envelope(), []) is None
    assert decode(envelope(), [item(), item(tool="another-tool", item_id="second")]) is None
    params = envelope()
    params["_meta"]["tool_params"]["body"] = "changed"
    assert decode(params, [item()]) is None
    params = envelope()
    params["itemId"] = "different-item"
    assert decode(params, [item()]) is None


def notification(
    *, completed=False, body="synthetic", item_id="opaque-item", tool="send", turn="turn"
):
    raw = {
        "item": ThreadItem(
            root=McpToolCallThreadItem(
                id=item_id,
                server="local",
                tool=tool,
                arguments={"body": body},
                status="completed" if completed else "inProgress",
                type="mcpToolCall",
            )
        ),
        "thread_id": "thread",
        "turn_id": turn,
    }
    payload = (
        ItemCompletedNotification(**raw, completed_at_ms=2)
        if completed
        else ItemStartedNotification(**raw, started_at_ms=1)
    )
    return Notification(method="item/completed" if completed else "item/started", payload=payload)


def runtime(monkeypatch, registry=None):
    from src.runtimes.codex import runtime as module

    result = object.__new__(CodexRuntime)
    result._active_turn = SimpleNamespace(thread_id="thread", id="turn")
    result._hook_session_ref = REF
    result._mcp_approval_items = {}
    result._mcp_approval_retired_items = set()
    result._mcp_approval_history = {}
    result._mcp_approval_items_changed = asyncio.Event()
    result.MCP_APPROVAL_ITEM_WAIT_SECONDS = 0.02
    result._cached_permission_mode = "full_access"
    result._runtime_constraints = RuntimeConstraints(human_review=True)
    monkeypatch.setattr(
        module,
        "runtime_session_hooks",
        lambda rt, provider: SessionHooks(registry or HookRegistry(), rt._hook_session_ref),
    )
    return result


async def test_real_typed_item_completion_or_conflicting_reuse_retires_identity(monkeypatch):
    rt = runtime(monkeypatch)
    rt._observe_mcp_approval_item(notification())
    assert await rt._decode_live_mcp_approval(envelope())
    rt._observe_mcp_approval_item(notification(body="changed"))
    rt._observe_mcp_approval_item(notification())
    assert await rt._decode_live_mcp_approval(envelope()) is None
    rt = runtime(monkeypatch)
    rt._observe_mcp_approval_item(notification())
    rt._observe_mcp_approval_item(notification(completed=True))
    rt._observe_mcp_approval_item(notification())
    assert await rt._decode_live_mcp_approval(envelope()) is None


async def test_bounded_wait_allows_real_item_arrival_and_cancellation_propagates(monkeypatch):
    rt = runtime(monkeypatch)
    pending = asyncio.create_task(rt._decode_live_mcp_approval(envelope()))
    await asyncio.sleep(0)
    rt._observe_mcp_approval_item(notification())
    assert await pending
    rt._mcp_approval_items.clear()
    pending = asyncio.create_task(rt._decode_live_mcp_approval(envelope()))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending


async def test_native_human_review_still_runs_after_canonical_mcp_guard(monkeypatch):
    seen = []
    registry = HookRegistry()

    async def required(ctx, event, next_):
        seen.append(event)
        return await next_()

    registry.register(
        TOOL_CHECK, required, owner="required", tier="builtin", fail_closed=True, required=True
    )
    rt = runtime(monkeypatch, registry)
    rt._observe_mcp_approval_item(notification())

    async def human(method, params):
        return "reject", "original permission denied", None

    rt._await_host_decision_coro = human
    assert await rt._tool_check_coro(
        "mcpServer/elicitation/request", envelope(), {"wrong": "envelope"}
    ) == {"action": "decline"}
    assert seen[0].get("input") == {"body": "synthetic"}
    assert seen[0].session == REF
    assert seen[0].get("tool.name") == "mcp__local__send"


async def test_ambiguous_live_request_stays_rejected_after_one_item_finishes(monkeypatch):
    rt = runtime(monkeypatch)
    rt._mcp_approval_items = {
        "first": item(item_id="first"),
        "second": item(tool="other", item_id="second"),
    }
    waiting = asyncio.create_task(rt._decode_live_mcp_approval(envelope()))
    await asyncio.sleep(0)
    rt._mcp_approval_items.pop("first")
    rt._mcp_approval_items_changed.set()
    assert await waiting is None


async def test_missing_item_and_old_turn_completion_cannot_select_or_remove_current(monkeypatch):
    rt = runtime(monkeypatch)
    assert await rt._decode_live_mcp_approval(envelope()) is None
    rt._observe_mcp_approval_item(notification())
    stale = notification(completed=True)
    stale.payload.turn_id = "old-turn"
    rt._observe_mcp_approval_item(stale)
    assert await rt._decode_live_mcp_approval(envelope())
    params = envelope()
    params["turnId"] = "old-turn"
    assert await rt._decode_live_mcp_approval(params) is None
    params = envelope()
    params["_meta"]["tool_params"]["body"] = "changed"
    assert await rt._decode_live_mcp_approval(params) is None


async def test_completed_first_item_cannot_erase_ambiguity_before_decoder_starts(monkeypatch):
    rt = runtime(monkeypatch)
    rt._observe_mcp_approval_item(notification(item_id="A", tool="send"))
    rt._observe_mcp_approval_item(notification(item_id="B", tool="other-tool"))
    rt._observe_mcp_approval_item(notification(item_id="A", tool="send", completed=True))
    assert set(rt._mcp_approval_items) == {"B"}
    assert await rt._decode_live_mcp_approval(envelope()) is None
    # A real opaque itemId can disambiguate only the corresponding active item.
    explicit = envelope()
    explicit["itemId"] = "B"
    assert (await rt._decode_live_mcp_approval(explicit))["tool"][
        "name"
    ] == "mcp__local__other-tool"
    explicit["itemId"] = "A"
    assert await rt._decode_live_mcp_approval(explicit) is None


async def test_ambiguity_history_is_turn_and_argument_key_scoped(monkeypatch):
    rt = runtime(monkeypatch)
    rt._observe_mcp_approval_item(notification(item_id="A"))
    rt._observe_mcp_approval_item(notification(item_id="B", tool="other-tool"))
    rt._observe_mcp_approval_item(notification(item_id="A", completed=True))
    rt._observe_mcp_approval_item(notification(item_id="C", body="different"))
    changed = envelope()
    changed["_meta"]["tool_params"]["body"] = "different"
    assert (await rt._decode_live_mcp_approval(changed))["tool_use_id"] == "C"
    assert await rt._decode_live_mcp_approval(envelope()) is None
    # A warm thread's next actual turn has its own key; late old-turn starts
    # cannot populate its state, including after the prior item completed.
    rt._active_turn = SimpleNamespace(thread_id="thread", id="next-turn")
    rt._observe_mcp_approval_item(notification(item_id="old-late", tool="other-tool"))
    rt._observe_mcp_approval_item(notification(item_id="new", turn="next-turn"))
    current = envelope()
    current["turnId"] = "next-turn"
    assert (await rt._decode_live_mcp_approval(current))["tool_use_id"] == "new"
    params = envelope()
    params["turnId"] = "next-turn"
    params["_meta"]["tool_params"]["body"] = "changed"
    assert await rt._decode_live_mcp_approval(params) is None

"""End-to-end approval contract — exercises the full park → resolve loop.

We drive the kernel orchestrator directly (no FastAPI TestClient) to
keep the test environment narrow: TestClient + the host's full startup
hook chain (Alembic, MCP session manager, schedule runner, etc.) is too
heavy and trips anyio cancel-scope races. The host route layer is
already validated by ``test_should_carry_route_shape_contracts`` in
``tests/test_sessions_routes_shape.py`` (route → service → orchestrator
forwarding). This test pins the deeper chain:

    requires_action (runtime sink) → events table
       ↓
    orchestrator.submit_action(pending_id, decision)
       ↓                          ↑ routed from runtime.submit_action
       ↓
    action_resolved (orchestrator emits to bus + DB)
       ↓
    list_events_after / SSE translation surfaces both as legacy types

A regression in any of those links manifests as a UI that parks forever
(no card render) or never resumes (no action_resolved after approve).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Literal

import pytest


class _FakeRuntime:
    """Minimal ``RuntimePort`` impl that parks until the host resolves.

    Implements just enough for ``orchestrator.submit_action`` to route a
    decision back: a per-pending ``asyncio.Event`` map. Real Claude /
    Codex / DeepAgents bridges do this with futures + SDK resume
    mechanics, but the orchestrator-facing surface is identical.
    """

    def __init__(self) -> None:
        self._pendings: dict[str, dict] = {}

    def update_sink(self, sink) -> None:
        pass

    async def run(self, session, user_message) -> None:
        pass

    async def submit_action(
        self,
        pending_id: str,
        decision: Literal["approve", "approve_with_changes", "reject", "answer"],
        message: str | None = None,
        answers: dict | None = None,
        modified_input: dict | None = None,
    ) -> None:
        entry = self._pendings.get(pending_id)
        if entry is None:
            return
        entry["decision"] = decision
        entry["message"] = message
        entry["answers"] = answers
        entry["modified_input"] = modified_input
        entry["event"].set()

    async def interrupt(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def park(self, pending_id: str) -> asyncio.Event:
        event = asyncio.Event()
        self._pendings[pending_id] = {
            "event": event,
            "decision": None,
            "message": None,
            "answers": None,
            "modified_input": None,
        }
        return event


@pytest.fixture
async def _store_and_orchestrator(tmp_path, monkeypatch):
    """Set up a kernel async store + orchestrator against a fresh SQLite.

    Drives the kernel's alembic upgrade against an isolated DB file so
    the tests don't bleed into one another or into a shared dev DB.
    """
    monkeypatch.setenv("VALUZ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VALUZ_DB_FILENAME", "approval-e2e.db")
    db_path = tmp_path / "approval-e2e.db"

    # Force re-import so the kernel env + alembic config pick up the new
    # env vars. ``kernel_bootstrap`` MUST be popped alongside ``config``:
    # it binds ``settings`` at module level (``from ...config import
    # settings``) and ``_set_kernel_env`` writes ``DATABASE_URL`` from
    # that binding. A stale binding points the kernel store at the real
    # dev DB — the test's fixture rows then leak into ~/.valuz/app/valuz.db.
    import sys

    for name in list(sys.modules):
        if name.startswith(("valuz_agent.infra.config", "valuz_agent.boot.kernel")):
            sys.modules.pop(name, None)

    # Side-effect: puts ``src.core`` on sys.path.
    import valuz_agent.boot.kernel as kb  # noqa: F401

    # Drive the kernel's alembic chain via the host helper.
    kb.run_kernel_migrations()

    from app.config import AppConfig  # type: ignore[import-not-found]
    from app.dependencies import (  # type: ignore[import-not-found]
        get_orchestrator,
        get_store,
        init_dependencies,
        shutdown_dependencies,
    )

    await init_dependencies(AppConfig())
    try:
        yield get_store(), get_orchestrator(), db_path
    finally:
        await shutdown_dependencies()


@pytest.mark.asyncio
async def test_should_complete_full_approval_cycle(_store_and_orchestrator, tmp_path):
    """Park → orchestrator.submit_action → action_resolved persisted + bus."""
    from src.core.agent_config import AgentConfig  # type: ignore[import-not-found]
    from src.core.events import Event  # type: ignore[import-not-found]
    from src.core.types import Message, Session, UserMessage  # type: ignore[import-not-found]

    store, orchestrator, _ = _store_and_orchestrator

    agent_id = uuid.uuid4().hex
    session_id = uuid.uuid4().hex
    message_id = uuid.uuid4().hex
    pending_id = "pending-abc"

    await store.save_session(
        Session(
            id=session_id,
            agent_config=AgentConfig(id=agent_id, name="a", model="claude-sonnet-4-6"),
            cwd=str(tmp_path),
            permission_mode="default",
        )
    )
    await store.save_message(
        Message(
            id=message_id,
            session_id=session_id,
            user_message=UserMessage(text="list temp"),
            started_at=datetime.now(),
            status="running",
        )
    )

    # Wire the fake runtime into the orchestrator's cache and park.
    fake = _FakeRuntime()
    orchestrator._runtimes[session_id] = fake
    orchestrator._active[session_id] = fake
    orchestrator._active_message[session_id] = await store.load_message(message_id)

    park_event = fake.park(pending_id)
    await store.append_event(
        session_id,
        message_id,
        Event(
            type="requires_action",
            data={
                "pending_id": pending_id,
                "subject": "shell_command",
                "runtime_provider": "claude_agent",
                "available_decisions": ["approve", "reject"],
                "payload": {"command": "ls /tmp"},
            },
        ),
    )

    # Resolve through the orchestrator's public API (same path the host
    # route handler takes after passing input validation).
    result = await orchestrator.submit_action(
        session_id,
        pending_id=pending_id,
        decision="approve",
    )

    assert result.pending_id == pending_id
    assert result.decision == "approve"
    assert result.idempotent is False
    assert park_event.is_set()
    assert fake._pendings[pending_id]["decision"] == "approve"

    # ``action_resolved`` persisted to the events table — SSE replay
    # (and WS reconnect's ``_build_replay``) will surface it. The
    # legacy SSE translation of the two event types is covered by
    # ``tests/adapters/test_event_sse_adapter_approval.py``; we don't
    # assert it here because the SSE adapter reads from a module-level
    # async engine that's pinned at first import and doesn't follow
    # the per-test fixture's data_dir override (would falsely fail when
    # this test runs alongside other DB-touching tests).
    events = await store.get_events(session_id, limit=100, offset=0)
    types = [e.type for e in events]
    assert "requires_action" in types
    assert "action_resolved" in types
    resolved_event = next(e for e in events if e.type == "action_resolved")
    assert resolved_event.data["decision"] == "approve"
    assert resolved_event.data["resolved_by"] == "user"


@pytest.mark.asyncio
async def test_should_return_idempotent_on_same_decision_retry(_store_and_orchestrator, tmp_path):
    """Same (pending_id, decision) twice → idempotent=True + original timestamp."""
    from src.core.agent_config import AgentConfig  # type: ignore[import-not-found]
    from src.core.events import Event  # type: ignore[import-not-found]
    from src.core.types import Message, Session, UserMessage  # type: ignore[import-not-found]

    store, orchestrator, _ = _store_and_orchestrator

    pid, aid, sid, mid = (uuid.uuid4().hex for _ in range(4))
    pending_id = "pending-idem"
    await store.save_session(
        Session(
            id=sid,
            agent_config=AgentConfig(id=aid, name="a", model="m"),
            cwd=str(tmp_path),
        )
    )
    await store.save_message(
        Message(
            id=mid,
            session_id=sid,
            user_message=UserMessage(text="hi"),
            started_at=datetime.now(),
            status="running",
        )
    )

    fake = _FakeRuntime()
    orchestrator._runtimes[sid] = fake
    orchestrator._active[sid] = fake
    orchestrator._active_message[sid] = await store.load_message(mid)
    fake.park(pending_id)
    await store.append_event(
        sid,
        mid,
        Event(
            type="requires_action",
            data={
                "pending_id": pending_id,
                "subject": "shell_command",
                "available_decisions": ["approve", "reject"],
                "payload": {"command": "x"},
            },
        ),
    )

    first = await orchestrator.submit_action(sid, pending_id=pending_id, decision="approve")
    retry = await orchestrator.submit_action(sid, pending_id=pending_id, decision="approve")

    assert first.idempotent is False
    assert retry.idempotent is True
    assert retry.accepted_at == first.accepted_at


@pytest.mark.asyncio
async def test_should_reject_conflicting_decision_with_pending_action_conflict(
    _store_and_orchestrator, tmp_path
):
    """Different decision on the same pending_id → PendingActionConflictError."""
    from src.core.agent_config import AgentConfig  # type: ignore[import-not-found]
    from src.core.events import Event  # type: ignore[import-not-found]
    from src.core.orchestrator import PendingActionConflictError  # type: ignore[import-not-found]
    from src.core.types import Message, Session, UserMessage  # type: ignore[import-not-found]

    store, orchestrator, _ = _store_and_orchestrator

    pid, aid, sid, mid = (uuid.uuid4().hex for _ in range(4))
    pending_id = "pending-conflict"
    await store.save_session(
        Session(
            id=sid,
            agent_config=AgentConfig(id=aid, name="a", model="m"),
            cwd=str(tmp_path),
        )
    )
    await store.save_message(
        Message(
            id=mid,
            session_id=sid,
            user_message=UserMessage(text="hi"),
            started_at=datetime.now(),
            status="running",
        )
    )

    fake = _FakeRuntime()
    orchestrator._runtimes[sid] = fake
    orchestrator._active[sid] = fake
    orchestrator._active_message[sid] = await store.load_message(mid)
    fake.park(pending_id)
    await store.append_event(
        sid,
        mid,
        Event(
            type="requires_action",
            data={
                "pending_id": pending_id,
                "subject": "shell_command",
                "available_decisions": ["approve", "reject"],
                "payload": {"command": "x"},
            },
        ),
    )

    await orchestrator.submit_action(sid, pending_id=pending_id, decision="approve")
    with pytest.raises(PendingActionConflictError):
        await orchestrator.submit_action(sid, pending_id=pending_id, decision="reject")


@pytest.mark.asyncio
async def test_should_seal_orphan_pendings_on_startup_walk(_store_and_orchestrator, tmp_path):
    """``scan_orphan_pendings`` writes ``action_resolved(expired)`` for unresolved rows."""
    from src.core.agent_config import AgentConfig  # type: ignore[import-not-found]
    from src.core.events import Event  # type: ignore[import-not-found]
    from src.core.types import Message, Session, UserMessage  # type: ignore[import-not-found]

    store, orchestrator, _ = _store_and_orchestrator

    pid, aid, sid, mid = (uuid.uuid4().hex for _ in range(4))
    pending_id = "pending-stale"
    # Status must be ``running`` for the scan to find it (simulates a
    # crash mid-turn).
    await store.save_session(
        Session(
            id=sid,
            agent_config=AgentConfig(id=aid, name="a", model="m"),
            cwd=str(tmp_path),
            status="running",
        )
    )
    await store.save_message(
        Message(
            id=mid,
            session_id=sid,
            user_message=UserMessage(text="hi"),
            started_at=datetime.now(),
            status="running",
        )
    )
    await store.append_event(
        sid,
        mid,
        Event(
            type="requires_action",
            data={
                "pending_id": pending_id,
                "subject": "shell_command",
                "message_id": mid,
                "available_decisions": ["approve", "reject"],
                "payload": {"command": "x"},
            },
        ),
    )

    sealed = await orchestrator.scan_orphan_pendings()
    assert sealed >= 1

    events = await store.get_events(sid, limit=100, offset=0)
    expired = [
        e for e in events if e.type == "action_resolved" and e.data.get("pending_id") == pending_id
    ]
    assert len(expired) == 1
    assert expired[0].data["decision"] == "expired"
    assert expired[0].data["resolved_by"] == "system"


# ---------------------------------------------------------------------------
# V5+d008b53 — approval contract v2 paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_commit_session_rule_and_return_rule_id_on_approve_for_session(
    _store_and_orchestrator, tmp_path
):
    """``approve_for_session`` commits a kernel-owned rule and surfaces its UUID.

    Pins three host-visible guarantees:
      1. ``SubmitActionResult.rule_id`` is non-None (host forwards it
         out the ``/v1/sessions/{id}/actions`` response).
      2. The persisted ``action_resolved`` event carries the same
         ``rule_id`` so SSE replay / WS reconnect see the rule.
      3. The runtime ONLY ever sees plain ``approve`` at its boundary —
         the session verb is kernel-side state and never leaks to the
         SDK adapters.
    """
    from src.core.agent_config import AgentConfig  # type: ignore[import-not-found]
    from src.core.events import Event  # type: ignore[import-not-found]
    from src.core.types import Message, Session, UserMessage  # type: ignore[import-not-found]

    store, orchestrator, _ = _store_and_orchestrator

    pid, aid, sid, mid = (uuid.uuid4().hex for _ in range(4))
    pending_id = "pending-rule-1"
    await store.save_session(
        Session(
            id=sid,
            agent_config=AgentConfig(id=aid, name="a", model="m"),
            cwd=str(tmp_path),
        )
    )
    await store.save_message(
        Message(
            id=mid,
            session_id=sid,
            user_message=UserMessage(text="run npm test"),
            started_at=datetime.now(),
            status="running",
        )
    )

    fake = _FakeRuntime()
    orchestrator._runtimes[sid] = fake
    orchestrator._active[sid] = fake
    orchestrator._active_message[sid] = await store.load_message(mid)
    fake.park(pending_id)

    preview = {
        "kind": "shell_command",
        "display": "Bash(npm test:*)",
        "runtime_kind": "claude_pattern",
        "rule_data": {"pattern": "Bash(npm test:*)"},
    }
    await store.append_event(
        sid,
        mid,
        Event(
            type="requires_action",
            data={
                "pending_id": pending_id,
                "subject": "shell_command",
                "available_decisions": ["approve", "approve_for_session", "reject"],
                "payload": {"command": "npm test", "cwd": str(tmp_path)},
                "session_rule_preview": preview,
            },
        ),
    )

    result = await orchestrator.submit_action(
        sid,
        pending_id=pending_id,
        decision="approve_for_session",
    )

    assert result.decision == "approve_for_session"
    assert isinstance(result.rule_id, str) and len(result.rule_id) > 0
    # Runtime saw plain ``approve`` — the session verb is kernel-only.
    assert fake._pendings[pending_id]["decision"] == "approve"

    events = await store.get_events(sid, limit=100, offset=0)
    resolved = next(
        e for e in events if e.type == "action_resolved" and e.data.get("pending_id") == pending_id
    )
    assert resolved.data["decision"] == "approve_for_session"
    assert resolved.data["rule_id"] == result.rule_id


@pytest.mark.asyncio
async def test_should_forward_modified_input_to_runtime_on_approve_with_changes(
    _store_and_orchestrator, tmp_path
):
    """A1 verb: orchestrator passes ``modified_input`` through to the runtime."""
    from src.core.agent_config import AgentConfig  # type: ignore[import-not-found]
    from src.core.events import Event  # type: ignore[import-not-found]
    from src.core.types import Message, Session, UserMessage  # type: ignore[import-not-found]

    store, orchestrator, _ = _store_and_orchestrator

    pid, aid, sid, mid = (uuid.uuid4().hex for _ in range(4))
    pending_id = "pending-edit-1"
    await store.save_session(
        Session(
            id=sid,
            agent_config=AgentConfig(id=aid, name="a", model="m"),
            cwd=str(tmp_path),
        )
    )
    await store.save_message(
        Message(
            id=mid,
            session_id=sid,
            user_message=UserMessage(text="edit and approve"),
            started_at=datetime.now(),
            status="running",
        )
    )

    fake = _FakeRuntime()
    orchestrator._runtimes[sid] = fake
    orchestrator._active[sid] = fake
    orchestrator._active_message[sid] = await store.load_message(mid)
    fake.park(pending_id)

    original = {"command": "rm -rf /tmp/cache", "cwd": str(tmp_path)}
    await store.append_event(
        sid,
        mid,
        Event(
            type="requires_action",
            data={
                "pending_id": pending_id,
                "subject": "shell_command",
                "available_decisions": ["approve", "approve_with_changes", "reject"],
                "payload": original,
                "original_input": original,
            },
        ),
    )

    edited = {"command": "rm -rf /tmp/cache --dry-run", "cwd": str(tmp_path)}
    result = await orchestrator.submit_action(
        sid,
        pending_id=pending_id,
        decision="approve_with_changes",
        modified_input=edited,
    )

    assert result.decision == "approve_with_changes"
    # No rule_id on this verb.
    assert result.rule_id is None
    # Runtime received the edited args (Claude → updated_input,
    # DeepAgents → EditDecision.edited_action.args). The fake just
    # records it.
    assert fake._pendings[pending_id]["modified_input"] == edited
    assert fake._pendings[pending_id]["decision"] == "approve_with_changes"

    events = await store.get_events(sid, limit=100, offset=0)
    resolved = next(
        e for e in events if e.type == "action_resolved" and e.data.get("pending_id") == pending_id
    )
    # Persisted event records the modified input so reconnect can
    # replay the complete decision shape.
    assert resolved.data["modified_input"] == edited

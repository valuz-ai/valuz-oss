"""``POST /v1/sessions/{id}/fork`` — server-side session fork.

Ordering contract (docs/design/session-fork.md §6.5): after validation the
NATIVE fork runs first (``orchestrator.fork_session`` →
``RuntimePort.fork_session``) while nothing is persisted — a refused fork
costs nothing. Only then is the kernel history copied, and the session row
is saved LAST as the commit point. Copy mechanics live in
``src.core.session_fork``; the source session is never written.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import pytest
import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from app.routes.sessions import fork_session
from app.schemas import ForkSessionRequest
from fastapi import HTTPException
from src.core import Event, Message, Session, UserMessage
from src.core.agent_config import AgentConfig


def _session(**overrides: object) -> Session:
    defaults: dict = {
        "id": "src-sess",
        "agent_config": AgentConfig(id="agent-1", name="tester"),
        "cwd": "/tmp/ws",
        "user_id": "owner",
        "runtime_provider": "codex",
        "status": "idle",
        "runtime_session_id": "th-src",
        "metadata": {"valuz": {"name": "source"}},
    }
    defaults.update(overrides)
    return Session(**defaults)


def _message(
    idx: int,
    *,
    session_id: str = "src-sess",
    status: str = "completed",
    turn_id: str | None = None,
    thread_id: str | None = "th-src",
    todos: list | None = None,
) -> Message:
    metadata: dict = {}
    if turn_id is not None:
        metadata["runtime_native"] = {
            "provider": "codex",
            **({"thread_id": thread_id} if thread_id else {}),
            "turn_id": turn_id,
        }
    return Message(
        id=f"m{idx}",
        session_id=session_id,
        user_message=UserMessage(text=f"prompt {idx}"),
        assistant_message=f"answer {idx}",
        started_at=idx * 1000,
        ended_at=idx * 1000 + 500,
        status=status,
        total_turns=1,
        metadata=metadata,
        todos=todos,
    )


class _Store:
    def __init__(self, session: Session, messages: list[Message]) -> None:
        self.sessions = {session.id: session}
        self.messages = {m.id: m for m in messages}
        self._by_session: dict[str, list[Message]] = {}
        for m in messages:
            self._by_session.setdefault(m.session_id, []).append(m)
        # message_id -> stored events
        self.events: dict[str, list[Event]] = {}
        self.saved_sessions: list[Session] = []
        self.saved_messages: list[Message] = []
        self.appended: list[tuple[str, str, Event, str | None]] = []
        self.deleted: list[str] = []
        # Cross-component ordering trace shared with the fake orchestrator.
        self.oplog: list[str] = []

    async def load_session(self, owner: str, session_id: str) -> Session | None:
        assert owner == "owner"
        return self.sessions.get(session_id)

    async def load_message(self, owner: str, message_id: str) -> Message | None:
        return self.messages.get(message_id)

    async def list_messages_for_session(
        self, owner: str, session_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[Message]:
        # Mirror the real store: newest-first pages.
        rows = sorted(
            self._by_session.get(session_id, []), key=lambda m: m.started_at, reverse=True
        )
        return rows[offset : offset + limit]

    async def save_session(self, session: Session) -> None:
        self.oplog.append("save_session")
        self.saved_sessions.append(session)
        self.sessions[session.id] = session

    async def save_message(self, owner: str, message: Message) -> None:
        self.oplog.append("save_message")
        self.saved_messages.append(message)

    async def get_events_for_message(
        self, owner: str, message_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[Event]:
        return self.events.get(message_id, [])[offset : offset + limit]

    async def append_event(
        self,
        owner: str,
        session_id: str,
        message_id: str,
        event: Event,
        *,
        request_id: str | None = None,
    ) -> int:
        self.appended.append((session_id, message_id, event, request_id))
        return len(self.appended)

    async def delete_session(self, owner: str, session_id: str) -> bool:
        self.deleted.append(session_id)
        return self.sessions.pop(session_id, None) is not None


class _Orchestrator:
    """Fake ``fork_session``: backfills the native id like the codex port."""

    def __init__(self, store: _Store, *, error: Exception | None = None) -> None:
        self.store = store
        self.error = error
        self.forked: list[tuple[str, str, str | None]] = []
        self.cleaned: list[str] = []
        self.runtime_contexts: list[dict[str, str] | None] = []

    async def fork_session(
        self,
        owner: str,
        session: Session,
        *,
        source_native_session_id: str,
        anchor: str | None = None,
        runtime_context: dict[str, str] | None = None,
    ) -> str:
        self.store.oplog.append("fork_session")
        self.forked.append((session.id, source_native_session_id, anchor))
        self.runtime_contexts.append(runtime_context)
        if self.error is not None:
            raise self.error
        session.runtime_session_id = "th-forked"
        return "th-forked"

    async def cleanup(self, session_id: str) -> None:
        self.cleaned.append(session_id)


async def test_message_fork_native_first_then_copy_then_commit() -> None:
    source = _session()
    messages = [
        _message(1, turn_id="t1"),
        _message(2, turn_id="t2", todos=[{"content": "step", "status": "pending"}]),
        _message(3, turn_id="t3"),
    ]
    store = _Store(source, messages)
    store.events["m1"] = [
        Event(type="user_message", data={"message": "prompt 1", "message_id": "m1"}, timestamp=1),
        Event(type="assistant_message", data={"text": "answer 1", "message_id": "m1"}, timestamp=2),
    ]
    orchestrator = _Orchestrator(store)

    result = await fork_session(
        "src-sess", ForkSessionRequest(message_id="m2"), store, orchestrator, "owner"
    )
    forked = store.saved_sessions[-1]

    assert result["data"].id == forked.id
    assert forked.id != source.id
    # Born idle with the anchor turn's settled state — a fork with history
    # is not a never-ran placeholder ("created" would hide it from the
    # host's runs-driven lists until the first Send).
    assert forked.status == "idle"
    # The native fork ran against the anchor's self-describing source, and
    # the backfilled thread id is in the response.
    assert orchestrator.forked == [(forked.id, "th-src", "t2")]
    assert result["data"].runtime_session_id == "th-forked"
    # Ordering: native fork strictly before any write; session row is the
    # final commit point.
    assert store.oplog[0] == "fork_session"
    assert store.oplog[-1] == "save_session"
    assert store.oplog.count("save_session") == 1
    # Provenance only — no fork_intent stamp remains in the design.
    assert forked.metadata["forked_from"] == {"session_id": "src-sess", "message_id": "m2"}
    assert "fork_intent" not in forked.metadata
    # Anchor is inclusive; later messages are dropped; order is oldest-first.
    assert [m.user_message.text for m in store.saved_messages] == ["prompt 1", "prompt 2"]
    assert all(m.session_id == forked.id for m in store.saved_messages)
    assert all(m.id not in {"m1", "m2", "m3"} for m in store.saved_messages)
    # The self-describing native anchors ride along unchanged.
    assert store.saved_messages[1].metadata["runtime_native"]["turn_id"] == "t2"
    # Carry-forward: the fork's live todos are the last snapshot in range.
    assert forked.todos == [{"content": "step", "status": "pending"}]
    # Events are re-homed onto the new message id.
    new_m1 = store.saved_messages[0].id
    assert [(sid, mid) for sid, mid, _e, _r in store.appended] == [(forked.id, new_m1)] * 2
    assert all(e.data["message_id"] == new_m1 for _s, _m, e, _r in store.appended)
    assert all(r is not None for _s, _m, _e, r in store.appended)
    # Non-destructive: the source row was never re-saved.
    assert all(s.id != source.id for s in store.saved_sessions)


async def test_session_fork_at_tail_forks_with_null_anchor() -> None:
    source = _session()
    store = _Store(source, [_message(1, turn_id="t1"), _message(2, status="running")])
    orchestrator = _Orchestrator(store)

    await fork_session("src-sess", ForkSessionRequest(), store, orchestrator, "owner")
    forked = store.saved_sessions[-1]

    assert orchestrator.forked == [(forked.id, "th-src", None)]
    assert forked.metadata["forked_from"] == {"session_id": "src-sess", "message_id": None}
    # Orphaned running rows are not copied.
    assert [m.user_message.text for m in store.saved_messages] == ["prompt 1"]


async def test_runtime_context_reaches_the_native_fork() -> None:
    """The fork builds a runtime, so it needs the caller's opaque context.

    Without it a deployment that persists a runtime-context MARKER instead of
    a real credential cannot materialize one, and the native fork dies as a
    502 before it ever touches the transcript — even though forking makes no
    model call at all.
    """
    store = _Store(_session(), [_message(1, turn_id="t1")])
    orchestrator = _Orchestrator(store)

    await fork_session(
        "src-sess",
        ForkSessionRequest(runtime_context={"example.runtime": "resolved"}),
        store,
        orchestrator,
        "owner",
    )

    assert orchestrator.runtime_contexts == [{"example.runtime": "resolved"}]


async def test_never_ran_source_is_plain_copy_without_native_fork() -> None:
    source = _session(runtime_session_id=None)
    store = _Store(source, [])
    orchestrator = _Orchestrator(store)

    await fork_session("src-sess", ForkSessionRequest(), store, orchestrator, "owner")
    forked = store.saved_sessions[-1]

    assert orchestrator.forked == []
    assert forked.metadata["forked_from"]["session_id"] == "src-sess"
    # A plain config copy carries no history — it IS an empty placeholder.
    assert forked.status == "created"


async def test_anchor_thread_id_falls_back_to_session_thread() -> None:
    source = _session()
    store = _Store(source, [_message(1, turn_id="t1", thread_id=None)])
    orchestrator = _Orchestrator(store)

    await fork_session(
        "src-sess", ForkSessionRequest(message_id="m1"), store, orchestrator, "owner"
    )

    assert orchestrator.forked[0][1] == "th-src"


async def test_caller_metadata_is_kept_but_cannot_shadow_provenance() -> None:
    source = _session()
    store = _Store(source, [_message(1, turn_id="t1")])

    await fork_session(
        "src-sess",
        ForkSessionRequest(
            message_id="m1",
            metadata={"valuz": {"name": "source (fork)"}, "forked_from": "spoof"},
        ),
        store,
        _Orchestrator(store),
        "owner",
    )

    forked = store.saved_sessions[-1]
    assert forked.metadata["valuz"] == {"name": "source (fork)"}
    assert forked.metadata["forked_from"] == {"session_id": "src-sess", "message_id": "m1"}


async def test_native_fork_failure_persists_nothing() -> None:
    source = _session()
    store = _Store(source, [_message(1, turn_id="t1")])
    orchestrator = _Orchestrator(store, error=RuntimeError("codex refused the fork (-32600)"))

    with pytest.raises(HTTPException) as exc:
        await fork_session(
            "src-sess", ForkSessionRequest(message_id="m1"), store, orchestrator, "owner"
        )

    assert exc.value.status_code == 502
    # Native-fork-first: the failure happened before any kernel write.
    assert store.saved_sessions == []
    assert store.saved_messages == []
    assert store.appended == []
    assert store.deleted == []


async def test_unwired_runtime_maps_to_422_and_persists_nothing() -> None:
    source = _session(runtime_provider="claude_agent")
    store = _Store(source, [])
    orchestrator = _Orchestrator(
        store, error=NotImplementedError("claude_agent native fork is not implemented yet")
    )

    with pytest.raises(HTTPException) as exc:
        await fork_session("src-sess", ForkSessionRequest(), store, orchestrator, "owner")

    assert exc.value.status_code == 422
    assert store.saved_sessions == []


async def test_mid_copy_failure_sweeps_orphans() -> None:
    source = _session()
    store = _Store(source, [_message(1, turn_id="t1")])
    orchestrator = _Orchestrator(store)

    async def _boom(owner: str, message: Message) -> None:
        raise RuntimeError("disk full")

    store.save_message = _boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        await fork_session(
            "src-sess", ForkSessionRequest(message_id="m1"), store, orchestrator, "owner"
        )

    forked_id = orchestrator.forked[0][0]
    assert store.deleted == [forked_id]
    assert orchestrator.cleaned == [forked_id]
    assert store.sessions["src-sess"] is source


async def test_anchor_without_native_anchor_is_409() -> None:
    source = _session()
    store = _Store(source, [_message(1, turn_id=None)])

    with pytest.raises(HTTPException) as exc:
        await fork_session(
            "src-sess", ForkSessionRequest(message_id="m1"), store, _Orchestrator(store), "owner"
        )
    assert exc.value.status_code == 409


async def test_anchor_from_other_session_is_409() -> None:
    source = _session()
    foreign = _message(9, session_id="other-sess", turn_id="t9")
    store = _Store(source, [_message(1, turn_id="t1")])
    store.messages[foreign.id] = foreign

    with pytest.raises(HTTPException) as exc:
        await fork_session(
            "src-sess", ForkSessionRequest(message_id="m9"), store, _Orchestrator(store), "owner"
        )
    assert exc.value.status_code == 409


async def test_running_anchor_is_409() -> None:
    source = _session()
    store = _Store(source, [_message(1, status="running", turn_id="t1")])

    with pytest.raises(HTTPException) as exc:
        await fork_session(
            "src-sess", ForkSessionRequest(message_id="m1"), store, _Orchestrator(store), "owner"
        )
    assert exc.value.status_code == 409


async def test_tail_fork_of_running_session_is_409() -> None:
    source = _session(status="running")
    store = _Store(source, [_message(1, turn_id="t1")])

    with pytest.raises(HTTPException) as exc:
        await fork_session("src-sess", ForkSessionRequest(), store, _Orchestrator(store), "owner")
    assert exc.value.status_code == 409


async def test_unknown_session_is_404() -> None:
    store = _Store(_session(), [])
    with pytest.raises(HTTPException) as exc:
        await fork_session("nope", ForkSessionRequest(), store, _Orchestrator(store), "owner")
    assert exc.value.status_code == 404

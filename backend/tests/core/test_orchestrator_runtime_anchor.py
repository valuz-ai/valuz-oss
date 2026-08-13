"""Native per-turn fork anchors land on ``Message.metadata["runtime_native"]``.

Runtimes capture the native identifier of the turn they just drove (codex
``turn_id`` / Claude transcript ``message_uuid`` / deepagents
``checkpoint_id``) and the orchestrator consumes it — read-and-clear — at
message finalize. That stored anchor is the seam message-granularity fork
resolves against (docs/design/session-fork.md). These tests pin the
orchestrator half of the contract:

* an anchor offered by the runtime is persisted on the finalized message;
* consume semantics — a turn that captures nothing stamps nothing (no
  stale carry-over from the previous message);
* runtimes (and fakes) WITHOUT the hook keep working unchanged.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import copy

import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.orchestrator import SessionOrchestrator
from src.core.types import EndTurn, Session, UserMessage


class _FakeStore:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.messages: list[object] = []
        self.appended: list[Event] = []
        self._next_seq = 0

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        return self._session if session_id == self._session.id else None

    async def save_session(self, session: Session) -> None:
        self._session = session

    async def save_message(self, user_id: str, message: object) -> None:
        self.messages.append(copy.deepcopy(message))

    async def append_event(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
        event: Event,
        **kwargs: object,
    ) -> int:
        self.appended.append(event)
        self._next_seq += 1
        return self._next_seq

    def terminal_session_update(self) -> Event:
        updates = [e for e in self.appended if e.type == "session_update"]
        assert updates, "no session_update events recorded"
        return updates[-1]


class _AnchorRuntime:
    """Minimal RuntimePort fake with a scripted per-run anchor sequence."""

    def __init__(self, sink: object, anchors: list[dict[str, object] | None]) -> None:
        self.sink = sink
        self._anchors = anchors
        self._runs = 0
        self.consume_calls = 0
        self.supports_native_continuation = False
        self.has_live_background_tasks = False

    @property
    def approval_rule_matcher(self) -> object:
        return object()

    def update_sink(self, sink: object) -> None:
        self.sink = sink

    def set_session_rule_finder(self, finder: object) -> None:  # pragma: no cover
        pass

    async def run(self, session: Session, user_message: UserMessage) -> None:
        self._pending_anchor = self._anchors[self._runs]
        self._runs += 1
        await self.sink.emit(Event(type="assistant_message", data={"text": "ok"}))
        session.stop_reason = EndTurn()
        session.status = "idle"
        await self.sink.emit(
            Event(
                type="session_idle",
                data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
            )
        )

    def consume_turn_anchor(self) -> dict[str, object] | None:
        self.consume_calls += 1
        anchor, self._pending_anchor = self._pending_anchor, None
        return anchor

    async def interrupt(self) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:  # pragma: no cover
        pass


class _NoHookRuntime(_AnchorRuntime):
    """Same fake without the anchor hook — the pre-anchor runtime shape."""

    consume_turn_anchor = None  # type: ignore[assignment]


def _session(tmp_path) -> Session:
    return Session(
        id="sess-anchor-1",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        user_id="owner-1",
        status="created",
    )


def _wire(monkeypatch, session: Session, runtime_cls, anchors):
    store = _FakeStore(session)
    runtimes: list[_AnchorRuntime] = []

    def create_runtime(*args, **kwargs):  # noqa: ANN002, ANN003
        runtime = runtime_cls(args[2], anchors)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)
    return store, runtimes


async def test_anchor_is_persisted_on_message_metadata(tmp_path, monkeypatch) -> None:
    anchor = {"provider": "codex", "thread_id": "th-1", "turn_id": "turn-1"}
    session = _session(tmp_path)
    store, _ = _wire(monkeypatch, session, _AnchorRuntime, [anchor])

    message = await SessionOrchestrator(store).run_turn(
        "owner-1", session.id, UserMessage(text="hi")
    )

    assert message.metadata["runtime_native"] == anchor
    assert store.messages[-1].metadata["runtime_native"] == anchor
    # The terminal session_update advertises the anchor to the UI — the
    # wire signal "Fork from here" keys its enabled state on.
    assert store.terminal_session_update().data["fork_anchor"] is True


async def test_no_anchor_means_no_metadata_key(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path)
    store, runtimes = _wire(monkeypatch, session, _AnchorRuntime, [None, None])

    message = await SessionOrchestrator(store).run_turn(
        "owner-1", session.id, UserMessage(text="hi")
    )

    assert "runtime_native" not in message.metadata
    assert runtimes[0].consume_calls == 1
    assert store.terminal_session_update().data["fork_anchor"] is False


async def test_anchor_does_not_leak_to_the_next_message(tmp_path, monkeypatch) -> None:
    anchor = {"provider": "codex", "thread_id": "th-1", "turn_id": "turn-1"}
    session = _session(tmp_path)
    store, _ = _wire(monkeypatch, session, _AnchorRuntime, [anchor, None])

    orchestrator = SessionOrchestrator(store)
    first = await orchestrator.run_turn("owner-1", session.id, UserMessage(text="one"))
    second = await orchestrator.run_turn("owner-1", session.id, UserMessage(text="two"))

    assert first.metadata["runtime_native"] == anchor
    assert "runtime_native" not in second.metadata


async def test_runtime_without_hook_is_unaffected(tmp_path, monkeypatch) -> None:
    session = _session(tmp_path)
    store, _ = _wire(monkeypatch, session, _NoHookRuntime, [None])

    message = await SessionOrchestrator(store).run_turn(
        "owner-1", session.id, UserMessage(text="hi")
    )

    assert message.status == "completed"
    assert "runtime_native" not in message.metadata

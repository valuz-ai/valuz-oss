"""Turn start announces ``running`` — the interim ``session_update``.

``run_turn`` persists ``session.status = "running"`` at turn entry but
historically emitted no event for it: the only ``session_update`` was the
terminal one after the turn (normally ``idle``). Every follower that derives
status from the event stream — the conversation header pill, the control
plane's ``run.status`` projection, per-turn re-subscribers on queue drains —
therefore sat on ``created``/stale for the whole turn and only caught up on a
manual refresh. These tests pin the fix: an interim
``session_update{status: running}`` is emitted right after ``user_message``
and BEFORE the runtime runs, and the terminal frame still closes the turn.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import copy
import json

import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.orchestrator import SessionOrchestrator
from src.core.types import (
    BARE_COMPLETION_METADATA_KEY,
    EndTurn,
    Error,
    Session,
    UserMessage,
)
from src.runtimes.network_egress import EgressRegistrationError


class _FakeStore:
    """Just enough StorePort for one ``run_turn``: session load/save + the
    DatabaseEventSink append path (where the emitted events land)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.appended: list[Event] = []
        self._next_seq = 0

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        return self._session if session_id == self._session.id else None

    async def save_session(self, session: Session) -> None:
        self._session = session

    async def save_message(self, user_id: str, message: object) -> None:
        pass

    async def append_event(
        self, user_id: str, session_id: str, message_id: str, event: Event, **kw: object
    ) -> int:
        self.appended.append(event)
        self._next_seq += 1
        return self._next_seq


class _FakeRuntime:
    """RuntimePort stand-in that snapshots the persisted events at run() entry
    — proving the interim frame precedes the model turn, not just the return."""

    def __init__(self, store: _FakeStore) -> None:
        self._store = store
        self.types_at_run: list[str] | None = None
        self.has_live_background_tasks = False

    @property
    def approval_rule_matcher(self) -> object:
        return object()

    def update_sink(self, sink: object) -> None:
        pass

    def set_session_rule_finder(self, finder: object) -> None:  # pragma: no cover
        pass

    async def run(self, session: Session, user_message: UserMessage) -> None:
        self.types_at_run = [e.type for e in self._store.appended]
        session.status = "idle"

    async def interrupt(self) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:
        pass


async def test_run_turn_does_not_create_pending_task_clarification(
    tmp_path,
    monkeypatch,
) -> None:
    session = Session(
        id="sess-pending-turns",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        user_id="owner-1",
        status="created",
        metadata={"valuz": {"task_coverage_enabled": True}},
    )
    store = _FakeStore(session)
    orch = SessionOrchestrator(store)  # type: ignore[arg-type]

    def create_runtime(*_args, **_kwargs) -> _FakeRuntime:
        return _FakeRuntime(store)

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    await orch.run_turn(
        "owner-1",
        session.id,
        UserMessage(
            text="按用户给定的连续季度阈值判断是否触发，不重新制定规则。"
        ),
    )

    assert "pending_task_clarification" not in store._session.metadata["valuz"]

    await orch.run_turn(
        "owner-1",
        session.id,
        UserMessage(text="连续季度阈值为 50%，继续按原任务检查。"),
    )

    assert "pending_task_clarification" not in store._session.metadata["valuz"]


async def test_missing_context_is_left_to_the_native_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    session = Session(
        id="sess-host-clarification",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        user_id="owner-1",
        status="created",
        metadata={"valuz": {"task_coverage_enabled": True}},
    )
    store = _FakeStore(session)
    orch = SessionOrchestrator(store)  # type: ignore[arg-type]

    runtime = _FakeRuntime(store)

    monkeypatch.setattr(
        "src.runtimes.factory.create_runtime",
        lambda *_args, **_kwargs: runtime,
    )

    message = await orch.run_turn(
        "owner-1",
        session.id,
        UserMessage(text="按用户给定的连续季度阈值判断是否触发，不重新制定规则。"),
    )

    assert runtime.types_at_run == ["user_message", "session_update"]
    assert message.assistant_message is None
    assert not any(event.type in {"tool_use", "tool_result"} for event in store.appended)
    assert "pending_task_clarification" not in store._session.metadata["valuz"]


async def test_failed_turn_does_not_restore_legacy_pending_task_clarification(
    tmp_path,
    monkeypatch,
) -> None:
    session = Session(
        id="sess-pending-failed-resume",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        user_id="owner-1",
        status="created",
        metadata={
            "valuz": {
                "task_coverage_enabled": True,
                "pending_task_clarification": {
                    "version": 1,
                    "originalRequest": "按用户给定的连续季度阈值判断是否触发。",
                    "missingInputs": ["连续季度阈值"],
                    "supplements": [],
                },
            }
        },
    )
    store = _FakeStore(session)
    orch = SessionOrchestrator(store)  # type: ignore[arg-type]

    class _FailingRuntime(_FakeRuntime):
        async def run(self, session: Session, user_message: UserMessage) -> None:
            self.types_at_run = [e.type for e in self._store.appended]
            session.status = "idle"
            session.stop_reason = Error(
                category="execution_error",
                message="transient provider stream failure",
            )

    monkeypatch.setattr(
        "src.runtimes.factory.create_runtime",
        lambda *_args, **_kwargs: _FailingRuntime(store),
    )

    message = await orch.run_turn(
        "owner-1",
        session.id,
        UserMessage(text="连续季度阈值为 50%，继续按原任务检查。"),
    )

    assert message.status == "errored"
    assert "pending_task_clarification" not in store._session.metadata["valuz"]


async def test_egress_initialization_failure_finalizes_turn_without_runtime(
    tmp_path,
    monkeypatch,
) -> None:
    session = Session(
        id="sess-egress-failure",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        user_id="owner-1",
        status="created",
    )
    store = _FakeStore(session)
    orch = SessionOrchestrator(store)  # type: ignore[arg-type]

    async def reject_egress(*_args, **_kwargs):
        raise EgressRegistrationError("egress_manager_unavailable")

    monkeypatch.setattr(
        "src.runtimes.network_egress.prepare_runtime_egress",
        reject_egress,
    )

    message = await orch.run_turn(
        "owner-1",
        session.id,
        UserMessage(text="hello"),
    )

    assert message.status == "errored"
    assert message.error_message == {
        "category": "network_egress_unavailable",
        "message": (
            "Unified model networking is unavailable. Check Network settings "
            "or switch to model-client-managed connections."
        ),
    }
    assert store._session.status == "idle"
    assert [event.type for event in store.appended] == [
        "user_message",
        "session_error",
        "session_update",
    ]
    assert store.appended[1].data["code"] == "egress_manager_unavailable"


async def test_run_turn_emits_running_session_update_before_runtime(tmp_path, monkeypatch) -> None:
    agent = AgentConfig(id="agent-1", name="tester")
    session = Session(
        id="sess-1",
        agent_config=agent,
        cwd=str(tmp_path),
        user_id="owner-1",
        status="created",
    )
    store = _FakeStore(session)
    orch = SessionOrchestrator(store)  # type: ignore[arg-type]
    runtime = _FakeRuntime(store)
    monkeypatch.setattr("src.runtimes.factory.create_runtime", lambda *a, **k: runtime)

    message = await orch.run_turn("owner-1", "sess-1", UserMessage(text="hi"))

    types = [e.type for e in store.appended]
    # Interim frame right after the start marker...
    assert types[:2] == ["user_message", "session_update"]
    running = store.appended[1]
    assert running.data["status"] == "running"
    assert running.data["message_id"] == message.id
    # ...and already durable BEFORE the runtime ran a single token.
    assert runtime.types_at_run == ["user_message", "session_update"]
    # The terminal frame still closes the turn with the post-turn status.
    terminal = store.appended[-1]
    assert terminal.type == "session_update"
    assert terminal.data["status"] == "idle"


class _CitationRepairRuntime:
    def __init__(self, sink: object) -> None:
        self.sink = sink
        self.prompts: list[str] = []
        self.sessions: list[Session] = []
        self.closed = False
        self.has_live_background_tasks = False

    @property
    def approval_rule_matcher(self) -> object:
        return object()

    def update_sink(self, sink: object) -> None:
        self.sink = sink

    async def run(self, session: Session, user_message: UserMessage) -> None:
        self.prompts.append(user_message.text)
        self.sessions.append(copy.deepcopy(session))
        is_repair = bool(session.metadata.get(BARE_COMPLETION_METADATA_KEY))
        if not is_repair:
            evidence = {
                "_valuz_evidence": {
                    "evidenceHandle": "ev_repair_12345678",
                    "source": {
                        "sourceId": "doc-1",
                        "providerId": "docs",
                        "documentId": "doc-1",
                        "sourceType": "document",
                        "title": "Report",
                        "retrievedAt": "2026-07-30T10:00:00Z",
                    },
                    "evidence": {
                        "kind": "text",
                        "quote": "Revenue increased by 12%.",
                        "snippet": "Revenue increased by 12%.",
                        "capturedAt": "2026-07-30T10:00:00Z",
                    },
                    "locator": {"kind": "pdf", "page": 1},
                }
            }
            await self.sink.emit(
                Event(type="tool_use", data={"id": "tool-1", "name": "doc_search"})
            )
            await self.sink.emit(
                Event(
                    type="tool_result",
                    data={"id": "tool-1", "content": json.dumps(evidence)},
                )
            )
            answer = "Revenue declined."
            session.runtime_session_id = "native-research-thread"
        else:
            context = json.loads(
                user_message.text.split("Restricted repair context (JSON):\n", 1)[1]
            )
            answer = json.dumps(
                {
                    "version": "citation-claim-patch-v1",
                    "patches": [
                        {
                            "claimId": context["claimIssues"][0]["claimId"],
                            "replacementText": "Revenue increased by 12%.",
                            "evidenceHandles": ["ev_repair_12345678"],
                        }
                    ],
                }
            )
        await self.sink.emit(Event(type="assistant_message", data={"text": answer}))
        session.status = "idle"
        session.stop_reason = EndTurn()
        await self.sink.emit(
            Event(
                type="session_idle",
                data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
            )
        )

    async def interrupt(self) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:
        self.closed = True


async def test_run_turn_does_not_repair_an_unresolved_claim(tmp_path, monkeypatch) -> None:
    agent = AgentConfig(id="agent-1", name="tester")
    session = Session(
        id="sess-1",
        agent_config=agent,
        cwd=str(tmp_path),
        user_id="owner-1",
        status="created",
        skills=("/bundled/skills/citation",),
        metadata={
            "valuz": {
                "citation_verification_enabled": True,
                "task_coverage_enabled": False,
            }
        },
    )
    store = _FakeStore(session)
    orch = SessionOrchestrator(store)  # type: ignore[arg-type]
    runtimes: list[_CitationRepairRuntime] = []

    def create_runtime(*args, **kwargs) -> _CitationRepairRuntime:  # noqa: ANN002, ANN003
        runtime = _CitationRepairRuntime(args[2])
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)

    message = await orch.run_turn(
        "owner-1",
        "sess-1",
        UserMessage(text="Answer with citations"),
    )

    assert len(runtimes) == 1
    assert len(runtimes[0].prompts) == 1
    assert runtimes[0].prompts[0] == "Answer with citations"
    assert runtimes[0].closed is False
    assert store._session.runtime_session_id == "native-research-thread"
    assert message.assistant_message is not None
    assert "Revenue declined." in message.assistant_message
    assert [event.type for event in store.appended].count("assistant_message") == 1
    assert [event.type for event in store.appended].count("session_idle") == 1

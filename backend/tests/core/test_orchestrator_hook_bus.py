"""Host-level hook events, dispatched by the orchestrator for every runtime.

``session.start`` / ``session.end`` follow the warm runtime's life;
``prompt.submit`` sees (and may rewrite or drop) the user's text before the
runtime does; ``turn.start`` / ``turn.complete`` bracket the runtime's turn;
a registered ``/command`` is answered without any runtime at all.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.hooks import (
    COMMAND_RUN,
    PROMPT_SUBMIT,
    SESSION_END,
    SESSION_START,
    TURN_COMPLETE,
    TURN_START,
    CommandOutput,
    PromptDecision,
    command_registry,
    hook_registry,
)
from src.core.orchestrator import SessionOrchestrator
from src.core.types import BARE_COMPLETION_METADATA_KEY, Session, UserMessage

OWNER = "test.orchestrator-hook-bus"


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    hook_registry.unregister_owner(OWNER)
    command_registry.unregister_owner(OWNER)


class _FakeStore:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.appended: list[Event] = []
        self.messages: list[Any] = []

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        return self._session if session_id == self._session.id else None

    async def save_session(self, session: Session) -> None:
        self._session = session

    async def save_message(self, user_id: str, message: Any) -> None:
        self.messages.append(message)

    async def append_event(
        self, user_id: str, session_id: str, message_id: str, event: Event, **kw: object
    ) -> int:
        self.appended.append(event)
        return len(self.appended)


class _FakeRuntime:
    def __init__(self, log: list[str], sink: Any) -> None:
        self.log = log
        self.sink = sink
        self.prompts: list[str] = []
        self.has_live_background_tasks = False

    @property
    def approval_rule_matcher(self) -> object:
        return object()

    def update_sink(self, sink: object) -> None:
        self.sink = sink

    async def run(self, session: Session, user_message: UserMessage) -> None:
        self.log.append("runtime.run")
        self.prompts.append(user_message.text)
        await self.sink.emit(Event(type="assistant_message", data={"text": "model answer"}))
        session.status = "idle"

    async def interrupt(self) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:
        self.log.append("runtime.close")


def _setup(tmp_path, monkeypatch, *, metadata: dict[str, Any] | None = None):  # noqa: ANN001, ANN202
    session = Session(
        id="sess-hooks",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        user_id="owner-1",
        runtime_provider="deepagents",
        status="created",
        metadata=metadata or {},
    )
    store = _FakeStore(session)
    orch = SessionOrchestrator(store)  # type: ignore[arg-type]
    log: list[str] = []
    runtimes: list[_FakeRuntime] = []

    def create_runtime(_agent: Any, _session: Any, sink: Any, **_kwargs: Any) -> _FakeRuntime:
        runtime = _FakeRuntime(log, sink)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)
    return session, store, orch, log, runtimes


def _tracer(log: list[str], label: str):  # noqa: ANN202
    async def handler(ctx, event, next_):  # noqa: ANN001
        log.append(f"{label}:{event.session.runtime_provider}")
        return await next_()

    return handler


async def test_events_fire_in_order_around_the_runtime(tmp_path, monkeypatch) -> None:
    session, _store, orch, log, _ = _setup(tmp_path, monkeypatch)
    for event in (SESSION_START, PROMPT_SUBMIT, TURN_START, TURN_COMPLETE, SESSION_END):
        hook_registry.register(event, _tracer(log, event), owner=OWNER)

    await orch.run_turn("owner-1", session.id, UserMessage(text="hi"))
    await orch.cleanup(session.id)

    assert log == [
        "prompt.submit:deepagents",
        "session.start:deepagents",
        "turn.start:deepagents",
        "runtime.run",
        "turn.complete:deepagents",
        "runtime.close",
        "session.end:deepagents",
    ]


async def test_turn_complete_carries_the_outcome(tmp_path, monkeypatch) -> None:
    session, _store, orch, _log, _ = _setup(tmp_path, monkeypatch)
    seen: list[dict[str, Any]] = []

    async def watch(ctx, event, next_):  # noqa: ANN001
        seen.append(dict(event.data))
        return await next_()

    hook_registry.register(TURN_COMPLETE, watch, owner=OWNER)
    message = await orch.run_turn("owner-1", session.id, UserMessage(text="hi"))
    assert seen == [
        {
            "message_id": message.id,
            "status": "completed",
            "stop_reason": None,
            "assistant_text": "model answer",
        }
    ]


async def test_prompt_submit_rewrites_and_adds_context(tmp_path, monkeypatch) -> None:
    session, _store, orch, _log, runtimes = _setup(tmp_path, monkeypatch)

    async def rewrite(ctx, event, next_):  # noqa: ANN001
        await next_()
        return PromptDecision(text=event.get("text").upper(), context=("(be brief)",))

    hook_registry.register(PROMPT_SUBMIT, rewrite, owner=OWNER)
    await orch.run_turn("owner-1", session.id, UserMessage(text="hello"))
    assert runtimes[0].prompts == ["HELLO\n\n(be brief)"]


async def test_prompt_submit_drop_never_reaches_the_runtime(tmp_path, monkeypatch) -> None:
    session, store, orch, log, runtimes = _setup(tmp_path, monkeypatch)

    async def drop(ctx, event, next_):  # noqa: ANN001
        return PromptDecision(text=event.get("text"), drop="contains a secret")

    hook_registry.register(PROMPT_SUBMIT, drop, owner=OWNER)
    message = await orch.run_turn("owner-1", session.id, UserMessage(text="my key is sk-1"))
    assert runtimes == [] and "runtime.run" not in log
    errors = [e for e in store.appended if e.type == "session_error"]
    assert errors and errors[0].data["category"] == "prompt_blocked"
    assert errors[0].data["message"] == "contains a secret"
    assert message.status == "errored"
    assert store._session.status == "idle"
    types = [e.type for e in store.appended]
    assert types[:2] == ["user_message", "session_update"]
    assert types[-1] == "session_update"


async def test_registered_command_answers_without_the_model(tmp_path, monkeypatch) -> None:
    session, store, orch, log, runtimes = _setup(tmp_path, monkeypatch)
    completed: list[str] = []

    async def tally(session_ref, args: str) -> str:  # noqa: ANN001
        return f"{session_ref.session_id} tally={args}"

    async def wrap(ctx, event, next_):  # noqa: ANN001
        result = await next_()
        return CommandOutput(text=f"[{result.text}]")

    async def on_complete(ctx, event, next_):  # noqa: ANN001
        completed.append(event.get("assistant_text"))
        return await next_()

    command_registry.register("tally", tally, owner=OWNER, description="count things")
    hook_registry.register(COMMAND_RUN, wrap, owner=OWNER)
    hook_registry.register(TURN_COMPLETE, on_complete, owner=OWNER)

    message = await orch.run_turn("owner-1", session.id, UserMessage(text="/tally 3"))
    assert runtimes == [] and log == []
    assert message.status == "completed"
    assert message.assistant_message == "[sess-hooks tally=3]"
    assert completed == ["[sess-hooks tally=3]"]
    types = [e.type for e in store.appended]
    assert "assistant_message" in types and types[-1] == "session_update"

    # An unregistered slash command is the runtime's business, as before.
    await orch.run_turn("owner-1", session.id, UserMessage(text="/compact"))
    assert runtimes and runtimes[0].prompts == ["/compact"]


async def test_failing_command_ends_its_turn_with_an_error(tmp_path, monkeypatch) -> None:
    session, store, orch, _log, runtimes = _setup(tmp_path, monkeypatch)

    async def broken(session_ref, args: str) -> str:  # noqa: ANN001
        raise RuntimeError("database unreachable")

    command_registry.register("report", broken, owner=OWNER)
    message = await orch.run_turn("owner-1", session.id, UserMessage(text="/report"))
    assert runtimes == []
    assert message.status == "errored"
    errors = [e for e in store.appended if e.type == "session_error"]
    assert errors[0].data["category"] == "command_failed"
    assert "database unreachable" in errors[0].data["message"]


async def test_bare_sessions_skip_hooks_and_commands(tmp_path, monkeypatch) -> None:
    session, _store, orch, log, runtimes = _setup(
        tmp_path, monkeypatch, metadata={BARE_COMPLETION_METADATA_KEY: True}
    )

    async def tally(session_ref, args: str) -> str:  # noqa: ANN001
        return "should not run"

    command_registry.register("tally", tally, owner=OWNER)
    for event in (SESSION_START, PROMPT_SUBMIT, TURN_START, TURN_COMPLETE):
        hook_registry.register(event, _tracer(log, event), owner=OWNER)
    await orch.run_turn("owner-1", session.id, UserMessage(text="/tally"))
    assert log == ["runtime.run"]
    assert runtimes[0].prompts == ["/tally"]

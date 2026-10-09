"""Terminal wire events must identify a finalized persisted execution Message."""

# ruff: noqa: I001 — kernel bootstrap precedes src imports
from __future__ import annotations
import copy
import pytest
import valuz_agent.boot.kernel  # noqa: F401
from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.hooks import (
    PROMPT_SUBMIT,
    CommandOutput,
    PromptDecision,
    command_registry,
    hook_registry,
)
from src.core.orchestrator import SessionOrchestrator
from src.core.types import EndTurn, Error, Session, UserInterrupt, UserMessage
from src.runtimes.network_egress import EgressRegistrationError

OWNER = "test.terminal-persistence"


@pytest.mark.parametrize("path", ["normal", "error", "interrupt", "command", "drop", "egress"])
async def test_terminal_event_follows_exact_message_persistence(tmp_path, monkeypatch, path):
    saved = {}
    terminal = []
    session = Session(
        id="owned-main",
        user_id="owner",
        cwd=str(tmp_path),
        agent_config=AgentConfig(id="agent", name="test"),
    )

    class Store:
        async def load_session(self, user_id, session_id):
            assert user_id == "owner" and session_id == session.id
            return session

        async def save_session(self, value):
            pass

        async def save_message(self, user_id, message):
            assert user_id == "owner"
            saved[message.id] = copy.deepcopy(message)

        async def append_event(self, user_id, session_id, message_id, event, **kwargs):
            if event.type in {"session_idle", "session_error"}:
                persisted = saved[message_id]
                assert persisted.status != "running", "terminal escaped before Message finalization"
                assert persisted.ended_at is not None
                if path in {"normal", "command"}:
                    assert persisted.assistant_message == "actual answer"
                terminal.append(event.type)
            return 1

    class Runtime:
        has_live_background_tasks = False
        approval_rule_matcher = None

        def __init__(self, sink):
            self.sink = sink

        def update_sink(self, sink):
            self.sink = sink

        async def run(self, value, message):
            if path == "error":
                value.stop_reason = Error(category="execution_error", message="failed")
                await self.sink.emit(
                    Event(
                        type="session_error",
                        data={"category": "execution_error", "message": "failed"},
                    )
                )
            elif path == "interrupt":
                value.stop_reason = UserInterrupt()
            else:
                value.stop_reason = EndTurn()
                await self.sink.emit(
                    Event(type="assistant_message", data={"text": "actual answer"})
                )
            value.status = "idle"
            await self.sink.emit(
                Event(type="session_idle", data={"stop_reason": {"type": value.stop_reason.type}})
            )

        async def close(self):
            pass

    def factory(agent, value, sink, **kwargs):
        if path == "egress":
            raise EgressRegistrationError("unavailable")
        return Runtime(sink)

    monkeypatch.setattr("src.runtimes.factory.create_runtime", factory)

    async def command(*args):
        return CommandOutput(text="actual answer")

    async def drop(ctx, event, next_):
        return PromptDecision(text=event.get("text"), drop="blocked")

    if path == "command":
        command_registry.register("proof", command, owner=OWNER)
    if path == "drop":
        hook_registry.register(PROMPT_SUBMIT, drop, owner=OWNER)
    try:
        message = await SessionOrchestrator(Store()).run_turn(
            "owner",
            session.id,
            UserMessage(text="/proof" if path == "command" else "synthetic input"),
        )
        assert terminal
        assert message.id in saved
    finally:
        hook_registry.unregister_owner(OWNER)
        command_registry.unregister_owner(OWNER)

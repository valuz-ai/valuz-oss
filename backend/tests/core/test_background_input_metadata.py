"""The trusted input envelope survives transport, persistence and live events."""

# ruff: noqa: I001 — bootstrap before kernel imports
from __future__ import annotations

import pytest
import valuz_agent.boot.kernel  # noqa: F401
from app.routes.run import _parse_user_message
from app.routes.messages import _message_to_data
from src.adapters.sqlalchemy_store.converters import user_message_to_dict, dict_to_user_message
from src.core.events import Event
from src.core.orchestrator import _MessageObserverSink
from src.core.types import Message, UserMessage

META = {
    "background_input": {
        "input_id": "i",
        "source": "background",
        "presentation": {"kind": "personal_work_result", "work_ref_id": "w"},
    }
}


def test_metadata_transport_and_persistence_round_trip_is_detached():
    original = {"message": {"text": "retained evidence", "metadata": META}}
    user = _parse_user_message(original)
    restored = dict_to_user_message(user_message_to_dict(user))
    assert restored.metadata == META
    msg = Message(
        id="m", session_id="s", user_message=restored, started_at=1, metadata=restored.metadata
    )
    assert _message_to_data(msg).metadata == META
    assert _message_to_data(msg).user_message.metadata == META
    original["message"]["metadata"] = {"forged": "changed"}
    assert user.metadata == META
    assert (
        _parse_user_message({"message": {"text": "managed_work_result human text"}}).metadata == {}
    )


@pytest.mark.parametrize("value", [None, [], {"object": object()}, {"nan": float("nan")}])
def test_metadata_accepts_only_json_objects(value):
    with pytest.raises(ValueError):
        _parse_user_message({"message": {"text": "hello", "metadata": value}})


async def test_user_message_event_uses_host_metadata_and_rejects_runtime_spoof():
    class Sink:
        def __init__(self):
            self.events = []

        async def emit(self, event):
            self.events.append(event)

    sink = Sink()
    observer = _MessageObserverSink(sink, input_metadata=META)
    await observer.emit(
        Event(
            type="user_message", data={"message": "retained evidence", "metadata": {"forged": True}}
        )
    )
    assert sink.events[0].data == {"message": "retained evidence", "metadata": META}
    plain = Sink()
    await _MessageObserverSink(plain).emit(
        Event(type="user_message", data={"message": "personal_work_result", "metadata": META})
    )
    assert plain.events[0].data == {"message": "personal_work_result"}


async def test_real_orchestrator_stamps_message_and_persisted_start_event(tmp_path, monkeypatch):
    from tests.core.test_orchestrator_turn_start_event import _FakeStore, _FakeRuntime
    from src.core.agent_config import AgentConfig
    from src.core.orchestrator import SessionOrchestrator
    from src.core.types import Session

    session = Session(
        id="s", agent_config=AgentConfig(id="a", name="A"), cwd=str(tmp_path), user_id="owner"
    )
    store = _FakeStore(session)
    runtime = _FakeRuntime(store)
    monkeypatch.setattr("src.runtimes.factory.create_runtime", lambda *args, **kwargs: runtime)
    orch = SessionOrchestrator(store)
    message = await orch.run_turn(
        "owner", "s", UserMessage(text="full original evidence", metadata=META)
    )
    assert message.metadata["background_input"] == META["background_input"]
    start = next(e for e in store.appended if e.type == "user_message")
    assert start.data["message"] == "full original evidence"
    assert start.data["metadata"] == META
    await orch.shutdown()

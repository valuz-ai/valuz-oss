"""PersistThenBroadcastSink + live-frame ``seq`` stamping.

PR #85 review follow-up: the backfill/live boundary of the event streams
is deduplicated by giving live frames of PERSISTED events their storage
row id. These pin the mechanism end to end: sequential persist→stamp→
broadcast, live-only types flowing unstamped, failure isolation, and the
wire projection (``live_event_to_data`` lifting ``data["seq"]`` to the
``EventData.seq`` field).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from app.serializers import live_event_to_data
from src.adapters.database_sink import DatabaseEventSink
from src.adapters.persist_then_broadcast_sink import PersistThenBroadcastSink
from src.core.events import Event


class _FakeStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.appended: list[Event] = []
        self._next_seq = 100

    async def append_event(self, session_id: str, message_id: str, event: Event) -> int:
        if self.fail:
            raise RuntimeError("db down")
        self.appended.append(event)
        self._next_seq += 1
        return self._next_seq


class _RecordingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        if self.fail:
            raise RuntimeError("live sink dead")
        self.events.append(event)


def _sink_pair(*, store_fail: bool = False, live_fail: bool = False):
    store = _FakeStore(fail=store_fail)
    live = _RecordingSink(fail=live_fail)
    db = DatabaseEventSink(store, "sess-1", "msg-1")  # type: ignore[arg-type]
    return store, live, PersistThenBroadcastSink(db, live)


def test_persisted_event_broadcasts_with_seq_stamped() -> None:
    store, live, sink = _sink_pair()
    asyncio.run(sink.emit(Event(type="tool_use", data={"name": "echo"}, timestamp=7)))

    # Persisted WITHOUT the stamp; broadcast WITH it.
    assert store.appended[0].data == {"name": "echo"}
    assert live.events[0].data == {"name": "echo", "seq": 101}
    assert live.events[0].timestamp == 7


def test_live_only_delta_skips_db_and_carries_no_seq() -> None:
    store, live, sink = _sink_pair()
    asyncio.run(sink.emit(Event(type="text_delta", data={"text": "t"})))

    assert store.appended == []
    assert "seq" not in live.events[0].data


def test_db_failure_still_broadcasts_unstamped() -> None:
    _, live, sink = _sink_pair(store_fail=True)
    asyncio.run(sink.emit(Event(type="tool_use", data={})))

    assert len(live.events) == 1
    assert "seq" not in live.events[0].data


def test_live_failure_never_blocks_persistence() -> None:
    store, _, sink = _sink_pair(live_fail=True)
    asyncio.run(sink.emit(Event(type="tool_use", data={})))

    assert len(store.appended) == 1  # persisted despite the dead live sink


def test_live_event_to_data_lifts_seq_to_wire_field() -> None:
    stamped = Event(type="tool_use", data={"name": "echo", "seq": 42}, timestamp=1)
    frame = live_event_to_data(stamped, session_id="sess-1")
    assert frame.seq == 42
    assert frame.session_id == "sess-1"
    assert "seq" not in frame.data  # lifted, not duplicated

    unstamped = Event(type="text_delta", data={"text": "t"}, timestamp=1)
    assert live_event_to_data(unstamped).seq is None


def test_payload_seq_on_non_persisted_event_is_stripped() -> None:
    """The security-load-bearing branch: an agent-controllable ``seq`` in a
    live-only event's payload must NOT reach consumers — it would advance
    their dedup cursor past legitimate events."""
    store, live, sink = _sink_pair()
    asyncio.run(sink.emit(Event(type="text_delta", data={"text": "t", "seq": 999})))

    assert store.appended == []  # still live-only
    assert "seq" not in live.events[0].data
    assert live.events[0].data["text"] == "t"


def test_payload_seq_on_persisted_event_is_overwritten_by_row_id() -> None:
    """A persisted event can't choose its own seq either — the DB row id
    wins over whatever the payload carried."""
    store, live, sink = _sink_pair()
    asyncio.run(sink.emit(Event(type="tool_use", data={"name": "x", "seq": 999})))

    assert live.events[0].data["seq"] == 101  # the fake store's row id


def test_payload_seq_stripped_even_when_db_fails() -> None:
    """DB-failure fallback must not leak the payload seq unstamped."""
    _, live, sink = _sink_pair(store_fail=True)
    asyncio.run(sink.emit(Event(type="tool_use", data={"seq": 999})))

    assert "seq" not in live.events[0].data

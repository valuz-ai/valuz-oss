"""Split-cursor + ``event_uid`` dedup on the user-level control-plane SSE
(``iter_user_events_sse``).

Mirror of ``test_event_sse_adapter_dedup.py`` for the user stream: the
kernel store is LOCAL-authority, so the owner tap's live frames carry the
kernel's LOCAL seq while the durable backfill floor reads the DURABLE
store's own seq — independent counters that must never be compared. Pins:

- the backfill cursor is advanced ONLY by history-sourced frames; a live
  frame with a huge local seq never drags it forward;
- live/history dedup keys on ``event_uid`` in both directions through one
  shared seen-set;
- heartbeats advertise the HISTORY cursor only;
- uid-less live lifecycle frames (legacy kernels) always flow.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.* (sys.path)
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*

from valuz_agent.adapters import event_sse_adapter as adapter
from valuz_agent.adapters.data_reader import bind_data_reader


def _ev(seq: int, session_id: str, type_: str, *, uid: str | None = None, **data):
    return SimpleNamespace(
        seq=seq, session_id=session_id, type=type_, data=data, timestamp=seq, event_uid=uid
    )


class CursorRecordingReader:
    """Minimal DataReader recording every backfill ``after_seq``."""

    def __init__(self, events):
        self._events = events
        self.after_seqs: list[int] = []

    async def get_events_after_for_user(self, user_id, *, after_seq=0, types=None, limit=200):
        self.after_seqs.append(after_seq)
        rows = [e for e in self._events if e.seq > after_seq and (types is None or e.type in types)]
        return rows[:limit]


@pytest.fixture
def bind_reader():
    bound = {}

    def _bind(events):
        reader = CursorRecordingReader(events)
        bound["reader"] = reader
        bind_data_reader(reader)
        return reader

    yield _bind
    bind_data_reader(None)


@pytest.fixture
def live_tap(monkeypatch):
    """Patch the owner cross-session live tap with a fixed event list."""
    events: list = []

    async def _fake(user_id, types=None):
        for e in events:
            yield e
        # Park forever so the merge loop falls into its floor/heartbeat branch
        # (the pump would otherwise re-subscribe and replay the same frames).
        await asyncio.Event().wait()

    monkeypatch.setattr(adapter.kernel_client, "subscribe_all_events_for", _fake)

    def _set(evs):
        events.clear()
        events.extend(evs)

    return _set


def _drive(monkeypatch, reader, *, after_seq=0, min_backfills=2, heartbeats=None):
    """Consume the stream until ``min_backfills`` floor reads fired.

    Shrinks the floor + heartbeat intervals so the run terminates fast;
    returns the delivered (non-heartbeat) frames.
    """
    monkeypatch.setattr(adapter, "CONTROL_BACKFILL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(adapter, "IDLE_HEARTBEAT_SECONDS", 0.15)
    monkeypatch.setattr(adapter, "POLL_INTERVAL_SECONDS", 0.02)

    async def _collect() -> list[dict]:
        frames: list[dict] = []
        gen = adapter.iter_user_events_sse("user-A", after_seq=after_seq)
        try:
            while True:
                frame = await asyncio.wait_for(gen.__anext__(), timeout=2)
                if frame.get("event") == "heartbeat":
                    if heartbeats is not None:
                        heartbeats.append(json.loads(frame["data"]))
                    if len(reader.after_seqs) >= min_backfills:
                        break
                    continue
                frames.append(frame)
        except TimeoutError:
            pass
        finally:
            await gen.aclose()
        return frames

    return asyncio.run(_collect())


def test_live_seq_never_advances_the_backfill_cursor(monkeypatch, bind_reader, live_tap) -> None:
    # Correctness-critical: the tap's kernel-LOCAL seq must never become the
    # durable ``after_seq`` — otherwise the floor silently skips durable
    # lifecycle history sitting between the cursors.
    reader = bind_reader([_ev(2, "s", "session_idle", uid="u-hist")])
    live_tap([_ev(999_999, "s", "session_idle", uid="u-live")])
    heartbeats: list = []

    frames = _drive(monkeypatch, reader, min_backfills=3, heartbeats=heartbeats)

    assert [json.loads(f["data"])["seq"] for f in frames] == [2, 999_999]
    # after_seqs[0] is the initial backfill (client after_seq=0); every floor
    # read after it resumes from the last HISTORY seq (2) — the live frame's
    # local seq never leaks into the durable cursor.
    assert len(reader.after_seqs) >= 3
    assert reader.after_seqs[0] == 0
    assert all(s == 2 for s in reader.after_seqs[1:])
    # Heartbeats advertise the history cursor only (the one seq-space the
    # client may echo back as after_seq).
    assert heartbeats and all(h["seq"] == 2 for h in heartbeats)


def test_live_frame_deduped_when_floor_replays_its_uid(monkeypatch, live_tap) -> None:
    # Live-first direction: a lifecycle event delivered by the tap (local
    # seq 101) is later re-read by the durable floor (durable seq 5). The
    # shared uid seen-set suppresses the second copy — while the cursor
    # still advances on the history row's durable seq.
    class _Reader:
        """Durable row appears only from the SECOND read onward (write-through
        lag): the initial backfill misses it, a later floor read replays it."""

        def __init__(self):
            self.after_seqs: list[int] = []

        async def get_events_after_for_user(self, user_id, *, after_seq=0, types=None, limit=200):
            self.after_seqs.append(after_seq)
            if len(self.after_seqs) < 2:
                return []
            row = _ev(5, "s", "session_idle", uid="uX")
            return [row] if row.seq > after_seq else []

    reader = _Reader()
    bind_data_reader(reader)
    try:
        live_tap([_ev(101, "s", "session_idle", uid="uX")])
        frames = _drive(monkeypatch, reader, min_backfills=3)
    finally:
        bind_data_reader(None)

    # Exactly one copy delivered (the live one), carrying its uid.
    assert len(frames) == 1
    payload = json.loads(frames[0]["data"])
    assert payload["seq"] == 101
    assert payload["event_uid"] == "uX"
    # The deduped history row STILL advanced the durable cursor: later floor
    # reads resume from its durable seq (5), not from 0 (and never from 101).
    assert reader.after_seqs[0] == 0
    assert reader.after_seqs[-1] == 5


def test_uid_less_live_lifecycle_frames_always_flow(monkeypatch, bind_reader, live_tap) -> None:
    # Legacy kernels mint no event_uid; their local seq is not comparable to
    # the durable cursor, so uid-less lifecycle frames pass through even when
    # their seq sits at/under the client's after_seq.
    reader = bind_reader([])
    live_tap([_ev(3, "s", "session_idle")])  # uid=None, seq ≤ after_seq

    frames = _drive(monkeypatch, reader, after_seq=3, min_backfills=1)

    assert len(frames) == 1
    payload = json.loads(frames[0]["data"])
    assert payload["seq"] == 3
    assert payload["event_uid"] is None


def test_backfill_frames_carry_event_uid_on_the_wire(monkeypatch, bind_reader, live_tap) -> None:
    reader = bind_reader(
        [
            _ev(1, "s", "user_message", uid="u1"),
            _ev(2, "s", "session_idle"),  # legacy row: no uid
        ]
    )
    live_tap([])

    frames = _drive(monkeypatch, reader, min_backfills=1)

    payloads = [json.loads(f["data"]) for f in frames]
    assert payloads[0]["event_uid"] == "u1"
    assert payloads[1]["event_uid"] is None  # key present, null for legacy rows

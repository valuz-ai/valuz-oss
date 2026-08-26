"""Consumer-side dedup in the host SSE adapter (``iter_events_sse``).

The kernel event store is LOCAL-authority: live kernel frames carry the
kernel's LOCAL seq while history reads carry the DURABLE store's own seq —
two independent counters that must never be compared. These tests drive
the adapter with a fake seam and pin the split-cursor contract:

- live/history dedup keys on the store-independent ``event_uid`` (one
  shared seen-set, both directions: a backfilled uid suppresses its live
  copy, and a live-delivered uid suppresses its later history backfill);
- the durable ``history_cursor`` is advanced ONLY by history-sourced
  frames — a live frame's (kernel-local) seq never touches it;
- heartbeats advertise the HISTORY cursor only (the one seq-space a
  client may echo back as ``after_seq``);
- uid-less frames (live-only deltas, legacy kernels) always flow.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import asyncio
import json

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from app.schemas import EventData

from valuz_agent.adapters import event_sse_adapter


def _live(seq: int | None, *, uid: str | None = None, type: str = "session_idle") -> EventData:
    # ``session_idle`` translates to a legacy frame, so it always renders.
    return EventData(
        type=type, data={"stop_reason": "end_turn"}, timestamp=1, seq=seq, event_uid=uid
    )


def _hist(seq: int, *, uid: str | None = None, type: str = "session_idle") -> EventData:
    """A durable-store row (history read) — seq is the DURABLE counter."""
    return EventData(type=type, data={}, timestamp=1, seq=seq, message_id="m", event_uid=uid)


def _drive(
    monkeypatch,
    *,
    backfill: list[EventData],
    live: list[EventData],
    polls: list,
    min_polls: int = 0,
    poll_pages: list[list[EventData]] | None = None,
    heartbeats: list | None = None,
):
    """Run iter_events_sse against a fake seam; return delivered frames.

    ``polls`` records the ``after_seq`` of every DB poll AFTER the initial
    backfill read; each poll returns the next page from ``poll_pages`` (or
    ``[]``). With ``min_polls`` the run is held open until that many polls
    fired — deterministic cursor-advance assertions without timing
    sensitivity. ``heartbeats`` (if given) collects heartbeat payloads.
    """
    polls_reached = asyncio.Event()
    pages = list(poll_pages or [])
    seen_initial = False

    async def _fake_get_events(_user_id, session_id, *, limit=200, offset=0, after_seq=None):
        nonlocal seen_initial
        first = not seen_initial
        seen_initial = True
        if first and backfill:
            return list(backfill)
        polls.append(after_seq)
        if len(polls) >= min_polls:
            polls_reached.set()
        return pages.pop(0) if pages else []

    async def _fake_subscribe(_user_id, session_id):
        for item in live:
            yield item
        # Then idle forever so the adapter falls into its poll branch.
        await asyncio.Event().wait()

    monkeypatch.setattr(event_sse_adapter.kernel_client, "get_events", _fake_get_events)
    monkeypatch.setattr(
        event_sse_adapter.kernel_client, "subscribe_session_events", _fake_subscribe
    )
    # The adapter's live pump attaches via the peek-only variant (never
    # provisions) — fake it with the same stream.
    monkeypatch.setattr(
        event_sse_adapter.kernel_client, "subscribe_session_events_existing", _fake_subscribe
    )

    async def _collect() -> list[dict]:
        frames: list[dict] = []
        gen = event_sse_adapter.iter_events_sse("sess-1", after_seq=0)
        try:
            while True:
                frame = await asyncio.wait_for(gen.__anext__(), timeout=2)
                if frame.get("event") == "heartbeat":
                    if heartbeats is not None:
                        heartbeats.append(json.loads(frame["data"]))
                    if min_polls and not polls_reached.is_set():
                        continue  # hold open until the poll quota is met
                    break  # idle reached — everything deliverable was delivered
                frames.append(frame)
        except TimeoutError:
            pass
        finally:
            await gen.aclose()
        return frames

    # Shrink the heartbeat threshold so the run terminates quickly.
    monkeypatch.setattr(event_sse_adapter, "IDLE_HEARTBEAT_SECONDS", 0.3)
    monkeypatch.setattr(event_sse_adapter, "POLL_INTERVAL_SECONDS", 0.05)
    return asyncio.run(_collect())


def test_live_frame_already_covered_by_backfill_is_skipped_by_uid(monkeypatch) -> None:
    # The same persisted event arrives twice: once via the durable backfill
    # (durable seq 5) and once live (kernel-LOCAL seq 105 — a DIFFERENT
    # number for the SAME event). Only the shared ``event_uid`` identifies
    # the duplicate.
    polls: list = []
    frames = _drive(
        monkeypatch,
        backfill=[_hist(5, uid="u5")],
        live=[_live(105, uid="u5")],  # the duplicate from the overlap window
        polls=polls,
    )
    assert len(frames) == 1  # backfill copy only
    assert json.loads(frames[0]["data"])["event_uid"] == "u5"


def test_repeated_live_uid_is_delivered_exactly_once(monkeypatch) -> None:
    frames = _drive(
        monkeypatch,
        backfill=[],
        live=[_live(7, uid="u7"), _live(7, uid="u7"), _live(8, uid="u8")],
        polls=[],
    )
    assert len(frames) == 2  # u7 once, u8 once


def test_live_frame_deduped_when_history_backfill_replays_its_uid(monkeypatch) -> None:
    # The other direction across the boundary: an event delivered LIVE first
    # (kernel-local seq 101) is later re-read by the durable poll (durable
    # seq 5). The uid seen-set must suppress the second copy — while the
    # history cursor STILL advances on the history row's durable seq.
    polls: list = []
    frames = _drive(
        monkeypatch,
        backfill=[],
        live=[_live(101, uid="uX")],
        poll_pages=[[_hist(5, uid="uX")]],
        polls=polls,
        min_polls=3,
    )
    assert len(frames) == 1  # the live copy only
    assert json.loads(frames[0]["data"])["event_uid"] == "uX"
    # polls[0] is the first idle poll (after_seq=0 — untouched by the live
    # frame's local seq 101); it returned the history dup (durable seq 5),
    # so every later poll resumes from 5.
    assert polls[0] == 0
    assert all(p == 5 for p in polls[1:])


def test_live_seq_never_advances_the_history_cursor(monkeypatch) -> None:
    # Correctness-critical: the kernel's LOCAL seq (live frames) and the
    # durable seq (backfill cursor) are INDEPENDENT counters. A live frame
    # with a huge local seq must not drag the durable cursor forward —
    # otherwise the backfill would silently skip durable history.
    polls: list = []
    heartbeats: list = []
    _drive(
        monkeypatch,
        backfill=[_hist(5, uid="u5")],
        live=[_live(999_999, uid="u-live")],
        polls=polls,
        min_polls=3,
        heartbeats=heartbeats,
    )
    # Every durable poll resumes from the last HISTORY seq (5), never from
    # the live frame's local seq.
    assert len(polls) >= 3
    assert all(p == 5 for p in polls)
    # Heartbeats advertise the history cursor only — the one seq-space the
    # client may echo back as after_seq on reconnect.
    assert heartbeats and all(h["seq"] == 5 for h in heartbeats)


def test_unstamped_delta_frames_always_flow(monkeypatch) -> None:
    live = [
        EventData(type="text_delta", data={"text": "a"}, timestamp=1, seq=None),
        EventData(type="text_delta", data={"text": "b"}, timestamp=1, seq=None),
    ]
    frames = _drive(monkeypatch, backfill=[], live=live, polls=[])
    assert len(frames) == 2


def test_uid_less_live_frames_flow_even_with_a_stamped_seq(monkeypatch) -> None:
    # Legacy kernels stamp a seq but mint no event_uid. Their seq is
    # kernel-local — comparing it against the durable cursor is forbidden —
    # so uid-less frames always pass through (never deduped).
    frames = _drive(
        monkeypatch,
        backfill=[_hist(50, uid="u50")],
        live=[_live(3, uid=None)],  # local seq below the durable cursor
        polls=[],
    )
    assert len(frames) == 2  # the backfill frame AND the uid-less live frame


def test_history_frames_carry_event_uid_on_the_wire(monkeypatch) -> None:
    polls: list = []
    frames = _drive(
        monkeypatch,
        backfill=[_hist(1, uid="u1"), _hist(2, uid=None)],  # legacy row: no uid
        live=[],
        polls=polls,
    )
    payloads = [json.loads(f["data"]) for f in frames]
    assert payloads[0]["event_uid"] == "u1"
    assert payloads[1]["event_uid"] is None  # legacy rows: key present, null


def test_list_events_after_pages_under_the_kernel_cap(monkeypatch) -> None:
    """A >1000 request pages in chunks of 1000 so it returns the full set
    over HTTP — where the route's Query(le=1000) would reject a single
    limit=2000 call (the in-process client silently dodged that)."""
    import asyncio

    from valuz_agent.adapters import event_sse_adapter as adp

    calls: list[tuple] = []

    async def _fake_get_events(_user_id, session_id, *, limit=200, offset=0, after_seq=None):
        calls.append((after_seq, limit))
        assert limit <= 1000, "host must never ask the kernel for >1000"
        # 2500 total events (seq 1..2500); page from after_seq.
        start = (after_seq or 0) + 1
        end = min(start + limit, 2501)
        return [
            EventData(type="tool_use", data={}, timestamp=1, seq=s, message_id="m")
            for s in range(start, end)
        ]

    monkeypatch.setattr(adp.kernel_client, "get_events", _fake_get_events)
    frames = asyncio.run(adp.list_events_after("s", after_seq=0, limit=2000))

    # Got the full 2000 (not truncated at 1000), in three ≤1000 pages.
    assert len(frames) == 2000
    assert all(limit <= 1000 for _, limit in calls)
    assert len(calls) == 2  # 1000 + 1000

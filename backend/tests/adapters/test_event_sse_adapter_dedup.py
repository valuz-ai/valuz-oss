"""Consumer-side dedup in the host SSE adapter (``iter_events_sse``).

PR #87 review follow-up: the producer-side stamping had tests, the
consumer rules didn't. These drive the adapter with a fake seam and pin:

- a live frame whose ``seq`` the cursor already covers is skipped;
- a repeated live ``seq`` is forwarded exactly once;
- live frames ADVANCE the cursor, so the idle DB poll never re-reads
  what was already delivered live (the legacy double-delivery fix);
- unstamped (live-only delta) frames always flow.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import asyncio

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from app.schemas import EventData

from valuz_agent.adapters import event_sse_adapter


def _live(seq: int | None, *, type: str = "session_idle") -> EventData:
    # ``session_idle`` translates to a legacy frame, so it always renders.
    return EventData(type=type, data={"stop_reason": "end_turn"}, timestamp=1, seq=seq)


def _drive(
    monkeypatch,
    *,
    backfill: list[EventData],
    live: list[EventData],
    polls: list,
    min_polls: int = 0,
):
    """Run iter_events_sse against a fake seam; return delivered frames.

    ``polls`` records the ``after_seq`` of every DB poll; each poll
    returns [] (the live path is what's under test). With ``min_polls``
    the run is held open until that many polls fired — deterministic
    cursor-advance assertions without timing sensitivity.
    """
    polls_reached = asyncio.Event()

    async def _fake_get_events(_user_id, session_id, *, limit=200, offset=0, after_seq=None):
        if after_seq == 0 and backfill:
            page, backfill[:] = list(backfill), []
            return page
        polls.append(after_seq)
        if len(polls) >= min_polls:
            polls_reached.set()
        return []

    async def _fake_subscribe(_user_id, session_id):
        for item in live:
            yield item
        # Then idle forever so the adapter falls into its poll branch.
        await asyncio.Event().wait()

    monkeypatch.setattr(event_sse_adapter.kernel_client, "get_events", _fake_get_events)
    monkeypatch.setattr(
        event_sse_adapter.kernel_client, "subscribe_session_events", _fake_subscribe
    )

    async def _collect() -> list[dict]:
        frames: list[dict] = []
        gen = event_sse_adapter.iter_events_sse("sess-1", after_seq=0)
        try:
            while True:
                frame = await asyncio.wait_for(gen.__anext__(), timeout=2)
                if frame.get("event") == "heartbeat":
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


def test_live_frame_already_covered_by_backfill_is_skipped(monkeypatch) -> None:
    polls: list = []
    backfill = [
        EventData(type="session_idle", data={}, timestamp=1, seq=5, message_id="m"),
    ]
    frames = _drive(
        monkeypatch,
        backfill=backfill,
        live=[_live(5)],  # the duplicate from the overlap window
        polls=polls,
    )
    assert len(frames) == 1  # backfill copy only


def test_repeated_live_seq_is_delivered_exactly_once(monkeypatch) -> None:
    frames = _drive(
        monkeypatch,
        backfill=[],
        live=[_live(7), _live(7), _live(8)],
        polls=[],
    )
    assert len(frames) == 2  # 7 once, 8 once


def test_live_frames_advance_the_poll_cursor(monkeypatch) -> None:
    polls: list = []
    # min_polls=3 holds the stream open until the initial backfill read
    # plus at least two idle polls fired — the assertions below can never
    # pass vacuously on a short run.
    _drive(monkeypatch, backfill=[], live=[_live(9)], polls=polls, min_polls=3)
    # polls[0] is the initial (empty) backfill read at after_seq=0; every
    # idle poll AFTER the live frame starts at 9 — the legacy
    # double-delivery (poll re-reading live-delivered events) is gone.
    assert len(polls) >= 3
    assert polls[0] == 0
    assert all(p == 9 for p in polls[1:])


def test_unstamped_delta_frames_always_flow(monkeypatch) -> None:
    live = [
        EventData(type="text_delta", data={"text": "a"}, timestamp=1, seq=None),
        EventData(type="text_delta", data={"text": "b"}, timestamp=1, seq=None),
    ]
    frames = _drive(monkeypatch, backfill=[], live=live, polls=[])
    assert len(frames) == 2


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

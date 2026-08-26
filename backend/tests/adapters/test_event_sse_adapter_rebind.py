"""The live tap must follow a session that moves to a different sandbox.

Regression for a silent live-streaming loss under scoped (per-turn) allocation:
chat provisions a FRESH instance for every turn after the first, and the
previous instance is left RUNNING (reclaimed by its TTL, not stopped at swap
time). Nothing therefore ends the host's existing subscription — the kernel's
``events/stream`` is an unbounded loop and the HTTP client sets no read timeout
— so the tap stays pinned to the old instance for the rest of the connection
while the new one streams to nobody.

Observable symptom before the fix: from turn 2 on, no token streaming. Persisted
events still arrive via the 2s durable backfill (in batches); every live-only
frame — ``text_delta``, ``thinking_delta``, ``tool_*_delta``, ``workflow_progress``,
``bg_task_*``, which the DB sink never persists — is lost.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import asyncio
import json

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from app.schemas import EventData

from valuz_agent.adapters import event_sse_adapter


def _delta(text: str) -> EventData:
    """A live-ONLY frame — the class of event the stale tap loses entirely."""
    return EventData(type="text_delta", data={"text": text}, timestamp=1, seq=None)


class _Fleet:
    """Two sandboxes for one session, swapped mid-stream.

    ``instance`` is what ``current_kernel_id`` reports. A subscription opened
    against an instance keeps yielding that instance's frames and then idles
    FOREVER — faithfully reproducing a stream that is still connected to the
    sandbox the session no longer runs on.
    """

    def __init__(self, frames: dict[str, list[EventData]]) -> None:
        self.instance = "https://old.pool"
        self.frames = frames
        self.subscribed: list[str] = []

    async def current_kernel_id(self, _user_id: str, _session_id: str) -> str:
        return self.instance

    async def subscribe(self, _user_id: str, _session_id: str):
        bound = self.instance
        self.subscribed.append(bound)
        for item in self.frames.get(bound, []):
            yield item
        await asyncio.Event().wait()  # never ends on its own — the whole point


def _run(monkeypatch, fleet: _Fleet, *, swap_after: float) -> list[dict]:
    async def _no_history(_user_id, _session_id, *, limit=200, offset=0, after_seq=None):
        return []

    monkeypatch.setattr(event_sse_adapter.kernel_client, "get_events", _no_history)
    monkeypatch.setattr(
        event_sse_adapter.kernel_client, "current_kernel_id", fleet.current_kernel_id
    )
    monkeypatch.setattr(
        event_sse_adapter.kernel_client, "subscribe_session_events_existing", fleet.subscribe
    )
    monkeypatch.setattr(event_sse_adapter, "KERNEL_REBIND_POLL_SECONDS", 0.05)
    monkeypatch.setattr(event_sse_adapter, "POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(event_sse_adapter, "DB_BACKFILL_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(event_sse_adapter, "IDLE_HEARTBEAT_SECONDS", 1.0)

    async def _collect() -> list[dict]:
        async def _swap() -> None:
            await asyncio.sleep(swap_after)
            fleet.instance = "https://new.pool"

        swapper = asyncio.create_task(_swap())
        frames: list[dict] = []
        gen = event_sse_adapter.iter_events_sse("sess-1", "owner-1", after_seq=0)
        try:
            while True:
                frame = await asyncio.wait_for(gen.__anext__(), timeout=3)
                if frame.get("event") == "heartbeat":
                    break
                frames.append(frame)
        except TimeoutError:
            pass
        finally:
            swapper.cancel()
            await gen.aclose()
        return frames

    return asyncio.run(_collect())


def test_should_receive_the_new_instances_deltas_after_a_per_turn_swap(monkeypatch) -> None:
    fleet = _Fleet(
        {
            "https://old.pool": [_delta("turn-1 ")],
            "https://new.pool": [_delta("turn-2 "), _delta("streamed")],
        }
    )

    frames = _run(monkeypatch, fleet, swap_after=0.2)

    texts = [json.loads(f["data"])["payload"]["text"] for f in frames]
    assert texts == ["turn-1 ", "turn-2 ", "streamed"]


def test_should_rebind_onto_the_instance_the_session_moved_to(monkeypatch) -> None:
    fleet = _Fleet({"https://old.pool": [], "https://new.pool": []})

    _run(monkeypatch, fleet, swap_after=0.2)

    assert fleet.subscribed[0] == "https://old.pool"
    assert "https://new.pool" in fleet.subscribed


def test_should_not_churn_the_subscription_while_the_instance_is_unchanged(monkeypatch) -> None:
    """The watch must only tear the tap down on an actual swap — re-subscribing
    on a timer would drop live frames in every reconnect gap."""
    fleet = _Fleet({"https://old.pool": []})

    _run(monkeypatch, fleet, swap_after=99)  # never swaps within the run

    assert fleet.subscribed == ["https://old.pool"]

"""Server-side ``types=`` filtering on the kernel global event stream.

The host control plane consumes ``/kernel/v1/events/stream`` lifecycle-only;
without a server-side allowlist the owner's kernel ships every token delta
across the wire just for the host to discard them. This drives the real route
generator (same direct-drive pattern as ``test_events_stream_dedup``) with a
stub orchestrator and pins:

- only allowlisted event types are emitted;
- non-listed types (deltas, tool events) are dropped at the source;
- ``session_id`` stamping is preserved on the frames that do flow.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import asyncio
import json

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src/app


@pytest.mark.asyncio
async def test_stream_all_events_filters_types_server_side() -> None:
    from app.routes.events import stream_all_events  # type: ignore[import-not-found]
    from src.core.events import Event  # type: ignore[import-not-found]

    class _StubOrchestrator:
        def __init__(self) -> None:
            self.tap = None

        def attach_global_tap(self, tap) -> None:
            self.tap = tap

        def detach_global_tap(self, tap) -> None:
            self.tap = None

    orch = _StubOrchestrator()
    response = await stream_all_events(
        object(),  # request — the route deletes it (disconnect = cancel scope)
        orch,
        types="user_message,session_idle",
    )

    received: list[dict] = []

    async def _consume() -> None:
        async for item in response.body_iterator:
            if item.get("event") != "event":
                continue  # heartbeat
            received.append(json.loads(item["data"]))
            if len(received) >= 2:
                return

    task = asyncio.create_task(_consume())
    # The tap attaches on the generator's first iteration — wait for it.
    while orch.tap is None:
        await asyncio.sleep(0)
    # Interleave allowlisted and non-allowlisted types.
    await orch.tap.queue.put(("s1", Event(type="text_delta", data={"text": "x"})))
    await orch.tap.queue.put(("s1", Event(type="user_message", data={})))
    await orch.tap.queue.put(("s2", Event(type="tool_use", data={"name": "t"})))
    await orch.tap.queue.put(("s2", Event(type="session_idle", data={})))
    try:
        await asyncio.wait_for(task, timeout=10)
    finally:
        await response.body_iterator.aclose()

    assert [f["type"] for f in received] == ["user_message", "session_idle"]
    assert [f["session_id"] for f in received] == ["s1", "s2"]
    assert orch.tap is None  # detached on close — no zombie tap


@pytest.mark.asyncio
async def test_stream_all_events_unfiltered_passes_everything() -> None:
    # The decision aggregator consumes this stream with NO allowlist — it
    # needs requires_action / action_resolved and friends. No ``types`` ⇒
    # every event flows (the pre-filter behavior, unchanged).
    from app.routes.events import stream_all_events  # type: ignore[import-not-found]
    from src.core.events import Event  # type: ignore[import-not-found]

    class _StubOrchestrator:
        def __init__(self) -> None:
            self.tap = None

        def attach_global_tap(self, tap) -> None:
            self.tap = tap

        def detach_global_tap(self, tap) -> None:
            self.tap = None

    orch = _StubOrchestrator()
    response = await stream_all_events(object(), orch, types=None)

    received: list[dict] = []

    async def _consume() -> None:
        async for item in response.body_iterator:
            if item.get("event") != "event":
                continue
            received.append(json.loads(item["data"]))
            if len(received) >= 2:
                return

    task = asyncio.create_task(_consume())
    while orch.tap is None:
        await asyncio.sleep(0)
    await orch.tap.queue.put(("s1", Event(type="text_delta", data={"text": "x"})))
    await orch.tap.queue.put(("s1", Event(type="requires_action", data={})))
    try:
        await asyncio.wait_for(task, timeout=10)
    finally:
        await response.body_iterator.aclose()

    assert [f["type"] for f in received] == ["text_delta", "requires_action"]

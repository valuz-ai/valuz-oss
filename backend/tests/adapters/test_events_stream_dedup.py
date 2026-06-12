"""Exactly-once delivery on the kernel ``events/stream`` endpoint.

PR #87 review follow-up: the stamping (producer) side had tests; this
drives the real stream route's frame generator and pins the consumer
rules:

- the backfill advances the cursor as it pages, so a live frame from the
  tap-before-backfill overlap window (same ``seq``) is skipped;
- repeated live ``seq`` is delivered exactly once;
- fresh live frames (higher ``seq``) and unstamped delta frames flow.

The overlap is injected deterministically: live frames are emitted onto
the session bus (``emit_session_event(create_bus=True)``) with a stamped
``data["seq"]`` AFTER the stream's backfilled frames are observed — by
then the tap is attached (attach happens before backfill), which is
exactly the race window the dedup must close.

The route function is driven directly and its ``body_iterator`` consumed
(with a stub ``Request``): sse-starlette's disconnect listener doesn't
cooperate with httpx's ASGITransport, and the unit under test is the
frame generator's dedup logic, not the SSE envelope.

Scope note (conscious trade-off): events are injected via
``append_event`` / ``emit_session_event``, NOT by running a real turn —
the ``run_turn`` → ``PersistThenBroadcastSink`` wiring is covered by the
sink's unit tests (``test_persist_then_broadcast.py``); stitching a live
runtime into this test would buy little for its cost.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import asyncio
import json
import sys
import uuid

import pytest


@pytest.fixture
async def kernel_app(tmp_path, monkeypatch):
    """Kernel routers on a fresh FastAPI app over a private, migrated DB."""
    monkeypatch.setenv("VALUZ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VALUZ_DB_FILENAME", "stream-dedup.db")

    saved_modules = {
        name: mod
        for name, mod in sys.modules.items()
        if name.startswith(("valuz_agent.infra.config", "valuz_agent.boot.kernel"))
    }
    for name in saved_modules:
        sys.modules.pop(name, None)

    import valuz_agent.boot.kernel as kb

    kb.run_kernel_migrations()

    from fastapi import FastAPI

    from app.config import AppConfig  # type: ignore[import-not-found]
    from app.dependencies import (  # type: ignore[import-not-found]
        get_orchestrator,
        get_store,
        init_dependencies,
        shutdown_dependencies,
    )

    await init_dependencies(AppConfig())
    app = FastAPI()
    for router in kb.get_kernel_routers():
        app.include_router(router)
    try:
        yield app, get_store(), get_orchestrator()
    finally:
        await shutdown_dependencies()
        for name in [
            n
            for n in sys.modules
            if n.startswith(("valuz_agent.infra.config", "valuz_agent.boot.kernel"))
        ]:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)


@pytest.mark.asyncio
async def test_stream_delivers_each_event_exactly_once_across_the_boundary(kernel_app) -> None:
    app, store, orchestrator = kernel_app
    del app  # routers are mounted; we drive the route function directly

    from app.routes.sessions import stream_session_events  # type: ignore[import-not-found]
    from src.core.agent_config import AgentConfig  # type: ignore[import-not-found]
    from src.core.events import Event  # type: ignore[import-not-found]
    from src.core.types import Session  # type: ignore[import-not-found]

    session_id = str(uuid.uuid4())
    await store.save_session(
        Session(id=session_id, agent_config=AgentConfig(id="a", name="a"), cwd="/tmp/x")
    )
    # Three persisted events (seq 1..3 in a fresh DB). No FK constraints —
    # the anchor message id doesn't need a row.
    persisted_seqs = []
    for i in range(3):
        seq = await store.append_event(
            session_id, "m-test", Event(type="tool_use", data={"name": f"t{i}"})
        )
        persisted_seqs.append(seq)
    last_seq = persisted_seqs[-1]

    class _StubRequest:
        async def is_disconnected(self) -> bool:
            return False

    response = await stream_session_events(
        session_id,
        _StubRequest(),  # type: ignore[arg-type]
        store,
        orchestrator,
        after_seq=0,
    )

    received: list[dict] = []

    async def _consume() -> None:
        emitted_overlap = False
        async for item in response.body_iterator:
            if item.get("event") != "event":
                continue  # heartbeat
            frame = json.loads(item["data"])
            received.append(frame)

            if len(received) == 3 and not emitted_overlap:
                emitted_overlap = True
                # The tap attached before backfill — these land in the
                # live queue:
                # 1. the overlap duplicate (same seq as the last
                #    backfilled event),
                dup = Event(type="tool_use", data={"name": "t2", "seq": last_seq})
                await orchestrator.emit_session_event(session_id, dup, create_bus=True)
                # 2. the same duplicate AGAIN (repeated live seq),
                await orchestrator.emit_session_event(session_id, dup, create_bus=True)
                # 3. a fresh stamped event,
                fresh = Event(type="tool_use", data={"name": "t3", "seq": last_seq + 1})
                await orchestrator.emit_session_event(session_id, fresh, create_bus=True)
                # 4. an unstamped live-only delta.
                delta = Event(type="text_delta", data={"text": "d"})
                await orchestrator.emit_session_event(session_id, delta, create_bus=True)
            if len(received) >= 5:
                return  # 3 backfill + fresh + delta

    try:
        await asyncio.wait_for(_consume(), timeout=15)
    finally:
        # Close the generator so the tap detaches.
        await response.body_iterator.aclose()

    # Exactly once: 3 backfilled frames, the fresh live frame, the delta —
    # and NO duplicate of last_seq beyond the backfill copy.
    seqs = [f.get("seq") for f in received]
    assert seqs[:3] == persisted_seqs
    assert seqs.count(last_seq) == 1
    assert (last_seq + 1) in seqs
    assert [f["type"] for f in received].count("text_delta") == 1
    assert len(received) == 5

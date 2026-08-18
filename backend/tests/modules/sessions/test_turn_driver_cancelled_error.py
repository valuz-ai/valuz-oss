"""An ``asyncio.CancelledError`` escaping ``kernel_client.run_turn`` is an
INTERRUPTION of the turn, never a "run failed".

Regression for the "run failed / CancelledError under a completed answer" report:
the host's ``except BaseException`` catch-all minted
``session_error{category: "CancelledError", message: "agent turn failed"}``
for a cancellation, which the client renders as the red failure card (retry /
switch model) and mirrors into a ``run_failed`` notification. The turn's answer
was already complete — the cancellation came out of the runtime's post-run
client teardown. Cancellation now classifies like the runtimes' own
``user_interrupt`` / ``interrupted`` stop reasons.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.infra.local_identity import resolve_local_user_id
from valuz_agent.modules.sessions import run_orchestrator as run_orch
from valuz_agent.modules.sessions import turn_driver

LOCAL_USER_ID = resolve_local_user_id()


def _as_async(fn: Any) -> Any:
    async def _inner(*a: Any, **k: Any) -> Any:
        return fn(*a, **k)

    return _inner


class _Bus:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, event_type: str, **payload: Any) -> None:
        self.published.append({"type": event_type, **payload})


def _drive(monkeypatch: pytest.MonkeyPatch, raise_exc: BaseException) -> dict[str, Any]:
    monkeypatch.setattr(turn_driver, "always_on_mcp_hook", lambda *a, **k: _as_async(lambda: None))

    class _Reader:
        async def get_session(self, *a: Any, **k: Any) -> Any:
            return SimpleNamespace(status="idle", stop_reason=None, metadata={})

    monkeypatch.setattr(turn_driver, "data_reader", lambda: _Reader())

    async def _raising_run_turn(*a: Any, **k: Any) -> Any:
        raise raise_exc

    monkeypatch.setattr(turn_driver.kernel_client, "run_turn", _raising_run_turn)
    live_events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        turn_driver.kernel_client,
        "emit_live_event",
        _as_async(lambda _u, _s, t, d: live_events.append((t, d))),
    )

    finalize_calls: list[dict[str, Any]] = []

    async def _fake_finalize(session_id: str, content: str, status: str, **k: Any) -> None:
        finalize_calls.append({"status": status, **k})

    monkeypatch.setattr(run_orch, "_finalize_session", _fake_finalize)

    bus = _Bus()
    returned = asyncio.run(
        turn_driver.run_session_to_idle("sess-1", "hi", bus, user_id=LOCAL_USER_ID)
    )
    return {
        "returned": returned,
        "finalize": finalize_calls,
        "live": live_events,
        "published": bus.published,
    }


def test_cancelled_error_is_an_interruption_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _drive(monkeypatch, asyncio.CancelledError())

    # Loop-local semantics: the caller sees an interruption…
    assert out["returned"] == "interrupted"
    # …the kernel store sees a clean, resumable idle with the cancellation
    # threaded through so finalize can record the interruption marker…
    assert len(out["finalize"]) == 1
    assert out["finalize"][0]["status"] == "idle"
    assert isinstance(out["finalize"][0]["error"], asyncio.CancelledError)
    # …no live ``session_error{category: CancelledError}`` is broadcast…
    assert out["live"] == []
    # …and the finish is published as an interruption, not a failure.
    finished = [p for p in out["published"] if p["type"] == "session.finished"]
    assert finished == [
        {"type": "session.finished", "session_id": "sess-1", "status": "interrupted"}
    ]


def test_real_exceptions_still_finalize_as_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _drive(monkeypatch, RuntimeError("boom"))

    assert out["returned"] == "terminated"
    assert out["finalize"][0]["status"] == "terminated"
    assert isinstance(out["finalize"][0]["error"], RuntimeError)
    assert out["live"] == [("session_error", {"category": "RuntimeError", "message": "boom"})]
    finished = [p for p in out["published"] if p["type"] == "session.finished"]
    assert finished[0]["status"] == "failed"

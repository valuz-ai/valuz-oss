"""A turn the user STOPPED must leave a durable marker of having been stopped.

The interrupt that raises (host teardown → ``CancelledError``) was already
covered by ``test_turn_driver_cancelled_error``. This is the one that does NOT
raise, which is the ordinary case: the user presses Stop, the runtime absorbs
the cancellation, stamps ``session.stop_reason = Error(category=
"user_interrupt")`` and returns a Message normally.

That path used to record nothing. No ``session_idle`` is emitted for it (the
orchestrator's ``release_session_idle`` has no pending event), the terminal
``session_update`` carries the SESSION status — a plain ``idle``, since
``cancelled`` is a ``MessageStatus`` and never a session one — and finalize saw
``error=None``, so it wrote no ``session_error`` either. The cancellation
survived only on the Message row, which no client surface reads.

Found in the wild: a conversation whose middle turn stores
``turn_phase×2 · session_update`` where both its neighbours store
``session_idle · session_update``. On reload it rebuilt as an ordinary
finished answer whose reply just stops, and everything downstream of the
transcript recorded it as finished.
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


# --------------------------------------------------------------------------
# 1. ``run_session_to_idle`` reads the category off the authoritative Message
# --------------------------------------------------------------------------


def _drive(monkeypatch: pytest.MonkeyPatch, stop_reason: dict[str, Any] | None) -> dict[str, Any]:
    """Run one turn whose ``run_turn`` RETURNS (never raises) with ``stop_reason``."""
    monkeypatch.setattr(turn_driver, "always_on_mcp_hook", lambda *a, **k: _as_async(lambda: None))

    class _Reader:
        async def get_session(self, *a: Any, **k: Any) -> Any:
            # Deliberately clean: the durable mirror lags the kernel, so the
            # outcome must come off the Message, not off this re-read.
            return SimpleNamespace(status="idle", stop_reason=None, metadata={})

    monkeypatch.setattr(turn_driver, "data_reader", lambda: _Reader())

    message = SimpleNamespace(
        id="msg-1",
        status="cancelled" if stop_reason else "completed",
        stop_reason=stop_reason,
    )
    monkeypatch.setattr(turn_driver.kernel_client, "run_turn", _as_async(lambda *a, **k: message))
    monkeypatch.setattr(
        turn_driver.kernel_client, "emit_live_event", _as_async(lambda *a, **k: None)
    )

    finalize_calls: list[dict[str, Any]] = []

    async def _fake_finalize(session_id: str, content: str, status: str, **k: Any) -> None:
        finalize_calls.append({"status": status, **k})

    monkeypatch.setattr(run_orch, "_finalize_session", _fake_finalize)

    bus = _Bus()
    returned = asyncio.run(
        turn_driver.run_session_to_idle("sess-1", "hi", bus, user_id=LOCAL_USER_ID)
    )
    return {"returned": returned, "finalize": finalize_calls, "published": bus.published}


def test_user_stop_threads_its_category_into_finalize(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _drive(
        monkeypatch,
        {
            "type": "error",
            "message": "cancelled",
            "category": "user_interrupt",
            "retry_status": "terminal",
        },
    )

    assert out["returned"] == "interrupted"
    assert len(out["finalize"]) == 1
    # The kernel store still sees a clean, resumable idle…
    assert out["finalize"][0]["status"] == "idle"
    # …and nothing raised, so the reason can only travel as the category.
    assert out["finalize"][0]["error"] is None
    assert out["finalize"][0]["interrupt_category"] == "user_interrupt"


def test_host_teardown_keeps_its_own_category(monkeypatch: pytest.MonkeyPatch) -> None:
    """``interrupted`` is NOT the user's doing and must not be relabelled as it."""
    out = _drive(monkeypatch, {"type": "error", "category": "interrupted", "message": "torn down"})

    assert out["returned"] == "interrupted"
    assert out["finalize"][0]["interrupt_category"] == "interrupted"


def test_a_real_failure_is_not_an_interruption(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _drive(monkeypatch, {"type": "error", "category": "execution_error", "message": "boom"})

    assert out["returned"] == "terminated"
    assert out["finalize"][0]["interrupt_category"] is None


def test_a_clean_turn_carries_no_category(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _drive(monkeypatch, {"type": "end_turn"})

    assert out["returned"] == "idle"
    assert out["finalize"][0]["interrupt_category"] is None


# --------------------------------------------------------------------------
# 2. ``_finalize_session`` turns that category into the durable marker
# --------------------------------------------------------------------------


def _finalize(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> tuple[list[Any], list[Any]]:
    """Drive ``_finalize_session`` and capture the request it sends the kernel."""
    monkeypatch.setattr(run_orch, "_resolve_session_owner", _as_async(lambda _s: LOCAL_USER_ID))
    monkeypatch.setattr(
        run_orch.kernel_client,
        "get_session",
        _as_async(lambda *a, **k: SimpleNamespace(metadata={"valuz": {"name": "n"}})),
    )
    requests: list[Any] = []
    monkeypatch.setattr(
        run_orch.kernel_client,
        "finalize_session",
        _as_async(lambda _u, _s, req: requests.append(req)),
    )
    projected: list[Any] = []
    monkeypatch.setattr(
        run_orch,
        "_project_conversation_run_result",
        _as_async(lambda _sess, _u, _sid, ev: projected.append(ev)),
    )

    asyncio.run(run_orch._finalize_session("sess-1", "hi", "idle", **kwargs))
    return requests, projected


def test_user_stop_becomes_a_durable_session_error(monkeypatch: pytest.MonkeyPatch) -> None:
    requests, _ = _finalize(monkeypatch, interrupt_category="user_interrupt")

    assert len(requests) == 1
    event = requests[0].error_event
    assert event is not None
    assert event.type == "session_error"
    # The category is what the client keys the quiet grey line on, and what
    # separates "you stopped this" from "the runtime went away".
    assert event.data["category"] == "user_interrupt"
    # An interruption is NOT a failure: the session stays a clean, resumable
    # idle rather than being stamped with an error stop_reason.
    assert requests[0].stop_reason_type is None
    assert requests[0].stop_reason_message is None
    assert requests[0].status == "idle"


def test_the_marker_does_not_raise_a_failure_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The projector already skips ``_INTERRUPT_CATEGORIES`` — assert the event
    we hand it is one of them, so a user stop never lights the failure badge."""
    _, projected = _finalize(monkeypatch, interrupt_category="user_interrupt")

    assert len(projected) == 1
    assert projected[0].data["category"] in turn_driver._INTERRUPT_CATEGORIES


def test_a_clean_turn_still_records_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    requests, projected = _finalize(monkeypatch)

    assert requests[0].error_event is None
    assert projected == [None]

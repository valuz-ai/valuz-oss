"""``_finalize_session`` persists a turn failure durably.

Regression for the "no friendly session event on a failed turn" bug: a failed
agent turn only emitted ``session_error`` via ``emit_live_event`` (live-only,
missed by any client not connected at failure time), so on reload the UI showed
a bare "Run failed" with no reason. The fix threads the captured exception into
``_finalize_session``, which
appends a ``session_error`` event as part of the same kernel ``finalize`` call —
so the reason survives reload and the terminal state is marked an error.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from valuz_agent.modules.sessions import run_orchestrator as run_orch


def _as_async(fn: Any) -> Any:
    async def _f(*args: Any, **kwargs: Any) -> Any:
        return fn(*args, **kwargs)

    return _f


def _patch_common(monkeypatch: Any, captured: dict[str, Any]) -> None:
    fake_sess = SimpleNamespace(metadata={"valuz": {"name": "n"}})
    monkeypatch.setattr(run_orch, "_resolve_session_owner", _as_async(lambda _sid: "owner-1"))
    monkeypatch.setattr(
        run_orch.kernel_client, "get_session", _as_async(lambda _uid, _sid: fake_sess)
    )

    def _capture(_uid: str, _sid: str, req: Any) -> None:
        captured["req"] = req

    monkeypatch.setattr(run_orch.kernel_client, "finalize_session", _as_async(_capture))


def test_finalize_with_error_appends_session_error_event(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, captured)

    asyncio.run(
        run_orch._finalize_session(
            "sess-1", "hi", "terminated", error=RuntimeError("boom: skill x")
        )
    )

    req = captured["req"]
    assert req.status == "terminated"
    assert req.error_event is not None
    assert req.error_event.type == "session_error"
    assert req.error_event.data["category"] == "RuntimeError"
    assert req.error_event.data["message"] == "boom: skill x"
    assert req.stop_reason_type == "error"
    assert req.stop_reason_message == "boom: skill x"


def test_finalize_blank_error_falls_back_to_default_message(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, captured)

    asyncio.run(run_orch._finalize_session("sess-1", "hi", "terminated", error=RuntimeError("")))

    req = captured["req"]
    assert req.error_event.data["message"] == "agent turn failed"
    assert req.stop_reason_message == "agent turn failed"


def test_finalize_success_has_no_error_event(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, captured)

    asyncio.run(run_orch._finalize_session("sess-1", "hi", "idle"))

    req = captured["req"]
    assert req.status == "idle"
    assert req.error_event is None
    assert req.stop_reason_type is None
    assert req.stop_reason_message is None


def test_finalize_with_cancelled_error_records_an_interruption_marker(monkeypatch: Any) -> None:
    """A cancellation is threaded through as ``error`` so the turn still gets a
    durable terminal bracket on reload — but as the ``interrupted`` category the
    client renders quietly, and WITHOUT stamping the session's stop_reason as an
    error (the session stays a clean, resumable idle)."""
    captured: dict[str, Any] = {}
    _patch_common(monkeypatch, captured)

    asyncio.run(run_orch._finalize_session("sess-1", "hi", "idle", error=asyncio.CancelledError()))

    req = captured["req"]
    assert req.status == "idle"
    assert req.error_event is not None
    assert req.error_event.type == "session_error"
    assert req.error_event.data == {"category": "interrupted", "message": "turn interrupted"}
    assert req.stop_reason_type is None
    assert req.stop_reason_message is None

"""``run_session_to_idle``: the loop-local ``interrupted`` status must never
reach ``_finalize_session``.

``_resolve_turn_status`` returns ``"interrupted"`` when a turn ends idle with
a cancellation stop_reason (``user_interrupt`` / ``interrupted``). The actor
loop path (``_finalize_actor``) already maps it back to ``"idle"`` before
finalizing; ``run_session_to_idle``'s own finalize call must do the same —
otherwise ``FinalizeSessionRequest`` (``running|idle|terminated``) raises a
ValidationError and the session is never finalized.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.infra.local_identity import resolve_local_user_id
from valuz_agent.modules.sessions import turn_driver
from valuz_agent.modules.tasks import actor_runner

LOCAL_USER_ID = resolve_local_user_id()


def _as_async(fn: Any) -> Any:
    async def _inner(*a: Any, **k: Any) -> Any:
        return fn(*a, **k)

    return _inner


class _Bus:
    def publish(self, *a: Any, **k: Any) -> None:
        pass


def _run_to_idle_with_stop_reason(
    monkeypatch: pytest.MonkeyPatch, stop_reason: dict[str, Any] | None
) -> tuple[str, list[Any]]:
    """Drive one run_session_to_idle turn whose AUTHORITATIVE run_turn
    ``message`` carries ``stop_reason``; return (returned final_status, finalize
    call args). The turn outcome is classified off the message, not a re-read of
    the durable session (which is now only a secondary meter/error signal)."""
    # The turn's capability hook is irrelevant here — inert it (the fake
    # ``run_turn`` below never invokes it anyway).
    monkeypatch.setattr(turn_driver, "always_on_mcp_hook", lambda *a, **k: _as_async(lambda: None))

    after_run = SimpleNamespace(status="idle", stop_reason=stop_reason, metadata={})

    class _Reader:
        async def get_session(self, *a: Any, **k: Any) -> Any:
            return after_run

    monkeypatch.setattr(turn_driver, "data_reader", lambda: _Reader())
    monkeypatch.setattr(
        actor_runner.kernel_client,
        "run_turn",
        _as_async(lambda *a, **k: SimpleNamespace(id="m1", status="ok", stop_reason=stop_reason)),
    )

    finalize_calls: list[Any] = []
    import valuz_agent.modules.sessions.run_orchestrator as run_orch

    async def _fake_finalize(session_id: str, content: str, status: str, **k: Any) -> None:
        finalize_calls.append(status)

    monkeypatch.setattr(run_orch, "_finalize_session", _fake_finalize)

    returned = asyncio.run(
        turn_driver.run_session_to_idle("sess-1", "hi", _Bus(), user_id=LOCAL_USER_ID)
    )
    return returned, finalize_calls


def test_interrupted_turn_finalizes_as_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    returned, finalize_calls = _run_to_idle_with_stop_reason(
        monkeypatch, {"type": "error", "category": "user_interrupt", "message": "stopped"}
    )
    # Loop-local semantics preserved for the caller…
    assert returned == "interrupted"
    # …but the kernel store only ever sees a persistable status.
    assert finalize_calls == ["idle"]


def test_errored_turn_still_finalizes_terminated(monkeypatch: pytest.MonkeyPatch) -> None:
    returned, finalize_calls = _run_to_idle_with_stop_reason(
        monkeypatch, {"type": "error", "category": "APIError", "message": "boom"}
    )
    assert returned == "terminated"
    assert finalize_calls == ["terminated"]


def test_clean_turn_finalizes_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    returned, finalize_calls = _run_to_idle_with_stop_reason(monkeypatch, None)
    assert returned == "idle"
    assert finalize_calls == ["idle"]

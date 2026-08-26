"""Runtime-initiated mode transitions persist immediately, not at turn end.

Field bug: after approving a plan (Claude ``ExitPlanMode``), the runtime
flips the in-memory ``session.mode`` and emits ``mode_changed{by:"runtime"}``,
but the store row kept ``mode="plan"`` until the end-of-turn save. A client
that (re)opened the session mid-turn hydrated the stale row and showed the
plan chip again — the live frame only reaches clients attached at emit time
and replays are deliberately inert. The observer now writes the transition
through to the store the moment the event flows past.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401 — kernel sys.path side-effect

from src.core.events import Event
from src.core.orchestrator import _make_mode_persist, _MessageObserverSink


class _CollectSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


def _mode_event(by: str, mode: str = "default") -> Event:
    return Event(type="mode_changed", data={"mode": mode, "by": by})


async def test_runtime_mode_change_invokes_write_through() -> None:
    persisted: list[str] = []

    async def _persist(mode: str) -> None:
        persisted.append(mode)

    sink = _CollectSink()
    obs = _MessageObserverSink(sink, mode_persist=_persist)
    await obs.emit(_mode_event(by="runtime"))
    assert persisted == ["default"]
    assert obs.runtime_mode_change == "default"
    # The event still reaches the inner sink (SSE / persistence path).
    assert [e.type for e in sink.events] == ["mode_changed"]


async def test_user_mode_change_does_not_write_through() -> None:
    persisted: list[str] = []

    async def _persist(mode: str) -> None:  # pragma: no cover — must not run
        persisted.append(mode)

    obs = _MessageObserverSink(_CollectSink(), mode_persist=_persist)
    await obs.emit(_mode_event(by="user"))
    assert persisted == []
    assert obs.runtime_mode_change is None


async def test_write_through_failure_never_breaks_the_turn() -> None:
    async def _persist(mode: str) -> None:
        raise RuntimeError("store down")

    sink = _CollectSink()
    obs = _MessageObserverSink(sink, mode_persist=_persist)
    await obs.emit(_mode_event(by="runtime"))  # must not raise
    assert obs.runtime_mode_change == "default"
    assert [e.type for e in sink.events] == ["mode_changed"]


async def test_make_mode_persist_writes_only_on_a_real_transition() -> None:
    saved: list[Any] = []
    row = SimpleNamespace(mode="plan")

    class _Store:
        async def load_session(self, user_id: str, session_id: str) -> Any:
            return row

        async def save_session(self, session: Any) -> None:
            saved.append(session.mode)

    persist = _make_mode_persist(_Store(), "u1", "s1")
    await persist("default")
    assert saved == ["default"] and row.mode == "default"
    # Idempotent: same mode again → no second save.
    await persist("default")
    assert saved == ["default"]

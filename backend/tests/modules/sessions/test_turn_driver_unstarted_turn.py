"""A turn that dies BEFORE the kernel accepts it still has to record the user's
message.

Regression for the "error card floats above my own message, and the page spins
on 正在启动本地运行环境 forever" report. A cloud turn whose execution capability
was rejected (control plane 403) failed inside ``kernel_client.run_turn``'s
pre-flight — allocation / overlay runtime context — so the kernel was never
entered and never wrote the ``user_message`` event that opens a turn. The
session row ended up ``terminated`` with a stop reason and the event stream was
EMPTY.

The client rebuilds the transcript purely from events, so that combination is
unassembleable: with no user message the failure has nothing to attach to (it
renders above the user's own bubble) and the optimistic bubble is never retired
(the startup header counts up forever). The driver now writes the missing
``user_message`` itself, before the failure is recorded.

It writes it through ``record_unstarted_turn``, which mints a fresh Message.
The first cut used ``append_event``, whose contract is "anchor onto the
session's most recent message" — for a turn that never started that is the
PREVIOUS turn's message, so both turns came back carrying one ``message_id``.
Clients key a turn by ``message_id``, so the second turn's bubble and its error
card rendered INSIDE the first turn, splitting the first turn's own answer
around them. See ``test_kernel_unstarted_turn_route.py`` for the anchor itself.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.adapters.kernel_client import TurnNotStartedError
from valuz_agent.infra.local_identity import resolve_local_user_id
from valuz_agent.modules.sessions import run_orchestrator as run_orch
from valuz_agent.modules.sessions import turn_driver

LOCAL_USER_ID = resolve_local_user_id()


def _as_async(fn: Any) -> Any:
    async def _inner(*a: Any, **k: Any) -> Any:
        return fn(*a, **k)

    return _inner


class _Bus:
    def publish(self, event_type: str, **payload: Any) -> None:
        return None


def _drive(monkeypatch: pytest.MonkeyPatch, raise_exc: BaseException) -> list[Any]:
    """Run one turn whose ``run_turn`` raises; return the appended events."""
    monkeypatch.setattr(turn_driver, "always_on_mcp_hook", lambda *a, **k: _as_async(lambda: None))

    class _Reader:
        async def get_session(self, *a: Any, **k: Any) -> Any:
            return SimpleNamespace(status="idle", stop_reason=None, metadata={})

    monkeypatch.setattr(turn_driver, "data_reader", lambda: _Reader())

    async def _raising_run_turn(*a: Any, **k: Any) -> Any:
        raise raise_exc

    monkeypatch.setattr(turn_driver.kernel_client, "run_turn", _raising_run_turn)

    recorded: list[Any] = []
    monkeypatch.setattr(
        turn_driver.kernel_client,
        "record_unstarted_turn",
        _as_async(lambda _u, _s, req: recorded.append(req)),
    )
    monkeypatch.setattr(
        turn_driver.kernel_client,
        "append_event",
        _as_async(lambda *a, **k: pytest.fail("the unstarted turn must not reuse append_event")),
    )
    monkeypatch.setattr(
        turn_driver.kernel_client, "emit_live_event", _as_async(lambda *a, **k: None)
    )
    monkeypatch.setattr(run_orch, "_finalize_session", _as_async(lambda *a, **k: None))

    asyncio.run(turn_driver.run_session_to_idle("sess-1", "你好", _Bus(), user_id=LOCAL_USER_ID))
    return recorded


def test_records_the_user_message_when_the_turn_never_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = _drive(monkeypatch, TurnNotStartedError("502: execution capability returned 403"))

    assert len(recorded) == 1
    # The text has to be the user's, verbatim — it is what anchors the failure
    # in the transcript and what the client matches its optimistic bubble against.
    assert recorded[0].message == "你好"


def test_does_not_double_write_when_the_kernel_owned_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A failure raised from INSIDE the kernel means the turn was accepted, so
    # the kernel already opened the event bracket. Writing a second user message
    # here would duplicate the user's bubble.
    assert _drive(monkeypatch, RuntimeError("model call blew up")) == []

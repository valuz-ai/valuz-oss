"""``src.core.session_fork`` mechanics — paging, cutting, re-minting.

The fork route composes these; the route tests cover composition, these
cover the paging boundaries the route fakes never reach.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import pytest
import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from src.core import Event, Message, Session, UserMessage
from src.core.agent_config import AgentConfig
from src.core.types import EndTurn
from src.core.session_fork import build_forked_session, collect_history, copy_history


def _message(idx: int, *, status: str = "completed") -> Message:
    return Message(
        id=f"m{idx}",
        session_id="src",
        user_message=UserMessage(text=f"p{idx}"),
        started_at=idx,
        status=status,
    )


class _Store:
    def __init__(self, messages: list[Message], events: dict[str, list[Event]] | None = None):
        self._messages = messages
        self._events = events or {}
        self.saved: list[Message] = []
        self.appended: list[tuple[str, str, Event]] = []

    async def list_messages_for_session(
        self, owner: str, session_id: str, *, limit: int, offset: int
    ) -> list[Message]:
        rows = sorted(self._messages, key=lambda m: m.started_at, reverse=True)
        return rows[offset : offset + limit]

    async def save_message(self, owner: str, message: Message) -> None:
        self.saved.append(message)

    async def get_events_for_message(
        self, owner: str, message_id: str, *, limit: int, offset: int
    ) -> list[Event]:
        return self._events.get(message_id, [])[offset : offset + limit]

    async def append_event(
        self, owner: str, session_id: str, message_id: str, event: Event, *, request_id=None
    ) -> int:
        self.appended.append((session_id, message_id, event))
        return len(self.appended)


async def test_collect_history_pages_past_one_page() -> None:
    # 450 rows > two 200-row pages; newest-first store pages must come back
    # oldest-first and complete.
    store = _Store([_message(i) for i in range(450)])
    rows = await collect_history(store, "o", "src")
    assert len(rows) == 450
    assert [r.id for r in rows[:3]] == ["m0", "m1", "m2"]


async def test_collect_history_cuts_inclusively_at_anchor() -> None:
    store = _Store([_message(i) for i in range(5)])
    rows = await collect_history(store, "o", "src", until_message_id="m2")
    assert [r.id for r in rows] == ["m0", "m1", "m2"]


async def test_collect_history_anchor_cut_keeps_running_rows_in_range() -> None:
    # With an anchor the cut is positional — an interrupted turn BEFORE the
    # anchor is part of the copied history. Only the tail copy drops
    # in-flight rows.
    store = _Store([_message(0), _message(1, status="running"), _message(2)])
    with_anchor = await collect_history(store, "o", "src", until_message_id="m2")
    assert [r.id for r in with_anchor] == ["m0", "m1", "m2"]
    tail = await collect_history(store, "o", "src")
    assert [r.id for r in tail] == ["m0", "m2"]


async def test_collect_history_unknown_anchor_raises() -> None:
    store = _Store([_message(0)])
    with pytest.raises(LookupError):
        await collect_history(store, "o", "src", until_message_id="nope")


async def test_copy_history_pages_events_and_rehomes_ids() -> None:
    # 750 events > one 500-row page; every copied event must land on the
    # new message id — column and stamped data alike.
    events = [
        Event(type="text", data={"i": i, "message_id": "m0"}, timestamp=i) for i in range(750)
    ]
    store = _Store([], events={"m0": events})
    copied = await copy_history(store, "o", [_message(0)], "target")

    assert len(copied) == 1
    new_id = copied[0].id
    assert new_id != "m0" and copied[0].session_id == "target"
    assert len(store.appended) == 750
    assert all(sid == "target" and mid == new_id for sid, mid, _e in store.appended)
    assert all(e.data["message_id"] == new_id for _s, _m, e in store.appended)
    # Order preserved.
    assert [e.data["i"] for _s, _m, e in store.appended] == list(range(750))


def _source_session() -> Session:
    return Session(
        id="src",
        agent_config=AgentConfig(id="a", name="a"),
        cwd="/tmp/ws",
        user_id="o",
        runtime_provider="codex",
        status="idle",
        runtime_session_id="th-src",
    )


def test_fork_with_history_is_born_idle_with_anchor_stop_reason() -> None:
    anchor = _message(2)
    anchor.stop_reason = EndTurn()
    forked = build_forked_session(
        _source_session(),
        anchor_message_id=anchor.id,
        copied_messages=[_message(1), anchor],
    )
    # A settled conversation, not a never-ran placeholder — "created"
    # would hide it from the runs-driven session lists until first Send.
    assert forked.status == "idle"
    assert isinstance(forked.stop_reason, EndTurn)
    assert forked.runtime_session_id is None


def test_plain_config_copy_stays_created() -> None:
    forked = build_forked_session(
        _source_session(),
        anchor_message_id=None,
        copied_messages=[],
    )
    assert forked.status == "created"
    assert forked.stop_reason is None

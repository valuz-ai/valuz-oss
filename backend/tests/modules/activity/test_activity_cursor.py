"""Unit coverage for the activity feed's keyset cursor + tab routing — the
tricky pure logic behind ``modules/activity/service`` — plus the chat-row kernel
enrichment (the end-to-end merge is exercised via the ``/v1/activity`` route in
the browser)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from valuz_agent.modules.activity import service as svc


def _c(kind: str, cid: str, sort_at: int) -> svc._Cand:
    return svc._Cand(kind=kind, id=cid, sort_at=sort_at, project_id="p", is_auto=False)


def test_cursor_round_trip() -> None:
    c = _c("chat", "sess-1", 1782880550280)
    assert svc._decode(svc._encode(c)) == (1782880550280, "chat", "sess-1")


def test_decode_bad_cursor_is_none() -> None:
    assert svc._decode("garbage") is None
    assert svc._decode("") is None


def test_order_key_newest_first_stable_tiebreak() -> None:
    a = _c("chat", "a", 200)
    b = _c("task", "b", 100)
    same_ts_1 = _c("chat", "x", 150)
    same_ts_2 = _c("task", "y", 150)
    ordered = sorted([b, same_ts_2, a, same_ts_1], key=svc._order_key)
    # Newest sort_at first; for the ts=150 tie, "chat" sorts before "task".
    assert [c.id for c in ordered] == ["a", "x", "y", "b"]


def test_drop_already_seen_advances_past_cursor() -> None:
    # A page returned down to (150, chat, x); the next page must exclude every
    # item at-or-before that key and keep only strictly-older ones.
    cands = [
        _c("chat", "a", 200),
        _c("chat", "x", 150),
        _c("task", "y", 150),
        _c("task", "b", 100),
    ]
    cands.sort(key=svc._order_key)
    cur = (150, "chat", "x")
    cur_key = (-cur[0], cur[1], cur[2])
    kept = [c.id for c in cands if svc._order_key(c) > cur_key]
    # a(200) and x(150,chat) are at-or-before the cursor → dropped; y & b remain.
    assert kept == ["y", "b"]


def test_tab_source_and_automation_routing() -> None:
    assert svc._want_sessions("chat") and not svc._want_tasks("chat")
    assert svc._want_tasks("task") and not svc._want_sessions("task")
    assert svc._want_sessions("automation") and svc._want_tasks("automation")
    assert svc._want_sessions("all") and svc._want_tasks("all")
    assert svc._want_playbooks("playbook")
    assert svc._want_playbooks("all")
    assert not svc._want_playbooks("automation")

    assert svc._automation_filter("automation") is True
    assert svc._automation_filter("chat") is False
    assert svc._automation_filter("task") is False
    assert svc._automation_filter("all") is None


async def test_chat_rows_carry_kernel_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    # The row's Fork entry is gated client-side on the runtime
    # (deepseek_harness cannot fork), so enrichment passes the kernel's
    # ``runtime_provider`` through.
    rows = [
        SimpleNamespace(session_id="s-dsh", updated_at=2, project_id="p", origin="user"),
        SimpleNamespace(session_id="s-codex", updated_at=1, project_id="p", origin="user"),
    ]
    sessions = [
        SimpleNamespace(
            id="s-dsh", metadata={}, status="idle", runtime_provider="deepseek_harness"
        ),
        SimpleNamespace(id="s-codex", metadata={}, status="idle", runtime_provider="codex"),
    ]

    async def _noop(*_a: object, **_k: object) -> None:
        return None

    async def _rows(*_a: object, **_k: object) -> list[SimpleNamespace]:
        return rows

    async def _sessions(*_a: object, **_k: object) -> list[SimpleNamespace]:
        return sessions

    async def _names(*_a: object, **_k: object) -> dict[str, str]:
        return {}

    monkeypatch.setattr(svc.project_index, "ensure_legacy_session_index", _noop)
    monkeypatch.setattr(svc.project_index, "list_chat_index_rows", _rows)
    monkeypatch.setattr(svc.kernel_client, "list_sessions", _sessions)
    monkeypatch.setattr(svc, "project_name_map", _names)

    page = await svc.list_activity("owner", tab="chat")

    assert {i.id: i.runtime for i in page.items} == {
        "s-dsh": "deepseek_harness",
        "s-codex": "codex",
    }

"""Unit coverage for the activity feed's keyset cursor + tab routing — the
tricky pure logic behind ``modules/activity/service`` (the end-to-end merge is
exercised via the ``/v1/activity`` route in the browser)."""

from __future__ import annotations

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

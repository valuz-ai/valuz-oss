"""REST history envelopes carry ``event_uid`` — the SSE/REST identity parity.

The frontend dedups/merges REST history rows against SSE live/backfill frames
by ``event_uid`` (seqs are per-store and never comparable). The SSE adapter
stamps the uid on its frames; these tests pin that the service's envelope
projection (``list_events`` / ``list_events_window``) does NOT drop it on the
REST path — the regression this file was added for.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.modules.sessions.service as svc_mod
from valuz_agent.adapters.event_sse_adapter import SessionEventFrame


def _frame(seq: int, uid: str | None) -> SessionEventFrame:
    return SessionEventFrame(
        seq=seq,
        event_type="message.user",
        payload={"text": "hi"},
        timestamp=1000 + seq,
        event_uid=uid,
    )


@pytest.fixture
def service(monkeypatch):
    class _Reader:
        async def get_session(self, user_id, session_id):  # noqa: ANN001
            return SimpleNamespace(id=session_id, user_id=user_id)

    monkeypatch.setattr(svc_mod, "data_reader", lambda: _Reader())
    # The history-read paths touch none of the constructor collaborators.
    return svc_mod.SessionService(
        event_bus=SimpleNamespace(publish=lambda *a, **k: None),
        project_svc=SimpleNamespace(),
        providers=SimpleNamespace(),
        skills=SimpleNamespace(),
        projects=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_list_events_envelope_carries_event_uid(service, monkeypatch):
    frames = [_frame(11, "uid-a"), _frame(12, None)]  # None = pre-uid legacy row

    async def _list_events_after(session_id, *, user_id, after_seq, limit):  # noqa: ANN001
        return frames

    monkeypatch.setattr(
        "valuz_agent.adapters.event_sse_adapter.list_events_after", _list_events_after
    )
    items = await service.list_events("sid", user_id="u")
    assert [(e.seq, e.event_uid) for e in items] == [(11, "uid-a"), (12, None)]


@pytest.mark.asyncio
async def test_list_events_window_envelope_carries_event_uid(service, monkeypatch):
    window = SimpleNamespace(items=[_frame(21, "uid-w")], has_more=False)

    async def _list_events_window(session_id, *, user_id, before_seq, turn_limit):  # noqa: ANN001
        return window

    monkeypatch.setattr(
        "valuz_agent.adapters.event_sse_adapter.list_events_window", _list_events_window
    )
    items, has_more = await service.list_events_window("sid", user_id="u")
    assert has_more is False
    assert [(e.seq, e.event_uid) for e in items] == [(21, "uid-w")]

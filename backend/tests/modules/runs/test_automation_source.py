"""Automation-backed sessions are reclassified to the ``automation`` source.

A kernel session that backs an automation run must surface in the activity
overview as ``source_kind="automation"`` with its ``automation_id`` set — not as
a plain chat/task run — so menu/Activity can pin it in the cross-type Running
group and route clicks to ``/automations/:id``. When no index is wired (tests /
older callers) the classification is a no-op.
"""

# ruff: noqa: I001
from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel on the import path)
from valuz_agent.modules.runs import service as svc_mod
from valuz_agent.modules.runs.service import RunSummary, RunsService


class _FakeStore:
    async def list_projects(self, *_a, **_k):
        return []

    async def list_all(self, *_a, **_k):
        return []


def _async_return(value):
    async def _inner(*_a, **_k):
        return value

    return _inner


def _wire(monkeypatch, sessions):
    monkeypatch.setattr(svc_mod, "require_current_user_id", lambda: "u1")
    monkeypatch.setattr(
        svc_mod.project_index,
        "list_recent",
        _async_return(
            [SimpleNamespace(session_id=s.id, project_id="") for s in sessions]
        ),
    )
    monkeypatch.setattr(svc_mod.kernel_client, "list_sessions", _async_return(sessions))


def _summary(session_id: str, source: str) -> RunSummary:
    return RunSummary(
        session_id=session_id,
        source_kind=source,  # type: ignore[arg-type]
        project_id="",
        title="t",
        status="running",
        updated_at=1,
    )


@pytest.mark.asyncio
async def test_automation_session_is_relabelled(monkeypatch):
    sessions = [
        SimpleNamespace(id="auto-sess", status="running", created_at=2),
        SimpleNamespace(id="chat-sess", status="running", created_at=1),
    ]
    _wire(monkeypatch, sessions)

    async def index(session_ids):
        assert set(session_ids) == {"auto-sess", "chat-sess"}
        return {"auto-sess": "automation-1"}

    service = RunsService(
        _FakeStore(), _FakeStore(), _FakeStore(), _FakeStore(), automation_index=index
    )

    # ``_build`` would naturally classify ``auto-sess`` as a plain chat run.
    async def _build(sess, *_a, **_k):
        return _summary(sess.id, "assistant")

    service._build = _build  # type: ignore[method-assign]

    out = {r.session_id: r for r in await service.list_runs(status="running")}

    assert out["auto-sess"].source_kind == "automation"
    assert out["auto-sess"].automation_id == "automation-1"
    # The non-automation session is untouched.
    assert out["chat-sess"].source_kind == "assistant"
    assert out["chat-sess"].automation_id is None


@pytest.mark.asyncio
async def test_no_index_is_a_noop(monkeypatch):
    sessions = [SimpleNamespace(id="s1", status="running", created_at=1)]
    _wire(monkeypatch, sessions)

    service = RunsService(_FakeStore(), _FakeStore(), _FakeStore(), _FakeStore())

    async def _build(sess, *_a, **_k):
        return _summary(sess.id, "assistant")

    service._build = _build  # type: ignore[method-assign]

    out = await service.list_runs(status="running")

    assert out[0].source_kind == "assistant"
    assert out[0].automation_id is None

"""Background-busy sessions surface in the running overview.

A session whose turn ended but whose ``run_in_background`` process is still
running must keep signalling in-flight work after the user navigates away —
the sidebar pulse and the Activity page both derive from
``GET /v1/runs?status=running``. The kernel orchestrator knows which warm
runtimes carry live background tasks; ``list_runs`` merges that set in and
marks such rows ``background=True`` (status surfaced as ``running``).
"""

# ruff: noqa: I001
from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel on the import path)
from valuz_agent.modules.runs import service as svc_mod
from valuz_agent.modules.runs.service import RunsService


class _FakeStore:
    async def list_projects(self, *_a, **_k):
        return []

    async def list_all(self, *_a, **_k):
        return []

    async def list_by_session_ids(self, *_a, **_k):
        return []

    async def list_by_ids(self, *_a, **_k):
        return []

    async def latest_events_by_task(self, *_a, **_k):
        return {}

    async def list_run_session_ids(self, *_a, **_k):
        return set()


def _async_return(value):
    async def _inner(*_a, **_k):
        return value

    return _inner


def _wire(monkeypatch, sessions, *, bg_busy):
    monkeypatch.setattr(
        svc_mod.project_index,
        "list_recent",
        _async_return(
            [
                SimpleNamespace(session_id=s.id, project_id="", updated_at=s.created_at)
                for s in sessions
            ]
        ),
    )
    monkeypatch.setattr(svc_mod.kernel_client, "list_sessions", _async_return(sessions))
    if isinstance(bg_busy, Exception):

        async def _raise(*_a, **_k):
            raise bg_busy

        monkeypatch.setattr(svc_mod.kernel_client, "bg_busy_session_ids", _raise)
    else:
        monkeypatch.setattr(
            svc_mod.kernel_client, "bg_busy_session_ids", _async_return(list(bg_busy))
        )

    store = _FakeStore()
    service = RunsService(store, store, store, store, store)

    async def _fake_build(user_id, sess, _ts, _ws, _tm, effective, *, background=False, **_k):
        return SimpleNamespace(
            session_id=sess.id,
            status=effective,
            background=background,
            updated_at=sess.created_at,
        )

    service._build = _fake_build  # type: ignore[method-assign]
    return service


@pytest.mark.asyncio
async def test_idle_session_with_live_bg_task_surfaces_as_running(monkeypatch):
    sessions = [
        SimpleNamespace(id="bg", status="idle", created_at=2),
        SimpleNamespace(id="quiet", status="idle", created_at=1),
    ]
    service = _wire(monkeypatch, sessions, bg_busy=["bg"])

    out = await service.list_runs("u1", status="running")

    assert [r.session_id for r in out] == ["bg"]
    assert out[0].status == "running"
    assert out[0].background is True


@pytest.mark.asyncio
async def test_streaming_session_keeps_its_background_flag(monkeypatch):
    # A turn actively streaming AND a bg task running — surfaced once, as a
    # normal running row that also carries the background marker.
    sessions = [SimpleNamespace(id="both", status="running", created_at=1)]
    service = _wire(monkeypatch, sessions, bg_busy=["both"])

    out = await service.list_runs("u1", status="running")

    assert [r.session_id for r in out] == ["both"]
    assert out[0].background is True


@pytest.mark.asyncio
async def test_bg_probe_failure_degrades_gracefully(monkeypatch):
    sessions = [
        SimpleNamespace(id="live", status="running", created_at=2),
        SimpleNamespace(id="idle", status="idle", created_at=1),
    ]
    service = _wire(monkeypatch, sessions, bg_busy=RuntimeError("kernel seam down"))

    out = await service.list_runs("u1", status="running")

    # The overview still works; only the bg augmentation is lost.
    assert [r.session_id for r in out] == ["live"]
    assert out[0].background is False

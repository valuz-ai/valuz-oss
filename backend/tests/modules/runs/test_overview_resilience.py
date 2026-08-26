"""A single malformed session must not blank the whole activity overview.

``list_runs`` enriches each kernel session into a ``RunSummary``. If one
session's enrichment raises (a bad todo, a missing relation, …) the overview
must skip that session and still return the rest — historically an unguarded
failure here made the entire Activity view disappear.
"""

# ruff: noqa: I001
from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_list_runs_skips_a_session_that_fails_to_build(monkeypatch):
    sessions = [
        SimpleNamespace(id="bad", status="running", created_at=1),
        SimpleNamespace(id="ok", status="running", created_at=2),
    ]

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

    store = _FakeStore()
    service = RunsService(store, store, store, store, store)

    async def _fake_build(user_id, sess, *_a, **_k):
        if sess.id == "bad":
            raise ValueError("boom — malformed session")
        return SimpleNamespace(session_id=sess.id, updated_at=sess.created_at)

    service._build = _fake_build  # type: ignore[method-assign]

    out = await service.list_runs("u1", status="running")

    assert [r.session_id for r in out] == ["ok"]


@pytest.mark.asyncio
async def test_list_runs_bounds_concurrent_session_enrichment(monkeypatch):
    """One overview request must leave DB connections for other API calls.

    A finished overview can contain hundreds of sessions.  Launching every
    per-session message lookup at once exhausted the shared SQLite QueuePool
    and made unrelated local endpoints look unreachable to the desktop.
    """

    sessions = [
        SimpleNamespace(id=f"session-{index}", status="running", created_at=index)
        for index in range(24)
    ]
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

    store = _FakeStore()
    service = RunsService(store, store, store, store, store)
    active = 0
    max_active = 0

    async def _fake_build(user_id, sess, *_a, **_k):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return SimpleNamespace(session_id=sess.id, updated_at=sess.created_at)

    service._build = _fake_build  # type: ignore[method-assign]

    out = await service.list_runs("u1", status="running")

    assert len(out) == len(sessions)
    assert max_active <= svc_mod._ENRICH_CONCURRENCY


def _async_return(value):
    async def _inner(*_a, **_k):
        return value

    return _inner


def test_task_events_are_resolved_before_the_concurrent_build() -> None:
    """The overview builds its rows with ``asyncio.gather``. Reading a task's
    latest event from inside that fan-out issued concurrent statements on the
    ONE request-scoped AsyncSession — unsupported by SQLAlchemy — and the
    per-row ``except Exception`` turned the resulting InvalidRequestError into
    "skipping session …", so runs silently vanished from the list.

    Pin the shape: the batch read happens once, before any building.
    """
    import inspect

    from valuz_agent.modules.runs.service import RunsService

    src = inspect.getsource(RunsService.list_runs)
    batch_at = src.index("latest_events_by_task")
    gather_at = src.index("asyncio.gather")
    assert batch_at < gather_at, (
        "the per-task event read must be resolved BEFORE the gather, not issued from inside it"
    )
    # And the single-task helper it replaced must not come back.
    assert not hasattr(RunsService, "_latest_task_event")

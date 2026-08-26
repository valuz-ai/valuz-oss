"""A project's sidebar accordion must not depend on the global recency window.

``list_runs`` without ``project_id`` fetches ONE global window of recent
sessions (``_INDEX_POOL``) and then returns at most ``_FINISHED_LIMIT`` of
them. Quick chats share that window, so an install with a few hundred of them
pushes every project conversation past its tail — projects then render with
nothing nested under them.

``project_id`` must therefore filter at the index query, not after it, and
``limit`` must bound the response.
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


def _service() -> RunsService:
    store = _FakeStore()
    service = RunsService(store, store, store, store, store)

    async def _fake_build(user_id, sess, *_a, **_k):
        return SimpleNamespace(
            session_id=sess.id,
            updated_at=sess.created_at,
            origin="user",
        )

    service._build = _fake_build  # type: ignore[method-assign]
    return service


def _index_rows(rows: list[tuple[str, str, int]]):
    return [SimpleNamespace(session_id=sid, project_id=pid, updated_at=ts) for sid, pid, ts in rows]


@pytest.mark.asyncio
async def test_project_id_filters_at_the_index_query(monkeypatch) -> None:
    """The scope must reach the SQL, not filter an already-truncated window."""
    seen: dict = {}

    async def _list_recent(*_a, **kwargs):
        seen.update(kwargs)
        return _index_rows([("s1", "p1", 10)])

    monkeypatch.setattr(svc_mod.project_index, "list_recent", _list_recent)
    monkeypatch.setattr(
        svc_mod.kernel_client,
        "list_sessions",
        _async_return([SimpleNamespace(id="s1", status="idle", created_at=10)]),
    )

    out = await _service().list_runs("u1", status="finished", project_id="p1")

    assert seen["project_id"] == "p1"
    assert [r.session_id for r in out] == ["s1"]


@pytest.mark.asyncio
async def test_unscoped_listing_still_asks_for_the_global_window(monkeypatch) -> None:
    seen: dict = {}

    async def _list_recent(*_a, **kwargs):
        seen.update(kwargs)
        return _index_rows([("s1", "p1", 10)])

    monkeypatch.setattr(svc_mod.project_index, "list_recent", _list_recent)
    monkeypatch.setattr(
        svc_mod.kernel_client,
        "list_sessions",
        _async_return([SimpleNamespace(id="s1", status="idle", created_at=10)]),
    )

    await _service().list_runs("u1", status="finished")

    assert seen["project_id"] is None


@pytest.mark.asyncio
async def test_limit_bounds_the_response(monkeypatch) -> None:
    sessions = [SimpleNamespace(id=f"s{i}", status="idle", created_at=i) for i in range(10)]
    monkeypatch.setattr(
        svc_mod.project_index,
        "list_recent",
        _async_return(_index_rows([(s.id, "p1", s.created_at) for s in sessions])),
    )
    monkeypatch.setattr(svc_mod.kernel_client, "list_sessions", _async_return(sessions))

    out = await _service().list_runs("u1", status="finished", project_id="p1", limit=3)

    # Newest first, capped at the requested budget.
    assert [r.session_id for r in out] == ["s9", "s8", "s7"]


def _async_return(value):
    async def _inner(*_a, **_k):
        return value

    return _inner

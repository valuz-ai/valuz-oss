from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from valuz_agent.modules.projects.service import ProjectService
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.project_lifecycle import NoopProjectLifecycleHook


@pytest.mark.parametrize("kind", [None, "chat"])
async def test_invalid_project_never_calls_hook(monkeypatch, kind):
    hook = SimpleNamespace(before_project_delete=AsyncMock())
    monkeypatch.setattr(ext, "project_lifecycle", hook)
    ds = SimpleNamespace(
        get_by_id=AsyncMock(return_value=SimpleNamespace(kind=kind) if kind else None)
    )
    with pytest.raises((KeyError, ValueError)):
        await ProjectService(ds, Mock()).delete_project("owner", "project")
    hook.before_project_delete.assert_not_awaited()
    ds.get_by_id.assert_awaited_once_with("owner", "project")


async def test_preparation_failure_prevents_all_destructive_work(monkeypatch):
    hook = SimpleNamespace(before_project_delete=AsyncMock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(ext, "project_lifecycle", hook)
    ds = SimpleNamespace(
        _db=object(),
        get_by_id=AsyncMock(return_value=SimpleNamespace(kind="project")),
        delete=AsyncMock(),
    )
    remove = AsyncMock()
    monkeypatch.setattr(
        "valuz_agent.modules.projects.service.project_index.remove_for_project", remove
    )
    docs = SimpleNamespace(remove_all_bindings=AsyncMock())
    with pytest.raises(RuntimeError, match="offline"):
        await ProjectService(ds, Mock(), document_datastore=docs).delete_project("owner", "project")
    hook.before_project_delete.assert_awaited_once_with(
        db=ds._db, user_id="owner", project_id="project"
    )
    remove.assert_not_awaited()
    docs.remove_all_bindings.assert_not_awaited()
    ds.delete.assert_not_awaited()


async def test_noop_preserves_oss_delete(monkeypatch):
    monkeypatch.setattr(ext, "project_lifecycle", NoopProjectLifecycleHook())
    ds = SimpleNamespace(
        _db=object(),
        get_by_id=AsyncMock(return_value=SimpleNamespace(kind="project")),
        delete=AsyncMock(),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.projects.service.project_index.remove_for_project",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr("valuz_agent.modules.tasks.purge.purge_project_tasks", AsyncMock())
    monkeypatch.setattr("valuz_agent.modules.memory.service.memory_store.drop_project", Mock())
    await ProjectService(ds, Mock()).delete_project("owner", "project")
    ds.delete.assert_awaited_once_with("owner", "project")

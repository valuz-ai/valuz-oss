from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from valuz_agent.facade.projects import ProjectLibrary


class _Service:
    def __init__(self) -> None:
        self.rows = {
            ("owner-a", "p1"): SimpleNamespace(id="p1", name="Primary", kind="project"),
            ("owner-a", "c1"): SimpleNamespace(id="c1", name="Chat", kind="chat"),
        }
        self.calls: list[tuple[object, ...]] = []

    async def list_projects(self, user_id: str):
        self.calls.append(("list", user_id))
        return [row for (owner, _), row in self.rows.items() if owner == user_id]

    async def get_project(self, user_id: str, project_id: str):
        self.calls.append(("get", user_id, project_id))
        try:
            return self.rows[(user_id, project_id)]
        except KeyError as exc:
            raise KeyError(project_id) from exc

    async def create_project(self, user_id: str, name: str, root_path: str | None):
        self.calls.append(("create", user_id, name, root_path))
        row = SimpleNamespace(id="p-new", name=name, kind="project")
        self.rows[(user_id, row.id)] = row
        return row

    async def create_chat_project_for_session(self, user_id: str, name: str):
        self.calls.append(("create_chat", user_id, name))
        row = SimpleNamespace(id="c-new", name=name, kind="chat")
        self.rows[(user_id, row.id)] = row
        return row

    async def delete_project(self, user_id: str, project_id: str) -> None:
        self.calls.append(("delete", user_id, project_id))
        try:
            del self.rows[(user_id, project_id)]
        except KeyError as exc:
            raise KeyError(project_id) from exc


@asynccontextmanager
async def _dependency(service: _Service) -> AsyncIterator[_Service]:
    yield service


async def test_library_keeps_project_reads_owner_scoped() -> None:
    service = _Service()

    async def dependency():
        async with _dependency(service) as value:
            yield value

    with patch("valuz_agent.api.deps.get_project_service", dependency):
        library = ProjectLibrary()
        assert [ref.id for ref in await library.list("owner-a")] == ["p1", "c1"]
        assert [ref.id for ref in await library.list("owner-a", kind="project")] == ["p1"]
        assert await library.get("owner-b", "p1") is None


async def test_library_routes_create_chat_and_delete_through_project_service() -> None:
    service = _Service()

    async def dependency():
        async with _dependency(service) as value:
            yield value

    with patch("valuz_agent.api.deps.get_project_service", dependency):
        library = ProjectLibrary()
        created = await library.create("owner-a", name="NVIDIA Research")
        chat = await library.create_chat("owner-a", name="Scheduled research")
        assert created.kind == "project"
        assert chat.kind == "chat"
        assert await library.delete("owner-a", created.id) is True
        assert await library.delete("owner-b", "p1") is False

    assert ("create", "owner-a", "NVIDIA Research", None) in service.calls
    assert ("create_chat", "owner-a", "Scheduled research") in service.calls

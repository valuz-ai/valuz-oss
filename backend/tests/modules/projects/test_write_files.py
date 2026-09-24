"""``ProjectService.write_files`` — the upload path, and what it reports.

An upload is a host write into a project directory, so the workspace-sync port
hears about it: once per batch, with every file that landed (absolute paths),
also when a later file in the batch was rejected.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.config import settings
from valuz_agent.infra.database import Base
from valuz_agent.infra.eventbus import event_bus
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.projects.service import ProjectService
from valuz_agent.ports.extensions import ext

USER = "user-1"


@pytest.fixture
def sessionmaker_(tmp_path):  # noqa: ANN001, ANN201
    db_file = tmp_path / "proj.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[ProjectRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _managed_root(tmp_path, monkeypatch):  # noqa: ANN001, ANN202
    monkeypatch.setattr(settings, "user_project_root", tmp_path / "Valuz")


@pytest.fixture
def port(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    bound = SimpleNamespace(after_write=AsyncMock(), before_read=AsyncMock())
    monkeypatch.setattr(ext, "workspace_sync", bound)
    return bound


def _service(db) -> ProjectService:  # noqa: ANN001
    return ProjectService(datastore=ProjectDatastore(db), event_bus=event_bus)


async def _project(sessionmaker_, tmp_path: Path) -> tuple[str, Path]:  # noqa: ANN001
    root = tmp_path / "repo"
    root.mkdir()
    async with sessionmaker_() as db:
        detail = await _service(db).create_project(USER, "Repo", root_path=str(root))
    return detail.id, root.resolve()


async def test_a_batch_is_written_and_reported_once(sessionmaker_, tmp_path, port) -> None:  # noqa: ANN001
    project_id, root = await _project(sessionmaker_, tmp_path)

    async def _parts():  # noqa: ANN202 — the route hands an async iterable
        yield "a.md", b"A"
        yield "sub/b.md", b"B"

    async with sessionmaker_() as db:
        written = await _service(db).write_files(USER, project_id, _parts())

    assert written == ["a.md", "sub/b.md"]
    assert (root / "sub" / "b.md").read_bytes() == b"B"
    port.after_write.assert_awaited_once_with(
        owner_user_id=USER,
        paths=(root / "a.md", root / "sub" / "b.md"),
        project_id=project_id,
    )
    port.before_read.assert_not_awaited()


async def test_a_rejected_file_still_reports_what_landed(sessionmaker_, tmp_path, port) -> None:  # noqa: ANN001
    project_id, root = await _project(sessionmaker_, tmp_path)

    async with sessionmaker_() as db:
        with pytest.raises(ValueError):
            await _service(db).write_files(
                USER, project_id, [("ok.md", b"1"), ("../escape.md", b"2")]
            )

    assert (root / "ok.md").exists()
    assert not (tmp_path / "escape.md").exists()
    port.after_write.assert_awaited_once_with(
        owner_user_id=USER, paths=(root / "ok.md",), project_id=project_id
    )


async def test_write_file_is_a_batch_of_one(sessionmaker_, tmp_path, port) -> None:  # noqa: ANN001
    project_id, root = await _project(sessionmaker_, tmp_path)

    async with sessionmaker_() as db:
        rel = await _service(db).write_file(USER, project_id, "notes/n.md", b"x")

    assert rel == "notes/n.md"
    port.after_write.assert_awaited_once_with(
        owner_user_id=USER, paths=(root / "notes" / "n.md",), project_id=project_id
    )


async def test_an_unknown_project_writes_and_reports_nothing(sessionmaker_, port) -> None:  # noqa: ANN001
    async with sessionmaker_() as db:
        with pytest.raises(KeyError):
            await _service(db).write_files(USER, "missing", [("a.md", b"A")])

    port.after_write.assert_not_awaited()


async def test_the_upload_route_hands_the_whole_batch_to_one_call(
    sessionmaker_, tmp_path, port
) -> None:  # noqa: ANN001, E501
    """The route streams parts into ONE ``write_files`` call, so a multi-file
    upload is one report, not one per file."""
    from valuz_agent.api.routes.projects import upload_project_files

    project_id, root = await _project(sessionmaker_, tmp_path)

    class _Upload:
        def __init__(self, filename: str, data: bytes) -> None:
            self.filename = filename
            self._data = data

        async def read(self) -> bytes:
            return self._data

    async with sessionmaker_() as db:
        out = await upload_project_files(
            project_id,
            files=[_Upload("x.md", b"1"), _Upload("y/z.md", b"2")],  # type: ignore[list-item]
            user_id=USER,
            svc=_service(db),
        )

    assert out == {"project_id": project_id, "written": ["x.md", "y/z.md"]}
    port.after_write.assert_awaited_once_with(
        owner_user_id=USER, paths=(root / "x.md", root / "y" / "z.md"), project_id=project_id
    )

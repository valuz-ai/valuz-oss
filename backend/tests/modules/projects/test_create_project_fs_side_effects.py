"""Binding a directory to a project is a read-only act.

``create_project`` / ``create_project_from_pack`` used to drop an empty
``.valuz/root`` file into the new project's cwd. Nothing ever read it — it was
the side effect of a write helper that happened to materialize the managed
workspace — and on the explicit-``root_path`` branch that cwd is a folder the
user picked in a directory dialog, often a git repo, where the marker surfaced
as untracked noise in ``git status``.

These pin both halves of the removal: an explicitly bound directory is left
exactly as the user had it, and the managed branch still yields a real cwd
(``allocate_managed_project_dir`` creates it — nothing downstream has to).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.config import settings
from valuz_agent.infra.database import Base
from valuz_agent.infra.eventbus import event_bus
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.projects.service import ProjectService

USER = "user-1"


@pytest.fixture
def sessionmaker_(tmp_path):  # noqa: ANN001, ANN201
    """Tmp-SQLite async sessionmaker with just the ``valuz_project`` table."""
    db_file = tmp_path / "proj.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[ProjectRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def _managed_root(tmp_path, monkeypatch):  # noqa: ANN001, ANN202
    """Keep the managed-workspace branch inside ``tmp_path``."""
    monkeypatch.setattr(settings, "user_project_root", tmp_path / "Valuz")


def _bound_dir(tmp_path: Path) -> Path:
    """A directory the user already has, with content worth not disturbing."""
    bound = tmp_path / "my-repo"
    bound.mkdir()
    (bound / "README.md").write_text("mine", encoding="utf-8")
    return bound


def _service(db) -> ProjectService:  # noqa: ANN001
    return ProjectService(datastore=ProjectDatastore(db), event_bus=event_bus)


async def test_create_project_writes_nothing_into_the_bound_directory(
    sessionmaker_,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    bound = _bound_dir(tmp_path)

    async with sessionmaker_() as db:
        detail = await _service(db).create_project(USER, "Repo", root_path=str(bound))

    assert detail.cwd == str(bound.resolve())
    assert sorted(p.name for p in bound.iterdir()) == ["README.md"]


async def test_pack_import_writes_nothing_into_the_bound_directory(
    sessionmaker_,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    bound = _bound_dir(tmp_path)

    async with sessionmaker_() as db:
        row = await _service(db).create_project_from_pack(
            USER,
            name="Repo",
            kind="project",
            icon=None,
            instructions_md=None,
            root_path=str(bound),
        )

    assert row.root_path == str(bound.resolve())
    assert sorted(p.name for p in bound.iterdir()) == ["README.md"]


async def test_managed_project_still_gets_a_real_empty_cwd(sessionmaker_) -> None:  # noqa: ANN001
    """No ``root_path`` = the cloud/managed path. The workspace must exist on
    disk by the time the row is returned — a session's cwd cannot be missing."""
    async with sessionmaker_() as db:
        detail = await _service(db).create_project(USER, "Managed", root_path=None)

    assert detail.cwd is not None
    cwd = Path(detail.cwd)
    assert cwd.is_dir()
    assert list(cwd.iterdir()) == []

"""Delivering a deliverable that arrives as content instead of a path.

A generated document (A2UI/OpenUI JSON) exists only as a tool result — there is
no file to point at. ``DeliveryRequest`` therefore has a second input form, and
the outcome has to be the same deliverable, the same version chain, and the
same on-disk snapshot the path form produces: the agent revises a generated
page by ``Read``-ing the version it was asked to change.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore
from valuz_agent.modules.artifacts.models import (
    STORAGE_KIND_FILE,
    STORAGE_KIND_INLINE,
)
from valuz_agent.modules.artifacts.scope import Scope
from valuz_agent.modules.artifacts.service import (
    DeliveryRequest,
    DeliveryStatus,
    deliver_artifact,
)

SCOPE = Scope(user_id="owner-1", project_id="proj-1", worktree="")
DOC = (
    '{"version":"v0.9.1","createSurface":{"surfaceId":"main","catalogId":"openui"}}\n'
    '{"version":"v0.9.1","updateComponents":{"surfaceId":"main","components":'
    '[{"id":"root","component":"Text","text":"hello"}]}}'
)


@pytest.fixture
async def session_factory():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    return workdir


async def _deliver(session_factory, cwd: Path, **kwargs):  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        result = await deliver_artifact(
            db,
            scope=SCOPE,
            scope_cwd=cwd,
            owner_roots=[cwd.resolve()],
            request=DeliveryRequest(**kwargs),
        )
        await db.commit()
        return result


async def test_should_record_a_deliverable_from_content(session_factory, cwd) -> None:
    result = await _deliver(
        session_factory, cwd, content=DOC, file_name="desk.a2ui.jsonl"
    )

    assert result.status is DeliveryStatus.RECORDED


async def test_should_write_a_snapshot_the_agent_can_read(session_factory, cwd) -> None:
    # The whole reason a content delivery still touches disk: a version the
    # agent cannot open is a version it cannot revise.
    await _deliver(session_factory, cwd, content=DOC, file_name="desk.a2ui.jsonl")

    snapshot = cwd / ".artifact"
    written = list(snapshot.rglob("desk.a2ui.jsonl"))
    assert len(written) == 1
    assert written[0].read_text(encoding="utf-8") == DOC


async def test_should_keep_the_document_on_the_content_row(session_factory, cwd) -> None:
    result = await _deliver(
        session_factory, cwd, content=DOC, file_name="desk.a2ui.jsonl"
    )

    async with session_factory() as db:
        revision = await ArtifactDatastore(db).get_revision(SCOPE.user_id, result.revision_id)
        content = await ArtifactDatastore(db).get_content(SCOPE.user_id, revision.content_id)
    assert content.storage_kind == STORAGE_KIND_INLINE
    assert content.content_inline == DOC


async def test_regenerating_appends_to_the_same_deliverable(session_factory, cwd) -> None:
    # "Generate the next version of this page" must land on the existing
    # lineage, not start a second one — that is what the version switcher and
    # the binding's parent chain are built on.
    first = await _deliver(
        session_factory, cwd, content=DOC, file_name="desk.a2ui.jsonl"
    )
    second = await _deliver(
        session_factory, cwd, content=DOC + "\n ", file_name="desk.a2ui.jsonl"
    )

    assert second.artifact_id == first.artifact_id
    assert second.version_no == 2


async def test_identical_content_is_not_a_new_version(session_factory, cwd) -> None:
    await _deliver(session_factory, cwd, content=DOC, file_name="desk.a2ui.jsonl")
    again = await _deliver(session_factory, cwd, content=DOC, file_name="desk.a2ui.jsonl")

    assert again.status is DeliveryStatus.UNCHANGED


async def test_content_without_a_file_name_is_rejected(session_factory, cwd) -> None:
    result = await _deliver(session_factory, cwd, content=DOC)

    assert result.status is DeliveryStatus.INVALID


async def test_both_forms_at_once_is_rejected(session_factory, cwd, tmp_path) -> None:
    src = cwd / "page.html"
    src.write_text("<p>hi</p>", encoding="utf-8")

    result = await _deliver(
        session_factory, cwd, abs_path=src, content=DOC, file_name="desk.a2ui.jsonl"
    )

    assert result.status is DeliveryStatus.INVALID


async def test_neither_form_is_rejected(session_factory, cwd) -> None:
    result = await _deliver(session_factory, cwd, display_name="nothing")

    assert result.status is DeliveryStatus.INVALID


async def test_path_form_still_stores_by_file(session_factory, cwd) -> None:
    # The content form must not have changed what a path delivery records.
    src = cwd / "report.md"
    src.write_text("# hi", encoding="utf-8")

    result = await _deliver(session_factory, cwd, abs_path=src)

    async with session_factory() as db:
        revision = await ArtifactDatastore(db).get_revision(SCOPE.user_id, result.revision_id)
        content = await ArtifactDatastore(db).get_content(SCOPE.user_id, revision.content_id)
    assert content.storage_kind == STORAGE_KIND_FILE
    assert content.content_inline is None

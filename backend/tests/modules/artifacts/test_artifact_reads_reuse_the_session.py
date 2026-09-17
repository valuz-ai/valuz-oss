"""These reads run on the caller's transaction when it offers one.

``async_unit_of_work`` is not re-entrant: it builds a fresh session, which
checks out another connection from the same pool. A helper that always opened
its own therefore cost a SECOND connection whenever it was called from inside a
transaction — the normal case, since a caller reads a binding in order to
decide what to write next.

With every caller holding two at once, N concurrent callers need 2N against a
fixed ceiling, and past half the ceiling they deadlock: each holds one and
waits out ``pool_timeout`` for a second nobody can release.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.artifacts.scope import Scope
from valuz_agent.modules.artifacts.service import (
    DeliveryRequest,
    bind_host_revision,
    count_scope_artifacts,
    deliver_artifact,
    list_artifact_host_bindings,
    load_bound_host_revision,
)

SCOPE = Scope(user_id="owner-1", project_id="proj-1", worktree="")
DOC = '{"version":"v0.9.1","updateComponents":{"components":[{"id":"root"}]}}'


@asynccontextmanager
async def _forbidden(commit: bool = True):  # type: ignore[no-untyped-def]
    raise AssertionError("opened a second unit of work while one was already open")
    yield  # pragma: no cover


@pytest.fixture
async def bound(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    async with sessions() as db:
        delivered = await deliver_artifact(
            db,
            scope=SCOPE,
            scope_cwd=cwd,
            owner_roots=[cwd.resolve()],
            request=DeliveryRequest(content=DOC, file_name="page.a2ui.jsonl"),
        )
        await bind_host_revision(
            db,
            SCOPE.user_id,
            host_type="workspace",
            host_id="host-1",
            slot="main",
            artifact_revision_id=delivered.revision_id,
        )
        await db.commit()
    yield sessions, delivered
    await engine.dispose()


async def test_should_read_a_binding_on_the_session_it_is_given(bound, monkeypatch) -> None:
    sessions, delivered = bound
    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", _forbidden)

    async with sessions() as db:
        revision = await load_bound_host_revision(
            SCOPE.user_id, host_type="workspace", host_id="host-1", slot="main", db=db
        )

    assert revision is not None
    assert revision.artifact_revision_id == delivered.revision_id
    assert revision.document_inline == DOC


async def test_should_list_bindings_on_the_session_it_is_given(bound, monkeypatch) -> None:
    sessions, delivered = bound
    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", _forbidden)

    async with sessions() as db:
        bindings = await list_artifact_host_bindings(
            SCOPE.user_id, delivered.artifact_id, db=db
        )

    assert [binding.host_id for binding in bindings] == ["host-1"]


async def test_should_count_a_scope_on_the_session_it_is_given(bound, monkeypatch) -> None:
    sessions, _ = bound
    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", _forbidden)

    async with sessions() as db:
        assert await count_scope_artifacts(
            SCOPE.user_id, SCOPE.project_id, SCOPE.worktree, db=db
        ) == 1


async def test_should_still_open_its_own_session_when_called_standalone(bound, monkeypatch) -> None:
    # The existing signature keeps working: a caller that passes no session
    # behaves exactly as before, opening one unit of work of its own.
    sessions, delivered = bound
    opened: list[bool] = []

    @asynccontextmanager
    async def counting(commit: bool = True):  # type: ignore[no-untyped-def]
        opened.append(True)
        async with sessions() as session:
            yield session
            if commit:
                await session.commit()

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", counting)

    revision = await load_bound_host_revision(
        SCOPE.user_id, host_type="workspace", host_id="host-1", slot="main"
    )

    assert opened == [True], "standalone must open exactly one unit of work"
    assert revision is not None
    assert revision.artifact_revision_id == delivered.revision_id

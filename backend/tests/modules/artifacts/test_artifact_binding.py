"""Binding a host surface to one exact revision.

The binding is what a page renders. It names a REVISION, never "the latest":
a regeneration must not silently replace what the user is looking at, so
adoption is a separate write — and a write that states what it believed was
bound, so two tabs adopting different versions cannot both win.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore
from valuz_agent.modules.artifacts.scope import Scope
from valuz_agent.modules.artifacts.service import (
    BindStatus,
    DeliveryRequest,
    bind_host_revision,
    deliver_artifact,
)

SCOPE = Scope(user_id="owner-1", project_id="proj-1", worktree="")
HOST = {"host_type": "finance.company-research", "host_id": "US:NVDA", "slot": "main"}
OTHER_OWNER = "owner-2"


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


async def _generate(session_factory, cwd: Path, body: str) -> str:
    """One generated version; returns its revision id."""
    async with session_factory() as db:
        result = await deliver_artifact(
            db,
            scope=SCOPE,
            scope_cwd=cwd,
            owner_roots=[cwd.resolve()],
            request=DeliveryRequest(content=body, file_name="page.a2ui.jsonl"),
        )
        await db.commit()
        return result.revision_id


async def _bind(session_factory, revision_id: str, **kwargs):  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        result = await bind_host_revision(
            db, SCOPE.user_id, artifact_revision_id=revision_id, **{**HOST, **kwargs}
        )
        await db.commit()
        return result


async def test_should_bind_a_revision_to_an_empty_slot(session_factory, cwd) -> None:
    revision_id = await _generate(session_factory, cwd, "v1")

    result = await _bind(session_factory, revision_id, expected_revision_id=None)

    assert result.status is BindStatus.BOUND


async def test_should_adopt_a_newer_revision_over_the_bound_one(
    session_factory, cwd
) -> None:
    first = await _generate(session_factory, cwd, "v1")
    second = await _generate(session_factory, cwd, "v2")
    await _bind(session_factory, first, expected_revision_id=None)

    result = await _bind(session_factory, second, expected_revision_id=first)

    assert result.status is BindStatus.BOUND
    assert result.artifact_revision_id == second


async def test_should_refuse_when_the_slot_moved_since_the_caller_read_it(
    session_factory, cwd
) -> None:
    first = await _generate(session_factory, cwd, "v1")
    second = await _generate(session_factory, cwd, "v2")
    third = await _generate(session_factory, cwd, "v3")
    await _bind(session_factory, second, expected_revision_id=None)

    # This caller still believes ``first`` is bound — somebody adopted v2 since.
    result = await _bind(session_factory, third, expected_revision_id=first)

    assert result.status is BindStatus.STALE


async def test_a_stale_refusal_reports_what_is_actually_bound(
    session_factory, cwd
) -> None:
    # So the UI can say "this page moved to v2" without a second round trip.
    first = await _generate(session_factory, cwd, "v1")
    second = await _generate(session_factory, cwd, "v2")
    await _bind(session_factory, second, expected_revision_id=None)

    result = await _bind(session_factory, first, expected_revision_id=None)

    assert result.current_revision_id == second


async def test_force_overrides_a_stale_expectation(session_factory, cwd) -> None:
    # The deliberate second click after the user has been shown the conflict.
    first = await _generate(session_factory, cwd, "v1")
    second = await _generate(session_factory, cwd, "v2")
    await _bind(session_factory, second, expected_revision_id=None)

    result = await _bind(
        session_factory, first, expected_revision_id=None, check_expected=False
    )

    assert result.status is BindStatus.BOUND


async def test_binding_an_unknown_revision_is_refused(session_factory, cwd) -> None:
    result = await _bind(session_factory, "nope", expected_revision_id=None)

    assert result.status is BindStatus.UNKNOWN_REVISION


async def test_should_not_bind_another_owners_revision(session_factory, cwd) -> None:
    # The revision lookup is owner-scoped, so a bare id carried over from
    # somebody else's workspace resolves to nothing rather than to their page.
    revision_id = await _generate(session_factory, cwd, "v1")

    async with session_factory() as db:
        result = await bind_host_revision(
            db, OTHER_OWNER, artifact_revision_id=revision_id, **HOST
        )

    assert result.status is BindStatus.UNKNOWN_REVISION


async def test_hosts_do_not_share_a_slot(session_factory, cwd) -> None:
    revision_id = await _generate(session_factory, cwd, "v1")
    await _bind(session_factory, revision_id, expected_revision_id=None)

    async with session_factory() as db:
        other = await ArtifactDatastore(db).get_binding(
            SCOPE.user_id, "finance.research-desk", "desk", "main"
        )

    assert other is None


async def test_unbinding_clears_the_slot_and_keeps_the_revision(
    session_factory, cwd
) -> None:
    revision_id = await _generate(session_factory, cwd, "v1")
    await _bind(session_factory, revision_id, expected_revision_id=None)

    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        assert await ds.delete_binding(SCOPE.user_id, **HOST) is True
        await db.commit()

    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        assert await ds.get_binding(SCOPE.user_id, **HOST) is None
        assert await ds.get_revision(SCOPE.user_id, revision_id) is not None


async def test_binding_response_reads_a_file_stored_document_back(
    session_factory, cwd
) -> None:
    """A revision does not have to arrive inline to get bound.

    An agent that recovers a page by writing the document to a file and
    delivering THAT path produces a file-stored revision; serving ``null``
    for its content blanked the whole workbench while the bytes sat intact
    on disk (observed live: a desk recovery bound v9 as ``file`` and the
    surface lost even its version bar).
    """
    from valuz_agent.api.routes.artifacts import _binding_response

    document = '{"version":"v0.9.1","createSurface":{"surfaceId":"main"}}\n' * 3
    source = cwd / "recovered.a2ui.jsonl"
    source.write_text(document, encoding="utf-8")

    async with session_factory() as db:
        result = await deliver_artifact(
            db,
            scope=SCOPE,
            scope_cwd=cwd,
            owner_roots=[cwd.resolve()],
            request=DeliveryRequest(abs_path=str(source)),
        )
        await db.commit()
        revision_id = result.revision_id

    await _bind(session_factory, revision_id)

    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        binding = await ds.get_binding(SCOPE.user_id, **HOST)
        assert binding is not None
        response = await _binding_response(ds, SCOPE.user_id, binding)

    assert response.content == document


async def test_binding_response_is_null_when_the_file_is_gone(
    session_factory, cwd
) -> None:
    """Missing bytes are reported as missing, not invented."""
    from valuz_agent.api.routes.artifacts import _binding_response

    source = cwd / "doomed.a2ui.jsonl"
    source.write_text('{"version":"v0.9.1"}\n', encoding="utf-8")

    async with session_factory() as db:
        result = await deliver_artifact(
            db,
            scope=SCOPE,
            scope_cwd=cwd,
            owner_roots=[cwd.resolve()],
            request=DeliveryRequest(abs_path=str(source)),
        )
        await db.commit()
        revision_id = result.revision_id

    await _bind(session_factory, revision_id)

    # The snapshot under .artifact/ is the bound path — remove it.
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        revision = await ds.get_revision(SCOPE.user_id, revision_id)
        assert revision is not None and revision.abs_path
        Path(revision.abs_path).unlink()
        binding = await ds.get_binding(SCOPE.user_id, **HOST)
        assert binding is not None
        response = await _binding_response(ds, SCOPE.user_id, binding)

    assert response.content is None


async def test_revision_content_reads_without_a_binding(session_factory, cwd) -> None:
    """Browsing a version must not require binding it first."""
    from valuz_agent.api.routes.artifacts import get_revision_content

    revision_id = await _generate(session_factory, cwd, '{"version":"v0.9.1"}')

    async with session_factory() as db:
        response = await get_revision_content(revision_id, db=db, user_id=SCOPE.user_id)

    assert response.content == '{"version":"v0.9.1"}'
    assert response.version_no == 1

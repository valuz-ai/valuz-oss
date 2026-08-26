"""The per-turn artifact section of ``<additional-context>``.

What the model is told about existing deliverables is the main thing making it
*continue* one rather than start a new one on every revision — so the shape of
this text is behaviour, not decoration. These tests pin the parts a later reader
would otherwise tidy away: the absolute paths, the truncation notice, and the
closing instructions.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.artifacts.context import MAX_LISTED, build_artifacts_section
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope
from valuz_agent.modules.artifacts.models import (
    REVISION_STATUS_MISSING,
    ArtifactContentRow,
    ArtifactHeadRow,
    ArtifactKeyRow,
    ArtifactRevisionRow,
    ArtifactRow,
)

_TABLES = [
    ArtifactRow.__table__,
    ArtifactKeyRow.__table__,
    ArtifactHeadRow.__table__,
    ArtifactRevisionRow.__table__,
    ArtifactContentRow.__table__,
]

MAIN = Scope(user_id="u1", project_id="p1")
BRANCH = Scope(user_id="u1", project_id="p1", worktree="feat-x")


@pytest.fixture
def session_factory(tmp_path):  # type: ignore[no-untyped-def]
    db_file = tmp_path / "artifacts.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=_TABLES)
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


async def _record(  # type: ignore[no-untyped-def]
    session_factory,
    scope: Scope,
    *,
    name: str,
    digest: str,
    status: str = "ready",
    abs_path: str | None = None,
) -> None:
    """One delivery, committed.

    The datastore only flushes — committing belongs to the caller's unit of work
    — and the section is read back through a second session, so the write has to
    land for real.
    """
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        artifact = await ds.find_by_keys(scope, rel_path=name, display_name=name)
        if artifact is None:
            artifact = await ds.create_artifact(
                scope, kind="document", display_name=name, rel_path=name
            )
        head = await ds.get_head(scope.user_id, artifact.id)
        content = await ds.create_content(scope.user_id, content_hash=digest, byte_size=1)
        await ds.append_revision(
            scope.user_id,
            artifact.id,
            expected_head_revision_id=head.revision_id if head else None,
            content=content,
            file_name=name,
            abs_path=(
                abs_path if abs_path is not None else f"/ws/p1/.artifact/{artifact.id}/v1/{name}"
            ),
            status=status,
        )
        await db.commit()


async def _archive(session_factory, scope: Scope, name: str) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        artifact = await ds.find_by_keys(scope, rel_path=name, display_name=name)
        assert artifact is not None
        await ds.archive(scope.user_id, artifact.id)
        await db.commit()


async def _section(session_factory, scope: Scope, **kwargs) -> str:  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        return await build_artifacts_section(
            db,
            user_id=scope.user_id,
            project_id=scope.project_id,
            worktree=scope.worktree,
            **kwargs,
        )


async def test_empty_scope_emits_nothing(session_factory):  # type: ignore[no-untyped-def]
    """No deliverables → no section, so an empty project pays no token cost."""
    assert await _section(session_factory, MAIN) == ""


async def test_no_project_emits_nothing(session_factory):  # type: ignore[no-untyped-def]
    assert await _section(session_factory, Scope(user_id="u1", project_id="")) == ""


async def test_lists_current_version_with_an_absolute_path(session_factory):  # type: ignore[no-untyped-def]
    """The path must be absolute — it is what the model reads and links."""
    await _record(session_factory, MAIN, name="report.md", digest="h1")
    await _record(session_factory, MAIN, name="report.md", digest="h2")

    section = await _section(session_factory, MAIN)

    assert "report.md" in section
    assert "v2" in section
    assert "current: /ws/p1/.artifact/" in section
    assert "1 total" in section


async def test_carries_the_artifact_id(session_factory):  # type: ignore[no-untyped-def]
    """Renaming a deliverable requires naming it, and this is the only place a
    session that did not itself deliver it can learn the id.

    Without it, a rename could only be expressed in the same conversation that
    made the thing — every later session would fork it instead.
    """
    await _record(session_factory, MAIN, name="report.md", digest="h1")

    async with session_factory() as db:
        artifact = await ArtifactDatastore(db).find_by_keys(
            MAIN, rel_path="report.md", display_name="report.md"
        )
    assert artifact is not None

    section = await _section(session_factory, MAIN)
    assert f"id {artifact.id}" in section


async def test_only_the_head_is_listed(session_factory):  # type: ignore[no-untyped-def]
    """Whole histories would grow the block by every revision ever made."""
    for digest in ("h1", "h2", "h3"):
        await _record(session_factory, MAIN, name="report.md", digest=digest)

    section = await _section(session_factory, MAIN)

    assert section.count("current:") == 1
    assert "v3" in section


async def test_truncates_and_says_so(session_factory):  # type: ignore[no-untyped-def]
    """A capped list that looked complete would have the model conclude a
    deliverable it cannot see does not exist."""
    for i in range(MAX_LISTED + 3):
        await _record(session_factory, MAIN, name=f"doc{i}.md", digest=f"h{i}")

    section = await _section(session_factory, MAIN)

    assert section.count("current:") == MAX_LISTED
    assert f"{MAX_LISTED + 3} total" in section
    assert "and 3 more" in section
    assert "ls .artifact/" in section


async def test_missing_bytes_are_named_without_a_path(session_factory):  # type: ignore[no-untyped-def]
    """A removed worktree leaves rows whose files are gone — do not send the
    model to read them."""
    await _record(
        session_factory,
        MAIN,
        name="gone.md",
        digest="h1",
        status=REVISION_STATUS_MISSING,
        abs_path=None,
    )

    section = await _section(session_factory, MAIN)

    assert "gone.md" in section
    assert "(unavailable)" in section
    assert "current: /" not in section


async def test_worktree_scope_sees_only_its_own(session_factory):  # type: ignore[no-untyped-def]
    """Listing the main line's deliverables to a worktree session would point it
    at paths its own ``ls .artifact/`` cannot find."""
    await _record(session_factory, MAIN, name="on-main.md", digest="h1")
    await _record(session_factory, BRANCH, name="on-branch.md", digest="h2")

    on_main = await _section(session_factory, MAIN)
    on_branch = await _section(session_factory, BRANCH)

    assert "on-main.md" in on_main and "on-branch.md" not in on_main
    assert "on-branch.md" in on_branch and "on-main.md" not in on_branch


async def test_other_owners_artifacts_are_not_listed(session_factory):  # type: ignore[no-untyped-def]
    await _record(
        session_factory, Scope(user_id="u2", project_id="p1"), name="theirs.md", digest="h1"
    )

    assert await _section(session_factory, MAIN) == ""


async def test_archived_artifacts_are_not_listed(session_factory):  # type: ignore[no-untyped-def]
    await _record(session_factory, MAIN, name="old.md", digest="h1")
    await _archive(session_factory, MAIN, "old.md")

    assert await _section(session_factory, MAIN) == ""


async def test_carries_the_revise_instructions(session_factory):  # type: ignore[no-untyped-def]
    """The instructions are the working part of this block.

    Without "same file name" the model forks a new deliverable on every
    revision; without "only when the user asked for a NEW deliverable" the
    listing biases it into revising something the user wanted left alone.
    """
    await _record(session_factory, MAIN, name="report.md", digest="h1")

    section = await _section(session_factory, MAIN)

    assert "SAME file name" in section
    assert "artifactId" in section  # the rename case, which keys cannot infer
    assert "Never write into .artifact/" in section
    assert "valuz-file://" in section


async def test_timestamps_follow_the_users_timezone(session_factory):  # type: ignore[no-untyped-def]
    """The clock line and these stamps must not disagree about the user's day."""
    await _record(session_factory, MAIN, name="report.md", digest="h1")

    tokyo = await _section(session_factory, MAIN, tz_name="Asia/Tokyo")
    honolulu = await _section(session_factory, MAIN, tz_name="Pacific/Honolulu")

    assert tokyo != honolulu


async def test_unknown_timezone_does_not_lose_the_section(session_factory):  # type: ignore[no-untyped-def]
    """A bad preference costs a wrong clock, not the whole listing."""
    await _record(session_factory, MAIN, name="report.md", digest="h1")

    assert "report.md" in await _section(session_factory, MAIN, tz_name="Not/AZone")

"""What happens to a worktree's deliverables when the worktree goes away.

Snapshots live inside the working directory, so removing a worktree destroys
them. The rows are not destroyed with them: a deliverable that existed is part
of the record, and a link the user still holds should explain itself rather than
404. What changes is liveness — the versions become ``missing`` and stop being
offered as openable.

The other half is refusing to get there by accident. Git no longer counts the
snapshot store as dirty, so nothing else would stop an automatic teardown from
quietly deleting the outputs a session was asked to produce.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra import git_worktree as gw
from valuz_agent.infra.database import Base
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope
from valuz_agent.modules.artifacts.models import (
    REVISION_STATUS_MISSING,
    REVISION_STATUS_READY,
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


async def _record(session_factory, scope: Scope, *, name: str, digest: str) -> str:  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        artifact = await ds.find_by_keys(scope, rel_path=name, display_name=name)
        if artifact is None:
            artifact = await ds.create_artifact(
                scope, kind="document", display_name=name, rel_path=name
            )
        head = await ds.get_head(scope.user_id, artifact.id)
        content = await ds.create_content(scope.user_id, content_hash=digest, byte_size=1)
        version_no = (head.version_no + 1) if head else 1
        await ds.append_revision(
            scope.user_id,
            artifact.id,
            expected_head_revision_id=head.revision_id if head else None,
            content=content,
            file_name=name,
            abs_path=f"/wt/feat-x/.artifact/{artifact.id}/v{version_no}/{name}",
        )
        await db.commit()
        return artifact.id


# ── Archiving on removal ──────────────────────────────────────────────────────


async def test_archives_the_scope_and_marks_every_version_missing(session_factory):  # type: ignore[no-untyped-def]
    artifact_id = await _record(session_factory, BRANCH, name="report.md", digest="h1")
    await _record(session_factory, BRANCH, name="report.md", digest="h2")

    async with session_factory() as db:
        retired = await ArtifactDatastore(db).archive_scope(BRANCH)
        await db.commit()

    assert retired == 1
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        revisions = await ds.list_revisions("u1", artifact_id)
        assert [r.status for r in revisions] == [REVISION_STATUS_MISSING] * 2
        # The path is kept as a breadcrumb — it says where the bytes WERE.
        assert all(r.abs_path for r in revisions)
        # The rows survive; only their liveness changed.
        assert await ds.get_artifact("u1", artifact_id) is not None


async def test_archived_scope_disappears_from_the_listings(session_factory):  # type: ignore[no-untyped-def]
    await _record(session_factory, BRANCH, name="report.md", digest="h1")

    async with session_factory() as db:
        await ArtifactDatastore(db).archive_scope(BRANCH)
        await db.commit()

    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        assert await ds.list_scope_heads(BRANCH) == []
        assert await ds.count_scope_artifacts(BRANCH) == 0
        # And the name is free again, so a later delivery starts clean rather
        # than resurrecting a history whose files are gone.
        assert await ds.find_by_keys(BRANCH, rel_path="report.md", display_name="report.md") is None


async def test_only_the_removed_worktrees_scope_is_touched(session_factory):  # type: ignore[no-untyped-def]
    """The main line's deliverables are not in that directory and must survive."""
    on_main = await _record(session_factory, MAIN, name="report.md", digest="h1")
    await _record(session_factory, BRANCH, name="report.md", digest="h2")

    async with session_factory() as db:
        await ArtifactDatastore(db).archive_scope(BRANCH)
        await db.commit()

    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        assert await ds.count_scope_artifacts(MAIN) == 1
        (revision,) = await ds.list_revisions("u1", on_main)
        assert revision.status == REVISION_STATUS_READY


async def test_archiving_an_empty_scope_is_a_no_op(session_factory):  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        assert await ArtifactDatastore(db).archive_scope(BRANCH) == 0


# ── Not getting there by accident ─────────────────────────────────────────────


@pytest.fixture
def removable_worktree(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """A worktree git would happily remove, so only our own guard can block it.

    Without pinning ``has_changes`` the fake repo fails closed and teardown
    stops for git's reasons — which would let this test pass whether or not the
    artifact guard exists at all.
    """
    from valuz_agent.modules.worktrees import service as worktree_module

    removals: list[str] = []
    monkeypatch.setattr(gw, "has_changes", lambda path, base_sha: False)
    monkeypatch.setattr(gw, "remove", lambda root, path, branch: removals.append(str(path)))
    monkeypatch.setattr(worktree_module, "_remove_sidecar", lambda root, flat: None)

    git_root = tmp_path / "repo"
    path = gw.worktrees_dir(git_root) / "feat-x"
    (path / ".git").mkdir(parents=True)
    snapshot = {
        "git_root": str(git_root),
        "path": str(path),
        "name": "feat-x",
        "branch": "feat-x",
        "base_sha": "abc123",
    }
    return snapshot, removals


async def test_automatic_teardown_keeps_a_worktree_holding_deliverables(  # type: ignore[no-untyped-def]
    session_factory,
    monkeypatch,
    removable_worktree,
):
    """Clean-teardown must not destroy the outputs the session produced.

    Git no longer sees the snapshot store as dirty, so this check is the only
    thing standing between an automatic cleanup and the user's deliverables.
    """
    from valuz_agent.modules.worktrees import service as worktree_module

    snapshot, removals = removable_worktree
    await _record(session_factory, BRANCH, name="report.md", digest="h1")

    async def _count(user_id: str, project_id: str, worktree: str) -> int:
        async with session_factory() as db:
            return await ArtifactDatastore(db).count_scope_artifacts(
                Scope(user_id=user_id, project_id=project_id, worktree=worktree)
            )

    monkeypatch.setattr(worktree_module, "_scope_artifact_count", _count)

    removed = await worktree_module.worktree_service.cleanup_if_clean(
        snapshot, user_id="u1", project_id="p1"
    )

    assert removed is False
    assert removals == []  # git was never asked to remove it
    # Refusing to remove must not archive anything either.
    async with session_factory() as db:
        assert await ArtifactDatastore(db).count_scope_artifacts(BRANCH) == 1


async def test_automatic_teardown_still_removes_an_empty_worktree(  # type: ignore[no-untyped-def]
    session_factory,
    monkeypatch,
    removable_worktree,
):
    """The guard is about deliverables, not about blocking cleanup outright."""
    from valuz_agent.modules.worktrees import service as worktree_module

    snapshot, removals = removable_worktree

    async def _count(user_id: str, project_id: str, worktree: str) -> int:
        return 0

    monkeypatch.setattr(worktree_module, "_scope_artifact_count", _count)

    removed = await worktree_module.worktree_service.cleanup_if_clean(
        snapshot, user_id="u1", project_id="p1"
    )

    assert removed is True
    assert len(removals) == 1


async def test_callers_without_owner_context_keep_the_old_behaviour(  # type: ignore[no-untyped-def]
    session_factory,
    monkeypatch,
    removable_worktree,
):
    """No owner → no artifact scope to check; do not silently block teardown."""
    from valuz_agent.modules.worktrees import service as worktree_module

    snapshot, removals = removable_worktree
    calls: list[tuple[str, str, str]] = []

    async def _count(user_id: str, project_id: str, worktree: str) -> int:
        calls.append((user_id, project_id, worktree))
        return 1

    monkeypatch.setattr(worktree_module, "_scope_artifact_count", _count)

    removed = await worktree_module.worktree_service.cleanup_if_clean(snapshot)

    assert calls == []
    assert removed is True and len(removals) == 1


# ── Git exclusion ─────────────────────────────────────────────────────────────


def test_artifact_store_is_excluded_from_git_status(tmp_path):  # type: ignore[no-untyped-def]
    """Otherwise every delivery makes the worktree permanently "dirty".

    That would silently disable clean-teardown and make ``discard`` refuse with
    "has work worth keeping" about files the user never wrote — inheriting a
    policy from what git considers noise instead of deciding it.
    """
    common_dir = tmp_path / ".git"
    common_dir.mkdir()

    gw.ensure_info_exclude(common_dir)

    lines = {line.strip() for line in (common_dir / "info" / "exclude").read_text().splitlines()}
    assert ".artifact/" in lines
    assert ".valuz/" in lines


def test_exclusion_is_idempotent_and_backfills_new_markers(tmp_path):  # type: ignore[no-untyped-def]
    """A repo excluded before ``.artifact/`` existed must pick it up."""
    common_dir = tmp_path / ".git"
    (common_dir / "info").mkdir(parents=True)
    (common_dir / "info" / "exclude").write_text("# valuz project worktrees\n.valuz/\n")

    gw.ensure_info_exclude(common_dir)
    gw.ensure_info_exclude(common_dir)

    text = (common_dir / "info" / "exclude").read_text()
    assert text.count(".artifact/") == 1
    assert text.count(".valuz/") == 1

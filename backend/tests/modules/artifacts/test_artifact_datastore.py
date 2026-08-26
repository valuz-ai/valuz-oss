"""Artifact datastore: identity lookup, idempotency, head compare-and-set.

These are the three things that can only be decided against the database, and
each has a failure mode that is invisible in the happy path: a lookup that
merges two deliverables, a replay that mints a phantom version, and two
deliveries in one turn that both think they won.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.artifacts.datastore import (
    ArtifactDatastore,
    Scope,
    name_key_value,
    normalize_name_key,
    rel_dir_of,
)
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


@pytest.fixture
def session_factory(tmp_path):  # type: ignore[no-untyped-def]
    db_file = tmp_path / "artifacts.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=_TABLES)
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


@pytest.fixture
def scope() -> Scope:
    return Scope(user_id="u1", project_id="p1")


async def _content(ds: ArtifactDatastore, digest: str, size: int = 10) -> ArtifactContentRow:
    return await ds.create_content(
        "u1",
        content_hash=digest,
        byte_size=size,
        storage_key=f"/ws/p1/.artifact/x/{digest}",
    )


async def _deliver(
    ds: ArtifactDatastore,
    scope: Scope,
    *,
    rel_path: str,
    display_name: str,
    digest: str,
    session_id: str = "s1",
) -> ArtifactRevisionRow | None:
    """One end-to-end delivery, mirroring the handler's intended order."""
    artifact = await ds.find_by_keys(scope, rel_path=rel_path, display_name=display_name)
    if artifact is None:
        artifact = await ds.create_artifact(
            scope, kind="document", display_name=display_name, rel_path=rel_path
        )
    current = await ds.get_head_with_revision(scope.user_id, artifact.id)
    head, head_revision = current if current is not None else (None, None)
    if head_revision is not None and head_revision.content_hash == digest:
        return head_revision  # a replay of the current version
    content = await _content(ds, digest)
    return await ds.append_revision(
        scope.user_id,
        artifact.id,
        expected_head_revision_id=head.revision_id if head else None,
        content=content,
        file_name=display_name,
        abs_path=f"/ws/p1/.artifact/{artifact.id}/v/{display_name}",
        source_session_id=session_id,
    )


# ── Identity ──────────────────────────────────────────────────────────────────


async def test_same_path_across_sessions_is_one_artifact(session_factory, scope):  # type: ignore[no-untyped-def]
    """The point of the whole design: a hand-off continues, it does not fork."""
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        r1 = await _deliver(
            ds, scope, rel_path="report.md", display_name="report.md", digest="h1", session_id="s1"
        )
        r2 = await _deliver(
            ds, scope, rel_path="report.md", display_name="report.md", digest="h2", session_id="s2"
        )

        assert r1 is not None and r2 is not None
        assert r1.artifact_id == r2.artifact_id
        assert (r1.version_no, r2.version_no) == (1, 2)
        assert r2.parent_revision_id == r1.id
        assert r1.source_session_id == "s1" and r2.source_session_id == "s2"


async def test_renamed_file_in_place_still_matches_via_name_key(session_factory, scope):  # type: ignore[no-untyped-def]
    """A rewrite renamed beside its predecessor lands on the same artifact.

    This is the case the name fallback exists for: the agent writes
    ``report-final.md`` next to the ``report.md`` it delivered before, under the
    same display name.
    """
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        first = await _deliver(
            ds, scope, rel_path="out/report.md", display_name="Quarterly Report", digest="h1"
        )
        second = await _deliver(
            ds, scope, rel_path="out/report-final.md", display_name="quarterly  report", digest="h2"
        )

        assert first is not None and second is not None
        assert first.artifact_id == second.artifact_id


async def test_cross_directory_rename_forks_rather_than_merges(session_factory, scope):  # type: ignore[no-untyped-def]
    """Name keys are directory-qualified, so moving folders starts a new artifact.

    The trade the qualifier buys: this case forks (recoverable) so that two
    unrelated same-named files cannot merge (not recoverable).
    """
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        draft = await _deliver(
            ds, scope, rel_path="draft/report.md", display_name="Quarterly Report", digest="h1"
        )
        final = await _deliver(
            ds, scope, rel_path="final/report.md", display_name="Quarterly Report", digest="h2"
        )

        assert draft is not None and final is not None
        assert draft.artifact_id != final.artifact_id


async def test_distinct_paths_and_names_are_distinct_artifacts(session_factory, scope):  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        a = await _deliver(ds, scope, rel_path="a.md", display_name="Alpha", digest="h1")
        b = await _deliver(ds, scope, rel_path="b.md", display_name="Beta", digest="h2")

        assert a is not None and b is not None
        assert a.artifact_id != b.artifact_id


async def test_same_name_in_different_folders_does_not_merge(session_factory, scope):  # type: ignore[no-untyped-def]
    """Two unrelated ``report.md`` must not become one deliverable's history.

    The name key is taken by whoever got there first; the second delivery has a
    distinct path key, so it gets its own artifact rather than silently becoming
    the first one's v2.
    """
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        marketing = await _deliver(
            ds, scope, rel_path="marketing/report.md", display_name="report.md", digest="h1"
        )
        finance = await _deliver(
            ds, scope, rel_path="finance/report.md", display_name="report.md", digest="h2"
        )

        assert marketing is not None and finance is not None
        assert marketing.artifact_id != finance.artifact_id


async def test_worktree_scope_isolates_identity(session_factory):  # type: ignore[no-untyped-def]
    """Same path, different cwd — a worktree is an independent line of work."""
    main = Scope(user_id="u1", project_id="p1")
    branch = Scope(user_id="u1", project_id="p1", worktree="feat-x")
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        on_main = await _deliver(ds, main, rel_path="r.md", display_name="r.md", digest="h1")
        on_branch = await _deliver(ds, branch, rel_path="r.md", display_name="r.md", digest="h2")

        assert on_main is not None and on_branch is not None
        assert on_main.artifact_id != on_branch.artifact_id


async def test_other_owner_cannot_match_my_artifact(session_factory, scope):  # type: ignore[no-untyped-def]
    other = Scope(user_id="u2", project_id="p1")
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        await _deliver(ds, scope, rel_path="r.md", display_name="r.md", digest="h1")

        assert await ds.find_by_keys(other, rel_path="r.md", display_name="r.md") is None


# ── Idempotency ───────────────────────────────────────────────────────────────


async def test_replaying_the_same_content_returns_the_same_revision(session_factory, scope):  # type: ignore[no-untyped-def]
    """A retried tool call must not mint a version whose only diff is the clock."""
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        first = await _deliver(ds, scope, rel_path="r.md", display_name="r.md", digest="h1")
        replay = await _deliver(ds, scope, rel_path="r.md", display_name="r.md", digest="h1")

        assert first is not None and replay is not None
        assert replay.id == first.id
        assert len(await ds.list_revisions("u1", first.artifact_id)) == 1


async def test_identical_content_on_a_different_artifact_is_allowed(session_factory, scope):  # type: ignore[no-untyped-def]
    """Idempotency is per artifact — two deliverables may hold the same bytes."""
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        a = await _deliver(ds, scope, rel_path="a.md", display_name="A", digest="same")
        b = await _deliver(ds, scope, rel_path="b.md", display_name="B", digest="same")

        assert a is not None and b is not None
        assert a.artifact_id != b.artifact_id


async def test_existing_content_row_is_reused_by_hash(session_factory, scope):  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        await _content(ds, "dup")

        found = await ds.find_content_by_hash("u1", "dup")
        assert found is not None
        assert await ds.find_content_by_hash("u2", "dup") is None  # owner-scoped


# ── Head compare-and-set ──────────────────────────────────────────────────────


async def test_stale_base_is_refused_not_branched(session_factory, scope):  # type: ignore[no-untyped-def]
    """Two deliveries racing on one artifact: one wins, the other is told.

    Both read the same head (what a runtime's parallel ``tool_use`` blocks do),
    so the loser must come back empty rather than fork the history or overwrite
    the winner.
    """
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        artifact = await ds.create_artifact(
            scope, kind="document", display_name="r.md", rel_path="r.md"
        )
        base = await ds.append_revision(
            "u1",
            artifact.id,
            expected_head_revision_id=None,
            content=await _content(ds, "h1"),
            file_name="r.md",
            abs_path="/ws/v1/r.md",
        )
        assert base is not None

        winner = await ds.append_revision(
            "u1",
            artifact.id,
            expected_head_revision_id=base.id,
            content=await _content(ds, "h2"),
            file_name="r.md",
            abs_path="/ws/v2/r.md",
        )
        loser = await ds.append_revision(
            "u1",
            artifact.id,
            expected_head_revision_id=base.id,  # stale — winner already moved it
            content=await _content(ds, "h3"),
            file_name="r.md",
            abs_path="/ws/v3/r.md",
        )

        assert winner is not None
        assert loser is None
        head = await ds.get_head("u1", artifact.id)
        assert head is not None
        assert (head.revision_id, head.version_no) == (winner.id, 2)


async def test_first_revision_requires_no_base(session_factory, scope):  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        artifact = await ds.create_artifact(
            scope, kind="document", display_name="r.md", rel_path="r.md"
        )
        # A caller that wrongly claims a base on a fresh artifact is refused.
        assert (
            await ds.append_revision(
                "u1",
                artifact.id,
                expected_head_revision_id="nope",
                content=await _content(ds, "h1"),
                file_name="r.md",
                abs_path="/ws/v1/r.md",
            )
            is None
        )


async def test_head_tracks_latest_across_a_chain(session_factory, scope):  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        last = None
        for i, digest in enumerate(("h1", "h2", "h3"), start=1):
            last = await _deliver(ds, scope, rel_path="r.md", display_name="r.md", digest=digest)
            assert last is not None and last.version_no == i

        assert last is not None
        head = await ds.get_head("u1", last.artifact_id)
        assert head is not None
        assert (head.revision_id, head.version_no) == (last.id, 3)


# ── Reads, rename, archive ────────────────────────────────────────────────────


async def test_list_scope_heads_returns_current_versions_only(session_factory, scope):  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        await _deliver(ds, scope, rel_path="a.md", display_name="A", digest="h1")
        await _deliver(ds, scope, rel_path="a.md", display_name="A", digest="h2")
        await _deliver(ds, scope, rel_path="b.md", display_name="B", digest="h3")

        rows = await ds.list_scope_heads(scope)
        assert len(rows) == 2
        by_name = {artifact.display_name: (head, rev) for artifact, head, rev in rows}
        assert by_name["A"][0].version_no == 2
        assert by_name["A"][1].content_hash == "h2"


async def test_list_session_revisions_is_scoped_to_that_session(session_factory, scope):  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        await _deliver(ds, scope, rel_path="a.md", display_name="A", digest="h1", session_id="s1")
        await _deliver(ds, scope, rel_path="a.md", display_name="A", digest="h2", session_id="s2")

        assert len(await ds.list_session_revisions("u1", "s1")) == 1
        assert len(await ds.list_session_revisions("u1", "s2")) == 1


async def test_rename_does_not_create_a_revision(session_factory, scope):  # type: ignore[no-untyped-def]
    """Relabelling is not a new generation — no bytes changed."""
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        rev = await _deliver(ds, scope, rel_path="r.md", display_name="Draft", digest="h1")
        assert rev is not None

        renamed = await ds.rename(scope, artifact_id=rev.artifact_id, display_name="Final Report")
        assert renamed is not None and renamed.display_name == "Final Report"
        assert len(await ds.list_revisions("u1", rev.artifact_id)) == 1

        # Both the old and the new name still resolve to it.
        for name in ("Draft", "Final Report"):
            found = await ds.find_by_keys(scope, rel_path="", display_name=name)
            assert found is not None and found.id == rev.artifact_id


async def test_archived_artifact_frees_its_keys(session_factory, scope):  # type: ignore[no-untyped-def]
    """Re-delivering an archived name starts fresh, it does not resurrect."""
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        first = await _deliver(ds, scope, rel_path="r.md", display_name="R", digest="h1")
        assert first is not None
        await ds.archive("u1", first.artifact_id)

        assert await ds.find_by_keys(scope, rel_path="r.md", display_name="R") is None
        second = await _deliver(ds, scope, rel_path="r.md", display_name="R", digest="h2")
        assert second is not None and second.artifact_id != first.artifact_id


async def test_missing_status_revision_can_be_recorded(session_factory, scope):  # type: ignore[no-untyped-def]
    """The backfill and worktree-removal both need a revision with no readable bytes."""
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        artifact = await ds.create_artifact(
            scope, kind="file", display_name="gone.pdf", rel_path="gone.pdf"
        )
        rev = await ds.append_revision(
            "u1",
            artifact.id,
            expected_head_revision_id=None,
            content=await _content(ds, "h1", size=0),
            file_name="gone.pdf",
            abs_path=None,
            status=REVISION_STATUS_MISSING,
            legacy_row_id="legacy-1",
        )

        assert rev is not None
        assert rev.status == REVISION_STATUS_MISSING
        assert rev.legacy_row_id == "legacy-1"


def test_normalize_name_key_folds_case_and_whitespace() -> None:
    assert normalize_name_key("  Quarterly   Report ") == normalize_name_key("quarterly report")
    # Punctuation is NOT folded: a wrong merge corrupts history, a wrong fork
    # does not.
    assert normalize_name_key("report-v2.md") != normalize_name_key("report v2.md")


def test_name_key_is_directory_qualified() -> None:
    assert name_key_value(rel_dir_of("marketing/report.md"), "report.md") != name_key_value(
        rel_dir_of("finance/report.md"), "report.md"
    )
    # Same folder, different file name, same display name -> same key.
    assert name_key_value(rel_dir_of("out/a.md"), "Report") == name_key_value(
        rel_dir_of("out/b.md"), "report"
    )
    assert name_key_value(rel_dir_of("top.md"), "Top") == "top"


async def test_lost_race_leaves_nothing_behind_for_the_retry(session_factory, scope):  # type: ignore[no-untyped-def]
    """A refused delivery must not block its own retry.

    The caller's response to ``None`` is to re-read the head and try again with
    the same content. If the losing attempt had already written its revision
    row, that retry would collide with the content-hash idempotency constraint
    and fail a delivery that should have succeeded.
    """
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        artifact = await ds.create_artifact(
            scope, kind="document", display_name="r.md", rel_path="r.md"
        )
        base = await ds.append_revision(
            "u1",
            artifact.id,
            expected_head_revision_id=None,
            content=await _content(ds, "h1"),
            file_name="r.md",
            abs_path="/ws/v1/r.md",
        )
        assert base is not None
        winner = await ds.append_revision(
            "u1",
            artifact.id,
            expected_head_revision_id=base.id,
            content=await _content(ds, "h2"),
            file_name="r.md",
            abs_path="/ws/v2/r.md",
        )
        assert winner is not None

        content = await _content(ds, "h3")
        assert (
            await ds.append_revision(
                "u1",
                artifact.id,
                expected_head_revision_id=base.id,  # stale
                content=content,
                file_name="r.md",
                abs_path="/ws/v3/r.md",
            )
            is None
        )
        # Nothing recorded for the refused attempt.
        assert [r.content_hash for r in await ds.list_revisions("u1", artifact.id)] == ["h1", "h2"]

        retried = await ds.append_revision(
            "u1",
            artifact.id,
            expected_head_revision_id=winner.id,  # re-read head
            content=content,
            file_name="r.md",
            abs_path="/ws/v3/r.md",
        )
        assert retried is not None and retried.version_no == 3


async def test_new_artifact_at_a_used_path_takes_the_key_over(session_factory, scope):  # type: ignore[no-untyped-def]
    """The ``asNewArtifact`` path: same file, deliberately a different deliverable.

    A path holds one artifact at a time. The newcomer takes the key so later
    deliveries of that file continue IT, while the previous artifact keeps its
    history.
    """
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        old = await _deliver(ds, scope, rel_path="r.md", display_name="R", digest="h1")
        assert old is not None

        fresh = await ds.create_artifact(scope, kind="document", display_name="R", rel_path="r.md")
        assert fresh.id != old.artifact_id

        found = await ds.find_by_keys(scope, rel_path="r.md", display_name="R")
        assert found is not None and found.id == fresh.id
        # The displaced artifact still has its history.
        assert len(await ds.list_revisions("u1", old.artifact_id)) == 1

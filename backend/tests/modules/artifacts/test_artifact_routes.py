"""Artifact read endpoints.

Three questions, three answers, and the distinctions between them are the point:

* per session — what did *this conversation* produce (including versions since
  superseded, flagged as such);
* per scope — what does the *workspace* currently hold;
* per artifact — how did one of them get here.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.api.routes.artifacts import (
    list_artifact_revisions,
    list_scope_artifacts,
)
from valuz_agent.infra.database import Base
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
    session_id: str = "s1",
    status: str = "ready",
    abs_path: str | None = None,
) -> str:
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        artifact = await ds.find_by_keys(scope, rel_path=name, display_name=name)
        if artifact is None:
            artifact = await ds.create_artifact(
                scope, kind="document", display_name=name, rel_path=name
            )
        head = await ds.get_head(scope.user_id, artifact.id)
        content = await ds.create_content(
            scope.user_id, content_hash=digest, byte_size=42, mime_type="text/markdown"
        )
        # Mirrors the real layout: every version gets its OWN directory, which
        # is what keeps a superseded version openable.
        version_no = (head.version_no + 1) if head else 1
        await ds.append_revision(
            scope.user_id,
            artifact.id,
            expected_head_revision_id=head.revision_id if head else None,
            content=content,
            file_name=name,
            abs_path=(
                abs_path
                if abs_path is not None
                else f"/ws/.artifact/{artifact.id}/v{version_no}/{name}"
            ),
            source_session_id=session_id,
            status=status,
        )
        await db.commit()
        return artifact.id


# ── Scope listing ─────────────────────────────────────────────────────────────


async def test_lists_deliverables_at_their_latest_version(session_factory):  # type: ignore[no-untyped-def]
    await _record(session_factory, MAIN, name="report.md", digest="h1")
    await _record(session_factory, MAIN, name="report.md", digest="h2")

    async with session_factory() as db:
        response = await list_scope_artifacts(
            project_id="p1", worktree="", limit=200, db=db, user_id="u1"
        )

    assert response.total == 1
    (item,) = response.items
    assert item.display_name == "report.md"
    assert item.version_no == 2
    assert item.current.version_no == 2
    assert item.current.ref.startswith("valuz-file://")


async def test_scope_listing_is_worktree_aware(session_factory):  # type: ignore[no-untyped-def]
    """A worktree's deliverables live in its own directory; mixing the two lists
    files that cannot both be open."""
    await _record(session_factory, MAIN, name="on-main.md", digest="h1")
    await _record(session_factory, BRANCH, name="on-branch.md", digest="h2")

    async with session_factory() as db:
        main = await list_scope_artifacts(
            project_id="p1", worktree="", limit=200, db=db, user_id="u1"
        )
        branch = await list_scope_artifacts(
            project_id="p1", worktree="feat-x", limit=200, db=db, user_id="u1"
        )

    assert [i.display_name for i in main.items] == ["on-main.md"]
    assert [i.display_name for i in branch.items] == ["on-branch.md"]


async def test_scope_listing_is_owner_scoped(session_factory):  # type: ignore[no-untyped-def]
    await _record(session_factory, Scope(user_id="u2", project_id="p1"), name="x.md", digest="h1")

    async with session_factory() as db:
        response = await list_scope_artifacts(
            project_id="p1", worktree="", limit=200, db=db, user_id="u1"
        )

    assert response.items == [] and response.total == 0


async def test_total_reports_beyond_the_limit(session_factory):  # type: ignore[no-untyped-def]
    """The client needs to know the page is partial to offer "show more"."""
    for i in range(4):
        await _record(session_factory, MAIN, name=f"doc{i}.md", digest=f"h{i}")

    async with session_factory() as db:
        response = await list_scope_artifacts(
            project_id="p1", worktree="", limit=2, db=db, user_id="u1"
        )

    assert len(response.items) == 2
    assert response.total == 4


async def test_missing_bytes_yield_no_openable_ref(session_factory):  # type: ignore[no-untyped-def]
    """Show the version, but do not offer to open a file that is not there."""
    await _record(
        session_factory,
        MAIN,
        name="gone.md",
        digest="h1",
        status=REVISION_STATUS_MISSING,
        abs_path=None,
    )

    async with session_factory() as db:
        response = await list_scope_artifacts(
            project_id="p1", worktree="", limit=200, db=db, user_id="u1"
        )

    (item,) = response.items
    assert item.current.status == REVISION_STATUS_MISSING
    assert item.current.ref == ""


# ── History ───────────────────────────────────────────────────────────────────


async def test_revisions_are_oldest_first_each_with_its_own_path(session_factory):  # type: ignore[no-untyped-def]
    """Every version stays openable — that is what "does not overwrite" means."""
    artifact_id = await _record(session_factory, MAIN, name="report.md", digest="h1")
    await _record(session_factory, MAIN, name="report.md", digest="h2")
    await _record(session_factory, MAIN, name="report.md", digest="h3")

    async with session_factory() as db:
        response = await list_artifact_revisions(artifact_id, db=db, user_id="u1")

    assert [r.version_no for r in response.items] == [1, 2, 3]
    assert all(r.ref for r in response.items)
    assert response.display_name == "report.md"


async def test_revisions_of_another_owner_are_not_found(session_factory):  # type: ignore[no-untyped-def]
    artifact_id = await _record(
        session_factory, Scope(user_id="u2", project_id="p1"), name="x.md", digest="h1"
    )

    async with session_factory() as db:
        with pytest.raises(Exception) as excinfo:
            await list_artifact_revisions(artifact_id, db=db, user_id="u1")

    assert "404" in str(excinfo.value) or "not found" in str(excinfo.value).lower()


async def test_unknown_artifact_is_404(session_factory):  # type: ignore[no-untyped-def]
    async with session_factory() as db:
        with pytest.raises(Exception) as excinfo:
            await list_artifact_revisions("NOPE", db=db, user_id="u1")

    assert "404" in str(excinfo.value) or "not found" in str(excinfo.value).lower()


async def test_history_records_which_session_made_each_version(session_factory):  # type: ignore[no-untyped-def]
    """Provenance survives the hand-off — that is the point of continuing an
    artifact across sessions rather than forking one per conversation."""
    artifact_id = await _record(
        session_factory, MAIN, name="report.md", digest="h1", session_id="s1"
    )
    await _record(session_factory, MAIN, name="report.md", digest="h2", session_id="s2")

    async with session_factory() as db:
        response = await list_artifact_revisions(artifact_id, db=db, user_id="u1")

    assert [r.source_session_id for r in response.items] == ["s1", "s2"]


# ── File tree ─────────────────────────────────────────────────────────────────


def test_artifact_store_is_never_listed_in_the_file_tree(tmp_path):  # type: ignore[no-untyped-def]
    """Excluded even with ``include_hidden``, unlike ``.git`` and friends.

    The snapshot store is not user content: listing it invites edits to files
    whose whole value is that they do not change, and buries the working tree
    under one directory per version.
    """
    from valuz_agent.modules.projects.service import _walk_dir

    (tmp_path / "report.md").write_text("x", encoding="utf-8")
    (tmp_path / ".artifact" / "A7K2PH3M" / "v1").mkdir(parents=True)
    (tmp_path / ".artifact" / "A7K2PH3M" / "v1" / "report.md").write_text("v1", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    for include_hidden in (False, True):
        names = {n.name for n in _walk_dir(tmp_path, depth=3, include_hidden=include_hidden)}
        assert ".artifact" not in names
        assert "report.md" in names

    # Sanity: ``.git`` IS revealed by include_hidden, so the assertion above is
    # about the exclusion and not about hidden handling in general.
    revealed = {n.name for n in _walk_dir(tmp_path, depth=3, include_hidden=True)}
    assert ".git" in revealed


# ── Per-session listing ───────────────────────────────────────────────────────


async def test_session_listing_flags_superseded_versions(session_factory, monkeypatch):  # type: ignore[no-untyped-def]
    """The panel must not present a stale version as the deliverable.

    A session's list is what it delivered, so a version another session has
    since replaced still belongs in it — but marked, or the user would open an
    old file believing it current.
    """
    from valuz_agent.api.routes import sessions as sessions_routes

    await _record(session_factory, MAIN, name="report.md", digest="h1", session_id="s1")
    await _record(session_factory, MAIN, name="report.md", digest="h2", session_id="s2")

    class _Reader:
        async def get_session(self, user_id: str, session_id: str) -> object:
            return object()

    monkeypatch.setattr(sessions_routes, "data_reader", lambda: _Reader())

    async with session_factory() as db:
        first = await sessions_routes.list_artifacts("s1", db=db, user_id="u1")
        second = await sessions_routes.list_artifacts("s2", db=db, user_id="u1")

    (v1,) = first.items
    (v2,) = second.items
    assert (v1.version_no, v1.is_current) == (1, False)
    assert (v2.version_no, v2.is_current) == (2, True)
    # Both remain openable at their own snapshot — superseded is not deleted.
    assert v1.file_path != v2.file_path
    assert v1.artifact_id == v2.artifact_id


# ── Query shape ───────────────────────────────────────────────────────────────


async def test_listing_does_not_query_per_row(session_factory):  # type: ignore[no-untyped-def]
    """Listing N deliverables must not cost N round trips.

    Every one of these endpoints needs the content row behind each revision it
    returns, and fetching them one at a time is a per-item network hop on the
    cloud's Postgres — paid on every panel open. Counting statements is the only
    way this stays true: the loop reads correctly either way.
    """
    for i in range(6):
        await _record(session_factory, MAIN, name=f"doc{i}.md", digest=f"h{i}")

    async with session_factory() as db:
        seen: list[str] = []
        from sqlalchemy import event

        def _count(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
            if statement.lstrip().upper().startswith("SELECT"):
                seen.append(statement)

        engine = db.get_bind()
        engine = getattr(engine, "sync_engine", engine)
        event.listen(engine, "before_cursor_execute", _count)
        try:
            response = await list_scope_artifacts(
                project_id="p1", worktree="", limit=200, db=db, user_id="u1"
            )
        finally:
            event.remove(engine, "before_cursor_execute", _count)

    assert len(response.items) == 6
    # heads+revisions join, count, contents — a small constant, not 6-ish.
    assert len(seen) <= 4, f"{len(seen)} selects for 6 rows:\n" + "\n".join(seen)


async def test_session_listing_shows_one_row_per_deliverable(session_factory, monkeypatch):  # type: ignore[no-untyped-def]
    """Delivering the same file twice produced one thing, twice — not two things.

    A row per revision reads as a duplicate file in the panel, and once the row
    can expand a version history it would show that history once per row.
    """
    from valuz_agent.api.routes import sessions as sessions_routes

    await _record(session_factory, MAIN, name="report.md", digest="h1", session_id="s1")
    await _record(session_factory, MAIN, name="report.md", digest="h2", session_id="s1")
    await _record(session_factory, MAIN, name="other.md", digest="h3", session_id="s1")

    class _Reader:
        async def get_session(self, user_id: str, session_id: str) -> object:
            return object()

    monkeypatch.setattr(sessions_routes, "data_reader", lambda: _Reader())

    async with session_factory() as db:
        response = await sessions_routes.list_artifacts("s1", db=db, user_id="u1")

    assert [i.file_name for i in response.items] == ["report.md", "other.md"]
    # And it is the LATEST version this session produced, not the first.
    report = response.items[0]
    assert report.version_no == 2
    assert report.is_current is True


# ── Across sessions ───────────────────────────────────────────────────────────


async def test_a_later_session_can_open_versions_it_did_not_make(session_factory, monkeypatch):  # type: ignore[no-untyped-def]
    """History is the deliverable's, not the conversation's.

    A hand-off is only real if the session that inherits a deliverable can see
    where it came from — otherwise "one artifact continued across sessions" is
    true in the database and invisible everywhere else.
    """
    from valuz_agent.api.routes import sessions as sessions_routes

    artifact_id = await _record(
        session_factory, MAIN, name="report.md", digest="h1", session_id="sessA"
    )
    await _record(session_factory, MAIN, name="report.md", digest="h2", session_id="sessB")

    class _Reader:
        async def get_session(self, user_id: str, session_id: str) -> object:
            return object()

    monkeypatch.setattr(sessions_routes, "data_reader", lambda: _Reader())

    async with session_factory() as db:
        history = await list_artifact_revisions(artifact_id, db=db, user_id="u1")
        panel_a = await sessions_routes.list_artifacts("sessA", db=db, user_id="u1")
        panel_b = await sessions_routes.list_artifacts("sessB", db=db, user_id="u1")

    # Both generations, each attributable and each still openable.
    assert [(r.version_no, r.source_session_id, bool(r.ref)) for r in history.items] == [
        (1, "sessA", True),
        (2, "sessB", True),
    ]
    # The session that made v1 still lists it — flagged as no longer current, so
    # the panel does not present a superseded version as the deliverable.
    assert [(i.version_no, i.is_current) for i in panel_a.items] == [(1, False)]
    assert [(i.version_no, i.is_current) for i in panel_b.items] == [(2, True)]


async def test_the_workspace_view_shows_one_current_deliverable(session_factory):  # type: ignore[no-untyped-def]
    """However many sessions it took, the workspace holds one thing at v2.

    This is the view a session that delivered nothing has to read from — its own
    per-session list is empty by definition.
    """
    await _record(session_factory, MAIN, name="report.md", digest="h1", session_id="sessA")
    await _record(session_factory, MAIN, name="report.md", digest="h2", session_id="sessB")

    async with session_factory() as db:
        response = await list_scope_artifacts(
            project_id="p1", worktree="", limit=200, db=db, user_id="u1"
        )

    assert response.total == 1
    (item,) = response.items
    assert (item.display_name, item.version_no) == ("report.md", 2)

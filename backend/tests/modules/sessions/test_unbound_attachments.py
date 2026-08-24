"""An attachment exists before its session does.

Attaching a file used to require a session, so the composer created one — and
under scoped allocation creating a session provisions a sandbox. The upload
path never touches it: the bytes go to the owner's data dir and the parse is a
host task over HTTP. So the upload stands alone and the turn binds it.

What that moves, and what these tests pin: binding is now the moment a file
stops being a draft, and everything that used to be guaranteed by "a session
had to exist" has to be guaranteed by owner scoping and the staging quota
instead.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.api.routes.sessions import (
    _MAX_STAGED_ATTACHMENTS,
    _bind_staged_attachments,
    _enforce_staging_quota,
)
from valuz_agent.infra.database import Base
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.models import SessionAttachmentRow

OWNER = "owner-1"
OTHER = "owner-2"


@pytest.fixture
def db(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "unbound.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[SessionAttachmentRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod,
        "AsyncSessionLocal",
        async_sessionmaker(bind=async_engine, expire_on_commit=False),
    )


async def _stage(user_id: str, name: str, *, session_id: str | None = None) -> str:
    async with async_unit_of_work() as conn:
        row = await SessionDatastore(conn).create_attachment(
            user_id,
            SessionAttachmentRow(
                session_id=session_id,
                filename=name,
                stored_path=f"attachments/x/{name}",
                parse_status="ready",
                size_bytes=1,
                mime_type="text/plain",
                source_kind="local",
            ),
        )
        return str(row.id)


async def _row(attachment_id: str, user_id: str = OWNER) -> SessionAttachmentRow | None:
    async with async_unit_of_work() as conn:
        return await SessionDatastore(conn).get_attachment(user_id, attachment_id)


# ── staging ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_attachment_can_exist_with_no_session(db) -> None:  # type: ignore[no-untyped-def]
    """The whole point: uploading needs nothing from a session, so it does not
    wait for one — and under scoped allocation, waiting for one meant waiting
    for a sandbox."""
    staged = await _stage(OWNER, "a.png")

    row = await _row(staged)
    assert row is not None
    assert row.session_id is None


@pytest.mark.asyncio
async def test_staging_is_per_owner(db) -> None:  # type: ignore[no-untyped-def]
    await _stage(OWNER, "mine.png")
    await _stage(OTHER, "theirs.png")

    async with async_unit_of_work() as conn:
        mine = await SessionDatastore(conn).list_unbound_attachments(OWNER)

    assert [r.filename for r in mine] == ["mine.png"]


@pytest.mark.asyncio
async def test_a_bound_attachment_leaves_the_staging_set(db) -> None:  # type: ignore[no-untyped-def]
    """Otherwise a sent file would sit in the composer forever, and would keep
    counting against the quota that is supposed to bound *drafts*."""
    staged = await _stage(OWNER, "a.png")
    async with async_unit_of_work() as conn:
        await _bind_staged_attachments(conn, OWNER, "sess-1", [staged])

    async with async_unit_of_work() as conn:
        assert await SessionDatastore(conn).list_unbound_attachments(OWNER) == []


# ── binding ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_binding_claims_exactly_what_was_asked_for(db) -> None:  # type: ignore[no-untyped-def]
    """A person may have a project chat and a quick chat open at once. Sending
    in one must not swallow the other's files, which is why the turn names its
    attachments instead of taking everything staged."""
    mine = await _stage(OWNER, "sending.png")
    other_composer = await _stage(OWNER, "still-drafting.png")

    async with async_unit_of_work() as conn:
        await _bind_staged_attachments(conn, OWNER, "sess-1", [mine])

    assert (await _row(mine)).session_id == "sess-1"
    assert (await _row(other_composer)).session_id is None


@pytest.mark.asyncio
async def test_binding_never_moves_a_file_between_conversations(db) -> None:  # type: ignore[no-untyped-def]
    """A resent turn, or a stale composer, must not pull an attachment out of
    the conversation that already showed it."""
    staged = await _stage(OWNER, "a.png")
    async with async_unit_of_work() as conn:
        await _bind_staged_attachments(conn, OWNER, "sess-1", [staged])
    async with async_unit_of_work() as conn:
        await _bind_staged_attachments(conn, OWNER, "sess-2", [staged])

    assert (await _row(staged)).session_id == "sess-1"


@pytest.mark.asyncio
async def test_binding_cannot_reach_another_owners_attachment(db) -> None:  # type: ignore[no-untyped-def]
    """The ids come from a client. Owner scoping is what makes that safe, now
    that "the session exists and is yours" is no longer being checked."""
    theirs = await _stage(OTHER, "theirs.png")

    async with async_unit_of_work() as conn:
        await _bind_staged_attachments(conn, OWNER, "sess-1", [theirs])

    assert (await _row(theirs, OTHER)).session_id is None


@pytest.mark.asyncio
async def test_a_turn_with_no_attachments_binds_nothing(db) -> None:  # type: ignore[no-untyped-def]
    """Most turns are plain text and must not pay for this."""
    staged = await _stage(OWNER, "a.png")

    async with async_unit_of_work() as conn:
        await _bind_staged_attachments(conn, OWNER, "sess-1", None)

    assert (await _row(staged)).session_id is None


# ── the staging quota ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_the_overflow_is_evicted(db) -> None:  # type: ignore[no-untyped-def]
    for i in range(_MAX_STAGED_ATTACHMENTS + 3):
        await _stage(OWNER, f"f{i}.png")

    async with async_unit_of_work() as conn:
        await _enforce_staging_quota(conn, OWNER)
        left = await SessionDatastore(conn).list_unbound_attachments(OWNER)

    assert len(left) == _MAX_STAGED_ATTACHMENTS


@pytest.mark.asyncio
async def test_the_quota_ignores_attachments_a_turn_already_claimed(db) -> None:  # type: ignore[no-untyped-def]
    """It bounds abandoned drafts. A long conversation's own files are not
    drafts, and would otherwise be deleted out from under it."""
    for i in range(_MAX_STAGED_ATTACHMENTS + 5):
        await _stage(OWNER, f"sent{i}.png", session_id="sess-1")

    async with async_unit_of_work() as conn:
        await _enforce_staging_quota(conn, OWNER)
        rows = await SessionDatastore(conn).list_attachments(OWNER, "sess-1", include_consumed=True)

    assert len(rows) == _MAX_STAGED_ATTACHMENTS + 5


@pytest.mark.asyncio
async def test_being_over_quota_never_fails_the_upload(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Housekeeping must not sink the request that triggered it — the cap
    re-asserts on the next upload, but the person's file does not come back."""
    from valuz_agent.api.routes import sessions as routes

    await _stage(OWNER, "a.png")

    def _boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("disk is angry")

    monkeypatch.setattr(routes, "_data_file_path", _boom)

    async with async_unit_of_work() as conn:
        await _enforce_staging_quota(conn, OWNER)  # must not raise

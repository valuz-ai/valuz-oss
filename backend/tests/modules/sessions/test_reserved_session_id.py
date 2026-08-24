"""Uploading an attachment before the session exists.

Attachments upload against a session id, and under scoped allocation creating
the session provisions a sandbox — so requiring the session first made
attaching a file wait on one (~3.6s measured on a cloud deployment) for
something the upload path never touches. A reserved id separates the two.

What that costs, and what these tests pin: the id is no longer proof of
anything, so the shape check and the per-owner cap have to hold on their own.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.api.routes.sessions import (
    _MAX_UNCLAIMED_ATTACHMENTS,
    _evict_unclaimed_attachments,
    _is_session_id_shaped,
)
from valuz_agent.infra.database import Base
from valuz_agent.modules.sessions.datastore import SessionDatastore
from valuz_agent.modules.sessions.models import SessionAttachmentRow
from valuz_agent.modules.sessions.service import mint_session_id

OWNER = "owner-1"


@pytest.fixture
def db(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "reserved.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[SessionAttachmentRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod,
        "AsyncSessionLocal",
        async_sessionmaker(bind=async_engine, expire_on_commit=False),
    )


# ── the id shape ─────────────────────────────────────────────────────────


def test_a_minted_id_is_accepted() -> None:
    assert _is_session_id_shaped(mint_session_id())


def test_a_forked_session_id_is_accepted() -> None:
    """Both spellings of a UUID are live in the sessions table.

    The host mints ``uuid4().hex``; the kernel mints ``str(uuid4())`` on fork
    and whenever the host lets it. Measured on a deployment: 767 of the former,
    24 of the latter. Accepting only the host's would reject a forked session's
    own id — working everywhere except the path nobody tests.
    """
    assert _is_session_id_shaped("24425b09-8a28-48d9-b1fe-df1e803df5ff")


@pytest.mark.parametrize(
    "candidate",
    ["", "../../etc/passwd", "g" * 32, mint_session_id()[:20], mint_session_id().upper()],
)
def test_anything_else_is_refused(candidate: str) -> None:
    """Not authorization — owner scoping does that — but a name that could
    never become a session must not open an attachment directory."""
    assert not _is_session_id_shaped(candidate)


# ── the per-owner cap ────────────────────────────────────────────────────


async def _add(user_id: str, session_id: str, name: str, created_at: int | None = None) -> str:
    from valuz_agent.infra.db import async_unit_of_work

    async with async_unit_of_work() as conn:
        row = await SessionDatastore(conn).create_attachment(
            user_id,
            SessionAttachmentRow(
                session_id=session_id,
                filename=name,
                **({"created_at": created_at} if created_at is not None else {}),
                stored_path=f"attachments/{session_id}/{name}",
                parse_status="ready",
                size_bytes=1,
                mime_type="text/plain",
                source_kind="local",
            ),
        )
        return str(row.id)


class _Sessions:
    """``data_reader`` stand-in: only ``get_session`` matters here."""

    def __init__(self, live: set[str]) -> None:
        self._live = live

    async def get_session(self, user_id: str, session_id: str):  # type: ignore[no-untyped-def]  # noqa: ARG002
        return object() if session_id in self._live else None


def _bind(monkeypatch, live: set[str]) -> None:  # type: ignore[no-untyped-def]
    from valuz_agent.api.routes import sessions as routes

    monkeypatch.setattr(routes, "data_reader", lambda: _Sessions(live))


async def _remaining() -> list[SessionAttachmentRow]:
    from valuz_agent.infra.db import async_unit_of_work

    async with async_unit_of_work() as conn:
        return await SessionDatastore(conn).list_unconsumed_attachments(OWNER)


@pytest.mark.asyncio
async def test_a_claimed_attachment_is_never_evicted(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An attachment whose session exists belongs to it, at any age.

    The cap governs abandoned drafts; a real conversation's files are not
    drafts and must not be counted against it, or a long-lived session would
    lose its own attachments to someone else's abandoned ones.
    """
    from valuz_agent.infra.db import async_unit_of_work

    live = mint_session_id()
    for i in range(_MAX_UNCLAIMED_ATTACHMENTS + 5):
        await _add(OWNER, live, f"f{i}.txt")
    _bind(monkeypatch, {live})

    async with async_unit_of_work() as conn:
        await _evict_unclaimed_attachments(conn, OWNER)

    assert len(await _remaining()) == _MAX_UNCLAIMED_ATTACHMENTS + 5


@pytest.mark.asyncio
async def test_only_the_overflow_goes(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Oldest first, and the newest survivors are exactly the cap."""
    from valuz_agent.infra.db import async_unit_of_work

    # Distinct stamps on purpose: ``created_at`` is milliseconds, so a burst
    # of uploads ties and "oldest" is then whatever the index returns. The
    # tie is acceptable in production (evicting either of two files uploaded
    # in the same millisecond is the same decision); it is not acceptable as
    # the thing this test claims to check.
    for i in range(_MAX_UNCLAIMED_ATTACHMENTS + 3):
        await _add(OWNER, mint_session_id(), f"f{i}.txt", created_at=1_700_000_000_000 + i)
    _bind(monkeypatch, set())

    async with async_unit_of_work() as conn:
        await _evict_unclaimed_attachments(conn, OWNER)

    left = await _remaining()
    assert len(left) == _MAX_UNCLAIMED_ATTACHMENTS
    # f0/f1/f2 were the oldest three.
    assert {r.filename for r in left} == {
        f"f{i}.txt" for i in range(3, _MAX_UNCLAIMED_ATTACHMENTS + 3)
    }


@pytest.mark.asyncio
async def test_another_owners_drafts_do_not_count(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The cap is per owner. Sharing it would let one account's abandoned
    drafts delete another's staged files."""
    from valuz_agent.infra.db import async_unit_of_work

    mine = [await _add(OWNER, mint_session_id(), f"m{i}.txt") for i in range(3)]
    for i in range(_MAX_UNCLAIMED_ATTACHMENTS + 5):
        await _add("owner-2", mint_session_id(), f"t{i}.txt")
    _bind(monkeypatch, set())

    async with async_unit_of_work() as conn:
        await _evict_unclaimed_attachments(conn, OWNER)

    assert {r.id for r in await _remaining()} == set(mine)


@pytest.mark.asyncio
async def test_being_over_quota_never_fails_the_upload(db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Housekeeping must not sink the request that triggered it.

    The cap re-asserts on the next upload; losing this one to a lookup blip
    would trade a bounded disk cost for the person's actual file.
    """
    from valuz_agent.api.routes import sessions as routes
    from valuz_agent.infra.db import async_unit_of_work

    await _add(OWNER, mint_session_id(), "f.txt")

    class _Broken:
        async def get_session(self, *a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("durable is down")

    monkeypatch.setattr(routes, "data_reader", lambda: _Broken())

    async with async_unit_of_work() as conn:
        await _evict_unclaimed_attachments(conn, OWNER)  # must not raise

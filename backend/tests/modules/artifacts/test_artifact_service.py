"""The delivery service, called the way a module other than the MCP tool would.

The point of the extraction is that recording a deliverable does not require an
agent, a session, or a tool call — so these tests use none of them. What they
pin is that the rules a caller inherits by coming through here (owner boundary,
identity matching, content idempotency, head CAS) are the service's and not the
tool's, because a second implementation of any of them would be a second set of
rules for what a deliverable is.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope
from valuz_agent.modules.artifacts.models import (
    ArtifactContentRow,
    ArtifactHeadRow,
    ArtifactKeyRow,
    ArtifactKind,
    ArtifactRevisionRow,
    ArtifactRow,
)
from valuz_agent.modules.artifacts.service import (
    DeliveryRequest,
    DeliveryStatus,
    deliver_artifact,
)

_TABLES = [
    ArtifactRow.__table__,
    ArtifactKeyRow.__table__,
    ArtifactHeadRow.__table__,
    ArtifactRevisionRow.__table__,
    ArtifactContentRow.__table__,
]

SCOPE = Scope(user_id="u1", project_id="p1")


@pytest.fixture
def session_factory(tmp_path):  # type: ignore[no-untyped-def]
    db_file = tmp_path / "artifacts.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=_TABLES)
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


@pytest.fixture
def cwd(tmp_path):  # type: ignore[no-untyped-def]
    """The delivery's working directory. ``tmp_path`` stays outside it."""
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    return workdir


async def _deliver(session_factory, cwd, path: Path, **kwargs):  # type: ignore[no-untyped-def]
    """One delivery, with no ExecContext, no session and no tool anywhere."""
    async with session_factory() as db:
        result = await deliver_artifact(
            db,
            scope=SCOPE,
            scope_cwd=cwd,
            owner_roots=[cwd.resolve()],
            request=DeliveryRequest(abs_path=path, **kwargs),
        )
        await db.commit()
        return result


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


async def test_a_module_can_record_a_deliverable_without_an_agent(session_factory, cwd):  # type: ignore[no-untyped-def]
    result = await _deliver(
        session_factory,
        cwd,
        _write(cwd / "chart.html", "<html/>"),
        display_name="Q3 chart",
        kind=ArtifactKind.UI,
    )

    assert result.status is DeliveryStatus.RECORDED
    assert result.ok
    assert result.version_no == 1
    stored = Path(result.abs_path or "")
    assert ".artifact" in stored.parts
    assert stored.read_text(encoding="utf-8") == "<html/>"

    async with session_factory() as db:
        artifact = await ArtifactDatastore(db).get_artifact("u1", result.artifact_id or "")
    assert artifact is not None
    assert artifact.display_name == "Q3 chart"
    assert artifact.kind == ArtifactKind.UI


async def test_versioning_applies_to_every_caller(session_factory, cwd):  # type: ignore[no-untyped-def]
    """A module delivering twice gets a version, not an overwrite — the same
    rule the agent gets, because it is the same code."""
    src = _write(cwd / "chart.html", "v1")
    first = await _deliver(session_factory, cwd, src)
    src.write_text("v2", encoding="utf-8")
    second = await _deliver(session_factory, cwd, src)

    assert first.artifact_id == second.artifact_id
    assert (first.version_no, second.version_no) == (1, 2)
    assert Path(first.abs_path or "").read_text(encoding="utf-8") == "v1"


async def test_unchanged_content_is_not_a_new_version(session_factory, cwd):  # type: ignore[no-untyped-def]
    src = _write(cwd / "chart.html", "same")
    first = await _deliver(session_factory, cwd, src)
    again = await _deliver(session_factory, cwd, src)

    assert again.status is DeliveryStatus.UNCHANGED
    assert again.ok  # a no-op is a success, not a failure
    assert again.revision_id == first.revision_id


async def test_the_owner_boundary_is_the_services_not_the_tools(session_factory, cwd, tmp_path):  # type: ignore[no-untyped-def]
    """An internal caller cannot skip the check by not being the MCP tool."""
    intruder = _write(tmp_path / "elsewhere" / "secret.md", "not yours")

    result = await _deliver(session_factory, cwd, intruder)

    assert result.status is DeliveryStatus.NOT_OWNED
    assert not (cwd / ".artifact").exists()


async def test_a_missing_file_is_reported_not_raised(session_factory, cwd):  # type: ignore[no-untyped-def]
    """Callers handling several files need each outcome, not an exception that
    ends the batch on the first bad entry."""
    result = await _deliver(session_factory, cwd, cwd / "nope.md")

    assert result.status is DeliveryStatus.NOT_FOUND
    assert not result.ok


async def test_contradictory_options_are_rejected(session_factory, cwd):  # type: ignore[no-untyped-def]
    result = await _deliver(
        session_factory,
        cwd,
        _write(cwd / "a.md", "x"),
        artifact_id="SOMETHING",
        as_new_artifact=True,
    )

    assert result.status is DeliveryStatus.INVALID
    assert result.detail  # says which inputs disagree


async def test_kind_defaults_to_file_rather_than_being_guessed(session_factory, cwd):  # type: ignore[no-untyped-def]
    """Same rule as the tool: an extension does not say what something is for."""
    result = await _deliver(session_factory, cwd, _write(cwd / "deck.pptx", "x"))

    async with session_factory() as db:
        artifact = await ArtifactDatastore(db).get_artifact("u1", result.artifact_id or "")
    assert artifact is not None and artifact.kind == ArtifactKind.FILE


async def test_the_caller_owns_the_transaction(session_factory, cwd):  # type: ignore[no-untyped-def]
    """The service does not commit, so a batch can be all-or-nothing.

    A caller that rolls back must leave no artifact behind — otherwise "one
    transaction for the batch" would not hold for anyone but the MCP tool.
    """
    src = _write(cwd / "chart.html", "v1")
    async with session_factory() as db:
        result = await deliver_artifact(
            db,
            scope=SCOPE,
            scope_cwd=cwd,
            owner_roots=[cwd.resolve()],
            request=DeliveryRequest(abs_path=src),
        )
        assert result.status is DeliveryStatus.RECORDED
        await db.rollback()

    async with session_factory() as db:
        assert await ArtifactDatastore(db).count_scope_artifacts(SCOPE) == 0


async def test_a_lost_race_is_retried_rather_than_reported(session_factory, cwd, monkeypatch):  # type: ignore[no-untyped-def]
    """Losing the head is ordinary, not something a caller should handle.

    A runtime can emit several tool_use blocks in one turn, so two deliveries to
    the same deliverable race routinely. Surfacing that to a model as an error
    it must diagnose and retry — for contention the server can absorb in one
    re-read — would be pushing our concurrency control into the prompt.
    """
    from valuz_agent.modules.artifacts import datastore as ds_mod

    src = _write(cwd / "chart.html", "v1")
    await _deliver(session_factory, cwd, src)

    real = ds_mod.ArtifactDatastore.append_revision
    calls = {"n": 0}

    async def _lose_once(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # somebody else moved the head first
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(ds_mod.ArtifactDatastore, "append_revision", _lose_once)

    src.write_text("v2", encoding="utf-8")
    result = await _deliver(session_factory, cwd, src)

    assert calls["n"] == 2  # it tried again
    assert result.status is DeliveryStatus.RECORDED
    assert result.version_no == 2


async def test_sustained_contention_still_reports_stale_head(session_factory, cwd, monkeypatch):  # type: ignore[no-untyped-def]
    """One retry, not a spin: losing twice means something else is wrong."""
    from valuz_agent.modules.artifacts import datastore as ds_mod

    src = _write(cwd / "chart.html", "v1")
    await _deliver(session_factory, cwd, src)

    calls = {"n": 0}

    async def _always_lose(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return None

    monkeypatch.setattr(ds_mod.ArtifactDatastore, "append_revision", _always_lose)

    src.write_text("v2", encoding="utf-8")
    result = await _deliver(session_factory, cwd, src)

    assert calls["n"] == 2
    assert result.status is DeliveryStatus.STALE_HEAD


async def test_a_racing_duplicate_becomes_unchanged_not_a_new_version(
    session_factory, cwd, monkeypatch
):  # type: ignore[no-untyped-def]
    """If the delivery that beat us recorded these very bytes, there is nothing
    to add — the retry must notice that rather than mint a version."""
    from valuz_agent.modules.artifacts import datastore as ds_mod

    src = _write(cwd / "chart.html", "v1")
    await _deliver(session_factory, cwd, src)

    real = ds_mod.ArtifactDatastore.append_revision
    winner: dict[str, str] = {}
    calls = {"n": 0}

    async def _somebody_else_gets_there_first(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            # Another delivery of the SAME new bytes lands, and then ours is
            # refused — exactly what two tool_use blocks in one turn produce.
            other = await real(self, *args, **kwargs)
            winner["id"] = other.id
            return None
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(
        ds_mod.ArtifactDatastore, "append_revision", _somebody_else_gets_there_first
    )

    src.write_text("v2", encoding="utf-8")
    result = await _deliver(session_factory, cwd, src)

    assert calls["n"] == 1  # the retry never reached the append
    assert result.status is DeliveryStatus.UNCHANGED
    assert result.version_no == 2
    assert result.revision_id == winner["id"]


async def test_returning_to_earlier_bytes_is_a_new_version(session_factory, cwd):  # type: ignore[no-untyped-def]
    """Only the head decides what a replay is.

    Delivering content that appears further back in the history means the
    caller is RETURNING to it, which is the next generation and not a no-op. A
    uniqueness rule over the whole history would make that unrecordable —
    nothing issues a revert yet, and this is what keeps one possible.
    """
    src = _write(cwd / "report.md", "A")
    await _deliver(session_factory, cwd, src)
    src.write_text("B", encoding="utf-8")
    await _deliver(session_factory, cwd, src)

    src.write_text("A", encoding="utf-8")  # back to what v1 held
    back = await _deliver(session_factory, cwd, src)

    assert back.status is DeliveryStatus.RECORDED
    assert back.version_no == 3
    assert Path(back.abs_path or "").read_text(encoding="utf-8") == "A"


async def test_the_type_follows_the_name_not_the_bytes(session_factory, cwd):  # type: ignore[no-untyped-def]
    """Two deliverables holding identical content share a content row, so a
    type stored there would be read back by a deliverable that never had that
    name."""
    page = _write(cwd / "page.html", "same")
    notes = _write(cwd / "notes.txt", "same")

    first = await _deliver(session_factory, cwd, page)
    second = await _deliver(session_factory, cwd, notes)

    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        a = await ds.get_revision("u1", first.revision_id or "")
        b = await ds.get_revision("u1", second.revision_id or "")

    assert a is not None and b is not None
    assert a.content_id == b.content_id  # the sharing this guards against
    assert a.mime_type == "text/html"
    assert b.mime_type == "text/plain"


async def test_a_lost_race_publishes_nothing(session_factory, cwd, monkeypatch):  # type: ignore[no-untyped-def]
    """Two deliveries racing compute the SAME version number, so both target
    one path. Only the one that wins the head may publish — otherwise the
    loser's bytes end up under the winner's row."""
    from valuz_agent.modules.artifacts import datastore as ds_mod

    src = _write(cwd / "chart.html", "v1")
    first = await _deliver(session_factory, cwd, src)
    version_dir = Path(first.abs_path or "").parent.parent / "v2"

    async def _always_lose(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(ds_mod.ArtifactDatastore, "append_revision", _always_lose)

    src.write_text("v2", encoding="utf-8")
    result = await _deliver(session_factory, cwd, src)

    assert result.status is DeliveryStatus.STALE_HEAD
    # Neither the snapshot nor the staging copy it was built in.
    assert not (version_dir / "chart.html").exists()
    assert list(version_dir.glob("*")) == []


async def test_an_unrecordable_snapshot_does_not_pass_as_delivered(  # type: ignore[no-untyped-def]
    session_factory, cwd, monkeypatch
):
    """If publishing fails after the head moved, the generation stays on record
    but is marked missing — so the panel does not offer a broken link, and a
    retry is not mistaken for a replay of it."""
    from valuz_agent.modules.artifacts import snapshot as snap_mod

    src = _write(cwd / "chart.html", "v1")
    await _deliver(session_factory, cwd, src)

    def _boom(staged):  # type: ignore[no-untyped-def]
        raise OSError("mount went away")

    monkeypatch.setattr(snap_mod, "promote_snapshot", _boom)

    src.write_text("v2", encoding="utf-8")
    failed = await _deliver(session_factory, cwd, src)
    assert failed.status is DeliveryStatus.SNAPSHOT_FAILED
    assert not failed.ok

    monkeypatch.undo()
    retried = await _deliver(session_factory, cwd, src)

    assert retried.status is DeliveryStatus.RECORDED  # not "unchanged"
    assert Path(retried.abs_path or "").read_text(encoding="utf-8") == "v2"

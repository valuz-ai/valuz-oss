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
    first = await _deliver(session_factory, cwd, src)

    real = ds_mod.ArtifactDatastore.find_revision_by_content
    calls = {"n": 0}

    async def _appears_on_retry(self, user_id, artifact_id, content_hash):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # not there when we looked
        return await real(self, user_id, artifact_id, content_hash)

    monkeypatch.setattr(ds_mod.ArtifactDatastore, "find_revision_by_content", _appears_on_retry)
    monkeypatch.setattr(ds_mod.ArtifactDatastore, "append_revision", lambda *a, **k: _none())

    async def _none():  # type: ignore[no-untyped-def]
        return None

    result = await _deliver(session_factory, cwd, src)

    assert result.status is DeliveryStatus.UNCHANGED
    assert result.revision_id == first.revision_id

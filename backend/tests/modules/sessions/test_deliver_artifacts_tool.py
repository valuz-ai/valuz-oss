"""``deliver_artifacts`` handler — the versioned delivery path.

The handler runs on the host toolkit MCP path: the kernel ``ExecContext`` carries
the calling ``session_id`` and the MCP wrapper passes the session owner
explicitly. It resolves the session's working directory, checks the
model-supplied path against the owner boundary, snapshots the bytes into
``.artifact/`` and records a generation.

Two collaborators are pinned per test because they need a live session and the
managed project root: ``resolve_delivery_scope`` and ``owner_allowed_roots``.
Everything below the handler — identity, idempotency, head CAS, the snapshot
itself — is real.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede `from src.*`
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import valuz_agent.boot.kernel  # noqa: F401 — puts kernel src/ on sys.path
from valuz_agent.integrations.toolkit_mcp_server import HostExecContext

from valuz_agent.infra.database import Base
from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope
from valuz_agent.modules.artifacts.models import (
    ArtifactKind,
    ArtifactContentRow,
    ArtifactHeadRow,
    ArtifactKeyRow,
    ArtifactRevisionRow,
    ArtifactRow,
)
from valuz_agent.modules.artifacts.scope import DeliveryScope, ScopeUnavailableError
from valuz_agent.modules.sessions import artifacts_tool as tool_mod
from valuz_agent.modules.sessions.artifacts_tool import _deliver_artifacts_handler

_TABLES = [
    ArtifactRow.__table__,
    ArtifactKeyRow.__table__,
    ArtifactHeadRow.__table__,
    ArtifactRevisionRow.__table__,
    ArtifactContentRow.__table__,
]


@pytest.fixture
def session_factory(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "artifacts.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=_TABLES)
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", factory)
    return factory


@pytest.fixture
def cwd(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """The session's working directory, wired as both scope cwd and owner root.

    ``tmp_path`` itself stays deliberately OUTSIDE it, so every test has an
    out-of-bounds path on hand without inventing one.
    """
    workdir = tmp_path / "workspace"
    workdir.mkdir()

    async def _scope(user_id: str, session_id: str) -> DeliveryScope:
        return DeliveryScope(scope=Scope(user_id=user_id, project_id="p1"), cwd=workdir)

    async def _roots(user_id: str) -> list[Path]:
        return [workdir.resolve()]

    monkeypatch.setattr(tool_mod, "resolve_delivery_scope", _scope)
    monkeypatch.setattr(tool_mod, "owner_allowed_roots", _roots)
    return workdir


async def _deliver(*entries, session_id: str = "s1"):  # type: ignore[no-untyped-def]
    result = await _deliver_artifacts_handler(
        {"attachments": list(entries)},
        HostExecContext(session_id=session_id, user_id="u1"),
    )
    payload = json.loads(result.content) if result.content.startswith("{") else None
    return result, payload


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── Recording a deliverable ───────────────────────────────────────────────────


async def test_records_a_snapshot_not_a_reference(session_factory, cwd):  # type: ignore[no-untyped-def]
    """The delivered path and the recorded path are different files.

    This is the point of the change: editing the working copy afterwards must
    not alter what was already delivered.
    """
    src = _write(cwd / "report.md", "v1")

    result, payload = await _deliver({"filePath": str(src)})

    assert not result.is_error
    (entry,) = payload["results"]
    assert entry["status"] == "recorded"
    assert entry["versionNo"] == 1
    stored = Path(entry["absPath"])
    assert stored != src
    assert ".artifact" in stored.parts
    assert stored.read_text(encoding="utf-8") == "v1"

    src.write_text("edited after delivery", encoding="utf-8")
    assert stored.read_text(encoding="utf-8") == "v1"


async def test_returns_an_absolute_path_for_linking(session_factory, cwd):  # type: ignore[no-untyped-def]
    """``absPath`` is absolute — it is what a ``valuz-file://`` link carries."""
    _, payload = await _deliver({"filePath": str(_write(cwd / "report.md", "v1"))})

    assert Path(payload["results"][0]["absPath"]).is_absolute()


async def test_redelivery_appends_a_version(session_factory, cwd):  # type: ignore[no-untyped-def]
    src = _write(cwd / "report.md", "v1")
    _, first = await _deliver({"filePath": str(src)})
    src.write_text("v2", encoding="utf-8")
    _, second = await _deliver({"filePath": str(src)})

    a, b = first["results"][0], second["results"][0]
    assert a["artifactId"] == b["artifactId"]
    assert (a["versionNo"], b["versionNo"]) == (1, 2)
    assert b["isNewVersion"] is True
    # Both versions readable, side by side.
    assert Path(a["absPath"]).read_text(encoding="utf-8") == "v1"
    assert Path(b["absPath"]).read_text(encoding="utf-8") == "v2"


async def test_unchanged_content_is_a_no_op(session_factory, cwd):  # type: ignore[no-untyped-def]
    """Covers a replayed tool call and an agent redelivering what it never touched."""
    src = _write(cwd / "report.md", "same")
    _, first = await _deliver({"filePath": str(src)})
    _, again = await _deliver({"filePath": str(src)})

    a, b = first["results"][0], again["results"][0]
    assert b["status"] == "unchanged"
    assert b["revisionId"] == a["revisionId"]
    assert b["versionNo"] == 1


async def test_continues_across_sessions(session_factory, cwd):  # type: ignore[no-untyped-def]
    src = _write(cwd / "report.md", "v1")
    _, first = await _deliver({"filePath": str(src)}, session_id="s1")
    src.write_text("v2", encoding="utf-8")
    _, second = await _deliver({"filePath": str(src)}, session_id="s2")

    assert first["results"][0]["artifactId"] == second["results"][0]["artifactId"]
    assert second["results"][0]["versionNo"] == 2


async def test_as_new_artifact_forces_a_separate_deliverable(session_factory, cwd):  # type: ignore[no-untyped-def]
    src = _write(cwd / "report.md", "v1")
    _, first = await _deliver({"filePath": str(src)})
    src.write_text("a different deliverable that happens to reuse the name", encoding="utf-8")
    _, second = await _deliver({"filePath": str(src), "asNewArtifact": True})

    assert first["results"][0]["artifactId"] != second["results"][0]["artifactId"]
    assert second["results"][0]["versionNo"] == 1


async def test_kind_comes_from_the_caller(session_factory, cwd):  # type: ignore[no-untyped-def]
    """The agent says what a deliverable is; the server does not guess."""
    _, payload = await _deliver(
        {"filePath": str(_write(cwd / "deck.pptx", "x")), "kind": "presentation"}
    )

    async with session_factory() as db:
        artifact = await ArtifactDatastore(db).get_artifact(
            "u1", payload["results"][0]["artifactId"]
        )

    assert artifact is not None
    assert artifact.display_name == "deck.pptx"
    assert artifact.kind == ArtifactKind.PRESENTATION


async def test_kind_is_never_inferred_from_the_extension(session_factory, cwd):  # type: ignore[no-untyped-def]
    """A telling extension must NOT produce a kind on its own.

    An extension says how a file is encoded, not what it is for: the same
    ``.html`` is a report, a tool, or a chart. Guessing puts a confident-looking
    label on something the agent could simply have named, and it would flip on a
    md -> pdf export of a product whose kind never changed.
    """
    _, payload = await _deliver({"filePath": str(_write(cwd / "deck.pptx", "x"))})

    async with session_factory() as db:
        artifact = await ArtifactDatastore(db).get_artifact(
            "u1", payload["results"][0]["artifactId"]
        )

    assert artifact is not None
    assert artifact.kind == ArtifactKind.FILE


async def test_an_unknown_kind_falls_back_rather_than_failing(session_factory, cwd):  # type: ignore[no-untyped-def]
    """A bad label is not worth losing a delivery over."""
    _, payload = await _deliver({"filePath": str(_write(cwd / "a.md", "x")), "kind": "chart"})

    assert payload["results"][0]["status"] == "recorded"
    async with session_factory() as db:
        artifact = await ArtifactDatastore(db).get_artifact(
            "u1", payload["results"][0]["artifactId"]
        )
    assert artifact is not None and artifact.kind == ArtifactKind.FILE


def test_the_schema_asks_only_for_what_the_caller_can_know() -> None:
    """Nothing derivable from the file itself is a parameter.

    Size, mime and hash are properties of the bytes, which the server is holding;
    a parameter for them invites a guess that gets thrown away. What the caller
    knows and the server cannot — where it wrote, what to call it, what it is —
    stays.
    """
    from valuz_agent.modules.sessions.artifacts_tool import _PARAMS

    props = _PARAMS["properties"]["attachments"]["items"]["properties"]
    assert set(props) == {
        "filePath",
        "fileName",
        "kind",
        "mimeType",
        "artifactId",
        "asNewArtifact",
    }
    assert "fileSize" not in props


def test_the_tool_schema_offers_every_kind() -> None:
    """The schema is rendered from the enum, so a new family cannot be added
    without the model being told about it."""
    from valuz_agent.modules.sessions.artifacts_tool import _PARAMS

    schema = _PARAMS["properties"]["attachments"]["items"]["properties"]["kind"]
    assert schema["enum"] == [k.value for k in ArtifactKind]
    for kind in ArtifactKind:
        assert f"'{kind.value}'" in schema["description"]


async def test_explicit_file_name_becomes_the_display_name(session_factory, cwd):  # type: ignore[no-untyped-def]
    _, payload = await _deliver(
        {"filePath": str(_write(cwd / "out.md", "x")), "fileName": "Quarterly Report"}
    )

    assert Path(payload["results"][0]["absPath"]).name == "Quarterly Report"


async def test_size_and_hash_come_from_the_bytes(session_factory, cwd):  # type: ignore[no-untyped-def]
    """Measured from the file, and there is no parameter to claim otherwise.

    ``fileSize`` used to be in the schema and read by nothing. The model filled
    it in anyway — with ``0`` — so it was a field that taught the model to guess
    a value it cannot know and that would be discarded regardless.
    """
    src = _write(cwd / "report.md", "exactly eleven")

    _, payload = await _deliver({"filePath": str(src)})

    async with session_factory() as db:
        revision = await ArtifactDatastore(db).get_revision(
            "u1", payload["results"][0]["revisionId"]
        )
        assert revision is not None
        content = await ArtifactDatastore(db).find_content_by_hash("u1", revision.content_hash)

    assert content is not None
    assert content.byte_size == len("exactly eleven")
    assert content.content_hash.startswith("sha256:")


# ── Boundaries ────────────────────────────────────────────────────────────────


async def test_rejects_path_outside_owner_roots(session_factory, cwd, tmp_path):  # type: ignore[no-untyped-def]
    """Another tenant's absolute path must not become this owner's deliverable."""
    intruder = _write(tmp_path / "other_tenant" / "secret.pdf", "not yours")

    result, payload = await _deliver({"filePath": str(intruder)})

    assert result.is_error
    assert payload["results"][0]["status"] == "not_owned"
    # Nothing was copied — the refusal happens before any read.
    assert not (cwd / ".artifact").exists()


async def test_rejects_symlink_escaping_owner_roots(session_factory, cwd, tmp_path):  # type: ignore[no-untyped-def]
    """A link inside the workspace pointing out of it is still out of bounds."""
    outside = _write(tmp_path / "outside.pdf", "not yours")
    link = cwd / "innocent.pdf"
    link.symlink_to(outside)

    result, payload = await _deliver({"filePath": str(link)})

    assert result.is_error
    assert payload["results"][0]["status"] == "not_owned"


async def test_out_of_bounds_path_is_not_an_existence_oracle(session_factory, cwd, tmp_path):  # type: ignore[no-untyped-def]
    """Existing and non-existing out-of-bounds paths are indistinguishable.

    The boundary check runs before the ``isfile`` probe, so both report the same
    status — otherwise the difference would leak whether another tenant holds
    that path.
    """
    real = _write(tmp_path / "elsewhere_real.pdf", "x")
    ghost = tmp_path / "elsewhere_ghost.pdf"

    statuses = []
    for candidate in (real, ghost):
        _, payload = await _deliver({"filePath": str(candidate)})
        statuses.append(payload["results"][0]["status"])

    assert statuses[0] == statuses[1] == "not_owned"


async def test_refuses_to_deliver_out_of_the_artifact_store(session_factory, cwd):  # type: ignore[no-untyped-def]
    """Re-delivering a snapshot would record a version whose content is a version."""
    _, payload = await _deliver({"filePath": str(_write(cwd / "report.md", "v1"))})

    result, again = await _deliver({"filePath": payload["results"][0]["absPath"]})

    assert result.is_error
    assert again["results"][0]["status"] == "in_artifact_store"


async def test_owned_but_out_of_scope_path_is_reported_distinctly(
    session_factory, cwd, tmp_path, monkeypatch
):  # type: ignore[no-untyped-def]
    """Inside the owner's roots, but belonging to another project or worktree.

    Identity is scope-relative, so there is no key to file this under — and the
    model needs to hear something other than "not yours", which would be wrong.
    """
    sibling = _write(tmp_path / "other_project" / "report.md", "x")

    async def _roots(user_id: str) -> list[Path]:
        return [tmp_path.resolve()]  # owns both directories

    monkeypatch.setattr(tool_mod, "owner_allowed_roots", _roots)

    result, payload = await _deliver({"filePath": str(sibling)})

    assert result.is_error
    assert payload["results"][0]["status"] == "not_in_scope"


async def test_missing_file_is_reported(session_factory, cwd):  # type: ignore[no-untyped-def]
    result, payload = await _deliver({"filePath": str(cwd / "nope.txt")})

    assert result.is_error
    assert payload["results"][0]["status"] == "not_found"


async def test_partial_batch_records_the_good_entries(session_factory, cwd, tmp_path):  # type: ignore[no-untyped-def]
    """One bad path must not sink the legitimate deliveries beside it."""
    ok = _write(cwd / "mine.md", "mine")
    intruder = _write(tmp_path / "theirs.md", "theirs")

    result, payload = await _deliver({"filePath": str(ok)}, {"filePath": str(intruder)})

    assert not result.is_error  # at least one recorded
    assert payload["delivered_count"] == 1
    assert {r["status"] for r in payload["results"]} == {"recorded", "not_owned"}


# ── Preconditions ─────────────────────────────────────────────────────────────


async def test_unresolvable_scope_fails_the_whole_call(session_factory, monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """A removed worktree must not fall back to the project cwd.

    Falling back would resolve the same relative path against a different root,
    splitting one deliverable's history across two directories.
    """

    async def _boom(user_id: str, session_id: str) -> DeliveryScope:
        raise ScopeUnavailableError("the worktree 'feat-x' this session runs in no longer exists")

    monkeypatch.setattr(tool_mod, "resolve_delivery_scope", _boom)

    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(tmp_path / "x.md")}]},
        HostExecContext(session_id="s1", user_id="u1"),
    )

    assert result.is_error
    assert "worktree" in result.content


async def test_unresolvable_workspace_root_fails_closed(session_factory, cwd, monkeypatch):  # type: ignore[no-untyped-def]
    """No resolvable root → refuse the call with its own message.

    Reporting every entry as "outside your workspace" would send the model
    chasing paths that were never the problem.
    """
    src = _write(cwd / "report.md", "v1")

    async def _no_roots(user_id: str) -> list[Path]:
        return []

    monkeypatch.setattr(tool_mod, "owner_allowed_roots", _no_roots)

    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(src)}]},
        HostExecContext(session_id="s1", user_id="u1"),
    )

    assert result.is_error
    assert "workspace root" in result.content


async def test_empty_attachments_errors() -> None:
    result = await _deliver_artifacts_handler(
        {"attachments": []}, HostExecContext(session_id="s1", user_id="u1")
    )
    assert result.is_error


async def test_no_session_id_errors(cwd) -> None:  # type: ignore[no-untyped-def]
    result = await _deliver_artifacts_handler(
        {"attachments": [{"filePath": str(_write(cwd / "x.txt", "x"))}]},
        HostExecContext(user_id="u1"),
    )
    assert result.is_error
    assert "session" in result.content.lower()


# ── Renaming a deliverable ────────────────────────────────────────────────────


async def test_a_rename_continues_the_deliverable_when_named(session_factory, cwd):  # type: ignore[no-untyped-def]
    """Renaming a file must not split its history in two.

    Neither key can recognise this on its own — the path changed and so did the
    name — so the caller says which deliverable it is. Only it knows.
    """
    first = _write(cwd / "brief.md", "v1")
    _, before = await _deliver({"filePath": str(first)})
    artifact_id = before["results"][0]["artifactId"]

    # What an agent actually does to rename: write the new name, drop the old.
    renamed = _write(cwd / "早报.md", "v2")
    first.unlink()
    _, after = await _deliver({"filePath": str(renamed), "artifactId": artifact_id})

    entry = after["results"][0]
    assert entry["artifactId"] == artifact_id
    assert entry["versionNo"] == 2
    # Both versions still readable, each at its own snapshot.
    assert Path(before["results"][0]["absPath"]).read_text(encoding="utf-8") == "v1"
    assert Path(entry["absPath"]).read_text(encoding="utf-8") == "v2"


async def test_after_a_rename_the_new_path_finds_it_without_the_id(session_factory, cwd):  # type: ignore[no-untyped-def]
    """The deliverable follows the file: the next delivery needs no id."""
    _, before = await _deliver({"filePath": str(_write(cwd / "brief.md", "v1"))})
    artifact_id = before["results"][0]["artifactId"]

    renamed = _write(cwd / "早报.md", "v2")
    await _deliver({"filePath": str(renamed), "artifactId": artifact_id})
    renamed.write_text("v3", encoding="utf-8")
    _, third = await _deliver({"filePath": str(renamed)})

    assert third["results"][0]["artifactId"] == artifact_id
    assert third["results"][0]["versionNo"] == 3


async def test_a_rename_updates_the_display_name(session_factory, cwd):  # type: ignore[no-untyped-def]
    _, before = await _deliver({"filePath": str(_write(cwd / "brief.md", "v1"))})
    artifact_id = before["results"][0]["artifactId"]

    await _deliver({"filePath": str(_write(cwd / "早报.md", "v2")), "artifactId": artifact_id})

    async with session_factory() as db:
        artifact = await ArtifactDatastore(db).get_artifact("u1", artifact_id)
    assert artifact is not None and artifact.display_name == "早报.md"


async def test_an_artifact_id_from_another_scope_is_refused(session_factory, cwd):  # type: ignore[no-untyped-def]
    """An id is a bare string; resolving it unscoped would let a delivery append
    a version to somebody else's deliverable."""
    async with session_factory() as db:
        ds = ArtifactDatastore(db)
        theirs = await ds.create_artifact(
            Scope(user_id="u2", project_id="p1"),
            kind="document",
            display_name="theirs.md",
            rel_path="theirs.md",
        )
        await db.commit()

    result, payload = await _deliver(
        {"filePath": str(_write(cwd / "mine.md", "x")), "artifactId": theirs.id}
    )

    assert result.is_error
    assert payload["results"][0]["status"] == "unknown_artifact"


async def test_an_unknown_artifact_id_is_refused_rather_than_guessed(session_factory, cwd):  # type: ignore[no-untyped-def]
    """The caller asked for something specific; quietly doing something else
    would hide the mistake and fork anyway."""
    result, payload = await _deliver(
        {"filePath": str(_write(cwd / "a.md", "x")), "artifactId": "NOSUCH00"}
    )

    assert result.is_error
    assert payload["results"][0]["status"] == "unknown_artifact"


async def test_the_two_escape_hatches_cannot_both_be_set(session_factory, cwd):  # type: ignore[no-untyped-def]
    """They say opposite things — continue this one, versus start a new one."""
    _, payload = await _deliver(
        {
            "filePath": str(_write(cwd / "a.md", "x")),
            "artifactId": "SOMETHING",
            "asNewArtifact": True,
        }
    )

    assert payload["results"][0]["status"] == "invalid"

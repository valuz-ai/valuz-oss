"""``skill.submit`` as an operation: propose → confirm / cancel, durably.

Runs the real ``OperationService`` over the real skill library service on a
temp-file SQLite (the operation, skill index and artifact tables all live on
one ``Base``), with the kernel session lookup and the staging resolver
monkeypatched to temp dirs — so the handler executes inside the operation's
savepoint exactly as in production, commits deferred.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import valuz_agent.boot.kernel  # noqa: F401 — kernel ``src`` on sys.path for HostExecContext
from valuz_agent.infra.config import settings
from valuz_agent.infra.database import Base
from valuz_agent.modules.artifacts import models as _artifact_models  # noqa: F401
from valuz_agent.modules.operations import models as _operation_models  # noqa: F401
from valuz_agent.modules.operations.service import OperationService
from valuz_agent.modules.skills import operations as skill_ops
from valuz_agent.modules.skills import staging as staging_mod
from valuz_agent.modules.skills.datastore import SkillDatastore

USER = "u-submit"
SESSION = "sess-submit"


class _Projects:
    async def list(self, user_id: str, *, kind: str | None = None):  # type: ignore[no-untyped-def]
        return []

    async def get(self, user_id: str, project_id: str):  # type: ignore[no-untyped-def]
        return None


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    data_dir = tmp_path / "data"
    library = tmp_path / "library"
    staging = tmp_path / "staging"
    for d in (data_dir, library, staging):
        d.mkdir()
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "user_skills_dir", library)

    async def _staging_dir(user_id: str, session_id: str, *, mkdir: bool = False) -> Path:
        return staging

    monkeypatch.setattr(staging_mod, "staging_dir_for_session", _staging_dir)

    from valuz_agent.adapters import kernel_client

    async def _get_session(user_id: str, session_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            user_id=user_id,
            metadata={
                "valuz": {"project_id": "chat-default", "creation_context": {"kind": "chat"}}
            },
        )

    monkeypatch.setattr(kernel_client, "get_session", _get_session)

    # The handler builds a real ProjectService; give its ``_resolve_project``
    # a chat project without a projects table full of rows.
    from valuz_agent.modules.skills.service import SkillLibraryService

    async def _resolve_project(self, user_id: str, project_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(id=project_id, kind="chat", root_path=None, name=project_id)

    monkeypatch.setattr(SkillLibraryService, "_resolve_project", _resolve_project)

    async def _list_projects(self, user_id: str):  # type: ignore[no-untyped-def]
        return [SimpleNamespace(id="chat-default", kind="chat", root_path=None, name="chat")]

    async def _get_project(self, user_id: str, project_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(id=project_id, kind="chat", root_path=None, name=project_id)

    from valuz_agent.modules.projects.service import ProjectService

    monkeypatch.setattr(ProjectService, "list_projects", _list_projects)
    monkeypatch.setattr(ProjectService, "get_project", _get_project)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _uow(commit: bool = True):  # type: ignore[no-untyped-def]
        async with factory() as db:
            yield db
            if commit:
                await db.commit()

    yield SimpleNamespace(
        factory=factory,
        uow=_uow,
        library=library,
        staging=staging,
        data_dir=data_dir,
        projects=_Projects(),
    )
    await engine.dispose()


def _stage(staging: Path, slug: str, body: str) -> Path:
    d = staging / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: demo {slug}\n---\n\n{body}\n", encoding="utf-8"
    )
    return d


async def _propose(env, slug: str, **kw):  # type: ignore[no-untyped-def]
    async with env.uow() as db:
        row = await skill_ops.propose_skill_submission(
            db,
            USER,
            SESSION,
            slug,
            summary=kw.get("summary", "made it"),
            change_kind=kw.get("change_kind", "create"),
            files_touched=["SKILL.md"],
        )
        return row.id, row.proposal_hash, dict(row.preview), row.state


async def _confirm(env, op_id: str, digest: str, decision: dict | None = None):  # type: ignore[no-untyped-def]
    async with env.uow() as db:
        row = await OperationService(db, env.projects).confirm(
            USER, op_id, expected_proposal_hash=digest, decision=decision
        )
        return row.state, row.error_code, row.error_message, dict(row.result_payload)


async def _cancel(env, op_id: str, digest: str):  # type: ignore[no-untyped-def]
    async with env.uow() as db:
        row = await OperationService(db, env.projects).cancel(
            USER, op_id, expected_proposal_hash=digest
        )
        return row.state


async def _versions(env, slug: str) -> list[int]:  # type: ignore[no-untyped-def]
    from valuz_agent.modules.artifacts.service import list_artifact_revisions

    async with env.uow(commit=False) as db:
        row = await SkillDatastore(db).get_by_source_path(USER, str(env.library / slug))
        if row is None or not row.artifact_id:
            return []
        revisions, _ = await list_artifact_revisions(db, USER, row.artifact_id)
        return [r.version_no for r in revisions]


async def test_propose_records_the_draft_and_is_idempotent(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    op_id, digest, preview, state = await _propose(env, "demo")
    assert state == "awaiting_confirmation"
    assert preview["conflict_kind"] == skill_ops.CONFLICT_NONE
    assert preview["next_version"] == 1
    assert [f["path"] for f in preview["files"]] == ["SKILL.md"]

    again_id, again_digest, _, _ = await _propose(env, "demo")
    assert (again_id, again_digest) == (op_id, digest)


async def test_propose_refuses_an_unstaged_slug(env) -> None:  # type: ignore[no-untyped-def]
    async with env.uow() as db:
        with pytest.raises(LookupError):
            await skill_ops.propose_skill_submission(
                db, USER, SESSION, "nope", summary="", change_kind="create", files_touched=[]
            )


async def test_confirm_saves_and_versions_the_skill(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    op_id, digest, _, _ = await _propose(env, "demo")

    state, code, message, result = await _confirm(env, op_id, digest)

    assert (state, code) == ("succeeded", None), message
    assert result["slug"] == "demo" and result["version_no"] == 1 and result["artifact_id"]
    assert (env.library / "demo" / "SKILL.md").read_text().count("version: 1") == 1
    assert not (env.staging / "demo").exists()
    assert await _versions(env, "demo") == [1]
    # confirming again is a no-op success, not a second version
    state, _, _, _ = await _confirm(env, op_id, digest)
    assert state == "succeeded"
    assert await _versions(env, "demo") == [1]


async def test_confirm_is_stale_when_the_draft_changed_after_submit(env) -> None:  # type: ignore[no-untyped-def]
    d = _stage(env.staging, "demo", "first")
    op_id, digest, _, _ = await _propose(env, "demo")
    (d / "SKILL.md").write_text("---\nname: demo\n---\nedited after submit\n", encoding="utf-8")

    state, code, message, _ = await _confirm(env, op_id, digest)

    assert (state, code) == ("stale", "OPERATION_STALE"), message
    assert not (env.library / "demo").exists()  # nothing was saved
    assert await _versions(env, "demo") == []


async def test_collision_needs_a_decision_new_version(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    op_id, digest, _, _ = await _propose(env, "demo")
    await _confirm(env, op_id, digest)
    # a second draft under the same slug, NOT prepared from the library copy
    _stage(env.staging, "demo", "second, unprepared")
    op2, digest2, preview, _ = await _propose(env, "demo")
    assert preview["conflict_kind"] == skill_ops.CONFLICT_UNPREPARED_COLLISION
    assert preview["next_version"] == 2

    state, code, message, _ = await _confirm(env, op2, digest2)
    assert (state, code) == ("failed", "OPERATION_FAILED")
    assert "skill_slug_collision" in (message or "")
    assert "second" not in (env.library / "demo" / "SKILL.md").read_text()

    state, _, _, result = await _confirm(env, op2, digest2, {"mode": "new_version"})
    assert state == "succeeded"
    assert result["version_no"] == 2 and result["decision_mode"] == "new_version"
    assert "second" in (env.library / "demo" / "SKILL.md").read_text()
    assert await _versions(env, "demo") == [1, 2]


async def test_collision_decision_rename_saves_a_new_skill(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    op_id, digest, _, _ = await _propose(env, "demo")
    await _confirm(env, op_id, digest)
    _stage(env.staging, "demo", "a different thing")
    op2, digest2, _, _ = await _propose(env, "demo")

    state, _, message, result = await _confirm(
        env, op2, digest2, {"mode": "rename", "new_slug": "demo-2"}
    )

    assert state == "succeeded", message
    assert result["slug"] == "demo-2" and result["renamed_from"] == "demo"
    assert result["version_no"] == 1
    md = (env.library / "demo-2" / "SKILL.md").read_text()
    assert "name: demo-2" in md and "a different thing" in md
    assert "first" in (env.library / "demo" / "SKILL.md").read_text()  # untouched
    assert await _versions(env, "demo") == [1]
    assert await _versions(env, "demo-2") == [1]


async def test_rename_to_an_existing_slug_is_refused(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    op_id, digest, _, _ = await _propose(env, "demo")
    await _confirm(env, op_id, digest)
    _stage(env.staging, "demo", "again")
    op2, digest2, _, _ = await _propose(env, "demo")

    state, code, message, _ = await _confirm(
        env, op2, digest2, {"mode": "rename", "new_slug": "demo"}
    )
    assert (state, code) == ("failed", "OPERATION_FAILED")
    assert (env.staging / "demo").is_dir()  # draft still there for another try


async def test_prepared_edit_saves_as_next_version_without_a_decision(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    op_id, digest, _, _ = await _propose(env, "demo")
    await _confirm(env, op_id, digest)

    # prepare_skill_edit's effect: library copy + provenance marker in staging
    await staging_mod.prepare_optimize(USER, SESSION, env.library / "demo", "skill-id")
    (env.staging / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo\nversion: 1\n---\n\nimproved\n", encoding="utf-8"
    )
    op2, digest2, preview, _ = await _propose(env, "demo", change_kind="update")
    assert preview["conflict_kind"] == skill_ops.CONFLICT_SAME_SOURCE

    state, _, message, result = await _confirm(env, op2, digest2)
    assert state == "succeeded", message
    assert result["version_no"] == 2
    assert "version: 2" in (env.library / "demo" / "SKILL.md").read_text()
    assert await _versions(env, "demo") == [1, 2]


async def test_cancel_removes_the_draft(env) -> None:  # type: ignore[no-untyped-def]
    _stage(env.staging, "demo", "first")
    op_id, digest, _, _ = await _propose(env, "demo")

    assert await _cancel(env, op_id, digest) == "cancelled"
    assert not (env.staging / "demo").exists()
    assert not (env.library / "demo").exists()

    # the same draft submitted again after a dismissal is a NEW proposal
    _stage(env.staging, "demo", "first")
    op2, _, _, state = await _propose(env, "demo")
    assert op2 != op_id and state == "awaiting_confirmation"


async def test_submit_tool_returns_the_operation_envelope(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json

    from valuz_agent.integrations.toolkit_mcp_server import HostExecContext
    from valuz_agent.integrations.tools_skill_creator import _submit_skill_handler

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", env.uow)
    _stage(env.staging, "demo", "first")

    result = await _submit_skill_handler(
        {"slug": "demo", "summary": "s", "change_kind": "create", "files_touched": ["SKILL.md"]},
        HostExecContext(session_id=SESSION, user_id=USER),
    )

    assert not result.is_error
    body = json.loads(result.content)
    assert body["ok"] is True and body["action"] == "submit"
    assert body["operation"]["state"] == "awaiting_confirmation"
    assert body["operation"]["preview"]["slug"] == "demo"
    assert "v1" in body["message"]


async def test_submit_tool_still_teaches_the_staging_path(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from valuz_agent.integrations.toolkit_mcp_server import HostExecContext
    from valuz_agent.integrations.tools_skill_creator import _submit_skill_handler

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", env.uow)
    result = await _submit_skill_handler(
        {"slug": "ghost", "summary": "s", "change_kind": "create", "files_touched": []},
        HostExecContext(session_id=SESSION, user_id=USER),
    )
    assert result.is_error
    assert str(env.staging / "ghost") in result.content


async def test_prepare_skill_edit_tool_seeds_staging_from_the_library(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json

    from valuz_agent.integrations.toolkit_mcp_server import HostExecContext
    from valuz_agent.integrations.tools_skill_creator import _prepare_skill_edit_handler

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", env.uow)
    _stage(env.staging, "demo", "first")
    op_id, digest, _, _ = await _propose(env, "demo")
    await _confirm(env, op_id, digest)

    result = await _prepare_skill_edit_handler(
        {"slug": "demo"}, HostExecContext(session_id=SESSION, user_id=USER)
    )

    assert not result.is_error, result.content
    body = json.loads(result.content)
    assert body["current_version"] == 1 and body["next_version"] == 2
    assert (env.staging / "demo" / "SKILL.md").is_file()
    assert staging_mod.read_staging_meta(env.staging / "demo") is not None

    missing = await _prepare_skill_edit_handler(
        {"slug": "nope"}, HostExecContext(session_id=SESSION, user_id=USER)
    )
    assert missing.is_error and "list_skills" in missing.content


# ── the key must follow the proposal, and failures must speak the envelope ──


async def test_resubmitting_the_same_bytes_as_an_update_is_not_a_conflict(env) -> None:  # type: ignore[no-untyped-def]
    """Regression: the flow this whole design is built around used to 409.

    The agent hand-writes a draft and submits (``create``, judged
    ``unprepared_collision``); the user does not confirm; the agent then does
    the right thing — ``prepare_skill_edit`` re-seeds the SAME bytes from the
    library — and submits again as ``update``. The staged tree is unchanged,
    so a key over ``(slug, bytes)`` is identical, while ``proposal_hash``
    covers ``change_kind`` and the summary and is not. ``propose`` then
    raised ``operation_idempotency_conflict`` on the CORRECTED submission.
    """
    _stage(env.staging, "demo", "first")
    op1, _, _, _ = await _propose(env, "demo", change_kind="create", summary="v1 draft")

    # Same bytes, different intent — exactly what prepare_skill_edit produces.
    op2, digest2, preview, state = await _propose(
        env, "demo", change_kind="update", summary="now an edit of the library copy"
    )

    assert op2 != op1, "a different proposal must not reuse the first record"
    assert state == "awaiting_confirmation"
    assert preview["change_kind"] == "update"
    # and it is still confirmable
    confirmed, code, message, _ = await _confirm(env, op2, digest2)
    assert (confirmed, code) == ("succeeded", None), message


async def test_an_identical_proposal_still_replays_onto_one_record(env) -> None:  # type: ignore[no-untyped-def]
    """The key follows the proposal, so idempotency still holds for a retry."""
    _stage(env.staging, "demo", "first")
    first = await _propose(env, "demo", change_kind="create", summary="same")
    again = await _propose(env, "demo", change_kind="create", summary="same")

    assert first[0] == again[0] and first[1] == again[1]


async def test_a_failed_submit_returns_an_envelope_not_a_bare_error(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A failure must be distinguishable from a historic card.

    The card falls back to a staging scan when the tool result carries no
    operation — that is how cards from sessions predating the record still
    render. A bare error string landed in the same branch, and because a save
    empties staging the scan said "waiting for the AI to write files": a
    failure shown as progress, before and after a reload alike.
    """
    import json

    from valuz_agent.integrations.toolkit_mcp_server import HostExecContext
    from valuz_agent.integrations.tools_skill_creator import _submit_skill_handler

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", env.uow)
    _stage(env.staging, "demo", "first")

    async def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise ValueError("operation_idempotency_conflict")

    monkeypatch.setattr("valuz_agent.modules.skills.operations.propose_skill_submission", _boom)
    result = await _submit_skill_handler(
        {"slug": "demo", "summary": "s", "change_kind": "update", "files_touched": []},
        HostExecContext(session_id=SESSION, user_id=USER),
    )

    assert result.is_error
    body = json.loads(result.content)
    assert body["ok"] is False
    assert body["action"] == "submit"
    assert "operation_idempotency_conflict" in body["message"]


async def test_the_not_staged_rejection_is_also_an_envelope(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import json

    from valuz_agent.integrations.toolkit_mcp_server import HostExecContext
    from valuz_agent.integrations.tools_skill_creator import _submit_skill_handler

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", env.uow)
    result = await _submit_skill_handler(
        {"slug": "ghost", "summary": "s", "change_kind": "create", "files_touched": []},
        HostExecContext(session_id=SESSION, user_id=USER),
    )

    assert result.is_error
    body = json.loads(result.content)
    assert body["ok"] is False and body["error_code"] == "skill_not_staged"
    # still teaches the exact path
    assert str(env.staging / "ghost") in body["message"]


# ── a cloud mirror may not veto a save that already touched disk ──────


async def test_a_failing_lifecycle_hook_does_not_undo_the_save(env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: filesystem and database used to diverge.

    A save writes the library directory (not transactional) and the version
    history (transactional). The overlay's mirror-to-cloud hook runs after
    both, inside the operation's savepoint — so its failure rolled the DB
    back while the directory kept the new content. Observed on qa: library
    ``version: 2``, history and ``list_skills`` still v1.
    """
    from valuz_agent.modules.skills import service as skills_service

    _stage(env.staging, "demo", "first")
    op_id, digest, _, _ = await _propose(env, "demo")

    class _AngryMirror:
        async def after_skill_saved(self, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("409 IDEMPOTENCY_KEY_REUSED")

        async def after_bundled_skills_materialized(self, **kwargs):  # type: ignore[no-untyped-def]
            return None

        async def before_skill_delete(self, **kwargs):  # type: ignore[no-untyped-def]
            return None

    from valuz_agent.ports.extensions import ext

    monkeypatch.setattr(ext, "skill_lifecycle", _AngryMirror(), raising=False)

    state, code, message, result = await _confirm(env, op_id, digest)

    assert (state, code) == ("succeeded", None), message
    assert result["version_no"] == 1
    # both stores moved together
    assert (env.library / "demo" / "SKILL.md").is_file()
    assert await _versions(env, "demo") == [1]
    assert skills_service is not None  # import kept meaningful for the reader

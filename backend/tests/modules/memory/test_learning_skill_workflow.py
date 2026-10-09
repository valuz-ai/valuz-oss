"""Marked learning drafts reuse the real existing skill approval/version pipeline.

The existing SQLite skill-submit fixture isolates staging/library and session
lookup. Memory reads/mutations use the real async MemoryLibrary and real local
catalog; the settings preference boundary uses that fixture's real SQLite.
Full no-monkeypatch Host/kernel/discovery/replay proof also exists separately.
"""

from __future__ import annotations

import json

import pytest

from tests.modules.skills.test_skill_submit_operation import (
    USER,
    _confirm,
    _propose,
    _stage,
)
from valuz_agent.facade.memory import MemoryLibrary
from valuz_agent.infra import db as host_db
from valuz_agent.integrations.memory_local import LocalMemoryBackend
from valuz_agent.modules.memory.learning import (
    SkillLearningRequest,
    preview_skill_learning,
    validate_learning_submission,
)
from valuz_agent.modules.memory.service import MemoryStore
from valuz_agent.modules.settings.models import AppSettingRow  # noqa: F401 — register table
from valuz_agent.modules.settings.preferences import set_memory_enabled
from valuz_agent.modules.skills import staging
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.memory import MemoryProtected, MemoryUnavailable, SourceRef

pytest_plugins = ("tests.modules.skills.test_skill_submit_operation",)


@pytest.fixture
async def learning(env, monkeypatch):
    monkeypatch.setattr(host_db, "AsyncSessionLocal", env.factory)
    backend = LocalMemoryBackend(MemoryStore())
    monkeypatch.setattr(ext, "memory_backend", backend)
    library = MemoryLibrary(USER)
    ids = []
    for index, content in enumerate(
        ("Check duplicate CSV row IDs.", "Never modify the input CSV.")
    ):
        result = await library.mutate(
            action="add",
            target="global",
            content=content,
            kind="lesson",
            source="user",
            source_refs=(
                SourceRef(kind="manual", source_id=f"owner-source-{index}", origin="owner"),
            ),
            operation_id=f"seed-{index}",
            base_revision=(await library.snapshot()).revision,
        )
        ids.append(result.record_id)
    request = SkillLearningRequest(
        record_ids=tuple(ids),
        name="learning-csv",
        purpose="Review repeated CSV checks",
        replay_cases=("Duplicate IDs must be identified.", "Unique IDs must stay unchanged."),
    )
    preview = await preview_skill_learning(
        USER, request, project_id="chat-default", library=library
    )
    directory = env.staging / request.name
    directory.mkdir()
    (directory / "SKILL.md").write_text(preview.draft)
    (directory / staging.LEARNING_META_FILENAME).write_text(
        preview.submission_context.model_dump_json()
    )
    yield library, request, preview, directory


@pytest.mark.parametrize("action", ["replace", "remove"])
async def test_confirm_refuses_changed_or_forgotten_learning_sources(env, learning, action):
    library, request, _preview, _directory = learning
    operation_id, digest, _card, state = await _propose(env, request.name)
    assert state == "awaiting_confirmation"
    await library.mutate(
        action=action,
        target="global",
        record_id=request.record_ids[0],
        content="Owner correction: use the current evidence." if action == "replace" else None,
        source="user",
        operation_id="owner-correction",
        base_revision=(await library.snapshot()).revision,
    )
    state, code, _message, _result = await _confirm(env, operation_id, digest)
    assert (state, code) == ("stale", "OPERATION_STALE")
    assert not (env.library / request.name).exists()


@pytest.mark.parametrize("change", ["remove", "edit"])
async def test_learning_metadata_cannot_change_after_exact_draft_approval(env, learning, change):
    _library, request, _preview, directory = learning
    operation_id, digest, _, _ = await _propose(env, request.name)
    metadata = directory / staging.LEARNING_META_FILENAME
    if change == "remove":
        metadata.unlink()
    else:
        value = json.loads(metadata.read_text())
        value["evidence"][0]["record_revision"] += 1
        metadata.write_text(json.dumps(value))
    state, code, _, _ = await _confirm(env, operation_id, digest)
    assert (state, code) == ("stale", "OPERATION_STALE")
    assert not (env.library / request.name).exists()


async def test_reviewed_current_learning_draft_saves_and_keeps_source_lineage(env, learning):
    _library, request, preview, _directory = learning
    operation_id, digest, card, _ = await _propose(env, request.name)
    assert card["learning_context"] == preview.submission_context.model_dump(mode="json")
    state, code, _, result = await _confirm(env, operation_id, digest)
    assert (state, code) == ("succeeded", None)
    assert result["version_no"] == 1
    assert (env.library / request.name / staging.LEARNING_META_FILENAME).exists()


async def test_disabled_memory_refuses_marked_submission(env, learning):
    _library, request, _preview, _directory = learning
    operation_id, digest, _, _ = await _propose(env, request.name)
    async with env.uow() as db:
        await set_memory_enabled(db, False, user_id=USER)
    state, _code, message, _ = await _confirm(env, operation_id, digest)
    assert state == "failed" and "refused" in message
    assert not (env.library / request.name).exists()


async def test_foreign_owner_claims_and_namespace_cannot_be_staged_as_verified(env, learning):
    library, request, preview, directory = learning
    with pytest.raises(MemoryProtected):
        await validate_learning_submission(
            "foreign", preview.submission_context, project_id="chat-default", library=library
        )
    value = preview.submission_context.model_dump(mode="json")
    value.update({"approved": True, "owner": USER, "namespace": "plugin:foreign"})
    (directory / staging.LEARNING_META_FILENAME).write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Invalid learning"):
        await _propose(env, request.name)


async def test_authority_binding_claim_changed_after_preview_is_not_accepted(env, learning):
    _library, request, _preview, directory = learning
    metadata = directory / staging.LEARNING_META_FILENAME
    value = json.loads(metadata.read_text())
    value["authority_id"] = "different-authority"
    metadata.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="authority binding changed"):
        await _propose(env, request.name)


async def test_unavailable_bound_authority_cannot_fallback_to_local_sources(
    env, learning, monkeypatch
):
    _library, request, _preview, _directory = learning
    operation_id, digest, _, _ = await _propose(env, request.name)

    class UnavailableAuthority:
        async def snapshot(self, _owner, *, namespace):
            raise MemoryUnavailable("private upstream diagnostic must not reach review card")

    monkeypatch.setattr(ext, "memory_backend", UnavailableAuthority())
    state, _code, message, _ = await _confirm(env, operation_id, digest)
    assert state == "failed" and "refresh unavailable" in message
    assert "private upstream" not in message
    assert not (env.library / request.name).exists()


async def test_live_authority_change_after_submit_refuses_save(env, learning, monkeypatch):
    _library, request, _preview, _directory = learning
    operation_id, digest, _, _ = await _propose(env, request.name)
    previous = ext.memory_backend

    class ChangedAuthority:
        async def snapshot(self, owner, *, namespace):
            snapshot = await previous.snapshot(owner, namespace=namespace)
            return snapshot.model_copy(
                update={"authority_id": "new-live-authority", "authority_epoch": 2}
            )

    monkeypatch.setattr(ext, "memory_backend", ChangedAuthority())
    state, code, _, _ = await _confirm(env, operation_id, digest)
    assert (state, code) == ("stale", "OPERATION_STALE")
    assert not (env.library / request.name).exists()


async def test_ordinary_manual_skill_remains_saveable_when_memory_is_disabled(env, learning):
    async with env.uow() as db:
        await set_memory_enabled(db, False, user_id=USER)
    _stage(env.staging, "ordinary-manual", "Owner authored an ordinary manual method.")
    operation_id, digest, card, _ = await _propose(env, "ordinary-manual")
    assert "learning_context" not in card
    state, code, _, result = await _confirm(env, operation_id, digest)
    assert (state, code) == ("succeeded", None)
    assert result["version_no"] == 1

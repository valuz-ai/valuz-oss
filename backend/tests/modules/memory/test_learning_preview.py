"""Skill suggestions use current reviewed evidence; producing a preview installs nothing."""

from pathlib import Path

import pytest

from valuz_agent.facade.memory import MemoryLibrary
from valuz_agent.infra.fs_registry import FsRegistry
from valuz_agent.integrations.memory_local import LocalMemoryBackend
from valuz_agent.modules.memory.learning import SkillLearningRequest, preview_skill_learning
from valuz_agent.modules.memory.service import MemoryStore
from valuz_agent.ports.memory import MemoryProtected, SourceRef


@pytest.fixture
def memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MemoryStore, MemoryLibrary]:
    registry = FsRegistry()
    monkeypatch.setattr(registry, "data_dir", lambda _owner: tmp_path)
    store = MemoryStore(registry)

    async def enabled(_owner: str) -> bool:
        return True

    monkeypatch.setattr(MemoryLibrary, "recall_enabled", staticmethod(enabled))
    return store, MemoryLibrary("owner", backend=LocalMemoryBackend(store))


def seed(store: MemoryStore, *, owner: str = "owner", source: str = "user") -> SkillLearningRequest:
    ids = []
    for index, content in enumerate(
        (
            "Check original evidence before summarizing.",
            "Record contradictions before making conclusions.",
        )
    ):
        result = store.mutate(
            owner,
            action="add",
            target="global",
            operation_id=f"seed-{index}",
            base_revision=store.snapshot(owner).revision,
            content=content,
            kind="lesson",
            source=source,
            source_refs=(SourceRef(kind="session", source_id=f"s-{index}", origin="owner"),),
        )
        assert result.record_id is not None
        ids.append(result.record_id)
    return SkillLearningRequest(
        record_ids=tuple(ids),
        name="evidence-review",
        purpose="Review repeated research work",
        replay_cases=(
            "Check a report with a contradictory source.",
            "Check a report without adequate original evidence.",
        ),
    )


async def test_preview_uses_current_reviewed_sources_and_installs_nothing(
    memory: tuple[MemoryStore, MemoryLibrary],
    tmp_path: Path,
) -> None:
    store, library = memory
    request = seed(store)
    before = store.snapshot("owner")
    preview = await preview_skill_learning("owner", request, project_id=None, library=library)
    assert preview.saved is False and preview.installed is False
    assert "original evidence" in preview.draft
    assert len(preview.evidence) == 2 and len(preview.replay_cases) == 2
    assert store.snapshot("owner") == before
    assert not list(tmp_path.rglob("SKILL.md"))


async def test_automatic_lessons_do_not_become_reviewed_skill_instructions(
    memory: tuple[MemoryStore, MemoryLibrary],
) -> None:
    store, library = memory
    request = seed(store, source="auto")
    with pytest.raises(MemoryProtected, match="confirmed"):
        await preview_skill_learning("owner", request, project_id=None, library=library)


async def test_forgotten_or_foreign_lessons_cannot_be_reused(
    memory: tuple[MemoryStore, MemoryLibrary],
) -> None:
    store, library = memory
    request = seed(store, owner="foreign")
    with pytest.raises(MemoryProtected, match="still exist"):
        await preview_skill_learning("owner", request, project_id=None, library=library)
    request = seed(store)
    store.mutate(
        "owner",
        action="remove",
        target="global",
        operation_id="forget",
        base_revision=store.snapshot("owner").revision,
        record_id=request.record_ids[0],
    )
    with pytest.raises(MemoryProtected, match="still exist"):
        await preview_skill_learning("owner", request, project_id=None, library=library)


async def test_correction_is_read_again_instead_of_reusing_an_old_draft(
    memory: tuple[MemoryStore, MemoryLibrary],
) -> None:
    store, library = memory
    request = seed(store)
    first = await preview_skill_learning("owner", request, project_id=None, library=library)
    store.mutate(
        "owner",
        action="replace",
        target="global",
        operation_id="correct",
        base_revision=store.snapshot("owner").revision,
        record_id=request.record_ids[0],
        content="Use audited evidence with explicit dates.",
    )
    second = await preview_skill_learning("owner", request, project_id=None, library=library)
    assert second.catalog_revision > first.catalog_revision
    assert "audited evidence" in second.draft and "Check original evidence" not in second.draft


async def test_memory_disabled_prevents_learning_preview(
    memory: tuple[MemoryStore, MemoryLibrary],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, library = memory
    request = seed(store)

    async def disabled(_owner: str) -> bool:
        return False

    monkeypatch.setattr(MemoryLibrary, "recall_enabled", staticmethod(disabled))
    with pytest.raises(MemoryProtected, match="disabled"):
        await preview_skill_learning("owner", request, project_id=None, library=library)


async def test_memory_tool_exposes_the_preview_without_saving(
    memory: tuple[MemoryStore, MemoryLibrary],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from types import SimpleNamespace

    from valuz_agent.integrations.toolkit_mcp_server import HostExecContext
    from valuz_agent.modules.memory import tools

    store, library = memory
    request = seed(store)
    original = store.snapshot("owner")
    monkeypatch.setattr(tools, "MemoryLibrary", lambda _owner: library)

    async def session(owner: str, session_id: str) -> SimpleNamespace:
        return SimpleNamespace(user_id=owner, metadata={"valuz": {"project_id": None}})

    monkeypatch.setattr(tools.kernel_client, "get_session", session)
    result = await tools._memory_handler(
        {"action": "skill_preview", **request.model_dump(mode="json")},
        HostExecContext(user_id="owner", session_id="source-session"),
    )
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload["saved"] is False and payload["installed"] is False
    assert payload["catalog_revision"] == original.revision
    assert store.snapshot("owner") == original

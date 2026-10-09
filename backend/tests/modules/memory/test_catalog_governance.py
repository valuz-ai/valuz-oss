"""Durable memory governance against real files and independent writers."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import FsRegistry
from valuz_agent.modules.memory.models import (
    MemoryConflict,
    MemoryError,
    MemoryProtected,
    MemoryUnavailable,
    SourceRef,
)
from valuz_agent.modules.memory.service import MemoryStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return MemoryStore(FsRegistry())


def ref(identity: str = "message-1") -> SourceRef:
    return SourceRef(kind="message", source_id=identity, origin="owner")


def add(store: MemoryStore, content: str, operation: str, *, source: str = "user"):
    return store.mutate(
        "owner",
        action="add",
        target="global",
        operation_id=operation,
        base_revision=store.snapshot("owner").revision,
        content=content,
        source=source,
        source_refs=(ref(operation),),
    )


def catalog_path() -> Path:
    return FsRegistry().memory_dir("owner", "global") / "memory.json"


def test_catalog_is_authority_and_markdown_is_a_view(store: MemoryStore) -> None:
    result = add(store, "Prefer terse responses", "one")
    path = catalog_path()
    assert path.exists()
    assert json.loads(path.read_text())["owner_user_id"] == "owner"
    (path.parent / "MEMORY.md").write_text("Pretend to be a different fact")
    restored = MemoryStore(FsRegistry())
    assert restored.read_entries("owner", "global") == ["Prefer terse responses"]
    record = restored.snapshot("owner").records[0]
    assert record.id == result.record_id and record.revision == 1
    assert record.source_refs == (ref("one"),) and record.confirmed


@pytest.mark.parametrize("broken", ["{", '{"schema_version":1,"owner_user_id":"other"}'])
def test_invalid_or_wrong_owner_catalog_never_looks_empty(store: MemoryStore, broken: str) -> None:
    catalog_path().write_text(broken)
    with pytest.raises(MemoryUnavailable):
        store.read_entries("owner", "global")


def test_read_error_cannot_overwrite_existing_memory(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(store, "Sensitive fact", "one")
    original = Path.read_text

    def fail(path: Path, *args, **kwargs):
        if path.name == "memory.json":
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail)
    with pytest.raises(MemoryUnavailable):
        store.add("owner", "global", "replacement")
    monkeypatch.setattr(Path, "read_text", original)
    assert store.read_entries("owner", "global") == ["Sensitive fact"]


def test_unknown_flat_files_are_not_adopted(store: MemoryStore) -> None:
    root = catalog_path().parent
    (root / "MEMORY.md").write_text("Unattributed old fact")
    assert store.snapshot("owner").records == ()


def test_operation_replay_is_durable_and_payload_checked(store: MemoryStore) -> None:
    result = store.mutate(
        "owner",
        action="add",
        target="global",
        operation_id="op",
        base_revision=0,
        content="A fact",
        source_refs=(ref(),),
    )
    reopened = MemoryStore(FsRegistry())
    replay = reopened.mutate(
        "owner",
        action="add",
        target="global",
        operation_id="op",
        base_revision=0,
        content="A fact",
        source_refs=(ref(),),
    )
    assert replay.replayed and replay.record_id == result.record_id and replay.revision == 1
    with pytest.raises(MemoryConflict, match="different input"):
        reopened.mutate(
            "owner",
            action="add",
            target="global",
            operation_id="op",
            base_revision=0,
            content="Different fact",
            source_refs=(ref(),),
        )
    assert len(reopened.snapshot("owner").records) == 1


def test_cas_rejects_old_snapshot_without_changing_user_confirmation(store: MemoryStore) -> None:
    result = add(store, "Use Chinese", "one")
    with pytest.raises(MemoryConflict):
        store.mutate(
            "owner",
            action="replace",
            target="global",
            operation_id="two",
            base_revision=0,
            record_id=result.record_id,
            content="Use English",
        )
    assert store.snapshot("owner").records[0].content == "Use Chinese"
    corrected = store.mutate(
        "owner",
        action="replace",
        target="global",
        operation_id="two",
        base_revision=1,
        record_id=result.record_id,
        content="Use English",
        source_refs=(ref("correction"),),
        kind="preference",
    )
    record = store.snapshot("owner").records[0]
    assert record.id == result.record_id and record.revision == 2
    assert record.confirmed and record.kind == "preference"
    assert record.supersedes == (f"{result.record_id}@1",)
    assert corrected.revision == 2


@pytest.mark.parametrize("source", ["auto", "agent"])
@pytest.mark.parametrize("action", ["replace", "remove", "clear"])
def test_nonhuman_mutations_cannot_change_confirmed_record(
    store: MemoryStore, source: str, action: str
) -> None:
    result = add(store, "A confirmed preference", "one")
    with pytest.raises(MemoryProtected):
        store.mutate(
            "owner",
            action=action,
            target="global",
            operation_id="two",
            base_revision=1,
            record_id=result.record_id,
            content="Model guess",
            source=source,
        )
    assert store.snapshot("owner").revision == 1


def test_legacy_auto_cannot_replace_without_snapshot(store: MemoryStore) -> None:
    store.add("owner", "global", "Old agent fact")
    result = store.replace("owner", "global", "Old", "New guess", source="auto")
    assert not result["success"] and result["error_code"] == "memory.protected"
    assert store.read_entries("owner", "global") == ["Old agent fact"]


def test_agent_never_claims_human_confirmation(store: MemoryStore) -> None:
    store.add("owner", "global", "An agent observation")
    record = store.snapshot("owner").records[0]
    assert not record.confirmed and record.source_refs == ()
    with pytest.raises(MemoryProtected):
        store.mutate(
            "owner",
            action="add",
            target="user",
            operation_id="untrusted",
            base_revision=1,
            content="Model guess",
            source="user",
            source_refs=(SourceRef(kind="tool", source_id="external"),),
        )


def test_forget_source_removes_lineage_and_blocks_reingestion(store: MemoryStore) -> None:
    first = add(store, "Private nickname", "one")
    store.mutate(
        "owner",
        action="replace",
        target="global",
        operation_id="correction",
        base_revision=1,
        record_id=first.record_id,
        content="Corrected private nickname",
        source_refs=(ref("two"),),
    )
    forgotten = store.forget_source("owner", ref("one"), operation_id="forget", base_revision=2)
    assert forgotten.affected_ids == (first.record_id,)
    assert store.list_records("owner", source_ref=ref("one")) == ()
    raw = catalog_path().read_text()
    assert "Private nickname" not in raw and "Corrected private nickname" not in raw
    reopened = MemoryStore(FsRegistry())
    replay = reopened.forget_source("owner", ref("one"), operation_id="forget", base_revision=2)
    assert replay.replayed and replay.revision == 3
    for source in ["auto", "user"]:
        with pytest.raises(MemoryProtected):
            reopened.mutate(
                "owner",
                action="add",
                target="global",
                operation_id="late-" + source,
                base_revision=3,
                content="A paraphrase",
                source=source,
                source_refs=(ref("one"),),
            )
    add(reopened, "Corrected private nickname", "new-explicit-input")
    assert reopened.snapshot("owner").records[0].source_refs == (ref("new-explicit-input"),)


def test_clear_retains_tombstones_and_rejects_late_auto(store: MemoryStore) -> None:
    store.add("owner", "global", "Forgotten content")
    store.clear("owner", "global")
    assert store.read_entries("owner", "global") == []
    assert catalog_path().exists()
    assert "Forgotten content" not in catalog_path().read_text()
    assert not store.add("owner", "global", "Forgotten content", source="auto")["success"]
    assert not store.add("owner", "global", "Paraphrase", source="auto")["success"]
    add(store, "Fresh explicit fact", "new")


def test_recall_is_scoped_bounded_and_filters_objects(store: MemoryStore) -> None:
    store.add("owner", "user", "Prefer concise replies")
    store.mutate(
        "owner",
        action="add",
        target="project",
        project_id="a",
        operation_id="a",
        base_revision=1,
        content="Project Alpha release",
        source="agent",
        object_refs=("alpha",),
    )
    store.add("owner", "project", "Project Beta release", project_id="b")
    snapshot = store.recall(
        "owner", "release", project_id="a", object_refs=("alpha",), max_chars=100
    )
    assert all(r.project_id != "b" for r in snapshot.records)
    assert "Project Alpha release" in [r.content for r in snapshot.records]
    assert store.recall("other", "release", project_id="a").records == ()
    assert store.recall("owner", "release", project_id="a", max_chars=1).records == ()


def test_invalid_namespace_kind_and_scope_are_rejected(store: MemoryStore) -> None:
    with pytest.raises(MemoryError):
        store.mutate(
            "owner",
            action="add",
            target="global",
            operation_id="a",
            base_revision=0,
            content="x",
            namespace="arbitrary",
        )
    with pytest.raises(MemoryError):
        store.mutate(
            "owner",
            action="add",
            target="global",
            operation_id="b",
            base_revision=0,
            content="x",
            project_id="a",
        )
    with pytest.raises(MemoryError):
        store.mutate(
            "owner",
            action="add",
            target="global",
            operation_id="c",
            base_revision=0,
            content="x",
            kind="permissions",
        )
    assert store.snapshot("owner").revision == 0


def test_frozen_injection_identifies_versions_and_invalidations_are_scoped(
    store: MemoryStore,
) -> None:
    result = add(store, "First preference", "initial")
    frozen = store.render_for_injection("owner")
    assert f"memory_id={result.record_id} revision=1" in frozen
    store.mutate(
        "owner",
        action="replace",
        target="global",
        operation_id="correct",
        base_revision=1,
        record_id=result.record_id,
        content="New preference",
        source_refs=(ref("correction"),),
    )
    store.add("owner", "project", "Project A private", project_id="a")
    store.add("owner", "project", "Project B private", project_id="b")
    store.drop_project("owner", "a")
    store.drop_project("owner", "b")
    assert "First preference" in frozen  # already captured bytes never mutated
    current = store.render_for_injection("owner")
    assert f"memory_id={result.record_id} revision=2" in current
    assert "First preference" not in current
    invalidated = store.invalidated_ids("owner", project_id="a")
    assert {item.project_id for item in invalidated} == {None, "a"}
    assert invalidated[0].record_id == result.record_id
    assert invalidated[0].record_revision == 1
    assert all("preference" not in item.model_dump_json() for item in invalidated)
    assert store.invalidated_ids("other", project_id="a") == ()


def _writer(directory: str, prefix: str, start) -> None:
    settings.data_dir = Path(directory)
    store = MemoryStore(FsRegistry())
    start.wait(10)
    for index in range(12):
        assert store.add("owner", "global", f"{prefix} record {index}")["success"]


def test_independent_processes_do_not_lose_writes(store: MemoryStore, tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    workers = [
        context.Process(target=_writer, args=(str(tmp_path), prefix, start))
        for prefix in ["a", "b"]
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(30)
        assert worker.exitcode == 0
    snapshot = store.snapshot("owner")
    assert len(snapshot.records) == 24 and snapshot.revision == 24
    assert len({record.id for record in snapshot.records}) == 24


def test_explicit_confirmation_of_duplicate_upgrades_record_without_copy(
    store: MemoryStore,
) -> None:
    store.add("owner", "global", "Use Python")
    original = store.snapshot("owner").records[0]
    result = add(store, "Use Python", "confirm")
    record = store.snapshot("owner").records[0]
    assert result.status == "applied" and record.confirmed
    assert record.id == original.id and record.revision == 2
    assert record.source_refs == (ref("confirm"),)
    assert len(store.snapshot("owner").records) == 1


def test_equal_text_for_distinct_objects_does_not_merge(store: MemoryStore) -> None:
    for index, obj in enumerate(["client:a", "client:b"]):
        store.mutate(
            "owner",
            action="add",
            target="global",
            operation_id=obj,
            base_revision=index,
            content="Prefers email",
            source="agent",
            object_refs=(obj,),
        )
    assert len(store.snapshot("owner").records) == 2


def test_export_failure_is_explicit_and_same_operation_repairs_view(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import valuz_agent.modules.memory.repository as repository

    add(store, "Private exported fact", "one")
    original = repository.atomic_write

    def fail(path: Path, text: str) -> None:
        if path.name == "MEMORY.md":
            raise PermissionError("disk error")
        original(path, text)

    monkeypatch.setattr(repository, "atomic_write", fail)
    with pytest.raises(MemoryUnavailable, match="Catalog committed"):
        store.mutate(
            "owner",
            action="replace",
            target="global",
            operation_id="update",
            base_revision=1,
            record_id=store.snapshot("owner").records[0].id,
            content="A corrected fact",
            source_refs=(ref("new"),),
        )
    assert store.snapshot("owner").revision == 2  # truthful durable outcome
    monkeypatch.setattr(repository, "atomic_write", original)
    replay = store.mutate(
        "owner",
        action="replace",
        target="global",
        operation_id="update",
        base_revision=1,
        record_id=store.snapshot("owner").records[0].id,
        content="A corrected fact",
        source_refs=(ref("new"),),
    )
    assert replay.replayed and replay.revision == 2
    assert "A corrected fact" in (catalog_path().parent / "MEMORY.md").read_text()
    assert "Private exported fact" not in catalog_path().read_text()


def test_correction_preserves_kind_and_object_scope_when_not_explicitly_changed(
    store: MemoryStore,
) -> None:
    first = store.mutate(
        "owner",
        action="add",
        target="global",
        operation_id="one",
        base_revision=0,
        content="Prefer concise reports",
        kind="preference",
        object_refs=("client:alpha",),
        source_refs=(ref(),),
    )
    store.mutate(
        "owner",
        action="replace",
        target="global",
        operation_id="correct",
        base_revision=1,
        record_id=first.record_id,
        content="Prefer detailed reports",
        source_refs=(ref("correction"),),
    )
    record = store.snapshot("owner").records[0]
    assert record.kind == "preference" and record.object_refs == ("client:alpha",)


def test_legacy_agent_clear_cannot_erase_confirmed_memory(store: MemoryStore) -> None:
    add(store, "Human confirmed preference", "one")
    with pytest.raises(MemoryProtected):
        store.clear("owner", "global", source="agent")
    assert store.read_entries("owner", "global") == ["Human confirmed preference"]
    store.clear("owner", "global", source="user")
    assert store.read_entries("owner", "global") == []


def test_catalog_publish_failure_keeps_old_revision_and_can_retry(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import valuz_agent.modules.memory.repository as repository

    add(store, "Original fact", "one")
    before = catalog_path().read_bytes()
    original = repository.os.replace

    def fail(source, destination):
        if Path(destination).name == "memory.json":
            raise OSError("publish refused")
        return original(source, destination)

    monkeypatch.setattr(repository.os, "replace", fail)
    with pytest.raises(MemoryUnavailable):
        add(store, "Next fact", "two")
    assert catalog_path().read_bytes() == before
    monkeypatch.setattr(repository.os, "replace", original)
    result = add(store, "Next fact", "two")
    assert result.revision == 2 and not result.replayed
    assert store.read_entries("owner", "global") == ["Original fact", "Next fact"]


def test_catalog_with_malformed_namespace_is_not_partially_read(store: MemoryStore) -> None:
    add(store, "A valid fact", "one")
    catalog = json.loads(catalog_path().read_text())
    catalog["records"][0]["namespace"] = "invalid/namespace"
    catalog_path().write_text(json.dumps(catalog))
    with pytest.raises(MemoryUnavailable, match="namespace"):
        store.snapshot("owner")


def test_source_identity_forget_covers_observation_revisions(store: MemoryStore) -> None:
    old = SourceRef(kind="document", source_id="doc", revision="v1", origin="agent")
    store.mutate(
        "owner",
        action="add",
        target="global",
        operation_id="doc-fact",
        base_revision=0,
        content="A fact from a document",
        source="auto",
        source_refs=(old,),
    )
    store.forget_source("owner", old, operation_id="forget", base_revision=1)
    fresh = SourceRef(kind="document", source_id="doc", revision="v2", origin="owner")
    with pytest.raises(MemoryProtected):
        store.mutate(
            "owner",
            action="add",
            target="global",
            operation_id="retry",
            base_revision=2,
            content="New phrasing",
            source="auto",
            source_refs=(fresh,),
        )
    assert store.list_records("owner", source_ref=fresh) == ()


@pytest.mark.parametrize("credential", ["api_key=synthetic-value", "Bearer synthetic-test-token"])
def test_all_writers_reject_recognizable_credentials(store: MemoryStore, credential: str) -> None:
    for writer in ["user", "agent", "auto"]:
        result = store.add("owner", "global", credential, source=writer)
        assert not result["success"] and "Credentials" in result["error"]
    assert store.snapshot("owner").records == ()
    assert store.add("owner", "global", "API keys are rotated monthly")["success"]


def test_legacy_response_snapshot_revision_matches_entries_after_another_writer(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = store._execute
    other = MemoryStore(FsRegistry())

    def interleave(*args, **kwargs):
        receipt = original(*args, **kwargs)
        assert other.add("owner", "global", "Another writer's fact")["success"]
        return receipt

    monkeypatch.setattr(store, "_execute", interleave)
    result = store.add("owner", "global", "My fact")
    assert result["mutation_revision"] == 1
    assert result["revision"] == 2
    assert result["entries"] == ["My fact", "Another writer's fact"]


def test_legacy_wrappers_preserve_host_source_refs(store: MemoryStore) -> None:
    source = SourceRef(kind="session", source_id="actual-session", origin="agent")
    assert store.add("owner", "global", "An agent note", source_refs=(source,))["success"]
    assert store.replace("owner", "global", "agent note", "A revised note", source_refs=(source,))[
        "success"
    ]
    assert store.snapshot("owner").records[0].source_refs == (source,)
    assert store.remove("owner", "global", "revised", source_refs=(source,))["success"]
    assert store.add("owner", "global", "Another note", source_refs=(source,))["success"]
    store.clear("owner", "global", source="agent", source_refs=(source,))
    assert store.snapshot("owner").records == ()


def test_sources_forgotten_reads_live_owner_watermarks_without_retained_text(
    store: MemoryStore,
) -> None:
    first = ref("source-one")
    second = ref("source-two")
    assert store.sources_forgotten("owner", (first, second)) == ()
    store.forget_source("owner", first, operation_id="forget-one", base_revision=0)
    assert store.sources_forgotten("owner", (first, second)) == (first,)
    revised = first.model_copy(update={"revision": "new", "origin": "agent"})
    assert store.sources_forgotten("owner", (revised,)) == (revised,)
    assert MemoryStore(FsRegistry()).sources_forgotten("owner", (first,)) == (first,)
    assert store.sources_forgotten("other-owner", (first,)) == ()


def test_operation_nonce_never_retains_deleted_content(store: MemoryStore) -> None:
    nonce = "a-private-observation-that-must-disappear"
    result = store.mutate(
        "owner",
        action="add",
        target="global",
        operation_id=nonce,
        base_revision=0,
        content="A private observation",
        source="user",
    )
    store.mutate(
        "owner",
        action="remove",
        target="global",
        operation_id="delete-" + nonce,
        base_revision=1,
        record_id=result.record_id,
        source="user",
    )
    raw = catalog_path().read_text()
    assert nonce not in raw and "A private observation" not in raw
    assert store.operation_receipt("owner", nonce) == result
    replay = store.mutate(
        "owner",
        action="add",
        target="global",
        operation_id=nonce,
        base_revision=0,
        content="A private observation",
        source="user",
    )
    assert replay.replayed and store.snapshot("owner").records == ()
    store.forget_source(
        "owner", ref("another-source"), operation_id="forget-" + nonce, base_revision=2
    )
    assert nonce not in catalog_path().read_text()


def test_invalid_operation_key_catalog_is_not_rewritten_with_raw_nonce(store: MemoryStore) -> None:
    add(store, "A fact", "one")
    catalog = json.loads(catalog_path().read_text())
    receipt = next(iter(catalog["operations"].values()))
    catalog["operations"] = {"a-raw-sensitive-nonce": receipt}
    catalog_path().write_text(json.dumps(catalog))
    before = catalog_path().read_bytes()
    with pytest.raises(MemoryUnavailable, match="operation keys"):
        store.clear("owner", "global")
    assert catalog_path().read_bytes() == before


def test_valid_dormant_namespace_is_preserved_without_leaking_to_core(store: MemoryStore) -> None:
    add(store, "An earlier domain observation", "one")
    catalog = json.loads(catalog_path().read_text())
    catalog["records"][0]["namespace"] = "domain.dormant"
    catalog_path().write_text(json.dumps(catalog))
    before = catalog_path().read_bytes()
    assert store.snapshot("owner").records == ()
    assert store.recall("owner", "observation").records == ()
    with pytest.raises(MemoryError, match="namespace"):
        store.snapshot("owner", namespace="domain.dormant")
    assert catalog_path().read_bytes() == before

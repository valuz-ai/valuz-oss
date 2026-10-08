"""Async authority facade keeps real local governance and failure boundaries."""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from valuz_agent.facade.memory import MemoryLibrary
from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import FsRegistry
from valuz_agent.integrations.memory_local import LocalMemoryBackend
from valuz_agent.modules.memory.service import MemoryStore
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.memory import (
    MemoryConflict,
    MemoryError,
    MemoryMutationCommand,
    MemoryMutationResult,
    MemoryProtected,
    MemorySnapshot,
    MemoryUnavailable,
    SourceRef,
)
from valuz_agent.ports.memory_maintenance import NoopMemoryMaintenance
from valuz_agent.ports.memory_namespaces import CoreMemoryNamespacePolicy


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(ext, "memory_backend", None)
    # A local facade fixture has no boot-owned journal/backend registration.
    # Restore the strict OSS baseline instead of inheriting another host test's
    # cleanup callback (which correctly fails when its journal DB is absent).
    monkeypatch.setattr(ext, "memory_maintenance", NoopMemoryMaintenance())
    monkeypatch.setattr(ext, "memory_namespace_policy", CoreMemoryNamespacePolicy())
    return MemoryStore(FsRegistry())


def evidence(identity: str = "real-human-input") -> SourceRef:
    return SourceRef(kind="message", source_id=identity, origin="owner")


async def test_async_facade_uses_real_local_catalog_with_owner_scope(store: MemoryStore) -> None:
    library = MemoryLibrary("owner", backend=LocalMemoryBackend(store))
    created = await library.mutate(
        action="add",
        target="global",
        operation_id="one",
        base_revision=0,
        content="Prefer concise responses",
        source="user",
        source_refs=(evidence(),),
        kind="preference",
    )
    snapshot = await library.snapshot()
    assert snapshot.owner_user_id == "owner" and snapshot.revision == 1
    assert snapshot.records[0].confirmed and snapshot.records[0].kind == "preference"
    assert (await library.list_records())[0].id == created.record_id
    assert (await library.recall("responses")).records[0].id == created.record_id
    assert "revision=1" in await library.render_for_injection()
    assert await library.operation_receipt("one") == created
    other = MemoryLibrary("other", backend=LocalMemoryBackend(store))
    assert await other.read_entries("global") == []
    assert (await other.snapshot()).owner_user_id == "other"
    await library.forget_source(evidence(), operation_id="forget", base_revision=1)
    assert await library.sources_forgotten((evidence(),)) == (evidence(),)
    assert (await library.invalidated_ids())[0].record_id == created.record_id
    assert await library.read_entries("global") == []


async def test_ext_backend_is_selected_and_failure_never_falls_back(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.add("owner", "global", "A stale local fact")

    class UnavailableBackend(LocalMemoryBackend):
        async def snapshot(self, user_id: str, *, namespace: str = "core") -> MemorySnapshot:
            raise MemoryUnavailable("Chosen authority is offline")

    monkeypatch.setattr(ext, "memory_backend", UnavailableBackend(store))
    library = MemoryLibrary("owner")
    with pytest.raises(MemoryUnavailable, match="offline"):
        await library.read_entries("global")
    assert store.read_entries("owner", "global") == ["A stale local fact"]


@pytest.mark.parametrize("operation", ["snapshot", "recall"])
async def test_wrong_owner_backend_snapshot_is_rejected(store: MemoryStore, operation: str) -> None:
    class MismatchedBackend(LocalMemoryBackend):
        async def snapshot(self, user_id: str, *, namespace: str = "core") -> MemorySnapshot:
            return MemorySnapshot(owner_user_id="different-owner", revision=0)

        async def recall(self, user_id: str, query: str = "", **kwargs) -> MemorySnapshot:
            return MemorySnapshot(owner_user_id="different-owner", revision=0)

    library = MemoryLibrary("owner", backend=MismatchedBackend(store))
    with pytest.raises(MemoryProtected, match="different owner"):
        if operation == "snapshot":
            await library.snapshot()
        else:
            await library.recall("anything")


async def test_local_backend_rejects_undeclared_namespace_and_blank_owner(
    store: MemoryStore,
) -> None:
    with pytest.raises(MemoryError, match="user_id"):
        MemoryLibrary("", backend=LocalMemoryBackend(store))
    library = MemoryLibrary("owner", backend=LocalMemoryBackend(store), namespace="unregistered")
    with pytest.raises(MemoryError, match="namespace"):
        await library.snapshot()
    assert store.snapshot("owner").records == ()


async def test_catalog_lock_and_io_run_outside_event_loop(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop_thread = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()
    original = store.snapshot
    observed_threads = []

    def blocking_snapshot(user_id: str, *, namespace: str = "core") -> MemorySnapshot:
        observed_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(2), "The event loop could not release the blocking store"
        return original(user_id, namespace=namespace)

    monkeypatch.setattr(store, "snapshot", blocking_snapshot)
    task = asyncio.create_task(MemoryLibrary("owner", backend=LocalMemoryBackend(store)).snapshot())
    assert await asyncio.to_thread(entered.wait, 1)
    # This executes on the event loop while real store IO is blocked elsewhere.
    release.set()
    result = await task
    assert result.owner_user_id == "owner" and observed_threads[0] != loop_thread


async def test_legacy_remote_like_write_uses_snapshot_cas_and_returns_current_revision(
    store: MemoryStore,
) -> None:
    store.add("owner", "global", "Original value")
    other = MemoryStore(FsRegistry())

    class RacingBackend(LocalMemoryBackend):
        async def mutate(
            self, user_id: str, command: MemoryMutationCommand
        ) -> MemoryMutationResult:
            assert command.base_revision == 1
            other.add(user_id, "global", "A competing write")
            return await super().mutate(user_id, command)

    library = MemoryLibrary("owner", backend=RacingBackend(store))
    response = await library.replace("global", "Original", "A stale replacement")
    assert not response["success"] and response["error_code"] == "memory.revision_conflict"
    assert response["revision"] == 2
    assert response["current_entries"] == ["Original value", "A competing write"]
    assert store.read_entries("owner", "global") == response["current_entries"]


async def test_legacy_facade_management_provenance_and_confirmation(store: MemoryStore) -> None:
    library = MemoryLibrary("owner", backend=LocalMemoryBackend(store))
    source = SourceRef(kind="session", source_id="actual-session", origin="agent")
    assert (await library.add("global", "A note", source_refs=(source,)))["success"]
    assert (await library.replace("global", "A note", "Updated note", source_refs=(source,)))[
        "success"
    ]
    assert (await library.list_records())[0].source_refs == (source,)
    automatic = await library.replace("global", "Updated", "Blind change", source="auto")
    assert not automatic["success"] and automatic["error_code"] == "memory.protected"
    assert (await library.remove("global", "Updated", source_refs=(source,)))["success"]
    assert (await library.add("project", "Project fact", project_id="p"))["success"]
    await library.drop_project("p")
    assert await library.read_entries("project", project_id="p") == []
    await library.mutate(
        action="add",
        target="user",
        operation_id="confirm",
        base_revision=(await library.snapshot()).revision,
        content="Human preference",
        source="user",
        source_refs=(evidence(),),
    )
    with pytest.raises(MemoryProtected):
        await library.clear("user", source="agent")
    await library.clear("user", source="user", source_refs=(evidence("clear"),))
    assert await library.read_entries("user") == []


async def test_nonautomatic_mutation_cleanup_partial_and_nonce_replay_retries_cleanup(
    store, monkeypatch
):
    from valuz_agent.ports.memory_maintenance import MemoryMaintenanceResult

    class Maintenance:
        calls = []
        complete = False

        async def purge_after_mutation(self, **values):
            self.calls.append(values)
            return MemoryMaintenanceResult(
                complete=self.complete,
                reason_code=None if self.complete else "memory.plan_cleanup_failed",
            )

    maintenance = Maintenance()
    monkeypatch.setattr(ext, "memory_maintenance", maintenance)
    library = MemoryLibrary("owner", backend=LocalMemoryBackend(store))
    command = dict(
        action="add",
        target="global",
        operation_id="same-actual-op",
        base_revision=0,
        source="agent",
        content="Synthetic actual fact",
        source_refs=(evidence(),),
    )
    first = await library.mutate(**command)
    assert first.revision == 1 and not first.maintenance_complete
    assert first.maintenance_reason_code == "memory.plan_cleanup_failed"
    assert maintenance.calls[-1]["owner_user_id"] == "owner"
    maintenance.complete = True
    replayed = await library.mutate(**command)
    assert replayed.replayed and replayed.revision == 1 and replayed.maintenance_complete
    assert len(maintenance.calls) == 2 and store.snapshot("owner").revision == 1
    await library.mutate(
        action="add",
        target="global",
        operation_id="auto-own-plan",
        base_revision=1,
        source="auto",
        content="Automatic fact",
    )
    assert len(maintenance.calls) == 2, "automatic apply must not purge its own plan"


async def test_forget_cleanup_failure_truthful_partial_after_catalog_commit(store, monkeypatch):
    class BrokenMaintenance:
        async def purge_after_mutation(self, **_values):
            raise RuntimeError("Synthetic unavailable journal")

    monkeypatch.setattr(ext, "memory_maintenance", BrokenMaintenance())
    library = MemoryLibrary("owner", backend=LocalMemoryBackend(store))
    added = await library.mutate(
        action="add",
        target="global",
        operation_id="fact-one",
        base_revision=0,
        source="agent",
        source_refs=(evidence(),),
        content="Forget this fact",
    )
    result = await library.forget_source(
        evidence(), operation_id="forget-one", base_revision=added.revision
    )
    assert (
        not result.maintenance_complete
        and result.maintenance_reason_code == "memory.cleanup_unavailable"
    )
    assert store.snapshot("owner").records == ()


@pytest.mark.parametrize(
    "binding",
    [
        {"authority_id": "only-id"},
        {"authority_epoch": 1},
        {"authority_id": " ", "authority_epoch": 0},
    ],
)
def test_authority_pair_rejects_partial_or_blank_snapshot_and_command(binding):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MemorySnapshot(owner_user_id="owner", revision=0, **binding)
    with pytest.raises(ValidationError):
        MemoryMutationCommand(
            action="add", target="global", operation_id="nonce", base_revision=0, **binding
        )


async def test_receipt_preserves_original_authority_and_nonce_cannot_cross_epoch(
    store: MemoryStore,
) -> None:
    library = MemoryLibrary("owner", backend=LocalMemoryBackend(store))
    command = dict(
        action="add",
        target="global",
        operation_id="epoch-nonce",
        base_revision=0,
        content="A stable preference",
        source="agent",
        authority_id="authority-a",
        authority_epoch=1,
    )
    result = await library.mutate(**command)
    assert result.authority_id == "authority-a" and result.authority_epoch == 1
    assert (await library.mutate(**command)).replayed
    receipt = await library.operation_receipt("epoch-nonce")
    assert receipt is not None and receipt.authority_epoch == 1
    with pytest.raises(MemoryConflict):
        await library.mutate(**(command | {"authority_id": "authority-b", "authority_epoch": 2}))
    ref = evidence()
    forgotten = await library.forget_source(
        ref,
        operation_id="forget-epoch",
        base_revision=1,
        authority_id="authority-a",
        authority_epoch=1,
    )
    assert forgotten.authority_epoch == 1
    with pytest.raises(MemoryConflict):
        await library.forget_source(
            ref,
            operation_id="forget-epoch",
            base_revision=1,
            authority_id="authority-b",
            authority_epoch=2,
        )


async def test_repeated_cancellation_during_real_guard_acquisition_never_leaks_lock(
    store, monkeypatch
):
    local = LocalMemoryBackend(store)
    original = store.storage_guard
    attempting = threading.Event()

    @contextmanager
    def observed(owner):
        attempting.set()
        with original(owner) as root:
            yield root

    with original("owner"):
        monkeypatch.setattr(store, "storage_guard", observed)
        context = local.storage_guard("owner")
        task = asyncio.create_task(context.__aenter__())
        assert await asyncio.to_thread(attempting.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    async with asyncio.timeout(2):
        async with local.storage_guard("owner"):
            pass


async def test_pre_binding_local_receipt_nonce_replay_survives_new_shared_contract(
    store: MemoryStore,
) -> None:
    import hashlib
    import json

    library = MemoryLibrary("owner", backend=LocalMemoryBackend(store))
    command = MemoryMutationCommand(
        action="add",
        target="global",
        operation_id="legacy-local",
        base_revision=0,
        content="An earlier local memory",
        source="agent",
    )
    result = await library.mutate(**command.model_dump(exclude={"namespace"}))
    path = FsRegistry().memory_dir("owner", "global") / "memory.json"
    catalog = json.loads(path.read_text())
    legacy_fingerprint = hashlib.sha256(
        json.dumps(
            [command.model_dump(mode="json", exclude={"authority_id", "authority_epoch"}), None],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    catalog["operations"][hashlib.sha256(b"legacy-local").hexdigest()]["fingerprint"] = (
        legacy_fingerprint
    )
    path.write_text(json.dumps(catalog))
    replay = await library.mutate(**command.model_dump(exclude={"namespace"}))
    assert replay.replayed and replay.record_id == result.record_id and replay.revision == 1
    assert replay.authority_id is None and replay.authority_epoch is None

"""Versioned memory refresh is bounded and does not alter frozen instructions."""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest

from valuz_agent.infra.fs_registry import FsRegistry
from valuz_agent.modules.memory import context as context_module
from valuz_agent.modules.memory.context import MemoryTurnContextProvider
from valuz_agent.modules.memory.models import MemoryUnavailable, SourceRef
from valuz_agent.modules.memory.service import MemoryStore
from valuz_agent.ports.message_context import TurnContextRequest


class _UOW:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> bool:
        return False


@pytest.fixture
def memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MemoryStore, dict[str, bool]]:
    fs = FsRegistry()
    monkeypatch.setattr(fs, "data_dir", lambda _owner: tmp_path)
    store = MemoryStore(fs)
    state = {"enabled": True}
    monkeypatch.setattr(context_module, "async_unit_of_work", lambda **_kwargs: _UOW())

    async def enabled(_db: object, *, user_id: str) -> bool:
        return state["enabled"]

    monkeypatch.setattr(context_module, "get_memory_enabled", enabled)
    return store, state


def request(owner: str = "alice", text: str = "Preferred language") -> TurnContextRequest:
    return TurnContextRequest(
        user_id=owner,
        session_id="main",
        project_id="p",
        host_ref=None,
        input_text=text,
        input_source="foreground",
    )


def save(store: MemoryStore, text: str, *, operation_id: str = "save") -> Any:
    return store.mutate(
        "alice",
        action="add",
        target="user",
        content=text,
        kind="preference",
        operation_id=operation_id,
        base_revision=store.snapshot("alice").revision,
        source="user",
        source_refs=(SourceRef(kind="manual", source_id=operation_id, origin="owner"),),
    )


def test_new_confirmed_revision_refreshes_data_without_rewriting_snapshot(memory) -> None:
    store, _ = memory
    original = save(store, "Preferred language is Chinese.")
    frozen = store.render_for_injection("alice")
    prefix_hash = hashlib.sha256(frozen.encode()).hexdigest()
    store.mutate(
        "alice",
        action="replace",
        target="user",
        content="Preferred language is English.",
        record_id=original.record_id,
        operation_id="correction",
        base_revision=original.revision,
        source="user",
        source_refs=(SourceRef(kind="manual", source_id="correction", origin="owner"),),
    )
    block = asyncio.run(MemoryTurnContextProvider(store).build(request=request()))
    assert "Preferred language is English." in block
    assert '"catalog_revision":2' in block and '"confirmed":true' in block
    assert '"record_revision":1' in block
    assert "Preferred language is Chinese." not in block
    assert hashlib.sha256(frozen.encode()).hexdigest() == prefix_hash
    assert "Preferred language is Chinese." in frozen


def test_other_owner_and_other_project_are_not_recalled(memory) -> None:
    store, _ = memory
    save(store, "Alice personal preference.")
    store.add("alice", "project", "Private project language note.", project_id="other-project")
    own = asyncio.run(MemoryTurnContextProvider(store).build(request=request()))
    other = asyncio.run(MemoryTurnContextProvider(store).build(request=request("bob")))
    assert "Alice personal preference." in own
    assert "Private project language" not in own
    assert "Alice personal preference." not in other


def test_forgetting_emits_only_retired_identity_not_deleted_content(memory) -> None:
    store, _ = memory
    original = save(store, "A private preference.")
    store.mutate(
        "alice",
        action="remove",
        target="user",
        record_id=original.record_id,
        operation_id="forget",
        base_revision=original.revision,
        source="user",
    )
    block = asyncio.run(MemoryTurnContextProvider(store).build(request=request()))
    assert "A private preference." not in block
    assert original.record_id in block and '"retired_versions"' in block
    assert "not proof of deletion" in block


def test_disabled_memory_never_reads_store(memory, monkeypatch: pytest.MonkeyPatch) -> None:
    store, state = memory
    state["enabled"] = False

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Disabled memory must not be read")

    monkeypatch.setattr(store, "recall", unexpected)
    block = asyncio.run(MemoryTurnContextProvider(store).build(request=request()))
    assert 'status="disabled"' in block


def test_unavailable_is_explicit_and_does_not_disclose_failure_text(
    memory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _ = memory

    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise MemoryUnavailable("secret provider error")

    monkeypatch.setattr(store, "recall", unavailable)
    block = asyncio.run(MemoryTurnContextProvider(store).build(request=request()))
    assert 'status="unavailable"' in block
    assert "secret provider error" not in block
    assert "assume there are no remembered facts" in block

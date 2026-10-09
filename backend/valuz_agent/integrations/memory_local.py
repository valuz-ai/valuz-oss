"""OSS local authority adapter. Blocking catalog locks never run on the event loop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from valuz_agent.modules.memory.service import MemoryStore, memory_store
from valuz_agent.ports.memory import (
    MemoryError,
    MemoryInvalidation,
    MemoryMutationCommand,
    MemoryMutationResult,
    MemorySnapshot,
    SourceRef,
)


def _require_core(namespace: str) -> None:
    from valuz_agent.ports.extensions import ext

    if not ext.memory_namespace_policy.is_registered(namespace):
        raise MemoryError("Unregistered memory namespace")


class LocalMemoryBackend:
    def __init__(self, store: MemoryStore | None = None) -> None:
        self._store = store or memory_store

    @asynccontextmanager
    async def storage_guard(self, user_id: str) -> AsyncIterator[Path]:
        """Exclusive local maintenance guard; blocking locks stay off the event loop."""
        context = self._store.storage_guard(user_id)
        acquisition = asyncio.create_task(asyncio.to_thread(context.__enter__))
        try:
            root = await asyncio.shield(acquisition)
        except asyncio.CancelledError:
            # A cancelled to_thread still acquires later: wait and release it.
            while not acquisition.done():
                try:
                    await asyncio.shield(acquisition)
                except asyncio.CancelledError:
                    continue
            acquisition.result()
            release = asyncio.create_task(asyncio.to_thread(context.__exit__, None, None, None))
            while not release.done():
                try:
                    await asyncio.shield(release)
                except asyncio.CancelledError:
                    continue
            release.result()
            raise
        try:
            yield root
        finally:
            await asyncio.to_thread(context.__exit__, None, None, None)

    async def operation_receipt_by_hash(
        self, user_id: str, operation_hash: str, expected_fingerprint: str | None = None
    ) -> MemoryMutationResult | None:
        return await asyncio.to_thread(
            self._store.operation_receipt_by_hash, user_id, operation_hash, expected_fingerprint
        )

    def mutation_fingerprint(self, command: MemoryMutationCommand) -> str:
        return self._store.mutation_fingerprint(command)

    def forget_fingerprint(
        self,
        source_ref: SourceRef,
        base_revision: int,
        authority_id: str | None = None,
        authority_epoch: int | None = None,
    ) -> str:
        return self._store.forget_fingerprint(
            source_ref, base_revision, authority_id, authority_epoch
        )

    async def snapshot(self, user_id: str, *, namespace: str = "core") -> MemorySnapshot:
        _require_core(namespace)
        return await asyncio.to_thread(self._store.snapshot, user_id, namespace=namespace)

    async def recall(
        self,
        user_id: str,
        query: str = "",
        *,
        project_id: str | None = None,
        object_refs: tuple[str, ...] = (),
        limit: int = 8,
        max_chars: int = 4000,
        namespace: str = "core",
    ) -> MemorySnapshot:
        _require_core(namespace)
        return await asyncio.to_thread(
            self._store.recall,
            user_id,
            query,
            project_id=project_id,
            object_refs=object_refs,
            limit=limit,
            max_chars=max_chars,
            namespace=namespace,
        )

    async def mutate(
        self,
        user_id: str,
        command: MemoryMutationCommand,
        *,
        namespace_access: object | None = None,
    ) -> MemoryMutationResult:
        _require_core(command.namespace)
        from valuz_agent.ports.memory_namespaces import authorize_namespace_write

        approval = await authorize_namespace_write(
            user_id, command, namespace_access, self._store.mutation_fingerprint(command)
        )
        return await asyncio.to_thread(
            self._store.mutate,
            user_id,
            action=command.action,
            target=command.target,
            operation_id=command.operation_id,
            base_revision=command.base_revision,
            record_id=command.record_id,
            content=command.content,
            project_id=command.project_id,
            source=command.source,
            source_refs=command.source_refs,
            kind=command.kind,
            namespace=command.namespace,
            object_refs=command.object_refs,
            observed_at=command.observed_at,
            authority_id=command.authority_id,
            authority_epoch=command.authority_epoch,
            namespace_approval=approval,
        )

    async def forget_source(
        self,
        user_id: str,
        source_ref: SourceRef,
        *,
        operation_id: str,
        base_revision: int,
        namespace: str = "core",
        authority_id: str | None = None,
        authority_epoch: int | None = None,
    ) -> MemoryMutationResult:
        _require_core(namespace)
        return await asyncio.to_thread(
            self._store.forget_source,
            user_id,
            source_ref,
            operation_id=operation_id,
            base_revision=base_revision,
            authority_id=authority_id,
            authority_epoch=authority_epoch,
        )

    async def invalidated_ids(
        self,
        user_id: str,
        *,
        project_id: str | None = None,
        namespace: str = "core",
    ) -> tuple[MemoryInvalidation, ...]:
        _require_core(namespace)
        return await asyncio.to_thread(
            self._store.invalidated_ids, user_id, project_id=project_id, namespace=namespace
        )

    async def sources_forgotten(
        self,
        user_id: str,
        source_refs: tuple[SourceRef, ...],
        *,
        namespace: str = "core",
    ) -> tuple[SourceRef, ...]:
        _require_core(namespace)
        return await asyncio.to_thread(self._store.sources_forgotten, user_id, source_refs)

    async def operation_receipt(
        self,
        user_id: str,
        operation_id: str,
        *,
        namespace: str = "core",
        expected_fingerprint: str | None = None,
    ) -> MemoryMutationResult | None:
        _require_core(namespace)
        return await asyncio.to_thread(
            self._store.operation_receipt, user_id, operation_id, expected_fingerprint
        )

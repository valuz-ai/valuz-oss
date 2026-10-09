"""Async memory authority facade for HTTP, tools, turns and background maintenance.

Owner and namespace are explicit. The OSS default wraps the existing local store;
a bound/custom backend is never silently replaced when it fails.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from valuz_agent.ports.memory import (
    MemoryBackendPort,
    MemoryError,
    MemoryInvalidation,
    MemoryKind,
    MemoryMutationCommand,
    MemoryMutationResult,
    MemoryProtected,
    MemoryRecord,
    MemorySnapshot,
    MemoryUnavailable,
    MutationAction,
    Source,
    SourceRef,
    Target,
)
from valuz_agent.ports.memory_maintenance import MemoryMaintenanceScope


class MemoryLibrary:
    def __init__(
        self,
        user_id: str,
        *,
        backend: MemoryBackendPort | None = None,
        namespace: str = "core",
        namespace_access: object | None = None,
    ) -> None:
        if not user_id or not user_id.strip():
            raise MemoryError("user_id is required")
        if not namespace.strip():
            raise MemoryError("namespace is required")
        self.user_id = user_id
        self.namespace = namespace
        self._namespace_access = namespace_access
        if backend is None:
            from valuz_agent.ports.extensions import ext

            backend = ext.memory_backend
        if backend is None:
            from valuz_agent.integrations.memory_local import LocalMemoryBackend

            backend = LocalMemoryBackend()
        self._backend = backend

    async def _verified(self, snapshot: MemorySnapshot) -> MemorySnapshot:
        if snapshot.owner_user_id != self.user_id:
            raise MemoryProtected("Memory backend returned a different owner")
        if any(record.namespace != self.namespace for record in snapshot.records):
            raise MemoryProtected("Memory backend returned records outside the namespace")
        from valuz_agent.ports.extensions import ext

        await ext.memory_namespace_policy.authorize_read(self.user_id, self.namespace, snapshot)
        return snapshot

    @staticmethod
    async def recall_enabled(user_id: str) -> bool:
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.settings.preferences import get_memory_enabled

        async with async_unit_of_work(commit=False) as db:
            return await get_memory_enabled(db, user_id=user_id)

    async def snapshot(self) -> MemorySnapshot:
        return await self._verified(
            await self._backend.snapshot(self.user_id, namespace=self.namespace)
        )

    async def list_records(
        self,
        target: Target | None = None,
        *,
        project_id: str | None = None,
        source_ref: SourceRef | None = None,
    ) -> tuple[MemoryRecord, ...]:
        if target is not None and target not in ("user", "global", "project"):
            raise MemoryError("Invalid memory target")
        if target == "project" and not project_id:
            raise MemoryError("project memory requires a project context")
        if target in ("user", "global") and project_id is not None:
            raise MemoryError("Only project memory has project_id")
        snapshot = await self.snapshot()
        return tuple(
            record
            for record in snapshot.records
            if (target is None or record.target == target)
            and (project_id is None or record.project_id == project_id)
            and (
                source_ref is None
                or any(
                    ref.kind == source_ref.kind and ref.source_id == source_ref.source_id
                    for ref in record.source_refs
                )
            )
        )

    async def recall(
        self,
        query: str = "",
        *,
        project_id: str | None = None,
        object_refs: tuple[str, ...] = (),
        limit: int = 8,
        max_chars: int = 4000,
    ) -> MemorySnapshot:
        return await self._verified(
            await self._backend.recall(
                self.user_id,
                query,
                project_id=project_id,
                object_refs=object_refs,
                limit=limit,
                max_chars=max_chars,
                namespace=self.namespace,
            )
        )

    async def mutate(
        self,
        *,
        action: MutationAction,
        target: Target,
        operation_id: str,
        base_revision: int,
        authority_id: str | None = None,
        authority_epoch: int | None = None,
        record_id: str | None = None,
        content: str | None = None,
        project_id: str | None = None,
        source: Source = "agent",
        source_refs: tuple[SourceRef, ...] = (),
        kind: MemoryKind | None = None,
        object_refs: tuple[str, ...] | None = None,
        observed_at: float | None = None,
    ) -> MemoryMutationResult:
        try:
            command = MemoryMutationCommand(
                action=action,
                target=target,
                authority_id=authority_id,
                authority_epoch=authority_epoch,
                operation_id=operation_id,
                base_revision=base_revision,
                record_id=record_id,
                content=content,
                project_id=project_id,
                source=source,
                source_refs=source_refs,
                kind=kind,
                namespace=self.namespace,
                object_refs=object_refs,
                observed_at=observed_at,
            )
        except ValidationError as exc:
            raise MemoryError("Invalid memory mutation") from exc
        from valuz_agent.ports.extensions import ext

        await ext.memory_namespace_policy.authorize_write(
            self.user_id, command, self._namespace_access
        )
        if self._namespace_access is None:
            result = await self._backend.mutate(self.user_id, command)
        else:
            result = await self._backend.mutate(
                self.user_id, command, namespace_access=self._namespace_access
            )
        if source == "auto":
            return result
        from valuz_agent.ports.memory_maintenance import MemoryMaintenanceScope

        return await self._cleanup(
            result,
            source_refs=source_refs,
            scope=MemoryMaintenanceScope(
                target=target, project_id=project_id, namespace=self.namespace
            ),
        )

    async def forget_source(
        self,
        source_ref: SourceRef,
        *,
        operation_id: str,
        base_revision: int,
        authority_id: str | None = None,
        authority_epoch: int | None = None,
    ) -> MemoryMutationResult:
        result = await self._backend.forget_source(
            self.user_id,
            source_ref,
            operation_id=operation_id,
            base_revision=base_revision,
            namespace=self.namespace,
            authority_id=authority_id,
            authority_epoch=authority_epoch,
        )
        return await self._cleanup(result, source_refs=(source_ref,))

    async def _cleanup(
        self,
        result: MemoryMutationResult,
        *,
        source_refs: tuple[SourceRef, ...] = (),
        scope: MemoryMaintenanceScope | None = None,
    ) -> MemoryMutationResult:
        from valuz_agent.ports.extensions import ext

        try:
            async with asyncio.timeout(5):
                cleanup = await ext.memory_maintenance.purge_after_mutation(
                    owner_user_id=self.user_id,
                    revision=result.revision,
                    affected_ids=result.affected_ids,
                    source_refs=source_refs,
                    scope=scope,
                )
        except Exception:
            return result.model_copy(
                update={
                    "maintenance_complete": False,
                    "maintenance_reason_code": "memory.cleanup_unavailable",
                }
            )
        return result.model_copy(
            update={
                "maintenance_complete": result.maintenance_complete and cleanup.complete,
                "maintenance_reason_code": result.maintenance_reason_code or cleanup.reason_code,
            }
        )

    async def invalidated_ids(
        self, *, project_id: str | None = None
    ) -> tuple[MemoryInvalidation, ...]:
        return await self._backend.invalidated_ids(
            self.user_id,
            project_id=project_id,
            namespace=self.namespace,
        )

    async def sources_forgotten(self, source_refs: tuple[SourceRef, ...]) -> tuple[SourceRef, ...]:
        return await self._backend.sources_forgotten(
            self.user_id, source_refs, namespace=self.namespace
        )

    async def operation_receipt(
        self, operation_id: str, *, expected_fingerprint: str | None = None
    ) -> MemoryMutationResult | None:
        if expected_fingerprint is None:
            return await self._backend.operation_receipt(
                self.user_id, operation_id, namespace=self.namespace
            )
        return await self._backend.operation_receipt(
            self.user_id,
            operation_id,
            namespace=self.namespace,
            expected_fingerprint=expected_fingerprint,
        )

    async def read_entries(self, target: Target, *, project_id: str | None = None) -> list[str]:
        return [
            record.content
            for record in await self.list_records(
                target,
                project_id=project_id if target == "project" else None,
            )
        ]

    async def render_for_injection(self, *, project_id: str | None = None) -> str:
        from valuz_agent.modules.memory.service import render_snapshot_for_injection

        return render_snapshot_for_injection(
            await self.snapshot(), project_id=project_id, namespace=self.namespace
        )

    async def _legacy(
        self,
        action: MutationAction,
        target: Target,
        *,
        content: str | None = None,
        old_text: str | None = None,
        project_id: str | None = None,
        source: Source = "agent",
        source_refs: tuple[SourceRef, ...] = (),
        base_revision: int | None = None,
    ) -> dict[str, Any]:
        from valuz_agent.modules.memory.service import MemoryStore

        project_id = project_id if target == "project" else None
        before = await self.snapshot()
        record_id = None
        try:
            if source == "auto" and action != "add" and base_revision is None:
                raise MemoryProtected("Automatic replacement/deletion requires a base snapshot")
            if old_text is not None:
                if not old_text.strip():
                    raise MemoryError("old_text cannot be empty")
                matches = [
                    record
                    for record in before.records
                    if record.target == target
                    and record.project_id == project_id
                    and old_text.strip() in record.content
                ]
                if len(matches) != 1:
                    raise MemoryError(
                        "no entry matched" if not matches else "multiple entries matched"
                    )
                record_id = matches[0].id
            receipt = await self.mutate(
                action=action,
                target=target,
                operation_id=uuid4().hex,
                base_revision=before.revision if base_revision is None else base_revision,
                authority_id=before.authority_id,
                authority_epoch=before.authority_epoch,
                record_id=record_id,
                content=content,
                project_id=project_id,
                source=source,
                source_refs=source_refs,
            )
        except MemoryUnavailable:
            raise
        except MemoryError as exc:
            current = await self.snapshot()
            entries = [
                record.content
                for record in current.records
                if record.target == target and record.project_id == project_id
            ]
            return {
                "success": False,
                "error": str(exc),
                "error_code": exc.error_code,
                "revision": current.revision,
                "current_entries": entries,
                "matches": [entry[:80] for entry in entries],
            }
        current = await self.snapshot()
        entries = [
            record.content
            for record in current.records
            if record.target == target and record.project_id == project_id
        ]
        return {
            "success": True,
            "target": target,
            "entries": entries,
            "entry_count": len(entries),
            "usage": MemoryStore.usage_for(entries, target),
            "revision": current.revision,
            "mutation_revision": receipt.revision,
            "record_id": receipt.record_id,
            "message": "entry " + action,
        }

    async def add(
        self,
        target: Target,
        content: str,
        *,
        project_id: str | None = None,
        source: Source = "agent",
        source_refs: tuple[SourceRef, ...] = (),
        base_revision: int | None = None,
    ) -> dict[str, Any]:
        return await self._legacy(
            "add",
            target,
            content=content,
            project_id=project_id,
            source=source,
            source_refs=source_refs,
            base_revision=base_revision,
        )

    async def replace(
        self,
        target: Target,
        old_text: str,
        new_content: str,
        *,
        project_id: str | None = None,
        source: Source = "agent",
        source_refs: tuple[SourceRef, ...] = (),
        base_revision: int | None = None,
    ) -> dict[str, Any]:
        return await self._legacy(
            "replace",
            target,
            old_text=old_text,
            content=new_content,
            project_id=project_id,
            source=source,
            source_refs=source_refs,
            base_revision=base_revision,
        )

    async def remove(
        self,
        target: Target,
        old_text: str,
        *,
        project_id: str | None = None,
        source: Source = "agent",
        source_refs: tuple[SourceRef, ...] = (),
        base_revision: int | None = None,
    ) -> dict[str, Any]:
        return await self._legacy(
            "remove",
            target,
            old_text=old_text,
            project_id=project_id,
            source=source,
            source_refs=source_refs,
            base_revision=base_revision,
        )

    async def clear(
        self,
        target: Target,
        *,
        project_id: str | None = None,
        source: Source = "user",
        source_refs: tuple[SourceRef, ...] = (),
        base_revision: int | None = None,
    ) -> None:
        snapshot = await self.snapshot()
        if source == "auto" and base_revision is None:
            raise MemoryProtected("Automatic replacement/deletion requires a base snapshot")
        await self.mutate(
            action="clear",
            target=target,
            project_id=project_id if target == "project" else None,
            operation_id=uuid4().hex,
            base_revision=snapshot.revision if base_revision is None else base_revision,
            authority_id=snapshot.authority_id,
            authority_epoch=snapshot.authority_epoch,
            source=source,
            source_refs=source_refs,
        )

    async def drop_project(self, project_id: str) -> None:
        await self.clear("project", project_id=project_id, source="user")

"""Owner-scoped memory management, correction and deletion receipts.

The existing MemoryStore remains the authority. Modern mutations use stable
identities, compare-and-set revisions and host-derived manual source references.
Memory collection can be disabled without preventing inspection or deletion.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.facade.memory import MemoryLibrary
from valuz_agent.facade.projects import ProjectLibrary
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.memory import Target
from valuz_agent.modules.memory.models import (
    MemoryConflict,
    MemoryError,
    MemoryKind,
    MemoryMutationResult,
    MemoryProtected,
    MemoryRecord,
    MemoryUnavailable,
    SourceKind,
    SourceRef,
)
from valuz_agent.modules.settings.preferences import (
    get_memory_auto_extract,
    get_memory_custom_instructions,
    get_memory_enabled,
    set_memory_auto_extract,
    set_memory_custom_instructions,
    set_memory_enabled,
)

router = APIRouter(prefix="/v1/memory", tags=["memory"])


class MemoryView(BaseModel):
    enabled: bool
    auto_extract: bool
    # Global reviewer guidance appended to the background extractor prompt
    # (empty = off). See memory-system-design §7.4.
    custom_instructions: str
    # Entries per scope, keyed by target (user / global / project-when-bound).
    entries: dict[str, list[str]]


class MemorySettings(BaseModel):
    enabled: bool
    auto_extract: bool
    custom_instructions: str


class MemorySettingsPatch(BaseModel):
    enabled: bool | None = None
    auto_extract: bool | None = None
    custom_instructions: str | None = None


class MemoryEntryDelete(BaseModel):
    target: Target
    old_text: str
    project_id: str | None = None


class MemoryClear(BaseModel):
    target: Target
    project_id: str | None = None


_CONTEXT_NOTICE = (
    "Saved revisions do not rewrite existing session instructions. Forgetting memory "
    "does not delete original conversations, files or information already loaded into a session."
)


class MemoryRevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=128)
    base_revision: int = Field(ge=0)
    authority_id: str | None = Field(default=None, min_length=1, max_length=128)
    authority_epoch: int | None = Field(default=None, ge=0, strict=True)


class MemoryRecordAdd(MemoryRevisionInput):
    target: Target
    content: str = Field(min_length=1, max_length=4000)
    project_id: str | None = None
    kind: MemoryKind = "fact"


class MemoryRecordForget(MemoryRevisionInput):
    target: Target
    project_id: str | None = None


class MemorySourceForget(MemoryRevisionInput):
    kind: SourceKind
    source_id: str = Field(min_length=1, max_length=256)


class MemoryRecordCorrection(MemoryRevisionInput):
    content: str = Field(min_length=1, max_length=4000)


class MemoryRecordsPage(BaseModel):
    authority_id: str | None = None
    authority_epoch: int | None = None
    revision: int
    records: tuple[MemoryRecord, ...]
    total: int
    offset: int
    limit: int
    context_notice: str = _CONTEXT_NOTICE


class MemoryMutationReceipt(MemoryMutationResult):
    context_notice: str = _CONTEXT_NOTICE


async def _store_call[T](function: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
    # The facade resolves one authority and never falls back after a binding failure.
    try:
        return await function(*args, **kwargs)
    except MemoryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.error_code, "message": "Memory authority is unavailable"},
        ) from exc
    except MemoryConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MemoryProtected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except MemoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _require_owned_project(user_id: str, project_id: str | None) -> None:
    if project_id and await ProjectLibrary().get(user_id, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")


async def _require_collection(user_id: str) -> None:
    async with async_unit_of_work(commit=False) as db:
        if not await get_memory_enabled(db, user_id=user_id):
            raise HTTPException(status_code=403, detail="Memory collection is disabled")


async def _record(user_id: str, record_id: str) -> MemoryRecord:
    snapshot = await _store_call(MemoryLibrary(user_id).snapshot)
    for record in snapshot.records:
        if record.id == record_id:
            await _require_owned_project(user_id, record.project_id)
            return record
    raise HTTPException(status_code=404, detail="Memory record not found")


def _manual_source(operation_id: str) -> tuple[SourceRef, ...]:
    # This endpoint explicitly submits content; callers cannot inject trusted
    # provenance, owner identities or namespace grants in the request body.
    return (SourceRef(kind="manual", source_id=operation_id, origin="owner"),)


@router.get("/records", operation_id="listMemoryRecords")
async def list_memory_records(
    project_id: str | None = None,
    target: Target | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    user_id: str = Depends(get_current_user_id),
) -> MemoryRecordsPage:
    await _require_owned_project(user_id, project_id)
    if target == "project" and not project_id:
        raise HTTPException(status_code=422, detail="Project memory requires a project")
    snapshot = await _store_call(MemoryLibrary(user_id).snapshot)
    records = tuple(
        record
        for record in snapshot.records
        if (record.target != "project" or record.project_id == project_id)
        and (target is None or record.target == target)
    )
    return MemoryRecordsPage(
        authority_id=snapshot.authority_id,
        authority_epoch=snapshot.authority_epoch,
        revision=snapshot.revision,
        records=records[offset : offset + limit],
        total=len(records),
        offset=offset,
        limit=limit,
    )


@router.post("/records", operation_id="addMemoryRecord")
async def add_memory_record(
    payload: MemoryRecordAdd,
    user_id: str = Depends(get_current_user_id),
) -> MemoryMutationReceipt:
    await _require_collection(user_id)
    await _require_owned_project(user_id, payload.project_id)
    result = await _store_call(
        MemoryLibrary(user_id).mutate,
        action="add",
        target=payload.target,
        content=payload.content,
        kind=payload.kind,
        project_id=payload.project_id,
        operation_id=payload.operation_id,
        base_revision=payload.base_revision,
        authority_id=payload.authority_id,
        authority_epoch=payload.authority_epoch,
        source="user",
        source_refs=_manual_source(payload.operation_id),
    )
    return MemoryMutationReceipt(**result.model_dump())


@router.patch("/records/{record_id}", operation_id="correctMemoryRecord")
async def correct_memory_record(
    record_id: str,
    payload: MemoryRecordCorrection,
    user_id: str = Depends(get_current_user_id),
) -> MemoryMutationReceipt:
    await _require_collection(user_id)
    record = await _record(user_id, record_id)
    result = await _store_call(
        MemoryLibrary(user_id).mutate,
        action="replace",
        target=record.target,
        record_id=record_id,
        content=payload.content,
        project_id=record.project_id,
        operation_id=payload.operation_id,
        base_revision=payload.base_revision,
        authority_id=payload.authority_id,
        authority_epoch=payload.authority_epoch,
        source="user",
        source_refs=_manual_source(payload.operation_id),
    )
    return MemoryMutationReceipt(**result.model_dump())


@router.delete("/records/{record_id}", operation_id="forgetMemoryRecord")
async def forget_memory_record(
    record_id: str,
    payload: MemoryRecordForget,
    user_id: str = Depends(get_current_user_id),
) -> MemoryMutationReceipt:
    await _require_owned_project(user_id, payload.project_id)
    snapshot = await _store_call(MemoryLibrary(user_id).snapshot)
    record = next((item for item in snapshot.records if item.id == record_id), None)
    if record is not None and (
        record.target != payload.target or record.project_id != payload.project_id
    ):
        raise HTTPException(status_code=422, detail="Record scope does not match")
    try:
        result = await _store_call(
            MemoryLibrary(user_id).mutate,
            action="remove",
            target=payload.target,
            record_id=record_id,
            project_id=payload.project_id,
            operation_id=payload.operation_id,
            base_revision=payload.base_revision,
            authority_id=payload.authority_id,
            authority_epoch=payload.authority_epoch,
            source="user",
            source_refs=_manual_source(payload.operation_id),
        )
    except HTTPException as exc:
        if record is None and exc.status_code == 422:
            raise HTTPException(status_code=404, detail="Memory record not found") from exc
        raise
    return MemoryMutationReceipt(**result.model_dump())


@router.post("/sources/forget", operation_id="forgetMemorySource")
async def forget_memory_source(
    payload: MemorySourceForget,
    user_id: str = Depends(get_current_user_id),
) -> MemoryMutationReceipt:
    source = SourceRef(kind=payload.kind, source_id=payload.source_id)
    snapshot = await _store_call(MemoryLibrary(user_id).snapshot)
    for record in snapshot.records:
        if any(
            ref.kind == source.kind and ref.source_id == source.source_id
            for ref in record.source_refs
        ):
            await _require_owned_project(user_id, record.project_id)
    result = await _store_call(
        MemoryLibrary(user_id).forget_source,
        source,
        operation_id=payload.operation_id,
        base_revision=payload.base_revision,
        authority_id=payload.authority_id,
        authority_epoch=payload.authority_epoch,
    )
    return MemoryMutationReceipt(**result.model_dump())


async def _view(project_id: str | None, user_id: str) -> MemoryView:
    await _require_owned_project(user_id, project_id)
    async with async_unit_of_work(commit=False) as db:
        enabled = await get_memory_enabled(db, user_id=user_id)
        auto_extract = await get_memory_auto_extract(db, user_id=user_id)
        custom_instructions = await get_memory_custom_instructions(db, user_id=user_id)
    entries: dict[str, list[str]] = {
        "user": await _store_call(MemoryLibrary(user_id).read_entries, "user"),
        "global": await _store_call(MemoryLibrary(user_id).read_entries, "global"),
    }
    if project_id:
        entries["project"] = await _store_call(
            MemoryLibrary(user_id).read_entries, "project", project_id=project_id
        )
    return MemoryView(
        enabled=enabled,
        auto_extract=auto_extract,
        custom_instructions=custom_instructions,
        entries=entries,
    )


@router.get("")
async def get_memory(
    project_id: str | None = None,
    user_id: str = Depends(get_current_user_id),
) -> MemoryView:
    """Return the memory toggles + current entries per scope."""
    return await _view(project_id, user_id)


@router.get("/settings", operation_id="getMemorySettings")
async def get_memory_settings(
    user_id: str = Depends(get_current_user_id),
) -> MemorySettings:
    async with async_unit_of_work(commit=False) as db:
        return MemorySettings(
            enabled=await get_memory_enabled(db, user_id=user_id),
            auto_extract=await get_memory_auto_extract(db, user_id=user_id),
            custom_instructions=await get_memory_custom_instructions(db, user_id=user_id),
        )


@router.patch("/settings")
async def patch_memory_settings(
    payload: MemorySettingsPatch,
    user_id: str = Depends(get_current_user_id),
) -> MemorySettings:
    """Toggle memory on/off. Only sent keys are updated."""
    async with async_unit_of_work() as db:
        if payload.enabled is not None:
            await set_memory_enabled(db, payload.enabled, user_id=user_id)
        if payload.auto_extract is not None:
            await set_memory_auto_extract(db, payload.auto_extract, user_id=user_id)
        if payload.custom_instructions is not None:
            # Setter trims + hard-caps; sending "" disables the directives.
            await set_memory_custom_instructions(db, payload.custom_instructions, user_id=user_id)
        return MemorySettings(
            enabled=await get_memory_enabled(db, user_id=user_id),
            auto_extract=await get_memory_auto_extract(db, user_id=user_id),
            custom_instructions=await get_memory_custom_instructions(db, user_id=user_id),
        )


@router.delete("/entry")
async def delete_memory_entry(
    payload: MemoryEntryDelete,
    user_id: str = Depends(get_current_user_id),
) -> MemoryView:
    """Delete the entry located by a unique ``old_text`` substring in ``target``."""
    await _require_owned_project(user_id, payload.project_id)
    try:
        result = await _store_call(
            MemoryLibrary(user_id).remove,
            payload.target,
            payload.old_text,
            project_id=payload.project_id,
            source="user",
        )
    except MemoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=str(result.get("error", "no match")))
    return await _view(payload.project_id, user_id)


@router.delete("/scope")
async def clear_memory_scope(
    payload: MemoryClear,
    user_id: str = Depends(get_current_user_id),
) -> MemoryView:
    """Clear every entry in ``target``."""
    await _require_owned_project(user_id, payload.project_id)
    try:
        await _store_call(
            MemoryLibrary(user_id).clear, payload.target, project_id=payload.project_id
        )
    except MemoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _view(payload.project_id, user_id)

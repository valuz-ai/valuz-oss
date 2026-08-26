"""Local backup routes — /v1/backup (docs/design/client-local-backup.md §7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.backup.schemas import (
    BackupConfigPatch,
    BackupConfigResponse,
    BackupFileListResponse,
    BackupRestoreRequest,
    BackupRestoreResponse,
    BackupRunProgress,
    BackupRunResponse,
    BackupVersionDetail,
    BackupVersionListResponse,
)
from valuz_agent.modules.backup.service import backup_service

router = APIRouter(prefix="/v1/backup", tags=["backup"])


@router.get("/config")
async def get_backup_config(
    user_id: str = Depends(get_current_user_id),
) -> BackupConfigResponse:
    async with async_unit_of_work(commit=False) as db:
        return await backup_service.get_config(db, user_id)


@router.put("/config")
async def put_backup_config(
    payload: BackupConfigPatch,
    user_id: str = Depends(get_current_user_id),
) -> BackupConfigResponse:
    async with async_unit_of_work() as db:
        return await backup_service.patch_config(db, user_id, payload)


@router.post("/runs", status_code=202)
async def run_backup_now(
    user_id: str = Depends(get_current_user_id),
) -> BackupRunResponse:
    return await backup_service.start_manual_run(user_id)


@router.get("/runs/current")
async def get_current_run(
    user_id: str = Depends(get_current_user_id),
) -> BackupRunProgress:
    return backup_service.progress()


@router.get("/versions")
async def list_backup_versions(
    user_id: str = Depends(get_current_user_id),
) -> BackupVersionListResponse:
    async with async_unit_of_work(commit=False) as db:
        return await backup_service.list_versions(db, user_id)


@router.get("/versions/{version_id}")
async def get_backup_version(
    version_id: str,
    user_id: str = Depends(get_current_user_id),
) -> BackupVersionDetail:
    async with async_unit_of_work(commit=False) as db:
        return await backup_service.get_version(db, user_id, version_id)


@router.delete("/versions/{version_id}", status_code=204)
async def delete_backup_version(
    version_id: str,
    user_id: str = Depends(get_current_user_id),
) -> None:
    async with async_unit_of_work(commit=False) as db:
        await backup_service.delete_version(db, user_id, version_id)


@router.get("/versions/{version_id}/files")
async def list_backup_files(
    version_id: str,
    path: str = "",
    user_id: str = Depends(get_current_user_id),
) -> BackupFileListResponse:
    async with async_unit_of_work(commit=False) as db:
        return await backup_service.list_files(db, user_id, version_id, path)


@router.get("/versions/{version_id}/files/download")
async def download_backup_file(
    version_id: str,
    path: str,
    user_id: str = Depends(get_current_user_id),
) -> FileResponse:
    async with async_unit_of_work(commit=False) as db:
        resolved = await backup_service.resolve_file(db, user_id, version_id, path)
    return FileResponse(resolved, filename=resolved.name)


@router.post("/versions/{version_id}/restore")
async def restore_backup_version(
    version_id: str,
    payload: BackupRestoreRequest,
    user_id: str = Depends(get_current_user_id),
) -> BackupRestoreResponse:
    async with async_unit_of_work(commit=False) as db:
        return await backup_service.request_restore(
            db, user_id, version_id, dry_run=payload.dry_run
        )

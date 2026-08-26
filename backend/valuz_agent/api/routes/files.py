"""File address resolution endpoint.

``POST /v1/files/resolve`` exchanges a batch of ``valuz-file://<abs>`` URIs for
access-address descriptors: ``kind=local`` (absolute path, read client-side) or
``kind=remote`` (presigned URL). The backend never returns file bytes here — the
client fetches from the returned address. See
``docs/design/file-address-resolution.md``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.modules.files.service import assert_owned, owner_allowed_roots, stat_meta
from valuz_agent.modules.files.uri import parse_valuz_file_uri
from valuz_agent.ports.file_address import get_file_address_resolver

router = APIRouter(prefix="/v1/files", tags=["files"])

# Guard against pathological batches (each ref does a stat). Rejected loudly
# rather than silently truncated.
MAX_REFS = 256

_COPYABLE = {"markdown", "code", "html", "plain"}


class ResolveRequest(BaseModel):
    refs: list[str]


class FileCapabilities(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    can_preview: bool = Field(serialization_alias="canPreview")
    can_download: bool = Field(serialization_alias="canDownload")
    can_open_external: bool = Field(serialization_alias="canOpenExternal")
    can_copy_content: bool = Field(serialization_alias="canCopyContent")


class ResolvedFileDescriptor(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ref: str
    kind: str  # "local" | "remote" | "" (error)
    abs_path: str | None = Field(default=None, serialization_alias="absPath")
    url: str | None = None
    expires_at: int | None = Field(default=None, serialization_alias="expiresAt")
    name: str
    mime_type: str | None = Field(default=None, serialization_alias="mimeType")
    size: int | None = None
    revision: str | None = None
    exists: bool
    preview_kind: str = Field(serialization_alias="previewKind")
    capabilities: FileCapabilities
    error: str | None = None  # "invalid_ref" | "forbidden" | "not_found"


class ResolveResponse(BaseModel):
    results: list[ResolvedFileDescriptor]


_NO_CAPS = FileCapabilities(
    can_preview=False, can_download=False, can_open_external=False, can_copy_content=False
)


def _error(ref: str, error: str) -> ResolvedFileDescriptor:
    # Do not leak name/existence for invalid or forbidden refs.
    return ResolvedFileDescriptor(
        ref=ref, kind="", name="", exists=False, preview_kind="unsupported",
        capabilities=_NO_CAPS, error=error,
    )


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_files(
    body: ResolveRequest,
    user_id: str = Depends(get_current_user_id),
) -> ResolveResponse:
    if len(body.refs) > MAX_REFS:
        raise HTTPException(status_code=422, detail=f"too many refs (max {MAX_REFS})")

    roots = await owner_allowed_roots(user_id)
    results = [await _resolve_one(ref, user_id, roots) for ref in body.refs]
    return ResolveResponse(results=results)


async def _resolve_one(ref: str, user_id: str, roots: list[Path]) -> ResolvedFileDescriptor:
    try:
        raw_path = parse_valuz_file_uri(ref)
    except ValueError:
        return _error(ref, "invalid_ref")

    try:
        real = assert_owned(Path(raw_path), roots)
    except PermissionError:
        return _error(ref, "forbidden")

    meta = stat_meta(real)

    try:
        addr = await get_file_address_resolver().to_address(
            owner_user_id=user_id, abs_path=real
        )
    except PermissionError:
        # Storage-side boundary rejection (overlay defense-in-depth).
        return _error(ref, "forbidden")

    return ResolvedFileDescriptor(
        ref=ref,
        kind=addr.kind,
        abs_path=str(addr.abs_path) if addr.abs_path is not None else None,
        url=addr.url,
        expires_at=addr.expires_at,
        name=meta.name,
        mime_type=meta.mime_type,
        size=meta.size,
        revision=meta.revision,
        exists=meta.exists,
        preview_kind=meta.preview_kind,
        capabilities=FileCapabilities(
            can_preview=meta.exists and meta.preview_kind != "unsupported",
            can_download=meta.exists,
            # Only meaningful for local files; the client further gates by
            # whether it is Electron (see design §9.2).
            can_open_external=addr.kind == "local",
            can_copy_content=meta.exists and meta.preview_kind in _COPYABLE,
        ),
        error=None if meta.exists else "not_found",
    )


__all__ = ["router"]

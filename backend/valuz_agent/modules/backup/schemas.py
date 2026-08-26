"""Backup module — Pydantic request/response schemas.

Wire shapes for ``/v1/backup/*`` (api/openapi.yaml is the contract source of
truth). The on-disk manifest/summary shapes live in ``manifest.py`` — they are
a durable disk format, not an HTTP contract, and evolve independently.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

BackupFrequency = Literal["manual", "every_6h", "daily", "weekly"]

FREQUENCY_INTERVAL_MS: dict[str, int] = {
    "every_6h": 6 * 60 * 60 * 1000,
    "daily": 24 * 60 * 60 * 1000,
    "weekly": 7 * 24 * 60 * 60 * 1000,
}


class BackupScope(BaseModel):
    """User-tunable inclusion switches. App data (DBs, docs derivatives,
    memories, attachments, managed KB roots) is always included and has no
    switch; ``secrets/`` is always excluded (plaintext credentials)."""

    managed_projects: bool = True
    external_projects: bool = False
    user_skills: bool = True


class BackupRetention(BaseModel):
    # Most-recent versions kept unconditionally.
    keep_recent: int = Field(default=7, ge=1, le=100)
    # Beyond keep_recent: one version per calendar day is kept for this many
    # days (a simplified GFS ladder); the rest are pruned.
    keep_daily_days: int = Field(default=56, ge=0, le=3650)
    # Total size cap; oldest versions pruned first, but the most recent
    # successful version is never deleted. 0 disables the cap.
    max_total_gb: int = Field(default=20, ge=0, le=10_000)


class BackupLastRun(BaseModel):
    at: int | None = None  # epoch ms
    status: Literal["ok", "failed", "skipped_no_change"] | None = None
    error: str | None = None
    version_id: str | None = None


class BackupRunProgress(BaseModel):
    running: bool = False
    phase: Literal["preflight", "db", "files", "summary", "finalize"] | None = None
    started_at: int | None = None  # epoch ms
    processed_bytes: int = 0
    trigger: Literal["manual", "scheduled"] | None = None


class BackupConfigResponse(BaseModel):
    supported: bool
    unsupported_reason: str | None = None
    enabled: bool
    frequency: BackupFrequency
    destination: str
    scope: BackupScope
    retention: BackupRetention
    last_run: BackupLastRun
    next_run_at: int | None = None  # epoch ms; null when manual/disabled
    run: BackupRunProgress
    versions_count: int
    total_bytes: int
    restore_result: dict[str, Any] | None = None  # boot restore report, if any


class BackupConfigPatch(BaseModel):
    enabled: bool | None = None
    frequency: BackupFrequency | None = None
    destination: str | None = Field(default=None, min_length=1)
    scope: BackupScope | None = None
    retention: BackupRetention | None = None


class BackupSummaryCounts(BaseModel):
    sessions: int = 0
    messages: int = 0
    projects: int = 0
    agents: int = 0
    skills: int = 0
    knowledge_bases: int = 0
    documents: int = 0
    automations: int = 0


class BackupVersionInfo(BaseModel):
    id: str
    created_at: int  # epoch ms
    kind: Literal["scheduled", "manual", "pre_restore"]
    total_bytes: int
    new_bytes: int  # bytes actually copied (not deduped against prior version)
    file_count: int
    duration_ms: int
    app_version: str | None = None
    counts: BackupSummaryCounts


class BackupVersionDetail(BackupVersionInfo):
    host_alembic: str | None = None
    kernel_alembic: str | None = None
    scope: BackupScope
    dedup: Literal["hardlink", "none"]
    # KB source files recorded at backup time (indexed in-place, never copied):
    # count + how many were missing on disk already when the backup ran.
    kb_source_count: int = 0
    kb_source_missing: int = 0


class BackupVersionListResponse(BaseModel):
    versions: list[BackupVersionInfo]


class BackupFileEntry(BaseModel):
    name: str
    path: str  # version-relative, always "/"-separated
    kind: Literal["dir", "file", "link"]
    size: int = 0


class BackupFileListResponse(BaseModel):
    path: str
    entries: list[BackupFileEntry]


class BackupRunResponse(BaseModel):
    started: bool
    run: BackupRunProgress


class BackupRestoreRequest(BaseModel):
    dry_run: bool = False


class BackupRestorePlanEntry(BaseModel):
    target: str
    action: Literal["replace", "create"]
    bytes: int


class BackupRestoreResponse(BaseModel):
    staged: bool
    requires_restart: bool
    plan: list[BackupRestorePlanEntry] = []

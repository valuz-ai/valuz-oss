"""Backup module exceptions — error code segment ``91x``."""

from valuz_agent.infra.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
)

# ── 400 ────────────────────────────────────────────────────────────────


class BackupDestinationInvalid(BadRequestError):
    error_code = 400_911
    message = "Backup destination is invalid"


# ── 404 ────────────────────────────────────────────────────────────────


class BackupVersionNotFound(NotFoundError):
    error_code = 404_911
    message = "Backup version not found"


class BackupFileNotFound(NotFoundError):
    error_code = 404_912
    message = "File not found in backup version"


# ── 409 ────────────────────────────────────────────────────────────────


class BackupAlreadyRunning(ConflictError):
    error_code = 409_911
    message = "A backup is already running"


class BackupRestoreConflict(ConflictError):
    error_code = 409_912
    message = "Backup version cannot be restored"


# ── 422 ────────────────────────────────────────────────────────────────


class BackupNotSupported(UnprocessableEntityError):
    error_code = 422_911
    message = "Backup is not supported for this deployment"


class BackupPreflightFailed(UnprocessableEntityError):
    error_code = 422_912
    message = "Backup preflight check failed"

"""Typed errors of third-party plugins (docs task card 04 §B).

Every error carries a stable string ``code`` next to the numeric ``error_code`` of
the shared ``ValuzError`` bases. The routes render them as
``{"detail": {"code", "message", ...}}`` (plus ``errors`` where a list of problems
helps the caller: manifest and config validation); in-process callers (the
``extension_manager`` tool, overlay code) read ``.code`` / ``.errors`` directly.
"""

from __future__ import annotations

from typing import Any

from valuz_agent.infra.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnprocessableEntityError,
    ValuzError,
)


class ThirdPartyError(ValuzError):
    #: Stable machine-readable code (what clients branch on).
    code: str = "third_party_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        errors: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.errors: list[str] = list(errors or [])
        self.extra: dict[str, Any] = dict(extra or {})

    def detail(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.errors:
            body["errors"] = self.errors
        body.update(self.extra)
        return body


class ThirdPartyUnavailable(ForbiddenError, ThirdPartyError):
    """Third-party plugins only exist on a local deployment (ADR-034)."""

    error_code = 403_901
    code = "third_party_unavailable"
    message = "Third-party plugins are only available on a local deployment"


class InvalidManifest(BadRequestError, ThirdPartyError):
    error_code = 400_901
    code = "invalid_manifest"
    message = "The plugin manifest or package is invalid"


class InvalidSource(BadRequestError, ThirdPartyError):
    """The source cannot be read as a plugin package (not a zip, unsafe archive,
    download failed, path missing)."""

    error_code = 400_902
    code = "invalid_source"
    message = "The plugin source cannot be read"


class SourceTooLarge(BadRequestError, ThirdPartyError):
    status_code = 413
    error_code = 413_901
    code = "source_too_large"
    message = "The plugin package is too large"


class NotDevPlugin(BadRequestError, ThirdPartyError):
    error_code = 400_903
    code = "not_a_dev_plugin"
    message = "Only a dev-linked plugin can be reloaded"


class InvalidConfig(BadRequestError, ThirdPartyError):
    error_code = 400_904
    code = "invalid_config"
    message = "The plugin settings do not match the plugin's config schema"


class InvalidStorageKey(BadRequestError, ThirdPartyError):
    error_code = 400_905
    code = "invalid_storage_key"
    message = "Storage keys must be 1-200 characters"


class InvalidAutomationInput(UnprocessableEntityError, ThirdPartyError):
    error_code = 422_902
    code = "invalid_input"
    message = "The input does not match the automation's input schema"


class VersionNotNewer(ConflictError, ThirdPartyError):
    error_code = 409_901
    code = "version_not_newer"
    message = "The installed version is not older than this package"


class AutomationBusy(ConflictError, ThirdPartyError):
    error_code = 409_902
    code = "automation_already_running"
    message = "The automation already has an active run"


class Sha256Mismatch(UnprocessableEntityError, ThirdPartyError):
    error_code = 422_901
    code = "sha256_mismatch"
    message = "The package does not match the expected sha256"


class PluginNotFound(NotFoundError, ThirdPartyError):
    error_code = 404_901
    code = "not_found"
    message = "Plugin not found"


class StorageKeyNotFound(NotFoundError, ThirdPartyError):
    error_code = 404_902
    code = "not_found"
    message = "Storage key not found"


class PluginAutomationNotFound(NotFoundError, ThirdPartyError):
    error_code = 404_903
    code = "automation_not_found"
    message = "The plugin declares no such automation"


class PluginRunNotFound(NotFoundError, ThirdPartyError):
    error_code = 404_904
    code = "run_not_found"
    message = "No such run for this plugin's automations"


class StorageQuotaExceeded(ThirdPartyError):
    status_code = 413
    error_code = 413_902
    code = "storage_quota_exceeded"
    message = "The plugin storage quota is exceeded"


__all__ = [
    "AutomationBusy",
    "InvalidAutomationInput",
    "InvalidConfig",
    "InvalidManifest",
    "InvalidSource",
    "InvalidStorageKey",
    "NotDevPlugin",
    "PluginAutomationNotFound",
    "PluginNotFound",
    "PluginRunNotFound",
    "Sha256Mismatch",
    "SourceTooLarge",
    "StorageKeyNotFound",
    "StorageQuotaExceeded",
    "ThirdPartyError",
    "ThirdPartyUnavailable",
    "VersionNotNewer",
]

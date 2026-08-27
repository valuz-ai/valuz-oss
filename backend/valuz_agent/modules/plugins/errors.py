"""Plugin module errors (module code 75)."""

from __future__ import annotations

from valuz_agent.infra.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
    ValuzError,
)


class PluginNotFound(NotFoundError):
    error_code = 404_751
    message = "Plugin not found"


class PluginInvalid(UnprocessableEntityError):
    """The plugin manifest is fatally invalid (Agent Plugins §5.2/§5.3) or the
    source does not contain a plugin at all."""

    error_code = 422_751
    message = "Invalid plugin"


class PluginConflict(ConflictError):
    """A same-name plugin from a different source is already installed."""

    error_code = 409_751
    message = "A plugin with this name is already installed from another source"


class PluginNotDeletable(ConflictError):
    """A builtin (app-managed) plugin cannot be uninstalled — disable it instead."""

    error_code = 409_752
    message = "Builtin plugins cannot be uninstalled; disable the plugin instead"


class PluginInstallFailed(BadRequestError):
    """Bad archive / caps exceeded / unusable source path."""

    error_code = 400_751
    message = "Plugin install failed"


class PluginSourceUnavailable(BadRequestError):
    """Update requested for a plugin whose source cannot be re-fetched (e.g. a
    one-off zip upload)."""

    error_code = 400_752
    message = "Plugin source cannot be re-fetched"


class PluginFetchFailed(ValuzError):
    """The plugin archive could not be downloaded (url / market source)."""

    status_code = 502
    error_code = 502_751
    message = "Plugin download failed"

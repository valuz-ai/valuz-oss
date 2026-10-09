"""Errors raised by the backend plugin host."""

from __future__ import annotations


class PluginHostError(Exception):
    """Base class for every plugin-host error."""


class DuplicatePluginError(PluginHostError):
    """Two plugins were registered under the same id."""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id
        super().__init__(f"plugin {plugin_id!r} is already registered")


class PluginGraphError(PluginHostError):
    """The ``needs`` / ``provides`` graph cannot be ordered."""


class MissingNeedError(PluginGraphError):
    """A plugin needs a service that no plugin provides and the base lacks."""

    def __init__(self, plugin_id: str, need: str) -> None:
        self.plugin_id = plugin_id
        self.need = need
        super().__init__(f"plugin {plugin_id!r} needs {need!r}, which nothing provides")


class PluginCycleError(PluginGraphError):
    """The ``needs`` / ``provides`` graph contains a cycle."""

    def __init__(self, members: list[str]) -> None:
        self.members = members
        super().__init__("plugin dependency cycle among: " + ", ".join(sorted(members)))


class PluginStartupError(PluginHostError):
    """A *required* plugin failed to apply. The original error is ``__cause__``."""

    def __init__(self, plugin_id: str, reason: str) -> None:
        self.plugin_id = plugin_id
        self.reason = reason
        super().__init__(f"required plugin {plugin_id!r} failed to start: {reason}")


class UnknownPortError(PluginHostError):
    """``ports.bind`` named a port that the extension container does not have."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"no extension port named {name!r}")


class InvalidPluginError(PluginHostError, TypeError):
    """An object offered as a plugin does not satisfy the plugin contract."""

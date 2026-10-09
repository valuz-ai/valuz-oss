"""The backend plugin host the running process composed.

Whoever composes the app through a :class:`PluginHost` (the commercial overlay
does; a bare OSS app composes directly and registers none) records it here,
so management surfaces (``/v1/builtin-plugins``) can show what is loaded,
failed or disabled without importing the composing package.
"""

from __future__ import annotations

from valuz_agent.plugin_host.host import PluginHost

_active: PluginHost | None = None


def set_active_plugin_host(host: PluginHost | None) -> None:
    global _active
    _active = host


def active_plugin_host() -> PluginHost | None:
    return _active

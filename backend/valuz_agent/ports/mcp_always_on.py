"""Edition-registered always-on internal MCP servers.

The five built-in always-on servers (docs / automations / playbooks /
connectors / harness) are hardcoded in ``adapters/capability_resolver``. Editions need the
same channel for their own domain tools (e.g. finance thesis/binding tools)
without forking the resolver: they append a spec here, carrying both the path
and the ASGI app to serve on it.

The resolver builds the full ``McpHttpServerConfig`` itself — URL from the
backend base + ``{path}/mcp``, plus the same internal credential headers and
tool timeout as the built-ins — so editions never handle the sandbox
credential. List semantics: editions append, they do not replace; reserved
built-in names are skipped defensively.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

# Model-visible server names are hyphenated, the spelling every catalog
# connector already uses (``valuz-data``, ``valuz-search``, ``valuz-following``).
# The built-ins used to be spelled with underscores, and living with both
# conventions cost real turns: models reached for the hyphen at the underscore
# servers — ``mcp__valuz-automations__automation``,
# ``mcp__valuz-finance__automation_output`` — six wrong tool names in three
# production days (valuz/valuz#13). One separator, nothing left to guess.
RESERVED_ALWAYS_ON_NAMES = frozenset(
    {"valuz-docs", "valuz-automations", "valuz-playbooks", "valuz-connectors", "harness"}
)


# Which built-in servers this process actually mounts. The plugin host decides: a
# disabled feature (``oss-automations``, ``oss-knowledge``, ...) mounts nothing, and
# sessions must not be handed a server whose URL 404s. ``None`` -- no composed app yet
# (unit tests, embedding callers) -- means "all of them", the pre-plugin behaviour.
_enabled_builtin_servers: frozenset[str] | None = None


def set_enabled_builtin_servers(names: Iterable[str] | None) -> None:
    """Record the built-in server names the composed app mounts (``create_app``)."""
    global _enabled_builtin_servers
    _enabled_builtin_servers = None if names is None else frozenset(names)


def builtin_server_enabled(name: str) -> bool:
    """Whether the built-in always-on server ``name`` is mounted in this process."""
    return _enabled_builtin_servers is None or name in _enabled_builtin_servers


def retired_always_on_name(name: str) -> str:
    """Pre-rename spelling of an always-on server name.

    Session rows stamped before the rename still carry the underscore name.
    The re-stamp in ``modules/sessions/capabilities`` preserves entries it does
    not recognise (that is how a user's own external MCPs survive), so without
    this the old entry would sit alongside the new one and the same server
    would be registered twice. Deriving the old spelling rather than listing it
    covers edition-registered names (``valuz-finance``) for free.
    """
    return name.replace("-", "_")


__all__ = [
    "AlwaysOnMcpServerSpec",
    "RESERVED_ALWAYS_ON_NAMES",
    "builtin_server_enabled",
    "retired_always_on_name",
    "set_enabled_builtin_servers",
]


@dataclass(frozen=True)
class AlwaysOnMcpServerSpec:
    """One edition-owned always-on MCP server.

    ``name`` is the model-visible server name (tools appear as
    ``mcp__{name}__*``); ``path`` is the internal ASGI mount path WITHOUT the
    trailing ``/mcp`` (e.g. ``/_internal/mcp/finance/base``).

    ``app_factory`` builds the ASGI app to serve there. Supplying it lets
    ``create_app`` mount the server through the SAME seam as the built-ins
    (``api/app.py::_mount_internal``), i.e. under every configured
    ``api_prefix`` base path rather than only at the root — which is what the
    URL above actually resolves to whenever ``backend_base_url`` carries an
    ingress sub-path. Declaring the path and the app together is the point: an
    edition that mounts by hand elsewhere can (and did) leave the advertised
    URL pointing at a path nothing serves. Left ``None`` for editions that
    still mount their own app in ``EditionApplication.register_api``; those are
    responsible for covering every base path themselves.
    """

    name: str
    path: str
    app_factory: Callable[[], object] | None = None

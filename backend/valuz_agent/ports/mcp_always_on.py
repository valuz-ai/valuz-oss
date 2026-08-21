"""Edition-registered always-on internal MCP servers.

The four built-in always-on servers (docs / automations / connectors /
harness) are hardcoded in ``adapters/capability_resolver``. Editions need the
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

from collections.abc import Callable
from dataclasses import dataclass

RESERVED_ALWAYS_ON_NAMES = frozenset(
    {"valuz_docs", "valuz_automations", "valuz_connectors", "harness"}
)

__all__ = ["AlwaysOnMcpServerSpec", "RESERVED_ALWAYS_ON_NAMES"]


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

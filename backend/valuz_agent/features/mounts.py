"""The OSS host's internal mounts: the DataService and the always-on MCP servers.

One definition per mount, owned (registered) by one feature plugin. Factories and
session managers are lazy refs so a disabled plugin never imports its server.

``server`` is the model-visible server name sessions are handed for a built-in mount
(``adapters/capability_resolver.always_on_http_mcp_servers`` advertises only the ones
whose mount is composed); the dsh bridge instead carries an ``AlwaysOnMcpServerSpec``
and is advertised like any edition server.
"""

from __future__ import annotations

from typing import Any

from valuz_agent.plugin_host.registry import InternalMount

_INT = "valuz_agent.integrations"
_ME = "valuz_agent.features.mounts"

DOCS = InternalMount(
    name="docs",
    path="/_internal/mcp/docs",
    build=f"{_INT}.docs_mcp_server:build_docs_mcp_asgi",
    session_manager=f"{_INT}.docs_mcp_server:docs_mcp_session_manager_run",
    server="valuz-docs",
)

# Host-mounted DataService (kernel three-table CRUD over /rpc/{op}). Mounted as a
# sub-app; its store + JWT verifier are bound in the lifespan
# (``steps.bind_data_service``) once the backend is known. A sandbox kernel reaches
# this over HTTP+JWT instead of holding a DB credential. /health + /openapi.json work
# pre-bind; /rpc is 401 until bound.
DATA = InternalMount(
    name="data", path="/_internal/data", build=f"{_ME}:build_data_service", takes_app=True
)

AUTOMATIONS = InternalMount(
    name="automations",
    path="/_internal/mcp/automations",
    build=f"{_INT}.automations_mcp_server:build_automations_mcp_asgi",
    session_manager=f"{_INT}.automations_mcp_server:automations_mcp_session_manager_run",
    server="valuz-automations",
)

PLAYBOOKS = InternalMount(
    name="playbooks",
    path="/_internal/mcp/playbooks",
    build=f"{_INT}.playbooks_mcp_server:build_playbooks_mcp_asgi",
    session_manager=f"{_INT}.playbooks_mcp_server:playbooks_mcp_session_manager_run",
    server="valuz-playbooks",
)

CONNECTORS = InternalMount(
    name="connectors",
    path="/_internal/mcp/connectors",
    build=f"{_INT}.connectors_mcp_server:build_connectors_mcp_asgi",
    session_manager=f"{_INT}.connectors_mcp_server:connectors_mcp_session_manager_run",
    server="valuz-connectors",
)

# The harness tools, per toolset. One session manager serves both toolsets, so it is
# attached to the first mount only.
TOOLKIT_BASE = InternalMount(
    name="toolkit-base",
    path="/_internal/mcp/toolkit/base",
    build=f"{_ME}:build_toolkit_base",
    session_manager=f"{_INT}.toolkit_mcp_server:toolkit_mcp_session_managers_run",
    server="harness",
)
TOOLKIT_LEAD = InternalMount(
    name="toolkit-lead", path="/_internal/mcp/toolkit/lead", build=f"{_ME}:build_toolkit_lead"
)

DSH_SESSION_MANAGER = (
    "valuz_agent.modules.dsh_plugins.mcp_server:dsh_plugins_mcp_session_manager_run"
)


def build_data_service(app: Any) -> Any:
    from valuz_agent.boot.kernel import make_data_service_placeholder

    # ``make_data_service_placeholder`` is untyped in boot/kernel.py (as it was when
    # this lived in ``create_app``).
    app.state.data_service_app = make_data_service_placeholder()  # type: ignore[no-untyped-call]
    return app.state.data_service_app


def build_toolkit_base() -> Any:
    from valuz_agent.integrations.toolkit_mcp_server import build_toolkit_mcp_asgi

    return build_toolkit_mcp_asgi("base")


def build_toolkit_lead() -> Any:
    from valuz_agent.integrations.toolkit_mcp_server import build_toolkit_mcp_asgi

    return build_toolkit_mcp_asgi("lead")


def dsh_plugins_mount() -> InternalMount:
    """The dsh plugin bridge: the resident dsh manager host's tool bridge, fronted as
    an always-on MCP server for every non-dsh runtime (local workstations only).
    dsh sessions load the plugins natively and skip it."""
    from valuz_agent.modules.dsh_plugins.mcp_server import (
        MOUNT_PATH,
        SERVER_NAME,
        build_dsh_plugins_mcp_asgi,
    )
    from valuz_agent.ports.mcp_always_on import AlwaysOnMcpServerSpec

    return InternalMount(
        name="dsh-plugins",
        path=MOUNT_PATH,
        spec=AlwaysOnMcpServerSpec(
            name=SERVER_NAME, path=MOUNT_PATH, app_factory=build_dsh_plugins_mcp_asgi
        ),
        session_manager=DSH_SESSION_MANAGER,
    )


def default_session_managers() -> list[str]:
    """The built-in MCP session managers, for callers that start them without a
    composed host (see ``steps.start_mcp_session_managers``)."""
    managers = [
        m.session_manager
        for m in (DOCS, AUTOMATIONS, PLAYBOOKS, CONNECTORS, TOOLKIT_BASE)
        if m.session_manager
    ]
    from valuz_agent.modules.dsh_plugins.manager import manager_enabled

    if manager_enabled():
        managers.append(DSH_SESSION_MANAGER)
    return [str(m) for m in managers]

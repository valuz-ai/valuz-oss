"""Canonical composition order of the OSS host's own contributions.

The hard-coded ``create_app`` / lifespan these replaced had one order, and parts of
it are load-bearing (route precedence where prefixes overlap, startup ordering --
see ``boot/phases.py``). Plugins only *register*; the order of what they register
lives here, in one place, and is pinned by ``tests/plugin_host/snapshots``.

* ``ROUTE_REFS``   slot -> router, in the order the aggregate ``api`` router includes
                   them. The dict order IS the canonical order. Routes of overlay
                   plugins (``module_registry``) always follow these.
* ``MOUNT_SLOTS``  the internal mounts, in mount order (``features/mounts.py``)
* ``TOOL_SLOTS``   the harness tool groups, in toolset order (``features/toolkit.py``)

Order matters between two slots only where their paths overlap (parser routes
extend ``/v1/system`` and ``/v1/settings`` so they follow those routers); otherwise
it is kept because "identical to before" is the cheapest thing to prove.
"""

from __future__ import annotations

_ROUTES = "valuz_agent.api.routes"

ROUTE_REFS: dict[str, str] = {
    "providers": f"{_ROUTES}.providers:router",
    "channels": f"{_ROUTES}.channels:router",
    "citations": f"{_ROUTES}.citations:router",
    "document_research": f"{_ROUTES}.document_research:router",
    "feedback": f"{_ROUTES}.feedback:router",
    "connectors": f"{_ROUTES}.connectors:router",
    "browser": f"{_ROUTES}.browser:router",
    "runs": f"{_ROUTES}.runs:router",
    "activity": f"{_ROUTES}.activity:router",
    "runtimes": f"{_ROUTES}.runtimes:router",
    "system": f"{_ROUTES}.system:router",
    "projects": f"{_ROUTES}.projects:router",
    "files": f"{_ROUTES}.files:router",
    "artifacts": f"{_ROUTES}.artifacts:router",
    "worktrees": f"{_ROUTES}.worktrees:router",
    "sessions": f"{_ROUTES}.sessions:router",
    # Attachments live outside ``/v1/sessions``: a file uploads before any
    # session exists and is bound by the turn that ships it.
    "attachments": f"{_ROUTES}.sessions:attachments_router",
    "stream": f"{_ROUTES}.stream:router",
    # The UI bus (plugins draw element trees into UI slots) — system-level,
    # owned by ``oss-core`` like the event stream it pushes on.
    "plugin_ui": f"{_ROUTES}.plugin_ui:router",
    "skills": f"{_ROUTES}.skills:router",
    "docs": f"{_ROUTES}.docs:router",
    "automations": f"{_ROUTES}.automations:router",
    "playbooks": f"{_ROUTES}.playbooks:router",
    "operations": f"{_ROUTES}.operations:router",
    "backup": f"{_ROUTES}.backup:router",
    "notifications": f"{_ROUTES}.notifications:router",
    "agents": f"{_ROUTES}.agents:router",
    "agent_templates": f"{_ROUTES}.agent_templates:router",
    "marketplace": f"{_ROUTES}.marketplace:router",
    "plugins": f"{_ROUTES}.plugins:router",
    "dsh_plugins": f"{_ROUTES}.dsh_plugins:router",
    "builtin_plugins": f"{_ROUTES}.builtin_plugins:router",
    # Application plugin management and immutable frontend assets.
    "app_plugins": f"{_ROUTES}.app_plugins:router",
    "app_plugin_assets": f"{_ROUTES}.app_plugins:assets_router",
    "tasks": f"{_ROUTES}.tasks:router",
    "analytics": f"{_ROUTES}.analytics:router",
    "resources": f"{_ROUTES}.resources:router",
    "onboarding": f"{_ROUTES}.onboarding:router",
    "settings": f"{_ROUTES}.settings:router",
    "memory": f"{_ROUTES}.memory:router",
    # Parser routes live in a separate module because they straddle the
    # ``/v1/system`` and ``/v1/settings`` namespaces (setup jobs vs. routing
    # config): two ``APIRouter`` instances that must follow ``system`` / ``settings``.
    "parser_system": f"{_ROUTES}.parser:system_router",
    "parser_settings": f"{_ROUTES}.parser:settings_router",
}

ROUTE_SLOTS: tuple[str, ...] = tuple(ROUTE_REFS)

#: Internal mounts, in mount order. ``data`` sits between ``docs`` and ``automations``
#: because that is where the DataService mount has always been.
MOUNT_SLOTS: tuple[str, ...] = (
    "docs",
    "data",
    "automations",
    "playbooks",
    "connectors",
    "toolkit-base",
    "toolkit-lead",
    "dsh-plugins",
)

#: Harness tool groups. Orchestration / dispatch groups build the per-kind part of
#: the ``base`` / ``lead`` toolset; the rest are shared by both, in this order.
TOOL_SLOTS: tuple[str, ...] = (
    "tasks-orchestration",
    "tasks-dispatch",
    "memory",
    "project-instructions",
    "submit-skill",
    "agent-proposal",
    "project",
    "skill-library",
    "plugin",
    "deliver-artifacts",
    "citation-calculation",
    "genui",
    "browser",
    "app-plugin-manager",
)

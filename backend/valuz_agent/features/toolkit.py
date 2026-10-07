"""Harness toolkit tool groups.

The ``harness`` MCP server (``integrations/toolkit_mcp_server``) serves two toolsets:
``base`` (every session) = orchestration launchers + the shared tools, ``lead`` (task
leads) = the dispatch set + the shared tools. Which tools exist used to be decided
inside ``steps.init_kernel``; each feature now contributes its own group, so a
disabled plugin's tools are not served.

Builders are lazy (``"module:attr"`` refs) and return a tuple of tool defs. The kernel
``ToolDef`` type is deliberately not imported here: tool defs are opaque to the host.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from valuz_agent.features.order import TOOL_SLOTS
from valuz_agent.plugin_host.registry import ToolGroup, resolve_ref

logger = logging.getLogger(__name__)

_ME = "valuz_agent.features.toolkit"

TOOL_GROUPS: dict[str, ToolGroup] = {
    g.slot: g
    for g in (
        ToolGroup("orchestration", "tasks-orchestration", f"{_ME}:orchestration_tool_defs"),
        ToolGroup("dispatch", "tasks-dispatch", f"{_ME}:dispatch_tool_defs"),
        ToolGroup("shared", "memory", f"{_ME}:memory_tool_defs"),
        ToolGroup("shared", "project-instructions", f"{_ME}:project_instructions_tool_defs"),
        ToolGroup("shared", "submit-skill", f"{_ME}:submit_skill_tool_defs"),
        ToolGroup("shared", "agent-proposal", f"{_ME}:agent_proposal_tool_defs"),
        # Entity management (agent/UI parity): projects, skill library, plugins.
        ToolGroup("shared", "project", f"{_ME}:project_tool_defs"),
        ToolGroup("shared", "skill-library", f"{_ME}:skill_library_tool_defs"),
        ToolGroup("shared", "plugin", f"{_ME}:plugin_tool_defs"),
        ToolGroup("shared", "deliver-artifacts", f"{_ME}:deliver_artifacts_tool_defs"),
        ToolGroup("shared", "citation-calculation", f"{_ME}:citation_calculation_tool_defs"),
        ToolGroup("shared", "genui", f"{_ME}:genui_tool_defs"),
        ToolGroup("shared", "browser", f"{_ME}:browser_tool_defs"),
        ToolGroup("shared", "app-plugin-manager", f"{_ME}:app_plugin_manager_tool_defs"),
    )
}
assert tuple(TOOL_GROUPS) == TOOL_SLOTS, "TOOL_GROUPS must follow features.order.TOOL_SLOTS"


def all_tool_groups() -> list[ToolGroup]:
    """Every group the OSS features define, canonical order (all features enabled)."""
    return [TOOL_GROUPS[slot] for slot in TOOL_SLOTS]


def compose_toolsets(groups: Sequence[Any]) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """``(base, lead)`` tool defs from the composed groups, in the order given."""
    parts: dict[str, list[Any]] = {"orchestration": [], "dispatch": [], "shared": []}
    for group in groups:
        parts[group.group].extend(resolve_ref(group.build)())
    shared = tuple(parts["shared"])
    return tuple(parts["orchestration"]) + shared, tuple(parts["dispatch"]) + shared


# -- builders -------------------------------------------------------------------


def _task_defs_by_name() -> dict[str, Any]:
    from valuz_agent.modules.tasks.orchestrator import task_orchestrator
    from valuz_agent.modules.tasks.tools.handlers import build_task_tool_defs

    return {t.name: t for t in build_task_tool_defs(task_orchestrator)}


def orchestration_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.modules.tasks.tools.declarations import ORCHESTRATION_TOOL_DECLARATIONS

    by_name = _task_defs_by_name()
    return tuple(by_name[d.name] for d in ORCHESTRATION_TOOL_DECLARATIONS if d.name in by_name)


def dispatch_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.modules.tasks.tools.declarations import DISPATCH_TOOL_DECLARATIONS

    by_name = _task_defs_by_name()
    return tuple(by_name[d.name] for d in DISPATCH_TOOL_DECLARATIONS if d.name in by_name)


def memory_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.modules.memory.tools import build_memory_tool_defs

    return tuple(build_memory_tool_defs())


def project_instructions_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.modules.projects.tools import build_project_instructions_tool_defs

    return tuple(build_project_instructions_tool_defs())


def submit_skill_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.integrations.tools_skill_creator import build_submit_skill_tool_defs

    return tuple(build_submit_skill_tool_defs())


def agent_proposal_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.integrations.tools_agent_proposal import build_agent_proposal_tool_defs

    return tuple(build_agent_proposal_tool_defs())


def project_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.modules.projects.tools import build_project_tool_defs

    return tuple(build_project_tool_defs())


def skill_library_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.integrations.tools_skill_library import build_skill_library_tool_defs

    return tuple(build_skill_library_tool_defs())


def plugin_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.integrations.tools_plugin import build_plugin_tool_defs

    return tuple(build_plugin_tool_defs())


def deliver_artifacts_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.modules.sessions.artifacts_tool import build_deliver_artifacts_tool_defs

    return tuple(build_deliver_artifacts_tool_defs())


def citation_calculation_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.modules.citations.calculation_tool import (
        build_citation_calculation_tool_defs,
    )

    return tuple(build_citation_calculation_tool_defs())


def genui_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.modules.genui.tools import build_generative_ui_tool_defs

    return tuple(build_generative_ui_tool_defs())


def browser_tool_defs() -> tuple[Any, ...]:
    """``browser_start`` / ``browser_stop`` -- only when the bound engine can run the
    daemon (local: Node + chrome-devtools-mcp on this host; a remote-sandbox engine
    declares it for its image); don't expose dead tools otherwise (e.g. headless/TUI
    without Node). See docs/design/browser-feature.md §8/§9.

    Also does the engine's boot setup now, before any session spawns its agent
    subprocess (which inherits env at spawn time): the local engine installs the
    friendly ``chrome-devtools`` wrapper on PATH so the agent runs a clean
    ``chrome-devtools <tool>``.
    """
    from valuz_agent.boot.steps import _startup_user_content_enabled
    from valuz_agent.ports.extensions import ext

    if not ext.browser_engine.available():
        logger.info("browser engine unavailable — browser_start/browser_stop not registered")
        return ()
    from valuz_agent.modules.browser.tools import build_browser_tool_defs

    defs = tuple(build_browser_tool_defs())
    if not _startup_user_content_enabled():
        logger.info(
            "startup user-content initialization disabled; browser engine bootstrap skipped"
        )
    else:
        ext.browser_engine.bootstrap()
    return defs


def app_plugin_manager_tool_defs() -> tuple[Any, ...]:
    from valuz_agent.integrations.tools_app_plugin_manager import (
        build_app_plugin_manager_tool_defs,
    )

    return tuple(build_app_plugin_manager_tool_defs())

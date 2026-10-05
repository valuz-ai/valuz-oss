"""``oss-core`` -- the shell every other feature stands on (required).

The routes of the shell (providers, runtimes, system, settings, onboarding,
resources, analytics), of projects / sessions / files / artifacts / worktrees /
stream / runs, and of the extensions API; the DataService and the ``harness``
toolkit MCP servers; the project / entity / artifact / genui harness tools; and the
process lifecycle that is not any one feature's: the data-dir guard, logging,
identity, schema, i18n, the kernel, session recovery, the decision inbox and
teardown.
"""

from __future__ import annotations

from typing import Any

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features import mounts
from valuz_agent.features._base import OssPlugin, routes, shutdown, startup, step
from valuz_agent.features.order import TOOL_SLOTS
from valuz_agent.features.toolkit import TOOL_GROUPS
from valuz_agent.plugin_host import PluginContext
from valuz_agent.plugin_host.registry import BootStep, HostRegistry


def _init_kernel(registry: HostRegistry) -> BootStep:
    """``init_kernel`` with the tool groups the composed plugins contributed."""

    def run(app: Any) -> Any:
        from valuz_agent.boot import steps

        groups = [e.tool for e in registry.tool_groups(TOOL_SLOTS)]
        return steps.init_kernel(app, toolkit=groups)

    return BootStep("init_kernel", run, takes_app=True)


def _start_mcp_session_managers(registry: HostRegistry) -> BootStep:
    """The MCP session managers of the mounts the composed plugins registered."""
    from valuz_agent.features.order import MOUNT_SLOTS

    def run(app: Any) -> Any:
        from valuz_agent.boot import steps

        managers = [
            e.mount.session_manager
            for e in registry.mounts(MOUNT_SLOTS)
            if e.mount.session_manager is not None
        ]
        return steps.start_mcp_session_managers(app, managers)

    return BootStep("start_mcp_session_managers", run, takes_app=True)


class CorePlugin(OssPlugin):
    id = "oss-core"
    required = True
    needs = ()
    provides = ("oss.core",)

    def register(self, ctx: PluginContext) -> None:
        routes(
            ctx,
            "providers",
            "runs",
            "runtimes",
            "system",
            "projects",
            "files",
            "artifacts",
            "worktrees",
            "sessions",
            "attachments",
            "stream",
            "plugin_ui",
            "extensions",
            "analytics",
            "resources",
            "onboarding",
            "settings",
        )
        ctx.internal_mounts.mount(mounts.DATA)
        ctx.internal_mounts.mount(mounts.TOOLKIT_BASE)
        ctx.internal_mounts.mount(mounts.TOOLKIT_LEAD)
        for slot in ("project-instructions", "project", "deliver-artifacts", "genui"):
            ctx.toolkit.add(TOOL_GROUPS[slot])

        startup(
            ctx,
            BootPhase.GUARD,
            step("guard_source_run_data_dir"),
            step("configure_structured_logging"),
            step("acquire_single_writer_lock"),
            BootStep(
                "start_parent_watchdog", "valuz_agent.boot.parent_watchdog:start_parent_watchdog"
            ),
            step("enrich_login_shell_path"),
            step("migrate_data_dir"),
            step("ensure_local_identity"),
        )
        startup(
            ctx,
            BootPhase.SCHEMA,
            step("bootstrap_schema"),
            step("configure_i18n"),
            step("colocate_kernel_history"),
        )
        startup(
            ctx,
            BootPhase.KERNEL,
            step("init_tracing"),
            _init_kernel(ctx.registry),
            step("bind_data_service", app=True),
        )
        startup(
            ctx,
            BootPhase.RECOVERY,
            step("recover_stranded_sessions"),
            step("resume_queued_input_drains"),
            step("seal_orphan_pendings"),
        )
        startup(
            ctx,
            BootPhase.RUNNERS,
            _start_mcp_session_managers(ctx.registry),
            step("warm_token_estimator"),
            step("start_decision_aggregator", app=True),
        )
        startup(ctx, BootPhase.READY, step("mark_boot_complete"))

        shutdown(
            ctx,
            BootPhase.DRAIN,
            BootStep("set_draining", "valuz_agent.infra.lifecycle:set_draining"),
        )
        shutdown(
            ctx,
            BootPhase.STOP_RUNNERS,
            step("stop_decision_aggregator", app=True),
            step("stop_mcp_session_managers", app=True),
        )
        shutdown(
            ctx,
            BootPhase.STOP_KERNEL,
            step("dispose_data_service", app=True),
            step("shutdown_kernel"),
            step("shutdown_tracing"),
            BootStep(
                "stop_parent_watchdog", "valuz_agent.boot.parent_watchdog:stop_parent_watchdog"
            ),
        )

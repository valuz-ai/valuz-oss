"""Boot phases and the canonical step order.

The process lifespan is no longer one hard-coded script: each feature plugin
registers the startup / shutdown steps it owns (``ctx.boot.startup(phase, step)``)
and ``boot/lifespan.py`` runs what the host collected. **This module is where the
order lives.** A step runs in its phase, at its position in the tables below; a step
a plugin registers that is not listed (an overlay's own) runs after the listed ones
of its phase, in registration order.

The order is load-bearing -- it is the order the lifespan has always had, pinned by
``tests/plugin_host/snapshots/boot.pre-refactor.json`` -- so every constraint that
used to sit as a comment in the script sits here, next to the step it constrains.
Do not reorder to "tidy up"; add a phase or a step and say why.

Sync steps are called directly, async steps are awaited; ``app`` is threaded to the
steps registered with ``takes_app=True`` (the ones that read / stash ``app.state``).
Shutdown mirrors startup in reverse, in coarser phases.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from valuz_agent.plugin_host.registry import BootStep, HostRegistry


class BootPhase(StrEnum):
    # -- startup -----------------------------------------------------------
    GUARD = "guard"  # before anything may write: data-dir guard, lock, identity
    SCHEMA = "schema"  # migrations + seeds, locale
    KERNEL = "kernel"  # tracing, kernel, DataService
    RECOVERY = "recovery"  # needs the kernel store
    RUNNERS = "runners"  # long-lived background services
    READY = "ready"  # the LAST blocking startup step
    POST_BOOT = "post-boot"  # background, never blocks readiness
    # -- shutdown ----------------------------------------------------------
    DRAIN = "drain"  # FIRST: stop starting new work
    STOP_RUNNERS = "stop-runners"  # reverse of RUNNERS / POST_BOOT
    STOP_KERNEL = "stop-kernel"  # reverse of GUARD..KERNEL


STARTUP_ORDER: Mapping[str, tuple[str, ...]] = {
    # The data-dir guard runs before EVERYTHING -- even logging config writes
    # under the data root, and a source-run backend must not touch the packaged
    # app's root at all.
    BootPhase.GUARD: (
        "guard_source_run_data_dir",  # FIRST
        "configure_structured_logging",
        "acquire_single_writer_lock",
        # Arm the parent-death watchdog as early as possible: a shell that dies
        # mid-boot must not leave an orphan holding the lock either. No-op unless
        # the spawner passed VALUZ_PARENT_PID (packaged desktop only).
        "start_parent_watchdog",
        # PATH enrichment runs before ANY step that resolves executables or hands
        # the environment to a child (kernel init registers tools via
        # ``node_available()``; stdio MCP children and the CLI login probe resolve
        # through this process's PATH). A Finder/launchd-launched backend only has
        # launchd's minimal PATH -- see ``boot/login_path.py``.
        "enrich_login_shell_path",
        # Data-dir cutover runs BEFORE identity: the owner id is read from the
        # migrated ``installation.json``, so it must be in place before
        # ``ensure_local_identity`` caches the id (else the cached id mismatches the
        # migrated rows' owner and breaks the official-skills reindex).
        "migrate_data_dir",
        # Staged backup restore applies here: after the data-dir cutover, before
        # identity caches the owner id and before any engine opens the SQLite
        # files (it replaces them at file level).
        "apply_backup_restore",
        "ensure_local_identity",  # seed owner ctx before any insert
    ),
    BootPhase.SCHEMA: (
        "bootstrap_schema",
        "configure_i18n",
        "colocate_kernel_history",  # seed valuz.db durable from kernel.db (one-time)
    ),
    BootPhase.KERNEL: (
        # Langfuse tracing BEFORE the kernel: the client + LangChain handler must
        # exist before the first turn runs. Env-gated no-op by default.
        "init_tracing",
        "init_kernel",
        "wire_memory_triggers",  # needs the toolkit + task events ``init_kernel`` set up
        "bind_data_service",
        "install_binding_change_listener",
    ),
    # recovery（依赖 kernel store 已就绪）
    BootPhase.RECOVERY: (
        "recover_stranded_sessions",
        "resume_queued_input_drains",
        "seal_orphan_pendings",
        # Must run BEFORE ``recover_active_tasks``: an orphaned task would otherwise
        # be respawned against a dead session and announced once per boot.
        "purge_tasks_of_deleted_projects",
        "recover_active_tasks",
        "resolve_informational_notification_backlog",
    ),
    # long-lived runners
    BootPhase.RUNNERS: (
        "start_mcp_session_managers",
        "start_automation_runtime",
        "start_task_health_monitor",
        "start_docs_auto_discovery",
        "start_skill_auto_scan",
        "start_backup_scheduler",
        "start_polling_scheduler",
        "warm_parse_pool",
        "warm_token_estimator",
        "resolve_marketplace_index",
        "start_skills",
        "start_decision_aggregator",
    ),
    # Registered last so every other startup step gets a chance to push a
    # ``record_warning(...)`` first -- anything that landed in the warnings buffer
    # turns ``status`` into ``degraded`` instead of ``running``.
    BootPhase.READY: ("mark_boot_complete",),  # LAST blocking startup step
    BootPhase.POST_BOOT: (
        "start_post_boot_agent_channels",
        "start_dsh_manager_if_plugins_installed",  # background, non-blocking
    ),
}

SHUTDOWN_ORDER: Mapping[str, tuple[str, ...]] = {
    # FIRST, before tearing anything down: flip the draining flag so the
    # long-lived task actor loops stop starting new turns and skip their
    # finalize. They then leave in-flight sessions ``running`` for boot recovery
    # to resume -- instead of racing the kernel-store / host-DB teardown below
    # (which otherwise spams "Dependencies not initialized" at every restart).
    BootPhase.DRAIN: ("set_draining",),
    BootPhase.STOP_RUNNERS: (
        "stop_managed_browser",
        "stop_dsh_manager",
        "stop_decision_aggregator",
        "stop_task_health_monitor",
        "stop_docs_auto_discovery",
        "stop_skill_auto_scan",
        "stop_backup_scheduler",
        "stop_agent_channels",
        "stop_skill_watcher",
        "stop_automation_runtime",
        "shutdown_parse_pool",
        "stop_polling_scheduler",
        "stop_mcp_session_managers",
    ),
    BootPhase.STOP_KERNEL: (
        "dispose_data_service",
        "shutdown_kernel",
        "shutdown_tracing",  # final span flush -- after every emitter stopped
        "stop_parent_watchdog",
    ),
}


@dataclass(frozen=True)
class BootPlan:
    """The ordered steps one app runs: what the host's registry holds, sorted."""

    startup: tuple[BootStep, ...]
    shutdown: tuple[BootStep, ...]

    @classmethod
    def from_registry(cls, registry: HostRegistry) -> BootPlan:
        return cls(
            startup=tuple(e.step for e in registry.steps("startup", STARTUP_ORDER)),
            shutdown=tuple(e.step for e in registry.steps("shutdown", SHUTDOWN_ORDER)),
        )

    @property
    def startup_names(self) -> list[str]:
        return [s.name for s in self.startup]

    @property
    def shutdown_names(self) -> list[str]:
        return [s.name for s in self.shutdown]

"""Process lifespan — the single explicit, ordered startup/shutdown script.

The startup order is load-bearing (see the order table in the boot-refactor
exec plan). Sync steps are called directly; async steps are awaited. ``app`` is
threaded through to the steps that read/stash ``app.state``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from valuz_agent.boot import parent_watchdog, steps


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ── startup（顺序 load-bearing，注释分组）──
    # The data-dir guard runs before EVERYTHING — even logging config writes
    # under the data root, and a source-run backend must not touch the
    # packaged app's root at all.
    steps.guard_source_run_data_dir()  # FIRST
    steps.configure_structured_logging()
    steps.acquire_single_writer_lock()
    # Arm the parent-death watchdog as early as possible: a shell that dies
    # mid-boot must not leave an orphan holding the lock either. No-op unless
    # the spawner passed VALUZ_PARENT_PID (packaged desktop only).
    parent_watchdog.start_parent_watchdog()
    # Data-dir cutover runs BEFORE identity: the owner id is read from the
    # migrated ``installation.json``, so it must be in place before
    # ``ensure_local_identity`` caches the id (else the cached id mismatches the
    # migrated rows' owner and breaks the official-skills reindex).
    steps.migrate_data_dir()
    # Staged backup restore applies here: after the data-dir cutover, before
    # identity caches the owner id and before any engine opens the SQLite
    # files (it replaces them at file level).
    steps.apply_backup_restore()
    steps.ensure_local_identity()  # seed owner ctx before any insert
    await steps.bootstrap_schema()
    await steps.configure_i18n()
    await steps.colocate_kernel_history()  # seed valuz.db durable from kernel.db (one-time)
    await steps.init_kernel(app)
    await steps.bind_data_service(app)
    steps.install_binding_change_listener()

    # recovery（依赖 kernel store 已就绪）
    await steps.recover_stranded_sessions()
    await steps.resume_queued_input_drains()
    await steps.seal_orphan_pendings()
    await steps.recover_active_tasks()

    # long-lived runners
    await steps.start_mcp_session_managers(app)
    await steps.start_automation_runtime(app)
    await steps.start_host_background_services(app)
    await steps.start_polling_scheduler()
    steps.warm_parse_pool()
    steps.warm_token_estimator()
    steps.resolve_marketplace_index()
    await steps.start_skills(app)
    await steps.start_decision_aggregator(app)
    steps.mark_boot_complete()  # LAST

    yield

    # ── shutdown（逆序拆解）──
    # FIRST, before tearing anything down: flip the draining flag so the
    # long-lived task actor loops stop starting new turns and skip their
    # finalize. They then leave in-flight sessions ``running`` for boot recovery
    # to resume — instead of racing the kernel-store / host-DB teardown below
    # (which otherwise spams "Dependencies not initialized" at every restart).
    from valuz_agent.infra.lifecycle import set_draining

    set_draining()
    await steps.stop_managed_browser()
    await steps.stop_decision_aggregator(app)
    await steps.stop_host_background_services(app)
    await steps.stop_automation_runtime(app)
    steps.shutdown_parse_pool()
    await steps.stop_polling_scheduler()
    await steps.stop_mcp_session_managers(app)
    await steps.dispose_data_service(app)
    await steps.shutdown_kernel()
    parent_watchdog.stop_parent_watchdog()

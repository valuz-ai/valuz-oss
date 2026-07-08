"""Process lifespan — the single explicit, ordered startup/shutdown script.

The startup order is load-bearing (see the order table in the boot-refactor
exec plan). Sync steps are called directly; async steps are awaited. ``app`` is
threaded through to the steps that read/stash ``app.state``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from valuz_agent.boot import steps


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # ── startup（顺序 load-bearing，注释分组）──
    steps.configure_structured_logging()  # FIRST
    steps.acquire_single_writer_lock()
    # Data-dir cutover runs BEFORE identity: the owner id is read from the
    # migrated ``installation.json``, so it must be in place before
    # ``ensure_local_identity`` caches the id (else the cached id mismatches the
    # migrated rows' owner and breaks the official-skills reindex).
    steps.migrate_data_dir()
    steps.ensure_local_identity()  # seed owner ctx before any insert
    await steps.bootstrap_schema()
    await steps.configure_i18n()
    await steps.colocate_kernel_history()  # seed valuz.db durable from kernel.db (one-time)
    steps.activate_downloaded_runtimes()  # env override BEFORE kernel serves/recovers sessions
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
    await steps.start_automation_runner(app)
    await steps.start_polling_scheduler()
    steps.warm_parse_pool()
    steps.warm_token_estimator()
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
    await steps.stop_automation_runner(app)
    steps.shutdown_parse_pool()
    await steps.stop_polling_scheduler()
    await steps.stop_mcp_session_managers(app)
    await steps.dispose_data_service(app)
    await steps.shutdown_kernel()

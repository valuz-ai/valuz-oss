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
    await steps.bootstrap_schema()
    await steps.configure_i18n()
    await steps.init_kernel(app)
    await steps.ensure_workspace_kernel_mirrors()
    steps.install_binding_change_listener()
    # ── recovery（依赖 kernel store 已就绪）──
    steps.recover_stranded_sessions()
    await steps.seal_orphan_pendings()
    await steps.recover_active_tasks()
    # ── long-lived runners ──
    await steps.start_mcp_session_managers(app)
    await steps.start_automation_runner(app)
    await steps.start_polling_scheduler()
    await steps.start_skills(app)
    await steps.start_decision_aggregator(app)
    steps.mark_boot_complete()  # LAST

    yield

    # ── shutdown（逆序拆解）──
    await steps.stop_decision_aggregator(app)
    await steps.stop_automation_runner(app)
    await steps.stop_polling_scheduler()
    await steps.stop_mcp_session_managers(app)
    await steps.shutdown_kernel()

"""Process lifecycle steps — one named function per app startup/shutdown hook.

Bodies are moved verbatim from the former ``@app.on_event`` hooks in
``api/app.py``. Stateless steps take no args; steps that read/stash
``app.state`` take ``app: FastAPI``. The startup order is load-bearing and is
expressed explicitly in ``boot/lifespan.py``.
"""

import logging

from fastapi import FastAPI

from valuz_agent.infra.config import settings

logger = logging.getLogger(__name__)


def configure_structured_logging() -> None:
    """Install JSON-line file handler on the root logger.

    Runs FIRST so subsequent startup hooks log through it. Why
    here instead of in ``main.py``: uvicorn calls
    ``logging.config.dictConfig`` during its own boot, which wipes
    any handlers previously attached to the root logger
    (``_clearExistingHandlers`` is part of stdlib's dictConfig
    implementation). By registering as a FastAPI startup hook we
    run *after* uvicorn's logging setup so our handler sticks.
    """
    from valuz_agent.infra.logging import configure_logging

    configure_logging()


def ensure_local_identity() -> None:
    """Resolve the local install owner id and seed the boot context with it.

    Runs early — before any schema bootstrap or seed insert — so every row
    created during boot is stamped with a real owner. Background tasks spawned
    during startup (automation runner, task runner, kernel mirrors) inherit
    this context via ``asyncio.create_task``. There is deliberately no global
    fallback: a context that was never seeded raises ``LookupError`` on read,
    so an unattributed insert fails loudly instead of being silently owned by
    the install id. OSS derives the id from the device fingerprint and persists
    it once to ``~/.valuz/app/installation.json``; the commercial overlay
    overrides per-request identity via ``ext.identity``.
    """
    from valuz_agent.infra.auth_context import set_current_user_id
    from valuz_agent.infra.local_identity import resolve_local_user_id

    set_current_user_id(resolve_local_user_id())


def acquire_single_writer_lock() -> None:
    """Refuse to start if another backend already owns the SQLite file.

    Only applies in SQLite mode — PostgreSQL handles concurrency natively.
    """
    if not settings.is_sqlite:
        return

    import os
    import sys as _sys

    if os.environ.get("VALUZ_SKIP_WRITER_LOCK") == "1":
        return

    from valuz_agent.infra.single_writer import (
        AnotherInstanceRunning,
        acquire_single_writer_lock,
    )

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = settings.data_dir / ".single-writer.lock"
    try:
        acquire_single_writer_lock(lock_path)
    except AnotherInstanceRunning:
        _sys.stderr.write(
            f"another valuz-agent backend already holds {lock_path}; "
            "refusing to start a second instance.\n"
        )
        _sys.exit(2)


async def bootstrap_schema() -> None:
    """Host schema bootstrap — run alembic on both the kernel and host
    chains, then seed.

    Boot order is load-bearing:

    1. Kernel alembic. The kernel owns
       ``projects``/``agents``/``sessions``/``events`` and writes
       to the default ``alembic_version`` table. Runs first so the
       kernel quartet exists before any host code touches it.
    2. Re-install our JSON logging handlers — alembic's
       ``fileConfig`` call clears the root logger's handlers, and
       ``configure_logging`` is idempotent.
    3. Host alembic. Runs ``upgrade head`` against the same SQLite
       file, but records its head in ``alembic_version_host`` so
       the two chains don't collide.
    4. ``seed_all`` — pure-insert seeders for built-in rows
       (providers today; more later). Safe to re-run on every boot.
    """
    from valuz_agent.boot.kernel import run_kernel_migrations
    from valuz_agent.boot.schema import run_host_migrations
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.seeds import seed_all

    settings.data_dir.mkdir(parents=True, exist_ok=True)

    # One-shot courtesy rename from the workspace→project naming cutover:
    # managed chat cwds moved from ``data_dir/workspaces/`` to
    # ``data_dir/projects/``. The DB is wiped by the cutover fingerprint,
    # but the directories hold user files — carry them over instead of
    # orphaning them. No-op once the new directory exists.
    legacy_dir = settings.data_dir / "workspaces"
    target_dir = settings.data_dir / "projects"
    if legacy_dir.is_dir() and not target_dir.exists():
        legacy_dir.rename(target_dir)

    # 1. Kernel alembic (its own ``alembic_version`` row).
    run_kernel_migrations()

    # 2. Re-install logging — alembic's fileConfig clobbers handlers.
    from valuz_agent.infra.logging import configure_logging

    configure_logging()

    # 3. Host alembic (``alembic_version_host`` row). Async env.py, driven
    #    on a dedicated thread (see ``run_host_migrations``).
    run_host_migrations()

    # 3.5 Re-install logging AGAIN — the host chain's ``fileConfig`` clears
    #     the root handlers exactly like the kernel chain's did in step 1,
    #     which previously killed the JSON file handler the 服务 log panel
    #     tails (and, before ``disable_existing_loggers=False`` landed in
    #     both env.py files, silenced every already-imported valuz logger).
    configure_logging()

    # 4. Pure-insert seeds for built-in rows.
    async with async_unit_of_work() as db:
        await seed_all(db)


async def configure_i18n() -> None:
    """Resolve the user's ``ui.default_locale`` once (async) and push it
    into the i18n in-memory cache.

    Runs after migrations + provider seeding so the settings table exists.
    From here on the sync ``t()`` path reads the pushed value with zero DB
    access; subsequent locale changes re-push via
    ``preferences.set_default_locale`` → ``i18n.set_locale``.
    """
    from valuz_agent.i18n import set_locale
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.settings.preferences import get_default_locale

    async with async_unit_of_work(commit=False) as db:
        set_locale(await get_default_locale(db))


async def init_kernel(app: FastAPI) -> None:
    from valuz_agent.boot.kernel import init_kernel_dependencies

    await init_kernel_dependencies()

    # Register host-provided custom tools into the kernel's global
    # tool registry. ``submit_skill`` is the companion to the
    # bundled skill-creator skill — agents declare it on their
    # ``tools`` tuple (handler=None) and the registry attaches the
    # real handler at session-build time. Idempotent.
    from valuz_agent.integrations.tools_skill_creator import (
        register_submit_skill_tool,
    )

    register_submit_skill_tool()

    # Register the lead-dispatch tools (dispatch / dispatch_batch /
    # list_members / finish_task). Lead-capable agents declare these on
    # their ``tools`` tuple (handler=None); the registry attaches the
    # real handlers (closures over the TaskOrchestrator) here. The lead
    # gate is enforced inside each handler (kernel Session has no tools
    # field — see lead-dispatch-mvp §S0①).
    from valuz_agent.modules.tasks.dispatch_mcp import register_dispatch_tools
    from valuz_agent.modules.tasks.orchestrator import task_orchestrator

    register_dispatch_tools(task_orchestrator)

    # Register memory_get / memory_write (runtime-agnostic; resolve scope
    # from the calling session's project cwd + task metadata).
    from valuz_agent.modules.memory.tools import register_memory_tools

    register_memory_tools()


def install_binding_change_listener() -> None:
    """Wire ``project.bindings.changed`` → docs caps refresh.

    DocumentLibraryService publishes this event whenever a project's
    KB bindings are added / removed (see docs/service.py:742). The
    subscriber walks every active session in that project and
    re-evaluates its docs skill+MCP slice — so binding a document
    to a project propagates to all open sessions immediately,
    rather than only to whatever new session the user creates next.

    Lazy refresh in ``send_message`` covers the same path on the
    next turn (belt-and-braces), so a missed event still converges
    once the user types again.
    """
    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.modules.sessions.capabilities import (
        refresh_docs_capabilities_for_project,
    )

    def _on_bindings_changed(**kwargs: object) -> None:
        # The eventbus is synchronous but publishes from coroutine code on
        # the running loop; the refresher is async — schedule it instead of
        # blocking the loop. Fire-and-forget: the lazy refresh in
        # ``send_message`` converges any missed/failed run on the next turn.
        import asyncio

        coro = refresh_docs_capabilities_for_project(**kwargs)  # type: ignore[arg-type]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
        else:
            task = loop.create_task(coro)
            task.add_done_callback(lambda t: t.exception())

    event_bus.subscribe(
        "project.bindings.changed",
        _on_bindings_changed,
    )


async def recover_stranded_sessions() -> None:
    """Clear ``running`` sessions left over from a previous process.

    See ``domains.execution.sessions.recovery`` for rationale. Runs
    after ``init_kernel`` so the kernel store is reachable.
    """
    from valuz_agent.modules.sessions.recovery import (
        recover_running_sessions,
    )

    await recover_running_sessions()


async def seal_orphan_pendings() -> None:
    """Seal every ``requires_action`` still open from a previous run.

    Approval contract v1 (V5+1aae940): pending approvals do not
    survive a host process restart — the runtime SDK that was
    parking on them is gone. The kernel orchestrator's
    ``scan_orphan_pendings`` walks every running session's events
    log and writes a synthetic ``action_resolved(decision="expired",
    resolved_by="system")`` for each unresolved pending so SSE
    replay shows a clean closure rather than a silent never-finish.

    Runs alongside ``recover_stranded_sessions`` because both fix
    symptoms of the same underlying event (host crash mid-turn) and
    both need the kernel store to be wired (``init_kernel`` already
    ran in the dependency-init startup hook).
    """
    import logging

    from valuz_agent.adapters import kernel_client

    try:
        sealed = await kernel_client.scan_orphan_pendings()
    except Exception:  # noqa: BLE001 — startup must not block on bookkeeping
        logging.getLogger(__name__).exception("scan_orphan_pendings failed")
        return
    if sealed:
        logging.getLogger(__name__).warning(
            "scan_orphan_pendings: sealed %d orphan approval(s) as expired",
            sealed,
        )


async def recover_active_tasks() -> None:
    """Layer 1 task recovery (VALUZ-RESUME): reconcile + re-drive ``active``
    tasks orphaned by the previous process exit.

    Runs after ``recover_stranded_sessions`` / ``seal_orphan_pendings`` so the
    kernel session rows are already reconciled (``scan_orphan_runs`` left
    interrupted members at ``idle`` + ``host_restart``). Only ``active`` tasks
    are touched; ``paused`` (user-stopped) wait for explicit resume.
    """
    import logging

    from valuz_agent.modules.tasks.orchestrator import task_orchestrator

    try:
        await task_orchestrator.recover_active_tasks()
    except Exception:  # noqa: BLE001 — startup must not block on bookkeeping
        logging.getLogger(__name__).exception("recover_active_tasks failed")


async def start_mcp_session_managers(app: FastAPI) -> None:
    """Bring the in-process docs MCP session manager online.

    FastMCP's ``StreamableHTTPSessionManager`` is started via an
    async context manager. When mounted as a Starlette sub-app under
    FastAPI the parent's ``lifespan`` doesn't propagate into the
    sub-app, so we drive the context manager manually and stash the
    AsyncExitStack on ``app.state`` so the matching shutdown handler
    can tear it down cleanly.

    Without this, every MCP request would terminate with
    ``Session terminated`` because the session manager's background
    task wouldn't be running.
    """
    from contextlib import AsyncExitStack

    from valuz_agent.integrations.automations_mcp_server import (
        automations_mcp_session_manager_run,
    )
    from valuz_agent.integrations.connectors_mcp_server import (
        connectors_mcp_session_manager_run,
    )
    from valuz_agent.integrations.docs_mcp_server import docs_mcp_session_manager_run

    stack = AsyncExitStack()
    await stack.__aenter__()
    await stack.enter_async_context(docs_mcp_session_manager_run())
    await stack.enter_async_context(automations_mcp_session_manager_run())
    await stack.enter_async_context(connectors_mcp_session_manager_run())
    app.state.docs_mcp_stack = stack


async def stop_mcp_session_managers(app: FastAPI) -> None:
    stack = getattr(app.state, "docs_mcp_stack", None)
    if stack is not None:
        await stack.__aexit__(None, None, None)
        app.state.docs_mcp_stack = None


async def start_automation_runner(app: FastAPI) -> None:
    from valuz_agent.modules.automations.failure_monitor import (
        automation_failure_monitor,
    )
    from valuz_agent.modules.automations.in_process_runner import (
        automation_runner,
    )

    await automation_runner.startup()
    # ADR-012: auto-pause runaway-failing automations. Lives alongside
    # the runner; same lifecycle, no shared state, single SQLite writer
    # (ADR-011) keeps DB access safe.
    await automation_failure_monitor.startup()

    from valuz_agent.modules.docs.scheduler import start_auto_discovery

    start_auto_discovery()


async def start_polling_scheduler() -> None:
    """Start the parser polling scheduler's on-loop tick task. Used only
    by cloud parser plugins (MinerU / PaddleOCR); idle otherwise."""
    from valuz_agent.api.deps import _polling_scheduler

    await _polling_scheduler().startup()


async def stop_polling_scheduler() -> None:
    from valuz_agent.api.deps import _polling_scheduler

    await _polling_scheduler().shutdown()


def warm_parse_pool() -> None:
    """Pre-spawn the document-parser worker processes. Local parses
    (pymupdf4llm / markitdown) run in a separate process so their GIL-bound
    work can't stall the event loop; warming here pays the spawn + import cost
    at boot instead of on the first upload. Best-effort, never fatal."""
    from valuz_agent.infra import parse_pool

    try:
        parse_pool.warm()
    except Exception:  # noqa: BLE001
        pass


def shutdown_parse_pool() -> None:
    from valuz_agent.infra import parse_pool

    parse_pool.shutdown()


async def start_skills(app: FastAPI) -> None:
    # Sync bundled official skills (e.g. skill-creator, valuz-handbook) into
    # the user's official-skills directory before scanning, so they appear
    # on first run. (Previously mis-placed in stop_polling_scheduler's
    # shutdown handler — skills only synced/scanned on process exit, lagging
    # a whole lifecycle.)
    from valuz_agent.integrations.skills_official_bootstrap import (
        sync_bundled_official_skills,
    )

    try:
        sync_bundled_official_skills()
    except Exception:
        pass

    from valuz_agent.api.deps import get_skill_service

    skill_gen = get_skill_service()
    skill_svc = await skill_gen.__anext__()
    try:
        await skill_svc.startup_scan()
    except Exception:
        pass
    finally:
        try:
            await skill_gen.__anext__()
        except StopAsyncIteration:
            pass

    import asyncio

    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.infra.file_watcher import SkillFileWatcher
    from valuz_agent.integrations.skills_filesystem import (
        _default_user_skill_root,
    )

    # NB: the post-session ``SkillCandidateDetector`` was removed —
    # in-session ``submit_skill`` (always-on via the bundled
    # skill-creator skill, see ADR-002 §2) is the canonical path
    # for proposing a skill, so the redundant retroactive scanner
    # was deleted along with its tables, routes, and frontend
    # surface. See the removal commit for the rationale.
    watcher = SkillFileWatcher(event_bus)
    user_root = _default_user_skill_root()
    if user_root.exists():
        watcher.add_path(user_root)
    app.state.skill_watcher = watcher
    asyncio.get_event_loop().create_task(watcher.start())


async def stop_automation_runner(app: FastAPI) -> None:
    from valuz_agent.modules.automations.failure_monitor import (
        automation_failure_monitor,
    )
    from valuz_agent.modules.automations.in_process_runner import (
        automation_runner,
    )

    await automation_failure_monitor.shutdown()
    await automation_runner.shutdown()

    from valuz_agent.modules.docs.scheduler import stop_auto_discovery

    stop_auto_discovery()

    watcher = getattr(app.state, "skill_watcher", None)
    if watcher is not None:
        await watcher.stop()


async def start_decision_aggregator(app: FastAPI) -> None:
    """ADR-022: kick off the global Decision Inbox aggregator.

    Scans active sessions for unresolved ``requires_action`` pendings,
    then subscribes to the kernel broadcast bus for live updates.
    Lives for the whole app lifetime.
    """
    from valuz_agent.api.deps import set_decision_aggregator
    from valuz_agent.modules.decisions.aggregator import DecisionAggregator

    agg = DecisionAggregator()
    await agg.start()
    set_decision_aggregator(agg)
    app.state.decision_aggregator = agg


async def stop_decision_aggregator(app: FastAPI) -> None:
    agg = getattr(app.state, "decision_aggregator", None)
    if agg is not None:
        await agg.stop()


def mark_boot_complete() -> None:
    """Flip the system status from ``starting`` → ``running``.

    Registered last so every other startup hook gets a chance to
    push a ``record_warning(...)`` first — anything that landed in
    the warnings buffer turns ``status`` into ``degraded`` instead.
    """
    from valuz_agent.modules.system.service import record_boot_complete

    record_boot_complete()


async def shutdown_kernel() -> None:
    from valuz_agent.boot.kernel import shutdown_kernel_dependencies

    await shutdown_kernel_dependencies()

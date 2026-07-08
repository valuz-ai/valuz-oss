"""Process lifecycle steps — one named function per app startup/shutdown hook.

Bodies are moved verbatim from the former ``@app.on_event`` hooks in
``api/app.py``. Stateless steps take no args; steps that read/stash
``app.state`` take ``app: FastAPI``. The startup order is load-bearing and is
expressed explicitly in ``boot/lifespan.py``.
"""

import asyncio
import logging

from fastapi import FastAPI

from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import fs_registry

logger = logging.getLogger(__name__)


def _startup_user_content_enabled() -> bool:
    return bool(settings.initialize_user_content_on_startup)


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
    fallback: a context that was never seeded reads as ``None``; required-owner
    APIs and non-null owner columns then fail instead of silently attributing
    work to the install id. OSS derives the id from the device fingerprint and persists
    it once to ``~/.valuz-oss/installation.json``; the commercial overlay
    overrides per-request identity by swapping ``AuthMiddleware`` (overriding
    ``resolve_user_id``) via ``ext.auth_middleware``.
    """
    if not _startup_user_content_enabled():
        from valuz_agent.infra.auth_context import set_current_user_id

        set_current_user_id(None)
        logger.info("startup user-content initialization disabled; local identity skipped")
        return

    from valuz_agent.infra.auth_context import set_current_user_id
    from valuz_agent.infra.local_identity import resolve_local_user_id

    set_current_user_id(resolve_local_user_id())


def acquire_single_writer_lock() -> None:
    """Refuse to start if another backend already owns the SQLite file.

    Only applies in SQLite mode — PostgreSQL handles concurrency natively.
    """
    from valuz_agent.infra.db_urls import is_sqlite_runtime

    if not is_sqlite_runtime():
        return

    import os
    import sys as _sys

    if os.environ.get("VALUZ_SKIP_WRITER_LOCK") == "1":
        return

    from valuz_agent.infra.local_identity import resolve_local_user_id
    from valuz_agent.infra.single_writer import (
        AnotherInstanceRunning,
        acquire_single_writer_lock,
    )

    if _startup_user_content_enabled():
        data_dir = fs_registry.data_dir(resolve_local_user_id())
    else:
        data_dir = fs_registry.shared_root()
    lock_path = data_dir / ".single-writer.lock"
    try:
        acquire_single_writer_lock(lock_path)
    except AnotherInstanceRunning:
        _sys.stderr.write(
            f"another valuz-agent backend already holds {lock_path}; "
            "refusing to start a second instance.\n"
        )
        _sys.exit(2)


def migrate_data_dir() -> None:
    """One-time data-dir cutover: carry a pre-rename ``~/.valuz/app`` install
    into the new flat ``~/.valuz-oss`` root (copy → rewrite DB path prefixes →
    repoint skill symlinks → verify).

    Runs under the single-writer lock and BEFORE ``ensure_local_identity`` and
    before any engine opens the SQLite files. The ordering vs. identity is
    load-bearing: the install owner id is read from ``installation.json``, so the
    migrated copy of that file must be in place first — otherwise the boot
    context caches a freshly-derived id that mismatches the migrated rows' owner,
    which breaks the owner-scoped official-skills reindex (a global skill ``id``
    INSERTed under a new owner collides with the migrated row). No-op once
    migrated / on a fresh install.
    """
    if not _startup_user_content_enabled():
        logger.info("startup user-content initialization disabled; data-dir migration skipped")
        return

    from valuz_agent.boot.migrate_data_dir import (
        migrate_legacy_data_dir,
        migrate_unscoped_data_root,
    )

    migrate_legacy_data_dir()
    migrate_unscoped_data_root()


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
    from valuz_agent.infra.logging import configure_logging
    from valuz_agent.seeds import seed_all

    user_content_enabled = _startup_user_content_enabled()

    # NB: the ``~/.valuz/app`` → ``~/.valuz-oss`` data-dir cutover runs earlier in
    # the lifespan (``migrate_data_dir``), before identity resolution — see that
    # step's docstring for why the ordering is load-bearing.
    if user_content_enabled:
        from valuz_agent.infra.local_identity import resolve_local_user_id

        user_id = resolve_local_user_id()
        data_dir = fs_registry.data_dir(user_id)

        # One-shot courtesy rename from the workspace→project naming cutover:
        # managed chat cwds moved from ``data_dir/workspaces/`` to
        # ``data_dir/projects/``. The DB is wiped by the cutover fingerprint,
        # but the directories hold user files — carry them over instead of
        # orphaning them. No-op once the new directory exists.
        legacy_dir = data_dir / "workspaces"
        target_dir = data_dir / "projects"
        if legacy_dir.is_dir() and not target_dir.exists():
            legacy_dir.rename(target_dir)

    # NB: the legacy ``kernel_db_split`` cutover (move kernel tables *out* of
    #    valuz.db into kernel.db) is RETIRED. It contradicts the DataService
    #    co-locate model, where sessions/messages/events live in valuz.db as the
    #    durable/read source (design §3 form 1); evicting them is wrong and its
    #    PK-based copy corrupted the dual-write buffer. Seeding now flows the
    #    other way (kernel.db → valuz.db) via ``colocate_kernel_history``.

    # 1. Kernel alembic (its own ``alembic_version`` row). SKIPPED in
    #    http mode — the standalone kernel owns its own database and
    #    migrates it itself (B5); running it here would migrate the
    #    host file's kernel tables that nothing in http mode reads.
    if not settings.is_http_kernel:
        run_kernel_migrations()

    # 3. Host alembic (``alembic_version_host`` row). Async env.py, driven
    #    on a dedicated thread (see ``run_host_migrations``).
    run_host_migrations()

    # 3.5 Re-install logging AGAIN — the host chain's ``fileConfig`` clears
    #     the root handlers exactly like the kernel chain's did in step 1,
    #     which previously killed the JSON file handler the 服务 log panel
    #     tails (and, before ``disable_existing_loggers=False`` landed in
    #     both env.py files, silenced every already-imported valuz logger).
    configure_logging()

    if not user_content_enabled:
        logger.info("startup user-content initialization disabled; seed/backfill skipped")
        return

    from valuz_agent.infra.local_identity import resolve_local_user_id

    # 4. Pure-insert seeds for built-in rows.
    async with async_unit_of_work() as db:
        await seed_all(db, user_id=resolve_local_user_id())

    # 5. One-time backfill of the connector module's legacy filesystem stores
    #    (project-config.json selection + local secret files) into the
    #    connector DB tables/columns. This remains local-only; cloud startup
    #    should not run owner-context-sensitive filesystem backfill.
    from valuz_agent.boot.backfill_connector_fs import backfill_connector_fs

    async with async_unit_of_work() as db:
        await backfill_connector_fs(db)


async def configure_i18n() -> None:
    """Resolve the user's ``ui.default_locale`` once (async) and push it
    into the i18n in-memory cache.

    Runs after migrations + provider seeding so the settings table exists.
    From here on the sync ``t()`` path reads the pushed value with zero DB
    access; subsequent locale changes re-push via
    ``preferences.set_default_locale`` → ``i18n.set_locale``.
    """
    if not _startup_user_content_enabled():
        logger.info("startup user-content initialization disabled; user locale skipped")
        return

    from valuz_agent.i18n import set_locale
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.infra.local_identity import resolve_local_user_id
    from valuz_agent.modules.settings.preferences import get_default_locale

    async with async_unit_of_work(commit=False) as db:
        set_locale(await get_default_locale(db, user_id=resolve_local_user_id()))


async def colocate_kernel_history() -> None:
    """One-time seed of the DataService durable (valuz.db) from kernel.db.

    Runs after schema bootstrap and before the durable store is read, so an
    install created before the "DataService always the data layer" flip keeps its
    history visible. In-process (sqlite) only — a remote/http kernel or a shared
    DB has nothing to co-locate. Guarded: a failure must not break boot.
    """
    if settings.is_http_kernel:
        return
    try:
        from valuz_agent.boot.kernel_db_colocate import colocate_kernel_history_into_host_db

        await colocate_kernel_history_into_host_db()
    except Exception:  # noqa: BLE001 — insert-only migration must never break boot
        logging.getLogger(__name__).warning("kernel-history co-locate skipped", exc_info=True)


async def init_kernel(app: FastAPI) -> None:
    # In-process kernel singletons (store + orchestrator) are NOT created
    # in http mode — the kernel runs as a separate process and the host
    # reaches it only through ``HttpKernelClient`` (B3). The host toolkit
    # MCP server below is installed in BOTH modes: it is the ④ callback
    # target the sandboxed kernel's runtime calls back into.
    if not settings.is_http_kernel:
        from valuz_agent.boot.kernel import init_kernel_dependencies

        await init_kernel_dependencies()

    # Install the host toolkit MCP toolsets. The harness tools
    # (dispatch / orchestration / memory / submit_skill) are served by the
    # host's in-process MCP server (``integrations/toolkit_mcp_server``)
    # and referenced from ``session.mcp_servers`` — every runtime consumes
    # them through its standard MCP client path, in-process and remote
    # alike. Toolset partition mirrors the former per-agent declarations:
    # ``base`` (every session) = orchestration launchers + memory +
    # submit_skill; ``lead`` (task leads) = dispatch set + memory +
    # submit_skill. The lead gate stays enforced inside each handler.
    from valuz_agent.integrations.toolkit_mcp_server import install_toolkit_toolsets
    from valuz_agent.integrations.tools_agent_proposal import build_agent_proposal_tool_defs
    from valuz_agent.integrations.tools_skill_creator import build_submit_skill_tool_defs
    from valuz_agent.modules.browser import service as browser_service
    from valuz_agent.modules.browser.tools import build_browser_tool_defs
    from valuz_agent.modules.memory.tools import build_memory_tool_defs
    from valuz_agent.modules.projects.tools import build_project_instructions_tool_defs
    from valuz_agent.modules.sessions.artifacts_tool import build_deliver_artifacts_tool_defs
    from valuz_agent.modules.tasks.dispatch_mcp import build_task_tool_defs
    from valuz_agent.modules.tasks.orchestrator import task_orchestrator
    from valuz_agent.modules.tasks.tools.declarations import (
        DISPATCH_TOOL_DECLARATIONS,
        ORCHESTRATION_TOOL_DECLARATIONS,
    )

    task_defs = build_task_tool_defs(task_orchestrator)
    by_name = {t.name: t for t in task_defs}
    orchestration_names = [d.name for d in ORCHESTRATION_TOOL_DECLARATIONS]
    dispatch_names = [d.name for d in DISPATCH_TOOL_DECLARATIONS]
    shared = (
        build_memory_tool_defs()
        + build_project_instructions_tool_defs()
        + build_submit_skill_tool_defs()
        + build_agent_proposal_tool_defs()
        + build_deliver_artifacts_tool_defs()
    )
    # browser_start/browser_stop only work when the engine (Node +
    # chrome-devtools-mcp) is available; don't expose dead tools otherwise
    # (e.g. headless/TUI without Node). See docs/design/browser-feature.md §8.
    if browser_service.node_available():
        shared = shared + build_browser_tool_defs()
        # Install the friendly ``chrome-devtools`` wrapper on PATH now, at boot —
        # before any session spawns its agent subprocess (which inherits env at
        # spawn time). Lets the agent run a clean ``chrome-devtools <tool>``.
        if not _startup_user_content_enabled():
            logger.info(
                "startup user-content initialization disabled; "
                "browser CLI bootstrap skipped"
            )
        elif browser_service.ensure_cli_on_path():
            logger.info("browser CLI installed on PATH (chrome-devtools)")
    else:
        logger.info("browser engine unavailable — browser_start/browser_stop not registered")
    install_toolkit_toolsets(
        base=tuple(by_name[n] for n in orchestration_names if n in by_name) + shared,
        lead=tuple(by_name[n] for n in dispatch_names if n in by_name) + shared,
    )

    # Wire the background memory extractor to the idle trigger (memory-system-design
    # §7): once a session goes quiet, review it and write durable memories through
    # the same MemoryStore pipeline as the foreground tool.
    from valuz_agent.modules.memory.runner import (
        run_extraction_for_session,
        run_task_finish_extraction,
    )
    from valuz_agent.modules.memory.scheduler import idle_scheduler, task_finish_scheduler

    idle_scheduler.set_runner(run_extraction_for_session)
    # Task-finish trigger (§7.1): when a multi-agent task completes, graduate its
    # durable multi-agent lessons + project progress into project memory.
    task_finish_scheduler.set_runner(run_task_finish_extraction)


async def bind_data_service(app: FastAPI) -> None:
    """Bind the host-mounted DataService (``/internal/data``) to its backend.

    The sub-app is mounted at factory time with no store (only ``/health`` +
    ``/openapi.json`` work until now). Here — once the host DB is up — we build a
    store over the configured durable backend (the user's Postgres) + an HS256
    verifier keyed by the host secret, so a sandbox can reach it over HTTP+JWT
    without ever holding the DSN. The store tier is read **purely from the
    environment** (``KERNEL_STORE`` / ``VALUZ_DURABLE_DATABASE_URL``), loaded at
    boot — the same env the kernel's ``AppConfig`` reads. Local-only deployments
    keep the DS inert (the in-process store is the data layer). Guarded: a
    failure must not break boot.
    """
    ds_app = getattr(app.state, "data_service_app", None)
    if ds_app is None:
        return
    try:
        import os

        from valuz_agent.boot import kernel as kb
        from valuz_agent.infra.data_service_secret import get_or_create_ds_secret
        from valuz_agent.infra.local_identity import resolve_local_user_id

        store_mode = os.environ.get("KERNEL_STORE", "local")
        dsn = os.environ.get("VALUZ_DURABLE_DATABASE_URL", "")
        # OSS default (local): the DataService backend is the host sqlite
        # (valuz.db). Resolve it here too so binding isn't ordering-dependent on
        # ``_set_kernel_env``. pg/remote provide the DSN via env.
        if store_mode == "local" and not dsn:
            from valuz_agent.infra.db_urls import db_url_async

            dsn = db_url_async()
        if not dsn:
            return
        store, engine = kb.build_host_data_service_store(dsn)
        await kb.ensure_host_data_service_schema(engine)
        ds_app.state.store = store
        # Per-owner verifier: resolves each token's signing secret by its owner, so
        # one shared host verifies every owner's data-service token (local = the one
        # owner resolves its own secret; cloud = many owners). Ensure the local
        # owner's secret exists up-front (mint side also does; idempotent).
        if _startup_user_content_enabled():
            get_or_create_ds_secret(resolve_local_user_id())
        ds_app.state.verifier = kb.make_host_data_service_verifier_per_owner()
        app.state._data_service_engine = engine
        # Unify host reads (sessions + events) through the DataService
        # (in-process), so reads never depend on the sandbox being alive. Bind
        # the in-process reader into the typed DataReader port.
        from valuz_agent.adapters.data_reader import bind_data_reader
        from valuz_agent.adapters.data_service_local import LocalDataServiceReader

        bind_data_reader(LocalDataServiceReader(store))
        logging.getLogger(__name__).info("host DataService bound (backend=%s)", store_mode)
    except Exception:  # noqa: BLE001 — DS binding must never break boot
        logging.getLogger(__name__).warning("host DataService bind skipped", exc_info=True)


async def dispose_data_service(app: FastAPI) -> None:
    from valuz_agent.adapters.data_reader import bind_data_reader

    bind_data_reader(None)
    engine = getattr(app.state, "_data_service_engine", None)
    if engine is not None:
        await engine.dispose()
        app.state._data_service_engine = None


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

        if not isinstance(kwargs.get("user_id"), str):
            return

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
    # The orphan scans run inside the kernel store — in http mode the
    # standalone kernel runs them itself at its own startup (B2); the
    # HttpKernelClient deliberately has no scan_orphan_* methods.
    if settings.is_http_kernel:
        return

    from valuz_agent.modules.sessions.recovery import (
        recover_running_sessions,
    )

    await recover_running_sessions()


async def resume_queued_input_drains() -> None:
    """Resume persisted session input-queue drains (session-input-queue §9 ②).

    Runs after ``recover_stranded_sessions`` so it sees the post-① state (any
    mid-turn session already terminated). Valid in both kernel modes — the queue
    is host-owned and the drain drives turns through the kernel client either
    way. Best-effort.
    """
    from valuz_agent.modules.sessions.recovery import resume_queued_drains

    await resume_queued_drains()


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

    # http mode: the standalone kernel seals its own orphans (B2).
    if settings.is_http_kernel:
        return

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
    from valuz_agent.integrations.toolkit_mcp_server import (
        toolkit_mcp_session_managers_run,
    )

    stack = AsyncExitStack()
    await stack.__aenter__()
    await stack.enter_async_context(docs_mcp_session_manager_run())
    await stack.enter_async_context(automations_mcp_session_manager_run())
    await stack.enter_async_context(connectors_mcp_session_manager_run())
    await stack.enter_async_context(toolkit_mcp_session_managers_run())
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

    if not _startup_user_content_enabled():
        logger.info("startup user-content initialization disabled; docs/skills scanners skipped")
        return

    from valuz_agent.modules.docs.scheduler import start_auto_discovery

    start_auto_discovery()

    from valuz_agent.modules.skills.scheduler import start_skill_auto_scan

    start_skill_auto_scan()


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


def activate_downloaded_runtimes() -> None:
    """Point ``CODEX_BIN_OVERRIDE`` at an on-demand-downloaded codex binary.

    Must run BEFORE the kernel serves sessions (the codex runtime reads the
    env at construction) and before boot recovery resumes codex sessions. A
    user-set override is respected; no completed download → no-op.
    Best-effort, never fatal."""
    import os

    from valuz_agent.modules.runtimes.setup_job import (
        CODEX_BIN_OVERRIDE_ENV,
        activate_codex_override,
    )

    try:
        if activate_codex_override():
            logger.info(
                "codex runtime: activated downloaded binary (%s=%s)",
                CODEX_BIN_OVERRIDE_ENV,
                os.environ.get(CODEX_BIN_OVERRIDE_ENV),
            )
    except Exception:  # noqa: BLE001
        logger.warning("codex runtime activation failed", exc_info=True)


def warm_token_estimator() -> None:
    """Pre-load the tiktoken vocab used by the goal-mode length fence in a
    background thread. The first ``get_encoding`` can fetch + parse the vocab
    (seconds); warming here keeps that off the event loop so the first task's
    spill check is instant. Best-effort, never fatal."""
    from valuz_agent.adapters.agent_resolver import prewarm_token_estimator

    try:
        prewarm_token_estimator()
    except Exception:  # noqa: BLE001
        pass


def shutdown_parse_pool() -> None:
    from valuz_agent.infra import parse_pool

    parse_pool.shutdown()


async def start_skills(app: FastAPI) -> None:
    if not _startup_user_content_enabled():
        logger.info("startup user-content initialization disabled; local skill indexing skipped")
        return

    # Sync bundled official skills (e.g. skill-creator, valuz-handbook) into
    # the user's official-skills directory before scanning, so they appear
    # on first run. (Previously mis-placed in stop_polling_scheduler's
    # shutdown handler — skills only synced/scanned on process exit, lagging
    # a whole lifecycle.)
    from valuz_agent.infra.local_identity import resolve_local_user_id
    from valuz_agent.integrations.skills_official_bootstrap import sync_bundled_official_skills

    # Local startup writes are owned explicitly by the stable local install id.
    # Shared/cloud deployments returned above, so this step never invents a
    # synthetic owner for a multi-user backend.
    owner = resolve_local_user_id()
    try:
        sync_bundled_official_skills(owner)
    except Exception:
        pass

    from valuz_agent.api.deps import get_skill_service_for_user

    skill_gen = get_skill_service_for_user(owner)
    skill_svc = await skill_gen.__anext__()
    try:
        # Deterministically index the bundled official skills FIRST, in the
        # same step that just synced them to disk. The broad startup_scan
        # below is best-effort (errors swallowed); when finance-edition
        # official skills land on disk out-of-band it can lag, leaving an
        # agent that references them unable to resolve the skill. This
        # targeted upsert guarantees they are in valuz_skill_index every
        # boot.
        try:
            indexed = await skill_svc.index_official_skills(owner)
            logger.info("index_official_skills: indexed %d official skill(s)", indexed)
        except Exception:
            logger.exception("index_official_skills failed")
        try:
            await skill_svc.startup_scan(owner)
        except Exception:
            # Best-effort, but no longer silent: a failed scan that leaves
            # the index stale was exactly the bug this step hardens against.
            logger.exception("startup_scan failed")
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
    user_root = _default_user_skill_root(owner)
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

    from valuz_agent.modules.skills.scheduler import stop_skill_auto_scan

    stop_skill_auto_scan()

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


async def stop_managed_browser() -> None:
    """Best-effort: stop the chrome-devtools daemon so app exit doesn't leave an
    orphan visible Chrome. The isolated profile persists (login state survives);
    only the window/daemon closes. Bounded + never blocks teardown."""
    try:
        from valuz_agent.modules.browser import service as browser_service

        await asyncio.wait_for(browser_service.stop(), timeout=10.0)
    except Exception:  # noqa: BLE001 — shutdown best-effort
        logger.warning("managed browser stop on shutdown failed", exc_info=True)


async def shutdown_kernel() -> None:
    from valuz_agent.boot.kernel import shutdown_kernel_dependencies

    await shutdown_kernel_dependencies()

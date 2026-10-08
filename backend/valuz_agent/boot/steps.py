"""Process lifecycle steps — one named function per app startup/shutdown hook.

Bodies are moved verbatim from the former ``@app.on_event`` hooks in
``api/app.py``. Stateless steps take no args; steps that read/stash
``app.state`` take ``app: FastAPI``. The startup order is load-bearing and is
expressed explicitly in ``boot/lifespan.py``.
"""

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI

from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import fs_registry

logger = logging.getLogger(__name__)


def _startup_user_content_enabled() -> bool:
    return bool(settings.initialize_user_content_on_startup)


def guard_source_run_data_dir() -> None:
    """Refuse to run a source (non-frozen) backend on the packaged app's data dir.

    Recurring incident class: a dev/test backend pointed at the real
    ``~/.valuz-oss`` runs host migrations and pushes ``alembic_version_host``
    ahead of the released build, which then fail-louds at its next boot
    (``ensure_host_schema_migratable``). The packaged ``valuz-server`` is
    PyInstaller-frozen (``sys.frozen``) and exempt; every other process must
    bring its own root (dev.sh pins ``~/.valuz-oss-dev``; the test conftest
    pins a tmp sandbox). ``VALUZ_ALLOW_PACKAGED_DATA_DIR=1`` is the explicit
    escape hatch for deliberately operating on the packaged store from source.

    Runs FIRST in the lifespan — before logging config (``log_dir`` writes
    under the root) and before the single-writer lock (the lock file is a
    root write). Also called by ``main.main`` and the management CLI commands,
    which touch the data dir without going through the lifespan.
    """
    import os
    import sys as _sys

    if getattr(_sys, "frozen", False):
        return
    if os.environ.get("VALUZ_ALLOW_PACKAGED_DATA_DIR") == "1":
        return

    from valuz_agent.infra.config import PACKAGED_DATA_DIR

    packaged_root = PACKAGED_DATA_DIR.resolve()
    if fs_registry.shared_root_path().resolve() == packaged_root:
        raise RuntimeError(
            "refusing to start: this backend runs from source (not the packaged "
            f"valuz-server) but its data dir resolves to {PACKAGED_DATA_DIR} — the "
            "packaged app's store. A source backend migrating that store strands "
            "the released app (its schema stamp moves past the release's migration "
            "chain). Use scripts/dev.sh (defaults VALUZ_DATA_DIR=~/.valuz-oss-dev) "
            "or set VALUZ_DATA_DIR. To operate on the packaged store on purpose, "
            "set VALUZ_ALLOW_PACKAGED_DATA_DIR=1."
        )
    if settings.log_file_path.expanduser().resolve().parent == (packaged_root / "logs").resolve():
        raise RuntimeError(
            "refusing to start: this backend runs from source but its log dir "
            f"resolves to {PACKAGED_DATA_DIR / 'logs'} — the packaged app's logs "
            "(source-run log lines there corrupt release forensics). The default "
            "log path follows VALUZ_DATA_DIR; unset VALUZ_LOG_FILE_PATH or point it "
            "elsewhere, or set VALUZ_ALLOW_PACKAGED_DATA_DIR=1 to operate on "
            "the packaged store on purpose."
        )


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


async def enrich_login_shell_path() -> None:
    """Merge the user's login-shell PATH into this process's PATH.

    A Finder / launchd-launched backend inherits launchd's minimal PATH and
    can't see user-installed tools (nvm's ``npx``, ``uv``, homebrew) that
    stdio MCP connectors, the CLI login probe and the browser dev fallback
    resolve by name. Append-only, fail-open, ``VALUZ_DISABLE_LOGIN_PATH=1``
    opts out — see ``boot/login_path.py``.
    """
    from valuz_agent.boot.login_path import enrich_login_shell_path as _enrich

    await _enrich()


def install_session_tools() -> None:
    """Put the bundled session commands (``valuz-python``, ``dsoffice``) on PATH.

    Every runtime's agent subprocess inherits this process's PATH at spawn, so
    the wrappers must exist before the first session starts. Fail-open: a
    missing tool only means the skills that use it report it as unavailable —
    see ``infra/session_tools.py``.
    """
    from valuz_agent.infra.fs_registry import FsRegistry
    from valuz_agent.infra.session_tools import ensure_session_tools_on_path

    try:
        installed = ensure_session_tools_on_path(FsRegistry().session_bin_dir())
    except Exception:  # noqa: BLE001 — a convenience wrapper must not break boot
        logger.warning("failed to install session command wrappers", exc_info=True)
        return
    if installed:
        logger.info("session commands on PATH: %s", ", ".join(installed))


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


def apply_backup_restore() -> None:
    """Apply a staged local-backup restore, if one is pending.

    Runs under the single-writer lock, AFTER the data-dir cutover and BEFORE
    ``ensure_local_identity`` / any engine opens the SQLite files — a restore
    replaces ``valuz.db`` / ``kernel.db`` / ``installation.json`` at file
    level, which is only safe while nothing has them open. No-op (one ``stat``)
    when nothing is pending. See docs/design/client-local-backup.md §8.
    """
    if not _startup_user_content_enabled():
        return

    from valuz_agent.boot.backup_restore import apply_pending_backup_restore

    apply_pending_backup_restore()


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


def init_tracing() -> None:
    """Langfuse tracing bootstrap — no-op unless the LANGFUSE_* env is set
    and the optional ``tracing`` extra is installed (see ``src.core.tracing``).

    Runs in the HOST process: covers the in-process kernel (desktop/local).
    The standalone sandbox kernel initializes itself in
    ``kernel/app/dependencies.init_dependencies``.
    """
    from src.core.tracing import init_tracing as _init_tracing

    _init_tracing()


def initialize_network_egress() -> None:
    from valuz_agent.boot.kernel import initialize_kernel_network_egress

    initialize_kernel_network_egress()


async def init_kernel(app: FastAPI, toolkit: Sequence[Any] | None = None) -> None:
    """Bring the in-process kernel up and install the harness toolsets.

    ``toolkit`` is the tool groups the composed plugins contributed (the registry's
    ``ToolGroup`` entries, canonical order). ``None`` -- the headless
    ``execution_runtime`` and direct callers -- installs every group the OSS
    features define, i.e. the all-features-enabled surface.
    """
    initialize_network_egress()

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
    # ``base`` (every session) = orchestration launchers + the shared tools;
    # ``lead`` (task leads) = dispatch set + the shared tools. The lead gate
    # stays enforced inside each handler. Which tools exist is decided by the
    # plugins: each feature contributes its own groups (``features/toolkit.py``).
    from valuz_agent.features.toolkit import all_tool_groups, compose_toolsets
    from valuz_agent.integrations.toolkit_mcp_server import install_toolkit_toolsets

    base, lead = compose_toolsets(all_tool_groups() if toolkit is None else toolkit)
    install_toolkit_toolsets(base=base, lead=lead)


def wire_memory_triggers() -> None:
    """Wire the background memory extractor to its triggers (``oss-memory``).

    Idle trigger (memory-system-design §7): once a session goes quiet, review it and
    write durable memories through the same MemoryStore pipeline as the foreground
    tool. Task-finish trigger (§7.1): when a multi-agent task completes, graduate
    its durable multi-agent lessons + project progress into project memory.
    Runs right after ``init_kernel`` (which builds the toolkit the memory tools
    live in).
    """
    from valuz_agent.modules.memory.scheduler import (
        idle_scheduler,
        memory_scheduler,
        task_finish_scheduler,
    )

    # Real finalizers await the durable scheduler. Do not arm process-only timers.
    memory_scheduler.registered = True
    idle_scheduler._runner = None
    task_finish_scheduler._runner = None


async def start_memory_recovery() -> None:
    from valuz_agent.modules.memory.scheduler import memory_scheduler

    await memory_scheduler.start()


async def stop_memory_recovery() -> None:
    from valuz_agent.modules.memory.scheduler import memory_scheduler

    await memory_scheduler.stop()


async def bind_data_service(app: FastAPI) -> None:
    """Bind the host-mounted DataService (``/_internal/data``, dual-mounted at
    the legacy ``/internal/data`` — ADR-013) to its backend.

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
        from valuz_agent.ports.sandbox_credential import get_sandbox_credential_verifier

        ds_app.state.verifier = get_sandbox_credential_verifier()
        app.state._data_service_engine = engine
        # Unify host reads (sessions + events) through the DataService
        # (in-process), so reads never depend on the sandbox being alive. Bind
        # the in-process reader into the typed DataReader port.
        from valuz_agent.adapters.data_reader import bind_data_reader
        from valuz_agent.adapters.data_service_local import LocalDataServiceReader

        bind_data_reader(LocalDataServiceReader(store))
        # …and bind the host DATA PLANE onto the same store: non-runtime
        # kernel_client facades (reads + at-rest control writes + stranded
        # reset) run the kernel route semantics against the durable copy.
        from valuz_agent.adapters import kernel_client

        kernel_client.bind_host_data_store(lambda: store)
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
    """Reset genuinely-stranded ``running`` sessions from a previous process.

    Liveness-aware (``modules.sessions.recovery``): a ``running`` row whose
    sandbox scope still holds a live remote sandbox is left alone (the turn may
    be executing there — critical with multiple host replicas + per-scope
    sandboxes on one shared durable); only confirmed-dead sessions are reset,
    to ``idle`` + resumable ``host_restart`` (so ``recover_active_tasks`` can
    re-drive interrupted task members). Runs after ``init_kernel`` so the
    kernel store is reachable.
    """
    # In http mode the standalone kernel reconciles its own store at its own
    # startup (B2) — and without a sandbox allocator the host cannot prove the
    # kernel process is NOT mid-turn, so a host-side durable reset here could
    # clobber a live turn. Skip; the kernel's own boot scan covers it.
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
    kernel session rows are already reconciled (stranded members sit at
    ``idle`` + ``host_restart`` — stamped by the liveness-aware host recovery,
    or by the kernel's own boot scan on the ``local`` tier). Only ``active`` tasks
    are touched; ``paused`` (user-stopped) wait for explicit resume.
    """
    import logging

    from valuz_agent.adapters import kernel_client
    from valuz_agent.modules.tasks.orchestrator import task_orchestrator
    from valuz_agent.modules.tasks.sandbox_scope import resolve_sandbox_scope

    # Task sessions execute in their task's sandbox (one instance per task,
    # lead + members together). Bind the session→scope lookup so every EXEC op
    # on a task session routes there; non-task sessions fall back to
    # per-session scope. No-op routing under the default BootSingletonAllocator.
    kernel_client.bind_sandbox_scope_resolver(resolve_sandbox_scope)

    try:
        await task_orchestrator.recovery.recover_active_tasks()
    except Exception:  # noqa: BLE001 — startup must not block on bookkeeping
        logging.getLogger(__name__).exception("recover_active_tasks failed")


async def purge_tasks_of_deleted_projects() -> None:
    """Remove tasks whose project is gone. Must run BEFORE recovery.

    Project deletion now cascades, but installs created before that carry the
    orphans it used to leave: an ``active`` task with no kernel sessions, which
    recovery respawns against a dead session id and the health monitor then
    announces as "blocked" — for a project the user deleted, on a row with no
    delete path. Ordering matters: after ``recover_active_tasks`` this sweep
    would still fire that notification once per boot before cleaning up.

    Cross-owner, like the recovery sweep it precedes. A no-op once drained.
    """
    import logging

    from sqlalchemy import select

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.projects.models import ProjectRow
    from valuz_agent.modules.tasks.models import TaskRow
    from valuz_agent.modules.tasks.purge import purge_tasks

    try:
        async with async_unit_of_work(commit=False) as db:
            live = {pid for pid in (await db.execute(select(ProjectRow.id))).scalars().all()}
            orphans: dict[str, list[str]] = {}
            for task_id, user_id, project_id in (
                await db.execute(select(TaskRow.id, TaskRow.user_id, TaskRow.project_id))
            ).all():
                if project_id not in live:
                    orphans.setdefault(user_id, []).append(task_id)
        purged = 0
        for user_id, task_ids in orphans.items():
            purged += await purge_tasks(user_id, task_ids)
        if purged:
            logging.getLogger(__name__).warning(
                "tasks: purged %d task(s) whose project no longer exists", purged
            )
    except Exception:  # noqa: BLE001 — startup must not block on bookkeeping
        logging.getLogger(__name__).exception("deleted-project task sweep failed")


async def resolve_informational_notification_backlog() -> None:
    """Close notifications that report a FINISHED thing but were left open.

    Completions are ingested already-resolved, so they toast and land in
    history without occupying the action inbox. Rows written before that sat
    in "未处理" wearing a Resume button, and nothing else would ever clear
    them — a day of successful runs buried the failures that do need
    attention. One sweep at boot; a no-op once drained.
    """
    import logging

    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.notifications.datastore import NotificationDatastore

    try:
        async with async_unit_of_work() as db:
            closed = await NotificationDatastore(db).resolve_informational_backlog()
        if closed:
            logging.getLogger(__name__).info(
                "notifications: closed %d informational notification(s) left open", closed
            )
    except Exception:  # noqa: BLE001 — startup must not block on bookkeeping
        logging.getLogger(__name__).exception("informational notification sweep failed")


async def start_ui_push_transport() -> None:
    """Receive UI bus pushes made by other backend processes (OSS: no-op)."""
    from collections.abc import Mapping
    from typing import Any

    from valuz_agent.modules.plugin_ui.push import ui_push_hub
    from valuz_agent.ports.extensions import ext

    def deliver(message: Mapping[str, Any]) -> None:
        ui_push_hub.deliver_remote(message)

    await ext.ui_push_transport.start(deliver)


async def stop_ui_push_transport() -> None:
    from valuz_agent.ports.extensions import ext

    await ext.ui_push_transport.stop()


def warn_unreachable_plugin_hooks() -> None:
    """Name the plugins whose ``ctx.hooks`` / ``ctx.commands`` sessions won't see.

    They register in this process; a session runs in a separate kernel when the
    sandbox allocator is not the in-process one, or with ``VALUZ_KERNEL_MODE=
    http``. Such handlers belong in the kernel image (``VALUZ_KERNEL_HOOK_MODULES``).
    """
    import os

    from valuz_agent.plugin_host.hooks_api import plugin_hook_owners
    from valuz_agent.ports.extensions import ext
    from valuz_agent.ports.sandbox_allocator import BootSingletonAllocator

    remote_mode = os.environ.get("VALUZ_KERNEL_MODE", "inprocess").strip().lower() == "http"
    sandboxed = not isinstance(ext.sandbox_allocator, BootSingletonAllocator)
    if not (remote_mode or sandboxed):
        return
    owners = plugin_hook_owners()
    if owners:
        logging.getLogger(__name__).warning(
            "plugins %s registered hook handlers/commands in the host process, but sessions "
            "run in %s; they will not apply there — ship them in the kernel image "
            "(VALUZ_KERNEL_HOOK_MODULES) instead",
            ", ".join(owners),
            "a sandboxed kernel" if sandboxed else "a remote kernel (VALUZ_KERNEL_MODE=http)",
        )


async def start_mcp_session_managers(app: FastAPI, managers: Sequence[Any] | None = None) -> None:
    """Bring the in-process MCP session managers online.

    FastMCP's ``StreamableHTTPSessionManager`` is started via an
    async context manager. When mounted as a Starlette sub-app under
    FastAPI the parent's ``lifespan`` doesn't propagate into the
    sub-app, so we drive the context manager manually and stash the
    AsyncExitStack on ``app.state`` so the matching shutdown handler
    can tear it down cleanly.

    Without this, every MCP request would terminate with
    ``Session terminated`` because the session manager's background
    task wouldn't be running.

    ``managers`` are the zero-arg context-manager factories (``Ref``) of the mounts
    the composed plugins registered, in canonical order. ``None`` -- direct callers --
    starts the built-in servers, plus the dsh bridge where the manager is enabled.
    """
    from contextlib import AsyncExitStack

    from valuz_agent.plugin_host.registry import resolve_ref

    stack = AsyncExitStack()

    from valuz_agent.infra.config import settings as _settings

    if not _settings.is_http_kernel:
        # The kernel's ``/mcp/toolkit/{session_id}`` bridge (codex's path to
        # kernel-owned ToolDefs, e.g. PTC's execute_code) is mounted on the
        # host app in in-process mode (api/app.py); its
        # StreamableHTTPSessionManager must be running or every request dies
        # with "Session terminated". The standalone kernel runs it inside its
        # own lifespan instead.
        from app.mcp_toolkit_router import mcp_router_lifespan

        await stack.enter_async_context(mcp_router_lifespan())
    await stack.__aenter__()
    if managers is None:
        from valuz_agent.features.mounts import default_session_managers

        managers = default_session_managers()
    for run in managers:
        await stack.enter_async_context(resolve_ref(run)())
    app.state.docs_mcp_stack = stack


async def stop_mcp_session_managers(app: FastAPI) -> None:
    stack = getattr(app.state, "docs_mcp_stack", None)
    if stack is not None:
        await stack.__aexit__(None, None, None)
        app.state.docs_mcp_stack = None


async def start_automation_runtime(app: FastAPI) -> None:
    """Start the deployment-bound automation scheduling/runtime transport."""
    from valuz_agent.ports.extensions import ext

    await ext.automation_runtime.startup()


async def start_task_health_monitor(app: FastAPI) -> None:
    """Task watchdog (``oss-tasks``): detect a lead that died without finalizing
    (the hole boot recovery can't see mid-process) → mark blocked so it surfaces +
    resumes."""
    from valuz_agent.modules.tasks.recovery import task_health_monitor

    await task_health_monitor.startup()


async def start_docs_auto_discovery(app: FastAPI) -> None:
    """Knowledge-base auto-discovery scanner (``oss-knowledge``)."""
    if not _startup_user_content_enabled():
        logger.info("startup user-content initialization disabled; docs scanner skipped")
        return

    from valuz_agent.modules.docs.scheduler import start_auto_discovery

    start_auto_discovery()


async def start_skill_auto_scan(app: FastAPI) -> None:
    """Skill-directory auto-scan (``oss-skills``)."""
    if not _startup_user_content_enabled():
        logger.info("startup user-content initialization disabled; skills scanner skipped")
        return

    from valuz_agent.modules.skills.scheduler import start_skill_auto_scan as _start

    _start()


async def start_backup_scheduler(app: FastAPI) -> None:
    """Scheduled local backups (``oss-backup``)."""
    if not _startup_user_content_enabled():
        logger.info("startup user-content initialization disabled; backup scheduler skipped")
        return

    from valuz_agent.modules.backup.scheduler import start_backup_scheduler as _start

    _start()


async def start_host_background_services(app: FastAPI) -> None:
    """Start non-automation host monitors and optional content scanners.

    The aggregate of the per-feature steps above, in their historical order; the
    plugin host runs the steps individually (each owned by its feature), this stays
    for embedding callers and tests that start "the host background services".
    """
    await start_task_health_monitor(app)

    if not _startup_user_content_enabled():
        logger.info("startup user-content initialization disabled; docs/skills scanners skipped")
        return

    await start_docs_auto_discovery(app)
    await start_skill_auto_scan(app)
    await start_backup_scheduler(app)


async def start_automation_runner(app: FastAPI) -> None:
    """Backward-compatible aggregate used by older embedding tests/callers."""
    await start_automation_runtime(app)
    await start_host_background_services(app)


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
    (pymupdf4llm) run in a separate process so their GIL-bound
    work can't stall the event loop; warming here pays the spawn + import cost
    at boot instead of on the first upload. Best-effort, never fatal."""
    from valuz_agent.infra import parse_pool

    try:
        parse_pool.warm()
    except Exception:  # noqa: BLE001
        pass


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


def resolve_marketplace_index() -> None:
    """Kick off the once-per-process market index candidate race in the
    background so the endpoint is settled by the time the marketplace UI
    makes its first request. The outcome (winner or nothing-reachable) is
    final for the process lifetime — requests never re-probe. No-op when an
    explicit ``marketplace_index_base_url`` is configured. Best-effort,
    never fatal."""
    from valuz_agent.modules.marketplace.market_index import resolve_index_in_background

    try:
        resolve_index_in_background()
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

    # Builtin plugins (e.g. office) sync alongside the bundled skill trees —
    # same boot step, same idempotent content-hash convergence.
    from valuz_agent.integrations.bundled_plugins import sync_bundled_builtin_plugins

    try:
        await sync_bundled_builtin_plugins(owner)
    except Exception:
        logger.exception("builtin plugin sync failed")

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
    #
    # On an out-of-band edit under the user's skill root the watcher re-indexes
    # (owner-scoped ``startup_scan``) so the catalog read path reflects the
    # change within ~300 ms instead of waiting for the next auto-scan tick.
    async def _reindex_user_skills() -> None:
        gen = get_skill_service_for_user(owner)
        svc = await gen.__anext__()
        try:
            indexed = await svc.startup_scan(owner)
            logger.debug("skill file-watcher: reindexed %d skill(s)", indexed)
        finally:
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

    watcher = SkillFileWatcher(_reindex_user_skills)
    user_root = _default_user_skill_root(owner)
    if user_root.exists():
        watcher.add_path(user_root)
    app.state.skill_watcher = watcher
    asyncio.get_event_loop().create_task(watcher.start())


async def stop_automation_runtime(app: FastAPI) -> None:
    """Stop the deployment-bound automation scheduling/runtime transport."""
    from valuz_agent.ports.extensions import ext

    await ext.automation_runtime.shutdown()


async def stop_task_health_monitor(app: FastAPI) -> None:
    from valuz_agent.modules.tasks.recovery import task_health_monitor

    await task_health_monitor.shutdown()


async def stop_docs_auto_discovery(app: FastAPI) -> None:
    from valuz_agent.modules.docs.scheduler import stop_auto_discovery

    stop_auto_discovery()


async def stop_skill_auto_scan(app: FastAPI) -> None:
    from valuz_agent.modules.skills.scheduler import stop_skill_auto_scan as _stop

    _stop()


async def stop_backup_scheduler(app: FastAPI) -> None:
    from valuz_agent.modules.backup.scheduler import stop_backup_scheduler as _stop

    _stop()


async def stop_agent_channels(app: FastAPI) -> None:
    """Stop the IM channel long connections (``oss-channels``)."""
    from valuz_agent.integrations.wecom_aibot_long_connection import wecom_aibot_supervisor

    await wecom_aibot_supervisor.shutdown()

    from valuz_agent.integrations.feishu_long_connection import feishu_supervisor

    await feishu_supervisor.shutdown()


async def stop_skill_watcher(app: FastAPI) -> None:
    watcher = getattr(app.state, "skill_watcher", None)
    if watcher is not None:
        await watcher.stop()


async def stop_host_background_services(app: FastAPI) -> None:
    """Stop non-automation host monitors and optional content scanners (the
    aggregate of the per-feature stop steps, in their historical order)."""
    await stop_task_health_monitor(app)
    await stop_docs_auto_discovery(app)
    await stop_skill_auto_scan(app)
    await stop_backup_scheduler(app)
    await stop_agent_channels(app)
    await stop_skill_watcher(app)


async def stop_automation_runner(app: FastAPI) -> None:
    """Backward-compatible aggregate used by older embedding tests/callers."""
    await stop_host_background_services(app)
    await stop_automation_runtime(app)


async def start_decision_aggregator(app: FastAPI) -> None:
    """ADR-022: kick off the global Decision Inbox aggregator.

    Scans active sessions for unresolved ``requires_action`` pendings,
    then subscribes to the kernel broadcast bus for live updates.
    Lives for the whole app lifetime.
    """
    from valuz_agent.modules.decisions.aggregator import (
        DecisionAggregator,
        set_decision_aggregator,
    )

    agg = DecisionAggregator()
    await agg.start()
    set_decision_aggregator(agg)
    app.state.decision_aggregator = agg


async def stop_decision_aggregator(app: FastAPI) -> None:
    agg = getattr(app.state, "decision_aggregator", None)
    if agg is not None:
        await agg.stop()
    # The notification ledger is durable and its SSE stream holds no in-process
    # subscriber state (it polls the table) — open streams end on their own when
    # the client disconnects / the server shuts down. Nothing to release here.


def mark_boot_complete() -> None:
    """Flip the system status from ``starting`` → ``running``.

    Registered last so every other startup hook gets a chance to
    push a ``record_warning(...)`` first — anything that landed in
    the warnings buffer turns ``status`` into ``degraded`` instead.
    """
    from valuz_agent.modules.system.service import record_boot_complete

    record_boot_complete()


async def start_post_boot_agent_channels(app: FastAPI) -> None:
    """Schedule channel long connections after the host has finished booting."""
    from valuz_agent.modules.channels.config import agent_channels_active

    if not agent_channels_active():
        logger.info("agent channel long connections disabled for this deployment")
        return

    from valuz_agent.integrations.wecom_aibot_long_connection import wecom_aibot_supervisor

    await wecom_aibot_supervisor.startup()

    from valuz_agent.integrations.feishu_long_connection import feishu_supervisor

    await feishu_supervisor.startup()


async def stop_managed_browser() -> None:
    """Best-effort: let the browser engine release what it holds so app exit
    doesn't leave an orphan visible Chrome (local engine). The isolated profile
    persists (login state survives); only the window/daemon closes. Bounded +
    never blocks teardown."""
    try:
        from valuz_agent.ports.extensions import ext

        await asyncio.wait_for(ext.browser_engine.shutdown(), timeout=10.0)
    except Exception:  # noqa: BLE001 — shutdown best-effort
        logger.warning("managed browser stop on shutdown failed", exc_info=True)


async def shutdown_kernel() -> None:
    from valuz_agent.boot.kernel import shutdown_kernel_with_network_egress

    await shutdown_kernel_with_network_egress()


def shutdown_tracing() -> None:
    """Flush + stop Langfuse tracing. Runs AFTER ``shutdown_kernel`` so every
    runtime has stopped emitting spans before the final flush. No-op when
    tracing was never enabled."""
    from src.core.tracing import shutdown_tracing as _shutdown_tracing

    _shutdown_tracing()


async def stop_dsh_manager() -> None:
    """Stop the resident dsh manager host, if one was started on demand."""
    from valuz_agent.modules.dsh_plugins.manager import get_dsh_manager

    try:
        await get_dsh_manager().stop()
    except Exception:
        logger.warning("dsh manager host stop failed", exc_info=True)


async def start_dsh_manager_if_plugins_installed() -> None:
    """Bring the dsh manager host up in the background when the managed
    profile carries user-installed bundles, so their tools reach every
    runtime's sessions (the always-on ``valuz-dsh-plugins`` MCP server) without
    the user opening Settings → Extensions first. Never blocks boot."""
    import asyncio

    from valuz_agent.modules.dsh_plugins.manager import (
        get_dsh_manager,
        has_user_bundles,
        unavailable_reason,
    )

    if unavailable_reason() is not None or not has_user_bundles():
        return

    async def _start() -> None:
        try:
            await get_dsh_manager().ensure_started()
        except Exception:
            logger.warning("dsh manager host autostart failed", exc_info=True)

    asyncio.get_running_loop().create_task(_start())


async def cleanup_superseded_app_plugin_versions() -> None:
    """Remove third-party plugin version directories an update superseded.

    Runs after boot (``POST_BOOT``): the previous version stays on disk while the
    process that updated it is alive, then goes at the next start. Never fails boot.
    """
    import asyncio

    from valuz_agent.modules.app_plugins.maintenance import cleanup_superseded_versions

    try:
        await asyncio.to_thread(cleanup_superseded_versions)
    except Exception:  # noqa: BLE001 — housekeeping must not break startup
        logger.warning("third-party plugin cleanup failed", exc_info=True)

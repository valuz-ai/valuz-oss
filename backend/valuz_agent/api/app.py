import logging
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp

from valuz_agent.api.middleware import (
    ErrorHandlerMiddleware,
    LocaleMiddleware,
    TimingMiddleware,
)
from valuz_agent.boot import lifespan
from valuz_agent.boot.phases import BootPlan
from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.plugin_host import PluginHost

logger = logging.getLogger("valuz_agent.api")

LifespanHook = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def _oss_lifespan(app: FastAPI, plan: BootPlan | None) -> AbstractAsyncContextManager[None]:
    # ``lifespan`` is read at call time (not bound at import): embedders and tests
    # may replace it on this module.
    return lifespan(app) if plan is None else lifespan(app, plan)


def _build_lifespan(
    lifespan_hooks: list[LifespanHook] | None, plan: BootPlan | None = None
) -> LifespanHook:
    if not lifespan_hooks and plan is None:
        return lifespan

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with _oss_lifespan(app, plan):
            async with AsyncExitStack() as stack:
                for hook in lifespan_hooks or []:
                    await stack.enter_async_context(hook(app))
                yield

    return _lifespan


def _compose_bare_host() -> PluginHost:
    """A bare OSS app composes its own host: the OSS plugins, with the persisted
    plugin prefs (``<data root>/plugins.json``) applied, recorded as the
    process's active host so ``/v1/builtin-plugins`` can report it."""
    from valuz_agent.features import compose_oss_host
    from valuz_agent.plugin_host import load_host_with_prefs, set_active_plugin_host

    host = compose_oss_host()
    load_host_with_prefs(host)
    set_active_plugin_host(host)
    return host


def create_app(
    api_prefix: list[str] | None = None,
    lifespan_hooks: list[LifespanHook] | None = None,
    plugin_host: PluginHost | None = None,
) -> FastAPI:
    """Build the host FastAPI application.

    ``api_prefix`` prepends one or more base paths to the whole public HTTP
    surface (host routers + overlay ``module_registry`` routes + in-process
    kernel routers) so the backend can sit behind a shared-host ingress that
    namespaces it by path. ``None`` (default) falls back to
    ``settings.api_prefix`` (env ``VALUZ_API_PREFIX``); an empty result → routes
    served at their native paths (behaviour unchanged). The internal sub-apps
    (``/_internal/data`` + ``/_internal/mcp/*``) are reached server-side via
    ``backend_base_url``; they are mounted under EACH configured base path (not
    just root) so a kernel whose ``backend_base_url`` carries the ingress
    sub-path — e.g. a cloud sandbox reachable only through it — resolves them too.
    ADR-013 renamed these from ``/internal/*`` to ``/_internal/*`` —
    ``/_internal/*`` is the only mount; stale session snapshots self-heal via
    the always-on MCP re-stamp (see ``_mount_internal`` below).

    ``lifespan_hooks`` lets overlays contribute resource lifecycles without
    mutating the returned app with deprecated startup/shutdown events.

    ``plugin_host`` is the composition. The OSS backend is itself a set of plugins
    (``valuz_agent.features.oss_plugins()``): each registers the routers, boot steps,
    internal mounts and harness tools it owns, and this factory *assembles* them
    from the host's registry. ``None`` (bare OSS) composes the OSS plugins itself
    and applies the plugin prefs; a caller that composed a host -- the commercial
    overlay builds ``oss_plugins()`` + its own + the edition's -- passes it already
    loaded (and calls ``plugin_host.attach_app(app)`` afterwards).
    """
    if getattr(sys, "frozen", False):
        from valuz_agent.infra.local_identity import resolve_local_user_id

        _env_path = fs_registry.data_dir(resolve_local_user_id()) / ".env"
    else:
        _env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(_env_path)

    bare = plugin_host is None
    host = _compose_bare_host() if plugin_host is None else plugin_host
    if not host.is_active("oss-core"):
        raise ValueError(
            "create_app(plugin_host=...) needs a loaded host that includes the OSS "
            "plugins (valuz_agent.features.oss_plugins())"
        )
    registry = host.registry

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        lifespan=_build_lifespan(lifespan_hooks, BootPlan.from_registry(registry)),
    )

    @app.exception_handler(RequestValidationError)
    async def _log_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default 422 handler returns the field-level detail in the
        # response body but logs nothing, so a request-body validation failure
        # shows up as a bare "422 Unprocessable Content" with no clue which
        # field was wrong. Log the offending path + the per-field errors so the
        # cause is visible in the backend log, then return the standard body.
        logger.warning(
            "422 validation error on %s %s: %s",
            request.method,
            request.url.path,
            exc.errors(),
        )
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    from valuz_agent.ports.extensions import ext

    app.add_middleware(ErrorHandlerMiddleware)
    # Inside Timing, wrapping the routes: sets the owner ContextVar so every row
    # created during the request is stamped with the resolved user_id.
    # ``ext.auth_middleware`` is a ``(cls, kwargs)`` tuple — defaults to OSS's
    # AuthMiddleware; the overlay may swap in a subclass (e.g. to publish its own
    # per-request ContextVars with a reset boundary, with deps in ``kwargs``).
    _auth_cls, _auth_kwargs = ext.auth_middleware
    app.add_middleware(_auth_cls, **_auth_kwargs)
    # Outside auth, inside Timing: the locale must be bound before any handler
    # (or any ``t()`` inside auth failures) renders text.
    app.add_middleware(LocaleMiddleware)
    app.add_middleware(TimingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # The whole public HTTP surface is aggregated into one router so a global
    # ``api_prefix`` can be applied uniformly (mirrors valuz-server's factory).
    # Infra mounts (/_internal/data, /_internal/mcp/*) are added to ``app`` below
    # via ``_mount_internal`` — mounted under each base path so a sandbox that can
    # only reach the host through the prefixed ingress resolves them too.
    api = APIRouter()
    # The host's own routers, in canonical order (``features/order.ROUTE_REFS``).
    # Resolved only now -- after every plugin has applied -- so a route module's
    # import-time snapshot (e.g. the connector catalog) sees what overlays contributed.
    from valuz_agent.features.order import ROUTE_SLOTS

    for route_entry in registry.routes(ROUTE_SLOTS):
        api.include_router(route_entry.resolve())

    # Apply overlay-registered modules into the same aggregate router so they
    # inherit the prefix too; middleware is not path-based and stays on the app
    # (ADR-001 §2.1).
    from valuz_agent.infra.middleware_registry import middleware_registry
    from valuz_agent.infra.module_registry import module_registry

    module_registry.apply(api)
    middleware_registry.apply(app)

    # Agent Harness V5 kernel — prefix /kernel/v1/* (ADR-013; the kernel's own
    # upstream default is /api/v1/*, overridden host-wide via KERNEL_API_PREFIX
    # — see valuz_agent.boot.kernel). Valuz business routes stay at /v1/* and
    # are progressively migrated to call into the kernel via
    # valuz_agent.adapters.* helpers. NOT mounted in http mode: the kernel runs
    # as a separate process and serves /kernel/v1/* itself; mounting the
    # in-process routers here would shadow it with a ghost kernel bound to a
    # different (host) database (B3).
    if not settings.is_http_kernel:
        from valuz_agent.boot.kernel import get_kernel_routers

        for kernel_router in get_kernel_routers():
            api.include_router(kernel_router)

        # Codex reaches kernel-owned ToolDefs (e.g. PTC's execute_code)
        # through the kernel's ``/mcp/toolkit/{session_id}`` bridge; the
        # kernel app serves it standalone, the host must serve it in-process.
        # Root mount on the OUTER app — codex's ``CODEX_TOOLKIT_BASE_URL``
        # carries no api prefix. The session manager behind it is started by
        # ``boot/steps.start_mcp_session_managers``.
        from app.mcp_toolkit_router import mount_mcp_router

        mount_mcp_router(app)

    # Mount the aggregate surface under each configured base path. ``None`` →
    # fall back to settings; an empty result → a single mount at "" (native
    # paths, unchanged). Multiple entries (e.g. ["", "/valuz-backend"]) → the
    # surface is served under each base at once.
    prefixes = api_prefix if api_prefix is not None else settings.api_prefix
    resolved_prefixes = prefixes or [""]
    for _prefix in resolved_prefixes:
        app.include_router(api, prefix=_prefix)

    # Internal sub-apps (DataService + in-process MCP servers) that a sandboxed
    # kernel reaches over HTTP+JWT via ``backend_base_url``. Mount each under
    # EVERY configured base path, not just root: a kernel whose
    # ``backend_base_url`` carries an ingress sub-path — e.g. a cloud sandbox
    # reachable ONLY through ``/valuz-backend/*`` (the internal cluster address is
    # unroutable from the sandbox) — must resolve ``{backend_base_url}/_internal/*``
    # too. With no ``api_prefix`` (the default, and every desktop build) this is a
    # single root mount, so behaviour is unchanged.
    #
    # ADR-013: the loopback plane lives at ``/_internal/...`` only. No legacy
    # ``/internal/...`` mount — a session snapshot that still carries a
    # pre-rename harness URL is self-healed by the always-on MCP re-stamp
    # (``modules/sessions/capabilities.refresh_always_on_mcp_for_session``
    # rewrites the persisted trio with current URLs on every turn).
    def _mount_internal(path: str, subapp: ASGIApp) -> None:
        for _p in resolved_prefixes:
            app.mount(f"{_p}{path}", subapp)

    # The host's internal mounts, in canonical order (``features/order.MOUNT_SLOTS``):
    # the in-process MCP servers (docs, automations, playbooks, connectors, the harness
    # toolkit) -- Starlette ASGI sub-apps, because FastMCP owns its own request
    # pipeline (streamable HTTP) -- and the host-mounted DataService (kernel three-table
    # CRUD over /rpc/{op}; its store + JWT verifier are bound in the lifespan by
    # ``steps.bind_data_service``). Each is owned by a plugin: a disabled feature mounts
    # nothing, and sessions are not handed its server. The MCP client gets a URL of the
    # form ``{backend_base_url}/_internal/mcp/docs/{session_id}/mcp`` -- see
    # ``adapters/capability_resolver.py``.
    from valuz_agent.features.order import MOUNT_SLOTS
    from valuz_agent.plugin_host.registry import resolve_ref
    from valuz_agent.ports.extensions import ext
    from valuz_agent.ports.mcp_always_on import set_enabled_builtin_servers

    mounted = registry.mounts(MOUNT_SLOTS)
    for mount_entry in mounted:
        mount = mount_entry.mount
        if mount.spec is not None:
            continue
        factory = resolve_ref(mount.build)
        _mount_internal(mount.path, factory(app) if mount.takes_app else factory())
    set_enabled_builtin_servers(m.mount.server for m in mounted if m.mount.server)

    # Always-on servers registered as specs (``ext.always_on_mcp_specs``: the dsh bridge
    # of the ``oss-dsh-plugins`` plugin and whatever an overlay registered), through the
    # same seam. The resolver advertises them as ``{backend_base_url}{path}/mcp`` — the
    # identical shape it uses for the built-ins above — so they need the identical
    # mounting, and an edition mounting by hand in ``register_api`` has to rediscover
    # that. One that mounted at the bare path only shipped a spec whose advertised URL
    # 404'd under every prefixed deployment. Specs registered before ``create_app``
    # (plugins append them while they apply) are picked up here; a spec without a
    # factory is an edition that still mounts its own.
    for _spec in ext.always_on_mcp_specs:
        if _spec.app_factory is not None:
            _mount_internal(_spec.path, _spec.app_factory())

    if bare:
        host.attach_app(app)

    # Startup/shutdown orchestration lives in ``boot/lifespan.py`` and runs the boot
    # steps the plugins registered (bound via ``lifespan=`` above). The startup order
    # is load-bearing; see ``boot/phases.py``.
    return app


def __getattr__(name: str) -> FastAPI:
    # ``valuz_agent.api.app:app`` used to be built at import time. Composing an app
    # has side effects now (the active plugin host), so it is built on first access.
    if name == "app":
        return create_app()
    raise AttributeError(name)

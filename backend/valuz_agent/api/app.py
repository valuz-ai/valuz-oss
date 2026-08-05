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

from valuz_agent.api.middleware import (
    ErrorHandlerMiddleware,
    TimingMiddleware,
)
from valuz_agent.api.routes.activity import router as activity_router
from valuz_agent.api.routes.agent_templates import router as agent_templates_router
from valuz_agent.api.routes.agents import router as agents_router
from valuz_agent.api.routes.analytics import router as analytics_router
from valuz_agent.api.routes.artifacts import router as artifacts_router
from valuz_agent.api.routes.automations import router as automations_router
from valuz_agent.api.routes.backup import router as backup_router
from valuz_agent.api.routes.browser import router as browser_router
from valuz_agent.api.routes.channels import router as channels_router
from valuz_agent.api.routes.citations import router as citations_router
from valuz_agent.api.routes.connectors import router as connectors_router
from valuz_agent.api.routes.docs import router as docs_router
from valuz_agent.api.routes.document_research import router as document_research_router
from valuz_agent.api.routes.files import router as files_router
from valuz_agent.api.routes.marketplace import router as marketplace_router
from valuz_agent.api.routes.memory import router as memory_router
from valuz_agent.api.routes.notifications import router as notifications_router
from valuz_agent.api.routes.onboarding import router as onboarding_router
from valuz_agent.api.routes.parser import settings_router as parser_settings_router
from valuz_agent.api.routes.parser import system_router as parser_system_router
from valuz_agent.api.routes.projects import router as projects_router
from valuz_agent.api.routes.providers import router as providers_router
from valuz_agent.api.routes.resources import router as resources_router
from valuz_agent.api.routes.runs import router as runs_router
from valuz_agent.api.routes.runtimes import router as runtimes_router
from valuz_agent.api.routes.sessions import router as sessions_router
from valuz_agent.api.routes.settings import router as settings_router
from valuz_agent.api.routes.skills import router as skills_router
from valuz_agent.api.routes.stream import router as stream_router
from valuz_agent.api.routes.system import router as system_router
from valuz_agent.api.routes.tasks import router as tasks_router
from valuz_agent.api.routes.worktrees import router as worktrees_router
from valuz_agent.boot import lifespan
from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import fs_registry

logger = logging.getLogger("valuz_agent.api")

LifespanHook = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def _build_lifespan(lifespan_hooks: list[LifespanHook] | None) -> LifespanHook:
    if not lifespan_hooks:
        return lifespan

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with lifespan(app):
            async with AsyncExitStack() as stack:
                for hook in lifespan_hooks:
                    await stack.enter_async_context(hook(app))
                yield

    return _lifespan


def create_app(
    api_prefix: list[str] | None = None,
    lifespan_hooks: list[LifespanHook] | None = None,
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
    """
    if getattr(sys, "frozen", False):
        from valuz_agent.infra.local_identity import resolve_local_user_id

        _env_path = fs_registry.data_dir(resolve_local_user_id()) / ".env"
    else:
        _env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(_env_path)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        lifespan=_build_lifespan(lifespan_hooks),
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
    api.include_router(providers_router)
    api.include_router(channels_router)
    api.include_router(citations_router)
    api.include_router(document_research_router)
    api.include_router(connectors_router)
    api.include_router(browser_router)
    api.include_router(runs_router)
    api.include_router(activity_router)
    api.include_router(runtimes_router)
    api.include_router(system_router)
    api.include_router(projects_router)
    api.include_router(files_router)
    api.include_router(artifacts_router)
    api.include_router(worktrees_router)
    api.include_router(sessions_router)
    api.include_router(stream_router)
    api.include_router(skills_router)
    api.include_router(docs_router)
    api.include_router(automations_router)
    api.include_router(backup_router)
    api.include_router(notifications_router)
    api.include_router(agents_router)
    api.include_router(agent_templates_router)
    api.include_router(marketplace_router)
    api.include_router(tasks_router)
    api.include_router(analytics_router)
    api.include_router(resources_router)
    api.include_router(onboarding_router)
    api.include_router(settings_router)
    api.include_router(memory_router)
    # Parser routes live in a separate module because they straddle the
    # ``/v1/system`` and ``/v1/settings`` namespaces (setup jobs vs.
    # routing config). One module, two ``APIRouter`` instances.
    api.include_router(parser_system_router)
    api.include_router(parser_settings_router)

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
    def _mount_internal(path: str, subapp: object) -> None:
        for _p in resolved_prefixes:
            app.mount(f"{_p}{path}", subapp)

    # In-process docs MCP server. Mounted as a Starlette ASGI sub-app
    # because FastMCP owns its own request pipeline (streamable HTTP
    # protocol). The kernel's MCP client gets an URL of the form
    # ``{backend_base_url}/_internal/mcp/docs/{session_id}/mcp`` injected
    # into ``session.mcp_servers`` whenever the project has any KB
    # binding — see ``adapters/capability_resolver.py``.
    from valuz_agent.integrations.docs_mcp_server import build_docs_mcp_asgi

    _mount_internal("/_internal/mcp/docs", build_docs_mcp_asgi())

    # Host-mounted DataService (kernel three-table CRUD over /rpc/{op}). Mounted
    # here as a sub-app; its store + JWT verifier are bound in the lifespan
    # (``steps.bind_data_service``) once the backend is known. A sandbox kernel
    # reaches this over HTTP+JWT instead of holding a DB credential. /health +
    # /openapi.json work pre-bind; /rpc is 401 until bound.
    from valuz_agent.boot.kernel import make_data_service_placeholder

    app.state.data_service_app = make_data_service_placeholder()
    _mount_internal("/_internal/data", app.state.data_service_app)

    # In-process automations MCP server — exposes the ``automation`` tool
    # to every session. Replaces the legacy ``cronjob`` tool per ADR-021.
    from valuz_agent.integrations.automations_mcp_server import (
        build_automations_mcp_asgi,
    )

    _mount_internal("/_internal/mcp/automations", build_automations_mcp_asgi())

    # In-process connectors MCP server — exposes the ``create_mcp`` tool to
    # every session so the agent can create connectors on behalf of the user.
    from valuz_agent.integrations.connectors_mcp_server import (
        build_connectors_mcp_asgi,
    )

    _mount_internal("/_internal/mcp/connectors", build_connectors_mcp_asgi())

    # In-process toolkit MCP server — serves the harness tools (dispatch /
    # orchestration / memory / submit_skill) per toolset. Sessions reference
    # it via an ``mcp_servers`` entry named ``harness`` so every runtime
    # consumes host tools through its standard MCP client path.
    from valuz_agent.integrations.toolkit_mcp_server import build_toolkit_mcp_asgi

    _mount_internal("/_internal/mcp/toolkit/base", build_toolkit_mcp_asgi("base"))
    _mount_internal("/_internal/mcp/toolkit/lead", build_toolkit_mcp_asgi("lead"))

    # Startup/shutdown orchestration lives in ``boot/lifespan.py`` (bound via
    # ``lifespan=lifespan`` above). The startup order is load-bearing; see the
    # order table in the boot-refactor exec plan.
    return app


app = create_app()

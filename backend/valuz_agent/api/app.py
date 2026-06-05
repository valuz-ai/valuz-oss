import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from valuz_agent.api.middleware import ErrorHandlerMiddleware, TimingMiddleware
from valuz_agent.api.routes.agents import router as agents_router
from valuz_agent.api.routes.analytics import router as analytics_router
from valuz_agent.api.routes.automations import router as automations_router
from valuz_agent.api.routes.connectors import router as connectors_router
from valuz_agent.api.routes.decisions import router as decisions_router
from valuz_agent.api.routes.docs import router as docs_router
from valuz_agent.api.routes.onboarding import router as onboarding_router
from valuz_agent.api.routes.parser import settings_router as parser_settings_router
from valuz_agent.api.routes.parser import system_router as parser_system_router
from valuz_agent.api.routes.projects import router as workspaces_router
from valuz_agent.api.routes.providers import router as providers_router
from valuz_agent.api.routes.resources import router as resources_router
from valuz_agent.api.routes.runs import router as runs_router
from valuz_agent.api.routes.runtimes import router as runtimes_router
from valuz_agent.api.routes.sessions import router as sessions_router
from valuz_agent.api.routes.settings import router as settings_router
from valuz_agent.api.routes.skills import router as skills_router
from valuz_agent.api.routes.system import router as system_router
from valuz_agent.api.routes.tasks import router as tasks_router
from valuz_agent.boot import lifespan
from valuz_agent.infra.config import settings


def create_app() -> FastAPI:
    if getattr(sys, "frozen", False):
        _env_path = settings.data_dir / ".env"
    else:
        _env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(_env_path)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(TimingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(providers_router)
    app.include_router(connectors_router)
    app.include_router(runs_router)
    app.include_router(runtimes_router)
    app.include_router(system_router)
    app.include_router(workspaces_router)
    app.include_router(sessions_router)
    app.include_router(skills_router)
    app.include_router(docs_router)
    app.include_router(automations_router)
    app.include_router(decisions_router)
    app.include_router(agents_router)
    app.include_router(tasks_router)
    app.include_router(analytics_router)
    app.include_router(resources_router)
    app.include_router(onboarding_router)
    app.include_router(settings_router)
    # Parser routes live in a separate module because they straddle the
    # ``/v1/system`` and ``/v1/settings`` namespaces (setup jobs vs.
    # routing config). One module, two ``APIRouter`` instances.
    app.include_router(parser_system_router)
    app.include_router(parser_settings_router)

    # Apply overlay-registered modules and middleware (ADR-001 §2.1).
    from valuz_agent.infra.middleware_registry import middleware_registry
    from valuz_agent.infra.module_registry import module_registry

    module_registry.apply(app)
    middleware_registry.apply(app)

    # Vendored Agent Harness V5 kernel — mounted at /api/v1/* (its native prefix).
    # Valuz business routes stay at /v1/* and are progressively migrated to call
    # into the kernel via valuz_agent.adapters.* helpers.
    from valuz_agent.boot.kernel import get_kernel_routers

    for kernel_router in get_kernel_routers():
        app.include_router(kernel_router)

    # In-process docs MCP server. Mounted as a Starlette ASGI sub-app
    # because FastMCP owns its own request pipeline (streamable HTTP
    # protocol). The kernel's MCP client gets an URL of the form
    # ``{backend_base_url}/internal/mcp/docs/{session_id}/mcp`` injected
    # into ``session.mcp_servers`` whenever the project has any KB
    # binding — see ``adapters/capability_resolver.py``.
    from valuz_agent.integrations.docs_mcp_server import build_docs_mcp_asgi

    app.mount("/internal/mcp/docs", build_docs_mcp_asgi())

    # In-process automations MCP server — exposes the ``automation`` tool
    # to every session. Replaces the legacy ``cronjob`` tool per ADR-021.
    from valuz_agent.integrations.automations_mcp_server import (
        build_automations_mcp_asgi,
    )

    app.mount("/internal/mcp/automations", build_automations_mcp_asgi())

    # In-process connectors MCP server — exposes the ``create_mcp`` tool to
    # every session so the agent can create connectors on behalf of the user.
    from valuz_agent.integrations.connectors_mcp_server import (
        build_connectors_mcp_asgi,
    )

    app.mount("/internal/mcp/connectors", build_connectors_mcp_asgi())

    # Startup/shutdown orchestration lives in ``boot/lifespan.py`` (bound via
    # ``lifespan=lifespan`` above). The startup order is load-bearing; see the
    # order table in the boot-refactor exec plan.
    return app


app = create_app()

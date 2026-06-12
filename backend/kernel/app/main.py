"""FastAPI application — entry point, lifespan, middleware, health check."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.config import AppConfig
from app.dependencies import init_dependencies, shutdown_dependencies
from app.mcp_toolkit_router import mcp_router_lifespan, mount_mcp_router
from app.routes.events import router as events_router
from app.routes.messages import router as messages_router
from app.routes.run import router as run_router
from app.routes.sessions import router as sessions_router
from app.routes.usage import router as usage_router
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

config = AppConfig()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # This lifespan only runs when the kernel app is served STANDALONE
    # (the host mounts the routers directly and never executes it). A
    # standalone kernel exposes session mutation, the full event stream
    # and the usage read surface — refuse to serve all of that
    # unauthenticated unless the operator opts in explicitly.
    if not config.auth_token:
        if os.getenv("KERNEL_ALLOW_UNAUTHENTICATED") != "1":
            raise RuntimeError(
                "Standalone kernel refuses to start without auth: set "
                "KERNEL_AUTH_TOKEN (bearer token required on every request). "
                "See backend/CLAUDE.md §kernel boundary for the development "
                "opt-out."
            )
        # The unauthenticated opt-in is loopback-only — and that must be
        # ENFORCED, not documented: AppConfig.host defaults to 0.0.0.0, so
        # a bare opt-in would otherwise expose session mutation, the full
        # event stream and the usage surface on every interface. IP
        # literals ONLY: a hostname like ``localhost`` resolves through
        # DNS/hosts at bind time and could be mapped to a non-loopback
        # address while a string check passes.
        if config.host not in ("127.0.0.1", "::1"):
            raise RuntimeError(
                "KERNEL_ALLOW_UNAUTHENTICATED=1 requires a loopback bind: "
                f"set HOST=127.0.0.1 (got {config.host!r})."
            )
    await init_dependencies(config)
    async with mcp_router_lifespan():
        yield
    await shutdown_dependencies()


app = FastAPI(
    title="Agent Harness",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


if config.auth_token:

    @app.middleware("http")
    async def _require_bearer_token(request: Request, call_next: Any) -> Any:
        """Standalone-kernel auth: every route except /health requires the
        configured bearer token. The WS run channel enforces the same token
        inside its handler (HTTP middleware doesn't cover websockets)."""
        if request.url.path != "/health":
            supplied = request.headers.get("authorization", "")
            if supplied != f"Bearer {config.auth_token}":
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        return await call_next(request)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(sessions_router)
app.include_router(messages_router)
app.include_router(run_router)
app.include_router(events_router)
app.include_router(usage_router)
mount_mcp_router(app)

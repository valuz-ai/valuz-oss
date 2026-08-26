"""FastAPI application — entry point, lifespan, middleware, health check."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app import config_gate
from app.config import AppConfig
from app.dependencies import init_dependencies, shutdown_dependencies
from app.dsh_user_questions_router import router as dsh_uq_router
from app.mcp_toolkit_router import mcp_router_lifespan, mount_mcp_router
from app.ptc_router import router as ptc_router
from app.routes.events import router as events_router
from app.routes.messages import router as messages_router
from app.routes.run import router as run_router
from app.routes.runtimes import router as runtimes_router
from app.routes.sessions import router as sessions_router
from app.routes.usage import router as usage_router
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

config = AppConfig()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global config
    # ── Config gate (snapshot-based sandboxes; default OFF) ───────────────
    # With KERNEL_CONFIG_WAIT=1 the process boots fully (all imports done)
    # and BLOCKS here until the host writes KERNEL_CONFIG_FILE, then rebuilds
    # ``config`` so every field re-reads the just-applied env (AppConfig
    # fields are default_factory lambdas — construction-time reads). This
    # lets a micro-VM snapshot freeze a fully-imported kernel and configure
    # it in milliseconds at resume instead of paying an interpreter restart.
    # Without the flag this is a single env check — boot path unchanged.
    # See app/config_gate.py for the full rationale + scope.
    if config_gate.gate_enabled():
        await config_gate.wait_for_config()
        config = AppConfig()

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


@app.middleware("http")
async def _require_bearer_token(request: Request, call_next: Any) -> Any:
    """Standalone-kernel auth: every route except /health requires the
    configured bearer token. The WS run channel enforces the same token
    inside its handler (HTTP middleware doesn't cover websockets).

    Registered UNCONDITIONALLY and checked at REQUEST time: under
    KERNEL_CONFIG_WAIT the auth token arrives at gate release, AFTER this
    module is imported — the previous import-time ``if config.auth_token``
    registration would have silently disabled auth for every gated kernel.
    Reads the module-global ``config``, which lifespan rebuilds at gate
    release. An empty token passes through, preserving the pre-existing
    unauthenticated behavior (which lifespan already restricts to the
    explicit loopback opt-in before serving anything).
    """
    token = config.auth_token
    # The PTC forwarding endpoint authenticates by one-shot execution token
    # (path segment) — the calling subprocess deliberately holds no kernel
    # bearer. See app/ptc_router.py.
    if token and "/v1/ptc/exec/" in request.url.path:
        return await call_next(request)
    # Same model for the dsh user-questions bridge: per-spawn token in the
    # path IS the credential. See app/dsh_user_questions_router.py.
    if token and "/v1/dsh/user-questions/" in request.url.path:
        return await call_next(request)
    if token and request.url.path != "/health":
        supplied = request.headers.get("authorization", "")
        if supplied != f"Bearer {token}":
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(sessions_router)
app.include_router(messages_router)
app.include_router(run_router)
app.include_router(events_router)
app.include_router(ptc_router)
app.include_router(dsh_uq_router)
app.include_router(usage_router)
app.include_router(runtimes_router)
mount_mcp_router(app)

# Self-extension control plane — only when running inside a sandbox that
# expects dynamic path grants (the Seatbelt provider sets the env). A
# vanilla standalone kernel never exposes it.
from app import sandbox_control  # noqa: E402

if sandbox_control.should_mount():
    app.include_router(sandbox_control.router)

# Credential refresh — only when a host manages this kernel's env through the
# config gate. The DataService bearer expires while the process runs, and a
# restart to pick up a new one would take the in-flight turn (and its
# background tasks) with it.
from app import credential_control  # noqa: E402

if credential_control.should_mount():
    app.include_router(credential_control.router)

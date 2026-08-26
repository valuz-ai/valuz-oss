"""Supported headless host runtime for non-HTTP execution workers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast

from fastapi import FastAPI


@asynccontextmanager
async def execution_runtime() -> AsyncIterator[None]:
    """Initialize only collaborators required to execute sessions/tasks.

    Schema migration belongs to deployment initialization or Web boot. This
    context deliberately does not start HTTP routers, automation loops, global
    recovery sweeps, MCP session managers, scanners, or local identity.
    """
    from valuz_agent.boot import steps

    host = cast(FastAPI, SimpleNamespace(state=SimpleNamespace()))
    steps.configure_structured_logging()
    steps.init_tracing()  # env-gated no-op; before the kernel, same as web boot
    await steps.init_kernel(host)
    await steps.bind_data_service(host)
    steps.install_binding_change_listener()
    try:
        yield
    finally:
        await steps.dispose_data_service(host)
        await steps.shutdown_kernel()
        steps.shutdown_tracing()  # final span flush — after every emitter stopped


__all__ = ["execution_runtime"]

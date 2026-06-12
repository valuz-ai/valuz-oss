"""Shared route-level validators for skills + mcp_servers payloads.

Sessions and agents both accept these structures (sessions per-turn,
agents as preset defaults), so the validation rules live here and both
routers import them. Validators raise ``HTTPException`` directly — they
are meant to be called from FastAPI handler bodies.
"""

from __future__ import annotations

import os

from fastapi import HTTPException

from app.schemas import (
    McpHttpServerConfigSchema,
    McpServerConfigSchema,
    McpStdioServerConfigSchema,
)
from src.core import (
    McpHttpServerConfig,
    McpServerConfig,
    McpStdioServerConfig,
)


def validate_skills(skills: list[str]) -> None:
    # ``os.path.isabs`` matches the OS the kernel runs on: on Windows it accepts
    # drive-rooted paths (``C:\\...``) and UNC paths, on POSIX it accepts ``/...``.
    # A bare ``startswith("/")`` check rejected every valid Windows skill path.
    for path in skills:
        if not os.path.isabs(path):
            raise HTTPException(
                status_code=400,
                detail=f"skills entries must be absolute paths; got '{path}'.",
            )


def validate_mcp_servers(servers: list[McpServerConfigSchema]) -> list[McpServerConfig]:
    seen: set[str] = set()
    out: list[McpServerConfig] = []
    for cfg in servers:
        if not cfg.name:
            raise HTTPException(status_code=400, detail="mcp_servers[].name must not be empty.")
        # NB: ``harness`` used to be reserved for the kernel's in-process
        # SDK MCP server. That server is retired — the host's toolkit MCP
        # server now legitimately claims the name (its tools keep the
        # ``mcp__harness__*`` identity models already know).
        if cfg.name in seen:
            raise HTTPException(
                status_code=400,
                detail=f"mcp_servers[].name must be unique; duplicate '{cfg.name}'.",
            )
        seen.add(cfg.name)
        if isinstance(cfg, McpStdioServerConfigSchema):
            if not cfg.command:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"mcp_servers[{cfg.name}].command must not be empty for stdio transport."
                    ),
                )
            out.append(
                McpStdioServerConfig(
                    name=cfg.name,
                    command=cfg.command,
                    args=tuple(cfg.args),
                    env=dict(cfg.env),
                    env_vars=tuple(cfg.env_vars),
                )
            )
        else:
            assert isinstance(cfg, McpHttpServerConfigSchema)
            if not cfg.url:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"mcp_servers[{cfg.name}].url must not be empty "
                        f"for {cfg.transport} transport."
                    ),
                )
            out.append(
                McpHttpServerConfig(
                    name=cfg.name,
                    url=cfg.url,
                    transport=cfg.transport,
                    headers=dict(cfg.headers),
                )
            )
    return out


def validate_registered_tools(tools: list) -> None:  # type: ignore[type-arg]
    """Reject tool declarations whose handlers aren't registered in-process.

    A ToolDef without a registry handler would surface to the model and
    fail at call time; catching it at create time gives a 400 with the
    offending names instead. (Moved from the former agents router when the
    agents table was removed — the embedded snapshot is validated at
    session creation now.)
    """
    from src.core.tool_registry import unresolved_tool_names

    unresolved = unresolved_tool_names(tuple(tools))
    if unresolved:
        names = ", ".join(sorted(unresolved))
        raise HTTPException(
            status_code=400,
            detail=f"Unknown or unregistered tools: {names}",
        )

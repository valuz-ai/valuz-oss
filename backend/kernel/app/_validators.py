"""Shared route-level validators for skills + mcp_servers payloads.

Sessions and agents both accept these structures (sessions per-turn,
agents as preset defaults), so the validation rules live here and both
routers import them. Validators raise ``HTTPException`` directly — they
are meant to be called from FastAPI handler bodies.
"""

from __future__ import annotations

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
    for path in skills:
        if not path.startswith("/"):
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
        if cfg.name == "harness":
            raise HTTPException(
                status_code=400,
                detail="mcp_servers[].name 'harness' is reserved for the kernel's own MCP server.",
            )
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

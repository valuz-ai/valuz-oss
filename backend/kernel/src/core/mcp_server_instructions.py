"""Bounded, trusted MCP Server Instructions injection.

MCP initialize instructions are server-controlled prompt text.  The kernel
therefore ignores them by default and reads them only from HTTP configs whose
Host already proved a catalog-pinned first-party identity.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import timedelta

import httpx
from src.core.types import McpHttpServerConfig, McpServerConfig

logger = logging.getLogger(__name__)

_INITIALIZE_TIMEOUT_SECONDS = 5.0
_MAX_INSTRUCTIONS_PER_SERVER = 8_000
_MAX_COMBINED_INSTRUCTIONS = 16_000


async def _fetch_http_server_instructions(cfg: McpHttpServerConfig) -> str:
    from mcp import ClientSession

    if cfg.transport == "sse":
        from mcp.client.sse import sse_client

        transport = sse_client(
            cfg.url,
            headers=dict(cfg.headers),
            timeout=_INITIALIZE_TIMEOUT_SECONDS,
            sse_read_timeout=_INITIALIZE_TIMEOUT_SECONDS,
        )
        http_client: httpx.AsyncClient | None = None
    else:
        from mcp.client.streamable_http import streamable_http_client

        http_client = httpx.AsyncClient(
            headers=dict(cfg.headers),
            timeout=_INITIALIZE_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        transport = streamable_http_client(cfg.url, http_client=http_client)

    try:
        async with asyncio.timeout(_INITIALIZE_TIMEOUT_SECONDS):
            async with transport as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(
                        seconds=_INITIALIZE_TIMEOUT_SECONDS
                    ),
                ) as session:
                    initialized = await session.initialize()
    finally:
        if http_client is not None:
            await http_client.aclose()

    instructions = getattr(initialized, "instructions", None)
    if not isinstance(instructions, str):
        return ""
    return instructions.strip()[:_MAX_INSTRUCTIONS_PER_SERVER]


async def append_trusted_mcp_server_instructions(
    base_instructions: str,
    servers: tuple[McpServerConfig, ...] | list[McpServerConfig],
) -> str:
    """Fetch trusted initialize instructions once and freeze them in a session.

    Fetches are concurrent, bounded, and fail-open.  Untrusted servers are not
    contacted at all for prompt text; a trusted server outage does not prevent
    the session from being created.
    """

    trusted = [
        cfg
        for cfg in servers
        if isinstance(cfg, McpHttpServerConfig) and cfg.server_instructions_trusted
    ]
    if not trusted:
        return base_instructions

    results = await asyncio.gather(
        *(_fetch_http_server_instructions(cfg) for cfg in trusted),
        return_exceptions=True,
    )
    blocks: list[str] = []
    remaining = _MAX_COMBINED_INSTRUCTIONS
    for cfg, result in zip(trusted, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning(
                "trusted MCP server %r instructions unavailable: %s",
                cfg.name,
                type(result).__name__,
            )
            continue
        content = result[:remaining].strip()
        if not content:
            continue
        remaining -= len(content)
        safe_name = html.escape(cfg.name, quote=True)
        blocks.append(
            f'<mcp-server-instructions name="{safe_name}" trust="builtin">\n'
            f"{content}\n"
            "</mcp-server-instructions>"
        )
        if remaining <= 0:
            break

    if not blocks:
        return base_instructions
    prefix = base_instructions.rstrip()
    addition = "\n\n".join(blocks)
    return f"{prefix}\n\n{addition}" if prefix else addition


__all__ = ["append_trusted_mcp_server_instructions"]

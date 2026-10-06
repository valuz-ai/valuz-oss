"""A tiny MCP server for the connector tools-API tests (run as a subprocess).

``python tiny_mcp_server.py http <port>`` serves streamable HTTP on 127.0.0.1;
``python tiny_mcp_server.py stdio`` serves stdio. ``write_note`` appends to the
file named by ``TINY_MCP_WRITE_LOG`` so a test can prove whether it ran.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


def build(port: int = 0) -> FastMCP:
    server = FastMCP("tiny", host="127.0.0.1", port=port)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @server.tool()
    def write_note(text: str) -> str:
        """Append a note (a write: no readOnlyHint)."""
        path = os.environ.get("TINY_MCP_WRITE_LOG")
        if path:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        return "written"

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    def drop_everything() -> str:
        """Explicitly not read-only."""
        return "dropped"

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def boom() -> str:
        """Always fails."""
        raise ValueError("kaboom")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def slow(seconds: float) -> str:
        """Sleep, then answer."""
        await asyncio.sleep(seconds)
        return "done"

    return server


if __name__ == "__main__":
    if sys.argv[1] == "http":
        build(int(sys.argv[2])).run("streamable-http")
    else:
        build().run("stdio")

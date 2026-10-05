"""A tiny stdio MCP server for the hook-bus MCP proxy tests."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

server = FastMCP("hook-bus-upstream")


@server.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return f"upstream:{text}"


@server.resource("memo://greeting")
def greeting() -> str:
    """A fixed resource."""
    return "hello from the upstream"


if __name__ == "__main__":
    server.run("stdio")

"""Verify the in-process docs MCP server boots and answers MCP initialize.

Spawns the FastAPI app under uvicorn on a random port, hits the docs MCP
streamable-HTTP endpoint with a real MCP client (the one in the ``mcp``
package), and confirms tool discovery returns ``doc_search`` and
``list_doc_scope``.
"""

import asyncio
import os
import threading
import time
import uuid
import socket
import urllib.request

import uvicorn

os.environ.setdefault("VALUZ_DATA_DIR", "/tmp/valuz-mcp-test")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main() -> None:
    port = _free_port()
    os.environ["VALUZ_BACKEND_BASE_URL"] = f"http://127.0.0.1:{port}"
    os.environ["VALUZ_INTERNAL_MCP_TOKEN_OVERRIDE"] = "test-secret-123"

    from valuz_agent.api.app import create_app

    cfg = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="info", lifespan="on")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    t.start()

    # Wait for the server to be ready.
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/docs", timeout=0.2)
            break
        except Exception:
            time.sleep(0.1)

    # 403 without the secret header — sanity check.
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/internal/mcp/docs/mcp", timeout=2.0)
        print("FAIL: expected 403 without header")
        return
    except urllib.error.HTTPError as e:
        assert e.code == 403, f"expected 403, got {e.code}"
        print("ok: 403 without internal header")

    # Full MCP handshake using the official client.
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"http://127.0.0.1:{port}/internal/mcp/docs/mcp"
    headers = {
        "X-Valuz-Internal": "test-secret-123",
        "X-Valuz-Session-Id": f"sess-{uuid.uuid4().hex}",
    }
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("tools:", names)
            assert "doc_search" in names, names
            assert "list_doc_scope" in names, names

    print("e2e ok")


asyncio.run(main())

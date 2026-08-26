"""The in-process host serves the kernel's /mcp/toolkit bridge.

Codex's ``harness_toolkit`` MCP entry points at
``{CODEX_TOOLKIT_BASE_URL}/mcp/toolkit/{session_id}``. The kernel app mounts
that path for the standalone form; in-process mode the HOST is the process
answering on that base URL, so it must mount the bridge too — otherwise PTC's
``execute_code`` 404s on codex sessions.
"""

from __future__ import annotations

from starlette.routing import Mount

from valuz_agent.api.app import create_app


def test_host_app_mounts_the_kernel_toolkit_bridge() -> None:
    app = create_app()
    mounts = {r.path for r in app.routes if isinstance(r, Mount)}
    assert "/mcp/toolkit" in mounts

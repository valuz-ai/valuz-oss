"""DTOs for the browser module.

Shared by the ``browser_start``/``browser_stop`` MCP tools and (M1) the
Settings HTTP routes. ``cli_prefix`` is the exact command prefix the skill
must use for subsequent ``chrome-devtools`` commands — returned to the agent so
the version / install path is never hardcoded in the SKILL.md.
"""

from __future__ import annotations

from pydantic import BaseModel


class EnvReport(BaseModel):
    """Environment readiness for the browser feature."""

    node_ok: bool


class BrowserStatus(BaseModel):
    daemon_running: bool
    # "managed" | "attach" (local engine) | "sandbox" (a remote engine: the
    # daemon runs headless where the agent's shell runs; no window to open).
    mode: str
    node_ok: bool
    cli_prefix: str
    pid: int | None = None
    # User-facing guidance for the Settings panel (e.g. "Node not found …").
    hints: list[str] = []


class BrowserStartResult(BaseModel):
    status: str  # "started" | "already_running"
    mode: str
    cli_prefix: str


class BrowserStopResult(BaseModel):
    status: str = "stopped"

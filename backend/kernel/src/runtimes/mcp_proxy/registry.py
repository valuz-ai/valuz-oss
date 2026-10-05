"""Which sessions route their MCP servers through the kernel proxy.

Codex and DSH connect to MCP servers themselves. While a ``tool.call``
handler is registered, their runtimes register the session's servers here
and hand the CLI proxied configs instead — one streamable-HTTP URL per server
on the kernel (``/mcp/proxy/<session>/<server>/``), authenticated by a
per-session bearer token. The kernel then owns the upstream connections and
every MCP call dispatches ``tool.call`` like it does for Claude and
DeepAgents. With no handler registered nothing is proxied and the CLIs keep
their direct connections, exactly as before.
"""

from __future__ import annotations

import hmac
import os
import secrets
import threading
from dataclasses import dataclass, field
from urllib.parse import quote

from src.core.hooks import SessionHooks
from src.core.types import McpHttpServerConfig, McpServerConfig
from src.runtimes.mcp_proxy.upstream import McpUpstream

MCP_PROXY_MOUNT_PATH = "/mcp/proxy"
# Same base the kernel toolkit endpoint uses for colocated CLI subprocesses.
_BASE_URL_ENV = "CODEX_TOOLKIT_BASE_URL"
_BASE_URL_DEFAULT = "http://127.0.0.1:8000"


@dataclass
class ProxiedSession:
    token: str
    hooks: SessionHooks
    upstreams: dict[str, McpUpstream] = field(default_factory=dict)

    def authorized(self, supplied: str) -> bool:
        return hmac.compare_digest(supplied, f"Bearer {self.token}")


_SESSIONS: dict[str, ProxiedSession] = {}
_LOCK = threading.Lock()


def proxy_base_url() -> str:
    return (os.getenv(_BASE_URL_ENV) or _BASE_URL_DEFAULT).rstrip("/")


def proxy_url(session_id: str, server: str) -> str:
    return f"{proxy_base_url()}{MCP_PROXY_MOUNT_PATH}/{session_id}/{quote(server, safe='')}/"


def register_session_proxy(
    session_id: str,
    servers: tuple[McpServerConfig, ...] | list[McpServerConfig],
    hooks: SessionHooks,
) -> tuple[McpServerConfig, ...]:
    """Route *servers* through the proxy; returns the configs to give the CLI.

    Re-registering a session replaces its previous entry (its upstream
    connections are left to :func:`unregister_session_proxy` of the caller
    that owned them).
    """
    token = secrets.token_urlsafe(32)
    entry = ProxiedSession(
        token=token,
        hooks=hooks,
        upstreams={cfg.name: McpUpstream(cfg) for cfg in servers},
    )
    with _LOCK:
        _SESSIONS[session_id] = entry
    return tuple(
        McpHttpServerConfig(
            name=cfg.name,
            url=proxy_url(session_id, cfg.name),
            transport="http",
            headers={"Authorization": f"Bearer {token}"},
            tool_timeout_sec=getattr(cfg, "tool_timeout_sec", None),
        )
        for cfg in servers
    )


def get_session_proxy(session_id: str) -> ProxiedSession | None:
    with _LOCK:
        return _SESSIONS.get(session_id)


async def unregister_session_proxy(session_id: str) -> None:
    with _LOCK:
        entry = _SESSIONS.pop(session_id, None)
    if entry is None:
        return
    for upstream in entry.upstreams.values():
        try:
            await upstream.close()
        except Exception:  # noqa: BLE001 — closing is best-effort
            pass


def reset_for_tests() -> None:
    with _LOCK:
        _SESSIONS.clear()


__all__ = [
    "MCP_PROXY_MOUNT_PATH",
    "ProxiedSession",
    "get_session_proxy",
    "proxy_base_url",
    "proxy_url",
    "register_session_proxy",
    "reset_for_tests",
    "unregister_session_proxy",
]

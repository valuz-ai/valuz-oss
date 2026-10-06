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


def _in_sandbox() -> bool:
    """True inside the cloud sandbox kernel image (it sets ``IS_SANDBOX``)."""
    return os.getenv("IS_SANDBOX", "").strip().lower() in {"1", "true", "yes"}


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


def proxy_session_mcp(
    session_id: str,
    servers: tuple[McpServerConfig, ...] | list[McpServerConfig],
    hooks: SessionHooks,
) -> tuple[McpServerConfig, ...] | None:
    """The session's MCP servers with the ones its handlers need proxied, or
    ``None`` when no ``tool.call`` handler can see an MCP call.

    When the only listener is the citation projection (Codex / DSH), only
    remote servers are proxied — Valuz's source-metadata providers are remote
    connectors — and stdio servers keep running as the CLI's own children.
    Any other handler gets every MCP call, so every server is proxied.
    """
    from src.core.hooks import TOOL_CALL
    from src.core.hooks.builtin.citation_projection import OWNER as CITATION_PROJECTION

    owners = hooks.tool_source_owners(TOOL_CALL, "mcp")
    if not owners:
        return None
    citation_only = all(owner == CITATION_PROJECTION for owner in owners)
    if citation_only and _in_sandbox():
        # In the cloud sandbox the proxy base (``CODEX_TOOLKIT_BASE_URL``) is
        # the HOST's callback URL, while the proxied session is registered in
        # this sandbox kernel — the CLI would reach a host that knows nothing
        # of it and every connector would show as disconnected. Until the
        # proxy has an address the sandbox can serve, the citation projection
        # alone does not reroute anything there (connectors stay direct).
        return None
    selected = (
        [cfg for cfg in servers if isinstance(cfg, McpHttpServerConfig)]
        if citation_only
        else list(servers)
    )
    if not selected:
        return None
    proxied = {cfg.name: cfg for cfg in register_session_proxy(session_id, selected, hooks)}
    return tuple(proxied.get(cfg.name, cfg) for cfg in servers)


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

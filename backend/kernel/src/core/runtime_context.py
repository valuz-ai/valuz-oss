"""Generic in-memory materialization of opaque runtime-turn context."""

from __future__ import annotations

import dataclasses

from src.core.types import McpHttpServerConfig, Session

_MARKER_PREFIX = "__runtime_context:"
_MARKER_SUFFIX = "__"


def runtime_context_marker(key: str) -> str:
    """Return the persisted marker for an overlay-owned runtime-context key."""
    if not key or ":" in key or key.startswith("_"):
        raise ValueError("runtime context key must be a public non-empty namespace")
    return f"{_MARKER_PREFIX}{key}{_MARKER_SUFFIX}"


def _marker_key(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith(_MARKER_PREFIX):
        return None
    if not value.endswith(_MARKER_SUFFIX):
        return None
    key = value[len(_MARKER_PREFIX) : -len(_MARKER_SUFFIX)]
    try:
        return key if runtime_context_marker(key) == value else None
    except ValueError:
        return None


def _materialize(value: str, context: dict[str, str]) -> str:
    key = _marker_key(value)
    if key is None:
        return value
    resolved = context.get(key)
    if not isinstance(resolved, str) or not resolved:
        raise ValueError(f"runtime context is missing marker {key!r}")
    return resolved


def materialize_runtime_context(session: Session, context: dict[str, str] | None) -> Session:
    """Return a runtime-only Session copy with opaque markers resolved.

    OSS deliberately does not assign semantics to any key or value.  Only
    explicit markers are replaced, and only in model/MCP runtime configuration;
    the persisted Session remains untouched.
    """
    provider = getattr(session, "model_provider", None)
    servers = getattr(session, "mcp_servers", ())
    provider_marker = _marker_key(provider.api_key) if provider is not None else None
    mcp_markers = [
        _marker_key(value)
        for server in servers
        if isinstance(server, McpHttpServerConfig)
        for value in server.headers.values()
    ]
    if provider_marker is None and not any(mcp_markers):
        return session
    values = context or {}
    runtime_provider = (
        dataclasses.replace(provider, api_key=_materialize(provider.api_key, values))
        if provider is not None and provider_marker is not None
        else provider
    )
    runtime_servers = tuple(
        dataclasses.replace(
            server,
            headers={key: _materialize(value, values) for key, value in server.headers.items()},
        )
        if isinstance(server, McpHttpServerConfig)
        and any(_marker_key(value) is not None for value in server.headers.values())
        else server
        for server in servers
    )
    return dataclasses.replace(
        session,
        model_provider=runtime_provider,
        mcp_servers=runtime_servers,
    )


__all__ = ["materialize_runtime_context", "runtime_context_marker"]

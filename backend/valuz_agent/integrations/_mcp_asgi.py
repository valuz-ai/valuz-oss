"""Shared ASGI wrapper for in-process built-in MCP endpoints."""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import PlainTextResponse

logger = logging.getLogger(__name__)


def internal_mcp_transport_security() -> TransportSecuritySettings:
    """Transport security for the built-in MCP servers: rebinding check off.

    ``FastMCP`` auto-enables DNS-rebinding protection when constructed with its
    default ``host="127.0.0.1"``, allowing only localhost ``Host`` headers — so
    a kernel reaching the host callback through a public ingress hostname gets
    ``421 Misdirected Request``. That protection defends *unauthenticated*
    localhost servers against browsers; these endpoints are only reachable
    through ``build_internal_mcp_asgi``, which rejects any request lacking a
    valid per-owner signed token, and the deployment's public hostname isn't
    knowable here. Auth stays with the token wrapper; the Host allowlist is off.
    """
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


@dataclass(frozen=True)
class BuiltinMCPContext:
    session_id: str
    user_id: str


_mcp_context: ContextVar[BuiltinMCPContext | None] = ContextVar(
    "valuz_internal_builtin_mcp_context", default=None
)


def get_current_mcp_context() -> BuiltinMCPContext:
    ctx = _mcp_context.get()
    if ctx is None:
        raise RuntimeError("MCP context unavailable: request is not MCP-scoped")
    return ctx


def get_current_mcp_session_id() -> str:
    return get_current_mcp_context().session_id


def get_current_mcp_user_id() -> str:
    return get_current_mcp_context().user_id


def set_current_mcp_context(*, session_id: str, user_id: str) -> Token[BuiltinMCPContext | None]:
    return _mcp_context.set(BuiltinMCPContext(session_id=session_id, user_id=user_id))


def reset_current_mcp_context(token: Token[BuiltinMCPContext | None]) -> None:
    _mcp_context.reset(token)


async def _resolve_session_owner(session_id: str) -> str | None:
    """Resolve the session owner from the raw session id (durable, cross-owner)."""
    from valuz_agent.adapters.data_reader import data_reader

    try:
        sessions = await data_reader().list_all_sessions(ids=[session_id], limit=1)
    except Exception:  # noqa: BLE001 — owner resolution is best-effort
        logger.warning(
            "Internal MCP: failed resolving owner for session %s", session_id, exc_info=True
        )
        return None
    return sessions[0].user_id if sessions else None


async def _verify_token_owner(token: str | None):  # noqa: ANN201
    """Verified owner from a per-owner MCP token, or None if invalid/absent.

    Same per-owner signing/verification as the data service (unifies the two
    forms — see ADR-012): the token's ``sub`` picks the owner's secret, the
    signature proves it. A forged ``sub`` / unknown owner fails.
    """
    if not token:
        return None
    from valuz_agent.ports.sandbox_credential import get_sandbox_credential_verifier

    try:
        claims = await get_sandbox_credential_verifier().verify(token)
    except Exception:  # noqa: BLE001 — auth backend failure must fail closed
        logger.warning("Internal MCP: sandbox credential verification failed", exc_info=True)
        return None
    return claims


def build_internal_mcp_asgi(inner: Any) -> Any:
    """Return a wrapper ASGI app for built-in MCP endpoints.

    The wrapper enforces (per-owner, both forms — ADR-012):
      1) ``X-Valuz-Internal`` carries a per-owner signed token → verified owner
      2) `X-Valuz-Session-Id` presence
      3) the session belongs to the verified owner (anti cross-owner)
      4) built-in MCP context publication for request-scoped access
    """

    async def _app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []
        }
        # Owner comes from the VERIFIED token — never a shared secret or a trusted
        # header. A forged sub / unknown owner fails verification.
        raw_token = headers.get("x-valuz-internal")
        credential_claims = await _verify_token_owner(raw_token)
        if credential_claims is None:
            # Say WHY. A 403 with a bare body cost a multi-hour hunt across
            # ingress, secrets, and kernel images: every sandbox call to every
            # built-in MCP was failing here and the access log showed only the
            # status. The reason goes to the log AND the body — this is an
            # internal endpoint, its only callers are our own runtimes, and
            # "no header" vs "bad token" is exactly the fork the next person
            # needs first.
            reason = "missing X-Valuz-Internal header" if not raw_token else "credential rejected"
            logger.warning(
                "Internal MCP 403 (%s): path=%s token_len=%d",
                reason,
                scope.get("path", ""),
                len(raw_token or ""),
            )
            response = PlainTextResponse(f"Forbidden: {reason}", status_code=403)
            await response(scope, receive, send)
            return

        session_id = headers.get("x-valuz-session-id") or ""
        if not session_id:
            response = PlainTextResponse("Missing X-Valuz-Session-Id header", status_code=400)
            await response(scope, receive, send)
            return

        # A managed credential may be bound to one session. Legacy owner
        # credentials have ``session_id=None`` and retain owner-wide behavior.
        if credential_claims.session_id is not None and credential_claims.session_id != session_id:
            logger.warning(
                "Internal MCP 403 (session-bound credential mismatch): "
                "credential session=%s request session=%s path=%s",
                credential_claims.session_id,
                session_id,
                scope.get("path", ""),
            )
            response = PlainTextResponse(
                "Forbidden: credential bound to another session", status_code=403
            )
            await response(scope, receive, send)
            return
        owner_id = credential_claims.user_id

        # The session must belong to the authenticated owner (cross-owner guard).
        session_owner = await _resolve_session_owner(session_id)
        if session_owner != owner_id:
            logger.warning(
                "Internal MCP 403 (session owner mismatch): session=%s "
                "resolved_owner=%s token_owner=%s path=%s",
                session_id,
                session_owner,
                owner_id,
                scope.get("path", ""),
            )
            response = PlainTextResponse(
                "Forbidden: session not owned by credential owner"
                + (" (session unknown here)" if session_owner is None else ""),
                status_code=403,
            )
            await response(scope, receive, send)
            return

        mcp_ctx_token = set_current_mcp_context(session_id=session_id, user_id=owner_id)
        try:
            await inner(scope, receive, send)
        finally:
            reset_current_mcp_context(mcp_ctx_token)

    return _app


__all__ = [
    "BuiltinMCPContext",
    "build_internal_mcp_asgi",
    "internal_mcp_transport_security",
    "get_current_mcp_context",
    "get_current_mcp_session_id",
    "get_current_mcp_user_id",
    "set_current_mcp_context",
    "reset_current_mcp_context",
]

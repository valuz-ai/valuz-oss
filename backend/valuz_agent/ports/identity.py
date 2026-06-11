"""Port: request-level user identity resolution.

OSS mode returns ``ANONYMOUS`` (a device-derived local user id) for every
request. The commercial version injects a JWT/OIDC-based ``IdentityResolver``
via ``ext.identity`` at app startup.

The resolver returns a plain ``user_id`` string — the OSS layer needs nothing
richer. The commercial overlay adds org / role / entitlement context through
``AuthHook.after_resolve()``.
"""

from __future__ import annotations

from typing import Any, Protocol


class IdentityResolver(Protocol):
    """Resolve the current user from an incoming HTTP request."""

    async def resolve(self, request: Any) -> str | None: ...


class AuthHook(Protocol):
    """Post-authentication hook called by ``AuthMiddleware``.

    Runs after identity resolution, before the request handler. Implementations
    may set additional ``ContextVar``\\s (org, roles, entitlements), or raise a
    ``ValuzError`` to reject the request (caught by ``ErrorHandlerMiddleware``).
    """

    async def after_resolve(self, request: Any, user_id: str | None) -> None: ...


__all__ = ["IdentityResolver", "AuthHook"]

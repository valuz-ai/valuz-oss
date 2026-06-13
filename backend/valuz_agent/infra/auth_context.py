"""Request-scoped owner id — the single runtime source of ``user_id`` stamps.

Every business row (host ``valuz_*`` tables and the kernel quartet) is stamped
with the owner's ``user_id``. Rather than thread that value through 30-odd
datastore ``create_*`` signatures, the ORM column ``default=`` reads it from this
``ContextVar``:

- During an HTTP request, ``AuthMiddleware`` sets it from the resolved
  ``user_id`` (OSS → the local install id; commercial → the logged-in
  user's id).
- Outside a request there is deliberately NO implicit fallback: the context
  must be set explicitly. Boot (``boot.steps.ensure_local_identity``) seeds the
  startup context with the local install id, and background tasks spawned
  during startup inherit it; any other background path (future multi-user
  runners) must wrap its work in ``set_current_user_id(owner)`` /
  ``reset_current_user_id`` itself. Reading an unset context raises
  ``LookupError`` — an insert without an owner fails loudly instead of being
  silently attributed to the install id.

Mirrors the ``set_request_id`` / ``reset_request_id`` pattern in
``infra.logging``.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_user_id: ContextVar[str | None] = ContextVar("valuz_current_user_id")


class OwnerContextUnsetError(LookupError):
    """Raised when an owner-scoped read runs with no owner in context.

    Subclasses ``LookupError`` so existing ``except LookupError`` keeps working.
    On the request path this means the caller is unauthenticated (the auth
    middleware resolved no identity), so the API layer maps it to **401** rather
    than a 500 — see ``api.middleware.ErrorHandlerMiddleware``.
    """


def get_current_user_id() -> str | None:
    """Return the owner id stamped on rows created in this context."""
    return _current_user_id.get()


def require_current_user_id() -> str:
    """Return the request-scoped owner id, or raise if it is absent.

    The read-side companion to the ``UserMixin`` write-stamp: every owner-scoped
    query needs a concrete ``user_id`` to filter on. Like the write path, this
    has NO implicit fallback — an unset context (or one explicitly set to
    ``None``) raises ``OwnerContextUnsetError`` so a query that would otherwise read
    across every owner fails loudly instead of silently. Background paths acting
    for a specific owner must ``set_current_user_id(owner)`` (or thread the
    recovered owner explicitly) before calling owner-scoped reads.
    """
    uid = _current_user_id.get(None)
    if uid is None:
        raise OwnerContextUnsetError(
            "current_user_id is unset; owner-scoped reads require an owner"
        )
    return uid


def set_current_user_id(user_id: str | None) -> Token[str | None]:
    """Set the owner id for the current context; returns a reset token."""
    return _current_user_id.set(user_id)


def reset_current_user_id(token: Token[str | None]) -> None:
    _current_user_id.reset(token)


__all__ = [
    "OwnerContextUnsetError",
    "get_current_user_id",
    "require_current_user_id",
    "set_current_user_id",
    "reset_current_user_id",
]

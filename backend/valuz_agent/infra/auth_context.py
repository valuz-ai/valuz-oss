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


def get_current_user_id() -> str | None:
    """Return the owner id stamped on rows created in this context."""
    return _current_user_id.get()


def set_current_user_id(user_id: str | None) -> Token[str | None]:
    """Set the owner id for the current context; returns a reset token."""
    return _current_user_id.set(user_id)


def reset_current_user_id(token: Token[str | None]) -> None:
    _current_user_id.reset(token)


__all__ = ["get_current_user_id", "set_current_user_id", "reset_current_user_id"]

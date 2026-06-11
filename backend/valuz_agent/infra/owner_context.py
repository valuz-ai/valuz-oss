"""Request-scoped owner id — the single runtime source of ``user_id`` stamps.

Every business row (host ``valuz_*`` tables and the kernel quartet) is stamped
with the owner's ``user_id``. Rather than thread that value through 30-odd
datastore ``create_*`` signatures, the ORM column ``default=`` reads it from this
``ContextVar``:

- During an HTTP request, ``AuthMiddleware`` sets it from the resolved
  ``UserIdentity.user_id`` (OSS → the local install id; commercial → the
  logged-in user's id).
- Outside a request (boot seeds, automations, the task runner, kernel mirrors)
  the default applies — seeded once at boot to the local install id by
  ``boot.steps.ensure_local_identity``.

Mirrors the ``set_request_id`` / ``reset_request_id`` pattern in
``infra.logging``. The default starts empty (the "unset / system" sentinel) and
is replaced at boot before any insert runs.
"""

from __future__ import annotations

import contextvars

# "" = unset/system sentinel. When the ContextVar reads back empty (no request
# set it), ``get_current_user_id`` falls through to ``_default_user_id``, which
# boot seeds to the local install id.
_current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "valuz_current_user_id", default=""
)
_default_user_id: str = ""


def get_current_user_id() -> str:
    """Return the owner id stamped on rows created in this context."""
    uid = _current_user_id.get()
    return uid if uid else _default_user_id


def set_current_user_id(user_id: str) -> contextvars.Token[str]:
    """Set the owner id for the current context; returns a reset token."""
    return _current_user_id.set(user_id)


def reset_current_user_id(token: contextvars.Token[str]) -> None:
    _current_user_id.reset(token)


def set_default_user_id(user_id: str) -> None:
    """Seed the out-of-request default owner id.

    Called once at boot with the resolved local install id so background work
    that never enters ``AuthMiddleware`` (boot seeds, automations, the
    task runner, kernel mirrors) still stamps a real owner.
    """
    global _default_user_id
    _default_user_id = user_id


__all__ = [
    "get_current_user_id",
    "set_current_user_id",
    "reset_current_user_id",
    "set_default_user_id",
]

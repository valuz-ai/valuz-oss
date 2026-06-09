"""Owner id for kernel rows — stamped on every projects/agents/sessions/
messages/events row's ``user_id`` column.

The kernel never imports host code, so it cannot reach the host's owner context
directly. Instead the host (valuz) seeds the default here once at boot
(``boot.kernel.init_kernel_dependencies`` → ``set_default_owner``); the kernel's
ORM ``default=`` reads it via ``get_owner_id``. A ``ContextVar`` override is also
exposed for hosts that resolve a per-request owner, but the module-level default
is what stamps rows created on the kernel's own background tasks/threads (where a
ContextVar set on the caller would not propagate).

Mirrors the host's ``valuz_agent.infra.owner_context``. The default starts empty
(the "unset / system" sentinel) and is replaced before the kernel writes any row.
"""

from __future__ import annotations

import contextvars

_current_owner: contextvars.ContextVar[str] = contextvars.ContextVar(
    "kernel_owner_id", default=""
)
_default_owner: str = ""


def get_owner_id() -> str:
    """Owner id stamped on kernel rows — ORM column ``default``."""
    uid = _current_owner.get()
    return uid if uid else _default_owner


def set_owner_id(user_id: str) -> contextvars.Token[str]:
    return _current_owner.set(user_id)


def reset_owner_id(token: contextvars.Token[str]) -> None:
    _current_owner.reset(token)


def set_default_owner(user_id: str) -> None:
    """Seed the default owner id (host calls this once at boot)."""
    global _default_owner
    _default_owner = user_id


__all__ = ["get_owner_id", "set_owner_id", "reset_owner_id", "set_default_owner"]

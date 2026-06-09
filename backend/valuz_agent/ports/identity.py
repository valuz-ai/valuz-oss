"""Port: request-level user identity resolution.

OSS mode returns ``ANONYMOUS`` for every request. The commercial version
injects a JWT/OIDC-based ``IdentityResolver`` via ``set_identity_resolver()``
in ``api/deps.py`` at app startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class UserIdentity:
    # The owner id. Stamped on every business row's ``user_id`` column and
    # consumed by billing / events / RuntimeContext. OSS → the device-derived
    # local install id; commercial → the logged-in user's id.
    user_id: str
    email: str | None = None
    display_name: str | None = None
    org_id: str | None = None
    roles: list[str] = field(default_factory=list)
    entitlements: list[str] = field(default_factory=list)


ANONYMOUS = UserIdentity(user_id="local-user")


class IdentityResolver(Protocol):
    """Resolve the current user from an incoming HTTP request."""

    def resolve(self, request: Any) -> UserIdentity | None: ...


__all__ = ["UserIdentity", "ANONYMOUS", "IdentityResolver"]

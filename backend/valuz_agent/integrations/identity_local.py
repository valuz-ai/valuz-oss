"""OSS identity resolver — every request is ``ANONYMOUS``.

This is the default wired in ``api/deps.py``. The commercial version
replaces it via ``set_identity_resolver()`` at startup.
"""

from __future__ import annotations

from typing import Any

from valuz_agent.infra.local_identity import resolve_local_user_id
from valuz_agent.ports.identity import UserIdentity


class LocalIdentityResolver:
    """All requests map to the single local user.

    ``user_id`` is the device-derived install id that lands in every row's
    ``user_id`` column (see ``infra.local_identity``).
    """

    def resolve(self, request: Any) -> UserIdentity:
        return UserIdentity(user_id=resolve_local_user_id())


__all__ = ["LocalIdentityResolver"]

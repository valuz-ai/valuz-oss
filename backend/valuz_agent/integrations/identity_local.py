"""OSS identity resolver — every request is ``ANONYMOUS``.

This is the default wired in ``api/deps.py``. The commercial version
replaces it via ``set_identity_resolver()`` at startup.
"""

from __future__ import annotations

from typing import Any

from valuz_agent.ports.identity import ANONYMOUS, UserIdentity


class LocalIdentityResolver:
    """All requests map to the single local user."""

    def resolve(self, request: Any) -> UserIdentity:
        return ANONYMOUS


__all__ = ["LocalIdentityResolver"]

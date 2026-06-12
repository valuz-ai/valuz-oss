"""OSS identity resolver — every request maps to the local install user.

This is the default wired in ``api/deps.py``. The commercial version
replaces it via ``ext.identity`` at startup.
"""

from __future__ import annotations

from typing import Any

from valuz_agent.infra.local_identity import resolve_local_user_id


class LocalIdentityResolver:
    """All requests map to the single local user.

    Returns the device-derived install id that lands in every row's
    ``user_id`` column (see ``infra.local_identity``).
    """

    async def resolve(self, request: Any) -> str:
        return resolve_local_user_id()


__all__ = ["LocalIdentityResolver"]

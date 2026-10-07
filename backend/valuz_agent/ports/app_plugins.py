"""Ports of third-party plugins (ADR-034, docs task card 04).

* ``AppPluginPolicyPort`` — may this user load this installed plugin? OSS lets
  everything through; a commercial overlay applies the organization's application plugin
  policy (blocked sources, blocked versions, revoked catalog versions).
* ``AppPluginPublisherPort`` — upload a packed plugin to a catalog. OSS has no
  catalog, so the port is unbound (``None``) and publishing reports that it
  needs a Valuz account.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class PolicyVerdict:
    allowed: bool
    reason: str | None = None


class AppPluginPolicyPort(Protocol):
    async def evaluate(self, user_id: str, plugin: Mapping[str, Any]) -> PolicyVerdict:
        """``plugin`` is the list item (``id``, ``version``, ``source``, …)."""
        ...


class AllowAllAppPluginPolicy:
    async def evaluate(self, user_id: str, plugin: Mapping[str, Any]) -> PolicyVerdict:
        del user_id, plugin
        return PolicyVerdict(allowed=True)


class AppPluginPublisherPort(Protocol):
    async def publish(
        self,
        user_id: str,
        *,
        archive_path: str,
        scope: str,
        distribution_ids: Sequence[str] = (),
        origin_session_id: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Submit ``archive_path`` (a packed plugin zip); returns the submission."""
        ...

    async def submissions(self, user_id: str) -> list[dict[str, Any]]: ...


__all__ = [
    "AllowAllAppPluginPolicy",
    "AppPluginPublisherPort",
    "PolicyVerdict",
    "AppPluginPolicyPort",
]

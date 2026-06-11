"""Port: authorize writes to user-managed providers.

OSS mode uses ``AllowAllProviderPolicy`` — every create/update/enable is
permitted (single-user, no governance). The commercial overlay binds a real
policy via ``set_provider_policy()`` at app startup so an org admin can lock
members out of adding/using their own BYOK providers ("禁止成员自定义模型"):
when the caller's org has that lock enabled, the bound policy denies writes to
``source="user"`` providers.

The decision is intentionally delegated to the overlay: the lock state lives
in the commercial control plane (cloud-backend), which the overlay queries.
OSS only provides the hook + a permissive default so single-user behavior is
unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

ProviderWriteAction = Literal["create", "update", "enable"]


@dataclass
class ProviderWriteContext:
    """Inputs the policy uses to decide. ``provider_source`` is the source of
    the provider being written (``"user"`` for member BYOK; ``"managed"`` /
    ``"system"`` are governed elsewhere and normally not gated here)."""

    user_id: str
    action: ProviderWriteAction
    provider_source: str = "user"


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str | None = None


class ProviderVisibilityCandidate(Protocol):
    """Minimal shape of a provider list item the policy inspects to decide
    visibility — just its ``id`` and ``source``."""

    id: str
    source: str


class ProviderPolicyPort(Protocol):
    """Gate writes to providers. Called by the provider CRUD routes before
    delegating to the service."""

    async def authorize_write(self, ctx: ProviderWriteContext) -> PolicyDecision:
        """Return a decision for the attempted provider write."""
        ...

    async def hide_user_providers(self) -> bool:
        """Whether to hide the caller's own (``source="user"``) providers from
        the providers list.

        Returns True when the caller's org has locked custom models — members
        then can neither see nor select their personal BYOK providers (the
        "禁止使用" half of the lock; the write half is ``authorize_write``).
        Rows are filtered from the list only, not deleted, so unlocking
        restores them.
        """
        ...

    async def hidden_provider_ids(
        self, candidates: Iterable[ProviderVisibilityCandidate]
    ) -> set[str]:
        """Return the ids among ``candidates`` to hide from the providers list.

        Richer than ``hide_user_providers``: the policy may hide *any* provider
        by id — builtin personal channels, member BYOK, etc. — based on the
        caller's org / platform model policy. ``candidates`` are the items about
        to be returned (each exposes ``id`` + ``source``). Default hides nothing.
        """
        ...


class AllowAllProviderPolicy:
    """Default policy — every write is permitted, nothing hidden (OSS single-user)."""

    async def authorize_write(self, ctx: ProviderWriteContext) -> PolicyDecision:
        return PolicyDecision(allowed=True)

    async def hide_user_providers(self) -> bool:
        return False

    async def hidden_provider_ids(
        self, candidates: Iterable[ProviderVisibilityCandidate]
    ) -> set[str]:
        return set()


_provider_policy: ProviderPolicyPort = AllowAllProviderPolicy()


def get_provider_policy() -> ProviderPolicyPort:
    from valuz_agent.ports.extensions import ext

    return ext.policy


def set_provider_policy(policy: ProviderPolicyPort) -> None:
    """Replace the provider policy (called by the commercial app at startup)."""
    from valuz_agent.ports.extensions import ext

    ext.policy = policy


__all__ = [
    "AllowAllProviderPolicy",
    "PolicyDecision",
    "ProviderPolicyPort",
    "ProviderVisibilityCandidate",
    "ProviderWriteAction",
    "ProviderWriteContext",
    "get_provider_policy",
    "set_provider_policy",
]

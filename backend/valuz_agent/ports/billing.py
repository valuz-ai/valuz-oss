"""Port: metering, budget checks, and balance queries.

OSS mode uses ``NoopBillingProvider`` — all operations are no-ops that
report unlimited budget. The commercial overlay binds a real provider
via ``set_billing_port()`` in ``api/deps.py`` at app startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class MeterEvent:
    user_id: str
    event_type: str  # "llm_call" | "tool_invocation" | ...
    cost_usd: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BudgetStatus:
    allowed: bool
    remaining_credits: float | None = None
    reason: str | None = None


@dataclass
class Balance:
    credits: float
    currency: str = "USD"


class BillingPort(Protocol):
    """Metering, budget enforcement, and balance queries."""

    def meter(self, event: MeterEvent) -> None:
        """Record a billable event."""
        ...

    def check_budget(self, user_id: str, estimated_cost: float = 0.0) -> BudgetStatus:
        """Check whether the user has sufficient budget to proceed."""
        ...

    def get_balance(self, user_id: str) -> Balance:
        """Return the user's current credit balance."""
        ...


class NoopBillingProvider:
    """Default billing provider — everything is free, budget is unlimited."""

    def meter(self, event: MeterEvent) -> None:
        pass

    def check_budget(self, user_id: str, estimated_cost: float = 0.0) -> BudgetStatus:
        return BudgetStatus(allowed=True)

    def get_balance(self, user_id: str) -> Balance:
        return Balance(credits=float("inf"))


_billing_port: BillingPort = NoopBillingProvider()


def get_billing_port() -> BillingPort:
    return _billing_port


def set_billing_port(port: BillingPort) -> None:
    """Replace the billing provider (called by commercial app at startup)."""
    global _billing_port
    _billing_port = port


__all__ = [
    "Balance",
    "BillingPort",
    "BudgetStatus",
    "MeterEvent",
    "NoopBillingProvider",
    "get_billing_port",
    "set_billing_port",
]

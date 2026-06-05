"""Standard event bus topics and typed payloads per ADR-001 §2.4.

Overlays subscribe to these topics for audit, notification, and
statistics. Billing uses ``BillingPort`` directly, not event
subscriptions.

Usage::

    from valuz_agent.infra.eventbus import event_bus
    from valuz_agent.infra.events import Topics, LlmCallCompleted

    # Subscribe
    def on_llm_call(event: LlmCallCompleted) -> None:
        print(event.cost_usd)

    event_bus.subscribe(Topics.LLM_CALL_COMPLETED, lambda **kw: on_llm_call(LlmCallCompleted(**kw)))

    # Publish
    event_bus.publish(
        Topics.LLM_CALL_COMPLETED,
        model_id="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
        scope={...},
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class Topics:
    """Stable event bus topic names (ADR-001 §2.4)."""

    LLM_CALL_COMPLETED = "llm.call.completed"
    TOOL_INVOKED = "tool.invoked"
    SESSION_EVENT_APPENDED = "session.event.appended"
    AUTH_PRINCIPAL_RESOLVED = "auth.principal.resolved"

    # Pre-existing OSS topics (kept for compatibility)
    WORKSPACE_BINDINGS_CHANGED = "workspace.bindings.changed"


@dataclass
class EventScope:
    """Common scope carried by all stable events."""

    user_id: str
    org_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None


@dataclass
class LlmCallCompleted:
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    scope: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolInvoked:
    tool_id: str
    agent_id: str | None
    result_status: str  # "success" | "error" | "timeout"
    scope: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionEventAppended:
    session_id: str
    event_type: str
    scope: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthPrincipalResolved:
    principal_id: str
    org_id: str | None = None


__all__ = [
    "AuthPrincipalResolved",
    "EventScope",
    "LlmCallCompleted",
    "SessionEventAppended",
    "ToolInvoked",
    "Topics",
]

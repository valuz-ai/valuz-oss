"""Global Decision Inbox module (ADR-022).

Aggregates pending ``requires_action(clarifying_questions)`` events across
all task-driven kernel sessions (lead + subtask). Exposes a REST snapshot
+ SSE incremental stream so the frontend can render a topbar badge +
drawer surfaced everywhere in the app — users don't have to drill into
the specific subtask session to discover a paused question.

Architecture: see ``docs/decisions/ADR-022-decision-inbox.md`` and
``docs/exec-plans/active/decision-inbox.md``.

Public surface:

- ``DecisionEntry`` / ``DecisionStreamEvent`` — wire schemas
- ``DecisionAggregator`` — in-memory snapshot + broadcast subscription
- ``enrich_pending`` — pure function that joins a kernel session +
  raw payload into a fully-enriched ``DecisionEntry`` (or ``None`` when
  the session isn't task-driven)
"""

from valuz_agent.modules.decisions.aggregator import DecisionAggregator
from valuz_agent.modules.decisions.schemas import (
    DecisionEntry,
    DecisionStreamEvent,
)
from valuz_agent.modules.decisions.service import enrich_pending

__all__ = [
    "DecisionAggregator",
    "DecisionEntry",
    "DecisionStreamEvent",
    "enrich_pending",
]

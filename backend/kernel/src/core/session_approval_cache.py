"""SessionApprovalCache — kernel-owned per-session approval-rule store.

When a user picks ``approve_for_session`` on a tool-approval pending,
the orchestrator records a ``SessionRule`` here. Subsequent approval
requests in the same session hit this cache *before* parking on the
user, and the orchestrator emits a synthetic
``action_resolved(decision="auto_approved")`` to seal them without a
user round-trip.

Lifecycle:

* Cleared on ``SessionOrchestrator.cleanup(session_id)`` (cold reload).
* Cleared on ``SessionOrchestrator.cleanup_all()`` if/when introduced.
* **Not** cleared on user interrupt — rules survive a Stop press, same
  as message history.
* **Not** cleared on WS disconnect — rules are session-bound, not
  connection-bound (consistent with the rest of the approval contract).
* Process restart loses the cache (no SQLite persistence in v2;
  intentionally matches codex's native ``tool_approvals`` behavior; v3+
  deferred per ``docs/design/approve-for-session.md`` §11).

Not propagated to sub-agents — sub-agent runtimes get an empty cache.
Matches codex's own non-inheritance for ``tool_approvals`` (its
``sync_session_approved_hosts_to`` only forks network hosts, one-way,
and we don't expose network-host approvals yet).

In-memory only. Process-local — no cross-process coordination. A new
``SessionApprovalCache`` is constructed once per ``SessionOrchestrator``
instance.

See ``docs/design/approve-for-session.md`` §4.1 for the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.core.approval_rule_matcher import RuntimeApprovalRuleMatcher

# Subjects eligible for session-scoped rules. ``clarifying_questions`` is
# intentionally excluded — "always ask the same question" has no useful
# semantic, and the orchestrator's subject↔decision invariant already
# rejects ``approve_for_session`` against that subject at the API layer.
SessionRuleSubject = Literal["shell_command", "file_change", "mcp_tool_call", "tool_input"]


@dataclass(frozen=True)
class SessionRule:
    """A session-scoped approval rule attached by the user.

    ``rule_id`` is the canonical handle the orchestrator emits on
    ``action_resolved(decision="approve_for_session", rule_id=...)`` and
    ``action_resolved(decision="auto_approved", auto_resolved_by_rule_id=...)``.
    Frontend uses it to render the "rule N" badge and to trace
    auto-approvals back to their originating pending.

    ``rule_data`` is opaque to the kernel — only the matcher that
    produced it interprets it. Must be JSON-serializable so it can
    round-trip through the events log (the requires_action emit
    embeds the preview, and an action_resolved replay needs to surface
    the same rule shape).
    """

    rule_id: str
    session_id: str
    originating_pending_id: str
    subject: SessionRuleSubject
    runtime_kind: str
    display: str
    rule_data: dict[str, Any]
    created_at: int  # Unix epoch ms (UTC)


class SessionApprovalCache:
    """In-memory per-session store of approval rules.

    Single-process, no locks — every read/write happens on the orchestrator's
    event loop (the runtime's ``_await_host_decisions`` and the route layer's
    ``submit_action`` both reach the cache through ``SessionOrchestrator``
    methods that run on the same loop). If a future component wants to
    touch the cache from another thread, it MUST go through a thread-safe
    facade or this class needs a lock added.
    """

    def __init__(self) -> None:
        self._rules: dict[str, list[SessionRule]] = {}

    def put(self, rule: SessionRule) -> None:
        """Append a rule to the session's list.

        Dedup intentionally NOT applied here — the spec §12 leaves dedup
        semantics to implementation. The orchestrator's pre-emit cache
        check makes accidental duplicate puts uncommon (the rule that
        would cause a hit would short-circuit at the runtime, so the
        same rule won't be re-derived and re-put for the same call).
        If production logs show clutter we can add
        ``(subject, runtime_kind, rule_data_canonical)`` dedup later.
        """
        self._rules.setdefault(rule.session_id, []).append(rule)

    def list(self, session_id: str) -> list[SessionRule]:
        """Return a fresh copy of the rules for this session (oldest first).

        Copy semantics so callers can iterate without mutating the
        underlying list while the orchestrator may be writing to it.
        """
        return list(self._rules.get(session_id, ()))

    def find_match(
        self,
        session_id: str,
        subject: str,
        tool_name: str,
        args: dict[str, Any],
        runtime_extras: dict[str, Any],
        matcher: RuntimeApprovalRuleMatcher,
    ) -> SessionRule | None:
        """Return the first stored rule that matches this approval request.

        Linear scan in oldest-first order — the user's earliest rule wins
        on ties, which gives a stable ``auto_resolved_by_rule_id`` for
        traceability. Per spec §12 we may add dedup or specificity
        ordering later; for v2 the simplest behavior is correct.

        ``matcher`` is the runtime's matcher instance — the kernel never
        introspects ``rule_data``, only the matcher does. This keeps the
        cache module free of per-runtime grammar.
        """
        for rule in self._rules.get(session_id, ()):
            if matcher.match(
                rule.runtime_kind,
                rule.rule_data,
                subject,
                tool_name,
                args,
                runtime_extras,
            ):
                return rule
        return None

    def clear(self, session_id: str) -> None:
        """Remove all rules for ``session_id``. No-op if none stored."""
        self._rules.pop(session_id, None)

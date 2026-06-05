"""RuntimePort — the single runtime interface for the Application."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from src.core.approval_rule_matcher import RuntimeApprovalRuleMatcher
from src.core.events import EventSink
from src.core.types import Session, UserMessage


class RuntimePort(Protocol):
    """Agent Runtime unified interface — Application's only runtime dependency."""

    @property
    def approval_rule_matcher(self) -> RuntimeApprovalRuleMatcher:
        """Per-runtime session-rule matcher used by the kernel cache.

        Each runtime adapter supplies one. Default is
        :class:`ExactArgsRuleMatcher`; runtimes with SDK-native pattern
        grammars (e.g. Claude's ``PermissionUpdate.suggestions``) extend
        or wrap it. The orchestrator calls
        ``approval_rule_matcher.match`` when consulting the cache before
        emitting a ``requires_action``; the runtime itself calls
        ``derive_rule`` to populate ``session_rule_preview`` on the
        outgoing pending payload.

        Runtimes that don't yet wire ``approve_for_session`` still
        expose this accessor (returning the exact-args fallback) so the
        port stays uniform — only their advertised
        ``available_decisions`` differs.
        """
        ...

    def update_sink(self, sink: EventSink) -> None:
        """Replace the event sink (e.g. after WebSocket reconnect)."""
        ...

    async def run(self, session: Session, user_message: UserMessage) -> None:
        """Execute one conversation turn.

        The runtime renders `user_message` through `build_user_prompt` (kernel
        helper) and feeds the resulting string into its SDK. Events are pushed
        via EventSink; session status is updated in place.
        """
        ...

    async def submit_action(
        self,
        pending_id: str,
        decision: Literal["approve", "approve_with_changes", "reject", "answer"],
        message: str | None = None,
        answers: dict[str, str | list[str]] | None = None,
        modified_input: dict[str, Any] | None = None,
    ) -> None:
        """Submit a decision for a pending requires_action.

        ``decision``:

        - ``approve`` / ``reject`` — tool-approval pendings (subjects
          ``shell_command`` / ``file_change`` / ``mcp_tool_call`` /
          ``tool_input``). Every runtime that implements approvals
          handles these.
        - ``approve_with_changes`` — tool-approval pendings only,
          available_decisions-gated to runtimes whose SDK accepts
          modified tool input on approval (Claude
          ``PermissionResultAllow(updated_input=...)``, DeepAgents
          HITL middleware ``EditDecision``). ``modified_input`` MUST
          be non-None and carries the replacement args dict (same
          shape as the original tool input from the pending payload).
          Codex doesn't surface the verb in ``available_decisions``
          and will ``NotImplementedError`` defensively if reached.
        - ``answer`` — only valid for ``clarifying_questions``
          pendings, which only the Claude Agent runtime currently
          emits (Claude SDK's ``AskUserQuestion`` tool). ``answers``
          MUST be non-None and maps question text → selected label(s).
          Codex / DeepAgents don't emit this subject and will
          ``NotImplementedError`` if reached with ``decision="answer"``.
        """
        ...

    async def interrupt(self) -> None:
        """Interrupt current execution."""
        ...

    async def close(self) -> None:
        """Release persistent resources (SDK clients, connections)."""
        ...

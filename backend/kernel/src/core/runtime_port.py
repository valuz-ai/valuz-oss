"""RuntimePort — the single runtime interface for the Application."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from src.core.approval_rule_matcher import RuntimeApprovalRuleMatcher
from src.core.events import EventSink
from src.core.tools import ToolDef
from src.core.types import Session, UserMessage


class RuntimePort(Protocol):
    """Agent Runtime unified interface — Application's only runtime dependency."""

    @property
    def supports_native_continuation(self) -> bool:
        """Whether a second ``run`` resumes the same provider-native thread."""
        ...

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

    async def prepare(self, session: Session) -> None:
        """Initialize persistent client resources without sending a model turn.

        Runtimes without a useful cold-start boundary may implement this as a
        no-op. Calling it repeatedly must be safe.
        """
        ...

    async def fork_session(
        self,
        session: Session,
        *,
        source_native_session_id: str,
        anchor: str | None = None,
    ) -> str:
        """Create *session*'s native thread by branching a source thread.

        The provider-native sibling of start/resume — codex's own lifecycle
        lists ``thread/start | thread/resume | thread/fork`` as the three
        ways a thread comes into being, and this port mirrors that
        (docs/design/session-fork.md). ``source_native_session_id`` is the
        native id of the thread to branch; ``anchor`` cuts inclusively at a
        runtime-native point — codex ``turn_id``, Claude transcript
        ``message_uuid``, deepagents ``checkpoint_id`` — and ``None``
        branches at the tail.

        Implementations MUST backfill ``session.runtime_session_id`` with
        the new native id (also returned) and may leave the runtime warm on
        that thread so the first Send resumes without a cold start. The
        source thread is never mutated. Runtimes whose native fork is not
        wired up yet raise ``NotImplementedError``.
        """
        ...

    async def run(self, session: Session, user_message: UserMessage) -> None:
        """Execute one conversation turn.

        The runtime renders `user_message` through `build_user_prompt` (kernel
        helper) and feeds the resulting string into its SDK. Events are pushed
        via EventSink; session status is updated in place.
        """
        ...

    async def run_task_coverage(
        self,
        session: Session,
        user_message: UserMessage,
        *,
        no_op_tool: ToolDef,
    ) -> None:
        """Run one append-only continuation on the same native thread.

        ``no_op_tool`` is a runtime-private terminal signal available only
        during this invocation.  Calling it lets a model finish a no-gap pass
        without manufacturing an assistant confirmation message.
        """
        ...

    def consume_turn_anchor(self) -> dict[str, Any] | None:
        """Return and clear the native per-turn fork anchor from the last ``run()``.

        The anchor identifies the runtime-native unit the just-finished
        kernel Message maps to — codex ``turn_id``, Claude transcript
        ``message_uuid``, deepagents ``checkpoint_id`` — always with a
        ``"provider"`` discriminator and the native thread/session id it
        belongs to. The orchestrator consumes it at message finalize and
        persists it under ``Message.metadata["runtime_native"]``; that
        stored value is the seam message-granularity fork resolves
        against (docs/design/session-fork.md). Read-and-clear semantics:
        a turn that captured nothing returns ``None`` rather than a
        stale anchor from an earlier message.
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

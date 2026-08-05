"""SessionOrchestrator — manages Runtime lifecycle around each session's cwd.

Transport-agnostic orchestration layer. WebSocket, REST, and CLI all delegate
to this class for runtime caching, turn execution, interrupt handling, and
cleanup.

Sessions are self-sufficient: each carries its own working directory
(``session.cwd``) and embedded agent snapshot (``session.agent_config``);
this orchestrator does not create or own the directory beyond seeding the
``.claude/CLAUDE.md`` stub.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from src.core import recovery
from src.core.agent_config import AgentConfig
from src.core.citation import (
    CitationGuard,
    EvidenceRegistry,
    compact_citation_tool_content,
    private_citation_tool_content,
)
from src.core.claim_evidence_resolution import SemanticVerifierPort
from src.core.events import Event, EventSink, GlobalEventTap
from src.core.prompt_builder import wrap_for_mode
from src.core.runtime_port import RuntimePort
from src.core.session_approval_cache import SessionApprovalCache, SessionRule
from src.core.session_bus import SessionEventBus
from src.core.store_port import StorePort
from src.core.task_coverage_continuation import (
    TASK_COVERAGE_NOOP_TOOL_NAME,
    build_task_coverage_continuation_prompt,
    build_task_coverage_noop_tool,
)
from src.core.time_utils import now_ms
from src.core.types import (
    Error,
    Message,
    Session,
    UserMessage,
)
from src.core.workspace import bootstrap_session_workspace

# Per-session callable injected into runtimes that wire ``approve_for_session``.
# Closes over (session_id, cache, runtime.approval_rule_matcher) so the
# runtime can check the cache without depending on SessionOrchestrator.
# Return value: matching ``SessionRule`` on hit, ``None`` on miss.
# See ``docs/design/approve-for-session.md`` §3.3 for the cache-hit flow.
SessionRuleFinder = Callable[[str, str, dict[str, Any], dict[str, Any]], "SessionRule | None"]
SemanticVerifierFactory = Callable[
    [str, Session],
    Awaitable[SemanticVerifierPort | None],
]

logger = logging.getLogger(__name__)


def _is_task_coverage_noop_tool_name(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    # Claude and Codex expose harness tools through MCP-qualified names;
    # DeepAgents uses the plain ToolDef name.
    return value == TASK_COVERAGE_NOOP_TOOL_NAME or value.endswith(
        f"__{TASK_COVERAGE_NOOP_TOOL_NAME}"
    )


class _TaskCoverageProtocolSink:
    """Hide only the explicit private no-gap protocol tool.

    Every normal Runtime event is forwarded unchanged.  In particular, an
    assistant message such as ``(empty)`` is *not* interpreted or suppressed;
    it remains visible.  A no-gap pass can be silent only when the Runtime
    calls the turn-scoped private tool supplied by ``run_task_coverage``.
    """

    def __init__(self, inner: EventSink) -> None:
        self._inner = inner
        self._private_tool_ids: set[str] = set()
        self.no_gap_declared = False

    async def emit(self, event: Event) -> None:
        tool_id = event.data.get("id") or event.data.get("tool_use_id")
        tool_name = event.data.get("name") or event.data.get("tool_name")
        if event.type in {"tool_use", "tool_input_delta"} and (
            _is_task_coverage_noop_tool_name(tool_name)
            or (isinstance(tool_id, str) and tool_id in self._private_tool_ids)
        ):
            if isinstance(tool_id, str):
                self._private_tool_ids.add(tool_id)
            if event.type == "tool_use":
                self.no_gap_declared = True
            return
        if event.type in {"tool_result", "tool_output_delta"} and isinstance(
            tool_id, str
        ) and tool_id in self._private_tool_ids:
            return
        await self._inner.emit(event)


def _is_externalized_tool_content(value: Any) -> bool:
    """Recognize runtime placeholders whose full result lives in a sidecar."""

    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return stripped.startswith("<persisted-output") or stripped.startswith("/large_tool_results/")


class SessionNotFoundError(Exception):
    """Raised when a session ID does not exist in the store."""


class PendingActionNotFoundError(Exception):
    """Raised when a ``submit_action`` references a ``pending_id`` with no
    matching ``requires_action`` event in the session's events log."""


class PendingActionConflictError(Exception):
    """Raised when ``submit_action`` is called twice for the same
    ``pending_id`` with different decisions. The first decision wins;
    callers see the previous decision in ``previous_decision``."""

    def __init__(self, pending_id: str, previous_decision: str, requested_decision: str) -> None:
        self.pending_id = pending_id
        self.previous_decision = previous_decision
        self.requested_decision = requested_decision
        super().__init__(
            f"pending {pending_id} already resolved as {previous_decision}; "
            f"refused to override with {requested_decision}"
        )


class PendingActionExpiredError(Exception):
    """Raised when ``submit_action`` references a pending that's already
    been sealed by the host (``expired`` from startup scan / timeout, or
    ``interrupted`` from a Stop press)."""

    def __init__(self, pending_id: str, reason: str) -> None:
        self.pending_id = pending_id
        self.reason = reason
        super().__init__(f"pending {pending_id} already resolved as {reason}")


class RuntimeUnavailableError(Exception):
    """Raised when ``submit_action`` arrives but no runtime is actively
    waiting on the decision (turn finished, runtime cache evicted, host
    restarted). The pending should already have been ``expired`` by the
    startup scan in that case."""


class ApprovalNotImplementedError(Exception):
    """Raised when the runtime hasn't yet wired the approval bridge
    (Slice 2 ships the API but only Slice 3 wires Claude; Codex / DeepAgents
    in Phase 2 / 3). Surfaces as 501 to the client so the front-end can
    distinguish 'not built yet' from 'rejected'."""


class PendingActionDecisionMismatchError(Exception):
    """Raised when the requested ``decision`` doesn't fit the pending's
    subject — currently ``decision="answer"`` against any subject other
    than ``clarifying_questions``. Surfaces as 400 so the client knows
    the contract was violated (vs 409 for legitimate same-pending
    racing). The reverse mismatch (approve/reject against a clarifying
    pending) is also caught here.
    """

    def __init__(self, pending_id: str, subject: str, decision: str) -> None:
        self.pending_id = pending_id
        self.subject = subject
        self.decision = decision
        super().__init__(
            f"pending {pending_id} has subject={subject!r}; decision={decision!r} is not valid"
        )


@dataclass(frozen=True)
class SubmitActionResult:
    pending_id: str
    decision: Literal["approve", "approve_with_changes", "approve_for_session", "reject", "answer"]
    accepted_at: int  # Unix epoch ms (UTC)
    idempotent: bool
    # Set when ``decision == "approve_for_session"`` — the UUID assigned to
    # the rule the user just attached. ``None`` for every other verb.
    rule_id: str | None = None


class _GlobalForwardTap:
    """Per-bus tap fanning every emit out to the orchestrator's global taps.

    Holds the orchestrator's tap list *by reference*, so taps registered
    after this bus was created still receive its events. A failing global
    tap is logged and skipped — never detached here, since the same tap
    object is shared across every session's forwarder.
    """

    def __init__(self, session_id: str, taps: list[GlobalEventTap]) -> None:
        self._session_id = session_id
        self._taps = taps

    async def emit(self, event: Event) -> None:
        for tap in list(self._taps):
            try:
                await tap.emit_session(self._session_id, event)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Global event tap failed for %s: %s", self._session_id, exc)


class _MessageIdStampSink:
    """Adds ``message_id`` to every outbound event's data dict.

    Wraps the user-facing sink (e.g. WebSocket) so clients can route events
    to the correct Message without an extra round-trip. The DatabaseEventSink
    already binds message_id at construction so it does not need this stamp;
    leaving the DB JSON free of the duplicate field keeps stored events
    clean.
    """

    def __init__(self, inner: EventSink, message_id: str) -> None:
        self._inner = inner
        self._message_id = message_id

    async def emit(self, event: Event) -> None:
        stamped = Event(
            type=event.type,
            data={**event.data, "message_id": self._message_id},
            timestamp=event.timestamp,
        )
        await self._inner.emit(stamped)


def _session_citation_quality_policy(
    session: Session,
) -> dict[str, Any] | None:
    """Read only the host-stamped, JSON-safe policy snapshot."""

    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    if not isinstance(valuz, dict):
        return None
    snapshot = valuz.get("citation_quality_policy")
    if not isinstance(snapshot, dict):
        return None
    if snapshot.get("mode") not in {"required-on-evidence", "strict-domain"}:
        return None
    if not isinstance(snapshot.get("config"), dict):
        return None
    return snapshot


def _session_citation_enabled(session: Session) -> bool:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    if not isinstance(valuz, dict):
        return True
    value = valuz.get("citation_enabled")
    return value if isinstance(value, bool) else True


def _session_citation_verification_enabled(session: Session) -> bool:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    if not isinstance(valuz, dict):
        return False
    value = valuz.get("citation_verification_enabled")
    return value if isinstance(value, bool) else False


def _session_task_coverage_enabled(session: Session) -> bool:
    """Task Coverage is independent from citation display/verification."""

    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    if not isinstance(valuz, dict):
        return True
    value = valuz.get("task_coverage_enabled")
    return value if isinstance(value, bool) else True


def _session_task_coverage_policy(session: Session) -> dict[str, Any] | None:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    if not isinstance(valuz, dict):
        return None
    policy = valuz.get("task_coverage_policy")
    if not isinstance(policy, dict):
        return None
    if not isinstance(policy.get("revision"), str):
        return None
    if not isinstance(policy.get("review_guidance"), dict):
        return None
    return policy


_PENDING_TASK_CLARIFICATION_KEY = "pending_task_clarification"


def _clear_legacy_pending_task_clarification(session: Session) -> None:
    """Discard Host-owned preflight state written by older builds."""

    metadata = copy.deepcopy(session.metadata) if isinstance(session.metadata, dict) else {}
    raw_valuz = metadata.get("valuz")
    if not isinstance(raw_valuz, dict) or _PENDING_TASK_CLARIFICATION_KEY not in raw_valuz:
        return
    valuz = dict(raw_valuz)
    valuz.pop(_PENDING_TASK_CLARIFICATION_KEY, None)
    metadata["valuz"] = valuz
    session.metadata = metadata


def _session_document_scope(session: Session) -> set[str] | None:
    """Return the host-stamped locked document scope, if present."""

    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    if not isinstance(valuz, dict):
        return None
    research = valuz.get("document_research")
    if (
        not isinstance(research, dict)
        or research.get("purpose") != "document-research"
        or research.get("source_scope") != "locked"
    ):
        return None
    document_ids = research.get("document_ids")
    if not isinstance(document_ids, list):
        return set()
    return {str(item) for item in document_ids if str(item)}


class _MessageObserverSink:
    """Pass Runtime events through first, then attach optional sidecars.

    The observer is intentionally not an answer controller.  It never edits,
    ranks, suppresses, or replaces Runtime-authored assistant text.  Evidence
    registration is shared infrastructure; Citation, Audit, and Task Coverage
    are three independent consumers controlled by separate switches.
    """

    def __init__(
        self,
        inner: EventSink,
        *,
        message_id: str = "message",
        user_prompt: str = "",
        citation_policy_available: bool = False,
        citation_quality_policy: dict[str, Any] | None = None,
        allowed_document_ids: set[str] | None = None,
        force_citation_required: bool = False,
        citation_enabled: bool = True,
        citation_verification_enabled: bool = True,
        semantic_verifier: SemanticVerifierPort | None = None,
        task_coverage_enabled: bool = True,
    ) -> None:
        self._inner = inner
        self._message_id = message_id
        self._user_prompt = user_prompt
        self._citation_policy_available = citation_policy_available
        self._citation_quality_policy = citation_quality_policy
        self._citation_enabled = citation_enabled
        self._citation_verification_enabled = citation_verification_enabled
        self._semantic_verifier = semantic_verifier
        self._task_coverage_enabled = task_coverage_enabled
        self._force_citation_required = force_citation_required or (
            isinstance(citation_quality_policy, dict)
            and citation_quality_policy.get("mode") == "strict-domain"
        )

        self._assistant_chunks: list[str] = []
        self._assistant_delta_chunks: list[str] = []
        self._assistant_sidecar_inputs: list[
            tuple[int, str, EvidenceRegistry, str | None]
        ] = []
        self._sidecars_finalized = False
        self._pending_idle_event: Event | None = None
        self._task_coverage_continuation_active = False
        self._task_coverage_continuation_attempts = 0
        self._task_coverage_segment_indices: list[int] = []

        self._tool_names: dict[str, str] = {}
        self._evidence_registry = EvidenceRegistry(
            allowed_document_ids=allowed_document_ids,
        )

        self.num_turns = 0
        self.error_payload: dict[str, Any] | None = None
        self.usage: dict[str, int] | None = None
        self.model_usage: dict[str, Any] | None = None
        self.citation_bundle: dict[str, Any] | None = None
        self.claim_audits: list[dict[str, Any]] = []
        self.task_coverage: dict[str, Any] | None = None
        self.last_todos: list[dict[str, Any]] | None = None
        self.runtime_mode_change: Literal["default", "plan", "goal"] | None = None

    async def emit(self, event: Event) -> None:
        if event.type == "citation_evidence":
            self._register_private_evidence(event)
            return

        if event.type == "assistant_message":
            await self._publish_runtime_assistant(event)
            return

        if event.type == "text_delta":
            text = event.data.get("text") or event.data.get("delta") or ""
            if text:
                self._assistant_delta_chunks.append(str(text))

        elif event.type == "tool_use":
            tool_use_id = event.data.get("id")
            tool_name = event.data.get("name")
            if isinstance(tool_use_id, str) and isinstance(tool_name, str):
                self._tool_names[tool_use_id] = tool_name

        elif event.type == "tool_result":
            event = self._register_and_redact_tool_result(event)

        elif event.type == "session_idle":
            raw_turns = event.data.get("num_turns")
            if isinstance(raw_turns, int) and raw_turns > 0:
                self.num_turns += raw_turns
            await self.ensure_partial_assistant_message()
            stop_reason = event.data.get("stop_reason")
            coverage_active = self._task_coverage_continuation_active
            coverage_stop_type = (
                str(stop_reason.get("type") or "error")
                if isinstance(stop_reason, dict)
                else "error"
            )
            if coverage_active and coverage_stop_type != "end_turn":
                event = Event(
                    type="session_idle",
                    data={
                        **event.data,
                        "stop_reason": {"type": "end_turn"},
                        "task_coverage_failure": coverage_stop_type,
                    },
                    timestamp=event.timestamp,
                )
            if coverage_active:
                self.task_coverage = (
                    {
                        "status": "complete",
                        "supplemented": bool(self._task_coverage_segment_indices),
                        "assistant_segment_indices": list(
                            self._task_coverage_segment_indices
                        ),
                    }
                    if coverage_stop_type == "end_turn"
                    else {
                        "status": "failed",
                        "reason": coverage_stop_type,
                        "supplemented": bool(self._task_coverage_segment_indices),
                        "assistant_segment_indices": list(
                            self._task_coverage_segment_indices
                        ),
                    }
                )
            self._task_coverage_continuation_active = False
            self._pending_idle_event = event
            if not self._task_coverage_enabled:
                await self.finalize_sidecars()
                await self.release_session_idle()
            return

        elif event.type == "session_error":
            self.error_payload = {
                "category": str(event.data.get("category") or "execution_error"),
                "message": str(event.data.get("message") or ""),
            }

        elif event.type == "usage_update":
            current = {
                "input_tokens": int(event.data.get("input_tokens") or 0),
                "output_tokens": int(event.data.get("output_tokens") or 0),
                "cache_read_tokens": int(
                    event.data.get("cache_read_tokens") or 0
                ),
                "cache_write_tokens": int(
                    event.data.get("cache_write_tokens") or 0
                ),
            }
            if self.usage is None:
                self.usage = current
            else:
                self.usage = {
                    key: self.usage.get(key, 0) + value
                    for key, value in current.items()
                }
            raw_model_usage = event.data.get("model_usage")
            if isinstance(raw_model_usage, dict):
                self.model_usage = copy.deepcopy(raw_model_usage)

        elif event.type == "todo_update":
            raw_todos = event.data.get("todos")
            if isinstance(raw_todos, list):
                self.last_todos = [
                    dict(item) for item in raw_todos if isinstance(item, dict)
                ]

        elif event.type == "mode_changed":
            if event.data.get("by") == "runtime":
                mode = event.data.get("mode")
                if mode in ("default", "plan", "goal"):
                    self.runtime_mode_change = mode

        await self._inner.emit(event)

    def _register_private_evidence(self, event: Event) -> None:
        citation_content = event.data.get("content")
        if not isinstance(citation_content, str):
            return
        tool_name = event.data.get("tool_name")
        model_content = event.data.get("model_content")
        self._evidence_registry.register_tool_projection(
            model_content if model_content is not None else citation_content,
            citation_content,
            tool_name=str(tool_name) if tool_name else None,
            trusted_private=True,
        )

    def _register_and_redact_tool_result(self, event: Event) -> Event:
        tool_use_id = event.data.get("id")
        tool_name = (
            self._tool_names.get(tool_use_id)
            if isinstance(tool_use_id, str)
            else None
        )
        citation_content = event.data.get("_citation_content")
        visible_content = event.data.get("content")
        compacted_content = compact_citation_tool_content(visible_content)
        private_projection = (
            citation_content
            if isinstance(citation_content, str)
            else private_citation_tool_content(visible_content)
            if compacted_content is not None
            else None
        )
        self._evidence_registry.register_tool_projection(
            compacted_content if compacted_content is not None else visible_content,
            private_projection,
            tool_name=tool_name,
            trusted_private=(
                private_projection is not None or compacted_content is not None
            ),
        )
        if "_citation_content" not in event.data and compacted_content is None:
            return event
        return Event(
            type=event.type,
            data={
                key: (compacted_content if key == "content" else value)
                for key, value in event.data.items()
                if key != "_citation_content"
            },
            timestamp=event.timestamp,
        )

    async def _publish_runtime_assistant(self, event: Event) -> bool:
        text = str(event.data.get("text") or event.data.get("content") or "")
        if not text:
            return False
        segment_index = len(self._assistant_sidecar_inputs)
        parent = event.data.get("parent_tool_use_id")
        parent_tool_use_id = str(parent) if parent is not None else None
        self._assistant_chunks.append(text)
        self._assistant_delta_chunks.clear()
        if self._task_coverage_continuation_active:
            self._task_coverage_segment_indices.append(segment_index)
        self._assistant_sidecar_inputs.append(
            (
                segment_index,
                text,
                self._evidence_registry.read_snapshot(),
                parent_tool_use_id,
            )
        )
        # This await is the persist-then-broadcast boundary.  Nothing in
        # Citation or Audit runs before it.
        await self._inner.emit(event)
        return True

    @property
    def partial_assistant_text(self) -> str | None:
        text = "".join(self._assistant_delta_chunks)
        return text or None

    @property
    def assistant_text(self) -> str | None:
        return "\n".join(self._assistant_chunks) if self._assistant_chunks else None

    async def ensure_partial_assistant_message(self) -> bool:
        text = self.partial_assistant_text
        if not text:
            return False
        return await self._publish_runtime_assistant(
            Event(type="assistant_message", data={"text": text})
        )

    async def begin_task_coverage_continuation(self) -> None:
        if self._task_coverage_continuation_attempts:
            raise RuntimeError("Task Coverage continuation may run at most once")
        await self.ensure_partial_assistant_message()
        self._task_coverage_continuation_attempts = 1
        self._task_coverage_continuation_active = True
        self._assistant_delta_chunks.clear()

    async def abort_task_coverage_continuation(self, *, reason: str) -> None:
        await self.ensure_partial_assistant_message()
        self._task_coverage_continuation_active = False
        self.task_coverage = {
            "status": "failed",
            "reason": reason,
            "supplemented": bool(self._task_coverage_segment_indices),
            "assistant_segment_indices": list(
                self._task_coverage_segment_indices
            ),
        }
        self._pending_idle_event = Event(
            type="session_idle",
            data={
                "stop_reason": {"type": "end_turn"},
                "num_turns": 0,
                "task_coverage_failure": reason,
            },
        )

    def mark_task_coverage_unavailable(self, *, reason: str) -> None:
        self.task_coverage = {"status": "unavailable", "reason": reason}

    def mark_task_coverage_no_gap(self) -> None:
        """Record a structured private no-gap result without adding text."""

        if self._task_coverage_segment_indices:
            # Runtime-authored assistant content always wins the visibility
            # contract.  A model that both calls the private no-op tool and
            # writes text produced a supplement; never hide or relabel it.
            return
        self.task_coverage = {
            "status": "complete",
            "supplemented": False,
            "assistant_segment_indices": [],
            "decision": "no-gap",
        }

    async def finalize_sidecars(self) -> None:
        if self._sidecars_finalized:
            return
        self._sidecars_finalized = True
        coverage_targets = set(self._task_coverage_segment_indices)
        if (
            self.task_coverage is not None
            and not coverage_targets
            and self._assistant_sidecar_inputs
        ):
            # Silent/no-op and unavailable continuations have no assistant
            # segment of their own. Attach the turn-level observation to the
            # last real Runtime message without inventing user-visible text.
            coverage_targets.add(self._assistant_sidecar_inputs[-1][0])

        if not (self._citation_enabled or self._citation_verification_enabled):
            if self.task_coverage is not None:
                for segment_index, _text, _registry, parent_tool_use_id in (
                    self._assistant_sidecar_inputs
                ):
                    if segment_index not in coverage_targets:
                        continue
                    sidecar_data: dict[str, Any] = {
                        "assistant_segment_index": segment_index,
                        "task_coverage": copy.deepcopy(self.task_coverage),
                    }
                    if parent_tool_use_id is not None:
                        sidecar_data["parent_tool_use_id"] = parent_tool_use_id
                    await self._inner.emit(
                        Event(type="assistant_message_sidecar", data=sidecar_data)
                    )
            return

        aggregate_citations: list[dict[str, Any]] = []
        aggregate_projection: dict[str, str] = {}
        seen_citation_ids: set[str] = set()

        for segment_index, text, registry, parent_tool_use_id in (
            self._assistant_sidecar_inputs
        ):
            guard = CitationGuard(
                registry,
                # All assistant messages in one user turn share a canonical
                # Citation id space. Message-local audit placement is carried
                # by ``assistant_segment_index`` on the sidecar, not by
                # minting a second id for the same Evidence identity.
                message_id=self._message_id,
                user_prompt=self._user_prompt,
                policy_available=self._citation_policy_available,
                quality_policy=self._citation_quality_policy,
                force_required=self._force_citation_required,
                enabled=True,
                verification_enabled=self._citation_verification_enabled,
                semantic_verifier=self._semantic_verifier,
            )
            try:
                result = (
                    await asyncio.to_thread(
                        guard.finalize,
                        text,
                    )
                    if self._citation_verification_enabled
                    else await asyncio.to_thread(
                        guard.finalize_projection,
                        text,
                    )
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "assistant sidecar failed segment=%s",
                    segment_index,
                    exc_info=True,
                )
                if (
                    self.task_coverage is not None
                    and segment_index in coverage_targets
                ):
                    sidecar_data: dict[str, Any] = {
                        "assistant_segment_index": segment_index,
                        "task_coverage": copy.deepcopy(self.task_coverage),
                    }
                    if parent_tool_use_id is not None:
                        sidecar_data["parent_tool_use_id"] = parent_tool_use_id
                    await self._inner.emit(
                        Event(type="assistant_message_sidecar", data=sidecar_data)
                    )
                continue

            bundle = result.bundle
            if not isinstance(bundle, dict):
                if (
                    self.task_coverage is not None
                    and segment_index in coverage_targets
                ):
                    sidecar_data = {
                        "assistant_segment_index": segment_index,
                        "task_coverage": copy.deepcopy(self.task_coverage),
                    }
                    if parent_tool_use_id is not None:
                        sidecar_data["parent_tool_use_id"] = parent_tool_use_id
                    await self._inner.emit(
                        Event(type="assistant_message_sidecar", data=sidecar_data)
                    )
                continue

            sidecar_data: dict[str, Any] = {
                "assistant_segment_index": segment_index,
            }
            if parent_tool_use_id is not None:
                sidecar_data["parent_tool_use_id"] = parent_tool_use_id

            has_payload = False
            if (
                self.task_coverage is not None
                and segment_index in coverage_targets
            ):
                sidecar_data["task_coverage"] = copy.deepcopy(
                    self.task_coverage
                )
                has_payload = True
            if self._citation_enabled:
                sidecar_data["citation_bundle"] = bundle
                has_payload = True
                projection = bundle.get("projection")
                if isinstance(projection, dict):
                    mapping = projection.get("evidenceHandleToCitationId")
                    if isinstance(mapping, dict):
                        aggregate_projection.update(
                            {
                                str(handle): str(citation_id)
                                for handle, citation_id in mapping.items()
                                if handle and citation_id
                            }
                        )
                citations = bundle.get("citations")
                if isinstance(citations, list):
                    for citation in citations:
                        if not isinstance(citation, dict):
                            continue
                        citation_id = citation.get("citationId")
                        if (
                            not isinstance(citation_id, str)
                            or not citation_id
                            or citation_id in seen_citation_ids
                        ):
                            continue
                        seen_citation_ids.add(citation_id)
                        aggregate_citations.append(copy.deepcopy(citation))

            quality = bundle.get("quality")
            if self._citation_verification_enabled and isinstance(quality, dict):
                audit = copy.deepcopy(quality)
                audit["assistantSegmentIndex"] = segment_index
                self.claim_audits.append(audit)
                sidecar_data["claim_audit"] = audit
                has_payload = True

            if has_payload:
                await self._inner.emit(
                    Event(type="assistant_message_sidecar", data=sidecar_data)
                )

        if self._citation_enabled and (
            aggregate_citations or aggregate_projection
        ):
            self.citation_bundle = {
                "version": 1,
                "citations": aggregate_citations,
                "projection": {
                    "evidenceHandleToCitationId": aggregate_projection,
                },
            }

    async def release_session_idle(self) -> None:
        event = self._pending_idle_event
        self._pending_idle_event = None
        if event is not None:
            await self._inner.emit(event)


class SessionOrchestrator:
    """Manages Runtime lifecycle for sessions.

    Responsibilities:
    1. Bind the runtime to the session's embedded AgentConfig snapshot
    2. Runtime caching per session (config changes take effect on new sessions)
    3. Active runtime tracking (interrupt support)
    4. Per-run Message lifecycle (one row per call to run_turn)

    Sessions sharing a cwd may run concurrently; the user is responsible for
    any workspace contention that arises (e.g. two sessions editing the same
    file).
    """

    # Warm-runtime eviction defaults. Each cached runtime holds a live CLI
    # subprocess (claude / codex) for the life of the cache entry, so an
    # unbounded ``_runtimes`` leaks one OS process per session touched —
    # they only die when the host exits (the SDKs' atexit reaper). These two
    # knobs bound that: a hard LRU ceiling on concurrent warm runtimes and an
    # idle TTL after which an untouched runtime is closed. ``<= 0`` disables
    # the corresponding policy. Overridable per-instance (composition root
    # reads env in ``app.dependencies``); see docs/design or the eviction
    # helpers below.
    DEFAULT_MAX_WARM_RUNTIMES: int = 6
    DEFAULT_RUNTIME_IDLE_TTL_S: float = 300.0  # 5 min
    DEFAULT_SWEEP_INTERVAL_S: float = 60.0  # 1 min
    # Extended idle TTL for runtimes reporting live background tasks
    # (``run_in_background`` processes die with the CLI subprocess, so normal
    # TTL eviction would kill user work mid-task). An EXTENSION rather than an
    # exemption: a crashed CLI can leave the busy signal stuck, and this is
    # the backstop against pinning a runtime forever. ``<= 0`` = full
    # exemption (busy runtimes never TTL-evicted).
    DEFAULT_BG_BUSY_RUNTIME_TTL_S: float = 3600.0  # 1 h

    def __init__(
        self,
        store: StorePort,
        *,
        max_warm_runtimes: int | None = None,
        runtime_idle_ttl_s: float | None = None,
        sweep_interval_s: float | None = None,
        bg_busy_runtime_ttl_s: float | None = None,
        semantic_verifier_factory: SemanticVerifierFactory | None = None,
    ) -> None:
        self._store = store
        self._runtimes: dict[str, RuntimePort] = {}
        # session_id -> monotonic timestamp of the last turn START/END on that
        # cached runtime. Drives idle-TTL + LRU eviction. Mirrors the lifetime
        # of ``_runtimes`` exactly (added on create, dropped on evict/cleanup).
        self._runtime_last_used: dict[str, float] = {}
        self._max_warm_runtimes = (
            self.DEFAULT_MAX_WARM_RUNTIMES if max_warm_runtimes is None else max_warm_runtimes
        )
        self._runtime_idle_ttl_s = (
            self.DEFAULT_RUNTIME_IDLE_TTL_S if runtime_idle_ttl_s is None else runtime_idle_ttl_s
        )
        self._sweep_interval_s = (
            self.DEFAULT_SWEEP_INTERVAL_S if sweep_interval_s is None else sweep_interval_s
        )
        self._bg_busy_runtime_ttl_s = (
            self.DEFAULT_BG_BUSY_RUNTIME_TTL_S
            if bg_busy_runtime_ttl_s is None
            else bg_busy_runtime_ttl_s
        )
        # Background idle-sweeper task. Started by ``start()`` (composition
        # root, has a running loop), cancelled by ``shutdown()``. ``None`` when
        # not running — the lazy sweep in ``_ensure_runtime`` still enforces
        # both policies on every turn, so eviction is correct even if the timer
        # was never started (e.g. unit tests driving ``_ensure_runtime``).
        self._sweeper_task: asyncio.Task[None] | None = None
        self._closing = False
        self._active: dict[str, RuntimePort] = {}
        self._active_message: dict[str, Message] = {}
        # Per-session outbound bus. Lifecycle is independent of any
        # particular WebSocket: the runtime always emits to the bus, and
        # the bus forwards to whichever client sink (if any) is currently
        # attached. Drops on disconnect, replays on reattach.
        self._buses: dict[str, SessionEventBus] = {}
        # Session-scoped approval rules (``approve_for_session`` verb).
        # Kernel-owned so the event-flow contract stays uniform across
        # runtimes — see ``docs/design/approve-for-session.md`` §4.1.
        # Cleared on ``cleanup(session_id)``; not persisted to DB in v2.
        self._session_approval_cache = SessionApprovalCache()
        # Process-wide event taps: each receives ``(session_id, event)``
        # for every event emitted on ANY session bus. The list object is
        # shared by reference with the per-bus forwarders created in
        # ``_get_or_create_bus``, so registration is effective for buses
        # created both before and after the tap was added.
        self._global_taps: list[GlobalEventTap] = []
        self._semantic_verifier_factory = semantic_verifier_factory
    @property
    def active_sessions(self) -> set[str]:
        return set(self._active)

    def has_cached_runtime(self, session_id: str) -> bool:
        return session_id in self._runtimes

    def set_semantic_verifier_factory(
        self,
        factory: SemanticVerifierFactory | None,
    ) -> None:
        """Install an owner-scoped bounded verifier factory.

        The factory receives the explicit owner and immutable Session model
        configuration.  It may return ``None`` when the deployment has no
        authorized provider; verification then stays deterministic and safely
        unresolved instead of borrowing ambient request identity.
        """

        self._semantic_verifier_factory = factory

    def _get_or_create_bus(self, session_id: str) -> SessionEventBus:
        bus = self._buses.get(session_id)
        if bus is None:
            bus = SessionEventBus(
                taps=[_GlobalForwardTap(session_id, self._global_taps)],
                session_id=session_id,
            )
            self._buses[session_id] = bus
        return bus

    async def attach_session_tap(
        self,
        user_id: str,
        session_id: str,
        sink: EventSink,
        *,
        replay: bool = False,
        live_partial: bool = False,
    ) -> None:
        """Register a passive multi-subscriber tap on a session's live stream.

        Unlike :meth:`attach_session_sink` (the single client slot used by
        the WS run channel), taps coexist: any number of observers — SSE
        streams, host aggregators — can tap one session without displacing
        the client or each other. ``replay=True`` first delivers the events
        of the in-progress message so a mid-turn tap sees a coherent view.

        ``live_partial=True`` additionally delivers the *unsealed* streaming
        state — partial assistant text, partial tool input, the latest
        workflow progress — which no replay path can reach because those
        types are never persisted. The two flags are independent: a caller
        that runs its own durable backfill wants ``live_partial`` alone.
        """
        bus = self._get_or_create_bus(session_id)
        replay_events = await self._build_replay(user_id, session_id) if replay else []
        await bus.add_tap(sink, replay=replay_events, live_partial=live_partial)

    async def detach_session_tap(self, session_id: str, sink: EventSink) -> None:
        """Unregister a tap added via :meth:`attach_session_tap`."""
        bus = self._buses.get(session_id)
        if bus is not None:
            await bus.remove_tap(sink)

    def attach_global_tap(self, tap: GlobalEventTap) -> None:
        """Register a process-wide tap receiving ``(session_id, event)``
        for every event on every session bus.

        Intended for singleton host-level aggregators (decision inbox,
        remote event streams). Synchronous on purpose — registration is a
        list append on the shared tap list, effective immediately for all
        existing and future buses.
        """
        self._global_taps.append(tap)

    def detach_global_tap(self, tap: GlobalEventTap) -> None:
        try:
            self._global_taps.remove(tap)
        except ValueError:
            pass

    async def emit_session_event(
        self, session_id: str, event: Event, *, create_bus: bool = False
    ) -> None:
        """Emit an event onto a session's bus from outside a turn.

        Used by the API layer for session-state notifications that are not
        tied to a Message — e.g., the ``mode_changed`` event fired from
        ``POST /sessions/{id}/mode``. If no bus exists yet (no client has
        ever attached and no turn has ever run), this is a no-op: the
        authoritative state lives on the ``Session`` row, and the event
        is purely a live-notification channel for currently-attached
        clients. No DB persistence — by design (see
        ``docs/design/session-modes.md`` §Events).

        ``create_bus=True`` forces bus creation so the event reaches
        global taps (and any tap registered between turns) even when no
        client has ever attached — used for synthetic notifications like
        the interrupt-fallback ``session_error``.
        """
        if create_bus:
            bus: SessionEventBus | None = self._get_or_create_bus(session_id)
        else:
            bus = self._buses.get(session_id)
        if bus is None:
            return
        await bus.emit(event)

    async def attach_session_sink(self, user_id: str, session_id: str, sink: EventSink) -> None:
        """Subscribe ``sink`` to this session's live event stream.

        If a turn is currently in flight, replays the events of the
        in-progress message first so the new client sees a coherent
        view of the run-so-far. Subsequent live emits arrive in order.
        """
        bus = self._get_or_create_bus(session_id)
        replay = await self._build_replay(user_id, session_id)
        await bus.attach(sink, replay=replay)

    async def detach_session_sink(self, session_id: str, sink: EventSink) -> None:
        """Unsubscribe ``sink``. Does not affect the running turn."""
        bus = self._buses.get(session_id)
        if bus is not None:
            await bus.detach(sink)

    async def _build_replay(self, user_id: str, session_id: str) -> list[Event]:
        """Replay = events of any message still in ``running`` status.

        We don't replay finalized history — REST handles that via
        ``GET /sessions/{id}/messages``. The bus only needs to fill the
        gap for the turn that's still emitting live events.

        DB stores raw events (no ``message_id`` field in ``data``); the
        live emit path stamps them on the way to the WS via
        :class:`_MessageIdStampSink`. Replay must stamp consistently so
        the client routes them to the right ``MessageView``.
        """
        active_message = self._active_message.get(session_id)
        if active_message is None:
            return []
        raw_events = await self._store.get_events_for_message(user_id, active_message.id)
        message_id = active_message.id
        return [
            Event(
                type=ev.type,
                data={**ev.data, "message_id": message_id},
                timestamp=ev.timestamp,
            )
            for ev in raw_events
        ]

    async def run_turn(
        self,
        user_id: str,
        session_id: str,
        user_message: UserMessage,
    ) -> Message:
        """Execute one conversation turn.

        Loads project and agent config from the session's bindings, creates
        a Message row for this run, then delegates to the runtime with the
        project's cwd as workspace root. The Message is finalized — with
        terminal status, assistant text, error payload, and stop reason —
        before this method returns.

        Outbound events flow through the session's :class:`SessionEventBus`,
        which forwards to whichever client sink is currently attached
        (or none). The DatabaseEventSink in the same composite ensures
        every event is persisted regardless of client state — this is
        what makes reconnect-with-replay correct.
        """
        from src.adapters.database_sink import DatabaseEventSink
        from src.adapters.delta_coalescing_sink import DeltaCoalescingSink
        from src.adapters.persist_then_broadcast_sink import PersistThenBroadcastSink

        session, agent = await self._load_session(user_id, session_id)
        # Older builds persisted Host-owned prerequisite state that could
        # rewrite or block a later user turn.  The native Agent now owns all
        # clarification decisions, so discard that obsolete control state at
        # the turn boundary without changing the user's current message.
        _clear_legacy_pending_task_clarification(session)
        citation_policy_snapshot = _session_citation_quality_policy(session)
        document_scope = _session_document_scope(session)
        task_coverage_enabled = _session_task_coverage_enabled(session)
        task_coverage_policy = _session_task_coverage_policy(session)
        current_task_prompt = user_message.text

        # Slice 3 of session-modes (broadened in slice 6 simplification):
        # both Claude and Codex process ``/plan <text>`` / ``/goal <text>``
        # in their user-input stream — Claude's CLI intercepts the slash,
        # codex's app-server interprets it as a per-turn mode marker.
        # ``wrap_for_mode`` prepends the matching slash so each turn in a
        # non-default mode enters the native mode for that turn. The
        # exceptions (Claude+plan toggle, DeepAgents no-primitive,
        # user-supplied slashes) are spelled out in ``wrap_for_mode``'s
        # docstring. The wrapped form is what gets persisted on the
        # ``Message`` row — source of truth is what the runtime saw, so
        # replay is correct without re-wrapping on read.
        wrapped_text = wrap_for_mode(user_message.text, session.mode, session.runtime_provider)
        if wrapped_text != user_message.text:
            user_message = dataclasses.replace(user_message, text=wrapped_text)

        message = Message(
            id=str(uuid.uuid4()),
            session_id=session_id,
            user_message=user_message,
            started_at=now_ms(),
            status="running",
        )
        await self._store.save_message(user_id, message)
        self._active_message[session_id] = message

        # Persist ``session.status = "running"`` so the DB row reflects
        # the in-flight state for the duration of the turn. Before this,
        # ``status="running"`` was set in-memory by each runtime at
        # ``run()`` entry but only saved back as ``"idle"`` at end of
        # turn — DB never observed a real "running" row, and
        # ``list_sessions(status="running")`` returned nothing in normal
        # operation. A host crash mid-turn now leaves a real orphan
        # ``running`` row for ``scan_orphan_runs`` to reset on the next
        # startup. The defensive reset in ``finally`` below covers the
        # narrower case where ``run()`` returns without restoring
        # ``status`` (all current runtimes do restore it, but the path
        # is defensive against a future runtime regression).
        session.status = "running"
        await self._store.save_session(session)

        bus = self._get_or_create_bus(session_id)
        bus_sink: EventSink = _MessageIdStampSink(bus, message.id)
        db_sink = DatabaseEventSink(self._store, user_id, session_id, message.id)
        # Persist FIRST, then broadcast with the row id stamped into
        # ``data["seq"]`` — live frames of persisted events carry stable
        # storage coordinates so stream consumers can deduplicate the
        # backfill/live boundary exactly. Live-only delta types skip the
        # DB and flow straight through (no added latency on the token
        # streaming path).
        persist_then_live: EventSink = PersistThenBroadcastSink(db_sink, bus_sink)
        # Coalesce per-token deltas into ~30ms batches before the
        # persist→broadcast pipeline. Reduces WS frame count and DB row
        # count without changing the canonical assistant_message/thinking
        # record.
        coalesced: EventSink = DeltaCoalescingSink(persist_then_live)
        citation_enabled = _session_citation_enabled(session)
        citation_verification_enabled = _session_citation_verification_enabled(session)
        semantic_verifier: SemanticVerifierPort | None = None
        if citation_verification_enabled and self._semantic_verifier_factory is not None:
            try:
                semantic_verifier = await self._semantic_verifier_factory(user_id, session)
            except Exception:  # noqa: BLE001 — optional sidecar must fail open
                logger.warning(
                    "semantic verifier provider unavailable for session %s",
                    session_id,
                    exc_info=True,
                )
        observer = _MessageObserverSink(
            coalesced,
            message_id=message.id,
            user_prompt=current_task_prompt,
            citation_policy_available=any(Path(path).name == "citation" for path in session.skills),
            citation_quality_policy=citation_policy_snapshot,
            allowed_document_ids=document_scope,
            force_citation_required=(document_scope is not None and citation_enabled),
            citation_enabled=citation_enabled,
            citation_verification_enabled=citation_verification_enabled,
            semantic_verifier=semantic_verifier,
            task_coverage_enabled=task_coverage_enabled,
        )

        # Sessions are self-sufficient: ``session.cwd`` is required at
        # creation. Seed the workspace stub lazily (idempotent, one stat on
        # the hot path) — there is no project-creation moment to hook.
        bootstrap_session_workspace(session.cwd, agent.name or None)
        runtime = await self._ensure_runtime(
            session_id,
            agent,
            session,
            observer,
            session.cwd,
        )
        self._active[session_id] = runtime

        try:
            await observer.emit(
                Event(
                    type="user_message",
                    data={
                        "message": user_message.text,
                        "attachments": [
                            {"source_path": a.source_path, "parsed_path": a.parsed_path}
                            for a in user_message.attachments
                        ],
                    },
                )
            )
            # The ``running`` flip above is persisted but was never announced:
            # the only ``session_update`` used to be the terminal one after the
            # turn. Clients that derive status from the event stream (session
            # header pill, control-plane ``run.status``) therefore sat on
            # ``created``/stale until end of turn. Emit the interim status here
            # so every follower — including per-turn re-subscribers on queue
            # drains — sees ``running`` the moment the turn actually starts.
            await observer.emit(
                Event(
                    type="session_update",
                    data={"status": "running", "message_id": message.id},
                )
            )
            await runtime.run(session, user_message)
            if (
                task_coverage_enabled
                and getattr(session.stop_reason, "type", None) == "end_turn"
            ):
                if not bool(
                    getattr(runtime, "supports_native_continuation", False)
                ):
                    observer.mark_task_coverage_unavailable(
                        reason="runtime-native-continuation-unsupported"
                    )
                else:
                    primary_status = session.status
                    primary_stop_reason = session.stop_reason
                    await observer.begin_task_coverage_continuation()
                    session.status = "running"
                    session.stop_reason = None
                    logger.info(
                        "task_coverage continuing message=%s session=%s on native thread",
                        message.id,
                        session.id,
                    )
                    coverage_sink = _TaskCoverageProtocolSink(observer)
                    runtime.update_sink(coverage_sink)
                    try:
                        await runtime.run_task_coverage(
                            session,
                            UserMessage(
                                text=build_task_coverage_continuation_prompt(
                                    task_coverage_policy
                                )
                            ),
                            no_op_tool=build_task_coverage_noop_tool(),
                        )
                    except Exception:  # noqa: BLE001 — optional enhancement is fail-open
                        logger.exception(
                            "task_coverage continuation failed message=%s session=%s; "
                            "preserving primary",
                            message.id,
                            session.id,
                        )
                        session.status = primary_status
                        session.stop_reason = primary_stop_reason
                        await observer.abort_task_coverage_continuation(
                            reason="runtime-exception"
                        )
                    else:
                        if coverage_sink.no_gap_declared:
                            observer.mark_task_coverage_no_gap()
                        if getattr(session.stop_reason, "type", None) != "end_turn":
                            session.status = primary_status
                            session.stop_reason = primary_stop_reason
                    finally:
                        runtime.update_sink(observer)
            await observer.ensure_partial_assistant_message()
            await observer.finalize_sidecars()
            await observer.release_session_idle()
            # finalize must run BEFORE save_session — it writes session.todos
            # (and message.todos) from the observer's last todo_update payload;
            # saving first would persist a stale snapshot.
            self._finalize_message(message, session, observer)
            # User-mutable fields (today: ``session.mode``) must survive a
            # mid-turn ``POST /mode``. The runtime holds the session by
            # reference and the unconditional ``save_session`` below would
            # otherwise clobber a parallel user write. Reconcile rule:
            #
            # * If the runtime explicitly emitted ``mode_changed{by:"runtime"}``
            #   during the turn (codex ``thread/goal/cleared`` listener or
            #   Claude bare-``/goal`` poll), the runtime's in-memory
            #   ``session.mode`` is the intended value — keep it.
            # * Otherwise reload from disk so any concurrent ``POST /mode``
            #   wins. The runtime didn't intend to change ``session.mode``;
            #   the in-memory value is just the snapshot from turn start.
            #
            # Only ``mode`` is reconciled here. Other runtime-owned fields
            # (``status``, ``stop_reason``, ``runtime_session_id``,
            # ``todos``) keep their in-memory values as before.
            if observer.runtime_mode_change is None:
                fresh = await self._store.load_session(user_id, session_id)
                if fresh is not None and fresh.mode != session.mode:
                    session.mode = fresh.mode
            await self._store.save_session(session)
            await self._store.save_message(user_id, message)
            await observer.emit(
                Event(
                    type="session_update",
                    data={"status": session.status, "message_id": message.id},
                )
            )
            return message
        finally:
            self._active.pop(session_id, None)
            self._active_message.pop(session_id, None)
            # Mark the runtime freshly-used at turn END too, not just at entry.
            # A long-running turn (in ``_active``, so never swept) could finish
            # well past the idle TTL measured from its start; without this bump
            # the very next sweep would evict a runtime that just went idle.
            if session_id in self._runtimes:
                self._runtime_last_used[session_id] = time.monotonic()
            # Defensive: if ``run()`` returned (or raised) without
            # resetting ``session.status``, force it back to ``"idle"``
            # so the DB doesn't carry a phantom ``running`` row from a
            # normal cleanup. Host crashes (SIGKILL / power loss) skip
            # this branch entirely — those orphans are intentionally
            # left for ``scan_orphan_runs`` to clean up on next startup.
            if session.status == "running":
                session.status = "idle"
                try:
                    await self._store.save_session(session)
                except Exception:
                    logger.exception(
                        "orchestrator: defensive status reset save_session failed for %s",
                        session_id,
                    )

    def active_message_id(self, session_id: str) -> str | None:
        message = self._active_message.get(session_id)
        return message.id if message is not None else None

    @staticmethod
    def _finalize_message(
        message: Message,
        session: Session,
        observer: _MessageObserverSink,
    ) -> None:
        message.ended_at = now_ms()
        message.assistant_message = observer.assistant_text
        if observer.citation_bundle is not None:
            message.metadata = {
                **message.metadata,
                "citation_bundle": observer.citation_bundle,
            }
        if observer.claim_audits:
            message.metadata = {
                **message.metadata,
                "claim_audits": copy.deepcopy(observer.claim_audits),
            }
        if observer.task_coverage is not None:
            message.metadata = {
                **message.metadata,
                "task_coverage": observer.task_coverage,
            }
        message.total_turns = observer.num_turns or 1
        message.stop_reason = session.stop_reason
        if observer.usage is not None:
            message.input_tokens = observer.usage["input_tokens"]
            message.output_tokens = observer.usage["output_tokens"]
            message.cache_read_tokens = observer.usage["cache_read_tokens"]
            message.cache_write_tokens = observer.usage["cache_write_tokens"]
        if observer.model_usage is not None:
            message.model_usage = observer.model_usage
        if observer.last_todos is not None:
            # change-only semantics: this turn's snapshot lands on Message,
            # and Session carries the live latest. UI does carry-forward.
            message.todos = list(observer.last_todos)
            session.todos = list(observer.last_todos)
        if isinstance(session.stop_reason, Error):
            message.status = (
                "cancelled" if session.stop_reason.category == "user_interrupt" else "errored"
            )
            message.error_message = observer.error_payload or {
                "category": session.stop_reason.category,
                "message": session.stop_reason.message,
            }
        else:
            message.status = "completed"

    async def interrupt(self, session_id: str) -> bool:
        runtime = self._active.get(session_id)
        if runtime is None:
            return False
        await runtime.interrupt()
        return True

    async def cleanup(self, session_id: str) -> None:
        self._active.pop(session_id, None)
        self._buses.pop(session_id, None)
        # Session-scoped approval rules are tied to the runtime's lifecycle —
        # clearing here means a cold-reload (PATCH that drops the cache,
        # process restart, explicit cleanup) starts fresh. Matches codex's
        # native ``tool_approvals`` non-persistence behavior; see
        # ``docs/design/approve-for-session.md`` §8.
        self._session_approval_cache.clear(session_id)
        runtime = self._runtimes.pop(session_id, None)
        self._runtime_last_used.pop(session_id, None)
        if runtime is not None:
            try:
                await runtime.close()
            except Exception:
                logger.debug("Error closing runtime for session %s", session_id, exc_info=True)

    async def _load_session(self, user_id: str, session_id: str) -> tuple[Any, AgentConfig]:
        session = await self._store.load_session(user_id, session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        # The embedded snapshot IS the agent for this session — the kernel
        # holds no agents table to consult.
        return session, session.agent_config

    async def _ensure_runtime(
        self,
        session_id: str,
        agent: AgentConfig,
        session: Any,
        sink: EventSink,
        workspace_root: str,
    ) -> RuntimePort:
        from src.runtimes.factory import create_runtime

        # Opportunistic eviction on every turn entry: close runtimes idle past
        # the TTL (skipping the one we're about to touch / any active turn).
        # This is the lazy half of the policy — the background sweeper covers
        # the zero-activity case; together they bound the live subprocess set.
        await self._sweep_idle_runtimes(exclude=session_id)

        cached = self._runtimes.get(session_id)
        if cached is not None:
            cached.update_sink(sink)
            self._runtime_last_used[session_id] = time.monotonic()
            return cached

        runtime = create_runtime(agent, session, sink, workspace_root=workspace_root)
        # Inject a session-rule finder so runtimes that wire
        # ``approve_for_session`` can consult the kernel-owned cache
        # before parking on the user. Implemented via duck-typed setter
        # rather than a Protocol method so runtimes that haven't wired
        # the verb yet (codex, claude in Phase 1) don't need a no-op
        # implementation. Phase 2 / 3 will lift the setter onto
        # ``RuntimePort`` once all three runtimes consume it.
        setter = getattr(runtime, "set_session_rule_finder", None)
        if callable(setter):
            setter(self._build_session_rule_finder(session_id, runtime))
        self._runtimes[session_id] = runtime
        self._runtime_last_used[session_id] = time.monotonic()
        # Enforce the hard LRU ceiling after admitting the new runtime. This is
        # the guaranteed bound on concurrent warm subprocesses, independent of
        # the TTL: no matter how many sessions are touched, at most
        # ``_max_warm_runtimes`` claude/codex processes stay alive at once.
        await self._enforce_runtime_cap(exclude=session_id)
        return runtime

    # ── Warm-runtime eviction (idle TTL + LRU cap) ─────────────────────────

    def start(self) -> None:
        """Start the background idle-sweeper. Idempotent; requires a running
        event loop (call from the composition root's async init). Eviction is
        still correct without it — the lazy sweep in ``_ensure_runtime`` runs
        on every turn — this just covers sessions that go idle with no further
        activity anywhere. No-op when the idle TTL is disabled (``<= 0``)."""
        if self._runtime_idle_ttl_s <= 0 or self._sweep_interval_s <= 0:
            return
        if self._sweeper_task is not None and not self._sweeper_task.done():
            return
        self._closing = False
        self._sweeper_task = asyncio.create_task(self._run_sweeper())

    async def shutdown(self) -> None:
        """Cancel the sweeper and close every cached runtime — i.e. terminate
        all live claude/codex subprocesses deterministically on host shutdown,
        rather than relying on the SDKs' atexit reaper. Called from
        ``app.dependencies.shutdown_dependencies``."""
        self._closing = True
        task = self._sweeper_task
        self._sweeper_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for session_id in list(self._runtimes):
            await self._evict_runtime(session_id)

    async def _run_sweeper(self) -> None:
        """Periodic idle sweep loop. Resilient: a failing sweep is logged and
        the loop continues; cancellation (shutdown) propagates."""
        try:
            while not self._closing:
                await asyncio.sleep(self._sweep_interval_s)
                if self._closing:
                    break
                try:
                    await self._sweep_idle_runtimes()
                except Exception:  # noqa: BLE001 — a bad sweep must not kill the loop
                    logger.debug("runtime idle sweep failed", exc_info=True)
        except asyncio.CancelledError:
            raise

    def _has_live_background_tasks(self, session_id: str) -> bool:
        """Duck-typed busy signal: the claude runtime exposes
        ``has_live_background_tasks`` while a ``run_in_background`` process it
        spawned is still running (the process is a child of the CLI subprocess,
        so eviction would kill the user's work mid-task). Runtimes without the
        attribute are never bg-busy."""
        runtime = self._runtimes.get(session_id)
        return bool(getattr(runtime, "has_live_background_tasks", False))

    def bg_busy_session_ids(self) -> list[str]:
        """Session ids of warm runtimes with live background tasks.

        Process-scoped (the orchestrator holds no owner index) — callers
        intersect with their own owner-scoped session set. The host's
        activity overview uses this so a session whose turn ended but whose
        ``run_in_background`` process is still running keeps signalling
        in-flight work."""
        return [sid for sid in self._runtimes if self._has_live_background_tasks(sid)]

    async def _sweep_idle_runtimes(self, *, exclude: str | None = None) -> None:
        """Close every cached runtime untouched for longer than the idle TTL.
        Never evicts an active turn (``_active``) — a parked approval keeps the
        session active because ``runtime.run()`` is still awaiting, so this also
        protects sessions waiting on a user decision. Runtimes with live
        background tasks get the extended ``bg_busy_runtime_ttl_s`` instead of
        the normal TTL (see the constant's comment for the rationale)."""
        if self._runtime_idle_ttl_s <= 0:
            return
        now = time.monotonic()
        stale: list[str] = []
        for sid, ts in list(self._runtime_last_used.items()):
            if sid == exclude or sid in self._active:
                continue
            ttl = self._runtime_idle_ttl_s
            if self._has_live_background_tasks(sid):
                if self._bg_busy_runtime_ttl_s <= 0:
                    continue  # full exemption
                ttl = max(ttl, self._bg_busy_runtime_ttl_s)
            if (now - ts) >= ttl:
                stale.append(sid)
        for sid in stale:
            await self._evict_runtime(sid)

    async def _enforce_runtime_cap(self, *, exclude: str | None = None) -> None:
        """Evict least-recently-used runtimes until the warm set is within the
        cap. Skips active turns and runtimes with live background tasks; if
        every over-cap entry is protected, the cap is briefly exceeded rather
        than tearing down a running subprocess (or killing background work).
        The extended-TTL sweep remains the backstop that unwinds a prolonged
        excess."""
        if self._max_warm_runtimes <= 0:
            return
        if len(self._runtimes) <= self._max_warm_runtimes:
            return
        evictable = sorted(
            (
                sid
                for sid in self._runtimes
                if sid != exclude
                and sid not in self._active
                and not self._has_live_background_tasks(sid)
            ),
            key=lambda s: self._runtime_last_used.get(s, 0.0),
        )
        for sid in evictable:
            if len(self._runtimes) <= self._max_warm_runtimes:
                break
            await self._evict_runtime(sid)

    async def _evict_runtime(self, session_id: str) -> None:
        """Drop a runtime from the warm cache and close it (kills its CLI
        subprocess). Keeps the session's event bus so an attached client keeps
        streaming and the next turn rebuilds the runtime (resuming via the
        persisted ``runtime_session_id``) — a cold reload, hence the approval
        cache is cleared to match ``cleanup`` semantics. Use ``cleanup`` (not
        this) when the session itself is going away."""
        runtime = self._runtimes.pop(session_id, None)
        self._runtime_last_used.pop(session_id, None)
        self._session_approval_cache.clear(session_id)
        if runtime is None:
            return
        try:
            await runtime.close()
        except Exception:  # noqa: BLE001
            logger.debug("Error evicting runtime for session %s", session_id, exc_info=True)
        else:
            logger.info("Evicted warm runtime for idle/over-cap session %s", session_id)

    def _build_session_rule_finder(
        self,
        session_id: str,
        runtime: RuntimePort,
    ) -> SessionRuleFinder:
        """Close over ``(session_id, cache, runtime.approval_rule_matcher)``
        so the runtime can check the cache without a backref to the
        orchestrator. Matcher is per-runtime — its ``match`` is the only
        code path that interprets ``rule_data``."""
        cache = self._session_approval_cache
        matcher = runtime.approval_rule_matcher

        def find(
            subject: str,
            tool_name: str,
            args: dict[str, Any],
            runtime_extras: dict[str, Any],
        ) -> SessionRule | None:
            return cache.find_match(session_id, subject, tool_name, args, runtime_extras, matcher)

        return find

    # ── Approval contract (Phase 1 / Slice 2) ──────────────────────────

    async def submit_action(
        self,
        user_id: str,
        session_id: str,
        pending_id: str,
        decision: Literal[
            "approve", "approve_with_changes", "approve_for_session", "reject", "answer"
        ],
        message: str | None = None,
        answers: dict[str, str | list[str]] | None = None,
        modified_input: dict[str, Any] | None = None,
    ) -> SubmitActionResult:
        """Resolve a pending ``requires_action`` event.

        Validation order (raises one of the typed errors below):
          1. Session loadable (else SessionNotFoundError)
          3. ``pending_id`` matches a ``requires_action`` event
             (else PendingActionNotFoundError)
          4. Decision matches the pending's subject and the pending's
             ``available_decisions``:
             - ``answer`` is only valid for ``clarifying_questions``,
               and that subject rejects bare ``approve`` /
               ``approve_with_changes`` (Claude SDK needs the
               structured ``answers`` payload).
             - ``approve_with_changes`` is only valid for tool-approval
               subjects on runtimes that expose the verb in
               ``available_decisions`` (Claude / DeepAgents); codex
               pendings reject it because their SDK has no
               ``updated_input`` analog.
             - ``approve_for_session`` requires the pending to
               advertise the verb in ``available_decisions`` AND to
               carry a ``session_rule_preview`` field populated by the
               runtime's matcher at emit time. Missing preview is a
               400 (runtime mis-wired). See
               ``docs/design/approve-for-session.md`` §3.2.
             Mismatch → PendingActionDecisionMismatchError.
          5. Pending not already sealed
             - same decision → idempotent 200 with original timestamp
             - different decision → PendingActionConflictError
             - ``expired`` / ``interrupted`` → PendingActionExpiredError
          6. A runtime must be parked on this approval (turn in flight)
             (else RuntimeUnavailableError)
          7. For ``approve_for_session``: commit the rule to the kernel
             cache, then forward to the runtime as plain ``approve`` —
             the rule lifecycle is kernel-owned, the runtime only sees
             SDK-mappable verbs.
          8. Forward decision to the runtime; if the runtime hasn't wired
             the bridge yet, raise ApprovalNotImplementedError so the
             route can surface 501 instead of 500
          9. Emit ``action_resolved`` (DB + bus) — includes ``answers``
             when ``decision == "answer"``, ``modified_input`` when
             ``decision == "approve_with_changes"``, and ``rule_id``
             when ``decision == "approve_for_session"`` so reconnects
             can replay the complete decision.
        """
        session = await self._store.load_session(user_id, session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        pending_event, resolved_event = await self._derive_pending(user_id, session_id, pending_id)
        if pending_event is None:
            raise PendingActionNotFoundError(pending_id)

        # Subject ↔ decision invariant. We treat this as a 400 rather than
        # a 409 because it's a contract violation (wrong shape for this
        # pending), not a legitimate race between two clients.
        pending_subject = str(pending_event.data.get("subject", ""))
        if pending_subject == "clarifying_questions":
            if decision not in ("answer", "reject"):
                raise PendingActionDecisionMismatchError(pending_id, pending_subject, decision)
        elif decision == "answer":
            raise PendingActionDecisionMismatchError(pending_id, pending_subject, decision)
        # ``approve_with_changes`` is per-pending — only Claude / DeepAgents
        # advertise it in ``available_decisions``. Codex emits the V1 baseline
        # so its pendings reject the verb here. Reading from the pending
        # event keeps the runtime as the source of truth — orchestrator
        # doesn't duplicate the SDK capability matrix.
        if decision == "approve_with_changes":
            allowed = pending_event.data.get("available_decisions") or []
            if "approve_with_changes" not in allowed:
                raise PendingActionDecisionMismatchError(pending_id, pending_subject, decision)
        # ``approve_for_session`` follows the same available_decisions gate
        # and additionally requires ``session_rule_preview`` on the pending
        # (the runtime's matcher fills this in when emitting). Missing
        # preview = runtime wired the verb without the preview — a 400
        # contract violation, not a 409 race.
        if decision == "approve_for_session":
            allowed = pending_event.data.get("available_decisions") or []
            if "approve_for_session" not in allowed:
                raise PendingActionDecisionMismatchError(pending_id, pending_subject, decision)
            preview = pending_event.data.get("session_rule_preview")
            if not isinstance(preview, dict):
                raise PendingActionDecisionMismatchError(pending_id, pending_subject, decision)

        if resolved_event is not None:
            previous = str(resolved_event.data.get("decision", ""))
            if previous in ("expired", "interrupted"):
                raise PendingActionExpiredError(pending_id, previous)
            if previous == decision:
                # Idempotent replay surfaces the original rule_id so the
                # client can re-discover the rule it created (e.g. WS
                # reconnect after the user double-clicked the button).
                prior_rule_id = resolved_event.data.get("rule_id")
                return SubmitActionResult(
                    pending_id=pending_id,
                    decision=decision,
                    accepted_at=resolved_event.timestamp,
                    idempotent=True,
                    rule_id=str(prior_rule_id) if isinstance(prior_rule_id, str) else None,
                )
            raise PendingActionConflictError(pending_id, previous, decision)

        runtime = self._runtimes.get(session_id)
        active_message = self._active_message.get(session_id)
        if runtime is None or active_message is None:
            # Pending exists in events but the runtime is gone — typical
            # cause: host restart, but startup scan should have sealed
            # the row first. Surface as 400 so the client refetches.
            raise RuntimeUnavailableError(session_id)

        # ``approve_for_session`` commits the rule kernel-side BEFORE
        # talking to the runtime — that way a runtime-side failure
        # leaves no orphaned rule, and the next matching call's cache
        # check sees the rule. The runtime always sees plain ``approve``
        # at its boundary (it has no SDK verb for session persistence;
        # see §5 of the design doc).
        committed_rule: SessionRule | None = None
        if decision == "approve_for_session":
            preview = pending_event.data["session_rule_preview"]
            committed_rule = SessionRule(
                rule_id=str(uuid.uuid4()),
                session_id=session_id,
                originating_pending_id=pending_id,
                subject=pending_subject,  # type: ignore[arg-type]
                runtime_kind=str(preview.get("runtime_kind", "exact")),
                display=str(preview.get("display", "")),
                rule_data=dict(preview.get("rule_data") or {}),
                created_at=now_ms(),
            )
            self._session_approval_cache.put(committed_rule)

        # Translate ``approve_for_session`` → ``approve`` at the runtime
        # boundary. The runtime's ``submit_action`` Literal does not
        # include the session verb (kernel-only).
        runtime_decision: Literal["approve", "approve_with_changes", "reject", "answer"]
        if decision == "approve_for_session":
            runtime_decision = "approve"
        else:
            runtime_decision = decision
        try:
            await runtime.submit_action(
                pending_id, runtime_decision, message, answers, modified_input
            )
        except NotImplementedError as exc:  # noqa: PERF203 — single-handler
            raise ApprovalNotImplementedError(str(exc)) from exc

        message_id = active_message.id
        resolved_data: dict[str, Any] = {
            "pending_id": pending_id,
            "decision": decision,
            "message": message,
            "resolved_by": "user",
        }
        # Payload-carrying verbs persist their payload on the event so
        # reconnect can replay the complete decision shape. Synthetic
        # emits (expired / interrupted) never carry these, mirroring
        # the bare reject case.
        if decision == "answer" and answers is not None:
            resolved_data["answers"] = answers
        if decision == "approve_with_changes" and modified_input is not None:
            resolved_data["modified_input"] = modified_input
        if committed_rule is not None:
            resolved_data["rule_id"] = committed_rule.rule_id
        resolved = Event(type="action_resolved", data=resolved_data)
        await self._store.append_event(user_id, session_id, message_id, resolved)
        bus = self._get_or_create_bus(session_id)
        await bus.emit(
            Event(
                type=resolved.type,
                data={**resolved.data, "message_id": message_id},
                timestamp=resolved.timestamp,
            )
        )
        return SubmitActionResult(
            pending_id=pending_id,
            decision=decision,
            accepted_at=resolved.timestamp,
            idempotent=False,
            rule_id=committed_rule.rule_id if committed_rule is not None else None,
        )

    # ── Internal helpers for runtime auto-approve flow ─────────────────

    @property
    def session_approval_cache(self) -> SessionApprovalCache:
        """Read-only access to the kernel-owned cache. Exposed primarily
        for tests; production runtimes consult the cache via the
        ``SessionRuleFinder`` injected by ``_ensure_runtime``."""
        return self._session_approval_cache

    async def _derive_pending(
        self, user_id: str, session_id: str, pending_id: str
    ) -> tuple[Event | None, Event | None]:
        """Return ``(requires_action, action_resolved)`` for ``pending_id``.

        Linear scan over the session's events log. Per design doc §4.4
        pending state is a derived view over events rather than a parallel
        table; for v1 the read path is good enough at low session
        cardinality.
        """
        pending: Event | None = None
        resolved: Event | None = None
        # Filter to the two pending markers at the store. A ``requires_action``
        # is the MOST RECENT event when it is resolved, so an unfiltered
        # oldest-first read (this used to cap at ``limit=1000, offset=0``)
        # silently dropped it in any session with >N events and ``submit_action``
        # then 404'd a live approval. The type filter makes the read
        # O(pendings), not O(session length), so length no longer matters.
        events = await self._store.get_events(
            user_id,
            session_id,
            types=("requires_action", "action_resolved"),
            limit=1000,
        )
        for ev in events:
            if ev.data.get("pending_id") != pending_id:
                continue
            if ev.type == "requires_action" and pending is None:
                pending = ev
            elif ev.type == "action_resolved" and resolved is None:
                resolved = ev
        return pending, resolved

    async def scan_orphan_pendings(self) -> int:
        """Seal every still-open ``requires_action`` with a synthetic
        ``action_resolved(decision="expired", resolved_by="system")``.

        Called on host startup (per design doc §6.3) — pending approvals
        do not survive a host process restart in v1; the contract is
        uniform across runtimes even though DeepAgents could technically
        do better. Returns the number of synthetic resolutions emitted.
        """
        sealed = 0
        # Own-lineage sweep: ``self._store`` reads are the kernel's runtime
        # sqlite (RuntimeStore authority) — sessions live on other processes
        # are structurally out of reach, so this is safe in every deployment.
        # ``user_id=None`` spans every owner within this kernel's own store.
        sessions = await self._store.list_sessions(None, status="running", limit=500)
        for session in sessions:
            sealed += await self._seal_session_pendings(session.user_id, session.id)
        return sealed

    async def _seal_session_pendings(self, user_id: str, session_id: str) -> int:
        """Seal one session's open ``requires_action`` events (see
        ``src.core.recovery.seal_session_pendings``) on this kernel's store."""
        return await recovery.seal_session_pendings(self._store, user_id, session_id)

    async def scan_orphan_runs(self) -> int:
        """On host startup, reset sessions left in ``status="running"``.

        These are turns the previous host process started (``run_turn``
        writes ``status="running"`` before calling the runtime, since
        the 2026-05 in-flight-status change) but never got to flip
        back to ``idle`` because the process was killed mid-turn. We:

        1. Set ``session.status = "idle"`` + ``stop_reason =
           Error(category="host_restart", ...)`` so the UI's session
           chip stops showing a phantom running indicator.
        2. Walk the session's messages and mark any
           ``Message.status == "running"`` row as ``"errored"`` with a
           ``host_restart`` ``error_message`` and ``ended_at = now`` —
           otherwise history reads would render a perpetual spinner.

        Pairs with ``scan_orphan_pendings`` (which seals any
        ``requires_action`` events still open on the same orphan
        turns). Both run from ``app/dependencies.py`` on startup. The
        ``status="idle"`` -> ``"running"`` -> ``"idle"`` cycle in a
        healthy turn never trips this scanner because the live
        ``run_turn`` ``finally`` block resets the status before save
        in the normal cleanup path. Only a true crash (SIGKILL /
        power loss / OOM) leaves the row behind.

        Returns the number of sessions reset.
        """
        reset = 0
        # Own-lineage sweep (see scan_orphan_pendings). Sessions stranded on
        # OTHER processes are the HOST's to reconcile (liveness-checked
        # ``reset_stranded_session``) — never this kernel's.
        sessions = await self._store.list_sessions(None, status="running", limit=500)
        for session in sessions:
            await self._reset_stranded(session)
            reset += 1
        return reset

    async def _reset_stranded(self, session: Session) -> None:
        """Reset one stranded session (see ``src.core.recovery.reset_stranded``)
        on this kernel's store."""
        await recovery.reset_stranded(self._store, session)

    async def reset_stranded_session(self, user_id: str, session_id: str) -> bool:
        """Per-session stranded reset on this kernel's own store (see
        ``src.core.recovery.reset_stranded_session`` — the host applies the
        same semantics to its durable for sessions whose sandbox is gone)."""
        return await recovery.reset_stranded_session(self._store, user_id, session_id)

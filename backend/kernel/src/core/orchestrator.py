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

import dataclasses
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from src.core.agent_config import AgentConfig
from src.core.events import Event, EventSink, GlobalEventTap
from src.core.prompt_builder import wrap_for_mode
from src.core.runtime_port import RuntimePort
from src.core.session_approval_cache import SessionApprovalCache, SessionRule
from src.core.session_bus import SessionEventBus
from src.core.store_port import StorePort
from src.core.time_utils import now_ms
from src.core.types import Error, Message, Session, UserMessage
from src.core.workspace import bootstrap_session_workspace

# Per-session callable injected into runtimes that wire ``approve_for_session``.
# Closes over (session_id, cache, runtime.approval_rule_matcher) so the
# runtime can check the cache without depending on SessionOrchestrator.
# Return value: matching ``SessionRule`` on hit, ``None`` on miss.
# See ``docs/design/approve-for-session.md`` §3.3 for the cache-hit flow.
SessionRuleFinder = Callable[[str, str, dict[str, Any], dict[str, Any]], "SessionRule | None"]

logger = logging.getLogger(__name__)


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


class _MessageObserverSink:
    """Forwards events to ``inner`` while accumulating per-Message state.

    Captures the assistant text fragments emitted as ``assistant_message``
    events, the ``num_turns`` reported in ``session_idle``, and any
    ``session_error`` payload. The orchestrator reads these accumulators when
    finalizing the Message row.
    """

    def __init__(self, inner: EventSink) -> None:
        self._inner = inner
        self._assistant_chunks: list[str] = []
        self.num_turns: int = 0
        self.error_payload: dict[str, Any] | None = None
        self.usage: dict[str, int] | None = None
        self.model_usage: dict[str, Any] | None = None
        # Last `todo_update` payload observed in this turn. None means the
        # agent did not touch the TODO list. An empty list is a meaningful
        # "all done" signal from the SDK and is preserved.
        self.last_todos: list[dict[str, Any]] | None = None
        # Captures runtime-emitted ``mode_changed{by: "runtime"}`` events
        # (codex ``thread/goal/cleared`` listener, Claude bare-``/goal``
        # poll). Used by ``run_turn`` to decide whether the final
        # ``save_session`` should honor the runtime's in-memory
        # ``session.mode`` (runtime emitted a change → keep it) or
        # reload from disk (user mutated mode mid-turn via ``POST /mode``
        # → don't clobber). ``None`` means no runtime-emitted mode
        # change observed this turn.
        self.runtime_mode_change: Literal["default", "plan", "goal"] | None = None

    async def emit(self, event: Event) -> None:
        if event.type == "assistant_message":
            text = event.data.get("text") or ""
            if text:
                self._assistant_chunks.append(str(text))
        elif event.type == "session_idle":
            raw = event.data.get("num_turns")
            if isinstance(raw, int) and raw > 0:
                self.num_turns = raw
        elif event.type == "session_error":
            self.error_payload = {
                "category": "execution_error",
                "message": str(event.data.get("message", "")),
            }
        elif event.type == "usage_update":
            self.usage = {
                "input_tokens": int(event.data.get("input_tokens") or 0),
                "output_tokens": int(event.data.get("output_tokens") or 0),
                "cache_read_tokens": int(event.data.get("cache_read_tokens") or 0),
                "cache_write_tokens": int(event.data.get("cache_write_tokens") or 0),
            }
            raw_model_usage = event.data.get("model_usage")
            self.model_usage = dict(raw_model_usage) if isinstance(raw_model_usage, dict) else None
        elif event.type == "todo_update":
            raw_todos = event.data.get("todos")
            if isinstance(raw_todos, list):
                self.last_todos = [dict(t) for t in raw_todos if isinstance(t, dict)]
        elif event.type == "mode_changed":
            if event.data.get("by") == "runtime":
                raw_mode = event.data.get("mode")
                if raw_mode in ("default", "plan", "goal"):
                    self.runtime_mode_change = raw_mode
        await self._inner.emit(event)

    @property
    def assistant_text(self) -> str | None:
        if not self._assistant_chunks:
            return None
        return "\n".join(self._assistant_chunks)


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

    def __init__(self, store: StorePort) -> None:
        self._store = store
        self._runtimes: dict[str, RuntimePort] = {}
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

    @property
    def active_sessions(self) -> set[str]:
        return set(self._active)

    def has_cached_runtime(self, session_id: str) -> bool:
        return session_id in self._runtimes

    def _get_or_create_bus(self, session_id: str) -> SessionEventBus:
        bus = self._buses.get(session_id)
        if bus is None:
            bus = SessionEventBus(taps=[_GlobalForwardTap(session_id, self._global_taps)])
            self._buses[session_id] = bus
        return bus

    async def attach_session_tap(
        self, session_id: str, sink: EventSink, *, replay: bool = False
    ) -> None:
        """Register a passive multi-subscriber tap on a session's live stream.

        Unlike :meth:`attach_session_sink` (the single client slot used by
        the WS run channel), taps coexist: any number of observers — SSE
        streams, host aggregators — can tap one session without displacing
        the client or each other. ``replay=True`` first delivers the events
        of the in-progress message so a mid-turn tap sees a coherent view.
        """
        bus = self._get_or_create_bus(session_id)
        replay_events = await self._build_replay(session_id) if replay else []
        await bus.add_tap(sink, replay=replay_events)

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

    async def attach_session_sink(self, session_id: str, sink: EventSink) -> None:
        """Subscribe ``sink`` to this session's live event stream.

        If a turn is currently in flight, replays the events of the
        in-progress message first so the new client sees a coherent
        view of the run-so-far. Subsequent live emits arrive in order.
        """
        bus = self._get_or_create_bus(session_id)
        replay = await self._build_replay(session_id)
        await bus.attach(sink, replay=replay)

    async def detach_session_sink(self, session_id: str, sink: EventSink) -> None:
        """Unsubscribe ``sink``. Does not affect the running turn."""
        bus = self._buses.get(session_id)
        if bus is not None:
            await bus.detach(sink)

    async def _build_replay(self, session_id: str) -> list[Event]:
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
        raw_events = await self._store.get_events_for_message(active_message.id)
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
        from src.adapters.persist_then_broadcast_sink import PersistThenBroadcastSink
        from src.adapters.delta_coalescing_sink import DeltaCoalescingSink

        session, agent = await self._load_session(session_id)

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
        await self._store.save_message(message)
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
        db_sink = DatabaseEventSink(self._store, session_id, message.id)
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
        observer = _MessageObserverSink(coalesced)

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
            await runtime.run(session, user_message)
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
                fresh = await self._store.load_session(session_id)
                if fresh is not None and fresh.mode != session.mode:
                    session.mode = fresh.mode
            await self._store.save_session(session)
            await self._store.save_message(message)
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
        if runtime is not None:
            try:
                await runtime.close()
            except Exception:
                logger.debug("Error closing runtime for session %s", session_id, exc_info=True)

    async def _load_session(self, session_id: str) -> tuple[Any, AgentConfig]:
        session = await self._store.load_session(session_id)
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

        cached = self._runtimes.get(session_id)
        if cached is not None:
            cached.update_sink(sink)
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
        return runtime

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
        session = await self._store.load_session(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        pending_event, resolved_event = await self._derive_pending(session_id, pending_id)
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
        await self._store.append_event(session_id, message_id, resolved)
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
        self, session_id: str, pending_id: str
    ) -> tuple[Event | None, Event | None]:
        """Return ``(requires_action, action_resolved)`` for ``pending_id``.

        Linear scan over the session's events log. Per design doc §4.4
        pending state is a derived view over events rather than a parallel
        table; for v1 the read path is good enough at low session
        cardinality.
        """
        pending: Event | None = None
        resolved: Event | None = None
        events = await self._store.get_events(session_id, limit=1000, offset=0)
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
        sessions = await self._store.list_sessions(status="running", limit=500)
        for session in sessions:
            events = await self._store.get_events(session.id, limit=1000, offset=0)
            open_pendings: dict[str, str] = {}  # pending_id → message_id
            for ev in events:
                pid = ev.data.get("pending_id")
                if not isinstance(pid, str):
                    continue
                if ev.type == "requires_action":
                    msg_id = ev.data.get("message_id")
                    if isinstance(msg_id, str):
                        open_pendings.setdefault(pid, msg_id)
                elif ev.type == "action_resolved":
                    open_pendings.pop(pid, None)
            for pid, msg_id in open_pendings.items():
                await self._store.append_event(
                    session.id,
                    msg_id,
                    Event(
                        type="action_resolved",
                        data={
                            "pending_id": pid,
                            "decision": "expired",
                            "resolved_by": "system",
                        },
                    ),
                )
                sealed += 1
        return sealed

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
        sessions = await self._store.list_sessions(status="running", limit=500)
        for session in sessions:
            session.status = "idle"
            session.stop_reason = Error(
                category="host_restart",
                retry_status="terminal",
                message="host process restarted while turn was in flight",
            )
            await self._store.save_session(session)
            messages = await self._store.list_messages_for_session(session.id)
            now = now_ms()
            for m in messages:
                if m.status != "running":
                    continue
                m.status = "errored"
                m.error_message = {
                    "category": "host_restart",
                    "message": "host process restarted while message was in flight",
                }
                m.ended_at = now
                await self._store.save_message(m)
            reset += 1
        return reset

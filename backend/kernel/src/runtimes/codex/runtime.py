"""CodexRuntime — wraps OpenAI Codex Python SDK as a RuntimePort.

Per ``docs/design/CODEX-INTEGRATION-DESIGN.md`` +
``docs/design/cross-runtime-approval-contract.md`` Phase 2:

* one ``AsyncCodex`` per Session, lazy-spawned on the first turn
* ``Session.instructions`` -> ``thread_start(developer_instructions=...)``
* ``Session.mcp_servers`` -> one-shot Codex config overrides
  (the per-thread ``ThreadStartParams.config`` dict silently drops
  unknown keys; secret MCP headers / stdio environment values are converted to
  Codex's ``env_http_headers`` / ``env_vars`` references so only environment
  variable names, never their values, enter ``--config k=v`` process flags).
* ``Session.skills`` -> materialized into ``cwd/.agents/skills/``
* ``Session.permission_mode`` drives the codex preset selection in
  ``_build_thread_kwargs`` (``never``+``danger-full-access`` for
  ``full_access``; ``on_request``+``workspace_write`` plus
  ``ApprovalsReviewer.user`` / ``.auto_review`` otherwise).
* token-level streaming via ``item/agentMessage/delta`` and
  ``item/reasoning/textDelta``; canonical assistant_message / thinking
  events on ``item/completed``
* default-mode approval bridge: monkey-patches
  ``_codex._client._sync._approval_handler`` (the ``AsyncAppServerClient``
  ctor doesn't expose the kwarg) and parks the sync caller on a
  cross-thread future via ``run_coroutine_threadsafe`` until
  ``submit_action`` resolves it or the 1h global timeout fires.
  Wire format depends on the request method (``{"decision": ...}`` for
  ``commandExecution`` / ``fileChange``, ``{"action": ...}`` for the
  MCP elicitation envelope) — see ``approval_bridge._build_approval_response``.
* commandExecution outputDelta buffered into the final ``tool_result``
  (no live shell-output stream to the UI)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import asdict
from typing import Any, Literal, cast
from urllib.parse import quote_plus

from openai_codex import (
    AsyncCodex,
    AsyncThread,
    AsyncTurnHandle,
    CodexConfig,
)
from openai_codex.generated.v2_all import (
    ApprovalsReviewer,
    AskForApproval,
    AskForApprovalValue,
    SandboxMode,
    TextUserInput,
    ThreadForkParams,
    ThreadResumeParams,
    ThreadStartParams,
    TurnCompletedNotification,
    TurnStartParams,
    TurnStatus,
    UserInput,
)
from pydantic import RootModel
from src.core.agent_config import AgentConfig
from src.core.approval_rule_matcher import ExactArgsRuleMatcher, RuntimeApprovalRuleMatcher
from src.core.events import AVAILABLE_DECISIONS_V1_WITH_SESSION, Event, EventSink
from src.core.rule_canonicalize import reduce_args_for_subject
from src.core.session_approval_cache import SessionRule
from src.core.tools import ExecContext, ToolDef, ToolKit
from src.core.types import (
    EndTurn,
    Error,
    McpStdioServerConfig,
    ModelProvider,
    ModelSettings,
    Session,
    StopReason,
    UserMessage,
    is_bare_completion,
)

# Approval bridge — pure helpers live in ``approval_bridge.py``; we
# re-export them here so existing call sites importing from
# ``runtime.py`` (e.g. tests written before the split) keep working.
from src.runtimes.codex.approval_bridge import (
    _build_approval_response,
    _build_codex_pending_payload,
    _classify_codex_subject,
    _extract_matcher_inputs,
)
from src.runtimes.codex.event_mapper import (
    extract_error,
    extract_goal_cleared,
    extract_mcp_server_status,
    extract_token_usage,
    extract_turn_completed,
    map_notification,
)
from src.runtimes.interruption import (
    absorb_interrupt_cancellations,
    describe_exception,
    is_runtime_interruption,
)
from src.runtimes.network_egress import (
    ModelIngressDescriptor,
    merge_loopback_no_proxy,
    record_runtime_egress_phase,
)

logger = logging.getLogger(__name__)


# Pydantic shape used to consume the response of ``thread/goal/clear``
# (and other codex JSON-RPC methods that don't have typed wrappers in
# the vendored SDK — the codex-goal-spike confirmed
# ``ThreadGoalClearResponse`` is absent from ``v2_all.py``). The
# SDK's ``AppServerClient.request`` API requires a ``response_model``
# kwarg; this RootModel accepts whatever JSON object codex returns
# without imposing a schema, so we don't have to chase per-method
# response shapes.
class _OpaqueDictResponse(RootModel[dict[str, Any]]):
    """Accept any JSON-object response for typed-wrapper-free JSON-RPC methods."""


CODEX_BIN_OVERRIDE_ENV = "CODEX_BIN_OVERRIDE"

# Where the codex subprocess can reach the harness backend's MCP-over-HTTP
# endpoint. Defaults to the dev backend's loopback address; production
# deploys can override (still must be reachable from the codex process —
# typically same host or private network only).
CODEX_TOOLKIT_BASE_URL_ENV = "CODEX_TOOLKIT_BASE_URL"
CODEX_TOOLKIT_BASE_URL_DEFAULT = "http://127.0.0.1:8000"

# MCP server name the codex config block uses for the harness toolkit.
_HARNESS_TOOLKIT_MCP_NAME = "harness_toolkit"

# codex caps every MCP tool call at 120s by default; the harness toolkit hosts
# ``await_members``, which parks up to one window unit. Raise the ceiling for the
# kernel-exposed toolkit so a healthy await isn't aborted with "timed out
# awaiting tools/call after 120s". = the host's await window (600) + a 120s
# margin (mirrors capability_resolver._INTERNAL_MCP_TOOL_TIMEOUT_SEC).
_HARNESS_TOOLKIT_TOOL_TIMEOUT_SEC = 720.0


class CodexRuntime:
    """Wraps the Codex SDK (``AsyncCodex`` + ``AsyncThread``) as a RuntimePort."""

    supports_native_continuation = True

    def __init__(
        self,
        config: AgentConfig,
        model: str,
        event_sink: EventSink,
        toolkit: ToolKit | None = None,
        workspace_root: str = "",
        model_provider: ModelProvider | None = None,
        model_settings: ModelSettings | None = None,
        egress_descriptor: ModelIngressDescriptor | None = None,
    ) -> None:
        self.config = config
        self.model = model
        self.event_sink = event_sink
        # ``ToolKit`` is exposed to codex as an MCP-over-HTTP server mounted
        # on the FastAPI app (see ``app/mcp_toolkit_router.py`` and
        # ``docs/design/CODEX-CUSTOM-TOOLS-DESIGN.md`` Option C). Each
        # session registers its toolkit + ExecContext at first turn and
        # unregisters on ``close()``. The endpoint is unauthenticated by
        # design — backend must bind loopback / private network.
        self.toolkit = toolkit
        self.workspace_root = workspace_root
        self.model_provider = model_provider
        self.model_settings = model_settings
        self.egress_descriptor = egress_descriptor

        self._codex: AsyncCodex | None = None
        self._thread: AsyncThread | None = None
        self._active_turn: AsyncTurnHandle | None = None
        self._active_task: asyncio.Task[Any] | None = None
        # ``interrupt()`` cancels of ``_active_task`` this turn; balanced by
        # ``run()`` after it swallows the injected ``CancelledError`` (see
        # ``absorb_interrupt_cancellations``).
        self._interrupt_cancels = 0
        self._prepare_lock = asyncio.Lock()
        self._background_prepare_tasks: set[asyncio.Task[Any]] = set()
        self._cancelled: bool = False
        # Native fork anchor for the codex turn most recently started:
        # ``{"provider": "codex", "thread_id": ..., "turn_id": ...}``. Set
        # from the ``turn/start`` response, read-and-cleared by the
        # orchestrator (``consume_turn_anchor``) into
        # ``Message.metadata["runtime_native"]`` — the message-granularity
        # fork seam (``thread/fork(lastTurnId)``, docs/design/session-fork.md).
        self._turn_anchor: dict[str, Any] | None = None
        # Tracks whether this runtime registered a toolkit endpoint so
        # ``close()`` can revoke it without needing the session reference.
        self._registered_session_id: str | None = None
        self._egress_runtime_key: str | None = None
        self._egress_turn_attempt_id: str | None = None

        # Approval bridge (Phase 2).
        # ``_pending_futures`` maps pending_id -> asyncio.Future that
        # ``_approval_handler`` is parked on via cross-thread Future.
        # ``submit_action`` resolves the future to (decision, message).
        # ``_cached_permission_mode`` is captured at ``_build_thread_kwargs``
        # time so the sync handler (which doesn't receive session) knows
        # whether to park or auto-accept. PATCHing session.permission_mode
        # mid-turn has no effect until the next turn (cold-reload semantics).
        self._pending_futures: dict[
            str, asyncio.Future[tuple[Literal["approve", "reject"], str | None]]
        ] = {}
        self._cached_permission_mode: Literal["default", "auto_review", "full_access"] = (
            "full_access"
        )
        # Last value actually applied to a turn — used by ``run()`` to
        # detect a PATCH on ``session.permission_mode`` /
        # ``session.model_settings.effort`` and surface the change as a
        # per-turn ``TurnStartParams`` override on the very next turn.
        # See the cross-runtime "PATCH applies on next turn after Send"
        # contract in ``docs/references/claude-agent-options-and-mutators.md``.
        self._applied_permission_mode: Literal["default", "auto_review", "full_access"] | None = (
            None
        )
        self._applied_effort: str | None = None
        # Slice 6 follow-up of session-modes: tracks the last-applied
        # ``session.mode`` so a user-initiated transition out of goal
        # can fire ``thread/goal/clear`` JSON-RPC at the next turn
        # start (mirrors Claude's reconcile mode-arm). Slice-6's
        # listener for ``thread/goal/cleared`` already covers the
        # model-self-completion path; this tracker covers the gap
        # where the user picks ``mode = "default"`` while a goal is
        # still running.
        self._applied_mode: Literal["default", "plan", "goal"] | None = None
        # Captured at ``run()`` time so the sync handler can post coros.
        self._loop: asyncio.AbstractEventLoop | None = None
        # Default rule matcher: exact (tool_name, canonical args) match.
        # ``_extract_matcher_inputs`` reduces codex's raw JSON-RPC params
        # to a stable shape before the matcher sees them (drops the
        # model-generated ``reason`` for shell commands, etc.). Spec
        # §5.2 explicitly opts out of codex's native ``AcceptForSession``
        # on the wire — kernel cache stays the single source of truth
        # for event-flow uniformity.
        self._approval_rule_matcher: RuntimeApprovalRuleMatcher = ExactArgsRuleMatcher()
        # Per-session callable injected by ``SessionOrchestrator._ensure_runtime``;
        # closes over (session_id, cache, this runtime's matcher) so the
        # runtime can consult the kernel-owned cache without a backref.
        # ``None`` until the orchestrator wires it (factory unit tests
        # without an orchestrator stay green — cache miss is the safe
        # fallback).
        self._session_rule_finder: (
            Callable[
                [str, str, dict[str, Any], dict[str, Any]],
                SessionRule | None,
            ]
            | None
        ) = None

    APPROVAL_TIMEOUT_SECONDS: float = 3600.0  # 1 h; class attr for test override

    # -- RuntimePort interface --

    @property
    def approval_rule_matcher(self) -> RuntimeApprovalRuleMatcher:
        return self._approval_rule_matcher

    def set_session_rule_finder(
        self,
        finder: Callable[
            [str, str, dict[str, Any], dict[str, Any]],
            SessionRule | None,
        ]
        | None,
    ) -> None:
        """Injected by ``SessionOrchestrator._ensure_runtime`` so this
        runtime can consult the kernel approval cache before parking on
        a user decision. Set to ``None`` to disable (mainly for tests).
        """
        self._session_rule_finder = finder

    def update_sink(self, sink: EventSink) -> None:
        self.event_sink = sink

    def consume_turn_anchor(self) -> dict[str, Any] | None:
        """Return and clear the native anchor captured by the last ``run()``.

        Read-and-clear so a turn that never reaches ``turn/start`` (resume
        failure, egress error) cannot inherit a stale anchor from the
        previous message.
        """
        anchor, self._turn_anchor = self._turn_anchor, None
        return anchor

    async def prepare(self, session: Session) -> None:
        """Warm the Codex app-server and thread without dispatching a turn."""
        task = asyncio.current_task()
        turn_attempt_id = f"prepare-{uuid.uuid4().hex}"
        if task is not None:
            self._background_prepare_tasks.add(task)
        try:
            await self._prepare(
                session,
                turn_attempt_id=turn_attempt_id,
            )
        except BaseException:
            # A best-effort prepare has no user turn whose normal ``run``
            # finalizer could close the monitoring activity.
            await self._emit_turn_phase(
                "runtime_prepare_failed",
                egress_runtime_key=session.id,
                egress_turn_attempt_id=turn_attempt_id,
            )
            raise
        finally:
            if task is not None:
                self._background_prepare_tasks.discard(task)

    async def _prepare(self, session: Session, *, turn_attempt_id: str) -> None:
        async with self._prepare_lock:
            self._egress_runtime_key = session.id
            self._egress_turn_attempt_id = turn_attempt_id
            self._loop = asyncio.get_running_loop()
            was_ready = self._codex is not None and self._thread is not None
            self._materialize_skills(session)
            await self._ensure_codex(session)
            await self._ensure_thread(session)
            if not was_ready:
                await self._emit_turn_phase("runtime_ready")

    async def fork_session(
        self,
        session: Session,
        *,
        source_native_session_id: str,
        anchor: str | None = None,
    ) -> str:
        """Branch a source codex thread into this session's native thread.

        The third thread-birth verb beside start/resume (codex lifecycle:
        ``thread/start | thread/resume | thread/fork``). Reuses the
        ``_prepare`` front half (skills + app-server spawn), then calls
        ``thread/fork`` directly — NOT ``_ensure_thread``, which would
        start/resume. ``anchor`` is a turn id; ``lastTurnId`` is inclusive
        and codex 0.144.4 rejects an in-progress or unknown turn with
        ``-32600`` (NEVER send ``beforeTurnId`` — this binary silently
        ignores it and forks the whole thread). The fork reads the source
        rollout from disk, so the source thread need not be loaded
        anywhere and is never mutated. Leaves ``self._thread`` on the new
        thread — the runtime is warm and the first Send resumes it.
        """
        async with self._prepare_lock:
            self._loop = asyncio.get_running_loop()
            self._materialize_skills(session)
            await self._ensure_codex(session)
        assert self._codex is not None
        common = self._build_thread_kwargs(session)
        t0 = time.monotonic()
        forked = await self._codex._client.thread_fork(
            source_native_session_id,
            ThreadForkParams(
                thread_id=source_native_session_id,
                last_turn_id=anchor,
                **common,
            ),
        )
        self._thread = AsyncThread(self._codex, forked.thread.id)
        # Same internal backfill as thread_start — no API write channel.
        session.runtime_session_id = self._thread.id
        await self._emit_turn_phase(
            "thread_init", mode="fork", duration_ms=int((time.monotonic() - t0) * 1000)
        )
        return self._thread.id

    async def run(self, session: Session, user_message: UserMessage) -> None:
        from datetime import datetime

        from src.core.prompt_builder import build_user_prompt

        session.status = "running"
        self._cancelled = False
        self._interrupt_cancels = 0
        self._active_task = asyncio.current_task()
        turn_attempt_id = uuid.uuid4().hex

        try:
            await self._prepare(session, turn_attempt_id=turn_attempt_id)
            assert self._codex is not None
            assert self._thread is not None

            # ``/compact`` sent as a normal turn IS a real codex compaction:
            # the app-server reads the full context and summarizes it (the turn
            # reports the pre-compaction token count as ``input_tokens`` and
            # replies "Compacted."). We keep that real execution and only
            # re-label the output to match ClaudeAgentRuntime: suppress the
            # "Compacted." assistant text and emit a ``compaction`` event
            # instead, then let the real ``usage_update`` flow through. (Do NOT
            # call ``AsyncThread.compact()`` here — that only fires
            # ``thread/compact/start`` and returns before the work happens; see
            # ``_is_compact_command``.)
            is_compact = _is_compact_command(user_message)

            # Slice 6 follow-up of session-modes: user-initiated goal
            # exit. Codex's slice-6 listener catches the
            # ``thread/goal/cleared`` notification when the model
            # self-completes a goal, but a user-driven exit
            # (``POST /mode {default}`` while goal still running)
            # leaves codex's thread-goal set until natural completion.
            # Send an explicit ``thread/goal/clear`` JSON-RPC so the
            # goal stops immediately. The SDK has no typed wrapper for
            # this method (codex-goal-spike noted ``ThreadGoalClearParams``
            # / ``Response`` are absent); use ``AppServerClient.request``
            # — the documented generic JSON-RPC escape hatch.
            #
            # Conditional on ``self._applied_mode == "goal"`` so this
            # only fires on the FIRST turn after exit. Subsequent
            # turns see applied_mode already at "default" and
            # short-circuit. The reverse case (entry into goal) is
            # NOT handled here — it's covered by ``wrap_for_mode``
            # in the orchestrator (each non-slash message in goal
            # mode wraps to ``/goal <text>``).
            if self._applied_mode == "goal" and session.mode != "goal" and self._thread is not None:
                try:
                    # Codex's JSON-RPC wire format is camelCase
                    # (``threadId``, not snake_case ``thread_id``). The
                    # Python SDK's typed pydantic models alias both
                    # forms via ``Field(alias="threadId",
                    # populate_by_name=True)`` so user-facing kwargs
                    # accept either spelling — but we're bypassing that
                    # serialization layer here (no typed wrapper for
                    # ``thread/goal/clear`` exists in the vendored SDK).
                    # Send the raw camelCase shape codex expects;
                    # snake_case ``thread_id`` produces ``Invalid
                    # request: missing field threadId`` at the wire
                    # boundary.
                    await self._codex._client.request(
                        "thread/goal/clear",
                        {"threadId": self._thread.id},
                        response_model=_OpaqueDictResponse,
                    )
                except Exception:
                    # codex returns success on a no-active-goal clear,
                    # so failures here are likely transport-level. Log
                    # and proceed — the user's turn shouldn't fail just
                    # because the exit dispatch hiccupped, and the
                    # goal-cleared listener will catch a delayed
                    # codex-side completion.
                    logger.exception("codex: thread/goal/clear failed")

            prompt = build_user_prompt(
                user_message,
                cwd=self.workspace_root,
                now=datetime.now().astimezone(),
            )

            turn_kwargs = self._build_turn_kwargs(session)
            # Sync the live caches the sync approval handler reads from.
            # ``_cached_permission_mode`` is the only one that matters
            # at request time (the sync handler decides park-vs-bypass);
            # ``_applied_*`` are bookkeeping for the cross-runtime
            # "next-turn after PATCH" contract.
            self._cached_permission_mode = session.permission_mode
            self._applied_permission_mode = session.permission_mode
            self._applied_effort = (
                session.model_settings.effort if session.model_settings is not None else None
            )
            self._applied_mode = session.mode
            # Low-level turn-start for the same reason as _ensure_thread:
            # the ergonomic ``AsyncThread.turn`` only accepts the narrowed
            # ``approval_mode``. ``TurnStartParams`` keeps the full tri-axis.
            # ``turn_start`` takes the prompt as its own ``input_items``
            # arg (a plain string is accepted and normalized on the wire),
            # which overrides ``params.input``; the typed ``input`` here
            # just satisfies ``TurnStartParams``' required field.
            turn_input = UserInput(root=TextUserInput(type="text", text=prompt))
            t_dispatch = time.monotonic()
            await self._emit_turn_phase("dispatch_started")
            turn_resp = await self._codex._client.turn_start(
                self._thread.id,
                prompt,
                TurnStartParams(thread_id=self._thread.id, input=[turn_input], **turn_kwargs),
            )
            self._active_turn = AsyncTurnHandle(self._codex, self._thread.id, turn_resp.turn.id)
            # Overwrite (never reset at run() entry): if a later turn fails
            # before ``turn/start`` the previous consume already cleared the
            # anchor, and a coverage continuation legitimately replaces the
            # primary turn's anchor with its own — the Message maps to the
            # LAST codex turn it drove.
            self._turn_anchor = {
                "provider": "codex",
                "thread_id": self._thread.id,
                "turn_id": turn_resp.turn.id,
            }
            # Observability: the gap from this row to the first thinking/text
            # delta = codex's pre-sampling pipeline + model TTFT.
            await self._emit_turn_phase(
                "dispatch", duration_ms=int((time.monotonic() - t_dispatch) * 1000)
            )

            completed: TurnCompletedNotification | None = None
            error_message: str | None = None
            usage_tracker = _TurnUsageTracker()
            saw_compaction = False
            saw_model_event = False

            # SDK declares ``stream()`` as ``AsyncIterator`` but always
            # returns an async generator; cast so we can call ``aclose``.
            stream = cast(
                AsyncGenerator[Any, None],
                self._active_turn.stream(),
            )
            try:
                async for notification in stream:
                    if self._cancelled:
                        break

                    for event in map_notification(notification):
                        # On a ``/compact`` turn, swallow the model's
                        # "Compacted." text — it's re-surfaced as a single
                        # ``compaction`` event after the turn (parity with the
                        # Claude runtime, which emits no assistant bubble for
                        # ``/compact``).
                        if is_compact and event.type in ("text_delta", "assistant_message"):
                            continue
                        if event.type == "compaction":
                            saw_compaction = True
                        if not saw_model_event and event.type in {
                            "text_delta",
                            "thinking_delta",
                            "assistant_message",
                            "thinking",
                            "tool_use",
                        }:
                            saw_model_event = True
                            await record_runtime_egress_phase(
                                self._egress_runtime_key,
                                self._egress_turn_attempt_id,
                                "model_first_event",
                            )
                        await self.event_sink.emit(event)

                    mcp_status = extract_mcp_server_status(notification)
                    if mcp_status is not None:
                        # MCP startup failures used to vanish into the void
                        # because the SDK doesn't surface them through the
                        # turn-level error channel — log them at WARNING so
                        # users actually see why a configured MCP isn't
                        # available as a tool.
                        status_value = mcp_status.status.value
                        if status_value in ("failed", "cancelled"):
                            logger.warning(
                                "codex MCP server '%s' %s: %s",
                                mcp_status.name,
                                status_value,
                                mcp_status.error or "(no error message)",
                            )
                        else:
                            logger.info(
                                "codex MCP server '%s' status=%s",
                                mcp_status.name,
                                status_value,
                            )

                    err = extract_error(notification)
                    if err is not None:
                        error_message = err
                        continue

                    usage = extract_token_usage(notification)
                    if usage is not None:
                        # One notification per model request; a turn that
                        # calls the model more than once produces several,
                        # and all of them belong to this turn's increment.
                        usage_tracker.observe(usage.token_usage)

                    if extract_goal_cleared(notification) and session.mode == "goal":
                        # Slice 6 of session-modes: codex-core fires
                        # ``thread/goal/cleared`` when a goal completes
                        # (model self-reports via ``update_goal``, user
                        # sends ``/goal clear``, or the codex CLI ends it).
                        # No polling needed — flip the kernel field +
                        # emit ``mode_changed{by: "runtime"}`` so clients
                        # see the transition live. Conditional on
                        # ``session.mode == "goal"`` so a stale
                        # notification on a session that's already in
                        # default doesn't double-emit.
                        session.mode = "default"
                        # Keep the runtime-side tracker in sync so the
                        # next turn's reconcile (the user-initiated
                        # exit dispatch above) doesn't *also* fire a
                        # redundant `thread/goal/clear` — codex's
                        # auto-completion has already cleared the goal.
                        self._applied_mode = "default"
                        await self.event_sink.emit(
                            Event(
                                type="mode_changed",
                                data={"mode": "default", "by": "runtime"},
                            )
                        )

                    turn_done = extract_turn_completed(notification)
                    if turn_done is not None:
                        completed = turn_done
                        break
            finally:
                await stream.aclose()

            if self._cancelled:
                session.status = "idle"
                session.stop_reason = Error(
                    category="user_interrupt",
                    retry_status="terminal",
                    message="cancelled",
                )
            elif error_message is not None and completed is None:
                session.status = "idle"
                session.stop_reason = Error(
                    category="execution_error",
                    retry_status="exhausted",
                    message=error_message,
                )
                await self.event_sink.emit(
                    Event(type="session_error", data={"message": error_message})
                )
            elif completed is not None:
                session.status = "idle"
                session.stop_reason = _stop_reason_from_turn(completed)
                # Turn-level failures (codex reports them as a *completed*
                # turn with ``TurnStatus.failed``) must surface as a
                # ``session_error`` event like the other two error paths —
                # without it the failure lives only in ``stop_reason`` and
                # clients render a silent idle (no error card, nothing on
                # replay).
                await self._emit_session_error_for_stop(session.stop_reason)
            else:
                session.status = "idle"
                session.stop_reason = EndTurn()

            # ``/compact`` turn: re-surface the (suppressed) "Compacted." as a
            # ``compaction`` event before the usage update, matching the Claude
            # runtime's ordering (compaction -> usage_update). Codex's turn
            # carries no compaction metadata of its own (unlike Claude's
            # ``compact_metadata``), so the marker is empty — we don't
            # synthesize trigger/pre_tokens. The real token counts are already
            # in the ``usage_update`` that follows; the upper layer can read
            # them there if it wants. Skipped when the mapper already surfaced
            # a marker from codex's native ``contextCompaction`` item this
            # turn — this synthetic one is only the fallback for binaries
            # that don't emit that item.
            if is_compact and completed is not None and not saw_compaction:
                await self.event_sink.emit(Event(type="compaction", data={}))

            # A turn that reached ``turn/completed`` reports its spend even
            # when no usage notification arrived (an all-local turn) — an
            # explicit zero, so the message row records "this turn cost
            # nothing" rather than leaving the field empty.
            turn_totals = usage_tracker.totals()
            if turn_totals is not None:
                await self.event_sink.emit(
                    Event(
                        type="usage_update",
                        data=_usage_payload_from_turn_totals(turn_totals, self.model),
                    )
                )
            elif completed is not None:
                await self.event_sink.emit(
                    Event(type="usage_update", data=_empty_usage_payload(self.model))
                )

        except asyncio.CancelledError:
            session.status = "idle"
            session.stop_reason = Error(
                category="user_interrupt",
                retry_status="terminal",
                message="cancelled",
            )
            absorb_interrupt_cancellations(self._interrupt_cancels)
            self._interrupt_cancels = 0
        except Exception as exc:
            session.status = "idle"
            if self._cancelled:
                session.stop_reason = Error(
                    category="user_interrupt",
                    retry_status="terminal",
                    message="cancelled",
                )
            elif is_runtime_interruption(exc):
                # The codex subprocess went away mid-turn ("closed stdout" /
                # broken pipe). Usually a graceful host stop tearing it down —
                # NOT a task failure — so leave it resumable (``interrupted``)
                # for boot recovery to re-drive (the same outcome a hard kill
                # gets via ``scan_orphan_runs``) and suppress the scary
                # session_error. BUT the same exception shape also covers a
                # spontaneous crash (upstream stream drop, OOM, panic, bad
                # config): ``TransportClosedError`` carries the codex
                # ``stderr_tail`` and a wrapped group hides the real leaf. This
                # layer can't see the host drain flag (that lives in
                # ``valuz_agent.modules.tasks``), so we can't reclassify — but we
                # MUST NOT swallow the cause: log it and thread it into the
                # stop_reason so an operator can tell a clean shutdown from a
                # crash instead of staring at a bare "runtime process
                # interrupted". Keeps category/recovery untouched (they key on
                # ``category``, not the message — see recovery.py).
                cause = describe_exception(exc)
                logger.warning(
                    "codex: runtime process interrupted mid-turn for session %s: %s",
                    session.id,
                    cause,
                )
                session.stop_reason = Error(
                    category="interrupted",
                    retry_status="terminal",
                    message=f"runtime process interrupted: {cause}",
                )
            else:
                # See ``describe_exception``: a wrapped ``ExceptionGroup`` would
                # otherwise reach the user as the opaque "unhandled errors in a
                # TaskGroup" — unwrap to the leaf and log the traceback (this
                # branch previously logged nothing).
                cause = describe_exception(exc)
                logger.exception("codex: turn failed for session %s: %s", session.id, cause)
                session.stop_reason = Error(
                    category="execution_error",
                    retry_status="exhausted",
                    message=cause,
                )
                await self.event_sink.emit(Event(type="session_error", data={"message": cause}))
                if self.config.hooks:
                    await self.config.hooks.fire("on_error", error=exc, session_id=session.id)
        finally:
            self._active_turn = None
            self._active_task = None
            await self.event_sink.emit(
                Event(
                    type="session_idle",
                    data={
                        "stop_reason": _stop_reason_to_dict(session.stop_reason),
                        "num_turns": 1,
                    },
                )
            )
            await record_runtime_egress_phase(
                self._egress_runtime_key,
                self._egress_turn_attempt_id,
                (
                    "interrupted"
                    if getattr(session.stop_reason, "category", None)
                    in {"user_interrupt", "interrupted"}
                    else "turn_complete"
                ),
            )

    async def run_task_coverage(
        self,
        session: Session,
        user_message: UserMessage,
        *,
        no_op_tool: ToolDef,
    ) -> None:
        """Resume the same Codex thread with a turn-scoped private tool."""

        previous = self.toolkit.get(no_op_tool.name)
        self.toolkit.register(no_op_tool)
        # Codex discovers harness MCP tools at process startup.  Reconnect the
        # SDK client, then resume the persisted native thread id.
        await self.close()
        try:
            await self.run(session, user_message)
        finally:
            if previous is None:
                self.toolkit.unregister(no_op_tool.name)
            else:
                self.toolkit.register(previous)
            await self.close()

    async def submit_action(
        self,
        pending_id: str,
        decision: Literal["approve", "approve_with_changes", "reject", "answer"],
        message: str | None = None,
        answers: dict[str, str | list[str]] | None = None,
        modified_input: dict[str, Any] | None = None,
    ) -> None:
        """Resolve the approval future the sync handler is parked on.

        A missing or already-done future means a true race (e.g. timeout fired
        then user clicked); we return silently — the orchestrator owns the
        idempotency / conflict check on its side.

        ``decision="answer"`` is reserved for ``clarifying_questions``
        pendings (Claude SDK ``AskUserQuestion``). Codex doesn't emit
        that subject — the orchestrator's subject↔decision invariant
        rejects mismatches at 400 before reaching us — so receiving it
        here means a contract violation upstream. Raise
        ``NotImplementedError`` defensively; the orchestrator translates
        it to a 501 ``ApprovalNotImplementedError``.

        ``decision="approve_with_changes"`` is similarly out-of-band for
        codex: codex's approval-response wire shapes
        (``{"decision": "accept"}`` / ``{"action": "accept"}``) carry no
        ``updated_input`` analog, so codex sessions never advertise the
        verb in ``available_decisions`` and the orchestrator 400s
        before we get here. Raise defensively for the same reason.
        """
        if decision == "answer":
            raise NotImplementedError(
                "CodexRuntime does not emit 'clarifying_questions' subjects; "
                "decision='answer' is claude_agent-only in v1."
            )
        if decision == "approve_with_changes":
            raise NotImplementedError(
                "CodexRuntime does not advertise 'approve_with_changes'; "
                "codex's approval-response wire shape has no updated_input field."
            )
        # ``answers`` / ``modified_input`` are forbidden by the
        # SubmitActionRequest validator for the verbs codex does handle
        # (approve / reject); reaching here with either set means the
        # validator was bypassed. Ignore (don't raise) for forward
        # compat — silent drop is safer than crashing the runtime.
        _ = answers
        _ = modified_input
        future = self._pending_futures.get(pending_id)
        if future is None or future.done():
            return
        future.set_result((decision, message))

    async def interrupt(self) -> None:
        self._cancelled = True
        # Seal pending approvals: cheap ``set_result`` first so the SDK
        # callback unblocks immediately even if the sink chain hangs
        # (e.g. DB locked); the ``action_resolved`` event still flushes
        # to the WS bus + DB, just second. Reverse order would risk
        # leaving the SDK blocked on a parked future indefinitely if
        # ``event_sink.emit`` ever stalls.
        for pending_id, future in list(self._pending_futures.items()):
            if future.done():
                continue
            future.set_result(("reject", "session interrupted"))
            await self._emit_synthetic_resolved(pending_id, "interrupted")
        self._pending_futures.clear()
        turn = self._active_turn
        if turn is not None:
            try:
                await turn.interrupt()
            except Exception:
                logger.debug("Codex turn interrupt failed", exc_info=True)
        task = self._active_task
        if task is not None and not task.done():
            task.cancel()
            self._interrupt_cancels += 1
        for prepare_task in tuple(self._background_prepare_tasks):
            if not prepare_task.done():
                prepare_task.cancel()

    async def close(self) -> None:
        current = asyncio.current_task()
        prepare_tasks = [
            task
            for task in self._background_prepare_tasks
            if task is not current and not task.done()
        ]
        for task in prepare_tasks:
            task.cancel()
        if prepare_tasks:
            await asyncio.gather(*prepare_tasks, return_exceptions=True)
        self._active_turn = None
        self._active_task = None
        self._thread = None
        # Drop bridge state so a stale post-close approval callback
        # (e.g. delayed flush from the SDK's drain thread) lands on the
        # ``_loop is None`` branch in ``_approval_handler`` and
        # auto-rejects instead of trying to post to a torn-down loop.
        # ``interrupt()`` already drains pending futures on the explicit
        # cancel path — this is the cleanup mirror for graceful close.
        self._loop = None
        self._pending_futures.clear()
        if self._codex is not None:
            try:
                await self._codex.close()
            except Exception:
                logger.debug("Error closing AsyncCodex", exc_info=True)
            self._codex = None
        if self._registered_session_id is not None:
            from src.core.mcp_bridge import unregister_session_toolkit

            unregister_session_toolkit(self._registered_session_id)
            self._registered_session_id = None

    # -- Lifecycle helpers --

    async def _emit_turn_phase(
        self,
        phase: str,
        *,
        egress_runtime_key: str | None = None,
        egress_turn_attempt_id: str | None = None,
        **fields: Any,
    ) -> None:
        """Persisted latency marker — see ``turn_phase`` in ``events.py``."""
        await self.event_sink.emit(Event(type="turn_phase", data={"phase": phase, **fields}))
        await record_runtime_egress_phase(
            egress_runtime_key
            if egress_runtime_key is not None
            else getattr(self, "_egress_runtime_key", None),
            egress_turn_attempt_id
            if egress_turn_attempt_id is not None
            else getattr(self, "_egress_turn_attempt_id", None),
            phase,
        )

    async def _ensure_codex(self, session: Session) -> None:
        if self._codex is not None:
            return
        t0 = time.monotonic()
        await self._emit_turn_phase("runtime_init_started")
        expose_toolkit = self._register_toolkit_if_eligible(session)
        codex_bin = _resolve_codex_bin()
        if codex_bin is None:
            raise RuntimeError("Codex runtime binary is unavailable")
        overrides = _build_config_overrides(
            session,
            self.model_provider,
            self.model,
            expose_toolkit=expose_toolkit,
            egress_base_url=(
                self.egress_descriptor.base_url if self.egress_descriptor is not None else None
            ),
        )
        safe_overrides, mcp_secret_env = _externalize_mcp_secrets(session, overrides)
        cfg = CodexConfig(
            codex_bin=codex_bin,
            config_overrides=safe_overrides,
            env=_build_codex_env(
                self.model_provider,
                egress_base_url=(
                    self.egress_descriptor.base_url if self.egress_descriptor is not None else None
                ),
                mcp_secret_env=mcp_secret_env,
            ),
        )
        self._codex = AsyncCodex(config=cfg)
        try:
            await self._codex.__aenter__()
        except BaseException:
            try:
                await self._codex.close()
            except Exception:
                logger.debug("Error closing failed Codex startup", exc_info=True)
            self._codex = None
            raise
        self._install_approval_handler()
        await self._emit_turn_phase("runtime_init", duration_ms=int((time.monotonic() - t0) * 1000))

    def _install_approval_handler(self) -> None:
        """Monkey-patch the sync client's approval handler.

        The ``AsyncAppServerClient`` ctor does not expose ``approval_handler``
        as a kwarg, so we reach in after construction. Drift detection: if
        the SDK changes the internal path we want a loud failure rather than
        silent fall-back to default-accept (which would incorrectly
        auto-approve every tool call in ``default`` mode).
        """
        assert self._codex is not None
        try:
            sync_client = self._codex._client._sync
        except AttributeError as exc:
            raise RuntimeError(
                "codex SDK shape changed: AsyncCodex._client._sync no longer "
                "exists. Approval handler cannot be installed; refusing to run "
                "rather than silently accepting every tool call."
            ) from exc
        sync_client._approval_handler = self._approval_handler

    async def _emit_session_error_for_stop(self, stop_reason: StopReason | None) -> None:
        """Emit ``session_error`` for an execution-error stop reason.

        Companion to the stream-error and runtime-exception paths, which
        emit inline — this covers turns codex reports as *completed* with
        ``TurnStatus.failed``. No-op for clean/interrupt/budget stops.
        """
        if isinstance(stop_reason, Error) and stop_reason.category == "execution_error":
            await self.event_sink.emit(
                Event(
                    type="session_error",
                    data={
                        "category": "execution_error",
                        "message": stop_reason.message,
                    },
                )
            )

    def _register_toolkit_if_eligible(self, session: Session) -> bool:
        """Register the session toolkit on the MCP router.

        Returns ``True`` when the harness MCP server should be advertised to
        codex (the toolkit has at least one callable tool). Returns
        ``False`` when the toolkit is empty or contains only declarations
        — in that case codex never sees the harness MCP server at all,
        which keeps the surface small.
        """
        if self.toolkit is None:
            return False
        callable_tools = [
            t for t in self.toolkit.list_tools() if t.handler is not None and t.permission != "deny"
        ]
        if not callable_tools:
            return False

        from src.core.mcp_bridge import register_session_toolkit

        register_session_toolkit(
            session.id,
            self.toolkit,
            ExecContext(
                workspace=self.workspace_root,
                session_id=session.id,
            ),
        )
        self._registered_session_id = session.id
        return True

    async def _ensure_thread(self, session: Session) -> None:
        if self._thread is not None:
            return
        assert self._codex is not None
        # Low-level path: the ergonomic ``AsyncCodex.thread_start`` /
        # ``thread_resume`` narrowed their approval surface to a 2-value
        # ``approval_mode`` enum (openai-codex 0.131), which cannot express
        # the harness's ``default`` host-approval mode (on_request +
        # reviewer=user). The wire ``ThreadStartParams`` /
        # ``ThreadResumeParams`` still carry the full tri-axis, so we build
        # them directly and call the low-level client, then wrap the result
        # back into the ergonomic ``AsyncThread`` — the stream / interrupt
        # consumption path is unchanged.
        common = self._build_thread_kwargs(session)
        t0 = time.monotonic()
        await self._emit_turn_phase("thread_init_started")
        if session.runtime_session_id:
            # NB: ``ThreadResumeParams.model`` is documented as
            # "Configuration overrides for the resumed thread, if any."
            # When omitted, codex resolves to the model pinned at
            # ``thread_start`` time (stored in codex's own thread
            # metadata, not this Session row). If the ambient codex
            # config later loses that model, resumes surface a
            # ``Missed model deployment`` error — recreate the session
            # to repin.
            resumed = await self._codex._client.thread_resume(
                session.runtime_session_id,
                ThreadResumeParams(thread_id=session.runtime_session_id, **common),
            )
            self._thread = AsyncThread(self._codex, resumed.thread.id)
            await self._emit_turn_phase(
                "thread_init", mode="resume", duration_ms=int((time.monotonic() - t0) * 1000)
            )
            return
        started = await self._codex._client.thread_start(ThreadStartParams(**common))
        self._thread = AsyncThread(self._codex, started.thread.id)
        # Persist the freshly-allocated thread id for future resumes.
        session.runtime_session_id = self._thread.id
        await self._emit_turn_phase(
            "thread_init", mode="start", duration_ms=int((time.monotonic() - t0) * 1000)
        )

    def _build_thread_kwargs(self, session: Session) -> dict[str, Any]:
        # Cache permission_mode so the sync approval handler (which does
        # not receive session) can read it without a lock.
        self._cached_permission_mode = session.permission_mode
        self._applied_permission_mode = session.permission_mode

        kwargs: dict[str, Any] = dict(self._permission_mode_to_kwargs(session.permission_mode))
        # ``sandbox_policy`` is per-turn-only; the legacy ``sandbox`` enum
        # is what thread_start / thread_resume accept. The helper returns
        # the legacy key for thread layer, callers at the turn layer
        # translate to ``sandbox_policy``.

        # Mirror ClaudeAgentRuntime._build_options: only forward ``model``
        # when the session actually carries one. Passing an empty string
        # makes the codex SDK try to resolve a deployment named "" — which
        # is exactly the failure mode users hit when running with no
        # per-session model + an Azure-style ``~/.codex/config.toml`` that
        # maps model names to deployment ids ("Missed model deployment"
        # error). Omitting the kwarg lets codex pick its own default the
        # same way the bare ``codex`` CLI does.
        if self.model:
            kwargs["model"] = self.model
        if self.workspace_root:
            kwargs["cwd"] = self.workspace_root
        if session.instructions:
            kwargs["developer_instructions"] = session.instructions
        return kwargs

    @staticmethod
    def _permission_mode_to_kwargs(
        mode: Literal["default", "auto_review", "full_access"],
    ) -> dict[str, Any]:
        """Translate the 3-preset harness ``permission_mode`` into the
        codex SDK's tri-axis ``approval_policy`` / ``sandbox`` /
        ``approvals_reviewer`` triplet. Single source of truth for the
        mapping — both ``_build_thread_kwargs`` (thread seed) and
        ``_build_turn_kwargs`` (per-turn live override) consume it so
        the two layers can't drift.

        Returns the LEGACY ``sandbox`` enum (the only sandbox channel
        ``ThreadStartParams`` / ``ThreadResumeParams`` accept). Per-turn
        callers translate the result to ``sandbox_policy`` (the rich
        union ``TurnStartParams`` accepts) — see ``_build_turn_kwargs``.
        """
        if mode == "full_access":
            return {
                "approval_policy": AskForApproval(root=AskForApprovalValue.never),
                # ``danger-full-access`` is required for MCP tool calls to
                # auto-approve under ``approval_policy=never``: codex's
                # ``mcp_permission_prompt_is_auto_approved`` only
                # short-circuits to ``approve`` when the permission profile
                # has full disk write access. With ``workspace-write`` MCP
                # calls silently come back as "user rejected MCP tool call".
                "sandbox": SandboxMode.danger_full_access,
            }
        # ``default`` and ``auto_review`` both use on_request +
        # workspace_write. The difference is the reviewer:
        #   - ``user``        → server-requests routed through our
        #                       _approval_handler sync callback.
        #   - ``auto_review`` → codex's guardian decides internally;
        #                       host only sees notification pairs
        #                       (item/autoApprovalReview/started +
        #                        item/autoApprovalReview/completed).
        return {
            "approval_policy": AskForApproval(root=AskForApprovalValue.on_request),
            "sandbox": SandboxMode.workspace_write,
            "approvals_reviewer": (
                ApprovalsReviewer.auto_review if mode == "auto_review" else ApprovalsReviewer.user
            ),
        }

    @staticmethod
    def _sandbox_mode_to_policy(mode: SandboxMode) -> Any:
        """Map ``ThreadStartParams.sandbox`` (legacy enum) to
        ``TurnStartParams.sandbox_policy`` (rich union variant). The
        per-turn override channel only accepts the rich form — see
        ``docs/references/codex-thread-vs-turn-kwargs.md`` Layer 2."""
        from openai_codex.generated.v2_all import (
            DangerFullAccessSandboxPolicy,
            ReadOnlySandboxPolicy,
            SandboxPolicy,
            WorkspaceWriteSandboxPolicy,
        )

        if mode == SandboxMode.danger_full_access:
            inner: Any = DangerFullAccessSandboxPolicy(type="dangerFullAccess")
        elif mode == SandboxMode.read_only:
            inner = ReadOnlySandboxPolicy(type="readOnly")
        else:
            inner = WorkspaceWriteSandboxPolicy(type="workspaceWrite")
        return SandboxPolicy(root=inner)

    # -- Approval bridge --

    def _approval_handler(
        self,
        method: str,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Sync handler invoked from the SDK's stdio reader thread.

        Posts an async coroutine onto the runtime loop to emit
        ``requires_action`` + park on a future, then blocks the calling
        thread until the host decision lands.

        Wire format depends on ``method``:
          - ``item/{commandExecution,fileChange}/requestApproval`` →
            ``{"decision": "accept" | "deny"}``. The ``deny`` value is
            undocumented in codex's approval protocol — best guess based
            on the ``denied`` enum in ``GuardianApprovalReviewStatus`` /
            ``deny`` in ``NetworkDomainPermission``.
          - ``mcpServer/elicitation/request`` → MCP elicitation envelope
            ``{"action": "accept" | "decline" | "cancel", "content"?: {...}}``
            (per ``mcp.types.ElicitResult``). Codex relays this method
            from MCP servers without translating the response shape, so
            sending ``{"decision": ...}`` is interpreted as malformed
            and the model sees "user rejected MCP tool call".
        """
        params = params or {}
        # ``full_access`` never gets here (approval_policy=never).
        # ``auto_review`` is host-bypassed by ApprovalsReviewer.auto_review
        # (codex decides internally via guardian notifications). For both,
        # auto-accept matches the policy contract — codex would have
        # already accepted internally; we just shouldn't fight it.
        if self._cached_permission_mode != "default":
            return _build_approval_response(method, "approve", params)
        if self._loop is None or self._loop.is_closed():
            # Either ``run()`` hasn't captured the loop yet (race on a
            # pre-run callback) or ``close()`` has cleared it. In either
            # case we cannot reach the host, and ``default`` mode means
            # the host *must* decide — so fail closed (auto-reject)
            # rather than silently auto-execute the tool call.
            logger.warning(
                "codex approval handler invoked with no live loop "
                "(method=%s); auto-rejecting in default mode",
                method,
            )
            return _build_approval_response(method, "reject", params)
        coro = self._await_host_decision_coro(method, params)
        # ``cf.result()`` raises ``concurrent.futures`` exceptions —
        # ``CancelledError`` / ``TimeoutError`` / generic — all
        # ``Exception``-derived, so a bare ``except Exception`` would
        # cover the cancel-from-``interrupt()`` path. The
        # ``BaseException`` net is purely defensive: ``asyncio.CancelledError``
        # is ``BaseException``-derived since Python 3.8 and we don't
        # want a future asyncio plumbing change to leak it through into
        # the SDK's stdio reader thread and crash the JSON-RPC loop.
        # ``KeyboardInterrupt`` / ``SystemExit`` are re-raised so
        # process shutdown still works.
        try:
            # run_coroutine_threadsafe returns concurrent.futures.Future;
            # blocking .result() is what we need on this thread. Add a
            # small buffer above the asyncio timeout so the inner path
            # always wins (synthetic action_resolved emitted there).
            cf = asyncio.run_coroutine_threadsafe(coro, self._loop)
            decision, message = cf.result(timeout=self.APPROVAL_TIMEOUT_SECONDS + 30.0)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            logger.exception("codex approval handler crashed; auto-rejecting")
            return _build_approval_response(method, "reject", params)
        # Codex's approval-response wire shapes ({"decision": "deny"}
        # / {"action": "decline"}) carry no ``reason`` field, so a user-supplied
        # reject ``message`` cannot reach the model — Claude SDK + DeepAgents
        # do deliver it, see docs/design/cross-runtime-approval-contract.md §10
        # "Reject-with-redirect". Log at INFO so host operators / on-call still
        # see the reason in app logs even though the agent only sees a generic
        # "user rejected MCP tool call". If codex's approval protocol gains a
        # reason field, thread ``message`` into ``_build_approval_response``
        # and drop the log.
        if decision == "reject" and message:
            logger.info(
                "codex reject (method=%s) carried host-side reason=%r "
                "(not forwarded to model — codex approval protocol has no "
                "reason field)",
                method,
                message,
            )
        return _build_approval_response(method, decision, params)

    async def _await_host_decision_coro(
        self,
        method: str,
        params: dict[str, Any],
    ) -> tuple[Literal["approve", "reject"], str | None]:
        """Async counterpart to the sync handler — runs on the runtime loop.

        Builds the pending payload + ``session_rule_preview`` from the
        matcher, emits ``requires_action`` (always, for audit-trail
        uniformity), then either:

        * Cache hit — emits ``action_resolved(decision="auto_approved",
          auto_resolved_by_rule_id=...)`` and returns ``("approve",
          None)`` immediately without parking. The codex SDK gets a
          plain ``{"decision": "accept"}`` on the wire — never
          ``AcceptForSession``, per spec §5.2 (kernel cache stays the
          single source of truth so the event-flow contract is
          uniform across runtimes).
        * Cache miss — parks on a future. On timeout emits a synthetic
          ``action_resolved(decision="expired")`` before returning.
        """
        pending_id = str(uuid.uuid4())
        subject = _classify_codex_subject(method)
        payload = _build_codex_pending_payload(subject, method, params, self.workspace_root)

        # Derive the rule preview the user would commit if they pick
        # ``approve_for_session``. ``_extract_matcher_inputs`` reduces
        # JSON-RPC params to the per-subject identity key — see the
        # docstring there and ``rule_canonicalize.py`` for the table.
        # MCP tool-call keys explicitly drop the ``input`` payload so
        # repeat calls to the same tool with different arguments share
        # one rule (spec §5; matches codex's native
        # ``McpToolApprovalKey``).
        rule_tool_name, rule_args = _extract_matcher_inputs(subject, method, params)
        runtime_extras: dict[str, Any] = {}
        derivation = self._approval_rule_matcher.derive_rule(
            subject, rule_tool_name, rule_args, runtime_extras
        )
        # The exact-args matcher's default display is generic; override
        # with the subject-aware reducer label so MCP rules read as
        # "any X call", file rules as "apply_patch on /path", etc.
        # Codex uses the exact matcher exclusively (no SDK pattern
        # grammar like Claude's), so the override is unconditional.
        _, subject_display = reduce_args_for_subject(subject, rule_tool_name, rule_args)
        session_rule_preview = {
            "kind": derivation.kind,
            "runtime_kind": derivation.runtime_kind,
            "display": subject_display,
            "rule_data": derivation.rule_data,
        }

        cache_hit = self._check_session_rule(subject, rule_tool_name, rule_args, runtime_extras)

        await self.event_sink.emit(
            Event(
                type="requires_action",
                data={
                    "pending_id": pending_id,
                    "subject": subject,
                    "runtime_provider": "codex",
                    "available_decisions": list(AVAILABLE_DECISIONS_V1_WITH_SESSION),
                    "payload": payload,
                    "session_rule_preview": session_rule_preview,
                },
            )
        )

        if cache_hit is not None:
            # Cache hit: synthetic auto-resolve. Bypass the orchestrator's
            # submit_action path entirely; the runtime emits the
            # action_resolved itself so bus + DB stay in sync. No
            # future to park — return immediately with a plain approve.
            await self.event_sink.emit(
                Event(
                    type="action_resolved",
                    data={
                        "pending_id": pending_id,
                        "decision": "auto_approved",
                        "auto_resolved_by_rule_id": cache_hit.rule_id,
                        "resolved_by": "system",
                    },
                )
            )
            return ("approve", None)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[Literal["approve", "reject"], str | None]] = (
            loop.create_future()
        )
        self._pending_futures[pending_id] = future

        try:
            decision, message = await asyncio.wait_for(
                future, timeout=self.APPROVAL_TIMEOUT_SECONDS
            )
        except TimeoutError:
            await self._emit_synthetic_resolved(pending_id, "expired")
            return ("reject", "approval timed out")
        finally:
            self._pending_futures.pop(pending_id, None)

        return (decision, message)

    def _check_session_rule(
        self,
        subject: str,
        tool_name: str,
        args: dict[str, Any],
        runtime_extras: dict[str, Any],
    ) -> SessionRule | None:
        """Consult the kernel session-rule cache via the injected finder.

        Returns ``None`` when no finder is wired (factory unit tests),
        when the finder raises (logged and treated as miss — never
        block the approval flow on a cache failure), or when no stored
        rule matches.
        """
        finder = self._session_rule_finder
        if finder is None:
            return None
        try:
            return finder(subject, tool_name, args, runtime_extras)
        except Exception:
            logger.exception(
                "codex: session rule check failed for %s; treating as miss",
                tool_name,
            )
            return None

    async def _emit_synthetic_resolved(self, pending_id: str, decision: str) -> None:
        """Used for runtime-side resolutions (timeout / interrupt) where
        the orchestrator isn't involved. Writes to DB + bus via the sink
        chain so the events log stays consistent for the next
        ``_derive_pending`` lookup.

        ``message`` is always ``None`` for synthetic resolutions —
        mirrors the orchestrator's user-resolved emit shape for schema
        consistency (analytics consumers can rely on the key always
        being present).
        """
        try:
            await self.event_sink.emit(
                Event(
                    type="action_resolved",
                    data={
                        "pending_id": pending_id,
                        "decision": decision,
                        "message": None,
                        "resolved_by": "system",
                    },
                )
            )
        except Exception:
            # Sink failures (DB locked, bus down) shouldn't bubble out —
            # the future has either already been sealed (interrupt path)
            # or is about to be (timeout path). Log so the gap is
            # visible in operations rather than silently lost.
            logger.exception(
                "codex: failed to emit synthetic action_resolved for %s",
                pending_id,
            )

    def _build_turn_kwargs(self, session: Session) -> dict[str, Any]:
        # Per-turn overrides drive the "PATCH applies on next turn after
        # Send" contract — see ``docs/references/codex-thread-vs-turn-kwargs.md``
        # Layer 2 + ``claude-agent-options-and-mutators.md`` cross-runtime
        # map. The runtime reads ``session`` live each turn (NOT from
        # the cached ``self.model_settings`` / ``self.permission_mode``)
        # so a PATCH that hits the store between turns takes effect
        # immediately on the next ``AsyncThread.turn(...)``.
        #
        # Both ``effort`` and the approval triplet
        # (``approval_policy`` / ``approvals_reviewer`` / ``sandbox_policy``)
        # MUST live here, not in ``_build_thread_kwargs``: codex pins
        # those into thread metadata at ``thread_start``, and
        # ``ThreadResumeParams`` does not accept ``effort`` /
        # ``sandbox_policy`` at all — so the thread-layer copies would
        # silently stop applying after the first turn. The thread-layer
        # call still happens (it seeds the first turn before any per-
        # turn override could fire), but the per-turn override
        # supersedes it forever after.
        kwargs: dict[str, Any] = {}

        # -- effort --
        effort = session.model_settings.effort if session.model_settings is not None else None
        if effort is not None:
            from openai_codex.generated.v2_all import ReasoningEffort

            kwargs["effort"] = ReasoningEffort(_map_effort_for_codex(effort))

        # -- permission_mode -> approval triplet --
        mode = session.permission_mode
        mapped = self._permission_mode_to_kwargs(mode)
        kwargs["approval_policy"] = mapped["approval_policy"]
        if "approvals_reviewer" in mapped:
            kwargs["approvals_reviewer"] = mapped["approvals_reviewer"]
        # ``TurnStartParams`` exposes the rich ``sandbox_policy`` union,
        # not the legacy ``sandbox`` enum. Translate the helper's enum
        # output to the matching policy variant.
        kwargs["sandbox_policy"] = self._sandbox_mode_to_policy(mapped["sandbox"])

        return kwargs

    def _materialize_skills(self, session: Session) -> None:
        if not self.workspace_root or not session.skills:
            return
        from src.runtimes.skills_materialize import prepare_codex_skills

        prepare_codex_skills(self.workspace_root, list(session.skills))


def _is_compact_command(user_message: UserMessage) -> bool:
    """True iff the user turn is the bare ``/compact`` slash command.

    Codex has no native ``/compact`` interception in its turn input, so the
    runtime detects it here and runs it as an ordinary turn: the app-server
    reads the full context and summarizes it (replying "Compacted."). The
    caller suppresses that reply and re-labels it as a ``compaction`` event —
    parity with the Claude runtime. We deliberately do NOT route to
    ``AsyncThread.compact()``: that fire-and-ack only starts the work and
    returns before it happens, with no observable completion.
    """
    return (user_message.text or "").strip() == "/compact"


def _resolve_codex_bin() -> str | None:
    """Return the codex binary path, or ``None`` to let the SDK resolve it.

    Resolution order:
      1. ``CODEX_BIN_OVERRIDE`` env var (desktop bundle / CI escape hatch)
      2. The bundled ``openai-codex-cli-bin`` binary that the published
         ``openai-codex`` SDK installs automatically — a per-platform,
         version-pinned codex runtime. This is the primary path: it is locked
         to the SDK build, so a stray ``codex`` on PATH can't drift the
         version out from under us.
      3. ``shutil.which("codex")`` (npm-installed CLI on dev machines without
         the bundled package)
      4. ``None`` -> let the SDK resolve it (it performs the same bundled lookup)
    """
    override = os.getenv(CODEX_BIN_OVERRIDE_ENV)
    if override:
        return override
    bundled = _bundled_codex_bin()
    if bundled:
        return bundled
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    return None


def _bundled_codex_bin() -> str | None:
    """Path to the ``openai-codex-cli-bin`` bundled codex binary, if present.

    Mirrors the SDK's own ``codex_cli_bin.bundled_codex_path()`` lookup so the
    harness prefers the exact binary the installed SDK is pinned against.
    Returns ``None`` if the runtime package is absent (e.g. an editable SDK
    checkout) or the resolved path doesn't exist, letting the caller fall back.
    """
    try:
        from codex_cli_bin import bundled_codex_path
    except ImportError:
        return None
    path = bundled_codex_path()
    return str(path) if path.exists() else None


_HARNESS_PROVIDER_NAME = "harness"
_HARNESS_PROVIDER_ENV_KEY = "HARNESS_CODEX_PROVIDER_API_KEY"

# System-level default for codex's ``model_reasoning_summary`` config
# key (TOML enum: ``auto`` / ``concise`` / ``detailed`` / ``none``).
# The codex CLI's hidden default is effectively ``none`` — no reasoning
# summary stream is emitted, which is why the harness saw no
# ``thinking`` / ``thinking_delta`` events from reasoning models out of
# the box. ``auto`` lets codex pick a sensible detail level per model,
# is ignored by non-reasoning models, and is the value the OpenAI
# Responses API documentation recommends. Not user-configurable — this
# is the harness's "show reasoning summaries by default" stance.
_CODEX_REASONING_SUMMARY_DEFAULT = "auto"
_CODEX_MCP_SECRET_ENV_PREFIX = "VALUZ_CODEX_MCP_SECRET_"
_CODEX_OPENAI_API_KEY = "OPENAI_API_KEY"
_CODEX_SHELL_APPLY_SECRET_FILTERS = "shell_environment_policy.ignore_default_excludes=false"
_CODEX_SHELL_CORE_INHERIT = 'shell_environment_policy.inherit="core"'
_CODEX_DISABLE_LOGIN_SHELL = "allow_login_shell=false"
_CODEX_MCP_UNSAFE_PARENT_ENV_NAMES = frozenset(
    {
        "ALL_PROXY",
        "AZURE_OPENAI_API_KEY",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "HARNESS_CODEX_PROVIDER_API_KEY",
        "NO_PROXY",
        "NODE_OPTIONS",
        "OPENAI_API_KEY",
        "PATH",
        "PATHEXT",
        "PYTHONPATH",
        "SHELL",
        "SYSTEMROOT",
        "USERPROFILE",
    }
)


def _externalize_mcp_secrets(
    session: Session,
    overrides: tuple[str, ...],
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Replace MCP secret literals with Codex environment references.

    Codex app-server does not accept ``--profile`` (verified against
    codex-cli 0.144.x), while putting the original ``http_headers`` / ``env``
    entries in repeated ``--config`` flags exposes their values in the OS
    process table. Codex officially supports ``env_http_headers`` for remote
    MCP headers and ``env_vars`` for stdio MCP variables, so use those paths
    and carry the values only in the child environment.

    Stdio ``env_vars`` preserves the variable's declared name. Two servers
    cannot therefore request different values for the same name through one
    Codex parent process; fail closed instead of silently giving one server the
    other's credential.
    """

    remove: set[str] = set()
    add: list[str] = []
    secret_env: dict[str, str] = {}
    # secret_env key -> redacted human origin ("http server 'x' header 'y'"),
    # for the residue diagnostics below. Never carries values.
    origin: dict[str, str] = {}
    stdio_secret_values: dict[str, tuple[str, str]] = {}
    nonce = uuid.uuid4().hex
    inherited_stdio_names = {
        name.upper()
        for cfg in session.mcp_servers
        if isinstance(cfg, McpStdioServerConfig)
        for name in cfg.env_vars
    }

    for server_index, cfg in enumerate(session.mcp_servers):
        if isinstance(cfg, McpStdioServerConfig):
            env_vars = list(cfg.env_vars)
            if cfg.env_vars:
                remove.add(f"mcp_servers.{cfg.name}.env_vars={_toml_array(cfg.env_vars)}")
            for key, value in cfg.env.items():
                upper_key = key.upper()
                if (
                    upper_key in _CODEX_MCP_UNSAFE_PARENT_ENV_NAMES
                    or upper_key.startswith("CODEX_")
                    or upper_key in inherited_stdio_names
                ):
                    raise RuntimeError(
                        "Codex cannot securely externalize MCP stdio environment "
                        f"variable {key!r}; use a dedicated, non-runtime variable name"
                    )
                remove.add(f"mcp_servers.{cfg.name}.env.{_toml_key(key)}={_toml_quote(value)}")
                previous = stdio_secret_values.get(upper_key)
                if previous is not None and previous != (key, value):
                    raise RuntimeError(
                        "Codex MCP stdio servers declare conflicting values "
                        f"for environment variable {key!r}; use distinct names"
                    )
                stdio_secret_values[upper_key] = (key, value)
                secret_env[key] = value
                origin[key] = f"stdio server {cfg.name!r} env {key!r}"
                if key not in env_vars:
                    env_vars.append(key)
            if env_vars:
                add.append(f"mcp_servers.{cfg.name}.env_vars={_toml_array(env_vars)}")
            continue

        for header_index, (key, value) in enumerate(cfg.headers.items()):
            remove.add(f"mcp_servers.{cfg.name}.http_headers.{_toml_key(key)}={_toml_quote(value)}")
            env_name = f"{_CODEX_MCP_SECRET_ENV_PREFIX}{nonce}_{server_index}_{header_index}"
            secret_env[env_name] = value
            origin[env_name] = f"http server {cfg.name!r} header {key!r}"
            add.append(
                f"mcp_servers.{cfg.name}.env_http_headers.{_toml_key(key)}={_toml_quote(env_name)}"
            )

    if secret_env:
        # codex-cli 0.144.4 applies the automatic KEY/SECRET/TOKEN filter to
        # app-server ``command/exec`` requests, but its model-facing ``shell``
        # tool can still inherit the full app-server environment.  MCP header
        # values have to live in that parent environment for
        # ``env_http_headers`` to resolve them, so make shell inheritance
        # fail-closed at the ``core`` baseline and disable login-shell startup.
        # The latter is required because a login-shell snapshot can restore
        # app-server variables after the environment policy has filtered them.
        # This keeps HOME, PATH, and the other portable process essentials
        # without loading profile state back into the model shell.
        for policy in (
            _CODEX_SHELL_APPLY_SECRET_FILTERS,
            _CODEX_SHELL_CORE_INHERIT,
            _CODEX_DISABLE_LOGIN_SHELL,
        ):
            if policy not in overrides:
                add.append(policy)
        # ``env_http_headers`` / ``env_vars`` are explicit references, and
        # codex-cli can keep referenced values available under ``core`` when
        # a login-shell snapshot is allowed to rehydrate its parent state.
        # Exact filters plus disabled login-shell startup close both paths.
        for env_name in secret_env:
            policy = (
                f"shell_environment_policy.filters.{_toml_key(env_name)}={_toml_quote('exclude')}"
            )
            if policy not in overrides:
                add.append(policy)
    safe_overrides = tuple(value for value in overrides if value not in remove) + tuple(add)
    residues = _find_secret_residues(safe_overrides, secret_env, origin)
    if residues:
        detail = "; ".join(residues)
        logger.error("codex MCP secret residue in argv overrides: %s", detail)
        raise RuntimeError(
            f"Refusing to launch Codex because an MCP secret remained in argv — {detail}"
        )
    return safe_overrides, secret_env


def _find_secret_residues(
    safe_overrides: tuple[str, ...],
    secret_env: dict[str, str],
    origin: dict[str, str],
) -> list[str]:
    """Diagnose externalized values that still appear in the override text.

    Matches VALUE PARTS only (the text after each line's first ``=``): a value
    that merely collides with a dotted key path — a server name,
    ``http_headers.``, a policy key — no longer trips the guard. The previous
    blind full-text substring check produced exactly that class of
    undebuggable false positive, because every header value (secret or not)
    is externalized. Each value is probed in the three shapes it could
    survive in: raw (a removal miss), TOML-escaped (as ``_toml_quote`` writes
    it into strings and arrays), and urlencoded (as ``merge_params_into_url``
    embeds query params into ``url``).

    Returns one redacted description per residue — origin, env name, length,
    and a SHA-256 prefix for cross-checking against stored credentials; never
    the value itself.
    """
    residues: list[str] = []
    value_parts = [(line.partition("=")[0], line.partition("=")[2]) for line in safe_overrides]
    for env_name, value in secret_env.items():
        if not value:
            continue
        probes = {value, _toml_quote(value)[1:-1], quote_plus(value)}
        matched = sorted(key for key, part in value_parts if any(p in part for p in probes))
        if matched:
            digest = hashlib.sha256(value.encode()).hexdigest()[:8]
            residues.append(
                f"{origin.get(env_name, 'unknown origin')} (env {env_name}, "
                f"len={len(value)}, sha256={digest}) matched override value(s): "
                + ", ".join(matched)
            )
    return residues


def _build_config_overrides(
    session: Session,
    provider: ModelProvider | None,
    model: str,
    *,
    expose_toolkit: bool = False,
    egress_base_url: str | None = None,
) -> tuple[str, ...]:
    """Serialize per-session config to dotted TOML override entries.

    Three channels are emitted:

    1. ``Session.mcp_servers`` -> ``mcp_servers.<name>.{url|command,...}``.
       Codex auto-detects transport: presence of ``command`` -> stdio,
       presence of ``url`` -> remote HTTP. Stdio entries emit
       ``command`` / ``args`` / ``env_vars`` and a dotted
       ``mcp_servers.X.env.<KEY>`` per var; remote entries emit ``url`` and a
       dotted ``mcp_servers.X.http_headers.<KEY>`` per header. Map fields
       (``env`` / ``http_headers``) are emitted ONE dotted key at a time, never
       as an inline table ``{ … }`` — codex's config overlay parser reads an inline
       table as a string and aborts at startup ("invalid type: string …
       expected a map"); see ``_toml_key``. Before launch,
       ``_externalize_mcp_secrets`` replaces header / explicit env values with
       environment references so their values never enter process argv.
    2. Model transport -> a synthetic ``[model_providers.harness]`` block when
       a user provider or an egress subscription descriptor is present. API
       keys use a dedicated ``env_key``; ChatGPT subscriptions use Codex's
       native ``requires_openai_auth`` path and keep credentials in auth.json.
    3. Harness ``ToolKit`` -> ``mcp_servers.harness_toolkit`` pointing at
       the FastAPI MCP-over-HTTP endpoint. Unauthenticated; the backend is
       expected to bind loopback / private network so the URL is only
       reachable from a colocated codex subprocess.

    Values are TOML-quoted to avoid injection through unescaped quotes /
    backslashes.
    """
    overrides: list[str] = []
    for cfg in session.mcp_servers:
        if isinstance(cfg, McpStdioServerConfig):
            overrides.append(f"mcp_servers.{cfg.name}.command={_toml_quote(cfg.command)}")
            if cfg.args:
                overrides.append(f"mcp_servers.{cfg.name}.args={_toml_array(cfg.args)}")
            if cfg.env_vars:
                # The CLI's default secret-name filtering protects shell
                # commands, but an MCP ``env_vars`` entry is an explicit
                # allowlist and can request the provider key by name. Never
                # emit that request on the egress API-key path; keep all other
                # user-declared variables unchanged.
                env_vars = tuple(
                    name
                    for name in cfg.env_vars
                    if not (
                        provider is not None
                        and egress_base_url is not None
                        and name.upper() == _HARNESS_PROVIDER_ENV_KEY
                    )
                )
                if env_vars:
                    overrides.append(f"mcp_servers.{cfg.name}.env_vars={_toml_array(env_vars)}")
            # ``env`` is a TOML map: emit one dotted key per var, NOT an inline
            # table — codex's config parser rejects ``env={ … }`` as a string
            # (see ``_toml_key``).
            for k, v in cfg.env.items():
                overrides.append(f"mcp_servers.{cfg.name}.env.{_toml_key(k)}={_toml_quote(v)}")
            continue
        overrides.append(f"mcp_servers.{cfg.name}.url={_toml_quote(cfg.url)}")
        if cfg.headers:
            # ``http_headers`` is a TOML map: emit one dotted key per header, NOT
            # an inline table. ``http_headers={ Authorization = "…" }`` makes
            # codex abort at startup with "invalid type: string … expected a
            # map" (see ``_toml_key``), which surfaced as a bogus
            # ``runtime process interrupted`` for every session with a
            # header-bearing HTTP MCP server.
            for k, v in cfg.headers.items():
                overrides.append(
                    f"mcp_servers.{cfg.name}.http_headers.{_toml_key(k)}={_toml_quote(v)}"
                )
        # Per-server tool-call timeout (seconds, TOML float). Emitted only when
        # the owner declared one — e.g. the host's ``harness`` toolkit, whose
        # ``await_members`` parks longer than codex's 120s default. A bare
        # number (never quoted) so codex reads it as a float, not a string.
        if cfg.tool_timeout_sec is not None:
            overrides.append(
                f"mcp_servers.{cfg.name}.tool_timeout_sec={float(cfg.tool_timeout_sec)}"
            )

    if expose_toolkit:
        base = os.getenv(CODEX_TOOLKIT_BASE_URL_ENV) or CODEX_TOOLKIT_BASE_URL_DEFAULT
        toolkit_url = f"{base.rstrip('/')}/mcp/toolkit/{session.id}"
        overrides.append(f"mcp_servers.{_HARNESS_TOOLKIT_MCP_NAME}.url={_toml_quote(toolkit_url)}")
        # Same 120s-default lift for the kernel-exposed toolkit path (await_members).
        overrides.append(
            f"mcp_servers.{_HARNESS_TOOLKIT_MCP_NAME}.tool_timeout_sec="
            f"{_HARNESS_TOOLKIT_TOOL_TIMEOUT_SEC}"
        )

    # NB: ``model_reasoning_effort`` is intentionally NOT emitted as a
    # server-level override here. Codex pins ``reasoning_effort`` into
    # thread metadata at ``thread_start`` and ``ThreadResumeParams`` has
    # no effort field, so a config-override-only path would silently
    # ignore edits made after the first turn. The effort lever lives in
    # ``_build_turn_kwargs`` (``TurnStartParams.effort``), which
    # documented semantics override per-turn-and-subsequent — that path
    # works regardless of whether the thread was just started or resumed.

    # Bare one-shot completion (``is_bare_completion``): strip the optional
    # built-in tools codex's config surface lets us drop. The codex base
    # instructions are baked into the CLI and cannot be removed, but each
    # tool below otherwise adds its schema (and prompt guidance) to every
    # request of a session that will never call a tool:
    #
    # * ``include_plan_tool`` — the update_plan/planning tool
    # * ``include_apply_patch_tool`` — the freeform apply_patch tool
    # * ``include_view_image_tool`` — the local-image attach tool
    # * ``web_search="disabled"`` — only on the subscription path
    #   (``provider is None``); the api-key branch below already emits it.
    if is_bare_completion(session):
        overrides.extend(
            [
                "include_plan_tool=false",
                "include_apply_patch_tool=false",
                "include_view_image_tool=false",
            ]
        )
        if provider is None:
            overrides.append('web_search="disabled"')

    # ``model_reasoning_summary``: subprocess-global default for
    # whether codex requests reasoning summaries from the model. The
    # codex CLI's hidden default is effectively ``none``, so without
    # this override the harness saw no ``thinking`` / ``thinking_delta``
    # events from o-series / gpt-5. Distinct from effort: there's no
    # thread-metadata pin trap for summary (``ThreadStartParams`` /
    # ``ThreadResumeParams`` neither expose nor pin it; only the
    # per-turn ``TurnStartParams.summary`` exists as a live override).
    # Config-level is therefore the right home for a system-wide
    # default that isn't user-tunable.
    overrides.append(f"model_reasoning_summary={_toml_quote(_CODEX_REASONING_SUMMARY_DEFAULT)}")

    # Channel-declared input window for models codex's own catalog can't
    # know (gateway aliases). ``model_context_window`` feeds codex's
    # remaining-context bookkeeping AND its compaction trigger: codex
    # derives ``auto_compact_token_limit`` as 90% of the resolved window on
    # its own (``ModelInfo::auto_compact_token_limit``) and clamps any
    # explicit ``model_auto_compact_token_limit`` to that same 90%, so the
    # runtime owns the threshold and we declare only the window. Bare TOML
    # integer (quoting turns it into a string codex rejects). Unlike
    # ``model_reasoning_effort`` there is no thread-metadata pin trap: the
    # model is locked per session, so the value never changes between
    # thread_start and any later resume.
    #
    # Known upstream cap (codex-cli 0.144.x): an alias codex resolves to
    # its fallback metadata carries ``max_context_window = 272k`` and the
    # override is clamped to it, so a declaration above 272k is honoured
    # only up to 272k unless the alias prefix-matches a catalog model with
    # a larger cap. Nothing to do here — declaring the real value keeps the
    # config correct for a codex that lifts the cap.
    max_input_tokens = (
        session.model_settings.max_input_tokens if session.model_settings is not None else None
    )
    if max_input_tokens:
        overrides.append(f"model_context_window={max_input_tokens}")

    if provider is not None:
        # Codex's ``web_search`` tool is wired against the OpenAI
        # subscription / ChatGPT Plus path; it errors out when routed
        # through a user-supplied API key (the ``model_provider`` branch).
        # ``WebSearchMode`` enum: ``disabled`` | ``cached`` | ``live``
        # — emitting ``disabled`` removes the tool from the catalog the
        # subprocess advertises to the model. Gate is ``provider is not
        # None`` regardless of ``base_url``: first-party OpenAI direct
        # API (``base_url=None``, ``OPENAI_API_KEY`` from session) hits
        # the same non-subscription wall.
        overrides.append('web_search="disabled"')

    effective_base_url = egress_base_url or (provider.base_url if provider is not None else None)
    if provider is None and egress_base_url is not None:
        name = _HARNESS_PROVIDER_NAME
        if model:
            overrides.append(f"model={_toml_quote(model)}")
        overrides.extend(
            [
                f"model_provider={_toml_quote(name)}",
                # Codex keys remote-compaction support off this display name.
                f"model_providers.{name}.name={_toml_quote('OpenAI')}",
                f"model_providers.{name}.base_url={_toml_quote(egress_base_url)}",
                f'model_providers.{name}.wire_api="responses"',
                f"model_providers.{name}.requires_openai_auth=true",
                # Prefer deterministic HTTPS streaming for the proxy canary.
                # A broken WSS stream otherwise incurs Codex's five reconnect
                # attempts before its own HTTPS fallback.
                f"model_providers.{name}.supports_websockets=false",
            ]
        )
    elif provider is not None and effective_base_url is not None:
        name = _HARNESS_PROVIDER_NAME
        env_key = _HARNESS_PROVIDER_ENV_KEY
        # Codex only supports ``wire_api = "responses"``; the harness-side
        # api_protocol field is ignored here. Routing for non-openai
        # protocols happens upstream (factory dispatch keeps anthropic out).
        # ``env_key`` is the env var the codex subprocess reads at request
        # time to get the API key — its value is supplied through
        # ``CodexConfig.env`` (see ``_build_codex_env``), not the TOML
        # ``[model_providers.harness.env]`` block, which only injects extras
        # into model HTTP calls and is not consulted for ``env_key``.
        overrides.extend(
            [
                f"model={_toml_quote(model)}",
                f"model_provider={_toml_quote(name)}",
                f"model_providers.{name}.name={_toml_quote('Harness-supplied gateway')}",
                f"model_providers.{name}.base_url={_toml_quote(effective_base_url)}",
                f'model_providers.{name}.wire_api="responses"',
                f"model_providers.{name}.env_key={_toml_quote(env_key)}",
            ]
        )
    elif provider is not None and model:
        # First-party OpenAI: no synthetic provider block, no
        # ``model_provider=harness`` override. Codex uses its built-in
        # ``openai`` provider (which reads ``OPENAI_API_KEY`` from env —
        # we inject it in ``_build_codex_env``). We still emit the model
        # override so the subprocess targets the session's model instead
        # of whatever ``~/.codex/config.toml`` happens to pin. The
        # ``model`` truthy guard mirrors ``_build_thread_kwargs``: empty
        # string here would make codex try to resolve a deployment named
        # ``""`` and fail with "Missed model deployment".
        overrides.append(f"model={_toml_quote(model)}")

    if provider is not None:
        # Every custom provider path puts an API key in the app-server parent
        # environment (HARNESS_CODEX_PROVIDER_API_KEY or OPENAI_API_KEY).
        # codex-cli 0.144.4's model ``shell`` path does not reliably honor the
        # automatic name filter, so use the same core boundary, login-shell
        # block, and exact keyed exclusion as MCP secrets.  Avoid changing
        # native ChatGPT subscription sessions, which carry no provider
        # credential in the process env.
        if _CODEX_SHELL_APPLY_SECRET_FILTERS not in overrides:
            overrides.append(_CODEX_SHELL_APPLY_SECRET_FILTERS)
        if _CODEX_SHELL_CORE_INHERIT not in overrides:
            overrides.append(_CODEX_SHELL_CORE_INHERIT)
        if _CODEX_DISABLE_LOGIN_SHELL not in overrides:
            overrides.append(_CODEX_DISABLE_LOGIN_SHELL)
        provider_env_key = (
            _HARNESS_PROVIDER_ENV_KEY if effective_base_url is not None else _CODEX_OPENAI_API_KEY
        )
        overrides.append(
            f"shell_environment_policy.filters.{_toml_key(provider_env_key)}="
            f"{_toml_quote('exclude')}"
        )

    return tuple(overrides)


def _build_codex_env(
    provider: ModelProvider | None,
    *,
    egress_base_url: str | None = None,
    mcp_secret_env: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Subprocess env passed to ``codex app-server``.

    Inherits the parent process env so the codex CLI keeps its existing
    ``~/.codex/config.toml`` lookups, ``AZURE_OPENAI_API_KEY`` etc., and
    publishes the per-session API key on **one of two** channels
    depending on whether the user wired a gateway:

    * ``base_url`` present — the harness emits a synthetic
      ``[model_providers.harness]`` TOML block whose ``env_key`` points
      at ``HARNESS_CODEX_PROVIDER_API_KEY``; we set that here.
    * ``base_url is None`` — codex uses its built-in ``openai``
      provider, which reads ``OPENAI_API_KEY``; we set that instead.
      The harness-specific env var is *not* set in this branch (it'd
      be dead weight; codex's built-in openai provider doesn't read
      it).

    ``mcp_secret_env`` carries values referenced by secret-free MCP
    ``env_http_headers`` / ``env_vars`` config entries. Generated HTTP-header
    names contain ``SECRET`` for defense in depth.  The generated config also
    constrains model shell tools to Codex's ``core`` inheritance baseline,
    disables login-shell profile restoration, and adds an exact keyed
    exclusion for every credential.  codex-cli 0.144.4
    does not reliably apply its automatic name filter on that execution path,
    and MCP environment references are otherwise retained after the core
    baseline.  Stdio variables remain explicitly available to their MCP
    processes without being inherited by model shell commands.
    """
    if provider is None:
        if egress_base_url is None and not mcp_secret_env:
            return None
        subscription_env = dict(os.environ)
        if egress_base_url is not None:
            # The model transport keeps using Codex's native ChatGPT auth.json.
            # Keep loopback ingress out of any proxy inherited by the GUI.
            merge_loopback_no_proxy(subscription_env, egress_base_url)
        if mcp_secret_env:
            subscription_env.update(mcp_secret_env)
        return subscription_env
    merged: dict[str, str] = dict(os.environ)
    if egress_base_url is not None or provider.base_url is not None:
        merged[_HARNESS_PROVIDER_ENV_KEY] = provider.api_key
        if egress_base_url is not None:
            merge_loopback_no_proxy(merged, egress_base_url)
        # Present as the CLI's originator (``codex_exec``) rather than the SDK's
        # default (``codex_python_sdk``). Some third-party OpenAI-compatible
        # gateways (e.g. Volcengine/Doubao) whitelist codex's proprietary tool
        # types (``namespace``…) + ``client_metadata`` only for the CLI
        # originator and 400 ("unknown tool type: namespace") for the SDK one —
        # even though the request body is byte-identical. Same App Server, so
        # spoofing the originator makes the SDK path match the working CLI path.
        merged["CODEX_INTERNAL_ORIGINATOR_OVERRIDE"] = "codex_exec"
    else:
        merged[_CODEX_OPENAI_API_KEY] = provider.api_key
    if mcp_secret_env:
        merged.update(mcp_secret_env)
    return merged


def _map_effort_for_codex(effort: str) -> str:
    """Map the harness's cross-runtime effort literal to codex's
    ``model_reasoning_effort`` value space.

    Codex accepts ``minimal | low | medium | high | xhigh``; the harness
    ``max`` level is Anthropic-only and maps down to codex ``xhigh``.
    Anything unrecognised falls back to ``medium`` defensively — better
    a known-safe value than a TOML parse error in the codex subprocess.
    """
    if effort == "max":
        return "xhigh"
    if effort in {"low", "medium", "high", "xhigh"}:
        return effort
    return "medium"


def _toml_quote(value: str) -> str:
    """Quote a string for inclusion in a TOML scalar value.

    Codex's ``-c k=v`` flag is parsed as TOML; unescaped quotes / backslashes
    in the input would corrupt the override. Use a basic-string with the
    standard escape set.
    """
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _toml_array(values: tuple[str, ...] | list[str]) -> str:
    """Render a sequence of strings as a TOML inline array literal."""
    return "[" + ", ".join(_toml_quote(v) for v in values) + "]"


_BARE_KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _toml_key(key: str) -> str:
    """Render one TOML dotted-key segment (bare when safe, else quoted).

    Bare for a TOML bare-key (``A-Za-z0-9_-``); otherwise a quoted key segment.
    Used to emit ``http_headers`` / ``env`` maps ONE dotted key at a time —
    codex's ``-c k=v`` parser does NOT accept an inline-table RHS. Passing
    ``mcp_servers.X.http_headers={ Authorization = "…" }`` makes codex read the
    whole ``{ … }`` as a *string* and abort at startup with
    ``invalid type: string … expected a map in mcp_servers.X.http_headers``
    (verified against codex-cli 0.137.0-alpha.4 in-sandbox). The dotted form
    ``mcp_servers.X.http_headers.Authorization="…"`` parses as a table entry.
    """
    if key and all(c in _BARE_KEY_CHARS for c in key):
        return key
    return _toml_quote(key)


def _stop_reason_from_turn(turn_done: TurnCompletedNotification) -> StopReason:
    status = turn_done.turn.status
    if status == TurnStatus.completed:
        return EndTurn()
    if status == TurnStatus.interrupted:
        return Error(
            category="user_interrupt",
            retry_status="terminal",
            message="cancelled",
        )
    if status == TurnStatus.failed:
        err = turn_done.turn.error
        return Error(
            category="execution_error",
            retry_status="exhausted",
            message=err.message if err is not None else "turn failed",
        )
    # ``in_progress`` should not appear on a turn/COMPLETED notification —
    # codex is contradicting itself. Surface it rather than swallowing it,
    # but NOT as ``BudgetExhausted``: a budget stop is a legible, expected
    # outcome that the UI now explains in so many words ("this turn hit the
    # runtime's maximum step count"), and telling the user that about a
    # protocol inconsistency is a confident lie. It reads as an anomaly,
    # which is what it is.
    return Error(
        category="execution_error",
        retry_status="exhausted",
        # ``status.value``, not ``status`` — this string lands in the user's
        # error card, and ``repr`` of the enum would leak
        # ``<TurnStatus.in_progress: 'inProgress'>`` into it.
        message=f"codex reported a completed turn still in status {status.value!r}",
    )


_BREAKDOWN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def _breakdown_fields(breakdown: Any) -> dict[str, int] | None:
    """Read one ``TokenUsageBreakdown`` into a plain int map."""
    if breakdown is None:
        return None
    return {field: int(getattr(breakdown, field, 0) or 0) for field in _BREAKDOWN_FIELDS}


class _TurnUsageTracker:
    """Accumulate ONE turn's token spend from codex's usage notifications.

    Codex emits ``thread/tokenUsage/updated`` once per model request, and its
    ``ThreadTokenUsage`` carries two views: ``total`` is cumulative for the
    thread, ``last`` covers only the request that just finished. Neither is
    the turn: a turn that calls the model twice (the common tool-use shape)
    produces two notifications, so keeping just the newest ``last`` — what
    this runtime used to do by overwriting the payload each time — silently
    dropped every request but the final one. On a real 3-turn session that
    lost 69,193 of the first turn's 70,974 input tokens.

    Differencing ``total`` across the turn is exact and, unlike summing
    ``last``, survives a repeated or coalesced notification. The pre-turn
    baseline is recovered from the first notification (``total - last``), so
    nothing has to be carried across turns — a resumed thread that restores
    its cumulative counter cannot leak into the next turn's numbers.
    ``last``-summing stays as the fallback for a payload with no ``total``.
    """

    def __init__(self) -> None:
        self._baseline: dict[str, int] | None = None
        self._latest: dict[str, int] | None = None
        self._summed: dict[str, int] = dict.fromkeys(_BREAKDOWN_FIELDS, 0)
        self._saw_any = False

    def observe(self, thread_usage: Any) -> None:
        last = _breakdown_fields(getattr(thread_usage, "last", None))
        total = _breakdown_fields(getattr(thread_usage, "total", None))
        if last is None and total is None:
            return
        self._saw_any = True
        if total is not None:
            if self._baseline is None:
                self._baseline = {
                    field: max(0, total[field] - (last or {}).get(field, 0))
                    for field in _BREAKDOWN_FIELDS
                }
            self._latest = total
        if last is not None:
            for field in _BREAKDOWN_FIELDS:
                self._summed[field] += last[field]

    def totals(self) -> dict[str, int] | None:
        """This turn's spend, or None if no usage notification arrived."""
        if not self._saw_any:
            return None
        if self._latest is None or self._baseline is None:
            return dict(self._summed)
        return {
            field: max(0, self._latest[field] - self._baseline[field])
            for field in _BREAKDOWN_FIELDS
        }


def _usage_payload_from_turn_totals(totals: dict[str, int], model: str) -> dict[str, Any]:
    """Project one turn's codex totals onto our four flat fields.

    Codex reports cached input as a subset of ``input_tokens`` (so the
    uncached remainder is the cross-runtime ``input_tokens``) and reasoning
    as a subset of ``output_tokens`` — its own ``total_tokens`` is
    ``input_tokens + output_tokens`` with reasoning nowhere added, which is
    how we know. Adding reasoning on top of output, as this used to, counted
    the reasoning tokens twice.
    """
    cache_read = totals["cached_input_tokens"]
    reasoning_output = totals["reasoning_output_tokens"]
    flat = {
        "input_tokens": max(0, totals["input_tokens"] - cache_read),
        "output_tokens": totals["output_tokens"],
        "cache_read_tokens": cache_read,
        "cache_write_tokens": 0,
    }
    payload: dict[str, Any] = dict(flat)
    payload["model_usage"] = {
        model: {
            **flat,
            "reasoning_output_tokens": reasoning_output,
            "total_tokens": totals["total_tokens"],
        }
    }
    return payload


def _empty_usage_payload(model: str) -> dict[str, Any]:
    flat = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    return {**flat, "model_usage": {model: dict(flat)}}


def _stop_reason_to_dict(reason: StopReason | None) -> dict[str, Any]:
    if reason is None:
        return {}
    return asdict(reason)

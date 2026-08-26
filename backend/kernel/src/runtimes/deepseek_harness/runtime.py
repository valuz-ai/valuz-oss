"""DeepSeekHarnessRuntime — drives a DeepSeek Harness SDK runtime as a RuntimePort.

One dsh runtime subprocess per kernel Session, spoken to over stdio JSON-RPC
(``jsonrpc_client``). The wire has no cancel / resume / fork / approval
methods yet (verified against dsh 0.1.0-rc.5 — see
docs/references/deepseek-harness/runtime-gap-analysis.md), so this adapter
ships the documented v1 stances:

* **Interrupt = kill.** ``interrupt()`` hard-stops the subprocess; the turn
  settles as ``user_interrupt`` and the next ``run`` cold-starts.
* **Cross-process continuation = transcript replay.** The SDK server cannot
  rehydrate persisted logs (id collision), so the adapter keeps its own
  transcript sidecar under the state dir and prepends a
  ``<conversation-history>`` block on the first prompt of a fresh process.
  Within one live process, dsh continues the session natively.
* **No tool approvals.** Tools composed into the session run unattended;
  ``permission_mode="auto_review"`` is rejected at session create (route
  guard). The ONE parked surface is the user-questions bridge below.
* **No native fork / task coverage.** ``fork_session`` and
  ``run_task_coverage`` raise; ``supports_native_continuation`` is False so
  the orchestrator marks task coverage unavailable instead of calling it.
* **Plan mode + user questions = in-process plugins + HTTP bridge.** The
  wire has no plan or user-questions channel either, so the composition
  (on a plan-capable closure) mounts ``dsh-plan-mode`` /
  ``dsh-user-questions`` / ``dsh-tool-ask-user`` plus the Valuz
  ``valuz-dsh-kernel-bridge`` plugin, which converges dsh plan state to
  ``session.mode`` at spawn and forwards ``ask()`` to the kernel's
  ``/kernel/v1/dsh/user-questions/{token}`` endpoint. The forward parks as
  a standard ``requires_action`` (subject ``exit_plan_mode`` for the plan
  review, ``clarifying_questions`` for ask_user_question batches) and
  ``submit_action`` resolves it — approval therefore continues the SAME
  dsh turn natively, like Claude's ExitPlanMode. ``plan/mode`` session
  events map to ``mode_changed{by: "runtime"}`` so the kernel session row
  stays authoritative.

Model config is per subprocess: ``initialize(provider, model, maxTokens)``
locks the model for the process lifetime, which matches the kernel's
"(runtime, provider, model) fixed at session create" invariant exactly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from src.core.agent_config import AgentConfig
from src.core.approval_rule_matcher import ExactArgsRuleMatcher, RuntimeApprovalRuleMatcher
from src.core.events import (
    AVAILABLE_DECISIONS_CLARIFYING,
    AVAILABLE_DECISIONS_V1,
    Event,
    EventSink,
)
from src.core.tools import ToolDef, ToolKit
from src.core.types import (
    EndTurn,
    Error,
    ModelProvider,
    ModelSettings,
    Session,
    StopReason,
    UserMessage,
)
from src.core.user_questions_bridge import (
    UserQuestionsBridgeRecord,
    register_user_questions_bridge,
    unregister_user_questions_bridge,
)
from src.runtimes.deepseek_harness.approval_bridge import (
    build_ask_answer_envelope,
    build_dsh_pending_payload,
    classify_dsh_subject,
)
from src.runtimes.deepseek_harness.composition import (
    DshLaunchSpec,
    cleanup_composition,
    resolve_launch,
    user_questions_endpoint,
    write_composition,
)
from src.runtimes.deepseek_harness.event_mapper import (
    DshEventMapper,
    extract_assistant_text,
    extract_step_usage,
    extract_turn_end_reason,
)
from src.runtimes.deepseek_harness.jsonrpc_client import (
    TRANSPORT_CLOSED,
    DshNotification,
    DshRuntimeClient,
)
from src.runtimes.interruption import (
    absorb_interrupt_cancellations,
    describe_exception,
    is_runtime_interruption,
)
from src.runtimes.skills_materialize import prepare_codex_skills

logger = logging.getLogger(__name__)

# The provider route the generated composition registers (dsh-llm-deepseek).
DSH_PROVIDER_ROUTE = "deepseek-official"

STATE_DIR_ENV = "VALUZ_DSH_STATE_DIR"
DEFAULT_STATE_DIR = "./dsh_state"

# Cold-start grace before the first prompt of a process whose composition
# mounts MCP servers. dsh registers MCP tools asynchronously after boot and
# the SDK wire has no readiness signal, so a prompt dispatched immediately
# after spawn assembles its tool schemas before the HTTP handshake +
# tools/list finish — the model's first turn then simply lacks every
# ``mcp__*`` tool (observed on the fast vendored carrier; the slower
# source-mode tsx boot masked it). Bounded and cancellable; sessions
# without MCP servers skip it entirely.
MCP_READY_GRACE_ENV = "VALUZ_DSH_MCP_READY_GRACE_SEC"
DEFAULT_MCP_READY_GRACE_SEC = 3.0

# Transcript-replay caps: newest entries win, oldest are dropped first.
_REPLAY_MAX_ENTRIES = 40
_REPLAY_MAX_CHARS = 60_000


class DeepSeekHarnessRuntime:
    """RuntimePort adapter over the dsh SDK stdio JSON-RPC runtime."""

    def __init__(
        self,
        config: AgentConfig,
        model: str,
        event_sink: EventSink,
        toolkit: ToolKit | None = None,
        workspace_root: str = "",
        model_provider: ModelProvider | None = None,
        model_settings: ModelSettings | None = None,
        state_dir: str | None = None,
        launch_spec: DshLaunchSpec | None = None,
    ) -> None:
        self.config = config
        self.model = model
        self.event_sink = event_sink
        self.workspace_root = workspace_root
        self.model_provider = model_provider
        self.model_settings = model_settings
        self._state_dir = state_dir or os.getenv(STATE_DIR_ENV) or DEFAULT_STATE_DIR
        self._launch_spec = launch_spec
        self._client: DshRuntimeClient | None = None
        self._config_path: str | None = None
        self._composition_fingerprint: str | None = None
        self._native_session_id: str | None = None
        self._process_turns = 0
        self._cancelled = False
        self._active_task: asyncio.Task[Any] | None = None
        # ``interrupt()`` cancels of ``_active_task`` this turn; balanced by
        # ``run()`` after it swallows the injected ``CancelledError`` (see
        # ``absorb_interrupt_cancellations``).
        self._interrupt_cancels = 0
        self._turn_anchor: dict[str, Any] | None = None
        self._mapper = DshEventMapper()
        # Kernel-owned ToolDefs (e.g. PTC's execute_code) are served to dsh
        # through the kernel's ``/mcp/toolkit/{session_id}`` bridge: the
        # runtime registers its toolkit in ``mcp_bridge`` at spawn and the
        # composition carries one extra ``dsh-mcp-client`` row (same path
        # codex takes). See ``_register_kernel_toolkit``.
        self.toolkit = toolkit or ToolKit()
        self._registered_session_id: str | None = None
        # User-questions bridge state (plan review + clarifying parks).
        # ``_uq_token`` is the per-spawn credential the composition hands
        # the bridge plugin; ``_uq_asks`` keeps every ask of the current
        # process (terminal states stay readable for poll idempotency).
        self._uq_token: str | None = None
        self._uq_asks: dict[str, _UserQuestionsAsk] = {}
        self._pending_futures: dict[
            str,
            asyncio.Future[
                tuple[Literal["approve", "reject", "answer"], str | None, dict[str, Any] | None]
            ],
        ] = {}
        self._ask_tasks: set[asyncio.Task[None]] = set()
        # Whether the spawned closure carries the plan plugin set, and the
        # dsh-side plan state we believe is in force (spawn-converged, then
        # tracked from wire ``plan/mode`` events). ``run()`` respawns when
        # it drifts from ``session.mode`` — e.g. the user toggled the chip
        # off between turns (there is no wire path to flip it live).
        self._plan_capable = False
        self._dsh_plan_active: bool | None = None

    # -- RuntimePort surface --

    @property
    def supports_native_continuation(self) -> bool:
        return False

    @property
    def approval_rule_matcher(self) -> RuntimeApprovalRuleMatcher:
        return ExactArgsRuleMatcher()

    def update_sink(self, sink: EventSink) -> None:
        self.event_sink = sink

    def consume_turn_anchor(self) -> dict[str, Any] | None:
        anchor = self._turn_anchor
        self._turn_anchor = None
        return anchor

    async def prepare(self, session: Session) -> None:
        await self._ensure_process(session)

    async def fork_session(
        self,
        session: Session,
        *,
        source_native_session_id: str,
        anchor: str | None = None,
    ) -> str:
        raise NotImplementedError(
            "deepseek_harness has no wire-level fork yet (dsh session.fork "
            "exists in-core; SDK server support is upstream work)"
        )

    async def run_task_coverage(
        self,
        session: Session,
        user_message: UserMessage,
        *,
        no_op_tool: ToolDef,
    ) -> None:
        raise NotImplementedError(
            "deepseek_harness reports supports_native_continuation=False; "
            "the orchestrator must not request a task-coverage continuation"
        )

    async def submit_action(
        self,
        pending_id: str,
        decision: Literal["approve", "approve_with_changes", "reject", "answer"],
        message: str | None = None,
        answers: dict[str, str | list[str]] | None = None,
        modified_input: dict[str, Any] | None = None,
    ) -> None:
        """Resolve a user-questions pending (the only parked surface here).

        ``approve`` / ``reject`` decide the ``exit_plan_mode`` plan review;
        ``answer`` carries the clarifying-questions selections. The decided
        future wakes ``_resolve_ask``, which translates the verb into dsh's
        answers envelope and releases the bridge plugin's long-poll — the
        dsh turn then continues natively (approve = same-turn execution).
        ``approve_with_changes`` is never advertised for these subjects;
        raise defensively, mirroring codex.
        """
        if decision == "approve_with_changes":
            raise NotImplementedError(
                "deepseek_harness does not advertise 'approve_with_changes'; "
                "the user-questions bridge has no modified-input analog."
            )
        _ = modified_input
        future = self._pending_futures.get(pending_id)
        if future is None or future.done():
            return
        future.set_result((decision, message, dict(answers) if answers is not None else None))

    async def interrupt(self) -> None:
        self._cancelled = True
        # Seal parked user-questions first (cheap set_result) so nothing
        # stays blocked if the sink chain hangs — codex ordering rationale.
        for pending_id, future in list(self._pending_futures.items()):
            if future.done():
                continue
            future.set_result(("reject", "session interrupted", None))
            await self._emit_synthetic_resolved(pending_id, "interrupted")
        self._pending_futures.clear()
        client = self._client
        if client is not None:
            # No cancel method on the wire — killing the subprocess is the
            # only abandon path. The reader loop's TRANSPORT_CLOSED sentinel
            # wakes the parked turn loop, which settles as user_interrupt.
            client.kill()
        task = self._active_task
        if task is not None and not task.done():
            task.cancel()
            self._interrupt_cancels += 1

    async def close(self) -> None:
        if self._registered_session_id is not None:
            from src.core.mcp_bridge import unregister_session_toolkit

            unregister_session_toolkit(self._registered_session_id)
            self._registered_session_id = None
        if self._uq_token is not None:
            unregister_user_questions_bridge(self._uq_token)
            self._uq_token = None
        current = asyncio.current_task()
        ask_tasks = [t for t in self._ask_tasks if t is not current and not t.done()]
        for t in ask_tasks:
            t.cancel()
        if ask_tasks:
            await asyncio.gather(*ask_tasks, return_exceptions=True)
        self._ask_tasks.clear()
        self._pending_futures.clear()
        self._uq_asks.clear()
        self._plan_capable = False
        self._dsh_plan_active = None
        client = self._client
        self._client = None
        self._native_session_id = None
        self._process_turns = 0
        if client is not None:
            try:
                await client.close()
            except Exception:
                logger.debug("dsh client close failed", exc_info=True)
        cleanup_composition(self._config_path)
        self._config_path = None

    async def run(self, session: Session, user_message: UserMessage) -> None:
        from src.core.prompt_builder import build_user_prompt

        session.status = "running"
        self._cancelled = False
        self._interrupt_cancels = 0
        self._active_task = asyncio.current_task()
        try:
            # The composition is baked once per subprocess, but the session's
            # capability state drifts between turns: the host's pre-turn
            # re-stamp rotates MCP credentials (external connector bearers
            # expire ~1h; the internal token re-bakes after restarts), and
            # instructions/skills can converge too. A live process holding a
            # stale composition would 403 on every MCP call with no
            # self-healing path — respawn instead; the transcript sidecar
            # replays context so continuity survives the cold start.
            if (
                self._client is not None
                and self._client.is_running
                and self._composition_fingerprint != _composition_fingerprint(session)
            ):
                logger.info(
                    "deepseek_harness: session %s capability state drifted — "
                    "respawning the runtime with a fresh composition",
                    session.id,
                )
                await self.close()
            # Plan-state drift is tracked separately from the fingerprint:
            # a runtime-initiated exit (approved exit_plan_mode) flips BOTH
            # sides — dsh's logged state and (via mode_changed write-through)
            # ``session.mode`` — so no respawn is needed then. Only a
            # user-side toggle between turns leaves them disagreeing, and
            # the wire has no live flip, so respawn with a fresh
            # ``planActive`` and let the bridge plugin re-converge.
            if (
                self._client is not None
                and self._client.is_running
                and self._plan_capable
                and self._dsh_plan_active is not None
                and self._dsh_plan_active != (session.mode == "plan")
            ):
                logger.info(
                    "deepseek_harness: session %s plan state drifted "
                    "(dsh=%s, kernel mode=%s) — respawning",
                    session.id,
                    self._dsh_plan_active,
                    session.mode,
                )
                await self.close()
            cold_start = self._client is None or not self._client.is_running
            await self._ensure_process(session)
            assert self._client is not None and self._native_session_id is not None

            prompt = build_user_prompt(
                user_message,
                cwd=self.workspace_root,
                now=datetime.now().astimezone(),
            )
            if cold_start and self._process_turns == 0:
                replay = self._build_replay_block(session)
                if replay:
                    prompt = f"{replay}\n\n{prompt}"

            self._mapper.reset()
            t_dispatch = time.monotonic()
            message_id = await self._client.session_prompt(
                self._native_session_id, [{"type": "text", "text": prompt}]
            )
            await self.event_sink.emit(
                Event(
                    type="turn_phase",
                    data={
                        "phase": "dispatch",
                        "duration_ms": int((time.monotonic() - t_dispatch) * 1000),
                    },
                )
            )

            outcome = await self._consume_turn(message_id)
            self._process_turns += 1

            if self._cancelled:
                session.stop_reason = Error(
                    category="user_interrupt", retry_status="terminal", message="cancelled"
                )
            else:
                session.stop_reason = _stop_reason_from_turn_end(outcome.turn_end_reason)
                if isinstance(session.stop_reason, Error):
                    await self.event_sink.emit(
                        Event(type="session_error", data={"message": session.stop_reason.message})
                    )
            session.status = "idle"

            await self.event_sink.emit(
                Event(type="usage_update", data=_usage_payload(outcome.usage_totals, self.model))
            )

            self._append_transcript(session, "user", user_message.text)
            if outcome.last_assistant_text:
                self._append_transcript(session, "assistant", outcome.last_assistant_text)

            self._turn_anchor = {
                "provider": "deepseek_harness",
                "native_session_id": self._native_session_id,
                "seq": outcome.last_seq,
            }
        except asyncio.CancelledError:
            session.status = "idle"
            session.stop_reason = Error(
                category="user_interrupt", retry_status="terminal", message="cancelled"
            )
            absorb_interrupt_cancellations(self._interrupt_cancels)
            self._interrupt_cancels = 0
        except Exception as exc:
            session.status = "idle"
            if self._cancelled:
                session.stop_reason = Error(
                    category="user_interrupt", retry_status="terminal", message="cancelled"
                )
            elif is_runtime_interruption(exc):
                cause = describe_exception(exc)
                logger.warning(
                    "deepseek_harness: runtime process interrupted mid-turn for session %s: %s",
                    session.id,
                    cause,
                )
                session.stop_reason = Error(
                    category="interrupted",
                    retry_status="terminal",
                    message=f"runtime process interrupted: {cause}",
                )
            else:
                cause = describe_exception(exc)
                logger.exception(
                    "deepseek_harness: turn failed for session %s: %s", session.id, cause
                )
                session.stop_reason = Error(
                    category="execution_error", retry_status="exhausted", message=cause
                )
                await self.event_sink.emit(Event(type="session_error", data={"message": cause}))
        finally:
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

    # -- turn internals --

    async def _consume_turn(self, message_id: str) -> _TurnOutcome:
        """Own the activity interval: inbox receipt -> whole-agent idle.

        Mirrors the official SDK's ``Session.run`` boundary (the wire has no
        per-prompt result): notifications before our message's inbox receipt
        belong to earlier activity and are skipped.
        """
        assert self._client is not None and self._native_session_id is not None
        outcome = _TurnOutcome()
        received = False
        while True:
            item = await self._client.notifications.get()
            if item is TRANSPORT_CLOSED:
                raise self._client._closed_error("dsh runtime exited mid-turn")
            assert isinstance(item, DshNotification)
            payload = item.payload
            if payload.get("sessionId") != self._native_session_id:
                continue
            if item.method == "session.event":
                event = payload.get("event")
                if not isinstance(event, dict):
                    continue
                if not received:
                    if _is_inbox_receipt(event, message_id):
                        received = True
                    continue
                seq = event.get("seq")
                if isinstance(seq, int):
                    outcome.last_seq = seq
                if event.get("type") == "plan/mode":
                    plan_data = event.get("data")
                    if isinstance(plan_data, dict) and isinstance(plan_data.get("active"), bool):
                        self._dsh_plan_active = plan_data["active"]
                for mapped in self._mapper.map_session_event(event):
                    await self.event_sink.emit(mapped)
                reason = extract_turn_end_reason(event)
                if reason is not None:
                    outcome.turn_end_reason = reason
                usage = extract_step_usage(event)
                if usage is not None:
                    outcome.add_usage(usage)
                text = extract_assistant_text(event)
                if text:
                    outcome.last_assistant_text = text
            elif item.method == "session.status":
                if received and payload.get("status") == "idle":
                    return outcome

    def _register_kernel_toolkit(self, session: Session) -> bool:
        """Publish this session's kernel ToolDefs on the mcp_bridge registry.

        Returns True when the composition should carry the kernel-toolkit
        MCP row — i.e. the toolkit has at least one callable, non-denied
        tool. The bridge (``kernel/app/mcp_toolkit_router``) resolves the
        record registered here per request; codex registers the same way.
        """
        callable_tools = [
            t for t in self.toolkit.list_tools() if t.handler is not None and t.permission != "deny"
        ]
        if not callable_tools:
            return False
        from src.core.mcp_bridge import register_session_toolkit
        from src.core.tools import ExecContext

        register_session_toolkit(
            session.id,
            self.toolkit,
            ExecContext(
                workspace=self.workspace_root,
                session_id=session.id,
                user_id=getattr(session, "user_id", "") or "",
            ),
        )
        self._registered_session_id = session.id
        return True

    # -- user-questions bridge (plan review + clarifying parks) --

    APPROVAL_TIMEOUT_SECONDS: float = 3600.0  # 1 h; class attr for test override
    # Server-side long-poll ceiling per GET — the plugin re-polls, so no
    # single HTTP request outlives client-side header timeouts.
    UQ_WAIT_CEILING_SECONDS: float = 30.0

    async def _start_user_questions_ask(self, questions: list[dict[str, Any]]) -> str:
        """Register one forwarded ``ask()`` as a parked ``requires_action``.

        Called by the transport layer (``app/dsh_user_questions_router``)
        on the runtime's own loop. Emits the pending event (plus the
        ``AskUserQuestion`` tool_use anchor for clarifying batches — the
        conversation page renders the interactive card by overriding that
        tool block, codex-established pattern; the mapper suppresses dsh's
        raw ``ask_user_question`` tool_use so the trace doesn't double-
        render), then spawns ``_resolve_ask`` to await the decision.
        """
        if not questions:
            raise ValueError("user-questions ask carried no questions")
        pending_id = str(uuid.uuid4())
        subject = classify_dsh_subject(questions)
        payload = build_dsh_pending_payload(subject, questions)

        if subject == "clarifying_questions":
            await self.event_sink.emit(
                Event(
                    type="tool_use",
                    data={
                        "id": pending_id,
                        "name": "AskUserQuestion",
                        "input": {"questions": payload.get("questions", [])},
                    },
                )
            )
            available = list(AVAILABLE_DECISIONS_CLARIFYING)
        else:
            # exit_plan_mode uses the V1 verb set — same rationale as the
            # Claude bridge: the model owns plan authorship, and "always
            # approve plans" has no useful semantic.
            available = list(AVAILABLE_DECISIONS_V1)

        await self.event_sink.emit(
            Event(
                type="requires_action",
                data={
                    "pending_id": pending_id,
                    "subject": subject,
                    "runtime_provider": "deepseek_harness",
                    "available_decisions": available,
                    "payload": payload,
                },
            )
        )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[
            tuple[Literal["approve", "reject", "answer"], str | None, dict[str, Any] | None]
        ] = loop.create_future()
        self._pending_futures[pending_id] = future
        ask = _UserQuestionsAsk(
            pending_id=pending_id,
            subject=subject,
            questions=[dict(q) for q in questions if isinstance(q, dict)],
            decision=future,
            result=loop.create_future(),
        )
        self._uq_asks[pending_id] = ask
        task = asyncio.create_task(self._resolve_ask(ask))
        self._ask_tasks.add(task)
        task.add_done_callback(self._ask_tasks.discard)
        return pending_id

    async def _wait_user_questions_answer(
        self, ask_id: str, wait_seconds: float
    ) -> dict[str, Any] | None:
        """Long-poll one ask's terminal state; ``None`` while pending.

        Raises ``KeyError`` for an unknown ask (the router turns that into
        404 — e.g. the kernel respawned and the token/ask are gone).
        Terminal states stay stored so a retried poll is idempotent.
        """
        ask = self._uq_asks[ask_id]
        wait = max(0.0, min(wait_seconds, self.UQ_WAIT_CEILING_SECONDS))
        try:
            await asyncio.wait_for(asyncio.shield(ask.result), timeout=wait)
        except TimeoutError:
            return None
        return ask.result.result()

    async def _resolve_ask(self, ask: _UserQuestionsAsk) -> None:
        """Await the host decision and release the bridge plugin's poll."""
        try:
            decision, message, answers = await asyncio.wait_for(
                ask.decision, timeout=self.APPROVAL_TIMEOUT_SECONDS
            )
        except TimeoutError:
            await self._emit_synthetic_resolved(ask.pending_id, "expired")
            if not ask.result.done():
                ask.result.set_result(
                    {
                        "status": "error",
                        "message": (
                            "the user did not respond to this request in time; "
                            "stop here and wait for their next message"
                        ),
                    }
                )
            return
        finally:
            self._pending_futures.pop(ask.pending_id, None)

        if ask.subject == "clarifying_questions":
            # Close the anchor pair so the card folds once answered
            # (mirrors the codex clarifying flow).
            content = (
                json.dumps(answers or {}, ensure_ascii=False)
                if decision == "answer"
                else (message or "declined")
            )
            await self.event_sink.emit(
                Event(
                    type="tool_result",
                    data={
                        "id": ask.pending_id,
                        "content": content,
                        "is_error": decision != "answer",
                    },
                )
            )

        envelope = build_ask_answer_envelope(ask.subject, ask.questions, decision, message, answers)
        if not ask.result.done():
            ask.result.set_result({"status": "answered", "answer": envelope})

    async def _emit_synthetic_resolved(self, pending_id: str, decision: str) -> None:
        """Runtime-side seal (timeout / interrupt) — same shape as codex."""
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
            logger.exception(
                "deepseek_harness: failed to emit synthetic action_resolved for %s",
                pending_id,
            )

    async def _ensure_process(self, session: Session) -> None:
        if self._client is not None and self._client.is_running:
            return
        t_init = time.monotonic()
        old_client = self._client
        self._client = None
        if old_client is not None:
            try:
                await old_client.close()
            except Exception:
                logger.debug("stale dsh client close failed", exc_info=True)
        cleanup_composition(self._config_path)
        self._config_path = None
        if self._uq_token is not None:
            # A failed spawn skips ``close()`` — drop the previous spawn's
            # bridge token here so the registry never accumulates dead
            # credentials across respawn attempts.
            unregister_user_questions_bridge(self._uq_token)
            self._uq_token = None

        launch = self._launch_spec or resolve_launch()
        if launch is None:
            raise RuntimeError(
                "deepseek_harness runtime is not launchable on this machine "
                "(set VALUZ_DSH_RUNTIME_BIN or VALUZ_DSH_ROOT)"
            )

        skills_root: str | None = None
        if session.skills:
            skills_root = prepare_codex_skills(self.workspace_root, session.skills)

        self._plan_capable = launch.plan_capable
        user_questions_url: str | None = None
        if launch.plan_capable:
            # Fresh credential per spawn — the token IS the auth for the
            # user-questions endpoint (PTC's model); revoked at close.
            self._uq_token = secrets.token_hex(16)
            register_user_questions_bridge(
                self._uq_token,
                UserQuestionsBridgeRecord(
                    start_ask=self._start_user_questions_ask,
                    wait_answer=self._wait_user_questions_answer,
                ),
            )
            user_questions_url = user_questions_endpoint(self._uq_token)
            # The bridge plugin converges dsh-side plan state to the baked
            # ``planActive`` on the first pre-step; treat it as in force
            # from spawn (wire ``plan/mode`` events keep it honest after).
            self._dsh_plan_active = session.mode == "plan"

        # The session's model_settings is the live value (PATCH /effort
        # mutates it between turns — codex reads it per turn the same way);
        # the constructor snapshot is only the fallback for callers that
        # never round-trip the session.
        model_settings = session.model_settings or self.model_settings
        self._config_path = write_composition(
            session,
            config_parent_dir=launch.config_parent_dir,
            workspace_root=self.workspace_root,
            skills_root=skills_root,
            model_settings=model_settings,
            kernel_toolkit=self._register_kernel_toolkit(session),
            plan_capable=launch.plan_capable,
            user_questions_url=user_questions_url,
        )
        self._composition_fingerprint = _composition_fingerprint(session)

        env = os.environ.copy()
        env.update(launch.env)
        env["DSH_CORDIS_CONFIG"] = self._config_path
        env["DSH_CWD"] = self.workspace_root
        if self.model_provider is not None:
            env["DEEPSEEK_API_KEY"] = self.model_provider.api_key
            if self.model_provider.base_url:
                env["DEEPSEEK_BASE_URL"] = self.model_provider.base_url

        client = DshRuntimeClient(launch.argv, cwd=launch.cwd, env=env)
        await client.start()
        max_tokens = model_settings.max_tokens if model_settings is not None else None
        try:
            await client.initialize(
                cwd=self.workspace_root or os.getcwd(),
                provider=DSH_PROVIDER_ROUTE,
                model=self.model,
                max_tokens=max_tokens,
            )
        except BaseException:
            await client.close()
            raise
        self._client = client
        if session.mcp_servers:
            try:
                grace = float(os.environ.get(MCP_READY_GRACE_ENV, DEFAULT_MCP_READY_GRACE_SEC))
            except ValueError:
                grace = DEFAULT_MCP_READY_GRACE_SEC
            if grace > 0:
                await asyncio.sleep(grace)
        self._process_turns = 0
        # Fresh native session per process: the SDK server cannot rehydrate a
        # persisted id ("id collision"), so each process gets a new thread and
        # the transcript sidecar carries the history across.
        self._native_session_id = f"{session.id}-{uuid.uuid4().hex[:8]}"
        session.runtime_session_id = self._native_session_id
        await self.event_sink.emit(
            Event(
                type="turn_phase",
                data={
                    "phase": "runtime_init",
                    "duration_ms": int((time.monotonic() - t_init) * 1000),
                },
            )
        )

    # -- transcript sidecar (cross-process continuation) --

    def _transcript_path(self, session: Session) -> Path:
        return Path(self._state_dir) / session.id / "transcript.jsonl"

    def _append_transcript(self, session: Session, role: str, text: str) -> None:
        if not text.strip():
            return
        try:
            path = self._transcript_path(session)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"role": role, "text": text}, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("deepseek_harness: transcript append failed", exc_info=True)

    def _build_replay_block(self, session: Session) -> str | None:
        try:
            path = self._transcript_path(session)
            if not path.is_file():
                return None
            entries: list[dict[str, Any]] = []
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, dict) and obj.get("text"):
                        entries.append(obj)
        except OSError:
            logger.warning("deepseek_harness: transcript read failed", exc_info=True)
            return None
        if not entries:
            return None
        entries = entries[-_REPLAY_MAX_ENTRIES:]
        lines: list[str] = []
        total = 0
        for entry in reversed(entries):
            rendered = f"[{entry.get('role', 'user')}] {entry['text']}"
            total += len(rendered)
            if total > _REPLAY_MAX_CHARS:
                break
            lines.append(rendered)
        lines.reverse()
        body = "\n".join(lines)
        return (
            "<conversation-history>\n"
            "The runtime process restarted; this is the transcript of the "
            "session so far. Continue the conversation from it.\n"
            f"{body}\n"
            "</conversation-history>"
        )


class _UserQuestionsAsk:
    """One forwarded ``ask()``: its park, decision, and terminal state."""

    def __init__(
        self,
        *,
        pending_id: str,
        subject: str,
        questions: list[dict[str, Any]],
        decision: asyncio.Future[
            tuple[Literal["approve", "reject", "answer"], str | None, dict[str, Any] | None]
        ],
        result: asyncio.Future[dict[str, Any]],
    ) -> None:
        self.pending_id = pending_id
        self.subject = subject
        self.questions = questions
        self.decision = decision
        self.result = result


class _TurnOutcome:
    """Facts collected across one owned activity interval."""

    def __init__(self) -> None:
        self.turn_end_reason: dict[str, Any] | None = None
        self.usage_totals: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "reasoning_tokens": 0,
        }
        self.last_assistant_text: str = ""
        self.last_seq: int | None = None

    def add_usage(self, usage: dict[str, int]) -> None:
        for key, value in usage.items():
            self.usage_totals[key] = self.usage_totals.get(key, 0) + value


def _composition_fingerprint(session: Session) -> str:
    """Stable digest of the session state baked into the subprocess.

    Covers what ``build_composition_rows`` and ``initialize`` read from the
    session: instructions (persona), skills, the full MCP server set
    including headers — a changed credential must change the digest — and
    ``model_settings`` (effort lands in the llm adapter row, max_tokens in
    ``initialize``), so a live-reconciled PATCH ``/effort`` reaches the
    runtime on the next turn instead of staying baked forever.
    """
    payload = json.dumps(
        {
            "instructions": session.instructions,
            "skills": list(session.skills),
            "mcp": [asdict(server) for server in session.mcp_servers],
            "model_settings": (
                asdict(session.model_settings) if session.model_settings is not None else None
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _is_inbox_receipt(event: dict[str, Any], message_id: str) -> bool:
    if event.get("type") != "agent/inbox/spliced":
        return False
    data = event.get("data")
    inserted = data.get("inserted") if isinstance(data, dict) else None
    return isinstance(inserted, list) and any(
        isinstance(message, dict) and message.get("id") == message_id for message in inserted
    )


def _stop_reason_from_turn_end(reason: dict[str, Any] | None) -> StopReason:
    """Map dsh ``turn/end`` reason kinds to kernel StopReason.

    ``max-tokens`` settles as EndTurn — the turn ended by provider policy,
    not by failure (same stance as dsh's own ACP server, which reports
    token-limit endings as ``end_turn``). A missing turn/end (idle without a
    turn) is a completed no-op interval.
    """
    kind = reason.get("kind") if reason is not None else None
    if kind == "error":
        error = reason.get("error") if reason is not None else None
        message = "turn failed"
        if isinstance(error, dict):
            message = str(error.get("message") or message)
        return Error(category="execution_error", retry_status="exhausted", message=message)
    return EndTurn()


def _usage_payload(totals: dict[str, int], model: str) -> dict[str, Any]:
    """Normalize summed dsh usage to the cross-runtime flat fields.

    dsh's TokenUsage convention is DISJOINT counts (llm-deepseek
    ``mapUsage``): ``inputTokens`` is already the UNCACHED prompt portion
    (``prompt_tokens - cached``), ``cacheReadTokens`` the cached portion, and
    ``outputTokens`` the full ``completion_tokens`` (reasoning is a detail
    sub-bucket). The flat fields therefore pass through unchanged — an
    earlier subset-style ``input - cache_read`` re-subtraction here clamped
    cached turns to input 0 / "100% hit rate" in the UI.
    """
    flat = {
        "input_tokens": totals.get("input_tokens", 0),
        "output_tokens": totals.get("output_tokens", 0),
        "cache_read_tokens": totals.get("cache_read_tokens", 0),
        "cache_write_tokens": 0,
    }
    return {
        **flat,
        "model_usage": {model: {**flat, "reasoning_tokens": totals.get("reasoning_tokens", 0)}},
    }


def _stop_reason_to_dict(reason: StopReason | None) -> dict[str, Any]:
    if reason is None:
        return {}
    return asdict(reason)

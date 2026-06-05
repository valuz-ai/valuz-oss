"""DeepAgentsRuntime — wraps langchain deepagents as a RuntimePort.

Maps AgentConfig + ToolKit → create_deep_agent(...) graph, harness Tools →
StructuredTool, SubAgentDef → SubAgent (typed dict), and streams the graph's
``astream_events`` output to harness Events.

Filesystem operations are bound to the project's cwd via FilesystemBackend
(virtual_mode=True so absolute / parent-traversal paths are rejected).

Approval contract (Phase 3 of the cross-runtime approval contract — see
``docs/design/cross-runtime-approval-contract.md`` §5.3, §9 Phase 3):
the runtime passes ``interrupt_on={tool: {"allowed_decisions":
["approve", "reject"]}}`` per registered tool when
``session.permission_mode == "default"``; after each ``astream_events``
loop we ``aget_state`` to detect HITL interrupts, emit one
``requires_action`` per ``ActionRequest`` in the single batched
interrupt, gather decisions, and resume via
``astream_events(Command(resume={"decisions": [...]}))``. The HITL
middleware batches all pending tool calls into one ``interrupt()`` per
``after_model`` and validates ``len(decisions) == len(action_requests)``
mid-graph — the gather order matches the action_requests array index
exactly. ``full_access`` mode omits the kwarg; ``auto_review`` is
hard-400'd at the route layer because DeepAgents has no classifier.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Literal

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from src.core.agent_config import AgentConfig, SubAgentDef
from src.core.approval_rule_matcher import ExactArgsRuleMatcher, RuntimeApprovalRuleMatcher
from src.core.events import AVAILABLE_DECISIONS_EDITABLE_WITH_SESSION, Event, EventSink
from src.core.rule_canonicalize import reduce_args_for_subject
from src.core.session_approval_cache import SessionRule
from src.core.tools import ExecContext, ToolDef, ToolKit, ToolResult
from src.core.types import (
    EndTurn,
    Error,
    McpStdioServerConfig,
    ModelProvider,
    ModelSettings,
    Session,
    StopReason,
    UserMessage,
)
from src.runtimes.deepagents.approval_bridge import (
    _build_pending_payload,
    _classify_subject,
)
from src.runtimes.deepagents.middleware import ToolErrorTolerantMiddleware
from src.runtimes.mcp_env import resolve_stdio_env

logger = logging.getLogger(__name__)


# Default location for the deepagents-specific checkpoint store. Kept separate
# from the harness business DB so langgraph schema migrations and our own
# alembic history don't interfere with each other. Override with env var.
DEFAULT_CHECKPOINT_DB = "./deepagents_checkpoints.db"
CHECKPOINT_DB_ENV = "DEEPAGENTS_CHECKPOINT_DB"

# langchain TodoListMiddleware tool name (auto-included by deepagents). Treated
# as a planning channel: emit `todo_update` and suppress the generic tool_use /
# tool_result pair so the UI trace doesn't double-render it.
DEEPAGENTS_TODO_TOOL_NAME = "write_todos"


class DeepAgentsRuntime:
    """Wraps deepagents `create_deep_agent` as a RuntimePort implementation."""

    def __init__(
        self,
        config: AgentConfig,
        model: str,
        event_sink: EventSink,
        toolkit: ToolKit | None = None,
        workspace_root: str = "",
        checkpoint_db: str | None = None,
        model_provider: ModelProvider | None = None,
        model_settings: ModelSettings | None = None,
    ) -> None:
        self.config = config
        self.model = model
        self.event_sink = event_sink
        self.toolkit = toolkit or ToolKit()
        self.workspace_root = workspace_root
        self.checkpoint_db = checkpoint_db or os.getenv(CHECKPOINT_DB_ENV) or DEFAULT_CHECKPOINT_DB
        self.model_provider = model_provider
        self.model_settings = model_settings
        self._graph: Any | None = None
        self._checkpointer: Any | None = None
        self._checkpointer_cm: Any | None = None
        self._active_task: asyncio.Task[Any] | None = None
        self._cancelled: bool = False
        # Identity of the session currently being run — exposed to
        # custom-tool handlers through ExecContext.
        self._cur_session_id: str = ""
        self._cur_agent_id: str = ""
        self._cur_project_id: str = ""

        # Approval bridge state (Phase 3 of the cross-runtime approval
        # contract). ``_pending_futures`` maps pending_id → future that
        # ``run()`` is parked on inside ``_await_host_decisions``;
        # ``submit_action`` resolves them. ``_cached_permission_mode`` is
        # captured at ``_ensure_graph`` time so PATCHing
        # ``session.permission_mode`` mid-turn does not retroactively
        # change ``interrupt_on`` for an already-built graph (cold-reload
        # semantics, mirrors Claude/Codex). ``_mcp_tool_names`` is the
        # set tracked at graph construction so subject classification can
        # tell MCP-origin tools from harness/built-in ones (langchain-mcp
        # tool names don't carry server prefixes — membership is the only
        # signal we have).
        # 3-tuple: ``(decision, message, modified_input)``. The 3rd slot
        # is set only when ``decision == "approve_with_changes"`` —
        # ``submit_action``'s validator pair guarantees it; carries the
        # replacement args dict for the HITL middleware's ``EditDecision``.
        self._pending_futures: dict[
            str,
            asyncio.Future[
                tuple[
                    Literal["approve", "approve_with_changes", "reject"],
                    str | None,
                    dict[str, Any] | None,
                ]
            ],
        ] = {}
        self._cached_permission_mode: Literal["default", "auto_review", "full_access"] = (
            "full_access"
        )
        # Last value actually applied to a turn. Used by ``run()`` to
        # detect a PATCH on ``session.permission_mode`` /
        # ``session.model_settings.effort`` between turns and trigger a
        # graph rebuild — see the cross-runtime "PATCH applies on next
        # turn after Send" contract in
        # ``docs/references/claude-agent-options-and-mutators.md``.
        # DeepAgents has no live mutator for either lever (both bake
        # into the langgraph at compile time), so the cheapest channel
        # is "reset ``self._graph`` and let ``_ensure_graph`` rebuild".
        self._applied_permission_mode: Literal["default", "auto_review", "full_access"] | None = (
            None
        )
        self._applied_effort: str | None = None
        self._mcp_tool_names: set[str] = set()
        # Per-session callable injected by the orchestrator via
        # ``set_session_rule_finder``. Closes over (session_id, cache,
        # this runtime's matcher) so this runtime can check the kernel
        # cache without holding a SessionOrchestrator backref. ``None``
        # until the orchestrator wires it (factory unit tests without
        # an orchestrator stay green — cache miss is the safe fallback).
        # See ``docs/design/approve-for-session.md`` §3.3 and
        # ``SessionRuleFinder`` in ``core/orchestrator.py``.
        self._session_rule_finder: (
            Callable[
                [str, str, dict[str, Any], dict[str, Any]],
                SessionRule | None,
            ]
            | None
        ) = None
        # Default matcher: exact (tool_name, canonical args) match.
        # DeepAgents has no SDK-supplied pattern grammar in v2; richer
        # matchers (Claude PermissionUpdate / codex argv) ship on those
        # runtimes' phases. Held as a single instance — matcher is
        # stateless and re-instantiation per call would be wasteful.
        self._approval_rule_matcher: RuntimeApprovalRuleMatcher = ExactArgsRuleMatcher()

    APPROVAL_TIMEOUT_SECONDS: float = 3600.0  # 1h; class attr for test override

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
        """Injected by ``SessionOrchestrator._ensure_runtime`` so this runtime
        can consult the kernel-owned approval cache before parking on a
        user decision. Set to ``None`` to disable (mainly for tests).
        """
        self._session_rule_finder = finder

    def update_sink(self, sink: EventSink) -> None:
        self.event_sink = sink

    async def run(self, session: Session, user_message: UserMessage) -> None:
        from datetime import datetime

        from src.core.prompt_builder import build_user_prompt

        session.status = "running"
        self._cancelled = False
        self._cur_session_id = session.id
        self._cur_agent_id = session.agent_id
        self._cur_project_id = session.project_id

        try:
            # Reconcile live session-driven levers BEFORE ``_ensure_graph``
            # so a PATCH on ``session.permission_mode`` /
            # ``session.model_settings.effort`` triggers a rebuild on
            # the next turn — cross-runtime "PATCH applies on next turn
            # after Send" contract. DeepAgents has no live mutator for
            # either lever (both bake into the langgraph at compile
            # time), so the cheapest channel is "drop the cached graph
            # + model client and let ``_ensure_graph`` rebuild".
            self._reconcile_session_levers(session)
            graph = await self._ensure_graph(session)
            if not session.runtime_session_id:
                session.runtime_session_id = session.id
            thread_id = session.runtime_session_id

            prompt = build_user_prompt(
                user_message,
                cwd=self.workspace_root,
                now=datetime.now().astimezone(),
            )
            stream_input: Any = {"messages": [{"role": "user", "content": prompt}]}
            stream_config: dict[str, Any] = {"configurable": {"thread_id": str(thread_id)}}

            usage_totals = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
            # run_ids of write_todos tool calls in this turn — used to drop
            # the matching on_tool_end so the UI doesn't show the duplicate
            # "Updated todo list to [...]" tool_result. Scoped at the run()
            # level so it survives across resume passes within the same turn.
            todo_run_ids: set[str] = set()

            self._active_task = asyncio.current_task()
            # Outer interrupt-resume loop. Each pass: stream events from
            # the graph until it pauses or completes; if it paused on a
            # HITL interrupt, park on host decisions and re-enter the
            # stream with Command(resume=...). The graph can interrupt
            # again on a later AI message in the same turn, so the loop
            # runs until ``state.next == ()`` (no pending tasks) or the
            # turn is cancelled. ``max_resume_iters`` is a belt-and-
            # suspenders safety net — a runaway graph that re-interrupts
            # forever should fail visibly, not loop forever.
            max_resume_iters = 32
            for _resume_iter in range(max_resume_iters):
                if self._cancelled:
                    break
                async for chunk in graph.astream_events(
                    stream_input,
                    stream_config,
                    version="v2",
                ):
                    if self._cancelled:
                        # task.cancel() should have raised CancelledError, but
                        # the langgraph stream may absorb cancellation
                        # internally. Bail out explicitly so a user-initiated
                        # stop is honored promptly even if cancellation is
                        # delayed.
                        break
                    event_name = chunk.get("event", "")
                    data = chunk.get("data", {}) or {}

                    if event_name == "on_chat_model_stream":
                        chunk_obj = data.get("chunk")
                        text = _extract_chunk_text(chunk_obj)
                        if text:
                            await self.event_sink.emit(
                                Event(type="text_delta", data={"text": text})
                            )
                        thinking_text = _extract_chunk_thinking(chunk_obj)
                        if thinking_text:
                            await self.event_sink.emit(
                                Event(type="thinking_delta", data={"text": thinking_text})
                            )

                    elif event_name == "on_chat_model_end":
                        output = data.get("output")
                        full_text = _extract_full_text(output)
                        if full_text:
                            await self.event_sink.emit(
                                Event(type="assistant_message", data={"text": full_text})
                            )
                        full_thinking = _extract_full_thinking(output)
                        if full_thinking:
                            await self.event_sink.emit(
                                Event(type="thinking", data={"text": full_thinking})
                            )
                        usage = _extract_usage(output)
                        if usage:
                            for key, val in usage.items():
                                usage_totals[key] = usage_totals.get(key, 0) + val

                    elif event_name == "on_tool_start":
                        tool_name = str(chunk.get("name", ""))
                        run_id = str(chunk.get("run_id", ""))
                        if tool_name == DEEPAGENTS_TODO_TOOL_NAME:
                            todo_run_ids.add(run_id)
                            raw_input = data.get("input", {}) or {}
                            todos = raw_input.get("todos") if isinstance(raw_input, dict) else None
                            if isinstance(todos, list):
                                await self.event_sink.emit(
                                    Event(
                                        type="todo_update",
                                        data={"todos": _jsonify(todos)},
                                    )
                                )
                        else:
                            await self.event_sink.emit(
                                Event(
                                    type="tool_use",
                                    data={
                                        "id": run_id,
                                        "name": tool_name,
                                        "input": _jsonify(data.get("input", {}) or {}),
                                    },
                                )
                            )

                    elif event_name == "on_tool_end":
                        run_id = str(chunk.get("run_id", ""))
                        if run_id in todo_run_ids:
                            # Matching write_todos call — suppress (todo_update
                            # already carried the structured payload).
                            continue
                        output = data.get("output")
                        is_error = _output_is_error(output)
                        await self.event_sink.emit(
                            Event(
                                type="tool_result",
                                data={
                                    "id": run_id,
                                    "content": _stringify_tool_output(output),
                                    "is_error": is_error,
                                },
                            )
                        )

                if self._cancelled:
                    break

                # The stream loop exited cleanly — either the graph completed
                # the turn or it paused on an interrupt. Snapshot state to
                # find out which.
                state = await graph.aget_state(stream_config)
                pending_interrupts = list(getattr(state, "interrupts", ()) or ())
                if not pending_interrupts:
                    break

                # HITL middleware emits exactly ONE batched interrupt per
                # ``after_model`` call. Its value carries N action_requests
                # — emit one ``requires_action`` per request, gather N
                # decisions, resume with one ``Command(resume=...)``.
                # ``len(decisions)`` MUST equal ``len(action_requests)`` or
                # the middleware raises ValueError mid-graph; the gather
                # below preserves that ordering by construction.
                interrupt_obj = pending_interrupts[0]
                interrupt_value = getattr(interrupt_obj, "value", None)
                if not isinstance(interrupt_value, dict):
                    logger.warning(
                        "deepagents: graph interrupt has unexpected value shape %r; "
                        "treating as turn end",
                        type(interrupt_value).__name__,
                    )
                    break
                action_requests = interrupt_value.get("action_requests") or []
                if not action_requests:
                    logger.warning(
                        "deepagents: graph interrupted with no action_requests; "
                        "treating as turn end"
                    )
                    break

                decisions = await self._await_host_decisions(action_requests)
                if decisions is None:
                    # Cancelled or timed out — pending events already sealed
                    # inside the helper; exit the outer loop without resuming
                    # so we don't keep an orphaned graph mid-interrupt.
                    break
                stream_input = Command(resume={"decisions": decisions})
            else:
                # Hit the safety cap without state settling. Surface as an
                # execution error so the operator sees the runaway.
                logger.error(
                    "deepagents: exceeded %d resume iterations; aborting turn",
                    max_resume_iters,
                )
                raise RuntimeError(
                    f"deepagents: graph re-interrupted more than {max_resume_iters} times"
                )

            if self._cancelled:
                session.stop_reason = Error(
                    category="user_interrupt",
                    retry_status="terminal",
                    message="cancelled",
                )
            else:
                session.stop_reason = EndTurn()

            session.status = "idle"
            await self._emit_usage_update(usage_totals)

        except asyncio.CancelledError:
            session.status = "idle"
            session.stop_reason = Error(
                category="user_interrupt",
                retry_status="terminal",
                message="cancelled",
            )
        except Exception as exc:
            session.status = "idle"
            session.stop_reason = Error(
                category="execution_error",
                retry_status="exhausted",
                message=str(exc),
            )
            await self.event_sink.emit(Event(type="session_error", data={"message": str(exc)}))
            if self.config.hooks:
                await self.config.hooks.fire("on_error", error=exc, session_id=session.id)
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

    async def interrupt(self) -> None:
        self._cancelled = True
        # Seal pending approvals before cancelling the task: cheap
        # ``set_result`` first so the parked ``_await_host_decisions``
        # gather unblocks immediately even if the sink chain stalls
        # (DB locked / bus saturated), then emit synthetic
        # ``action_resolved`` so the events log stays consistent with the
        # outbound bus. Mirror of Claude/Codex ``interrupt()`` semantics.
        for pending_id, future in list(self._pending_futures.items()):
            if future.done():
                continue
            future.set_result(("reject", "session interrupted", None))
            await self._emit_synthetic_resolved(pending_id, "interrupted")
        self._pending_futures.clear()
        task = self._active_task
        if task is not None and not task.done():
            task.cancel()

    async def submit_action(
        self,
        pending_id: str,
        decision: Literal["approve", "approve_with_changes", "reject", "answer"],
        message: str | None = None,
        answers: dict[str, str | list[str]] | None = None,
        modified_input: dict[str, Any] | None = None,
    ) -> None:
        """Resolve the pending future ``_await_host_decisions`` is parked on.

        The orchestrator already validates idempotency / conflict / expired
        against the events log before reaching us — so a missing or
        already-done future means a true race (e.g. interrupt fired then
        user clicked) and we just return; the orchestrator surfaces the
        appropriate state via its own checks on the next call.

        ``decision="answer"`` is reserved for ``clarifying_questions``
        pendings (Claude SDK ``AskUserQuestion``). DeepAgents doesn't
        emit that subject — the orchestrator's subject↔decision invariant
        rejects mismatches at 400 before reaching us — so receiving it
        here means a contract violation upstream. Raise
        ``NotImplementedError`` defensively; the orchestrator translates
        it to a 501 ``ApprovalNotImplementedError``.

        ``modified_input`` is only ever non-None for
        ``decision == "approve_with_changes"`` (Pydantic validator + the
        orchestrator's ``available_decisions`` gate enforce both halves).
        """
        if decision == "answer":
            raise NotImplementedError(
                "DeepAgentsRuntime does not emit 'clarifying_questions' subjects; "
                "decision='answer' is claude_agent-only in v1."
            )
        # ``answers`` is forbidden by the SubmitActionRequest validator
        # for non-answer decisions; silent drop is safer than crashing.
        _ = answers
        future = self._pending_futures.get(pending_id)
        if future is None or future.done():
            return
        future.set_result((decision, message, modified_input))

    async def close(self) -> None:
        # Defensive — any pending future left here means the SDK or test
        # tore down without going through ``interrupt()``. Clear the map
        # so the next instance starts clean; we don't bother emitting
        # synthetic ``action_resolved`` since close() implies the bus +
        # sink chain are also being torn down.
        self._pending_futures.clear()
        self._graph = None
        self._checkpointer = None
        if self._checkpointer_cm is not None:
            try:
                await self._checkpointer_cm.__aexit__(None, None, None)
            except Exception:
                logger.debug("Error closing deepagents checkpointer", exc_info=True)
            self._checkpointer_cm = None
        self._active_task = None

    # -- Approval bridge --

    async def _await_host_decisions(
        self,
        action_requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Park on N futures, one per ``ActionRequest``. Returns
        ``decisions[]`` aligned to ``action_requests[]`` when the host
        resolves all, or ``None`` when cancelled / timed out so the caller
        breaks out of the resume loop instead of feeding rejects back to
        the graph.

        ``decisions[i]`` order is enforced by construction — we iterate
        ``action_requests`` once, building parallel ``pending_ids`` and
        ``futures`` arrays, and gather in the same order. The HITL
        middleware validates ``len(decisions) == len(action_requests)``
        mid-graph (raises ``ValueError`` otherwise), so any partial
        result here would be a hard error on resume.
        """
        pending_ids: list[str] = []
        tool_names: list[str] = []
        futures: list[
            asyncio.Future[
                tuple[
                    Literal["approve", "approve_with_changes", "reject"],
                    str | None,
                    dict[str, Any] | None,
                ]
            ]
        ] = []
        loop = asyncio.get_event_loop()
        for action_request in action_requests:
            pending_id = str(uuid.uuid4())

            tool_name = str(action_request.get("name", ""))
            tool_names.append(tool_name)
            args = action_request.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            subject = _classify_subject(tool_name, self._mcp_tool_names)
            payload = _build_pending_payload(subject, tool_name, args, self.workspace_root)

            # v2 approve_for_session: derive the rule preview from this
            # runtime's matcher so the host UI can label the "Always for
            # this session" button accurately. ``runtime_extras`` is the
            # SDK-specific context the matcher consults — empty for the
            # exact-match default; Claude phase populates
            # ``claude_permission_updates`` here.
            #
            # Phase 4: reduce args per-subject *before* feeding the
            # matcher. For ``mcp_tool_call`` the reduction drops args
            # entirely so a stored rule covers any future call to the
            # same MCP tool regardless of arguments — see
            # ``rule_canonicalize.reduce_args_for_subject`` for the per-
            # subject identity table.
            runtime_extras: dict[str, Any] = {}
            reduced_args, subject_display = reduce_args_for_subject(subject, tool_name, args)
            derivation = self._approval_rule_matcher.derive_rule(
                subject, tool_name, reduced_args, runtime_extras
            )
            # The exact-args matcher's default display is generic
            # ("this exact <tool> call"); for MCP / file_change we want
            # the subject-aware label ("any X call" / "X on /path").
            # DeepAgents has no SDK pattern grammar so every derivation
            # is exact-kind — override unconditionally.
            session_rule_preview = {
                "kind": derivation.kind,
                "runtime_kind": derivation.runtime_kind,
                "display": subject_display,
                "rule_data": derivation.rule_data,
            }

            # Cache check happens BEFORE parking. On hit, we emit the
            # requires_action for audit-trail uniformity (every approval
            # site emits one) immediately followed by a synthetic
            # action_resolved(auto_approved). The graph still resumes
            # with a plain approve at the SDK boundary.
            cache_hit = self._check_session_rule(subject, tool_name, reduced_args, runtime_extras)

            pending_data: dict[str, Any] = {
                "pending_id": pending_id,
                "subject": subject,
                "runtime_provider": "deepagents",
                # DeepAgents only emits tool-approval subjects (no
                # clarifying_questions), so every pending advertises
                # the editable + session verb set. The HITL middleware's
                # ``allowed_decisions`` at the SDK boundary stays
                # ``["approve", "edit", "reject"]`` — ``approve_for_session``
                # is kernel-only, translated to ``approve`` before the
                # decision reaches the middleware.
                "available_decisions": list(AVAILABLE_DECISIONS_EDITABLE_WITH_SESSION),
                "payload": payload,
                "session_rule_preview": session_rule_preview,
            }
            await self.event_sink.emit(Event(type="requires_action", data=pending_data))

            future: asyncio.Future[
                tuple[
                    Literal["approve", "approve_with_changes", "reject"],
                    str | None,
                    dict[str, Any] | None,
                ]
            ] = loop.create_future()

            if cache_hit is not None:
                # Synthetic auto-approve — bypass the orchestrator's
                # submit_action path entirely (no user round-trip needed).
                # Emit action_resolved directly via the sink so it lands
                # on bus + DB. The pre-resolved future feeds the gather
                # loop with the standard ("approve", None, None) tuple,
                # keeping the translation path below uniform.
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
                future.set_result(("approve", None, None))
            else:
                # User round-trip path — orchestrator's submit_action
                # resolves the future on POST /actions.
                self._pending_futures[pending_id] = future

            pending_ids.append(pending_id)
            futures.append(future)

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*futures),
                timeout=self.APPROVAL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            # All pendings timed out together — emit synthetic ``expired``
            # for each still-unsealed future + drop them from the map.
            for pid, fut in zip(pending_ids, futures, strict=True):
                if not fut.done():
                    await self._emit_synthetic_resolved(pid, "expired")
                self._pending_futures.pop(pid, None)
            return None
        except asyncio.CancelledError:
            # interrupt() injected — pendings already sealed + emitted
            # inside that path; just propagate so the outer try/except
            # CancelledError finishes the turn.
            raise

        # Happy path: orchestrator emits ``action_resolved`` for each via
        # the REST entry, so we only clear our local map here.
        for pid in pending_ids:
            self._pending_futures.pop(pid, None)

        # Translate (decision, message, modified_input) tuples to the HITL
        # middleware's Decision TypedDict shape. The middleware's reject
        # branch already injects a synthetic ``ToolMessage(status="error",
        # content=decision.message or default)`` back into the
        # conversation so the model sees the rejection reason — no v2
        # "reject-with-redirect" overlap. The ``edit`` branch replaces
        # the tool call args with ``modified_input`` (we keep the
        # original tool name from ``action_requests[i]`` since v1 only
        # supports arg edits, not tool renames).
        decisions: list[dict[str, Any]] = []
        for (decision, message, modified_input), tool_name in zip(results, tool_names, strict=True):
            if decision == "approve":
                decisions.append({"type": "approve"})
            elif decision == "approve_with_changes":
                # Validator + orchestrator guarantee modified_input is
                # non-None here; fall back to the original action request
                # args (effectively a plain approve) so a missing payload
                # doesn't blow up the middleware's edit branch.
                edited_args = modified_input if modified_input is not None else {}
                decisions.append(
                    {
                        "type": "edit",
                        "edited_action": {"name": tool_name, "args": edited_args},
                    }
                )
            else:
                d: dict[str, Any] = {"type": "reject"}
                if message:
                    d["message"] = message
                decisions.append(d)
        return decisions

    def _check_session_rule(
        self,
        subject: str,
        tool_name: str,
        args: dict[str, Any],
        runtime_extras: dict[str, Any],
    ) -> SessionRule | None:
        """Consult the kernel session-rule cache via the injected finder.

        Returns ``None`` when no finder is wired (factory unit tests, or
        Phase < 2 wiring), when the finder raises (logged and treated as
        a miss — never block the approval flow on a cache failure), or
        when no stored rule matches.
        """
        finder = self._session_rule_finder
        if finder is None:
            return None
        try:
            return finder(subject, tool_name, args, runtime_extras)
        except Exception:
            logger.exception(
                "deepagents: session rule check failed for %s; treating as miss",
                tool_name,
            )
            return None

    async def _emit_synthetic_resolved(self, pending_id: str, decision: str) -> None:
        """Used for runtime-side resolutions (timeout / interrupt) where
        the orchestrator isn't involved. The event_sink chain writes to
        DB + bus, so the events log stays consistent for the next
        ``_derive_pending`` lookup. ``message`` is always ``None`` for
        synthetic resolutions — mirrors the orchestrator's user-resolved
        emit shape for schema consistency.
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
            # Sink failures shouldn't bubble out — the future has either
            # already been sealed (interrupt path) or is about to be
            # (timeout path). Log so the gap is visible in operations
            # rather than silently lost.
            logger.exception(
                "deepagents: failed to emit synthetic action_resolved for %s",
                pending_id,
            )

    # -- Graph construction --

    def _reconcile_session_levers(self, session: Session) -> None:
        """Drop ``self._graph`` if ``session.permission_mode`` or
        ``session.model_settings.effort`` changed since the last
        ``_ensure_graph`` build, so the next turn rebuilds with the
        fresh values — cross-runtime "PATCH applies on next turn after
        Send" contract.

        Both levers bake into the langgraph at compile time (effort →
        langchain chat model client kwargs; permission_mode →
        ``interrupt_on`` dict for the HITL middleware), so DeepAgents
        cannot do a "live mutator" like Claude's
        ``set_permission_mode``. The only path is to invalidate the
        cached graph and let ``_ensure_graph`` re-run on the next
        turn.

        Costs of the rebuild: roughly the same as session re-creation
        from the runtime's perspective (re-wire MCP, re-instantiate
        langchain client). The checkpointer + persistent ``thread_id``
        preserve conversation history; only the in-memory graph
        re-compiles.

        No-ops on the first turn (``_graph is None``) AND on any turn
        where the snapshots are still uninitialised — the latter case
        is a defensive guard for test setups that pre-populate
        ``self._graph`` directly without going through
        ``_ensure_graph``; the snapshot pair stays ``None / None`` in
        that case and we must not flag the live session values as
        "changed".
        """
        if self._graph is None:
            return
        # Snapshots are seeded inside ``_ensure_graph``; ``None`` means
        # the graph wasn't built via the normal path (most often a test
        # that mocked ``self._graph``). Reconcile only after we have a
        # real "previously applied" value to compare against.
        if self._applied_permission_mode is None and self._applied_effort is None:
            return

        new_mode = session.permission_mode
        new_effort = session.model_settings.effort if session.model_settings else None

        if new_mode == self._applied_permission_mode and new_effort == self._applied_effort:
            return

        # Drop the cached graph so ``_ensure_graph`` rebuilds cleanly.
        # The langchain checkpointer remains open — conversation
        # history survives because ``stream_config`` still carries the
        # same ``thread_id``.
        self._graph = None

    async def _ensure_graph(self, session: Session) -> Any:
        if self._graph is not None:
            return self._graph

        backend = (
            LocalShellBackend(root_dir=self.workspace_root)
            if self.workspace_root
            else LocalShellBackend()
        )

        tools = self._build_tools()
        mcp_tools = await self._build_mcp_tools(session)
        # Capture MCP-origin tool names *before* concatenating so subject
        # classification can distinguish MCP tool calls from harness/builtin
        # ones at approval time. langchain-mcp's ``MultiServerMCPClient``
        # does not prefix tool names with their server (unlike Claude's
        # ``mcp__<server>__<tool>`` convention), so name-membership is our
        # only signal for MCP origin.
        self._mcp_tool_names = {t.name for t in mcp_tools if hasattr(t, "name")}
        if mcp_tools:
            tools = [*tools, *mcp_tools]

        subagents = self._build_subagents()
        await self._open_checkpointer()

        skill_roots = self._materialize_skills(session)

        # D9: session is the runtime's source of truth for ``permission_mode``;
        # the agent value was prefilled at session creation but is decoupled
        # afterwards. We cache it on the instance so the ``run()`` outer
        # interrupt-resume loop can tell whether to expect a parked-on-host
        # interrupt (only ``default`` does that). PATCHing
        # ``session.permission_mode`` between turns: ``_reconcile_session_levers``
        # drops ``self._graph`` so this build runs again on the next turn
        # with the fresh value — cross-runtime "PATCH applies on next
        # turn after Send" contract.
        self._cached_permission_mode = session.permission_mode
        self._applied_permission_mode = session.permission_mode
        self._applied_effort = session.model_settings.effort if session.model_settings else None
        interrupt_on = self._build_interrupt_on(session.permission_mode, tools)

        graph_kwargs: dict[str, Any] = {
            "model": self._build_model_client(session),
            "tools": tools,
            "subagents": subagents or None,
            "backend": backend,
            "checkpointer": self._checkpointer,
            "middleware": [ToolErrorTolerantMiddleware()],
        }
        # DeepAgents prepends our ``system_prompt`` argument to its base
        # prompt; we pass the per-session ``instructions`` straight through
        # (the agent's instructions only seeds the session at creation; the
        # session is the runtime's source of truth).
        if session.instructions:
            graph_kwargs["system_prompt"] = session.instructions
        if skill_roots:
            graph_kwargs["skills"] = skill_roots
        # Only pass ``interrupt_on`` when ``default`` actually has tools to
        # gate — empty dict + ``full_access`` are equivalent (middleware
        # treats no-entry as auto-approve), and omitting the kwarg keeps
        # the kwargs surface clean for the no-approvals case.
        if interrupt_on:
            graph_kwargs["interrupt_on"] = interrupt_on

        self._graph = create_deep_agent(**graph_kwargs)
        return self._graph

    def _build_interrupt_on(
        self,
        permission_mode: Literal["default", "auto_review", "full_access"],
        tools: list[Any],
    ) -> dict[str, dict[str, list[str]]]:
        """Build the ``interrupt_on`` dict for ``create_deep_agent``.

        Per-tool ``{"allowed_decisions": ["approve", "edit", "reject"]}``
        caps the HITL middleware's accepted decision surface to our v1
        contract — ``"edit"`` matches the SDK's ``EditDecision`` literal,
        which is what our ``approve_with_changes`` verb maps to at the
        SDK boundary (see ``_await_host_decisions``). ``auto_review`` is
        hard-400'd at the route layer; we treat it defensively here as
        ``full_access`` (empty dict) so an unexpected mode never produces
        an undefined interrupt configuration.
        """
        if permission_mode != "default":
            return {}
        # The HITL middleware's ``allowed_decisions`` uses ``"edit"`` as
        # the SDK-native verb name; our public contract calls it
        # ``approve_with_changes`` (see
        # ``AVAILABLE_DECISIONS_EDITABLE``). The translation happens in
        # ``_await_host_decisions`` when building the resume payload —
        # this list is the inverse mapping for the SDK boundary.
        allowed: list[str] = ["approve", "edit", "reject"]
        return {t.name: {"allowed_decisions": allowed} for t in tools if hasattr(t, "name")}

    def _build_model_client(self, session: Session) -> Any:
        """Build a langchain chat model bound to the per-session gateway.

        DeepAgents requires an explicit model client (the factory enforces
        non-None ``model_provider`` + non-empty ``model`` before we get
        here), and the runtime maps ``api_protocol`` to the matching
        langchain backend:

        * ``"openai_completion"`` -> ``ChatOpenAI``    (OpenAI chat completions)
        * ``"anthropic"``         -> ``ChatAnthropic`` (Anthropic Messages API)
        * ``"gemini"``            -> ``ChatGoogleGenerativeAI``

        Reasoning effort is threaded through per-backend with the SDK's
        native parameter name — see ``_map_effort_for_*`` helpers for
        the cross-runtime mapping table.
        """
        if self.model_provider is None:
            # Defensive: factory should have rejected this already.
            raise RuntimeError(
                "DeepAgentsRuntime requires a model_provider; the factory "
                "should have rejected this session before instantiation."
            )
        protocol = self.model_provider.api_protocol
        # Read effort live from the session (NOT ``self.model_settings``,
        # which is a snapshot captured at runtime construct time). The
        # graph is rebuilt on PATCH via ``_reconcile_session_levers``,
        # so this method re-runs with the fresh value on the next turn.
        effort = session.model_settings.effort if session.model_settings is not None else None
        from pydantic import SecretStr

        if protocol == "anthropic":
            from langchain_anthropic import ChatAnthropic

            # ChatAnthropic streams already include usage on the final
            # AIMessageChunk — no opt-in flag needed. ``effort`` is
            # natively supported across the full union ``low|medium|
            # high|xhigh|max``. Only forward ``base_url`` when the
            # operator supplied one — first-party (``None``) lets
            # ChatAnthropic use its baked-in ``api.anthropic.com``.
            kwargs: dict[str, Any] = dict(
                api_key=SecretStr(self.model_provider.api_key),
                model_name=self.model,
                timeout=None,
                stop=None,
            )
            if self.model_provider.base_url is not None:
                kwargs["base_url"] = self.model_provider.base_url
            if effort is not None:
                kwargs["effort"] = effort
            return ChatAnthropic(**kwargs)

        if protocol == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI

            # Gemini's ``thinking_level`` accepts ``minimal|low|medium|
            # high``; ``xhigh`` and ``max`` both map down to ``high``.
            gemini_kwargs: dict[str, Any] = dict(
                model=self.model,
                google_api_key=SecretStr(self.model_provider.api_key),
            )
            if self.model_provider.base_url:
                # langchain-google-genai exposes the gateway base URL via
                # the underlying client_options; only set when the
                # operator supplied one.
                gemini_kwargs["client_options"] = {
                    "api_endpoint": self.model_provider.base_url,
                }
            if effort is not None:
                gemini_kwargs["thinking_level"] = _map_effort_for_gemini(effort)
            return ChatGoogleGenerativeAI(**gemini_kwargs)

        # openai_completion path (default for any non-anthropic /
        # non-gemini protocol). ``ChatOpenAI.reasoning_effort`` accepts
        # ``none|minimal|low|medium|high|xhigh`` — the harness ``max``
        # maps down to ``xhigh``.
        from langchain_openai import ChatOpenAI

        # TODO: remove this DeepSeek workaround once
        # https://github.com/langchain-ai/langchain/pull/37065 ships and
        # langchain-openai's ``ChatOpenAI`` handles the ``thinking`` chunk
        # shape DeepSeek-style providers emit. Until then we explicitly
        # disable reasoning output so the stream parses cleanly. We use a
        # substring match (case-insensitive) on the model name to also
        # catch aggregator-style aliases like ``volcengine/deepseek-r1``
        # or ``together/deepseek-v3``.
        extra_body = (
            {"thinking": {"type": "disabled"}} if "deepseek" in self.model.lower() else None
        )

        # ``base_url`` is only forwarded when the operator supplied one;
        # passing ``None`` to ChatOpenAI is technically a no-op (its
        # default is ``api.openai.com/v1``) but staying explicit keeps
        # the first-party-vs-gateway branch obvious at the call site.
        openai_kwargs: dict[str, Any] = dict(
            api_key=SecretStr(self.model_provider.api_key),
            model=self.model,
            # OpenAI-compatible streams omit usage by default; opt in so
            # `usage_metadata` lands on the final AIMessageChunk and our
            # `usage_update` event has real numbers.
            stream_usage=True,
            extra_body=extra_body,
        )
        if self.model_provider.base_url is not None:
            openai_kwargs["base_url"] = self.model_provider.base_url
        if effort is not None:
            openai_kwargs["reasoning_effort"] = _map_effort_for_openai(effort)
        return ChatOpenAI(**openai_kwargs)

    async def _open_checkpointer(self) -> Any:
        """Open the SQLite-backed checkpointer once per runtime instance.

        Survives a process restart: thread-id (= session.id) plus this on-disk
        store let langgraph rehydrate the conversation state on the next turn.
        The CM is held until ``close()`` so the cached graph keeps a live
        connection.
        """
        if self._checkpointer is not None:
            return self._checkpointer
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        directory = os.path.dirname(self.checkpoint_db)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._checkpointer_cm = AsyncSqliteSaver.from_conn_string(self.checkpoint_db)
        self._checkpointer = await self._checkpointer_cm.__aenter__()
        await self._checkpointer.setup()
        return self._checkpointer

    def _materialize_skills(self, session: Session) -> list[str]:
        if not self.workspace_root or not session.skills:
            return []
        from src.runtimes.skills_materialize import prepare_deepagents_skills

        root = prepare_deepagents_skills(self.workspace_root, list(session.skills))
        return [root]

    async def _build_mcp_tools(self, session: Session) -> list[Any]:
        """Fetch tools from session.mcp_servers via langchain MCP adapter.

        langchain calls the http transport ``streamable_http``; the kernel API
        uses ``http`` — translate here. Returns an empty list when no MCP
        servers are configured or the adapter is unavailable.
        """
        if not session.mcp_servers:
            return []
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError:
            logger.warning(
                "session.mcp_servers configured but langchain_mcp_adapters is not installed; "
                "skipping."
            )
            return []

        spec: dict[str, dict[str, Any]] = {}
        for cfg in session.mcp_servers:
            if isinstance(cfg, McpStdioServerConfig):
                # LangChain StdioConnection requires args even when empty;
                # env_vars resolution happens harness-side via the shared
                # resolver so the SDK only sees a flat env dict. Omit
                # ``env`` when the user supplied nothing so LangChain /
                # the underlying stdio_client inherits the parent env
                # naturally (HOME, PATH, etc. — needed by ``npx``-style
                # commands).
                stdio_entry: dict[str, Any] = {
                    "transport": "stdio",
                    "command": cfg.command,
                    "args": list(cfg.args),
                }
                env = resolve_stdio_env(cfg)
                if env is not None:
                    stdio_entry["env"] = env
                spec[cfg.name] = stdio_entry
                continue
            # LangChain calls our ``http`` ``streamable_http``.
            transport = "streamable_http" if cfg.transport == "http" else cfg.transport
            entry: dict[str, Any] = {"transport": transport, "url": cfg.url}
            if cfg.headers:
                entry["headers"] = dict(cfg.headers)
            spec[cfg.name] = entry

        client = MultiServerMCPClient(spec)  # type: ignore[arg-type]
        return list(await client.get_tools())

    # -- Tool conversion --

    def _build_tools(self) -> list[StructuredTool]:
        tools: list[StructuredTool] = []
        for tdef in self.toolkit.list_tools():
            if tdef.handler is None or tdef.permission == "deny":
                continue
            tools.append(self._to_structured_tool(tdef))
        return tools

    def _to_structured_tool(self, tdef: ToolDef) -> StructuredTool:
        captured_handler = tdef.handler
        captured_workspace = self.workspace_root
        captured_hooks = self.config.hooks
        tool_name = tdef.name

        async def _coroutine(**kwargs: Any) -> str:
            assert captured_handler is not None
            if captured_hooks and captured_hooks._handlers.get("before_tool"):
                hr = await captured_hooks.fire("before_tool", tool_name=tool_name, input=kwargs)
                if hr.action == "block":
                    raise RuntimeError(hr.reason or f"Tool {tool_name} blocked by hook")
            result: ToolResult = await captured_handler(
                kwargs,
                ExecContext(
                    workspace=captured_workspace,
                    session_id=self._cur_session_id,
                    agent_id=self._cur_agent_id,
                    project_id=self._cur_project_id,
                ),
            )
            if captured_hooks and captured_hooks._handlers.get("after_tool"):
                await captured_hooks.fire(
                    "after_tool", tool_name=tool_name, input=kwargs, result=result
                )
            return result.content

        return StructuredTool.from_function(
            coroutine=_coroutine,
            name=tdef.name,
            description=tdef.description or tdef.name,
            args_schema=tdef.parameters or None,
        )

    def _build_subagents(self) -> list[SubAgent]:
        subagents: list[SubAgent] = []
        for sub_def in self.config.callable_agents:
            subagents.append(self._to_subagent(sub_def))
        return subagents

    def _to_subagent(self, sub_def: SubAgentDef) -> SubAgent:
        sub_tools: list[StructuredTool] = []
        if sub_def.tools:
            for name in sub_def.tools:
                tdef = self.toolkit.get(name)
                if tdef and tdef.handler and tdef.permission != "deny":
                    sub_tools.append(self._to_structured_tool(tdef))

        entry: SubAgent = {
            "name": sub_def.name,
            "description": sub_def.description,
            "system_prompt": sub_def.prompt,
        }
        if sub_tools:
            entry["tools"] = sub_tools
        if sub_def.model:
            entry["model"] = sub_def.model
        if sub_def.skills:
            entry["skills"] = list(sub_def.skills)
        return entry

    # -- Usage --

    async def _emit_usage_update(self, totals: dict[str, int]) -> None:
        """Emit the per-run usage summary; one ``usage_update`` per turn."""
        if not any(totals.values()):
            return
        payload: dict[str, Any] = dict(totals)
        payload["model_usage"] = {self.model: dict(totals)}
        # cost_usd: deepagents has no first-party cost; left absent so callers
        # can distinguish "not provided" from a real zero.
        await self.event_sink.emit(Event(type="usage_update", data=payload))


def _map_effort_for_openai(effort: str) -> str:
    """Map harness effort literal to ``ChatOpenAI.reasoning_effort``.

    Accepts ``none|minimal|low|medium|high|xhigh``. Harness ``max`` is
    Anthropic-only — map down to OpenAI's highest ``xhigh``. Unknown
    values fall back to ``medium`` defensively.
    """
    if effort == "max":
        return "xhigh"
    if effort in {"low", "medium", "high", "xhigh"}:
        return effort
    return "medium"


def _map_effort_for_gemini(effort: str) -> str:
    """Map harness effort literal to ``ChatGoogleGenerativeAI.thinking_level``.

    Gemini accepts ``minimal|low|medium|high`` only. Harness ``xhigh``
    and ``max`` both clamp to ``high``. Unknown values fall back to
    ``medium``.
    """
    if effort in {"xhigh", "max"}:
        return "high"
    if effort in {"low", "medium", "high"}:
        return effort
    return "medium"


def _extract_full_text(output: Any) -> str:
    """Extract the assembled text content from an end-of-message AIMessage."""
    if output is None:
        return ""
    return _extract_chunk_text(output)


def _extract_chunk_text(chunk: Any) -> str:
    if chunk is None:
        return ""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                # Skip reasoning/thinking blocks here — they go to the
                # thinking channel via _extract_chunk_thinking.
                if part.get("type") in {"thinking", "reasoning"}:
                    continue
                text_value = part.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return ""


# Reasoning/thinking content can show up under several langchain shapes
# depending on the provider and SDK version. We probe all known places
# so the harness surfaces it consistently regardless of upstream wiring:
#   - ``chunk.content`` list items with ``type in {"thinking","reasoning"}``
#     and a payload keyed as ``thinking`` / ``reasoning`` / ``text``
#   - ``chunk.additional_kwargs["reasoning_content"]`` (str delta)
#   - ``chunk.additional_kwargs["thinking"]`` (str or dict with ``content``)
def _extract_chunk_thinking(chunk: Any) -> str:
    if chunk is None:
        return ""
    parts: list[str] = []

    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") not in {"thinking", "reasoning"}:
                continue
            for key in ("thinking", "reasoning", "text"):
                value = part.get(key)
                if isinstance(value, str) and value:
                    parts.append(value)
                    break

    extra = getattr(chunk, "additional_kwargs", None)
    if isinstance(extra, dict):
        reasoning_content = extra.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            parts.append(reasoning_content)
        thinking_field = extra.get("thinking")
        if isinstance(thinking_field, str) and thinking_field:
            parts.append(thinking_field)
        elif isinstance(thinking_field, dict):
            inner = thinking_field.get("content") or thinking_field.get("text")
            if isinstance(inner, str) and inner:
                parts.append(inner)

    return "".join(parts)


def _extract_full_thinking(output: Any) -> str:
    """Extract the assembled thinking text from an end-of-message AIMessage."""
    if output is None:
        return ""
    return _extract_chunk_thinking(output)


def _extract_usage(output: Any) -> dict[str, int] | None:
    """Project LangChain's ``UsageMetadata`` onto our four flat token fields.

    LangChain has no notion of cache_write/creation, so cache_write_tokens
    is always zero on the deepagents path.
    """
    if output is None:
        return None
    metadata = getattr(output, "usage_metadata", None)
    if isinstance(metadata, dict):
        cache_read = 0
        cache_write = 0
        details = metadata.get("input_token_details")
        if isinstance(details, dict):
            cache_read = int(details.get("cache_read", 0) or 0)
            cache_write = int(details.get("cache_creation", 0) or 0)
        return {
            "input_tokens": int(metadata.get("input_tokens", 0)),
            "output_tokens": int(metadata.get("output_tokens", 0)),
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
        }
    response_metadata = getattr(output, "response_metadata", None)
    if isinstance(response_metadata, dict):
        token_usage = response_metadata.get("token_usage") or {}
        if isinstance(token_usage, dict) and token_usage:
            return {
                "input_tokens": int(token_usage.get("prompt_tokens", 0)),
                "output_tokens": int(token_usage.get("completion_tokens", 0)),
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
    return None


def _output_is_error(output: Any) -> bool:
    status = getattr(output, "status", None)
    if status == "error":
        return True
    return False


def _stringify_tool_output(output: Any) -> str:
    if output is None:
        return ""
    content = getattr(output, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p) for p in content)
    return str(output)


def _stop_reason_to_dict(reason: StopReason | None) -> dict[str, Any]:
    if reason is None:
        return {}
    return asdict(reason)


def _jsonify(value: Any) -> Any:
    """Force a JSON-serializable copy of ``value``.

    langgraph's astream_events v2 passes ``uuid.UUID`` for ``run_id`` and may
    embed Pydantic models in tool inputs — both blow up Python's stdlib
    ``json.dumps``. SQLAlchemy's JSON column uses that serializer at INSERT
    time, so any non-clean value silently breaks event persistence even though
    the same payload streamed fine over WebSocket. Round-tripping with
    ``default=str`` ensures the dict that lands in the DB matches what the WS
    saw.
    """
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return str(value)

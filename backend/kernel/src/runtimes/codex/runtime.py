"""CodexRuntime — wraps OpenAI Codex Python SDK as a RuntimePort.

Per ``docs/design/CODEX-INTEGRATION-DESIGN.md`` +
``docs/design/cross-runtime-approval-contract.md`` Phase 2:

* one ``AsyncCodex`` per Session, lazy-spawned on the first turn
* ``Session.instructions`` -> ``thread_start(developer_instructions=...)``
* ``Session.mcp_servers`` -> ``CodexConfig.config_overrides``
  (the per-thread ``ThreadStartParams.config`` dict silently drops
  unknown keys; ``--config k=v`` process flags work — see spike
  ``docs/archive/codex-spike/spike_mcp_via_config_overrides_v2.py``).
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
import logging
import os
import shutil
import uuid
from collections.abc import AsyncGenerator, Callable
from dataclasses import asdict
from typing import Any, Literal, cast

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
from src.core.tools import ExecContext, ToolKit
from src.core.types import (
    BudgetExhausted,
    EndTurn,
    Error,
    McpStdioServerConfig,
    ModelProvider,
    ModelSettings,
    Session,
    StopReason,
    UserMessage,
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


class CodexRuntime:
    """Wraps the Codex SDK (``AsyncCodex`` + ``AsyncThread``) as a RuntimePort."""

    def __init__(
        self,
        config: AgentConfig,
        model: str,
        event_sink: EventSink,
        toolkit: ToolKit | None = None,
        workspace_root: str = "",
        model_provider: ModelProvider | None = None,
        model_settings: ModelSettings | None = None,
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

        self._codex: AsyncCodex | None = None
        self._thread: AsyncThread | None = None
        self._active_turn: AsyncTurnHandle | None = None
        self._active_task: asyncio.Task[Any] | None = None
        self._cancelled: bool = False
        # Tracks whether this runtime registered a toolkit endpoint so
        # ``close()`` can revoke it without needing the session reference.
        self._registered_session_id: str | None = None

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

    async def run(self, session: Session, user_message: UserMessage) -> None:
        from datetime import datetime

        from src.core.prompt_builder import build_user_prompt

        session.status = "running"
        self._cancelled = False

        try:
            self._loop = asyncio.get_running_loop()
            self._materialize_skills(session)

            await self._ensure_codex(session)
            assert self._codex is not None
            await self._ensure_thread(session)
            assert self._thread is not None

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

            self._active_task = asyncio.current_task()
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
            turn_resp = await self._codex._client.turn_start(
                self._thread.id,
                prompt,
                TurnStartParams(thread_id=self._thread.id, input=[turn_input], **turn_kwargs),
            )
            self._active_turn = AsyncTurnHandle(self._codex, self._thread.id, turn_resp.turn.id)

            completed: TurnCompletedNotification | None = None
            error_message: str | None = None
            usage_payload: dict[str, Any] | None = None

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
                        usage_payload = _usage_payload_from_token_usage(
                            usage.token_usage, self.model
                        )

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
                        # final usage from ``Turn`` itself when the running
                        # token-usage stream didn't catch the last update.
                        if usage_payload is None:
                            usage_payload = _empty_usage_payload(self.model)
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
            else:
                session.status = "idle"
                session.stop_reason = EndTurn()

            if usage_payload is not None:
                await self.event_sink.emit(Event(type="usage_update", data=usage_payload))

        except asyncio.CancelledError:
            session.status = "idle"
            session.stop_reason = Error(
                category="user_interrupt",
                retry_status="terminal",
                message="cancelled",
            )
        except Exception as exc:
            session.status = "idle"
            if self._cancelled:
                session.stop_reason = Error(
                    category="user_interrupt",
                    retry_status="terminal",
                    message="cancelled",
                )
            else:
                session.stop_reason = Error(
                    category="execution_error",
                    retry_status="exhausted",
                    message=str(exc),
                )
                await self.event_sink.emit(Event(type="session_error", data={"message": str(exc)}))
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

    async def close(self) -> None:
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

    async def _ensure_codex(self, session: Session) -> None:
        if self._codex is not None:
            return
        expose_toolkit = self._register_toolkit_if_eligible(session)
        cfg = CodexConfig(
            codex_bin=_resolve_codex_bin(),
            config_overrides=_build_config_overrides(
                session,
                self.model_provider,
                self.model,
                expose_toolkit=expose_toolkit,
            ),
            env=_build_codex_env(self.model_provider),
        )
        self._codex = AsyncCodex(config=cfg)
        await self._codex.__aenter__()
        self._install_approval_handler()

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
                agent_id=session.agent_id,
                project_id=session.project_id,
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
            return
        started = await self._codex._client.thread_start(ThreadStartParams(**common))
        self._thread = AsyncThread(self._codex, started.thread.id)
        # Persist the freshly-allocated thread id for future resumes.
        session.runtime_session_id = self._thread.id

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


def _build_config_overrides(
    session: Session,
    provider: ModelProvider | None,
    model: str,
    *,
    expose_toolkit: bool = False,
) -> tuple[str, ...]:
    """Serialize per-session config to ``--config k=v`` strings.

    Three channels are emitted:

    1. ``Session.mcp_servers`` -> ``mcp_servers.<name>.{url|command,...}``.
       Codex auto-detects transport: presence of ``command`` -> stdio,
       presence of ``url`` -> remote HTTP. Stdio entries emit
       ``command`` / ``args`` / ``env_vars`` / ``[mcp_servers.X.env]``
       per the Codex TOML spec; ``env_vars`` is passed through verbatim
       so codex resolves it against its own process env at child-spawn
       time (token values therefore never appear in the overrides
       string).
    2. ``Session.model_provider`` -> a synthetic ``[model_providers.harness]``
       block plus ``model = "..."`` / ``model_provider = "harness"`` so the
       codex subprocess routes through the user-supplied gateway.
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
                overrides.append(f"mcp_servers.{cfg.name}.env_vars={_toml_array(cfg.env_vars)}")
            if cfg.env:
                inline = ", ".join(f"{k} = {_toml_quote(v)}" for k, v in cfg.env.items())
                overrides.append(f"mcp_servers.{cfg.name}.env={{ {inline} }}")
            continue
        overrides.append(f"mcp_servers.{cfg.name}.url={_toml_quote(cfg.url)}")
        if cfg.headers:
            inline = ", ".join(f"{k} = {_toml_quote(v)}" for k, v in cfg.headers.items())
            overrides.append(f"mcp_servers.{cfg.name}.http_headers={{ {inline} }}")

    if expose_toolkit:
        base = os.getenv(CODEX_TOOLKIT_BASE_URL_ENV) or CODEX_TOOLKIT_BASE_URL_DEFAULT
        toolkit_url = f"{base.rstrip('/')}/mcp/toolkit/{session.id}"
        overrides.append(f"mcp_servers.{_HARNESS_TOOLKIT_MCP_NAME}.url={_toml_quote(toolkit_url)}")

    # NB: ``model_reasoning_effort`` is intentionally NOT emitted as a
    # server-level override here. Codex pins ``reasoning_effort`` into
    # thread metadata at ``thread_start`` and ``ThreadResumeParams`` has
    # no effort field, so a config-override-only path would silently
    # ignore edits made after the first turn. The effort lever lives in
    # ``_build_turn_kwargs`` (``TurnStartParams.effort``), which
    # documented semantics override per-turn-and-subsequent — that path
    # works regardless of whether the thread was just started or resumed.

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

    if provider is not None and provider.base_url is not None:
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
                f"model_providers.{name}.base_url={_toml_quote(provider.base_url)}",
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

    return tuple(overrides)


def _build_codex_env(
    provider: ModelProvider | None,
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
    """
    if provider is None:
        return None
    merged: dict[str, str] = dict(os.environ)
    if provider.base_url is not None:
        merged[_HARNESS_PROVIDER_ENV_KEY] = provider.api_key
    else:
        merged["OPENAI_API_KEY"] = provider.api_key
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
    # ``in_progress`` should not appear on a turn/completed notification, but
    # surface as a budget-exhausted-ish marker rather than silently.
    return BudgetExhausted(reason="max_turns")


def _usage_payload_from_token_usage(usage: Any, model: str) -> dict[str, Any]:
    """Project Codex's ``ThreadTokenUsage.total`` onto our four flat fields.

    Codex's ``TokenUsageBreakdown`` exposes ``cached_input_tokens`` (read
    cache) but no separate write-cache counter; cache_write is left at 0
    (same shape as DeepAgents). ``reasoning_output_tokens`` is preserved
    in ``model_usage`` for forward compatibility.
    """
    total = usage.total
    flat = {
        "input_tokens": int(total.input_tokens or 0),
        "output_tokens": int(total.output_tokens or 0),
        "cache_read_tokens": int(total.cached_input_tokens or 0),
        "cache_write_tokens": 0,
    }
    payload: dict[str, Any] = dict(flat)
    payload["model_usage"] = {
        model: {
            **flat,
            "reasoning_output_tokens": int(total.reasoning_output_tokens or 0),
            "total_tokens": int(total.total_tokens or 0),
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

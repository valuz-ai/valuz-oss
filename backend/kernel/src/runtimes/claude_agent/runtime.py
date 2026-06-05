"""ClaudeAgentRuntime — wraps Claude Agent SDK as RuntimePort implementation.

Maps AgentConfig → ClaudeAgentOptions, ToolKit → MCP Server,
SubAgentDef → AgentDefinition, Harness Hooks → SDK HookMatcher,
and SDK Messages → Harness Events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any, Literal

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SdkMcpTool,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolAnnotations,
    ToolResultBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
)
from claude_agent_sdk import (
    UserMessage as SdkUserMessage,
)
from claude_agent_sdk import (
    tool as sdk_tool,
)
from claude_agent_sdk._errors import ProcessError
from claude_agent_sdk.types import (
    HookContext,
    HookInput,
    SandboxSettings,
    StreamEvent,
    SyncHookJSONOutput,
    SystemPromptPreset,
    ToolPermissionContext,
)
from claude_agent_sdk.types import (
    McpHttpServerConfig as SdkMcpHttpServerConfig,
)
from claude_agent_sdk.types import (
    McpSSEServerConfig as SdkMcpSSEServerConfig,
)
from claude_agent_sdk.types import (
    McpStdioServerConfig as SdkMcpStdioServerConfig,
)

from src.core.agent_config import AgentConfig
from src.core.approval_rule_matcher import (
    ClaudePermissionUpdateRuleMatcher,
    ExactArgsRuleMatcher,
    RuntimeApprovalRuleMatcher,
    _permission_update_to_dict,
)
from src.core.events import (
    AVAILABLE_DECISIONS_CLARIFYING,
    AVAILABLE_DECISIONS_EDITABLE_WITH_SESSION,
    AVAILABLE_DECISIONS_V1,
    Event,
    EventSink,
)
from src.core.hooks import Hooks
from src.core.rule_canonicalize import reduce_args_for_subject
from src.core.session_approval_cache import SessionRule
from src.core.tools import ExecContext, ToolDef, ToolKit, ToolResult
from src.core.types import (
    BudgetExhausted,
    EndTurn,
    Error,
    McpServerConfig,
    McpStdioServerConfig,
    ModelProvider,
    ModelSettings,
    Session,
    UserMessage,
)

# Approval bridge — pure helpers live in ``approval_bridge.py``; we
# re-export them here so existing call sites importing from
# ``runtime.py`` (e.g. tests written before the split) keep working.
# ``_summarize_file_change`` is intentionally NOT re-exported — it's a
# private helper inside ``_build_pending_payload`` and has no callers
# outside the bridge module.
from src.runtimes.claude_agent.approval_bridge import (
    _build_pending_payload,
    _classify_subject,
)
from src.runtimes.mcp_env import resolve_stdio_env

logger = logging.getLogger(__name__)


# How many recent CLI-stderr lines to attach to ``session_error``
# payloads. The SDK's ProcessError carries no real stderr (just the
# placeholder string ``"Check stderr output for details"``); we wire
# ``ClaudeAgentOptions.stderr`` to a callback that buffers up to this
# many lines and surfaces them on failure so the actual CLI message
# reaches the user instead of being trapped on the backend's inherited
# stderr stream. 40 lines is enough for a typical Rust / node panic
# trace without flooding event payloads.
_STDERR_TAIL_LINES: int = 40


# Workflows contributor to ``_build_settings``. Dynamic workflows /
# ``/deep-research`` are a Pro research-preview opt-in (``enableWorkflows``)
# stored in the *user* surface (``~/.claude/settings.json`` /
# ``tengu_workflows_enabled`` in ``~/.claude.json``). Because the harness
# scopes sessions to ``setting_sources=["project"]`` (no user surface, by
# design — see ``_build_options``), that opt-in never loads and the bundled
# workflow commands stay unregistered ("Unknown command: /deep-research").
# Re-assert it as a harness default so sessions get workflows enabled
# WITHOUT re-inheriting the rest of the user's personal CLI state, and
# without writing a settings file into the user's project tree.
# See ``docs/references/claude-workflows-spike/README.md``.
_WORKFLOW_SETTINGS: dict[str, Any] = {"enableWorkflows": True}


# B2a: ``claude_agent + auto_review`` sessions fail at the first turn
# when the underlying Claude tier doesn't grant access to Anthropic's
# ``auto`` permission_mode (their LLM-classifier review surface). The
# audit (`docs/design/cross-runtime-approval-contract.md` §11 R3)
# deferred adding a separate preflight probe — too noisy at session
# create, extra cost on every first turn. Instead, classify the existing
# first-turn ``ProcessError`` so the user sees an actionable category +
# message instead of a generic execution_error blob.
#
# The patterns below match the most plausible substrings Claude Code
# CLI emits on stderr when its permission_mode arg is rejected. They are
# explicitly narrow — anything not matching falls through to
# execution_error, so this never *adds* false positives, only refines
# the existing failure path. ``in`` is case-sensitive; we lowercase the
# stderr once for the match.
_TIER_MISMATCH_PATTERNS: tuple[str, ...] = (
    # "permission_mode 'auto' is not supported on your plan"
    # "the 'auto' permission_mode requires Claude Pro"
    "permission_mode",
    # "auto-review is not available"
    "auto-review",
    # CLI may emit "Claude Pro subscription required" / "upgrade your plan"
    "subscription required",
    "upgrade your plan",
    # Generic "plan does not support" phrasing — kept last so it doesn't
    # eat plain "default plan" mentions; pairs with one of the above for
    # disambiguation isn't worth it given the next two lines, but we keep
    # this as a defensive backup.
    "plan does not support",
)


def _classify_first_turn_error(
    exc: BaseException,
    permission_mode: Literal["default", "auto_review", "full_access"],
) -> Error | None:
    """Return a structured ``Error`` if ``exc`` looks like an auto_review
    tier mismatch, else ``None`` so the caller falls through to the
    existing ``execution_error`` path.

    Pinned narrow: only fires for ``auto_review`` sessions and only when
    a known tier-mismatch substring appears in the SDK's stderr (or
    ``str(exc)`` if stderr is absent). Auth errors (401 / "Unauthorized")
    are not classified here — they affect all permission_modes equally
    and deserve their own category in a separate change.
    """
    if permission_mode != "auto_review":
        return None
    haystack = ""
    if isinstance(exc, ProcessError) and exc.stderr:
        haystack = exc.stderr
    else:
        haystack = str(exc)
    haystack_lower = haystack.lower()
    if not any(p in haystack_lower for p in _TIER_MISMATCH_PATTERNS):
        return None
    return Error(
        category="tier_mismatch",
        retry_status="terminal",
        message=(
            "auto_review mode is unavailable on this Claude tier. "
            "Recreate the session with permission_mode='default' or 'full_access', "
            "or upgrade your Claude plan."
        ),
    )


PERMISSION_MAP: dict[
    str,
    Literal[
        "default",
        "acceptEdits",
        "plan",
        "bypassPermissions",
        "dontAsk",
        "auto",
    ],
] = {
    # 3-preset surface (D1) → claude-agent-sdk permission_mode strings.
    # ``default`` keeps SDK semantics — host's ``can_use_tool`` callback
    # decides per tool (slice 3). ``auto_review`` maps to claude's own
    # ``auto`` mode (separate classifier). ``full_access`` maps to
    # ``bypassPermissions`` (skip every check).
    "default": "default",
    "auto_review": "auto",
    "full_access": "bypassPermissions",
}

MODEL_MAP: dict[str, str] = {
    "claude-opus-4-6": "opus",
    "claude-sonnet-4-6": "sonnet",
    "claude-haiku-4-5": "haiku",
}

# Built-in Claude SDK tool that the agent uses to maintain a structured todo
# list. We treat it as a planning channel: emit a `todo_update` event from
# its input and suppress the generic tool_use/tool_result pair so the UI
# trace doesn't double-render it.
CLAUDE_TODO_TOOL_NAME = "TodoWrite"

# Dynamic-workflow progress surfacing. The ``Workflow`` tool launches a run in a
# background runtime and returns immediately; ``/workflows`` (the live TUI) is
# unreachable through the SDK. The launch tool_result is the only place the run's
# artifact paths appear:
#   Transcript dir: <session_dir>/subagents/workflows/<runId>  — holds
#       ``journal.jsonl``, appended LIVE as each subagent starts/finishes. It is
#       written regardless of permission mode, so it works in ``full_access``
#       (bypassPermissions), where the ``can_use_tool`` callback never fires.
#       This is the live progress source.
#   Script file:    <session_dir>/workflows/scripts/<name>-<runId>.js — the
#       workflow script; emitted to the UI for the save/edit-for-reuse flow.
#   Summary:        the workflow's one-line description.
# The end-of-run result file ``<session_dir>/workflows/<runId>.json`` (rich:
# phases + per-agent + token/tool totals + status) is written only on
# termination — used for the final snapshot, NOT for live progress.
# See ``docs/references/claude-workflows-spike/README.md``.
CLAUDE_WORKFLOW_TOOL_NAME = "Workflow"
_WORKFLOW_TRANSCRIPT_RE = re.compile(
    r"Transcript dir:\s*(?P<tdir>\S+/subagents/workflows/(?P<run>wf_[\w-]+))"
)
_WORKFLOW_SCRIPT_RE = re.compile(r"Script file:\s*(?P<spath>\S+/workflows/scripts/\S+\.js)")
_WORKFLOW_SUMMARY_RE = re.compile(r"Summary:\s*(?P<summary>.+)")
_WORKFLOW_POLL_INTERVAL_S = 2.0

# ``_to_thinking_config`` was removed on 2026-05-12 along with the
# explicit ``thinking=`` kwarg to ``ClaudeAgentOptions`` — runtimes now
# let the SDK use its own thinking default. ``AgentConfig.thinking``
# still exists on the dataclass for backward-compat round-tripping, but
# this runtime ignores it. Re-introduce a converter here if a future
# product decision asks for explicit thinking control again.


class ClaudeAgentRuntime:
    """Wraps Claude Agent SDK (ClaudeSDKClient) as a RuntimePort implementation."""

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
        self.toolkit = toolkit or ToolKit()
        self.workspace_root = workspace_root
        self.model_provider = model_provider
        self.model_settings = model_settings
        self._client: ClaudeSDKClient | None = None
        self._active_client: ClaudeSDKClient | None = None
        # tool_use ids for TodoWrite calls in the current turn — used to
        # drop the matching ToolResultBlock from the outbound stream.
        self._todo_tool_use_ids: set[str] = set()
        # Maps content_block index -> (tool_use_id, tool_name) for the
        # current turn so ``input_json_delta`` chunks (which only carry an
        # index, not the id) can be routed to the right tool_use_id when
        # we emit ``tool_input_delta``.
        self._tool_block_by_index: dict[int, tuple[str, str]] = {}
        # tool_use ids for ``Workflow`` calls in the current turn, and the
        # background poll tasks streaming their ``workflow_progress``. Both are
        # per-turn scoped and torn down in ``run()``'s finally.
        self._workflow_tool_use_ids: set[str] = set()
        self._workflow_pollers: list[asyncio.Task[None]] = []
        # Launch descriptors for active workflow runs, for the final snapshot
        # read at turn teardown (the run's result file may land just as the
        # turn ends, after the poller's last tick).
        self._active_workflows: list[dict[str, Any]] = []
        # Set by ``interrupt()`` so the run loop's broad except can
        # distinguish a user-initiated cancel (→ ``user_interrupt``,
        # status ``cancelled``) from a real execution error.
        self._cancelled: bool = False
        # Rolling buffer of the CLI subprocess's stderr lines, bounded
        # so a chatty subprocess doesn't grow unbounded memory between
        # crashes. The SDK only pipes stderr when ``ClaudeAgentOptions.stderr``
        # is set; we wire ``_on_stderr_line`` there and include the
        # most-recent ``_STDERR_TAIL_LINES`` in ``session_error`` payloads
        # so users see the actual CLI failure mode instead of the SDK's
        # placeholder ``"Check stderr output for details"`` (verbatim
        # string from claude_agent_sdk/_internal/transport/subprocess_cli.py
        # — there is no actual stderr in the ProcessError).
        self._stderr_buffer: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        # Identity of the session currently being run — exposed to
        # custom-tool handlers through ExecContext.
        self._cur_session_id: str = ""
        self._cur_agent_id: str = ""
        self._cur_project_id: str = ""
        # The task running ``run()``. Cancelled by ``interrupt()`` so the
        # iterator unblocks even when ``receive_response().__anext__`` is
        # waiting on the SDK subprocess for the next chunk.
        self._active_task: asyncio.Task[Any] | None = None

        # Slice 3 — approval contract.
        # ``_pending_futures`` maps pending_id → asyncio.Future that
        # ``_permission_handler`` is parked on. ``submit_action``
        # resolves the future to a 4-tuple ``(decision, message,
        # answers, modified_input)``; the handler reconstructs a
        # ``PermissionResultAllow / Deny`` from it. Slots 3+4 are
        # mutually exclusive (validator-enforced upstream):
        # - ``answers`` set iff ``decision == "answer"`` (Claude SDK's
        #   ``AskUserQuestion`` clarifying-questions tool, see
        #   https://code.claude.com/docs/en/agent-sdk/user-input.md);
        #   carries ``{question: label | [labels]}`` for
        #   ``updated_input.answers``.
        # - ``modified_input`` set iff ``decision ==
        #   "approve_with_changes"``; carries the replacement tool args
        #   dict for ``PermissionResultAllow(updated_input=...)``.
        # ``_cached_permission_mode`` is captured at ``_build_options``
        # time so the SDK callback (which doesn't get session passed in)
        # knows whether it should park or auto-allow. PATCHing
        # session.permission_mode mid-turn does not retroactively change
        # the cached value (cold-reload semantics, per design doc).
        self._pending_futures: dict[
            str,
            asyncio.Future[
                tuple[
                    Literal["approve", "approve_with_changes", "reject", "answer"],
                    str | None,
                    dict[str, str | list[str]] | None,
                    dict[str, Any] | None,
                ]
            ],
        ] = {}
        self._cached_permission_mode: Literal["default", "auto_review", "full_access"] = (
            "full_access"
        )
        # Last value actually applied to a turn. Used by ``run()`` to
        # detect a PATCH on ``session.permission_mode`` /
        # ``session.model_settings.effort`` between turns and trigger
        # the cheapest channel each SDK exposes — see the cross-runtime
        # "PATCH applies on next turn after Send" contract in
        # ``docs/references/claude-agent-options-and-mutators.md``.
        # Initialised ``None`` so the first turn always sees a "change"
        # and the initial mode is applied via ``set_permission_mode``
        # rather than relying on the value already in options (which it
        # already is — the live mutator call on the first turn is a
        # no-op).
        self._applied_permission_mode: Literal["default", "auto_review", "full_access"] | None = (
            None
        )
        self._applied_effort: str | None = None
        # Slice 5 of session-modes: tracks the session.mode value last
        # applied to the live SDK client. ``None`` until the first spawn
        # so the first ``_reconcile_session_levers`` pass always seeds
        # via ``_build_options``. Transitions are driven from session
        # state at turn start (cross-runtime "PATCH applies on next turn
        # after Send" contract); see docs/design/session-modes.md §Per-runtime.
        self._applied_mode: Literal["default", "plan", "goal"] | None = None
        # Current-turn session reference, set at ``run()`` entry and
        # cleared on exit. The SDK ``can_use_tool`` callback runs
        # without session context, so ``_on_exit_plan_mode_approved``
        # needs this stash to flip ``session.mode`` + emit
        # ``mode_changed{by:"runtime"}``. Treat as read/write only
        # within a single ``run()`` invocation — not safe across turns.
        self._session: Session | None = None
        # One-shot flag set by ``_reconcile_session_levers`` when the
        # next spawn must fork (new SDK session id) rather than
        # continuing the resumed one. Required when transitioning INTO
        # ``bypassPermissions`` / ``dontAsk``: the Claude CLI on
        # ``--resume`` silently honors the resumed session's ORIGINAL
        # permission_mode and ignores the new ``--permission-mode``
        # flag, so without forking the rebuilt client would still
        # route every tool call through ``can_use_tool``. With
        # ``fork_session=True`` the SDK loads the prior conversation
        # but starts a fresh session id — the new permission_mode
        # binds to that fresh id. Consumed (and cleared) by the next
        # ``_build_options`` call.
        self._fork_next_spawn: bool = False
        # Phase 3 — approve_for_session matcher. Uses the SDK's
        # ``ToolPermissionContext.suggestions`` to derive pattern-grammar
        # rules (``Bash(npm test:*)``, ``Edit(src/**/*.ts)``,
        # ``mcp__server__tool``); falls back to exact-match when the
        # SDK has no proposal for the current call. See
        # ``docs/design/approve-for-session.md`` §5.1.
        self._approval_rule_matcher: RuntimeApprovalRuleMatcher = (
            ClaudePermissionUpdateRuleMatcher()
        )
        # Per-session callable injected by ``SessionOrchestrator._ensure_runtime``.
        # ``None`` until wired (factory unit tests stay green — cache
        # miss is the safe fallback).
        self._session_rule_finder: (
            Callable[
                [str, str, dict[str, Any], dict[str, Any]],
                SessionRule | None,
            ]
            | None
        ) = None

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
        self._session = session
        # Per-turn state — todo tool_use_ids are scoped to a single
        # query()/receive_response() cycle.
        self._todo_tool_use_ids = set()
        self._tool_block_by_index = {}
        self._workflow_tool_use_ids = set()
        self._workflow_pollers = []
        self._active_workflows = []
        self._cur_session_id = session.id
        self._cur_agent_id = session.agent_id
        self._cur_project_id = session.project_id
        self._cancelled = False
        # Reset stderr buffer so any ``session_error`` from this turn
        # carries only this turn's CLI output, not noise from prior
        # successful turns rolling in the deque.
        self._stderr_buffer.clear()

        try:
            # Reconcile live session-driven levers BEFORE the client-
            # spawn check so an effort change can trigger a cold reload
            # (destroy + rebuild) cleanly. Reads ``session`` live each
            # turn — see the cross-runtime "PATCH on next turn" contract.
            await self._reconcile_session_levers(session)

            if self._client is None:
                self._materialize_skills(session)
                opts = self._build_options(session)
                self._client = ClaudeSDKClient(options=opts)
                await self._client.__aenter__()
                # Initial spawn: ``_build_options`` already baked the
                # current session values in, so seed the trackers.
                self._applied_permission_mode = session.permission_mode
                self._applied_effort = (
                    session.model_settings.effort if session.model_settings else None
                )
                self._applied_mode = session.mode

            prompt = build_user_prompt(
                user_message,
                cwd=self.workspace_root,
                now=datetime.now().astimezone(),
            )
            self._active_client = self._client
            self._active_task = asyncio.current_task()
            await self._client.query(prompt)
            async for msg in self._client.receive_response():
                if self._cancelled:
                    # interrupt() was called — the SDK's internal queue
                    # may still hold many buffered StreamEvents (especially
                    # with partial messages on); drop them so the user sees
                    # the agent stop immediately.
                    break
                await self._handle_message(session, msg)
            # Slice 5 of session-modes: after the goal-mode turn's
            # ResultMessage lands, Claude doesn't surface a "goal
            # cleared" notification (spike-confirmed). Probe bare
            # ``/goal`` — local + ~3 ms + zero-cost — and if it
            # reports "No goal set", flip the session out of goal
            # mode and emit ``mode_changed{by: "runtime"}``. Probe
            # errors must not corrupt the just-completed turn.
            if not self._cancelled and session.mode == "goal" and self._client is not None:
                try:
                    await self._maybe_emit_goal_auto_exit(session)
                except Exception:
                    logger.exception("claude_agent: goal auto-exit probe failed")
            if self._cancelled:
                session.status = "idle"
                session.stop_reason = Error(
                    category="user_interrupt",
                    retry_status="terminal",
                    message="cancelled",
                )
                await self._destroy_client()
        except asyncio.CancelledError:
            # interrupt() called task.cancel() to unblock the SDK iterator
            # when it was waiting on the subprocess for the next chunk.
            session.status = "idle"
            session.stop_reason = Error(
                category="user_interrupt",
                retry_status="terminal",
                message="cancelled",
            )
            await self._destroy_client()
        except Exception as exc:
            session.status = "idle"
            if self._cancelled:
                # User-initiated interrupt — surface as a cancellation so
                # the message lands as ``cancelled`` rather than ``errored``
                # and the front-end shows the same shape DeepAgents uses.
                session.stop_reason = Error(
                    category="user_interrupt",
                    retry_status="terminal",
                    message="cancelled",
                )
            else:
                # B2a (`docs/design/cross-runtime-approval-contract.md`
                # §11 R3): for ``auto_review`` sessions, the most common
                # first-turn failure is "this Claude tier doesn't grant
                # access to permission_mode='auto'". Classify these
                # narrowly so the user sees ``category="tier_mismatch"``
                # + an actionable message; everything else falls through
                # to the existing generic ``execution_error`` shape.
                classified = _classify_first_turn_error(exc, self._cached_permission_mode)
                # Snapshot stderr buffer for inclusion in the event
                # payload. The SDK's ``ProcessError`` carries a stub
                # ``"Check stderr output for details"`` rather than real
                # CLI output, so the rolling buffer is the only path
                # for the actual error reason to reach the user. Empty
                # tail is fine — non-ProcessError exceptions (Cancelled,
                # network, etc.) don't populate it.
                stderr_tail = list(self._stderr_buffer)
                if classified is not None:
                    session.stop_reason = classified
                    await self.event_sink.emit(
                        Event(
                            type="session_error",
                            data={
                                "category": classified.category,
                                "message": classified.message,
                                "raw": str(exc),
                                "stderr_tail": stderr_tail,
                            },
                        )
                    )
                else:
                    session.stop_reason = Error(
                        category="execution_error",
                        retry_status="exhausted",
                        message=str(exc),
                    )
                    await self.event_sink.emit(
                        Event(
                            type="session_error",
                            data={
                                "message": str(exc),
                                "stderr_tail": stderr_tail,
                            },
                        )
                    )
                # Drop buffered stderr now that it's been surfaced — a
                # subsequent reconcile + rebuild attempt should start
                # fresh so its session_error (if any) carries only its
                # own stderr.
                self._stderr_buffer.clear()
                if self.config.hooks:
                    await self.config.hooks.fire("on_error", error=exc, session_id=session.id)
            await self._destroy_client()
        finally:
            await self._stop_workflow_pollers()
            self._active_client = None
            self._active_task = None

    # -- Dynamic-workflow progress --

    async def _start_workflow_poller(self, tool_use_id: str, result_content: str) -> None:
        """Parse a ``Workflow`` launch tool_result, emit the run's script for the
        UI's save/edit flow, and spawn a poller that streams live progress off the
        run's ``journal.jsonl``. No-op when the artifact paths can't be parsed —
        the run still proceeds, just without live progress.
        """
        launch = self._parse_workflow_launch(result_content)
        if launch is None:
            logger.debug("claude_agent: unparseable Workflow tool_result; no progress")
            return
        self._active_workflows.append({"tool_use_id": tool_use_id, **launch})
        # Emit the script + an initial running snapshot once. The script content
        # is for the upper system to display / edit / save (workflows
        # save-for-reuse); subsequent progress ticks omit it (the UI merges).
        await self._emit_workflow_state(
            tool_use_id,
            launch["run_id"],
            {
                "runId": launch["run_id"],
                "workflowName": launch["summary"] or None,
                "status": "running",
                "agentCount": 0,
                "agentsDone": 0,
                "workflowProgress": [],
                "scriptPath": launch["script_path"],
                "script": self._read_text(launch["script_path"]),
            },
        )
        self._workflow_pollers.append(
            asyncio.create_task(
                self._poll_workflow(
                    tool_use_id,
                    launch["run_id"],
                    launch["journal_path"],
                    launch["state_path"],
                    launch["summary"],
                )
            )
        )

    async def _poll_workflow(
        self,
        tool_use_id: str,
        run_id: str,
        journal_path: str,
        state_path: str,
        summary: str,
    ) -> None:
        """Stream live progress off ``journal.jsonl`` (per-agent started/result
        events, appended live and written regardless of permission mode). Once the
        run writes its terminal ``wf_<id>.json`` result file, emit that richer
        snapshot and stop. Cancelled at turn teardown. Emits only on change.
        """
        last_sig: tuple[Any, ...] | None = None
        try:
            while True:
                final = self._read_workflow_state(state_path)
                if final is not None:
                    await self._emit_workflow_state(tool_use_id, run_id, final)
                    return
                state = self._derive_live_state(run_id, summary, self._read_journal(journal_path))
                sig = (state["agentCount"], state["agentsDone"])
                if sig != last_sig:
                    last_sig = sig
                    await self._emit_workflow_state(tool_use_id, run_id, state)
                await asyncio.sleep(_WORKFLOW_POLL_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("claude_agent: workflow progress poller failed for %s", run_id)

    async def _emit_workflow_state(
        self, tool_use_id: str, run_id: str, state: dict[str, Any]
    ) -> None:
        await self.event_sink.emit(
            Event(
                type="workflow_progress",
                data={"id": tool_use_id, "run_id": run_id, "state": state},
            )
        )

    @staticmethod
    def _parse_workflow_launch(content: str) -> dict[str, str] | None:
        """Pull the run's live-artifact paths from a ``Workflow`` launch
        tool_result. Returns None unless both the transcript dir (live journal)
        and the script path are present."""
        tm = _WORKFLOW_TRANSCRIPT_RE.search(content)
        sm = _WORKFLOW_SCRIPT_RE.search(content)
        if tm is None or sm is None:
            return None
        run_id = tm.group("run")
        spath = sm.group("spath")
        # ``…/workflows/scripts/<x>.js`` -> result file ``…/workflows/<run>.json``
        workflows_dir = os.path.dirname(os.path.dirname(spath))
        summ = _WORKFLOW_SUMMARY_RE.search(content)
        return {
            "run_id": run_id,
            "summary": summ.group("summary").strip() if summ else "",
            "script_path": spath,
            "journal_path": os.path.join(tm.group("tdir"), "journal.jsonl"),
            "state_path": os.path.join(workflows_dir, f"{run_id}.json"),
        }

    @staticmethod
    def _derive_live_state(
        run_id: str, summary: str, events: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Fold ``journal.jsonl`` ``started`` / ``result`` events into a live
        progress snapshot: one entry per agent, ``done`` once its result lands."""
        order: list[str] = []
        done: set[str] = set()
        for e in events:
            aid = e.get("agentId")
            if not isinstance(aid, str):
                continue
            if aid not in order:
                order.append(aid)
            if e.get("type") == "result":
                done.add(aid)
        return {
            "runId": run_id,
            "workflowName": summary or None,
            "status": "running",
            "agentCount": len(order),
            "agentsDone": len(done),
            "workflowProgress": [
                {
                    "type": "workflow_agent",
                    "agentId": aid,
                    "state": "done" if aid in done else "progress",
                }
                for aid in order
            ],
        }

    @staticmethod
    def _read_journal(journal_path: str) -> list[dict[str, Any]]:
        """Read ``journal.jsonl`` (append-only; a trailing partial line is
        skipped). Missing/unreadable file -> empty list."""
        try:
            with open(journal_path, encoding="utf-8") as f:
                raw = f.read().splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in raw:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out

    @staticmethod
    def _read_text(path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    @staticmethod
    def _read_workflow_state(state_path: str) -> dict[str, Any] | None:
        """Load the end-of-run result JSON (``wf_<id>.json``). Tolerant of an
        absent (not-yet-written) / partial / unreadable file -> ``None``."""
        try:
            with open(state_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    async def _stop_workflow_pollers(self) -> None:
        """Cancel pollers, then emit a final snapshot per active run — the result
        file may land just as the turn ends, after the poller's last tick. If it
        hasn't, mark the last live snapshot ``completed`` so the UI doesn't stay
        stuck on ``running`` after ``session_idle``."""
        pollers = self._workflow_pollers
        active = self._active_workflows
        self._workflow_pollers = []
        self._active_workflows = []
        for task in pollers:
            task.cancel()
        if pollers:
            await asyncio.gather(*pollers, return_exceptions=True)
        for wf in active:
            try:
                final = self._read_workflow_state(wf["state_path"])
                if final is None:
                    final = self._derive_live_state(
                        wf["run_id"], wf["summary"], self._read_journal(wf["journal_path"])
                    )
                    final["status"] = "completed"
                await self._emit_workflow_state(wf["tool_use_id"], wf["run_id"], final)
            except Exception:
                logger.debug("claude_agent: final workflow snapshot failed", exc_info=True)

    async def submit_action(
        self,
        pending_id: str,
        decision: Literal["approve", "approve_with_changes", "reject", "answer"],
        message: str | None = None,
        answers: dict[str, str | list[str]] | None = None,
        modified_input: dict[str, Any] | None = None,
    ) -> None:
        """Resolve the approval future the SDK callback is parked on.

        The orchestrator already validates idempotency / conflict /
        expired and the subject↔decision invariant
        (``answer`` ↔ ``clarifying_questions``,
        ``approve_with_changes`` ↔ tool-approval subjects whose
        ``available_decisions`` exposes the verb) before reaching us — so
        a missing or already-done future means a true race (e.g.
        timeout fired then user clicked) and we just return; the
        orchestrator will surface the appropriate state via its own
        checks on the next call.

        ``answers`` is only ever non-None when ``decision == "answer"``;
        ``modified_input`` is only ever non-None when ``decision ==
        "approve_with_changes"`` (enforced by ``SubmitActionRequest``
        validator + orchestrator).
        """
        future = self._pending_futures.get(pending_id)
        if future is None or future.done():
            return
        future.set_result((decision, message, answers, modified_input))

    async def interrupt(self) -> None:
        self._cancelled = True
        # Slice 3 — seal pending approvals: cheap ``set_result`` first
        # so the SDK callback unblocks immediately even if the sink
        # chain hangs (e.g. DB locked); the ``action_resolved`` event
        # still flushes to the WS bus + DB, just second. Reverse order
        # would risk leaving the SDK blocked on a parked future
        # indefinitely if ``event_sink.emit`` ever stalls. Clarifying
        # pendings are sealed as ``reject`` (not ``answer``) so the SDK
        # gets a clean Deny rather than a half-formed allow with no
        # answers.
        for pending_id, future in list(self._pending_futures.items()):
            if future.done():
                continue
            future.set_result(("reject", "session interrupted", None, None))
            await self._emit_synthetic_resolved(pending_id, "interrupted")
        self._pending_futures.clear()
        if self._active_client is not None:
            try:
                await self._active_client.interrupt()
            except Exception:
                logger.debug("SDK interrupt failed", exc_info=True)
        # Forcefully unblock the receive_response iterator. The SDK's own
        # interrupt() may queue cancellation but not break the awaiting
        # ``__anext__``; cancelling the task injects CancelledError at the
        # next await point so the loop exits promptly.
        task = self._active_task
        if task is not None and not task.done():
            task.cancel()

    async def close(self) -> None:
        await self._destroy_client()

    async def _destroy_client(self) -> None:
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            finally:
                self._client = None
                self._active_client = None

    def _on_stderr_line(self, line: str) -> None:
        """Callback wired into ``ClaudeAgentOptions.stderr``. Pushes the
        CLI subprocess's stderr lines into a bounded rolling buffer so
        a chatty subprocess can't grow unbounded memory; only the most
        recent ``_STDERR_TAIL_LINES`` are kept.

        The buffer is surfaced in ``session_error`` payloads under the
        ``stderr_tail`` key and cleared after each emit. The SDK's
        ``ProcessError.stderr`` is a hardcoded placeholder string
        (``"Check stderr output for details"``) — without this
        callback the actual CLI failure mode is only visible in the
        backend's inherited stderr terminal, not in the event stream
        the front-end consumes.
        """
        # ``line`` already has its trailing newline stripped by the SDK's
        # TextReceiveStream; keep it as-is for round-trip fidelity.
        self._stderr_buffer.append(line)

    async def _reconcile_session_levers(self, session: Session) -> None:
        """Apply live-PATCHed ``session.permission_mode`` /
        ``session.model_settings.effort`` to the SDK client before the
        next turn — the cross-runtime "PATCH applies on next turn
        after Send" contract.

        Cheapest channel per field:

        * ``permission_mode`` — ``ClaudeSDKClient.set_permission_mode()``
          is a true live mutator (no client restart). Also updates
          ``_cached_permission_mode`` so the sync ``_permission_handler``
          callback (which doesn't receive ``session``) sees the new
          mode on the next tool-approval prompt.
        * ``effort`` — no live mutator exists on the Claude SDK. The
          only path is to destroy the client and let the next turn's
          ``_client is None`` branch rebuild ``ClaudeAgentOptions``
          with the fresh effort. ``options.resume`` preserves
          conversation history across the rebuild.

        Both arms compare to ``_applied_*`` snapshots so a no-change
        turn pays nothing.

        Called BEFORE the spawn check in ``run()`` so an effort change
        can cleanly destroy + let the spawn path rebuild — calling it
        after spawn would either no-op (client already built with old
        effort) or require an extra rebuild round trip.
        """
        # No client yet → spawn path will read fresh values from
        # ``session``; nothing to reconcile.
        if self._client is None:
            return

        new_mode = session.permission_mode
        new_effort = session.model_settings.effort if session.model_settings else None
        new_session_mode = session.mode

        # ``effort`` change → cold reload. Destroy first so the
        # permission_mode arm below doesn't bother (the rebuilt client
        # will pick up the new mode through ``_build_options`` anyway).
        if new_effort != self._applied_effort:
            await self._destroy_client()
            return

        # Slice 5 — session-mode transition. Run BEFORE the
        # permission_mode arm because ``mode == "plan"`` supersedes
        # ``session.permission_mode`` on the SDK side: the slash sets
        # the SDK's permissionMode to ``"plan"`` regardless of the
        # underlying value, and exiting plan must restore explicitly.
        # Entering goal is a deliberate no-op here — the orchestrator
        # wraps the user's NEXT message to ``/goal <text>`` and the
        # SDK enters goal mode on that turn (the bare ``/goal`` is
        # status-only on Claude, spike-confirmed).
        if new_session_mode != self._applied_mode:
            prior_mode = self._applied_mode
            if prior_mode == "goal" and new_session_mode != "goal":
                # ``/goal clear`` is a no-op when no goal is set, so
                # safe regardless of whether we ever dispatched ``/goal
                # <text>`` in this session. v1 keeps no per-runtime
                # ``goal_dispatched`` flag — the no-op is acceptable.
                try:
                    await self._client.query("/goal clear")
                except Exception:
                    logger.exception("claude_agent: /goal clear failed")
            if prior_mode == "plan" and new_session_mode != "plan":
                # Restore SDK permissionMode to the underlying
                # ``session.permission_mode`` (which may have been
                # PATCHed silently while we were in plan).  Typed
                # mutator: deterministic, doesn't depend on the CLI's
                # current toggle state.
                sdk_perm = PERMISSION_MAP.get(session.permission_mode, "default")
                try:
                    await self._client.set_permission_mode(sdk_perm)
                except Exception:
                    logger.exception(
                        "claude_agent: set_permission_mode(%s) failed on "
                        "plan exit; falling back to destroy+rebuild",
                        sdk_perm,
                    )
                    await self._destroy_client()
                    return
                # SDK is now back in sync with session.permission_mode.
                self._applied_permission_mode = session.permission_mode
                self._cached_permission_mode = session.permission_mode
            # Mode-ENTRY dispatch is wrap-driven for goal (orchestrator
            # prepends ``/goal <text>`` and Claude's CLI processes the
            # slash). Plan is the exception: Claude's ``/plan`` slash
            # is interactive-CLI-only and returns "isn't available in
            # this environment" through the SDK. The typed mutator IS
            # exposed, so plan entry goes through there. ``wrap_for_mode``
            # skips Claude+plan in lockstep — subsequent user messages
            # flow through unwrapped, Claude's plan permissionMode is
            # sticky on the client.
            if new_session_mode == "plan":
                try:
                    await self._client.set_permission_mode("plan")
                except Exception:
                    logger.exception(
                        "claude_agent: set_permission_mode('plan') failed; "
                        "falling back to destroy+rebuild"
                    )
                    await self._destroy_client()
                    return
                # SDK is now in plan mode; reflect in caches so the perm
                # arm below sees us as already-in-plan and short-circuits.
                self._applied_permission_mode = session.permission_mode
            self._applied_mode = new_session_mode

        # ``permission_mode`` change → live mutator when possible.
        # Skipped while in plan mode: the SDK's permissionMode is
        # already forced to ``"plan"``. Track the underlying value so
        # the plan-exit branch above can restore it on leaving.
        if new_session_mode == "plan":
            if new_mode != self._applied_permission_mode:
                self._applied_permission_mode = new_mode
                self._cached_permission_mode = new_mode
            return

        # Translate the 3-preset harness mode to the SDK's literal via
        # ``PERMISSION_MAP`` so the wire value matches what
        # ``_build_options`` would have emitted on a cold spawn.
        if new_mode != self._applied_permission_mode:
            sdk_mode = PERMISSION_MAP.get(new_mode, "default")
            # Claude CLI deliberately refuses to TRANSITION into
            # ``bypassPermissions`` (or ``dontAsk``) mid-session unless
            # the original spawn carried
            # ``--dangerously-skip-permissions``. The live mutator
            # raises ``Cannot set permission mode to bypassPermissions
            # because the session was not launched with
            # --dangerously-skip-permissions``. We never spawn with
            # that flag (it's an opt-in CLI escape hatch), so the live
            # mutator is guaranteed to fail for those destinations and
            # the fallback below would have to clean up anyway.
            # Short-circuit straight to destroy+rebuild — the CLI
            # accepts those modes AT LAUNCH (which is what the next
            # turn's ``_build_options`` emits), it only blocks the
            # mid-session upgrade. Avoids a confusing exception log
            # and a wasted SDK round-trip.
            #
            # Set the fork-next-spawn flag so the rebuild path adds
            # ``fork_session=True``. Without forking, ``--resume
            # <session_id>`` makes the CLI silently honor the resumed
            # session's ORIGINAL permission_mode (default) and ignore
            # the new ``--permission-mode bypassPermissions`` arg —
            # tool calls would still route through ``can_use_tool``
            # and the user would still see approval prompts despite
            # picking full_access. Forking creates a fresh session id
            # that loads the prior conversation but binds to the new
            # mode.
            if sdk_mode in {"bypassPermissions", "dontAsk"}:
                self._fork_next_spawn = True
                await self._destroy_client()
                return
            try:
                await self._client.set_permission_mode(sdk_mode)
            except Exception:
                # Defensive fallback for everything else (SDK shape
                # change / unforeseen CLI policy update). Falls back to
                # cold reload rather than silently leaving the user on
                # the old mode. ``exception()`` because we don't expect
                # this branch in steady state — repeated firings mean
                # something needs investigation.
                logger.exception(
                    "claude_agent: set_permission_mode(%s) failed; falling back to destroy+rebuild",
                    sdk_mode,
                )
                await self._destroy_client()
                return
            self._applied_permission_mode = new_mode
            self._cached_permission_mode = new_mode

    # -- Goal auto-exit (slice 5 of session-modes) --

    async def _maybe_emit_goal_auto_exit(self, session: Session) -> None:
        """Probe Claude's native goal status after a goal-mode turn.

        If the bare ``/goal`` query reports no active goal, the goal
        completed (or was cleared mid-stream) — flip ``session.mode`` to
        ``default``, update the live tracker, and emit
        ``mode_changed{by: "runtime"}`` so attached clients see the
        transition without polling. Claude does not surface a
        "goal cleared" notification (spike-confirmed), so polling after
        every goal-mode ResultMessage is the cheapest signal.
        """
        if self._client is None:
            return
        cleared = await self._probe_goal_status()
        if not cleared:
            return
        session.mode = "default"
        self._applied_mode = "default"
        await self.event_sink.emit(
            Event(
                type="mode_changed",
                data={"mode": "default", "by": "runtime"},
            )
        )

    async def _probe_goal_status(self) -> bool:
        """Send bare ``/goal`` and return True iff the response says
        no goal is active.

        Per the claude-goal-spike: bare ``/goal`` is answered locally —
        no model call, zero tokens, ~3 ms — and the reply is a single
        plain-text ``AssistantMessage`` followed by ``ResultMessage``.
        The exact wire string is ``"No goal set. Usage: ..."`` when
        cleared and ``"Goal active: ..."`` when not. We intentionally
        consume the probe's messages WITHOUT going through
        ``_handle_message`` so the status probe doesn't pollute the
        just-completed turn's harness event stream.
        """
        assert self._client is not None
        await self._client.query("/goal")
        text = ""
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text += block.text
            if isinstance(msg, ResultMessage):
                break
        return text.startswith("No goal set")

    # -- Options building --

    def _build_options(self, session: Session) -> ClaudeAgentOptions:
        mcp: dict[str, Any] = {}
        sdk_tools = self._build_mcp_tools()
        if sdk_tools:
            mcp["harness"] = create_sdk_mcp_server(name="harness", tools=sdk_tools)

        for cfg in session.mcp_servers:
            mcp[cfg.name] = _to_sdk_mcp_server(cfg)

        # ``allowed_tools`` is the Claude SDK's *approval-bypass* list
        # (it lowers to ``--allowedTools`` on the CLI). Tools matched
        # here skip ``can_use_tool`` entirely — the SDK auto-approves
        # without invoking our ``_permission_handler``. We therefore
        # only include:
        #
        # * ``mcp__harness__<tool>`` — harness-bridged tools the
        #   operator defined via ``AgentConfig.tools``. These are
        #   harness infrastructure (we are the authority on them) and
        #   are not subject to user approval.
        # * ``Agent`` — sub-agent dispatch (callable_agents), same
        #   rationale.
        #
        # Tools from user-configured ``session.mcp_servers`` are
        # NOT added here. They remain visible to the model because the
        # SDK registers them via the separate ``mcp_servers=`` kwarg
        # (``--mcp-config`` on the CLI), but every call routes through
        # ``can_use_tool`` so the harness approval flow runs. Before
        # Phase 4's e2e bug hunt this loop appended
        # ``mcp__<server>__*`` patterns and silently broke approvals
        # for every MCP tool on Claude (no ``requires_action`` event
        # was emitted; the SDK auto-approved upstream of our callback).
        allowed: list[str] = [
            f"mcp__harness__{t.name}" for t in self.toolkit.list_tools() if t.handler
        ]
        if self.config.callable_agents:
            allowed.append("Agent")

        sdk_agents = self._build_agents()
        # D9: session is the runtime's source of truth for permission_mode;
        # the agent value was prefilled at session creation but is decoupled
        # afterwards. We also cache it on the instance so
        # ``_permission_handler`` (which the SDK invokes without passing
        # session) can decide whether to park on host approval.
        self._cached_permission_mode = session.permission_mode
        # ``session.mode == "plan"`` supersedes the underlying
        # ``permission_mode`` at the SDK boundary so the initial spawn
        # is consistent with what the slash-dispatch path would set.
        # See docs/design/session-modes.md §Per-runtime / Claude.
        if session.mode == "plan":
            perm = "plan"
        else:
            perm = PERMISSION_MAP.get(session.permission_mode, "default")
        sdk_hooks = self._map_hooks()

        system_prompt = self._build_system_prompt(session)

        # Reasoning effort flows session-level via ``model_settings.effort``
        # (the agent's ``effort`` is just the default prefill at session
        # create). Claude SDK accepts the full union ``low|medium|high|
        # xhigh|max`` — no per-runtime mapping needed.
        #
        # ``thinking`` is intentionally NOT threaded through —
        # Claude SDK uses its own thinking default for the active model.
        effort_value = session.model_settings.effort if session.model_settings is not None else None

        # ``setting_sources`` is a *positive* filter on the Claude CLI:
        # only the listed surfaces get loaded (``["project"]`` -> just
        # the repo-level surface; CLI's no-flag default would also
        # include ``"user"`` + ``"local"``). We deliberately keep it at
        # ``["project"]`` so harness sessions stay scoped to the
        # project's own ``CLAUDE.md`` / ``.claude/skills/`` and do
        # **not** inherit the user's personal CLI state from
        # ``~/.claude.json``.
        #
        # Known observable consequence: when no per-session model is
        # picked (the ``if self.model:`` gate below skips the kwarg)
        # AND no per-session ``model_provider`` is wired, the Claude
        # SDK falls back to its own hardcoded default (today
        # ``claude-sonnet-4-6``) — NOT the user's ``/model`` choice
        # under ``~/.claude.json``. That is by design — the harness is
        # not a Claude Code shell. If you want a specific model,
        # configure it on the session/agent.
        opts_kwargs: dict[str, Any] = dict(
            cwd=self.workspace_root,
            setting_sources=["project"] if self.workspace_root else None,
            # Harness-injected CLI settings, layered on top of what
            # ``setting_sources`` loads as an *additional* MERGE layer (CLI
            # ``--settings``), not a file rewrite — see ``_build_settings``.
            settings=self._build_settings(),
            system_prompt=system_prompt,
            allowed_tools=allowed,
            permission_mode=perm,
            max_turns=self.config.max_turns,
            max_budget_usd=self.config.max_cost_usd,
            effort=effort_value,
            mcp_servers=mcp,
            agents=sdk_agents,
            hooks=sdk_hooks,
            can_use_tool=self._permission_handler,
            # Pipe the CLI subprocess's stderr through ``_on_stderr_line``
            # → the rolling ``_stderr_buffer``. Without this the SDK
            # lets stderr inherit the parent process's stream (visible
            # only in the backend's terminal, not in event payloads).
            # See ``_on_stderr_line`` + the ``ProcessError`` branch in
            # ``run()`` for the surfacing path.
            stderr=self._on_stderr_line,
            # Always on — text_delta / thinking_delta drive live UI; the
            # full ``assistant_message`` / ``thinking`` events from the
            # AssistantMessage stream remain the canonical record.
            include_partial_messages=True,
            sandbox=self._build_sandbox_settings(),
        )
        # gate the ``model`` kwarg on a truthy ``self.model``, NOT on whether
        # ``model_provider`` is set. The two are independently optional
        # after ``ModelProvider.base_url`` became optional — a user can
        # supply just an api_key (first-party fallback) without forcing a
        # specific model, or just a model with no gateway (use ambient
        # creds but a particular model). Passing an empty string here
        # would clobber the SDK's per-account default with the literal
        # ``""``, same bug codex would hit on a deployment named ``""``.
        if self.model:
            opts_kwargs["model"] = self.model
        # ``WebSearch`` is a Claude *subscription* tool — it works on
        # Claude Code CLI sessions backed by an Anthropic subscription
        # entitlement, but errors at use time on direct-API calls
        # routed through a user-supplied gateway / api_key. Presence of
        # a ``model_provider`` is the harness's "non-subscription mode"
        # signal, so we proactively hard-deny WebSearch in that branch
        # to keep the model from advertising a tool it can't actually
        # call. ``disallowed_tools`` is the SDK's hard-deny channel
        # (the model never sees the tool); contrast with
        # ``allowed_tools``, which is the *approval-bypass* list — see
        # ``docs/references/claude-agent-options-and-mutators.md`` G4.
        if self.model_provider is not None:
            opts_kwargs["disallowed_tools"] = ["WebSearch"]
        opts = ClaudeAgentOptions(**opts_kwargs)
        env = self._build_model_provider_env()
        if env is not None:
            opts.env = env

        if session.runtime_session_id:
            opts.resume = str(session.runtime_session_id)
            # Consume the one-shot fork flag — set by the reconcile
            # path when transitioning into ``bypassPermissions`` /
            # ``dontAsk`` so the rebuilt client gets a fresh SDK
            # session id (loading the prior conversation but free of
            # the resumed session's persisted permission_mode). The
            # SDK ignores ``fork_session`` when ``resume`` is unset,
            # so guarding under the resume branch is correct shape;
            # the flag is cleared unconditionally so a stale ``True``
            # from a prior turn can't carry over.
            opts.fork_session = self._fork_next_spawn
        self._fork_next_spawn = False

        return opts

    def _build_settings(self) -> str | None:
        """Build the harness's injected CLI settings as one inline ``settings``
        JSON layer (the SDK ``settings`` field == CLI ``--settings
        <file-or-json>``), merged on top of whatever ``setting_sources``
        loads. It is an *additional* layer (``--settings`` loads "additional
        settings"), so it **merges** with — never overwrites — the workspace's
        own ``.claude/settings.json``; the harness writes no file into the
        user's project tree. An empty result returns ``None`` so the kwarg is
        omitted.

        The workspace's own settings are read once up front so each harness
        default can defer to an explicit project value. Today there is a
        single default — dynamic workflows / ``/deep-research``
        (``enableWorkflows``), which the ``setting_sources=["project"]``
        scoping otherwise drops (it lives in the user surface; see
        ``_WORKFLOW_SETTINGS``). Keep it a true *default*: inject it only when
        the project hasn't set the key, so a project's explicit value (loaded
        via ``setting_sources``) wins. When more harness defaults appear,
        split this back into per-default helpers sharing ``project``.
        """
        # Read the workspace's own settings once. Tolerant of a missing /
        # unreadable / non-dict file (treated as empty).
        project: dict[str, Any] = {}
        if self.workspace_root:
            path = os.path.join(self.workspace_root, ".claude", "settings.json")
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    project = loaded
            except (OSError, json.JSONDecodeError):
                project = {}

        settings: dict[str, Any] = {}
        if "enableWorkflows" not in project:
            settings.update(_WORKFLOW_SETTINGS)
        return json.dumps(settings) if settings else None

    def _build_model_provider_env(self) -> dict[str, str] | None:
        """Build the spawned SDK subprocess's env when (and only when)
        ``session.model_provider`` carries a per-session credential
        override. Returns ``None`` for the no-override path so the
        caller leaves ``ClaudeAgentOptions.env`` unset and the SDK
        inherits the parent process env verbatim.

        Layout in the override case:

        * ``ANTHROPIC_BASE_URL`` — set only when the provider carries
          one. ``base_url is None`` (first-party Anthropic) wipes any
          stale parent-env value so the SDK falls back to its baked-in
          ``api.anthropic.com``.
        * ``ANTHROPIC_AUTH_TOKEN`` — the per-session api_key.
        * Non-Claude model aliases additionally rewrite the SDK's
          ``ANTHROPIC_DEFAULT_*_MODEL`` family so the CLI doesn't
          short-circuit to its built-in Claude defaults when running
          against a Claude-compatible gateway model (e.g.
          DeepSeek-via-anthropic-protocol).
        """
        if self.model_provider is None:
            return None
        merged: dict[str, str] = dict(os.environ)
        if self.model_provider.base_url is not None:
            merged["ANTHROPIC_BASE_URL"] = self.model_provider.base_url
        else:
            # If a previous env carried a stale base_url (e.g. parent
            # shell exported one for an unrelated workflow), wipe it so
            # the SDK actually falls back to its default rather than
            # silently inheriting the parent's pointer.
            merged.pop("ANTHROPIC_BASE_URL", None)
        merged["ANTHROPIC_AUTH_TOKEN"] = self.model_provider.api_key
        if "claude" not in self.model:
            merged["ANTHROPIC_MODEL"] = self.model
            merged["ANTHROPIC_DEFAULT_OPUS_MODEL"] = self.model
            merged["ANTHROPIC_DEFAULT_SONNET_MODEL"] = self.model
            merged["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = self.model
            merged["CLAUDE_CODE_SUBAGENT_MODEL"] = self.model
            merged["ENABLE_TOOL_SEARCH"] = "false"
        return merged

    def _build_system_prompt(self, session: Session) -> str | SystemPromptPreset:
        """Use Claude Code's preset system prompt; append the session's
        ``instructions`` text on top.

        With ``setting_sources=["project"]`` (set when workspace_root is
        present), the SDK auto-loads ``CLAUDE.md`` and ``.claude/skills/``.
        The harness no longer hand-assembles the system prompt — we just
        append the per-session instructions, if any. (The agent's
        ``instructions`` is a UI-side default; the session is the runtime's
        source of truth.)
        """
        if session.instructions:
            return SystemPromptPreset(
                type="preset",
                preset="claude_code",
                append=session.instructions,
            )
        return SystemPromptPreset(type="preset", preset="claude_code")

    # -- Skill materialization --

    def _materialize_skills(self, session: Session) -> None:
        """Copy session.skills into ``cwd/.claude/skills/`` for SDK auto-discovery.

        Skipped when no workspace root is configured (e.g. unit tests).
        """
        if not self.workspace_root or not session.skills:
            return
        from src.runtimes.skills_materialize import prepare_claude_skills

        prepare_claude_skills(self.workspace_root, list(session.skills))

    # -- Sandbox configuration --

    def _build_sandbox_settings(self) -> SandboxSettings | None:
        """Sandbox is opt-in per project (Slice C will wire SandboxConfig here)."""
        return None

    # -- Tool conversion --

    def _build_mcp_tools(self) -> list[SdkMcpTool[Any]]:
        tools: list[SdkMcpTool[Any]] = []
        for tdef in self.toolkit.list_tools():
            if tdef.handler is None:
                continue
            tools.append(self._to_sdk_tool(tdef))
        return tools

    def _to_sdk_tool(self, tdef: ToolDef) -> SdkMcpTool[Any]:
        captured_handler = tdef.handler

        async def handler(args: dict[str, Any]) -> dict[str, Any]:
            assert captured_handler is not None
            result = await captured_handler(
                args,
                ExecContext(
                    workspace=self.workspace_root,
                    session_id=self._cur_session_id,
                    agent_id=self._cur_agent_id,
                    project_id=self._cur_project_id,
                ),
            )
            return {
                "content": [{"type": "text", "text": result.content}],
                "isError": result.is_error,
            }

        return sdk_tool(
            tdef.name,
            tdef.description,
            tdef.parameters or {"type": "object", "properties": {}},
            annotations=ToolAnnotations(readOnlyHint=tdef.read_only),
        )(handler)

    # -- Sub-agent mapping --

    def _build_agents(self) -> dict[str, AgentDefinition] | None:
        if not self.config.callable_agents:
            return None

        agents: dict[str, AgentDefinition] = {}
        for a in self.config.callable_agents:
            model: str | None = None
            if a.model:
                model = MODEL_MAP.get(a.model, a.model)

            extra: dict[str, Any] = {}
            if "memory" in a.metadata:
                extra["memory"] = a.metadata["memory"]
            if "mcp_servers" in a.metadata:
                extra["mcpServers"] = a.metadata["mcp_servers"]

            agents[a.name] = AgentDefinition(
                description=a.description,
                prompt=a.prompt,
                tools=list(a.tools) if a.tools else None,
                model=model,
                skills=list(a.skills) if a.skills else None,
                **extra,
            )
        return agents

    # -- Hook mapping --

    def _map_hooks(
        self,
    ) -> (
        dict[
            Literal[
                "PreToolUse",
                "PostToolUse",
                "PostToolUseFailure",
                "UserPromptSubmit",
                "Stop",
                "SubagentStop",
                "PreCompact",
                "Notification",
                "SubagentStart",
                "PermissionRequest",
            ],
            list[HookMatcher],
        ]
        | None
    ):
        if not self.config.hooks:
            return None

        hooks: Hooks = self.config.hooks
        sdk_hooks: dict[
            Literal[
                "PreToolUse",
                "PostToolUse",
                "PostToolUseFailure",
                "UserPromptSubmit",
                "Stop",
                "SubagentStop",
                "PreCompact",
                "Notification",
                "SubagentStart",
                "PermissionRequest",
            ],
            list[HookMatcher],
        ] = {}

        if hooks._handlers.get("before_tool"):

            async def pre_tool_use(
                input_data: HookInput, tool_use_id: str | None, context: HookContext
            ) -> SyncHookJSONOutput:
                data: dict[str, Any] = dict(input_data)
                r = await hooks.fire(
                    "before_tool",
                    tool_name=data.get("tool_name", ""),
                    input=data.get("tool_input", {}),
                )
                if r.action == "block":
                    return SyncHookJSONOutput(continue_=False, stopReason=r.reason or "blocked")
                return SyncHookJSONOutput()

            sdk_hooks["PreToolUse"] = [HookMatcher(hooks=[pre_tool_use])]

        if hooks._handlers.get("after_tool"):

            async def post_tool_use(
                input_data: HookInput, tool_use_id: str | None, context: HookContext
            ) -> SyncHookJSONOutput:
                data: dict[str, Any] = dict(input_data)
                await hooks.fire(
                    "after_tool",
                    tool_name=data.get("tool_name", ""),
                    input=data.get("tool_input", {}),
                    result=ToolResult(content=str(data.get("tool_response", ""))),
                )
                return SyncHookJSONOutput()

            sdk_hooks["PostToolUse"] = [HookMatcher(hooks=[post_tool_use])]

        if hooks._handlers.get("on_stop"):

            async def stop_hook(
                input_data: HookInput, tool_use_id: str | None, context: HookContext
            ) -> SyncHookJSONOutput:
                await hooks.fire("on_stop")
                return SyncHookJSONOutput()

            sdk_hooks["Stop"] = [HookMatcher(hooks=[stop_hook])]

        return sdk_hooks if sdk_hooks else None

    # -- Permission handler --

    async def _permission_handler(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """SDK callback — invoked once per tool call before execution.

        Decision order (slice 3):

        1. ``AskUserQuestion`` (Claude SDK's clarifying-questions tool —
           https://code.claude.com/docs/en/agent-sdk/user-input.md):
           ALWAYS park on host regardless of permission_mode. The docs
           are explicit that "Your role is to present them to users and
           return their selections" — there's no auto-approve path that
           makes sense for clarifying questions (auto-allow with no
           ``answers`` would silently swallow the question). Even
           ``full_access`` / ``auto_review`` must surface this to the UI.
        2. Toolkit static markers (read_only / permission=auto/deny) —
           overrides mode entirely; these encode design-time safety.
        3. ``self._cached_permission_mode``:
           - ``full_access`` / ``auto_review`` → allow as-is. ``auto_review``
             is implemented by passing ``permission_mode="auto"`` to the
             SDK so the SDK's own classifier handles it; the SDK still
             calls back here for non-classifier-managed tools, where
             auto-allow matches the user's expectation.
           - ``default`` → emit ``requires_action`` event, park on a
             future, await ``submit_action`` (or timeout / interrupt).
        """
        if tool_name == "AskUserQuestion":
            # Bypass the toolkit + mode short-circuits below; clarifying
            # questions always require a host response.
            return await self._await_host_decision(tool_name, input_data, context)
        if tool_name == "ExitPlanMode":
            # ExitPlanMode is plan-mode's exit gate, not a tool call.
            # It MUST surface to the user regardless of permission_mode
            # — under ``full_access`` the line-below short-circuit
            # would otherwise auto-approve and silently leave plan
            # mode without user review. Same shape as AskUserQuestion.
            return await self._await_host_decision(tool_name, input_data, context)

        simple_name = tool_name
        if tool_name.startswith("mcp__harness__"):
            simple_name = tool_name[len("mcp__harness__") :]

        tdef = self.toolkit.get(simple_name)
        if tdef:
            if tdef.read_only or tdef.permission == "auto":
                return PermissionResultAllow(updated_input=input_data)
            if tdef.permission == "deny":
                return PermissionResultDeny(message=f"{tool_name} denied by policy")

        if self._cached_permission_mode != "default":
            return PermissionResultAllow(updated_input=input_data)

        return await self._await_host_decision(tool_name, input_data, context)

    async def _await_host_decision(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext | None = None,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Park on a future the host resolves via ``submit_action``.

        Emits ``requires_action`` (with subject + subject-specific payload
        per design doc §2.4) up the event sink, then waits up to
        ``APPROVAL_TIMEOUT_SECONDS``. Timeout / interrupt sealed via
        synthetic ``action_resolved(decision="expired" | "interrupted",
        resolved_by="system")``; happy path is sealed by the orchestrator
        when ``submit_action`` lands.

        ``context`` carries the SDK's ``PermissionUpdate.suggestions``
        which the v2 ``approve_for_session`` matcher uses to derive
        pattern-grammar rules (``Bash(npm test:*)`` etc.). Defaulted to
        ``None`` so internal callers + tests without the SDK context
        still work — the matcher falls back to exact match in that case.
        Clarifying-question pendings ignore the matcher entirely (the
        verb is not advertised — spec §6.1).
        """
        pending_id = str(uuid.uuid4())
        subject = _classify_subject(tool_name)
        payload = _build_pending_payload(subject, tool_name, input_data, self.workspace_root)

        # ``AskUserQuestion`` pendings need the ``answer`` decision verb
        # to come back with a structured ``answers`` payload (per the
        # SDK contract — bare approve has nothing to feed into
        # ``updated_input.answers``). Tool-approval pendings expose
        # ``approve_with_changes`` since Claude SDK accepts
        # ``PermissionResultAllow(updated_input=<modified>)`` natively,
        # plus ``approve_for_session`` since Phase 3 of the v2 rollout.
        is_clarifying = subject == "clarifying_questions"
        # ExitPlanMode uses the V1 verb set (``approve`` / ``reject``):
        # no ``approve_with_changes`` (the model owns plan authorship —
        # user-edited plans would silently change what the model executes)
        # and no ``approve_for_session`` (every plan is different —
        # "always approve plans this session" has no useful semantic).
        is_exit_plan_mode = subject == "exit_plan_mode"
        available_decisions: list[str]
        if is_clarifying:
            available_decisions = list(AVAILABLE_DECISIONS_CLARIFYING)
        elif is_exit_plan_mode:
            available_decisions = list(AVAILABLE_DECISIONS_V1)
        else:
            available_decisions = list(AVAILABLE_DECISIONS_EDITABLE_WITH_SESSION)

        pending_data: dict[str, Any] = {
            "pending_id": pending_id,
            "subject": subject,
            "runtime_provider": "claude_agent",
            "available_decisions": available_decisions,
            "payload": payload,
        }

        # ``approve_for_session`` is unavailable for clarifying_questions
        # (spec §6.1 — no useful "always ask the same question" semantic);
        # skip the matcher entirely for that subject. Same reasoning
        # applies to ``exit_plan_mode``: a plan-approval is a one-shot
        # acknowledgement of the model's proposal, not a rule template.
        cache_hit: SessionRule | None = None
        if not is_clarifying and not is_exit_plan_mode:
            # Plumb the SDK's pattern suggestions into the matcher's
            # ``runtime_extras`` bucket. The matcher reads them under
            # ``claude_permission_updates``; normalize PermissionUpdate
            # dataclass instances to dicts so the matcher can stay free
            # of SDK type imports.
            suggestions_raw = (
                list(getattr(context, "suggestions", []) or []) if context is not None else []
            )
            normalized: list[dict[str, Any]] = []
            for s in suggestions_raw:
                norm = _permission_update_to_dict(s)
                if norm is not None:
                    normalized.append(norm)
            runtime_extras: dict[str, Any] = {"claude_permission_updates": normalized}
            # Phase 4: reduce args per-subject before feeding the
            # matcher (see ``rule_canonicalize.reduce_args_for_subject``
            # for the identity table). The reducer keeps every key
            # Claude's pattern grammar needs at match time:
            # ``shell_command`` preserves ``command``,
            # ``file_change`` normalizes onto ``file_path``,
            # ``mcp_tool_call`` drops args (Claude's MCP pattern path
            # uses tool-name equality only — no args read).
            reduced_args, subject_display = reduce_args_for_subject(subject, tool_name, input_data)
            derivation = self._approval_rule_matcher.derive_rule(
                subject, tool_name, reduced_args, runtime_extras
            )
            # The exact-args fallback's default display is generic
            # ("this exact <tool> call"). When the SDK provided no
            # pattern suggestion the matcher falls through to
            # ``ExactArgsRuleMatcher`` — in that case override with the
            # subject-aware reducer label so MCP rules read as "any X
            # call" (matching the user's endpoint-level intuition) and
            # file_change rules say "Edit on /path". Pattern derivations
            # (Bash prefix / file globs proposed by the SDK) keep their
            # richer labels.
            display = derivation.display
            if derivation.runtime_kind == ExactArgsRuleMatcher.RUNTIME_KIND:
                display = subject_display
            pending_data["session_rule_preview"] = {
                "kind": derivation.kind,
                "runtime_kind": derivation.runtime_kind,
                "display": display,
                "rule_data": derivation.rule_data,
            }
            cache_hit = self._check_session_rule(subject, tool_name, reduced_args, runtime_extras)

        await self.event_sink.emit(Event(type="requires_action", data=pending_data))

        if cache_hit is not None:
            # Cache hit: emit a synthetic action_resolved + return Allow
            # directly without parking. Orchestrator's submit_action is
            # bypassed entirely (no user round-trip needed).
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
            return PermissionResultAllow(updated_input=input_data)

        loop = asyncio.get_event_loop()
        future: asyncio.Future[
            tuple[
                Literal["approve", "approve_with_changes", "reject", "answer"],
                str | None,
                dict[str, str | list[str]] | None,
                dict[str, Any] | None,
            ]
        ] = loop.create_future()
        self._pending_futures[pending_id] = future

        try:
            decision, message, answers, modified_input = await asyncio.wait_for(
                future, timeout=self.APPROVAL_TIMEOUT_SECONDS
            )
        except TimeoutError:
            await self._emit_synthetic_resolved(pending_id, "expired")
            return PermissionResultDeny(message="approval timed out")
        finally:
            self._pending_futures.pop(pending_id, None)

        if decision == "answer":
            # ``AskUserQuestion`` response per
            # https://code.claude.com/docs/en/agent-sdk/user-input.md
            # §"Return answers to Claude": ``updated_input`` must
            # include the original ``questions`` array plus the
            # ``answers`` map keyed by question text. ``answers``
            # being None here would be the orchestrator violating its
            # own invariant — surface a clear runtime error rather
            # than feed Claude an empty dict.
            if answers is None:
                logger.error(
                    "claude_agent: 'answer' decision for %s arrived without answers; "
                    "rejecting as fallback",
                    pending_id,
                )
                return PermissionResultDeny(message="missing answers")
            return PermissionResultAllow(
                updated_input={
                    "questions": input_data.get("questions", []),
                    "answers": answers,
                }
            )
        if decision == "approve_with_changes":
            # Claude SDK contract: ``updated_input`` is the same shape
            # as the original ``input_data`` (the tool's input dict).
            # Orchestrator's validator already guarantees
            # ``modified_input`` is non-None for this verb; fall back to
            # the original input if upstream slips so we don't feed
            # Claude an empty dict (still safer than crashing the
            # callback into the SDK reader loop).
            if modified_input is None:
                logger.error(
                    "claude_agent: 'approve_with_changes' decision for %s arrived "
                    "without modified_input; approving original input as fallback",
                    pending_id,
                )
                return PermissionResultAllow(updated_input=input_data)
            return PermissionResultAllow(updated_input=modified_input)
        if decision == "approve":
            if tool_name == "ExitPlanMode":
                # ExitPlanMode approve = user acknowledged the plan and
                # is releasing plan-mode constraints for execution. The
                # SDK lifts its internal plan permissionMode on this
                # Allow; we mirror at the kernel boundary so subsequent
                # turns don't re-enter plan via the reconcile path. See
                # docs/design/session-modes.md §Per-runtime / Claude.
                await self._on_exit_plan_mode_approved()
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(message=message or "User rejected.")

    async def _on_exit_plan_mode_approved(self) -> None:
        """Flip ``session.mode = "default"`` + lift the SDK's plan
        permissionMode + emit ``mode_changed{by: "runtime"}`` after an
        ExitPlanMode approve.

        Mirrors the codex ``thread/goal/cleared`` listener
        (``codex/runtime.py``) AND the plan-exit branch in
        ``_reconcile_session_levers`` (which dispatches the same
        ``set_permission_mode`` mutator on a user-initiated plan exit
        — see ``runtime.py:692``). The Claude SDK does NOT auto-lift
        plan permissionMode on an ExitPlanMode Allow — without this
        explicit mutator call, the next turn's tool calls would
        still be plan-restricted even though our session.mode says
        "default" (live-tested against an earlier version of this
        hook that just updated trackers).

        The orchestrator's reconcile-before-save honors the
        mode_changed event we emit (it skips the disk-reload when
        ``observer.runtime_mode_change`` is set), so a concurrent
        ``POST /mode`` from the user doesn't race.
        """
        session = self._session
        if session is None or session.mode != "plan":
            # Defensive: nothing to flip if we're not actually in plan
            # at the kernel level (could happen if a concurrent POST
            # /mode already moved us out before the SDK callback ran).
            return
        # SDK-side: actively lift plan permissionMode → restore to the
        # underlying session.permission_mode mapping. Skip silently if
        # the client is gone (shouldn't happen — we're inside a
        # ``can_use_tool`` callback so the client must be alive — but
        # log if it is, since destroy-and-rebuild would lose the
        # mid-turn approval future).
        if self._client is not None:
            sdk_perm = PERMISSION_MAP.get(session.permission_mode, "default")
            try:
                await self._client.set_permission_mode(sdk_perm)
            except Exception:
                logger.exception(
                    "claude_agent: set_permission_mode(%s) failed lifting "
                    "plan on ExitPlanMode approve; SDK may stay in plan",
                    sdk_perm,
                )
        else:
            logger.warning(
                "claude_agent: ExitPlanMode approve fired with no live "
                "client; cannot lift plan permissionMode"
            )
        # Kernel-side: mode + trackers.
        session.mode = "default"
        self._applied_mode = "default"
        self._applied_permission_mode = session.permission_mode
        self._cached_permission_mode = session.permission_mode
        try:
            await self.event_sink.emit(
                Event(
                    type="mode_changed",
                    data={"mode": "default", "by": "runtime"},
                )
            )
        except Exception:
            logger.exception("claude_agent: mode_changed emit failed after ExitPlanMode approve")

    def _check_session_rule(
        self,
        subject: str,
        tool_name: str,
        args: dict[str, Any],
        runtime_extras: dict[str, Any],
    ) -> SessionRule | None:
        """Consult the kernel session-rule cache via the injected finder.

        Returns ``None`` when no finder is wired (factory unit tests, or
        the runtime is being driven without an orchestrator), when the
        finder raises (logged + treated as a miss — never block the
        approval flow on a cache failure), or when no stored rule
        matches the call.
        """
        finder = self._session_rule_finder
        if finder is None:
            return None
        try:
            return finder(subject, tool_name, args, runtime_extras)
        except Exception:
            logger.exception(
                "claude_agent: session rule check failed for %s; treating as miss",
                tool_name,
            )
            return None

    async def _emit_synthetic_resolved(self, pending_id: str, decision: str) -> None:
        """Used for runtime-side resolutions (timeout / interrupt) where
        the orchestrator isn't involved. The event_sink chain writes to
        DB + bus, so the events log stays consistent for the next
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
                "claude_agent: failed to emit synthetic action_resolved for %s",
                pending_id,
            )

    # -- Message conversion --

    async def _handle_message(self, session: Session, message: Any) -> None:
        if isinstance(message, StreamEvent):
            event = message.event
            event_type = event.get("type")
            if event_type == "content_block_start":
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    block_id = block.get("id")
                    block_name = block.get("name")
                    block_index = event.get("index")
                    if (
                        isinstance(block_id, str)
                        and isinstance(block_name, str)
                        and isinstance(block_index, int)
                        and block_name != CLAUDE_TODO_TOOL_NAME
                    ):
                        # Register index -> (id, name) so subsequent
                        # input_json_delta chunks (which only carry an
                        # index, not the id) can be routed. We do NOT
                        # emit a tool_use here — that would duplicate the
                        # canonical one from AssistantMessage. The first
                        # tool_input_delta serves as the frontend's
                        # build-card signal instead.
                        self._tool_block_by_index[block_index] = (
                            block_id,
                            block_name,
                        )
            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    await self.event_sink.emit(
                        Event(type="text_delta", data={"text": delta.get("text", "")})
                    )
                elif delta.get("type") == "thinking_delta":
                    # Streamed extended-thinking text. Surfacing each chunk
                    # gives the user immediate feedback during the long pre-
                    # response wait instead of a 25s blank-screen pause.
                    await self.event_sink.emit(
                        Event(
                            type="thinking_delta",
                            data={"text": delta.get("thinking", "")},
                        )
                    )
                elif delta.get("type") == "input_json_delta":
                    block_index = event.get("index")
                    partial = delta.get("partial_json", "")
                    if isinstance(block_index, int) and isinstance(partial, str) and partial:
                        binding = self._tool_block_by_index.get(block_index)
                        if binding is not None:
                            tool_id, tool_name = binding
                            await self.event_sink.emit(
                                Event(
                                    type="tool_input_delta",
                                    data={
                                        "id": tool_id,
                                        "name": tool_name,
                                        "text": partial,
                                    },
                                )
                            )

        elif isinstance(message, SystemMessage):
            if message.subtype == "init":
                sdk_session_id = message.data.get("session_id")
                if sdk_session_id:
                    session.runtime_session_id = str(sdk_session_id)
            elif message.subtype == "compact_boundary":
                await self.event_sink.emit(Event(type="compaction", data={}))

        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_value = getattr(block, "text", "")
                    if text_value:
                        await self.event_sink.emit(
                            Event(type="assistant_message", data={"text": text_value})
                        )
                elif isinstance(block, ToolUseBlock):
                    if block.name == CLAUDE_TODO_TOOL_NAME:
                        # Planning channel — emit todo_update from the tool
                        # input, remember the id so the matching ToolResultBlock
                        # is also suppressed.
                        self._todo_tool_use_ids.add(block.id)
                        todos = block.input.get("todos") if isinstance(block.input, dict) else None
                        if isinstance(todos, list):
                            await self.event_sink.emit(
                                Event(type="todo_update", data={"todos": list(todos)})
                            )
                    else:
                        if block.name == CLAUDE_WORKFLOW_TOOL_NAME:
                            # Remember the id so the matching ToolResultBlock
                            # (which carries the run's state-file path) starts a
                            # progress poller.
                            self._workflow_tool_use_ids.add(block.id)
                        await self.event_sink.emit(
                            Event(
                                type="tool_use",
                                data={"id": block.id, "name": block.name, "input": block.input},
                            )
                        )
                elif isinstance(block, ThinkingBlock):
                    # Canonical record of the thinking turn. Mirrors how
                    # ``assistant_message`` carries the full text once
                    # ``text_delta`` streaming is done — UI uses deltas for
                    # live rendering and this block for history.
                    await self.event_sink.emit(
                        Event(type="thinking", data={"text": block.thinking})
                    )

        elif isinstance(message, SdkUserMessage):
            content = message.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        if block.tool_use_id in self._todo_tool_use_ids:
                            # Matching TodoWrite result — suppress (todo_update
                            # already carried the structured payload).
                            continue
                        result_content = (
                            block.content if isinstance(block.content, str) else str(block.content)
                        )
                        await self.event_sink.emit(
                            Event(
                                type="tool_result",
                                data={
                                    "id": block.tool_use_id,
                                    "content": result_content,
                                    "is_error": bool(block.is_error),
                                },
                            )
                        )
                        if block.tool_use_id in self._workflow_tool_use_ids:
                            await self._start_workflow_poller(block.tool_use_id, result_content)

        elif isinstance(message, ResultMessage):
            session.status = "idle"
            if self._cancelled:
                # SDK can return a normal ResultMessage even after a
                # user-initiated interrupt; force the cancel category
                # here so the message lands as ``cancelled``.
                session.stop_reason = Error(
                    category="user_interrupt",
                    retry_status="terminal",
                    message="cancelled",
                )
            else:
                match message.subtype:
                    case "success":
                        session.stop_reason = EndTurn()
                    case "error_max_turns":
                        session.stop_reason = BudgetExhausted(reason="max_turns")
                    case "error_max_budget_usd":
                        session.stop_reason = BudgetExhausted(reason="max_cost")
                    case "error_during_execution":
                        session.stop_reason = Error(
                            category="execution_error",
                            retry_status="exhausted",
                            message=message.result or "",
                        )
                    case _:
                        session.stop_reason = Error(
                            category=message.subtype,
                            retry_status="exhausted",
                            message=message.result or "",
                        )

            usage_payload = _build_usage_payload(self.model, message)
            await self.event_sink.emit(Event(type="usage_update", data=usage_payload))

            num_turns = message.num_turns or 1
            await self.event_sink.emit(
                Event(
                    type="session_idle",
                    data={
                        "stop_reason": _stop_reason_to_dict(session.stop_reason),
                        "num_turns": num_turns,
                    },
                )
            )


def _stop_reason_to_dict(reason: Any) -> dict[str, Any]:
    if reason is None:
        return {}
    from dataclasses import asdict

    return asdict(reason)


def _normalize_anthropic_usage(raw: Any) -> dict[str, int]:
    """Project the Anthropic-native usage shape onto our four flat fields.

    The Claude Agent SDK emits keys in camelCase (``inputTokens``,
    ``cacheReadInputTokens``, ``cacheCreationInputTokens``) inside
    ``ResultMessage.model_usage``, while ``ResultMessage.usage`` (the raw
    Anthropic API echo) uses snake_case. Accept both so we don't lose
    counts depending on which path the SDK populated.
    """
    if not isinstance(raw, dict):
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    def _pick(*keys: str) -> int:
        for k in keys:
            v = raw.get(k)
            if v is not None:
                return int(v or 0)
        return 0

    return {
        "input_tokens": _pick("input_tokens", "inputTokens"),
        "output_tokens": _pick("output_tokens", "outputTokens"),
        "cache_read_tokens": _pick("cache_read_input_tokens", "cacheReadInputTokens"),
        "cache_write_tokens": _pick("cache_creation_input_tokens", "cacheCreationInputTokens"),
    }


def _build_usage_payload(default_model: str, result_msg: Any) -> dict[str, Any]:
    """Build the ``usage_update`` event payload from a ResultMessage.

    Aggregate fields are the totals across all models that participated in
    the run (e.g. main agent + sub-agents). ``model_usage`` retains the
    SDK-native per-model breakdown so consumers can do per-model attribution.
    """
    raw_usage = getattr(result_msg, "usage", None) or {}
    raw_model_usage = getattr(result_msg, "model_usage", None)

    per_model: dict[str, dict[str, Any]] | None
    if isinstance(raw_model_usage, dict) and raw_model_usage:
        per_model = {str(k): dict(v) for k, v in raw_model_usage.items()}
        agg = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        for v in per_model.values():
            normalized = _normalize_anthropic_usage(v)
            for key, val in normalized.items():
                agg[key] += val
    else:
        agg = _normalize_anthropic_usage(raw_usage)
        per_model = {default_model: dict(raw_usage)} if raw_usage else None

    payload: dict[str, Any] = dict(agg)
    payload["model_usage"] = per_model
    cost = getattr(result_msg, "total_cost_usd", None)
    if cost is not None:
        payload["cost_usd"] = float(cost)
    return payload


def _to_sdk_mcp_server(
    cfg: McpServerConfig,
) -> SdkMcpHttpServerConfig | SdkMcpSSEServerConfig | SdkMcpStdioServerConfig:
    """Translate kernel `McpServerConfig` to the SDK's wire-format dict."""
    if isinstance(cfg, McpStdioServerConfig):
        stdio: SdkMcpStdioServerConfig = {
            "type": "stdio",
            "command": cfg.command,
            "args": list(cfg.args),
        }
        # Only include ``env`` when the user supplied something — once the
        # SDK forwards an env dict, the Claude CLI uses it as-is for the
        # MCP child, replacing the parent env entirely. Omitting lets the
        # CLI inherit naturally (HOME / PATH / etc.), which ``npx``-style
        # commands depend on.
        env = resolve_stdio_env(cfg)
        if env is not None:
            stdio["env"] = env
        return stdio
    if cfg.transport == "sse":
        sse: SdkMcpSSEServerConfig = {"type": "sse", "url": cfg.url}
        if cfg.headers:
            sse["headers"] = dict(cfg.headers)
        return sse
    http: SdkMcpHttpServerConfig = {"type": "http", "url": cfg.url}
    if cfg.headers:
        http["headers"] = dict(cfg.headers)
    return http

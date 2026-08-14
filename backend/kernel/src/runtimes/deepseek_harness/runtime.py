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
* **No approvals.** Tools composed into the session run unattended;
  ``permission_mode="auto_review"`` is rejected at session create (route
  guard), and ``submit_action`` raises.
* **No native fork / task coverage.** ``fork_session`` and
  ``run_task_coverage`` raise; ``supports_native_continuation`` is False so
  the orchestrator marks task coverage unavailable instead of calling it.

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
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from src.core.agent_config import AgentConfig
from src.core.approval_rule_matcher import ExactArgsRuleMatcher, RuntimeApprovalRuleMatcher
from src.core.events import Event, EventSink
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
from src.runtimes.deepseek_harness.composition import (
    DshLaunchSpec,
    cleanup_composition,
    resolve_launch,
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
from src.runtimes.interruption import describe_exception, is_runtime_interruption
from src.runtimes.skills_materialize import prepare_codex_skills

logger = logging.getLogger(__name__)

# The provider route the generated composition registers (dsh-llm-deepseek).
DSH_PROVIDER_ROUTE = "deepseek-official"

STATE_DIR_ENV = "VALUZ_DSH_STATE_DIR"
DEFAULT_STATE_DIR = "./dsh_state"

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
        self._turn_anchor: dict[str, Any] | None = None
        self._mapper = DshEventMapper()
        if toolkit is not None and toolkit.list_tools():
            # Kernel-registered ToolDefs reach the other runtimes through the
            # in-process MCP bridge; dsh consumes tools only through its own
            # composition (session.mcp_servers). Valuz sessions carry harness
            # tools as MCP rows, so this is expected to be empty here.
            logger.warning(
                "deepseek_harness: ignoring %d kernel toolkit tools (composition-only tools)",
                len(toolkit.list_tools()),
            )

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
        raise NotImplementedError(
            "deepseek_harness emits no requires_action (the dsh SDK wire has "
            "no approval flow); there is nothing to decide"
        )

    async def interrupt(self) -> None:
        self._cancelled = True
        client = self._client
        if client is not None:
            # No cancel method on the wire — killing the subprocess is the
            # only abandon path. The reader loop's TRANSPORT_CLOSED sentinel
            # wakes the parked turn loop, which settles as user_interrupt.
            client.kill()
        task = self._active_task
        if task is not None and not task.done():
            task.cancel()

    async def close(self) -> None:
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

        launch = self._launch_spec or resolve_launch()
        if launch is None:
            raise RuntimeError(
                "deepseek_harness runtime is not launchable on this machine "
                "(set VALUZ_DSH_RUNTIME_BIN or VALUZ_DSH_ROOT)"
            )

        skills_root: str | None = None
        if session.skills:
            skills_root = prepare_codex_skills(self.workspace_root, session.skills)

        self._config_path = write_composition(
            session,
            config_parent_dir=launch.config_parent_dir,
            workspace_root=self.workspace_root,
            skills_root=skills_root,
            model_settings=self.model_settings,
        )
        self._composition_fingerprint = _composition_fingerprint(session)

        env = os.environ.copy()
        env["DSH_CORDIS_CONFIG"] = self._config_path
        env["DSH_CWD"] = self.workspace_root
        if self.model_provider is not None:
            env["DEEPSEEK_API_KEY"] = self.model_provider.api_key
            if self.model_provider.base_url:
                env["DEEPSEEK_BASE_URL"] = self.model_provider.base_url

        client = DshRuntimeClient(launch.argv, cwd=launch.cwd, env=env)
        await client.start()
        max_tokens = (
            self.model_settings.max_tokens if self.model_settings is not None else None
        )
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
    """Stable digest of the session state baked into the Cordis composition.

    Covers exactly what ``build_composition_rows`` reads from the session:
    instructions (persona), skills, and the full MCP server set including
    headers — a changed credential must change the digest.
    """
    payload = json.dumps(
        {
            "instructions": session.instructions,
            "skills": list(session.skills),
            "mcp": [asdict(server) for server in session.mcp_servers],
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

    dsh reports cached input as a subset of ``inputTokens`` and its
    ``outputTokens`` already include reasoning (DeepSeek ``completion_tokens``
    semantics), so: uncached input = input - cache_read; output stays as-is;
    the reasoning bucket rides only the per-model breakdown.
    """
    cache_read = totals.get("cache_read_tokens", 0)
    flat = {
        "input_tokens": max(0, totals.get("input_tokens", 0) - cache_read),
        "output_tokens": totals.get("output_tokens", 0),
        "cache_read_tokens": cache_read,
        "cache_write_tokens": 0,
    }
    return {
        **flat,
        "model_usage": {
            model: {**flat, "reasoning_tokens": totals.get("reasoning_tokens", 0)}
        },
    }


def _stop_reason_to_dict(reason: StopReason | None) -> dict[str, Any]:
    if reason is None:
        return {}
    return asdict(reason)

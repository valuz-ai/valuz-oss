"""DeepAgentsRuntime — wraps langchain deepagents as a RuntimePort.

Maps AgentConfig + ToolKit → create_deep_agent(...) graph, harness Tools →
StructuredTool, SubAgentDef → SubAgent (typed dict), and streams the graph's
``astream_events`` output to harness Events.

Filesystem operations resolve against the project's cwd via
``_WorkspaceLocalShellBackend`` (virtual_mode=True: parent-traversal rejected,
virtual "/" = workspace root, dead-end mappings fall through to existing host
paths — see the class docstring).

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
import errno
import json
import logging
import os
import re
import shutil
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
from deepagents import SubAgent, create_deep_agent
from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import FileData, ReadResult
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from src.core.agent_config import AgentConfig, SubAgentDef
from src.core.approval_rule_matcher import ExactArgsRuleMatcher, RuntimeApprovalRuleMatcher
from src.core.citation import EvidenceRegistry
from src.core.events import AVAILABLE_DECISIONS_EDITABLE_WITH_SESSION, Event, EventSink
from src.core.mcp_source_metadata import wrap_mcp_result_metadata_for_transport
from src.core.rule_canonicalize import reduce_args_for_subject
from src.core.session_approval_cache import SessionRule
from src.core.task_coverage_continuation import TASK_COVERAGE_NOOP_TOOL_NAME
from src.core.tools import ExecContext, ToolDef, ToolKit, ToolResult
from src.core.tracing import langchain_config_overlay
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
from src.runtimes.deepagents._patches import (
    MAIN_GRAPH_RECURSION_LIMIT,
    apply_deepagents_patches,
)
from src.runtimes.deepagents.approval_bridge import (
    _build_pending_payload,
    _classify_subject,
)
from src.runtimes.deepagents.middleware import (
    CitationEvidenceCompactionMiddleware,
    InvalidToolCallPairMiddleware,
    ToolErrorTolerantMiddleware,
    WindowsPathVirtualizerMiddleware,
    citation_artifact_content,
)
from src.runtimes.interruption import (
    absorb_interrupt_cancellations,
    describe_exception,
    is_runtime_interruption,
)
from src.runtimes.mcp_env import resolve_stdio_env
from src.runtimes.network_egress import ForwardProxyDescriptor, record_runtime_egress_phase

logger = logging.getLogger(__name__)


def _is_internal_summarization_event(chunk: dict[str, Any]) -> bool:
    """Return whether a LangChain model event belongs to history compaction.

    LangChain tags the nested summarizer invocation with this metadata.  Its
    response is runtime state, not an Agent message: publishing it exposes the
    private handoff prompt and makes Citation Audit treat the summary as a
    second user-facing answer.  Normal primary, tool-adjacent, repair, and
    continuation model events do not carry this marker and remain visible.
    """

    metadata = chunk.get("metadata")
    return isinstance(metadata, dict) and metadata.get("lc_source") == "summarization"


async def _log_mcp_progress(
    progress: float,
    total: float | None,
    message: str | None,
    context: Any = None,
) -> None:
    """Consume MCP ``notifications/progress`` for a tool call.

    Registering ANY callback is what makes the SDK attach a
    ``progressToken`` to the request, which is what permits servers to
    report at all. This one only logs: nothing here resets a timer (the
    request timeout is a hard ceiling), so progress is an observability
    signal, not a lifeline. No measured client does otherwise: codex and the
    Claude CLI both abort exactly at their configured ceiling with beats
    still arriving.
    """
    logger.debug(
        "mcp progress: %s/%s %s",
        progress,
        total if total is not None else "?",
        message or "",
    )


async def _preserve_mcp_source_metadata(request: Any, handler: Any) -> Any:
    """Keep result-level MCP citation metadata through LangChain conversion."""

    result = await handler(request)
    return wrap_mcp_result_metadata_for_transport(
        result,
        server_name=str(getattr(request, "server_name", "") or "unknown"),
    )


# Apply third-party deepagents shims once, before any graph is built. See
# ``_patches`` — raises *subagents* above langgraph's default 25-step recursion
# limit (which they otherwise never escape). The *main* graph's limit is fixed
# separately, in ``stream_config`` below, because ``astream_events`` drops the
# bound budget deepagents sets — see ``MAIN_GRAPH_RECURSION_LIMIT``.
apply_deepagents_patches()


# Default location for the deepagents-specific checkpoint store. Kept separate
# from the harness business DB so langgraph schema migrations and our own
# alembic history don't interfere with each other. Override with env var.
DEFAULT_CHECKPOINT_DB = "./deepagents_checkpoints.db"
CHECKPOINT_DB_ENV = "DEEPAGENTS_CHECKPOINT_DB"

_CITATION_SYSTEM_POLICY_BLOCK_RE = re.compile(
    r'<citation-system-policy(?:\s+revision="[^"]*")?>\s*.*?'
    r"</citation-system-policy>",
    re.DOTALL,
)


def _citation_system_policy_block(instructions: str) -> str:
    """Extract only the immutable Evidence protocol for nested agents."""

    matches = list(_CITATION_SYSTEM_POLICY_BLOCK_RE.finditer(instructions or ""))
    return matches[-1].group(0).strip() if matches else ""


def _sanitize_anthropic_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop malformed signature-only thinking blocks from model history.

    Some Anthropic-compatible streaming gateways emit the terminal signature
    delta as a separate ``thinking`` content block. LangChain preserves that
    block in the checkpoint, but Anthropic rejects it on the next round because
    the required ``thinking`` field is absent. The signature-only block carries
    no user-visible reasoning and is not the tool call, so remove only that
    invalid transport fragment while preserving every complete thinking, text,
    and tool-use block.
    """

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload
    dropped = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        filtered: list[Any] = []
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "thinking"
                and not isinstance(block.get("thinking"), str)
            ):
                dropped += 1
                continue
            filtered.append(block)
        if len(filtered) != len(content):
            message["content"] = filtered
    if dropped:
        logger.warning(
            "deepagents: removed %s malformed signature-only thinking history block(s)",
            dropped,
        )
    return payload


# In an ephemeral cloud sandbox the checkpoint must be EXTERNALIZED (per-owner
# COS mount) so it survives sandbox recreation ("resume in a fresh sandbox").
# sqlite CANNOT live on COS — sqlite-on-COS-FUSE corrupts (WAL -shm mmap, no
# fcntl locking, whole-object PUT → SIGBUS / "database disk image is malformed").
# So IN-SANDBOX we use FileCheckpointSaver (write-once JSON files, corruption-free
# on COS). The LOCAL resident process defaults to the sqlite store above (durable
# local disk, single host). Gate: _in_sandbox(), overridable per deployment via
# ``DEEPAGENTS_CHECKPOINT_BACKEND`` (see _checkpoint_backend).
DEFAULT_CHECKPOINT_ROOT = "./deepagents_checkpoints"
CHECKPOINT_ROOT_ENV = "DEEPAGENTS_CHECKPOINT_ROOT"

# Explicit backend override. Unset (default) keeps the historical behaviour:
# sandbox → file, local → sqlite. A deployment that wants the SAME on-disk
# checkpoint shape in both places (e.g. so one collector/backup path can read
# local and sandbox runs alike) sets ``file`` locally.
CHECKPOINT_BACKEND_ENV = "DEEPAGENTS_CHECKPOINT_BACKEND"


def _in_sandbox() -> bool:
    """True inside the ephemeral cloud sandbox (→ file+COS checkpointer), false
    for the local resident process (→ sqlite checkpointer). ``IS_SANDBOX`` is set
    by the kernel sandbox image; ``KERNEL_STORE=remote`` is the SaaS store tier."""
    return os.getenv("IS_SANDBOX", "").strip().lower() in ("1", "true", "yes") or (
        os.getenv("KERNEL_STORE", "local").strip().lower() == "remote"
    )


def _checkpoint_backend() -> str:
    """``"file"`` or ``"sqlite"`` — which checkpoint store this process uses.

    ``DEEPAGENTS_CHECKPOINT_BACKEND`` wins when set to a recognised value;
    otherwise fall back to the deployment gate (:func:`_in_sandbox`), so the
    default behaviour is unchanged. An unrecognised value is ignored rather
    than raising: a typo in an env var must not take the runtime down.
    """
    raw = os.getenv(CHECKPOINT_BACKEND_ENV, "").strip().lower()
    if raw in ("file", "sqlite"):
        return raw
    return "file" if _in_sandbox() else "sqlite"


def _expand_embedded_json(value: Any, *, depth: int = 0) -> Any:
    """Decode JSON strings embedded inside an offloaded MCP envelope.

    MCP adapters commonly return a JSON content-block array whose ``text``
    field is itself serialized JSON.  Pretty-printing only the outer array
    leaves the useful dataset on one enormous line, so line-based pagination
    still cannot advance.  Decode only strings that visibly contain JSON and
    cap recursion to keep hostile or accidental nesting bounded.
    """

    if depth >= 6:
        return value
    if isinstance(value, dict):
        return {key: _expand_embedded_json(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_embedded_json(item, depth=depth + 1) for item in value]
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if len(candidate) < 2 or candidate[0] not in "[{" or candidate[-1] not in "]}":
        return value
    try:
        decoded = json.loads(candidate)
    except (TypeError, ValueError):
        return value
    return _expand_embedded_json(decoded, depth=depth + 1)


def _logical_large_result_content(content: str) -> str:
    """Expose one-line structured tool results as stable logical lines."""

    if len(content.splitlines()) > 1:
        return content
    try:
        decoded = json.loads(content)
    except (TypeError, ValueError):
        decoded = None
    if decoded is not None:
        rendered = json.dumps(
            _expand_embedded_json(decoded),
            ensure_ascii=False,
            indent=2,
        )
        if len(rendered.splitlines()) > 1:
            return rendered
    # Non-JSON output can still be a single minified/log line.  Fixed-width
    # logical lines make read_file pagination usable without mutating the
    # immutable artifact consumed by Citation extraction.
    width = 1_000
    if len(content) > width:
        return "\n".join(content[index : index + width] for index in range(0, len(content), width))
    return content


def _is_within(path: Path, root: Path) -> bool:
    """Lexical containment — deliberately WITHOUT ``resolve()``.

    Resolving first is what makes a materialized skill link (whose target lives
    outside the workspace by design) read as an escape attempt.
    """

    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _raise_if_symlink_loop(path: Path) -> None:
    """Raise ``OSError(ELOOP)`` if ``path`` is an unresolvable symlink loop.

    Python 3.13+ made ``Path.resolve(strict=False)`` return the unresolved path
    for a symlink loop instead of raising. Upstream restores the pre-3.13
    contract with an equivalent probe; the lexical resolution below skips
    ``resolve()`` entirely, so it carries its own copy rather than importing an
    upstream private helper (a rename there would otherwise break app boot).
    Windows reports NTFS reparse cycles as ``winerror=1921`` without a reliable
    ``errno`` mapping, hence the explicit second check.
    """

    if not path.is_symlink():
        return
    try:
        path.stat()
    except OSError as exc:
        if exc.errno == errno.ELOOP or getattr(exc, "winerror", None) == 1921:
            raise


class _WorkspaceLocalShellBackend(LocalShellBackend):
    """Keep DeepAgents virtual artifacts readable by both file and shell tools.

    Also makes ``virtual_mode`` compatible with how the harness materializes
    skills. Upstream resolves every path with ``Path.resolve()`` and then
    requires the result to sit under ``cwd``, which breaks two things this
    runtime depends on:

    1. ``_materialize_skills`` hands ``create_deep_agent(skills=[...])`` the
       ABSOLUTE root ``<cwd>/.agents/skills``. Read back as a *virtual* path it
       becomes ``<cwd>/<cwd>/.agents/skills`` — nonexistent, so skill discovery
       silently finds zero packages and a direct read reports "not found" while
       quoting the real path.
    2. ``skills_materialize`` links each package (symlink / junction) at its
       source outside the workspace, deliberately, so edits to the source are
       live. ``resolve()`` lands on that source and the containment check then
       rejects it, so even a correct virtual path lists nothing.

    Both are fixed by judging containment **lexically** — before symlink
    resolution — and by accepting an absolute path that is already inside the
    workspace as-is. ``..`` and ``~`` stay rejected.

    Out-of-workspace absolutes get a host fallthrough: virtual resolution
    wins whenever ``cwd / key`` names something real (so "/", every
    workspace path, and the virtual dialect behave exactly as before), and
    only a dead-end virtual mapping falls through to an existing host
    location. Without it, ``ls /Users/x/test`` mapped to
    ``<cwd>/Users/x/test`` and returned ``[]`` while the shell tool could
    read the same directory freely — an inconsistency, not a boundary
    (upstream documents ``virtual_mode`` as path semantics and explicitly
    NOT a security control, ``execute()`` below runs with un-translated
    host paths anyway, and claude/codex file tools reach the whole host
    subject to the same approval gating). Flipping ``virtual_mode=False``
    instead would break the virtual artifact namespaces — summarization
    writes ``/conversation_history/<thread>.md`` and the large-result
    offload uses ``/large_tool_results/`` — landing them on the filesystem
    root, the exact desktop fail/retry bug ``virtual_mode=True`` fixed,
    and would strand the Windows path virtualizer's ``/…`` outputs.
    """

    _VIRTUAL_ARTIFACT_PREFIXES = (
        "/large_tool_results/",
        "/conversation_history/",
    )

    def _resolve_path(self, key: str) -> Path:
        if not self.virtual_mode:
            return super()._resolve_path(key)

        if ".." in key or key.startswith("~"):
            msg = "Path traversal not allowed"
            raise ValueError(msg)

        # Tested on the raw key, not a "/"-prefixed copy: on Windows an
        # in-workspace absolute path is ``C:\...`` and prefixing would make it
        # unrecognizable. A driveless POSIX-style key is not absolute there and
        # falls through to the virtual mapping, as intended.
        raw = Path(key)
        if raw.is_absolute() and _is_within(raw, self.cwd):
            _raise_if_symlink_loop(raw)
            return raw

        # Virtual-first: the workspace mapping wins whenever it names
        # something real, so every path that resolved before keeps resolving
        # identically. Only a dead-end mapping may fall through to the host.
        candidate = self.cwd / key.lstrip("/")
        if not candidate.exists() and self._host_anchored(key, raw):
            _raise_if_symlink_loop(raw)
            return raw
        if not _is_within(candidate, self.cwd):
            msg = f"Path:{candidate} outside root directory: {self.cwd}"
            raise ValueError(msg)
        _raise_if_symlink_loop(candidate)
        return candidate

    def _host_anchored(self, key: str, raw: Path) -> bool:
        """Whether an absolute key names a real host location outside cwd.

        Anchored means the path itself exists, or its parent directory does —
        so a new file can be written into an existing host directory, while a
        typo still resolves against the host and errors honestly. The
        filesystem root is never an anchor: a bare top-level key like
        ``/notes.md`` stays in the virtual dialect (parent ``/`` proves
        nothing), and ``/`` itself always means the workspace root. The
        DeepAgents artifact namespaces are pinned to the workspace regardless
        of what exists on the host.
        """
        if not raw.is_absolute() or raw == Path(raw.anchor):
            return False
        if key.startswith(self._VIRTUAL_ARTIFACT_PREFIXES):
            return False
        if raw.exists():
            return True
        parent = raw.parent
        return parent != Path(parent.anchor) and parent.exists()

    def _to_virtual_path(self, path: Path) -> str:
        # ``ls`` renders each child through here. Upstream resolves first, so a
        # materialized skill link renders as its out-of-workspace source and is
        # dropped as "outside root" — the reason a populated skills root listed
        # as empty. Children come from ``iterdir()`` on an already-absolute
        # parent, so a lexical relative path is both correct and stable.
        # Out-of-workspace host paths (accepted by the ``_resolve_path``
        # fallthrough) render as their absolute POSIX form, which round-trips
        # through the same fallthrough.
        p = Path(path)
        try:
            return "/" + p.relative_to(self.cwd).as_posix()
        except ValueError:
            return p.as_posix()

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2_000,
    ) -> ReadResult:
        if not file_path.startswith("/large_tool_results/"):
            return super().read(file_path, offset=offset, limit=limit)

        raw = super().read(file_path, offset=0, limit=1)
        if raw.error or raw.file_data is None or raw.file_data.get("encoding") != "utf-8":
            return raw
        content = str(raw.file_data.get("content") or "")
        logical = _logical_large_result_content(content)
        if logical == content:
            return super().read(file_path, offset=offset, limit=limit)
        lines = logical.splitlines(keepends=True)
        if offset >= len(lines):
            return ReadResult(
                error=f"Line offset {offset} exceeds file length ({len(lines)} lines)"
            )
        return ReadResult(
            file_data=FileData(
                content="".join(lines[offset : offset + limit]),
                encoding="utf-8",
            )
        )

    def execute(self, command: str, *, timeout: int | None = None) -> Any:
        # Filesystem tools use a virtual root, while LocalShellBackend executes
        # with the workspace as cwd and does not translate virtual paths.
        # Convert only the two DeepAgents-owned artifact namespaces to relative
        # paths; ordinary absolute user paths retain their existing semantics.
        translated = command
        for prefix in self._VIRTUAL_ARTIFACT_PREFIXES:
            translated = translated.replace(prefix, prefix.removeprefix("/"))
        return super().execute(translated, timeout=timeout)


def _build_local_shell_backend(workspace_root: str | None) -> LocalShellBackend:
    """Create a backend that maps DeepAgents virtual paths into the workspace.

    Built-in summarization writes to virtual absolute paths such as
    ``/conversation_history/<thread>.md``. Without ``virtual_mode=True`` those
    paths target the host filesystem root, where desktop runs fail and retry
    summarization instead of completing the turn.
    """

    if workspace_root:
        return _WorkspaceLocalShellBackend(
            root_dir=workspace_root,
            inherit_env=True,
            virtual_mode=True,
        )
    return _WorkspaceLocalShellBackend(inherit_env=True, virtual_mode=True)


# langchain TodoListMiddleware tool name (auto-included by deepagents). Treated
# as a planning channel: emit `todo_update` and suppress the generic tool_use /
# tool_result pair so the UI trace doesn't double-render it.
DEEPAGENTS_TODO_TOOL_NAME = "write_todos"


def _session_gateway_headers(session: Session) -> dict[str, str]:
    """Build headers to forward to the gateway on each LLM call.

    ``X-Valuz-Session-Id`` tags gateway_debit ledger rows with the session.
    Ignored by first-party providers (api.openai.com / api.anthropic.com).
    """
    return {"X-Valuz-Session-Id": str(session.id)}


def _session_evidence_binding_enabled(session: Session) -> bool:
    """Whether the model needs the minimal private Evidence binding protocol."""

    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    valuz = metadata.get("valuz")
    return bool(
        isinstance(valuz, dict)
        and (valuz.get("citation_enabled") or valuz.get("citation_verification_enabled"))
    )


class DeepAgentsRuntime:
    """Wraps deepagents `create_deep_agent` as a RuntimePort implementation."""

    supports_native_continuation = True

    def __init__(
        self,
        config: AgentConfig,
        model: str,
        event_sink: EventSink,
        toolkit: ToolKit | None = None,
        workspace_root: str = "",
        checkpoint_db: str | None = None,
        checkpoint_root: str | None = None,
        model_provider: ModelProvider | None = None,
        model_settings: ModelSettings | None = None,
        egress_descriptor: ForwardProxyDescriptor | None = None,
    ) -> None:
        self.config = config
        self.model = model
        self.event_sink = event_sink
        self.toolkit = toolkit or ToolKit()
        self.workspace_root = workspace_root
        self.checkpoint_db = checkpoint_db or os.getenv(CHECKPOINT_DB_ENV) or DEFAULT_CHECKPOINT_DB
        self.checkpoint_root = (
            checkpoint_root or os.getenv(CHECKPOINT_ROOT_ENV) or DEFAULT_CHECKPOINT_ROOT
        )
        self.model_provider = model_provider
        self.model_settings = model_settings
        self.egress_descriptor = egress_descriptor
        # Only the model client's HTTP transport receives this explicit
        # proxy. Local shell, MCP and other process clients retain their
        # existing environment and can never read the proxy capability.
        self._egress_http_client: httpx.Client | None = None
        self._egress_http_async_client: httpx.AsyncClient | None = None
        self._graph: Any | None = None
        self._checkpointer: Any | None = None
        self._checkpointer_cm: Any | None = None
        self._active_task: asyncio.Task[Any] | None = None
        # ``interrupt()`` cancels of ``_active_task`` this turn; balanced by
        # ``run()`` after it swallows the injected ``CancelledError`` (see
        # ``absorb_interrupt_cancellations``).
        self._interrupt_cancels = 0
        self._cancelled: bool = False
        # Native fork anchor for the turn most recently completed:
        # ``{"provider": "deepagents", "thread_id": ..., "checkpoint_id":
        # ..., "parent_checkpoint_id": ...}``. ``checkpoint_id`` is the
        # langgraph checkpoint the turn settled on; ``parent_checkpoint_id``
        # is the pre-input checkpoint (``None`` on a fresh thread). Set only
        # on a cleanly-completed graph turn — bare completions have no
        # checkpoints and cancelled turns have no settled checkpoint.
        # Read-and-cleared by the orchestrator (``consume_turn_anchor``)
        # into ``Message.metadata["runtime_native"]`` — the
        # message-granularity fork seam (docs/design/session-fork.md).
        self._turn_anchor: dict[str, Any] | None = None
        # One immutable Evidence namespace per user turn, shared by the main
        # agent and every nested subagent.  The graph and its middleware live
        # across turns, so ``run`` resets this object instead of replacing it.
        self._turn_evidence_registry = EvidenceRegistry()
        self._continuing_same_user_turn = False
        # Identity of the session currently being run — exposed to
        # custom-tool handlers through ExecContext.
        self._cur_session_id: str = ""
        self._cur_user_id: str = ""
        self._egress_turn_attempt_id: str | None = None

        # Approval bridge state (Phase 3 of the cross-runtime approval
        # contract). ``_pending_futures`` maps pending_id → future that
        # ``run()`` is parked on inside ``_await_host_decisions``;
        # ``submit_action`` resolves them. ``_cached_permission_mode`` is
        # captured at ``_ensure_graph`` time so PATCHing
        # ``session.permission_mode`` mid-turn does not retroactively
        # change ``interrupt_on`` for an already-built graph (cold-reload
        # semantics, mirrors Claude/Codex). ``_mcp_tool_names`` is the
        # set tracked at graph construction so subject classification can
        # tell MCP-origin tools from runtime built-ins. The separate
        # ``_external_mcp_tool_names`` set excludes the host harness so the
        # orchestrator only gates post-run checks on external information.
        # langchain-mcp tool names don't carry server prefixes, so membership
        # is the only signal available at event-emission time.
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
        self._applied_citation_mode: bool | None = None
        self._mcp_tool_names: set[str] = set()
        self._external_mcp_tool_names: set[str] = set()
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

    async def _emit_citation_evidence(
        self,
        citation_join_id: str,
        tool_name: str | None,
        model_content: Any,
        citation_content: str,
    ) -> None:
        """Bridge a completed middleware sidecar into the generic event layer."""

        await self.event_sink.emit(
            Event(
                type="citation_evidence",
                data={
                    "tool_name": tool_name,
                    "model_content": model_content,
                    "content": citation_content,
                    "citation_join_id": citation_join_id,
                },
            )
        )

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

    def consume_turn_anchor(self) -> dict[str, Any] | None:
        """Return and clear the native anchor captured by the last ``run()``.

        Read-and-clear so a turn that never settles on a checkpoint
        (bare completion, cancel, graph failure) cannot inherit a stale
        anchor from the previous message.
        """
        anchor, self._turn_anchor = self._turn_anchor, None
        return anchor

    async def fork_session(
        self,
        session: Session,
        *,
        source_native_session_id: str,
        anchor: str | None = None,
    ) -> str:
        """Branch a source langgraph thread into this session's thread.

        Copies the source thread's checkpoint rows into a NEW thread keyed
        by this session's id — so the standard ``thread_id == session.id``
        binding holds for forks with zero special-casing. ``anchor`` is a
        turn-end checkpoint id (``runtime_native.checkpoint_id``); the copy
        walks the ``parent_checkpoint_id`` chain from it, so the new
        thread's latest checkpoint IS the anchor and later turns are
        excluded. Tail fork (``anchor=None``) copies the whole thread; a
        source with no checkpoints (bare completion / never ran) copies
        zero rows, which is legal there and a ``ValueError`` with an
        anchor. Pure store re-keying — no graph compile, no model client,
        and the source thread is never written.

        Only the sqlite backend is wired: the file backend is retired
        (local and cloud both run sqlite), so fork fails loud there
        instead of copying a store nothing reads.
        """
        if _checkpoint_backend() != "sqlite":
            raise NotImplementedError(
                "deepagents fork supports the sqlite checkpoint backend only "
                "(the file backend is retired)."
            )
        from src.runtimes.deepagents.checkpoint_fork import fork_sqlite_thread

        new_thread_id = str(session.id)
        copied = await fork_sqlite_thread(
            self.checkpoint_db,
            source_native_session_id,
            new_thread_id,
            anchor_checkpoint_id=anchor,
        )
        logger.info(
            "deepagents fork: copied %d checkpoints from thread %s to %s",
            copied,
            source_native_session_id,
            new_thread_id,
        )
        session.runtime_session_id = new_thread_id
        return new_thread_id

    async def prepare(self, session: Session) -> None:
        """DeepAgents has no external CLI cold start to prepare separately."""
        del session

    async def _emit_turn_phase(self, phase: str, **fields: Any) -> None:
        """Persisted latency marker — see ``turn_phase`` in ``events.py``."""
        await self.event_sink.emit(Event(type="turn_phase", data={"phase": phase, **fields}))
        await record_runtime_egress_phase(
            getattr(self, "_cur_session_id", "") or None,
            getattr(self, "_egress_turn_attempt_id", None),
            phase,
        )

    async def run(self, session: Session, user_message: UserMessage) -> None:
        from datetime import datetime

        from src.core.prompt_builder import build_user_prompt

        session.status = "running"
        self._cancelled = False
        self._interrupt_cancels = 0
        self._cur_session_id = session.id
        # getattr: run() is exercised with synthetic session stubs in tests.
        self._cur_user_id = getattr(session, "user_id", "") or ""
        self._egress_turn_attempt_id = uuid.uuid4().hex
        if not self._continuing_same_user_turn:
            self._turn_evidence_registry.reset()

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
            bare = is_bare_completion(session)
            if bare:
                # Raw chat model (see ``_ensure_graph``): it takes a plain
                # message list, not the graph's ``{"messages": [...]}``
                # state dict, and the session instructions must ride as an
                # explicit system message (the graph normally injects them).
                bare_messages: list[dict[str, str]] = []
                if session.instructions:
                    bare_messages.append({"role": "system", "content": session.instructions})
                bare_messages.append({"role": "user", "content": prompt})
                stream_input: Any = bare_messages
            else:
                stream_input = {"messages": [{"role": "user", "content": prompt}]}
            # ``recursion_limit`` MUST be set here, in the call-time config:
            # ``astream_events`` (below) ignores the budget deepagents binds onto
            # the graph with ``.with_config``, so without this the main loop runs
            # at langgraph's default of 25 and dies on any non-trivial turn. See
            # ``_patches.MAIN_GRAPH_RECURSION_LIMIT``.
            stream_config: dict[str, Any] = {
                "configurable": {"thread_id": str(thread_id)},
                "recursion_limit": MAIN_GRAPH_RECURSION_LIMIT,
            }
            # Langfuse tracing (empty overlay unless active): the shared
            # LangChain callback handler must ride in the CALL-TIME config —
            # same reason as ``recursion_limit`` above.
            stream_config.update(
                langchain_config_overlay(session_id=session.id, user_id=session.user_id)
            )
            known_citation_tool_messages: set[str] = set()
            start_checkpoint_id: str | None = None
            if not bare:
                initial_state = await graph.aget_state(stream_config)
                start_checkpoint_id = _state_checkpoint_id(initial_state)
                known_citation_tool_messages = {
                    key
                    for key, _tool_name, _model_content, _private_content in (
                        _state_citation_artifacts(initial_state)
                    )
                }

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
            # Observability: the gap from this row to the first thinking/text
            # delta = langgraph dispatch + model TTFT. Emitted once per turn
            # (before the first stream pass, not per resume iteration).
            await self._emit_turn_phase("dispatch")
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
            saw_model_event = False
            # The state snapshot the turn settled on — feeds the fork
            # anchor below. Stays ``None`` for bare completions (no
            # checkpointer) and for turns cancelled before the first
            # ``aget_state``.
            settled_state: Any = None
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
                        internal_summarization = _is_internal_summarization_event(chunk)
                        text = _extract_chunk_text(chunk_obj)
                        thinking_text = _extract_chunk_thinking(chunk_obj)
                        if (
                            not saw_model_event
                            and not internal_summarization
                            and (text or thinking_text)
                        ):
                            saw_model_event = True
                            await record_runtime_egress_phase(
                                self._cur_session_id or None,
                                self._egress_turn_attempt_id,
                                "model_first_event",
                            )
                        if text and not internal_summarization:
                            await self.event_sink.emit(
                                Event(type="text_delta", data={"text": text})
                            )
                        if thinking_text and not internal_summarization:
                            await self.event_sink.emit(
                                Event(type="thinking_delta", data={"text": thinking_text})
                            )

                    elif event_name == "on_chat_model_end":
                        output = data.get("output")
                        internal_summarization = _is_internal_summarization_event(chunk)
                        full_text = _extract_full_text(output)
                        if full_text and not internal_summarization:
                            await self.event_sink.emit(
                                Event(type="assistant_message", data={"text": full_text})
                            )
                        full_thinking = _extract_full_thinking(output)
                        if full_thinking and not internal_summarization:
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
                                        **(
                                            {"external": True}
                                            if tool_name in self._external_mcp_tool_names
                                            else {}
                                        ),
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
                        citation_content = citation_artifact_content(output)
                        event_data = {
                            "id": run_id,
                            "content": _stringify_tool_output(output),
                            "is_error": is_error,
                        }
                        raw_tool_call_id = getattr(output, "tool_call_id", None)
                        if raw_tool_call_id is None and isinstance(output, dict):
                            raw_tool_call_id = output.get("tool_call_id")
                        if raw_tool_call_id:
                            event_data["citation_join_id"] = str(raw_tool_call_id)
                        if citation_content is not None:
                            event_data["_citation_content"] = citation_content
                        await self.event_sink.emit(
                            Event(
                                type="tool_result",
                                data=event_data,
                            )
                        )

                if self._cancelled:
                    break

                if bare:
                    # Raw chat model: no tools, no HITL, no ``aget_state`` —
                    # a cleanly-ended stream IS the completed turn.
                    break

                # The stream loop exited cleanly — either the graph completed
                # the turn or it paused on an interrupt. Snapshot state to
                # find out which.
                state = await graph.aget_state(stream_config)
                settled_state = state
                for key, tool_name, model_content, citation_content in _state_citation_artifacts(
                    state
                ):
                    if key in known_citation_tool_messages:
                        continue
                    known_citation_tool_messages.add(key)
                    await self.event_sink.emit(
                        Event(
                            type="citation_evidence",
                            data={
                                "tool_name": tool_name,
                                "model_content": model_content,
                                "content": citation_content,
                                "citation_join_id": key,
                            },
                        )
                    )
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
                checkpoint_id = _state_checkpoint_id(settled_state)
                if checkpoint_id:
                    self._turn_anchor = {
                        "provider": "deepagents",
                        "thread_id": str(thread_id),
                        "checkpoint_id": checkpoint_id,
                        "parent_checkpoint_id": start_checkpoint_id,
                    }

            session.status = "idle"
            await self._emit_usage_update(usage_totals)

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
            if is_runtime_interruption(exc):
                # The runtime subprocess went away mid-turn — usually a graceful
                # host stop (resumable ``interrupted``, not a task failure; see
                # codex runtime for the full rationale, and suppress
                # session_error), but the same shape also covers a spontaneous
                # crash. Don't swallow the cause: log it and thread it into the
                # stop_reason so an operator can tell a clean shutdown from a
                # crash. Category/recovery untouched (they key on ``category``).
                cause = describe_exception(exc)
                logger.warning(
                    "deepagents: runtime process interrupted mid-turn for session %s: %s",
                    session.id,
                    cause,
                )
                session.stop_reason = Error(
                    category="interrupted",
                    retry_status="terminal",
                    message=f"runtime process interrupted: {cause}",
                )
            else:
                # See ``describe_exception``: a langgraph / MCP ``ClientSession``
                # failure can arrive wrapped in an ``ExceptionGroup`` whose
                # ``str()`` is the opaque "unhandled errors in a TaskGroup" —
                # unwrap to the leaf so the reason survives, and log the
                # traceback (this branch previously logged nothing).
                cause = describe_exception(exc)
                logger.exception("deepagents: turn failed for session %s: %s", session.id, cause)
                session.stop_reason = Error(
                    category="execution_error",
                    retry_status="exhausted",
                    message=cause,
                )
                await self.event_sink.emit(Event(type="session_error", data={"message": cause}))
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
            await record_runtime_egress_phase(
                self._cur_session_id or None,
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
        """Resume this Runtime/thread with one private terminal no-gap tool."""

        previous = self.toolkit.get(no_op_tool.name)
        self.toolkit.register(no_op_tool)
        # The compiled graph freezes its tool list.  Recompile on the same
        # runtime and checkpointer; ``session.runtime_session_id`` remains the
        # LangGraph thread id, so history and native continuity are preserved.
        self._graph = None
        self._continuing_same_user_turn = True
        try:
            await self.run(session, user_message)
        finally:
            self._continuing_same_user_turn = False
            if previous is None:
                self.toolkit.unregister(no_op_tool.name)
            else:
                self.toolkit.register(previous)
            # Primary turns after Coverage must regain the exact public tool
            # surface instead of inheriting the private protocol tool.
            self._graph = None

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
            self._interrupt_cancels += 1

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
        if getattr(self, "_egress_http_client", None) is not None:
            assert self._egress_http_client is not None
            self._egress_http_client.close()
            self._egress_http_client = None
        if getattr(self, "_egress_http_async_client", None) is not None:
            assert self._egress_http_async_client is not None
            await self._egress_http_async_client.aclose()
            self._egress_http_async_client = None

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
        if (
            self._applied_permission_mode is None
            and self._applied_effort is None
            and self._applied_citation_mode is None
        ):
            return

        new_mode = session.permission_mode
        new_effort = session.model_settings.effort if session.model_settings else None
        new_citation_mode = _session_evidence_binding_enabled(session)

        if (
            new_mode == self._applied_permission_mode
            and new_effort == self._applied_effort
            and new_citation_mode == self._applied_citation_mode
        ):
            return

        # Drop the cached graph so ``_ensure_graph`` rebuilds cleanly.
        # The langchain checkpointer remains open — conversation
        # history survives because ``stream_config`` still carries the
        # same ``thread_id``.
        self._graph = None

    async def _ensure_graph(self, session: Session) -> Any:
        if self._graph is not None:
            return self._graph

        # Bare one-shot completion (``is_bare_completion``): skip the
        # deepagents graph entirely — no base agent prompt, no built-in
        # planning/filesystem/subagent tools, no checkpointer, no HITL.
        # The raw langchain chat model is itself a Runnable, so ``run()``
        # streams it through the same ``astream_events`` loop; the
        # bare-mode branches there feed it a plain message list and skip
        # the graph-only interrupt/state machinery.
        if is_bare_completion(session):
            self._graph = self._build_model_client(session)
            self._cached_permission_mode = session.permission_mode
            self._applied_permission_mode = session.permission_mode
            self._applied_effort = session.model_settings.effort if session.model_settings else None
            self._applied_citation_mode = _session_evidence_binding_enabled(session)
            return self._graph

        # inherit_env=True so the agent shell sees the host's PATH / HOME / etc.
        # (parity with Claude/Codex, which inherit os.environ). Its default is an
        # EMPTY env — no PATH — so anything outside the shell's compiled-in
        # default path fails to resolve: the chrome-devtools wrapper, and in dev
        # even npx/node (nvm). See docs/design/browser-feature.md §8.
        backend = _build_local_shell_backend(self.workspace_root)

        t_build = time.monotonic()
        tools = self._build_tools()
        t_mcp = time.monotonic()
        mcp_tools = await self._build_mcp_tools(session)
        mcp_tools_ms = int((time.monotonic() - t_mcp) * 1000)
        # Capture MCP-origin tool names *before* concatenating so subject
        # classification can distinguish MCP tool calls from harness/builtin
        # ones at approval time. langchain-mcp's ``MultiServerMCPClient``
        # does not prefix tool names with their server (unlike Claude's
        # ``mcp__<server>__<tool>`` convention), so name-membership is our
        # only signal for MCP origin.
        self._mcp_tool_names = {t.name for t in mcp_tools if hasattr(t, "name")}
        if mcp_tools:
            tools = [*tools, *mcp_tools]

        skill_roots = self._materialize_skills(session)
        subagents = self._build_subagents(
            citation_protocol=(
                _citation_system_policy_block(session.instructions)
                if _session_evidence_binding_enabled(session)
                else ""
            ),
            skill_roots=skill_roots,
        )
        t_ckpt = time.monotonic()
        await self._open_checkpointer()
        checkpointer_ms = int((time.monotonic() - t_ckpt) * 1000)

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
        self._applied_citation_mode = _session_evidence_binding_enabled(session)
        interrupt_on = self._build_interrupt_on(session.permission_mode, tools)

        graph_kwargs: dict[str, Any] = {
            "model": self._build_model_client(session),
            "tools": tools,
            "subagents": subagents or None,
            "backend": backend,
            "checkpointer": self._checkpointer,
            "middleware": [
                InvalidToolCallPairMiddleware(),
                ToolErrorTolerantMiddleware(),
                WindowsPathVirtualizerMiddleware(self.workspace_root),
                CitationEvidenceCompactionMiddleware(
                    # Evidence Registry is shared infrastructure.  Always
                    # publish trusted source metadata to the Host; Citation
                    # and Audit switches decide only which post-run sidecars
                    # consume it.
                    evidence_registry=self._turn_evidence_registry,
                    citation_artifact_emitter=self._emit_citation_evidence,
                ),
            ],
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
        # Observability: MCP connect + tools/list is this runtime's dominant
        # variable build cost — surfaced as its own field.
        await self._emit_turn_phase(
            "runtime_init",
            duration_ms=int((time.monotonic() - t_build) * 1000),
            mcp_tools_ms=mcp_tools_ms,
            checkpointer_ms=checkpointer_ms,
        )
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
        # Channel-declared input window for models the langchain profile
        # registry can't know (gateway aliases like ``valuz-pro-anthropic``).
        # Without a profile, deepagents' SummarizationMiddleware falls back
        # to a fixed 170k trigger instead of 0.85 x the real window; the
        # explicit ``profile`` kwarg restores fraction-based compaction.
        # Known model names already get a registry profile — the host only
        # sets ``max_input_tokens`` for declared aliases, never guesses.
        max_input_tokens = (
            session.model_settings.max_input_tokens if session.model_settings is not None else None
        )
        profile = {"max_input_tokens": max_input_tokens} if max_input_tokens else None
        from pydantic import SecretStr

        if protocol == "anthropic":
            from langchain_anthropic import ChatAnthropic

            class _ValuzChatAnthropic(ChatAnthropic):
                """ChatAnthropic with gateway-history compatibility cleanup."""

                def _get_request_payload(
                    self,
                    input_: Any,
                    *,
                    stop: list[str] | None = None,
                    **kwargs: Any,
                ) -> dict[str, Any]:
                    payload = super()._get_request_payload(
                        input_,
                        stop=stop,
                        **kwargs,
                    )
                    return _sanitize_anthropic_request_payload(payload)

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
                default_headers=_session_gateway_headers(session),
            )
            if self.model_provider.base_url is not None:
                kwargs["base_url"] = self.model_provider.base_url
            egress_descriptor = getattr(self, "egress_descriptor", None)
            if egress_descriptor is not None:
                # ChatAnthropic forwards this to both of its locked httpx
                # transports. Unlike global proxy env, it is scoped to this
                # model client and is not inherited by agent tools.
                kwargs["anthropic_proxy"] = egress_descriptor.proxy_url
            if effort is not None:
                kwargs["effort"] = effort
            if profile is not None:
                # Does not disturb the max_tokens default fill below — that
                # reads the bundled registry directly, not ``self.profile``.
                kwargs["profile"] = profile
            # Unset max_tokens lets ChatAnthropic default from its profile
            # registry, which bottoms out at 4096 for model names it doesn't
            # know (gateway aliases, compatible third-party models) — pass an
            # explicit cap for those so long answers don't truncate.
            max_tokens = _resolve_anthropic_max_tokens(self.model)
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            return _ValuzChatAnthropic(**kwargs)

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
            if profile is not None:
                gemini_kwargs["profile"] = profile
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
            # Forward session identity to the gateway so gateway_debit rows
            # are tagged with this conversation's session_id and title. Ignored by
            # first-party providers (api.openai.com etc.).
            default_headers=_session_gateway_headers(session),
        )
        egress_descriptor = getattr(self, "egress_descriptor", None)
        if egress_descriptor is not None:
            if getattr(self, "_egress_http_client", None) is None:
                self._egress_http_client = httpx.Client(
                    proxy=egress_descriptor.proxy_url,
                    # Preserve the OpenAI SDK's locked default budget while
                    # replacing only its transport routing.
                    timeout=httpx.Timeout(600.0, connect=5.0),
                    trust_env=False,
                )
            if getattr(self, "_egress_http_async_client", None) is None:
                self._egress_http_async_client = httpx.AsyncClient(
                    proxy=egress_descriptor.proxy_url,
                    timeout=httpx.Timeout(600.0, connect=5.0),
                    trust_env=False,
                )
            openai_kwargs["http_client"] = self._egress_http_client
            openai_kwargs["http_async_client"] = self._egress_http_async_client
        if self.model_provider.base_url is not None:
            openai_kwargs["base_url"] = self.model_provider.base_url
        if effort is not None:
            openai_kwargs["reasoning_effort"] = _map_effort_for_openai(effort)
        if profile is not None:
            openai_kwargs["profile"] = profile
        return ChatOpenAI(**openai_kwargs)

    async def _open_checkpointer(self) -> Any:
        """Open the checkpointer once per runtime instance (cached until close()).

        Thread-id (= session.id) lets langgraph rehydrate conversation state on
        the next turn. Two backends, selected by :func:`_checkpoint_backend`
        (deployment gate by default, ``DEEPAGENTS_CHECKPOINT_BACKEND`` to pin):

        - **file** (sandbox default): ``FileCheckpointSaver`` over a per-owner
          COS mount — write-once JSON files survive sandbox recreation and, unlike
          sqlite, never corrupt on COS FUSE. No CM / no ``setup()``.
        - **sqlite** (local default): the SQLite store on durable local disk. The
          CM is held until ``close()`` so the cached graph keeps a live connection.

        The two stores are independent: switching backends starts from an empty
        checkpoint history, so in-flight sessions cannot rehydrate across a flip.
        """
        if self._checkpointer is not None:
            return self._checkpointer
        if _checkpoint_backend() == "file":
            from src.adapters.file_checkpoint_saver import FileCheckpointSaver

            os.makedirs(self.checkpoint_root, exist_ok=True)
            self._checkpointer = FileCheckpointSaver(self.checkpoint_root)
            self._checkpointer_cm = None  # file saver has no context manager
            return self._checkpointer
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        directory = os.path.dirname(self.checkpoint_db)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # A warm-runtime eviction closes the previous session's SQLite saver
        # immediately before the next session opens its own one.  On macOS,
        # removing/recreating WAL sidecars can briefly surface as SQLITE_IOERR
        # even though the database itself is healthy.  Never retain the
        # half-open saver: close it, yield once for the sidecar cleanup, and
        # retry exactly once.  Other SQLite errors still fail immediately.
        for attempt in range(2):
            checkpointer_cm = AsyncSqliteSaver.from_conn_string(self.checkpoint_db)
            checkpointer = await checkpointer_cm.__aenter__()
            try:
                await checkpointer.setup()
            except sqlite3.OperationalError as exc:
                await checkpointer_cm.__aexit__(type(exc), exc, exc.__traceback__)
                if attempt > 0 or "disk i/o error" not in str(exc).lower():
                    raise
                logger.warning(
                    "DeepAgents SQLite checkpointer setup hit a transient disk I/O "
                    "error; retrying once"
                )
                await asyncio.sleep(0.05)
                continue
            self._checkpointer_cm = checkpointer_cm
            self._checkpointer = checkpointer
            return checkpointer
        raise RuntimeError("unreachable")

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
        self._external_mcp_tool_names = set()
        if not session.mcp_servers:
            return []
        try:
            from langchain_mcp_adapters.callbacks import Callbacks
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
                # A stdio child is spawned by THIS process, so a which() miss
                # here is definitive — spawning anyway would surface as a bare
                # ``FileNotFoundError: [Errno 2]`` from deep inside anyio with
                # no hint of which server or command was at fault.
                if shutil.which(cfg.command) is None:
                    logger.warning(
                        "mcp server %r skipped: stdio command %r not found on PATH",
                        cfg.name,
                        cfg.command,
                    )
                    continue
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
            # A server that declares how long its tools may run must have that
            # honoured here too. Measured, every client aborts exactly at its
            # ceiling with progress still arriving (codex at ``tool_timeout_sec``,
            # the Claude CLI at its per-server ``timeout``), and here
            # ``mcp.shared.session`` wraps the receive in ``anyio.fail_after``,
            # which no notification restarts. The ceiling is therefore the only
            # lever, and a long generation dies on the default unless BOTH the
            # transport read and the session read are raised.
            timeout_sec = getattr(cfg, "tool_timeout_sec", None)
            if isinstance(timeout_sec, (int, float)) and timeout_sec > 0:
                # The two transports type this field differently
                # (``SSEConnection`` seconds-as-float vs
                # ``StreamableHttpConnection`` timedelta); give each its own.
                entry["sse_read_timeout"] = (
                    timedelta(seconds=float(timeout_sec))
                    if transport == "streamable_http"
                    else float(timeout_sec)
                )
                entry["session_kwargs"] = {
                    "read_timeout_seconds": timedelta(seconds=float(timeout_sec))
                }
            spec[cfg.name] = entry

        if not spec:
            return []
        client = MultiServerMCPClient(
            spec,  # type: ignore[arg-type]
            tool_interceptors=[_preserve_mcp_source_metadata],
            # The MCP SDK only attaches ``_meta.progressToken`` when a progress
            # callback exists, so a client with none tells servers "do not
            # bother" and gets silence back. Registering one makes long tools
            # observable (the host's ``generate_ui`` heartbeats through it);
            # it cannot extend this client's hard timeout, so it is for
            # visibility, not for survival — see the timeout mapping above.
            callbacks=Callbacks(on_progress=_log_mcp_progress),
        )
        # Load per server instead of one ``get_tools()`` over everything: the
        # aggregate call fails the WHOLE turn when any single server is
        # unreachable. The CLI runtimes degrade to "server unavailable, tools
        # absent" — match that here. ``gather`` keeps the adapter's previous
        # concurrent-connect behavior.
        names = list(spec)
        results = await asyncio.gather(
            *(client.get_tools(server_name=name) for name in names),
            return_exceptions=True,
        )
        tools: list[Any] = []
        external_tool_names: set[str] = set()
        for name, result in zip(names, results, strict=True):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                logger.warning("mcp server %r unavailable — skipping its tools: %s", name, result)
                continue
            tools.extend(result)
            if name not in {"harness", "harness_toolkit"}:
                external_tool_names.update(
                    tool.name for tool in result if isinstance(getattr(tool, "name", None), str)
                )
        self._external_mcp_tool_names = external_tool_names
        return tools

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
                    user_id=self._cur_user_id,
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
            # A no-gap decision must terminate the agent loop at the tool
            # boundary.  Otherwise LangGraph invokes the model once more and
            # many providers manufacture a visible "(empty)" confirmation.
            return_direct=tdef.name == TASK_COVERAGE_NOOP_TOOL_NAME,
        )

    def _build_subagents(
        self,
        *,
        citation_protocol: str = "",
        skill_roots: list[str] | None = None,
    ) -> list[SubAgent]:
        subagents: list[SubAgent] = []
        for sub_def in self.config.callable_agents:
            subagents.append(
                self._to_subagent(
                    sub_def,
                    citation_protocol=citation_protocol,
                )
            )
        if citation_protocol and not any(
            subagent["name"] == GENERAL_PURPOSE_SUBAGENT["name"] for subagent in subagents
        ):
            # DeepAgents auto-creates ``general-purpose`` with a private
            # system prompt and middleware stack. Explicitly mirror that
            # default only while Evidence binding is active so nested source
            # tools expose the same handles and the nested assistant knows the
            # same minimal protocol. Parent task instructions, Host plans, and
            # other session text are intentionally not copied.
            general_purpose: SubAgent = {
                "name": GENERAL_PURPOSE_SUBAGENT["name"],
                "description": GENERAL_PURPOSE_SUBAGENT["description"],
                "system_prompt": (
                    f"{GENERAL_PURPOSE_SUBAGENT['system_prompt']}\n\n{citation_protocol}"
                ),
                "middleware": [
                    InvalidToolCallPairMiddleware(),
                    WindowsPathVirtualizerMiddleware(self.workspace_root),
                    CitationEvidenceCompactionMiddleware(
                        evidence_registry=self._turn_evidence_registry,
                        citation_artifact_emitter=self._emit_citation_evidence,
                    ),
                ],
            }
            if skill_roots:
                general_purpose["skills"] = list(skill_roots)
            subagents.insert(0, general_purpose)
        return subagents

    def _to_subagent(
        self,
        sub_def: SubAgentDef,
        *,
        citation_protocol: str = "",
    ) -> SubAgent:
        sub_tools: list[StructuredTool] = []
        if sub_def.tools:
            for name in sub_def.tools:
                tdef = self.toolkit.get(name)
                if tdef and tdef.handler and tdef.permission != "deny":
                    sub_tools.append(self._to_structured_tool(tdef))

        entry: SubAgent = {
            "name": sub_def.name,
            "description": sub_def.description,
            "system_prompt": (
                f"{sub_def.prompt}\n\n{citation_protocol}".strip()
                if citation_protocol
                else sub_def.prompt
            ),
        }
        # Subagent ``middleware`` is additive (appended to deepagents' default
        # stack), so always carry the Windows-path normalizer; the citation
        # pair stays gated on the protocol as before.
        entry["middleware"] = [WindowsPathVirtualizerMiddleware(self.workspace_root)]
        if citation_protocol:
            entry["middleware"] += [
                InvalidToolCallPairMiddleware(),
                CitationEvidenceCompactionMiddleware(
                    evidence_registry=self._turn_evidence_registry,
                    citation_artifact_emitter=self._emit_citation_evidence,
                ),
            ]
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


# Explicit ``max_tokens`` for anthropic-protocol models langchain's profile
# registry doesn't know. 32k is Claude Code's own default output cap: valid
# for every current Claude model and battle-tested against the
# anthropic-compatible gateways where unknown names actually occur.
_ANTHROPIC_UNKNOWN_MODEL_MAX_TOKENS = 32_000


def _resolve_anthropic_max_tokens(model: str) -> int | None:
    """Resolve an explicit ``max_tokens`` for ``ChatAnthropic``, or ``None``.

    ``ChatAnthropic`` fills an unset ``max_tokens`` from a bundled per-model
    profile registry keyed by EXACT model name; any name it doesn't know —
    gateway aliases like ``openrouter/claude-sonnet-4-5`` or
    anthropic-compatible third-party models — silently falls back to 4096,
    which truncates long answers with ``stop_reason: max_tokens``.

    Returns ``None`` on a registry hit (ChatAnthropic's own per-model default
    is correct — e.g. 64k for sonnet-4-x, 128k for opus-4-6+). On a miss,
    retries with a leading ``vendor/`` gateway prefix stripped and forwards
    that profile's cap; otherwise returns the safe fallback above.
    """
    try:
        from langchain_anthropic.chat_models import _get_default_model_profile
    except ImportError:
        # Private helper moved in a langchain-anthropic upgrade: we can no
        # longer tell known names from unknown, so degrade to pre-workaround
        # behavior (knowns stay correct, unknowns fall back to langchain's
        # 4096). test_deepagents_max_tokens pins the import so the upgrade
        # PR revisits this instead of shipping the regression silently.
        return None

    if _get_default_model_profile(model).get("max_output_tokens") is not None:
        return None
    tail = model.rsplit("/", 1)[-1]
    if tail != model:
        cap = _get_default_model_profile(tail).get("max_output_tokens")
        if cap is not None:
            return int(cap)
    return _ANTHROPIC_UNKNOWN_MODEL_MAX_TOKENS


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

    LangChain's ``input_tokens`` is the SUM of every input bucket — its own
    ``total_tokens`` is ``input_tokens + output_tokens`` — and
    ``input_token_details.cache_read`` / ``cache_creation`` are subsets of it.
    The cross-runtime contract is the opposite: the four flat fields are
    DISJOINT, because every usage surface adds them up (session panel,
    monthly rollup, task usage, billing meter). Passing LangChain's total
    through unchanged therefore counted the cached prefix twice — on a real
    session, a turn with 77,859 prompt tokens of which 75,904 were cache hits
    was reported as 153,763, +97%, and dragged the displayed cache hit rate
    down from 97.5% to 49.4%. Subtract the cached buckets out, the same way
    the codex runtime does with ``cached_input_tokens``.

    The ``response_metadata`` fallback below carries no cache detail at all,
    so its ``prompt_tokens`` is reported as fully uncached — nothing to
    subtract, and no double count either.
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
            "input_tokens": max(0, int(metadata.get("input_tokens", 0)) - cache_read - cache_write),
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


def _state_checkpoint_id(state: Any) -> str | None:
    """Extract the langgraph checkpoint id a ``StateSnapshot`` points at.

    ``aget_state`` returns the id under ``config["configurable"]
    ["checkpoint_id"]``; a fresh thread (no checkpoints yet) has none.
    """
    config = getattr(state, "config", None)
    if not isinstance(config, dict):
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    value = configurable.get("checkpoint_id")
    return str(value) if value else None


def _state_citation_artifacts(state: Any) -> list[tuple[str, str | None, Any, str]]:
    """Return private evidence sidecars added by graph tool middleware.

    LangChain emits the underlying tool's ``on_tool_end`` event before
    ``awrap_tool_call`` has attached its compacted evidence artifact. The
    completed graph state contains the final ToolMessage, so replay only those
    private artifacts into Citation Guard before the answer is sealed.
    """

    values = getattr(state, "values", None)
    if not isinstance(values, dict):
        return []
    messages = values.get("messages")
    if not isinstance(messages, list):
        return []
    artifacts: list[tuple[str, str | None, Any, str]] = []
    for index, message in enumerate(messages):
        artifact = getattr(message, "artifact", None)
        if not isinstance(artifact, dict):
            continue
        citation_content = artifact.get("_valuz_citation_content")
        if not isinstance(citation_content, str):
            continue
        raw_id = getattr(message, "tool_call_id", None) or getattr(message, "id", None)
        key = str(raw_id or f"tool-message-{index}")
        raw_name = getattr(message, "name", None)
        model_content = getattr(message, "content", None)
        artifacts.append(
            (key, str(raw_name) if raw_name else None, model_content, citation_content)
        )
    return artifacts


def _stringify_tool_output(output: Any) -> str:
    if output is None:
        return ""
    content = getattr(output, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return json.dumps(_jsonify(content), ensure_ascii=False)
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

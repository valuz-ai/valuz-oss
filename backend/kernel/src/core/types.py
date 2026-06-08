"""Foundation types — Session, StopReason variants, and structured input."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.core.time_utils import now_ms

# -- Structured user input --


@dataclass(frozen=True)
class Attachment:
    """A file the user has attached to a turn.

    Upstream uploads files into the project's workspace and hands the harness
    their paths. The kernel does not move bytes — it only references.

    ``source_path`` is the original file the user attached (always present); the
    agent acts on this when it needs the raw bytes (rename it, run a tool over
    it, …). ``parsed_path`` is an optional extracted/normalized rendering the
    upstream produced — e.g. a Markdown text extract of a PDF — so the agent can
    read the content cheaply; it is ``None`` when the upstream did no parsing.
    ``build_user_prompt`` surfaces both, so the agent can read the extract yet
    still operate on the original.
    """

    source_path: str
    parsed_path: str | None = None


@dataclass(frozen=True)
class UserMessage:
    """One user input — free-form text plus optional file attachments and
    upstream-injected ``additional_context``.

    ``additional_context`` is an upstream-controlled string that the kernel
    renders verbatim into a ``<additional-context>`` block ahead of the
    user's text. It is the channel for runtime context the upstream system
    wants the agent to perceive but that doesn't belong in the user's own
    message — for example a user-selected knowledge-base file tree, the
    current business scenario tag, or a feature-flag summary. Empty string
    means no block is emitted.
    """

    text: str
    attachments: tuple[Attachment, ...] = ()
    additional_context: str = ""


# -- Model provider + settings --


ApiProtocol = Literal[
    "anthropic",
    "openai_completion",
    "openai_response",
    "gemini",
]


@dataclass(frozen=True)
class ModelProvider:
    """User-supplied model gateway.

    ``api_protocol`` is the wire protocol the runtime uses to call the
    gateway (``anthropic`` = Anthropic Messages API; ``openai_completion``
    = OpenAI Chat Completions; ``openai_response`` = OpenAI Responses API
    aka codex; ``gemini`` = Google Gemini API). The harness enforces
    which protocols are valid per ``runtime_provider`` at session create
    time — see ``backend/src/runtimes/factory.py``.

    ``base_url`` is optional: omit it for first-party vendors
    (Anthropic / OpenAI / Google) whose endpoint is baked into the SDK.
    Each runtime falls back to its SDK's ambient endpoint when
    ``base_url is None`` and still threads ``api_key`` through the
    corresponding standard env var.

    The kernel does not maintain a curated model catalog — every session
    must carry its own provider so the harness stays generic.
    """

    api_key: str
    base_url: str | None = None
    api_protocol: ApiProtocol = "anthropic"


# Cross-runtime reasoning-effort literal. Each runtime's matcher /
# config-overrides maps this to the SDK-supported subset (codex caps at
# ``xhigh``, Gemini's ``thinking_level`` caps at ``high``); Anthropic /
# Claude SDK natively supports the full range. The harness deliberately
# accepts the union — the UI is expected to surface per-model availability.
EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True)
class ModelSettings:
    """Optional per-session sampling / limit / reasoning knobs.

    ``temperature`` and ``max_tokens`` are persisted and round-tripped
    but not currently consumed by runtimes. ``effort`` is the live
    reasoning-budget lever — each runtime threads it through to its
    SDK's native control:

    * ``ClaudeAgentRuntime`` -> ``ClaudeAgentOptions.effort``
    * ``CodexRuntime`` -> ``model_reasoning_effort`` config override
    * ``DeepAgentsRuntime`` -> langchain backend kwarg
      (``reasoning_effort`` / ``effort`` / ``thinking_level``)
    """

    temperature: float | None = None
    max_tokens: int | None = None
    effort: EffortLevel | None = None


# -- MCP server config (tagged union) --


@dataclass(frozen=True)
class McpHttpServerConfig:
    """Remote MCP server reachable over HTTP / SSE.

    ``transport`` distinguishes streamable HTTP (default) from legacy SSE —
    the wire URL and header set are the same; runtime adapters pick the
    right SDK constant (e.g. langchain calls our ``http``
    ``"streamable_http"``).
    """

    name: str
    url: str
    transport: Literal["http", "sse"] = "http"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class McpStdioServerConfig:
    """Local MCP server launched as a subprocess of the harness.

    The session owner is responsible for vetting ``command`` — it runs
    with the harness process's own privileges. Use ``env_vars`` for
    secrets so values come from the harness process env (e.g. ``.env``)
    instead of being persisted in the sessions DB row; ``env`` is for
    non-secret tunables that are fine to live in config (e.g. log levels).

    See ``docs/design/MCP-SERVERS.md`` for the full lifecycle, env
    semantics, and trust boundary.
    """

    name: str
    transport: Literal["stdio"] = "stdio"
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    env_vars: tuple[str, ...] = ()


# Tagged union over the two transport-specific shapes. Callers dispatch
# via ``isinstance`` (or ``cfg.transport`` for the http/sse split).
McpServerConfig = McpHttpServerConfig | McpStdioServerConfig


# -- StopReason variants --


@dataclass
class EndTurn:
    """Agent completed a turn."""

    type: Literal["end_turn"] = "end_turn"


@dataclass
class BudgetExhausted:
    """Budget exhausted."""

    type: Literal["budget_exhausted"] = "budget_exhausted"
    reason: Literal["max_turns", "max_cost"] = "max_turns"


@dataclass
class Error:
    """Runtime error."""

    type: Literal["error"] = "error"
    category: str = ""
    retry_status: Literal["retrying", "exhausted", "terminal"] = "exhausted"
    message: str = ""


@dataclass
class UserInterrupt:
    """User-initiated interrupt."""

    type: Literal["user_interrupt"] = "user_interrupt"


StopReason = EndTurn | BudgetExhausted | Error | UserInterrupt


# -- Session --


RuntimeProvider = Literal["claude_agent", "codex", "deepagents"]


@dataclass
class Session:
    """Execution session — belongs to a Project, references an Agent.

    ``cwd`` overrides the working directory for this session's runtime; when
    empty the runtime falls back to the parent project's cwd. This lets a host
    give each session an isolated working directory (e.g. per-task / per-run)
    without minting a project per session. ``agent_id`` is required
    at creation; sessions seed ``instructions`` / ``skills`` /
    ``mcp_servers`` / ``model`` from the chosen agent on the frontend, but
    the values persisted here are the source of truth that the runtime
    reads — the agent's defaults are not consulted again post-creation.

    ``runtime_provider`` is the explicit dispatch key picked at creation
    and fixed for the session's lifetime — it determines which runtime
    drives the loop. ``model`` and ``model_provider`` are optional for
    ``claude_agent`` / ``codex`` (each runtime falls back to its SDK's
    ambient credentials), but required for ``deepagents`` because
    langchain needs an explicit model client.
    """

    id: str
    project_id: str
    agent_id: str
    # Per-session working directory override. Empty ("") = fall back to the
    # parent project's cwd. Set to give a session an isolated workdir.
    cwd: str = ""
    runtime_provider: RuntimeProvider = "claude_agent"
    model: str = ""
    model_provider: ModelProvider | None = None
    model_settings: ModelSettings | None = None
    instructions: str = ""
    skills: tuple[str, ...] = ()
    mcp_servers: tuple[McpServerConfig, ...] = ()
    permission_mode: Literal["default", "auto_review", "full_access"] = "full_access"
    mode: Literal["default", "plan", "goal"] = "default"
    status: Literal["created", "idle", "running", "terminated"] = "created"
    stop_reason: StopReason | None = None
    created_at: int = field(default_factory=now_ms)  # Unix epoch ms (UTC)
    metadata: dict[str, Any] = field(default_factory=dict)
    # Native session/thread id assigned by the underlying runtime SDK
    # (Claude Agent SDK session id, langgraph thread id, etc.). Used to
    # resume conversation state across process restarts.
    runtime_session_id: str | None = None
    # Latest TODO list snapshot, captured from `todo_update` events. Each
    # entry is `{"content": str, "status": "pending|in_progress|completed",
    # "activeForm"?: str}`. None when the agent has never updated todos in
    # this session.
    todos: list[dict[str, Any]] | None = None


# -- Message --

MessageStatus = Literal["running", "completed", "errored", "cancelled"]


@dataclass
class Message:
    """One run inside a Session — a single user input -> assistant turn.

    Each call to `SessionOrchestrator.run_turn` creates exactly one Message.
    Events emitted during the run carry the message_id so they can be
    reassembled per-run on history reads.

    Token usage fields are populated from the runtime's terminal
    ``usage_update`` event. Flat columns hold the cross-model aggregate;
    ``model_usage`` keeps the SDK-native per-model breakdown for forward
    compatibility (sub-agent attribution, reasoning tokens, etc.).
    """

    id: str
    session_id: str
    user_message: UserMessage
    started_at: int  # Unix epoch ms (UTC)
    status: MessageStatus = "running"
    assistant_message: str | None = None
    error_message: dict[str, Any] | None = None
    stop_reason: StopReason | None = None
    total_turns: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    model_usage: dict[str, Any] | None = None
    ended_at: int | None = None  # Unix epoch ms (UTC)
    metadata: dict[str, Any] = field(default_factory=dict)
    # End-of-turn TODO snapshot — only populated when the agent emitted at
    # least one `todo_update` during this turn. None means "this turn did
    # not modify the todo list" (carry-forward is a UI concern).
    todos: list[dict[str, Any]] | None = None

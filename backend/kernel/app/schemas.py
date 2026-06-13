"""Pydantic request/response schemas — matching api/openapi.yaml."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator

# -- Response envelope primitives --


class ApiError(BaseModel):
    code: str
    message: str


# -- Nested schemas --


class ToolDefSchema(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = False
    permission: Literal["auto", "ask", "deny"] = "auto"


class SubAgentDefSchema(BaseModel):
    name: str
    description: str = ""
    prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    model: str | None = None
    skills: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StopReasonSchema(BaseModel):
    type: str
    reason: str | None = None
    message: str | None = None
    category: str | None = None
    retry_status: str | None = None
    actions: list[dict[str, Any]] | None = None


class AttachmentSchema(BaseModel):
    source_path: str
    parsed_path: str | None = None


class UserMessageSchema(BaseModel):
    text: str
    attachments: list[AttachmentSchema] = Field(default_factory=list)


class McpHttpServerConfigSchema(BaseModel):
    """Remote MCP server reachable over HTTP / SSE."""

    name: str
    url: str
    transport: Literal["http", "sse"] = "http"
    headers: dict[str, str] = Field(default_factory=dict)


class McpStdioServerConfigSchema(BaseModel):
    """Local MCP server launched as a subprocess of the harness.

    See ``docs/design/MCP-SERVERS.md`` for env / env_vars semantics and
    the trust boundary.
    """

    name: str
    transport: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_vars: list[str] = Field(default_factory=list)


def _default_transport_to_http(value: Any) -> Any:
    """Pydantic v2 discriminated unions require the discriminator field to
    be present in the input. The OpenAPI contract has ``transport`` default
    to ``http`` though, so fill it in for callers that omit it before the
    discriminator runs.

    Wrap the BeforeValidator *outside* the discriminated union annotation
    so it executes before tag extraction; the inner Field(discriminator=...)
    only sees inputs that already carry ``transport``.
    """
    if isinstance(value, dict) and "transport" not in value:
        return {**value, "transport": "http"}
    return value


# Tagged union — Pydantic dispatches on the ``transport`` field.
McpServerConfigSchema = Annotated[
    Annotated[
        McpHttpServerConfigSchema | McpStdioServerConfigSchema,
        Field(discriminator="transport"),
    ],
    BeforeValidator(_default_transport_to_http),
]


# Cross-runtime wire-protocol enum. See ``backend/src/core/types.py`` for
# the per-runtime allowed subsets (factory.py enforces). The legacy
# ``openai`` value is split: ``openai_completion`` for chat completions
# (DeepAgents) vs ``openai_response`` for the Responses API (Codex).
ApiProtocolLiteral = Literal[
    "anthropic",
    "openai_completion",
    "openai_response",
    "gemini",
]

EffortLiteral = Literal["low", "medium", "high", "xhigh", "max"]


class ModelProviderInputSchema(BaseModel):
    """User-supplied model gateway. ``api_protocol`` selects the wire
    protocol; the route layer enforces per-runtime compatibility.

    ``base_url`` is optional — omit it for first-party vendors whose
    endpoint is baked into the SDK; each runtime falls back to its
    SDK's ambient default and still wires ``api_key`` into the
    standard env-var channel."""

    base_url: str | None = None
    api_key: str
    api_protocol: ApiProtocolLiteral = "anthropic"


class ModelProviderUpdateSchema(BaseModel):
    """PATCH variant — ``api_key`` is optional so callers can keep the stored
    key by omitting the field. Empty string is rejected at the route level.
    ``base_url`` is optional in the same first-party-fallback sense as the
    input schema."""

    base_url: str | None = None
    api_key: str | None = None
    api_protocol: ApiProtocolLiteral = "anthropic"


class ModelProviderResponseSchema(BaseModel):
    """Read-only response shape — never returns ``api_key``.

    ``base_url`` is ``None`` when the session targets a first-party
    vendor and the runtime falls back to its SDK's ambient endpoint."""

    base_url: str | None = None
    api_protocol: ApiProtocolLiteral


class ModelSettingsSchema(BaseModel):
    """Per-session sampling / limit / reasoning knobs.

    ``effort`` is the cross-runtime reasoning-budget lever — see
    ``ModelSettings`` in ``backend/src/core/types.py`` for the per-
    runtime mapping. The harness accepts the full union; runtimes that
    don't support a level map it down.
    """

    temperature: float | None = None
    max_tokens: int | None = None
    effort: EffortLiteral | None = None


RuntimeProvider = Literal["claude_agent", "codex", "deepagents"]


# -- Agent snapshot --


class AgentConfigSchema(BaseModel):
    """Wire shape of the embedded agent snapshot (``Session.agent_config``).

    Mirrors ``core.agent_config.AgentConfig`` minus runtime-only fields
    (hooks) and row-lifecycle fields (status / created_at). ``name`` is the
    only required field; everything else defaults like the dataclass.
    """

    id: str = ""
    name: str
    model: str = "claude-sonnet-4-6"
    runtime_provider: RuntimeProvider = "claude_agent"
    instructions: str = ""
    permission_mode: Literal["default", "auto_review", "full_access"] = "full_access"
    max_turns: int = 50
    max_cost_usd: float = 10.0
    tools: list[ToolDefSchema] = Field(default_factory=list)
    callable_agents: list[SubAgentDefSchema] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerConfigSchema] = Field(default_factory=list)
    effort: EffortLiteral | None = None
    thinking: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# -- Session --


class CreateSessionRequest(BaseModel):
    # Optional client-supplied id (UUID-shaped). Hosts pre-mint the id so
    # side-tables and per-session tokens can reference the session before
    # the create round-trip; omitted → the kernel mints one.
    id: str | None = None
    agent_config: AgentConfigSchema
    cwd: str
    mode: Literal["default", "plan", "goal"] = "default"
    runtime_provider: RuntimeProvider
    model: str = ""
    model_provider: ModelProviderInputSchema | None = None
    model_settings: ModelSettingsSchema | None = None
    instructions: str = ""
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerConfigSchema] = Field(default_factory=list)
    permission_mode: Literal["default", "auto_review", "full_access"] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateSessionRequest(BaseModel):
    instructions: str | None = None
    skills: list[str] | None = None
    mcp_servers: list[McpServerConfigSchema] | None = None
    model_provider: ModelProviderUpdateSchema | None = None
    model_settings: ModelSettingsSchema | None = None
    permission_mode: Literal["default", "auto_review", "full_access"] | None = None
    cwd: str | None = None
    metadata: dict[str, Any] | None = None


class EventPayload(BaseModel):
    """Out-of-band event append (``POST /sessions/{id}/events``).

    The kernel anchors the event onto the session's most recent message —
    callers (recovery, interrupt fallback, skill-candidate detection) don't
    hold a message id.
    """

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class AppendEventData(BaseModel):
    persisted: bool


class AppendEventResponse(BaseModel):
    data: AppendEventData
    error: ApiError | None = None


class FinalizeSessionRequest(BaseModel):
    """Terminal/idle state flip for out-of-band supervisors
    (``POST /sessions/{id}/finalize``).

    Used by boot recovery (running → terminated after a crash) and the
    interrupt fallback (running → idle with UserInterrupt). ``error_event``
    is appended after the flip when provided. Idempotent: flipping to the
    already-current status is a no-op success.
    """

    # ``running`` is the optimistic pre-run flip an in-process host applies
    # for immediate status UX before the turn actually starts; remote hosts
    # observe the WS run channel instead. ``idle``/``terminated`` are the
    # supervisor finalizations (recovery, interrupt fallback, cancel).
    status: Literal["running", "idle", "terminated"]
    stop_reason_type: Literal["user_interrupt", "error"] | None = None
    stop_reason_message: str | None = None
    # Optional metadata replacement applied atomically with the flip (the
    # supervisor usually stamps bookkeeping like last_user_message_text).
    metadata: dict[str, Any] | None = None
    error_event: EventPayload | None = None


class SetSessionModeRequest(BaseModel):
    """Body for ``POST /sessions/{id}/mode``.

    ``mode`` carries the full operational state; no separate payload —
    goal's condition is the *next* non-slash user message, wrapped to
    ``/goal <text>`` by the orchestrator. See ``docs/design/session-modes.md``.
    """

    mode: Literal["default", "plan", "goal"]


class TodoItem(BaseModel):
    content: str
    status: str
    # camelCase preserved to match the Claude Agent SDK's TodoWrite payload
    # verbatim — renaming would force aliasing on every (de)serialize and
    # diverge the wire shape from upstream.
    activeForm: str | None = None  # noqa: N815


class SessionData(BaseModel):
    id: str
    # Owner id of the session (the kernel row's ``user_id``). Surfaced over the
    # wire so the host can scope its own owner-filtered reads to whoever owns a
    # session — host background paths (tool handlers, capability refresh) often
    # hold only a ``SessionData`` and have no other way to recover the owner.
    # Empty string for sessions created before this field existed.
    user_id: str = ""
    agent_config: AgentConfigSchema
    runtime_provider: RuntimeProvider
    cwd: str = ""
    model: str = ""
    model_provider: ModelProviderResponseSchema | None = None
    model_settings: ModelSettingsSchema | None = None
    instructions: str = ""
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerConfigSchema] = Field(default_factory=list)
    permission_mode: Literal["default", "auto_review", "full_access"] = "full_access"
    mode: Literal["default", "plan", "goal"] = "default"
    status: str
    stop_reason: StopReasonSchema | None = None
    created_at: int  # Unix epoch milliseconds (UTC)
    metadata: dict[str, Any] = Field(default_factory=dict)
    runtime_session_id: str | None = None
    todos: list[TodoItem] | None = None


class SessionResponse(BaseModel):
    data: SessionData
    error: ApiError | None = None


class SessionListResponse(BaseModel):
    data: list[SessionData]
    error: ApiError | None = None


# -- Approval contract (Phase 1 / Slice 2) --


class SubmitActionRequest(BaseModel):
    pending_id: str
    # ``approve_for_session`` (v2) attaches a session-scoped rule so the next
    # matching tool call short-circuits with the synthetic ``auto_approved``
    # verb. ``auto_approved`` itself is kernel-emit-only and intentionally
    # absent from this Literal — Pydantic rejects it as input symmetric to
    # how ``expired`` / ``interrupted`` are rejected.
    decision: Literal["approve", "approve_with_changes", "approve_for_session", "reject", "answer"]
    message: str | None = None
    # ``answers`` carries the structured selection map for a
    # ``clarifying_questions`` pending (Claude SDK ``AskUserQuestion``).
    # Each key is the question text (matches ``payload.questions[].question``
    # from the ``requires_action`` event), each value is the selected
    # option's ``label`` for single-select or a list of labels for
    # multi-select. For free-text "Other" replies the value is the
    # user's raw text rather than the literal string ``"Other"``.
    answers: dict[str, str | list[str]] | None = None
    # ``modified_input`` carries the replacement args dict for an
    # ``approve_with_changes`` decision. Same shape as the original
    # tool input from the pending's payload — Claude maps it to
    # ``PermissionResultAllow.updated_input``, DeepAgents to
    # ``EditDecision.edited_action.args`` (the original tool name is
    # preserved by the runtime; only args are editable in v1).
    modified_input: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _enforce_decision_payload_invariants(self) -> SubmitActionRequest:
        # Each "payload-carrying" verb (``answer`` / ``approve_with_changes``)
        # has a 1:1 invariant with its payload field; defense-in-depth so
        # the orchestrator gets a clean shape regardless of what the wire
        # sent. The subject↔decision validation (e.g. ``answer`` is only
        # valid for ``clarifying_questions``) lives in the orchestrator
        # since the subject is server-side state not in this request.
        # ``approve_for_session`` itself carries no client-side payload —
        # the rule comes from the pending's ``session_rule_preview`` field
        # which the runtime populated at emit time. The orchestrator's
        # gate at submit time enforces preview presence (400 if missing).
        if self.decision == "answer" and self.answers is None:
            raise ValueError("decision='answer' requires the 'answers' field")
        if self.decision != "answer" and self.answers is not None:
            raise ValueError(
                f"'answers' is only valid with decision='answer'; got decision={self.decision!r}"
            )
        if self.decision == "approve_with_changes" and self.modified_input is None:
            raise ValueError("decision='approve_with_changes' requires the 'modified_input' field")
        if self.decision != "approve_with_changes" and self.modified_input is not None:
            raise ValueError(
                f"'modified_input' is only valid with decision='approve_with_changes'; "
                f"got decision={self.decision!r}"
            )
        return self


class SubmitActionData(BaseModel):
    session_id: str
    pending_id: str
    decision: Literal["approve", "approve_with_changes", "approve_for_session", "reject", "answer"]
    accepted_at: int  # Unix epoch milliseconds (UTC)
    idempotent: bool = False
    # Set when ``decision == "approve_for_session"`` — the kernel-assigned
    # UUID for the rule just attached. Frontend uses it to render the
    # rule badge on the resolved card and to trace future
    # ``auto_approved`` events back to their originating pending.
    rule_id: str | None = None


class SubmitActionResponse(BaseModel):
    data: SubmitActionData
    error: ApiError | None = None


# -- Messages --


class MessageData(BaseModel):
    id: str
    session_id: str
    user_message: UserMessageSchema
    assistant_message: str | None = None
    error_message: dict[str, Any] | None = None
    status: str
    stop_reason: StopReasonSchema | None = None
    total_turns: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    model_usage: dict[str, Any] | None = None
    started_at: int  # Unix epoch milliseconds (UTC)
    ended_at: int | None = None  # Unix epoch milliseconds (UTC)
    metadata: dict[str, Any] = Field(default_factory=dict)
    todos: list[TodoItem] | None = None


class MessageResponse(BaseModel):
    data: MessageData
    error: ApiError | None = None


class MessageListResponse(BaseModel):
    data: list[MessageData]
    error: ApiError | None = None


# -- Events --


class EventData(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: int  # Unix epoch milliseconds (UTC)
    # Storage coordinates — populated on cursor reads (``after_seq`` /
    # window queries) and on stream frames sourced from the DB. ``None``
    # for live (non-persisted) frames and legacy offset reads.
    seq: int | None = None
    message_id: str | None = None
    # Populated on the global (all-sessions) stream so subscribers don't
    # re-derive routing; ``None`` on session-scoped reads.
    session_id: str | None = None


class EventListResponse(BaseModel):
    data: list[EventData]
    error: ApiError | None = None


class EventWindowData(BaseModel):
    """Turn-aligned page of events (see ``GET /sessions/{id}/events/window``)."""

    items: list[EventData]
    has_more: bool


class EventWindowResponse(BaseModel):
    data: EventWindowData
    error: ApiError | None = None


# -- Usage aggregates --


class UsageRollupData(BaseModel):
    """Per-(UTC day, model) usage aggregate over completed messages."""

    day: str
    model: str
    request_count: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class UsageRollupResponse(BaseModel):
    data: list[UsageRollupData]
    error: ApiError | None = None


# -- Generic envelope --


class DataResponse(BaseModel):
    data: Any = None
    error: ApiError | None = None

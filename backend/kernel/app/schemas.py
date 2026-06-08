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


# -- Project request/data schemas --


class CreateProjectRequest(BaseModel):
    name: str
    cwd: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    metadata: dict[str, Any] | None = None


class ValidateCwdRequest(BaseModel):
    cwd: str


class ValidateCwdData(BaseModel):
    exists: bool
    is_dir: bool
    writable: bool
    has_dot_claude: bool
    absolute_path: str | None = None
    error: str | None = None


class ValidateCwdResponse(BaseModel):
    data: ValidateCwdData
    error: ApiError | None = None


class ProjectData(BaseModel):
    id: str
    name: str
    cwd: str
    status: str = "active"
    created_at: int | None = None  # Unix epoch milliseconds (UTC)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectResponse(BaseModel):
    data: ProjectData
    error: ApiError | None = None


class ProjectListResponse(BaseModel):
    data: list[ProjectData]
    error: ApiError | None = None


# -- Agent --


class CreateAgentRequest(BaseModel):
    """Create a new global agent. Only ``name`` is required — every other
    field carries a sensible default so users can ship a minimal agent and
    flesh it out later."""

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


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    model: str | None = None
    runtime_provider: RuntimeProvider | None = None
    instructions: str | None = None
    permission_mode: Literal["default", "auto_review", "full_access"] | None = None
    max_turns: int | None = None
    max_cost_usd: float | None = None
    tools: list[ToolDefSchema] | None = None
    callable_agents: list[SubAgentDefSchema] | None = None
    skills: list[str] | None = None
    mcp_servers: list[McpServerConfigSchema] | None = None
    effort: EffortLiteral | None = None
    thinking: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class AgentData(BaseModel):
    id: str
    name: str
    model: str
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
    status: str = "active"
    created_at: int | None = None  # Unix epoch milliseconds (UTC)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    data: AgentData
    error: ApiError | None = None


class AgentListResponse(BaseModel):
    data: list[AgentData]
    error: ApiError | None = None


# -- Session --


RuntimeProvider = Literal["claude_agent", "codex", "deepagents"]


class CreateSessionRequest(BaseModel):
    project_id: str
    agent_id: str
    runtime_provider: RuntimeProvider
    model: str = ""
    model_provider: ModelProviderInputSchema | None = None
    model_settings: ModelSettingsSchema | None = None
    instructions: str = ""
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerConfigSchema] = Field(default_factory=list)
    permission_mode: Literal["default", "auto_review", "full_access"] | None = None
    cwd: str = ""
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
    project_id: str
    agent_id: str
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


class EventListResponse(BaseModel):
    data: list[EventData]
    error: ApiError | None = None


# -- Generic envelope --


class DataResponse(BaseModel):
    data: Any = None
    error: ApiError | None = None

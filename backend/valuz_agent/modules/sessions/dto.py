"""Session view DTOs — the response shapes the API + callers consume.

Kept identical to the pre-split signatures so callers need no behavioral
changes; only the import path moves (``sessions.service`` → ``sessions.dto``).
These are plain dataclasses (no kernel / DB coupling), so they sit at the
bottom of the sessions module dependency graph.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SessionListItem:
    id: str
    project_id: str
    name: str | None
    status: str
    origin: str
    last_user_message_text: str | None
    locked_model_id: str | None
    # Provider id stamped at session creation. Surfaced here (not just on
    # SessionDetail) because the desktop sidebar's session list feeds the
    # composer's model selector — without provider id, the selector can't
    # match the locked model and falls back to the project default.
    # (Original symptom: composer showed claude-sonnet-4-6 even after the
    # user picked deepseek-v4-pro at create time.)
    locked_provider_id: str | None
    updated_at: int  # Unix epoch ms (UTC) — kernel session timestamp
    # Kernel runtime stamped at session creation. ``claude_agent`` |
    # ``codex`` | ``deepagents``. Read-only — derived from the provider's
    # ``provider_kind``.
    # Surfaced here so the UI can render a small runtime tag in the
    # session list without a second fetch.
    runtime_provider: str = "deepagents"
    # Approval contract v1 (V5+1aae940). Surfaced on the list so the UI
    # can render a "needs review" / "auto" badge without a second fetch.
    # See ``SessionDetail`` for the full semantics of each value.
    permission_mode: str = "full_access"
    # Reasoning-effort budget (kernel V5+bba3014 ``ModelSettings.effort``).
    # ``None`` = SDK default; otherwise one of ``low|medium|high|xhigh|max``.
    # Surfaced on the list so the composer can render the EffortSelector's
    # current value without a second fetch.
    effort: str | None = None
    # Owning task id if this session belongs to a task (lead session or a
    # dispatched sub-Run). Read from ``session.metadata["valuz"]["task_id"]``.
    # ``None`` = a user-initiated standalone conversation. Surfaces here so
    # the desktop sidebar's "recent sessions" list can hide task-internal
    # sessions (they're an implementation detail of the task run, not
    # something the user opened directly).
    task_id: str | None = None


@dataclass
class TodoItem:
    """One row in the agent's TODO list snapshot.

    Mirrors the kernel's ``Session.todos`` element shape, which itself
    matches the Claude Agent SDK's ``TodoWrite`` payload verbatim.
    """

    content: str
    status: str
    activeForm: str | None = None  # noqa: N815 — preserve SDK casing on the wire


@dataclass
class SessionDetail(SessionListItem):
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    created_at: int | None = None  # Unix epoch ms (UTC) — kernel session timestamp
    trigger_meta: dict[str, str] | None = None
    # Latest TODO snapshot from the kernel session (populated as the agent
    # calls TodoWrite). ``None`` means "no todos yet"; an empty list means
    # "all done" (preserved by the kernel's change-only semantics).
    todos: list[TodoItem] | None = None
    # Frozen system-prompt append captured at session creation from the
    # project's then-current ``instructions_md`` (see ADR-008). The
    # runtime hands this verbatim to the model on every turn; project
    # edits after creation do *not* mutate it. ``None`` when the project
    # had no instructions at create time. Frontend session panels should
    # render this — NOT the live project ``instructions_md`` — so users
    # see the prompt the running agent actually has.
    instructions: str | None = None
    # Project-local agent handle for this session, when it was created from
    # a project agent (e.g. a Project Task lead/member — stored in
    # ``metadata["valuz"]["agent_slug"]`` by ``build_member_session``). ``None``
    # for plain chat sessions that aren't bound to a named project agent.
    agent_slug: str | None = None


@dataclass
class SessionEventEnvelope:
    seq: int
    event: dict[str, object]
    # Unix epoch ms (UTC) the kernel persisted the event with. ``None``
    # for the rare unsourced envelope (synthetic / fallback paths). The
    # SSE adapter already emits this on the wire (``to_sse_data``); the
    # listEvents path now mirrors it so history replay can render
    # per-message clocks too. Frontend formats via ``new Date(ms)``.
    timestamp: int | None = None


@dataclass
class SessionRunResponse:
    session: SessionDetail
    events: list[SessionEventEnvelope]

"""Session view DTOs — the response shapes the API + callers consume.

Kept identical to the pre-split signatures so callers need no behavioral
changes; only the import path moves (``sessions.service`` → ``sessions.dto``).
These are plain dataclasses (no kernel / DB coupling), so they sit at the
bottom of the sessions module dependency graph.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorktreeRef:
    """Immutable pointer to the worktree a session was created in.

    Read from the ``metadata["valuz"]["worktree"]`` snapshot stamped at
    session creation — it reflects creation time, not whether the worktree
    still exists (git is the source of truth for liveness). ``None`` on the
    session means "ran in the main workspace".
    """

    name: str
    branch: str | None
    path: str
    # Liveness, computed on read (detail fetch only; ``None`` on list items).
    # False = the worktree was removed since the session was created — the
    # next send self-heals by recreating it at the same path.
    exists: bool | None = None


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
    # A ``run_in_background`` task is still executing in this session. Same
    # fact and same source as ``RunSummary.background`` (both read
    # ``kernel_client.bg_busy_session_ids()``), so the conversation header,
    # the sidebar pulse and the Activity page can never disagree about one
    # session.
    #
    # Deliberately NOT folded into ``status``: the launching turn genuinely
    # ends, and ``status`` drives the Stop button and queue routing — a
    # ``running`` lie there offers a Stop that stops nothing and routes new
    # messages into the queue (409 "Session is already running").
    background: bool = False
    # Owning task id if this session belongs to a task (lead session or a
    # dispatched sub-Run). Read from ``session.metadata["valuz"]["task_id"]``.
    # ``None`` = a user-initiated standalone conversation. Surfaces here so
    # the desktop sidebar's "recent sessions" list can hide task-internal
    # sessions (they're an implementation detail of the task run, not
    # something the user opened directly).
    task_id: str | None = None
    # Worktree the session runs in (creation-time snapshot). Surfaced on the
    # list so the sidebar can render a worktree badge without a second fetch.
    worktree: WorktreeRef | None = None
    # Fork provenance (docs/design/session-fork.md): the source session this
    # one was forked from — kernel-stamped ``metadata["forked_from"]``, the
    # only reliable lineage source (runtime-native lineage is not queryable).
    # ``None`` for sessions that are not forks. The source may since have
    # been deleted; the id is a navigation hint, not a guarantee.
    forked_from_session_id: str | None = None


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
    # Store-independent identity (the append ``request_id``) — the SAME uid the
    # SSE frames carry, so the frontend can dedup/merge REST history rows
    # against live/backfill frames across the two seq spaces. ``None`` for
    # legacy rows persisted before uid minting and synthetic envelopes.
    event_uid: str | None = None


@dataclass
class SessionRunResponse:
    session: SessionDetail
    events: list[SessionEventEnvelope]


@dataclass
class QueuedInput:
    """One queued follow-up input awaiting FIFO drain (or ``blocked``)."""

    id: str
    status: str  # queued | blocked
    position: int
    text: str
    attachment_count: int
    provider_id: str | None
    model_id: str | None
    error_message: str | None
    created_at: int
    updated_at: int | None


@dataclass
class QueuedInputList:
    session_id: str
    items: list[QueuedInput]
    # True when an interrupt soft-paused auto-drain and the queue awaits resume.
    paused: bool
    # True while a host drain chain is in flight for this session. A dispatched
    # (in-flight) item drops out of ``items`` (only queued/blocked are listed),
    # so clients that re-subscribe per drained turn (desktop) need this to keep
    # following until the LAST item finishes — not just while ``items`` is
    # non-empty. See docs/design/session-input-queue.md §14.5.
    draining: bool = False
    # The item the drain is executing RIGHT NOW (status ``dispatched``): out of
    # ``items`` but possibly not yet visible in the transcript. Clients keep
    # its bubble rendered until the user message lands instead of dropping it
    # one refetch too early.
    dispatching: QueuedInput | None = None

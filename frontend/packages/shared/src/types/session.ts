import type { WorktreeRef } from "./worktree";

/**
 * Approval mode for a session (kernel V5+1aae940; live-reconcile since
 * V5+bba3014). ``default`` parks every write / shell / MCP call on the
 * host for explicit user approval. ``auto_review`` lets an LLM
 * classifier auto-decide and surfaces a review feed (Claude tier only —
 * the API rejects this for DeepAgents). ``full_access`` auto-approves
 * everything (current default, matches the legacy "bypass" role).
 *
 * Mutable mid-session via ``PATCH /v1/sessions/{id}/permission-mode``;
 * applies on the next Send.
 */
export type SessionPermissionMode = "default" | "auto_review" | "full_access";

/**
 * Cross-runtime reasoning-effort budget (kernel V5+bba3014
 * ``ModelSettings.effort``). Mutable mid-session via
 * ``PATCH /v1/sessions/{id}/effort``; applies on next Send.
 *
 * Per-runtime value mapping:
 *   * Claude: pass-through all 5 values.
 *   * Codex: ``max`` clamps to ``xhigh``.
 *   * DeepAgents openai-completion: ``max`` → ``xhigh``.
 *   * DeepAgents anthropic: pass-through.
 *   * DeepAgents gemini: ``xhigh|max`` → ``high``.
 */
export type EffortLevel = "low" | "medium" | "high" | "xhigh" | "max";

/**
 * Session working mode (kernel ``Session.mode``,
 * docs/design/session-modes.md). ``plan`` = the runtime plans before
 * touching anything (Claude lowers to SDK ``permissionMode="plan"`` +
 * the ``ExitPlanMode`` approval card); ``goal`` = the runtime loops
 * until a goal condition is met (task lead/member sessions). Only
 * ``claude_agent`` / ``codex`` sessions accept non-default modes.
 *
 * Mutable mid-session via ``PATCH /v1/sessions/{id}/mode``. The runtime
 * can also exit a mode on its own (approved plan / completed goal) —
 * surfaced live as a ``session.mode_changed`` SSE frame
 * (``{mode, by: "user" | "runtime"}``).
 */
export type SessionMode = "default" | "plan" | "goal";

/**
 * Runtimes whose plan-mode lowering is wired end-to-end today.
 * ``claude_agent`` lowers natively (typed SDK
 * ``set_permission_mode("plan")`` + the ``ExitPlanMode`` approval
 * card); ``codex`` via ``turn/start.collaborationMode`` (plan proposal
 * arrives as ``session.plan_proposed`` and approval is client-driven).
 * deepagents / deepseek_harness have no native primitive and the
 * server 400s them. UI affordances gate on this list so users never
 * see a toggle the backend would reject.
 */
export const PLAN_MODE_RUNTIMES: readonly string[] = ["claude_agent", "codex"];

/** Whether the composer should offer the plan-mode toggle for a runtime. */
export function supportsPlanMode(
  runtimeProvider: string | null | undefined,
): boolean {
  return runtimeProvider != null && PLAN_MODE_RUNTIMES.includes(runtimeProvider);
}

export interface SessionListItem {
  id: string;
  project_id: string;
  name: string | null;
  status: "created" | "running" | "idle" | "failed" | "cancelled" | "archived";
  origin: "user" | "schedule" | "automation";
  /** CLIENT-side tag on multi-target editions: which execution target
   * answered the list row (e.g. "local"/"cloud"). Distinct from ``origin``
   * (the server-side initiator). Never sent by the server. */
  exec_origin?: string;
  last_user_message_text: string | null;
  locked_model_id: string | null;
  /**
   * Provider id stamped at session creation. Promoted from SessionDetail to
   * the list shape because the desktop sidebar's session list feeds the
   * composer's model selector — the selector matches on (providerId, modelId)
   * pairs, so without provider_id the locked model can't be displayed and
   * falls back to the project default provider.
   */
  locked_provider_id: string | null;
  runtime_provider: string;
  /**
   * Approval mode for this session. Live-reconcilable (kernel
   * V5+bba3014). Surfaced on the list shape so the UI can render an
   * "auto" / "needs review" badge without a second detail fetch.
   */
  permission_mode: SessionPermissionMode;
  /**
   * Reasoning-effort budget for this session (kernel V5+bba3014
   * ``ModelSettings.effort``). ``null`` = SDK default. Live-
   * reconcilable. Surfaced on the list shape so the composer can
   * render the EffortSelector's current value without a second fetch.
   */
  effort: EffortLevel | null;
  /**
   * Session working mode. Surfaced on the list shape so the composer's
   * Plan chip reflects the session's current mode without a second
   * fetch. Optional because older backends omit it — treat a missing
   * value as ``"default"``.
   */
  mode?: SessionMode;
  /**
   * Owning task id if this session belongs to a task run (lead session
   * or dispatched sub-Run). ``null`` = a user-initiated standalone
   * conversation. Read from ``session.metadata.valuz.task_id``. The
   * desktop sidebar's "recent" list filters on this so task-internal
   * sessions don't leak into the user's chat history.
   */
  task_id: string | null;
  /**
   * Worktree the session runs in (immutable creation-time snapshot).
   * `null` = the project's main workspace. On the *detail* shape the
   * backend also populates `worktree.exists` (liveness, computed on read).
   */
  worktree?: WorktreeRef | null;
  /**
   * Fork provenance (docs/design/session-fork.md): the source session this
   * one was forked from. `null` for sessions that are not forks. The source
   * may since have been deleted — a navigation hint, not a guarantee.
   */
  forked_from_session_id?: string | null;
  /** Unix epoch milliseconds (UTC). Format via `new Date(ms)`. */
  updated_at: number;
  /**
   * Aggregated token usage when the row originated from a session-detail
   * response. List endpoints may omit it to avoid an extra message scan.
   */
  total_tokens?: number;
  /**
   * A `run_in_background` task is still executing in this session. Same fact
   * and same source as `RunSummary.background` — both read the orchestrator's
   * live registry via `bg_busy_session_ids()` — so the conversation header,
   * the sidebar pulse and the Activity page can never disagree about one
   * session.
   *
   * Deliberately separate from `status`: the launching turn genuinely ends,
   * and `status` drives the Stop button and queue routing, so a `running`
   * value here would offer a Stop that stops nothing and route new messages
   * into the queue (409 "Session is already running").
   */
  background?: boolean;
}

/**
 * One row in the agent's TODO list snapshot. Mirrors the kernel's
 * `Session.todos` element shape, which itself matches the Claude Agent
 * SDK's TodoWrite payload verbatim — the camelCase `activeForm` is
 * preserved on the wire to avoid (de)serialise overhead and to keep the
 * shape identical to upstream.
 */
export interface TodoItem {
  content: string;
  /**
   * "pending" | "in_progress" | "completed". Typed as string for
   * forward-compat with future SDK additions.
   */
  status: string;
  /**
   * Gerund form ("Planning migration") rendered while in_progress.
   * Some runtimes (DeepAgents) omit it — fall back to `content`.
   */
  activeForm?: string | null;
}

export interface SessionDetail extends SessionListItem {
  total_tokens: number;
  total_cost_usd: number;
  /** Unix epoch milliseconds (UTC). Format via `new Date(ms)`. */
  created_at: number | null;
  /**
   * Free-form metadata captured at session creation. Currently used to
   * remember UI mode hints (e.g. {"mode": "skill-creator"}) so revisiting
   * the session from history restores the right panel/banner.
   */
  trigger_meta?: Record<string, string> | null;
  /**
   * Latest TODO snapshot from the kernel session, populated as the agent
   * calls TodoWrite. `null` means "no todos yet"; an empty array is a
   * meaningful "all done" signal preserved by the kernel.
   */
  todos?: TodoItem[] | null;
  /**
   * Frozen system-prompt append captured at session creation from the
   * project's then-current `instructions_md` (ADR-008). The runtime
   * hands this verbatim to the model on every turn; project edits
   * after creation do *not* mutate this field. `null` when the project
   * had no instructions at session-create time. Session panels should
   * render this — NOT the live project `instructions_md` — so users
   * see the prompt the running agent actually has.
   */
  instructions?: string | null;
  /**
   * Project-local agent handle when the session was created from a named
   * project agent (e.g. a Project Task lead/member). `null` for plain chat
   * sessions not bound to a named agent.
   */
  agent_slug?: string | null;
}

// ---------------------------------------------------------------------------
// Session event types (wire-level shapes shared by core + ui + apps).
// Moved from core/api/sessions-api.ts — pure data, no runtime deps.
// ---------------------------------------------------------------------------

export interface SessionEventDTO {
  /**
   * PER-STORE row id. The backend has TWO independent seq spaces: history
   * reads (REST list/window, SSE backfill frames, heartbeats) carry the
   * DURABLE store's seq, while live-stream frames carry the kernel's LOCAL
   * seq. Seqs from the two spaces must NEVER be compared against each other
   * or fed into one cursor — cross-store identity is ``event_uid``.
   * ``0`` marks a live unpersisted frame (streaming deltas).
   */
  seq: number;
  event: {
    event_type: string;
    payload: Record<string, string>;
  };
  /**
   * Unix epoch milliseconds (UTC) the kernel persisted the event with.
   * The SSE wire emits it (``SessionEventFrame.to_sse_data``); listEvents
   * mirrors it through ``SessionEventEnvelope.timestamp``. ``undefined``
   * for synthetic envelopes that don't have one. Format via `new Date(ms)`.
   */
  timestamp?: number;
  /**
   * Store-independent identity of a persisted event (32-hex string), present
   * on both history and live frames. This is the ONLY key valid for
   * cross-segment (history ↔ live) dedup/merge — ``seq`` is per-store.
   * ``null``/``undefined`` on live-only delta frames (never persisted) and
   * on legacy rows persisted before uid minting; those keep the historical
   * seq-based behavior.
   */
  event_uid?: string | null;
}

/**
 * Subjects the runtime can park on. ``shell_command`` / ``file_change``
 * / ``mcp_tool_call`` / ``tool_input`` all use the
 * ``approve | reject`` decision set; ``clarifying_questions`` uses the
 * ``answer | reject`` set (Claude SDK ``AskUserQuestion``). The host
 * preserves whatever string the kernel emits so future subjects flow
 * through transparently.
 */
export type RequiresActionSubject =
  | "shell_command"
  | "file_change"
  | "mcp_tool_call"
  | "tool_input"
  | "clarifying_questions"
  | (string & {});

/**
 * Preview of the session-scoped rule the kernel will commit if the
 * user picks "Always for this session" (kernel V5+d008b53). Each
 * runtime ships its own matcher so the ``display`` string reflects
 * the *native* rule grammar of the runtime (Claude pattern like
 * ``Bash(npm test:*)``, codex's MCP endpoint-level identity, exact
 * args for the default matcher). ``rule_data`` is the opaque payload
 * the matcher needs to match a future call — the frontend never
 * inspects it, only round-trips it through the rule preview UI.
 *
 * ``null`` for the ``clarifying_questions`` subject and for any
 * runtime that doesn't advertise ``approve_for_session`` in
 * ``available_decisions``.
 */
export interface SessionRulePreview {
  kind: string;
  display: string;
  runtime_kind: string;
  rule_data: Record<string, unknown>;
}

export interface RequiresActionEvent {
  message_id: string;
  pending_id: string;
  subject: RequiresActionSubject;
  runtime_provider: string;
  /**
   * Decisions the runtime advertised for this pending. v1 always
   * contains ``approve`` + ``reject``; v2 may add
   * ``approve_with_changes`` (Claude / DeepAgents) and
   * ``approve_for_session`` (every runtime that ships a rule matcher).
   * The clarifying_questions subject uses ``answer`` + ``reject``.
   */
  available_decisions: string[];
  /** Decoded subject-specific payload. Empty object on parse failure. */
  payload: Record<string, unknown>;
  /** ISO-8601 expiry; "" when the kernel didn't supply one. */
  expires_at: string;
  /**
   * V5+d008b53: structured preview of the session-scoped rule the
   * kernel will commit if the user picks "Always for this session".
   * ``null`` when the runtime doesn't advertise the verb (which is
   * also why ``approve_for_session`` won't appear in
   * ``available_decisions`` for that pending).
   */
  session_rule_preview: SessionRulePreview | null;
  /**
   * V5+d008b53: the original tool args the runtime parked on. The
   * frontend "Edit & Approve" JSON editor seeds from this when the
   * user opens the modal so they edit the *complete* args dict
   * rather than typing one from scratch. ``null`` for the
   * clarifying_questions subject (where args aren't tool args).
   */
  original_input: Record<string, unknown> | null;
}

export interface ActionResolvedEvent {
  message_id: string;
  pending_id: string;
  decision:
    | "approve"
    | "approve_with_changes"
    | "approve_for_session"
    | "auto_approved"
    | "reject"
    | "answer"
    | "expired"
    | "interrupted";
  resolved_by: "user" | "system" | (string & {});
  message: string;
  /** Decoded ``answers`` map, only populated when ``decision === "answer"``. */
  answers: Record<string, string | string[]>;
  /**
   * V5+d008b53: UUID of the committed session-scoped rule.
   * Non-null only when ``decision === "approve_for_session"``;
   * idempotent retries surface the original rule id.
   */
  rule_id: string | null;
  /**
   * V5+d008b53: rule that fired on this auto-approve.
   * Non-null only when ``decision === "auto_approved"``. The
   * frontend uses this to render the "auto-approved by rule X"
   * strip even though there was no preceding ``requires_action``.
   */
  auto_resolved_by_rule_id: string | null;
}

/**
 * One agent's slot in a Claude dynamic-workflow run. The kernel folds the
 * run's ``journal.jsonl`` into one entry per agent: ``progress`` while the
 * agent is running, ``done`` once its result lands. Extra fields (label /
 * phase) may appear in the terminal ``wf_<id>.json`` snapshot, which the
 * kernel forwards raw — so this type stays tolerant of unknown shapes.
 */
export interface WorkflowAgentProgress {
  /** Always ``"workflow_agent"`` for the live snapshot; tolerated as a free
   *  string so a richer terminal snapshot type flows through unchanged. */
  type?: string;
  agentId: string;
  state: "progress" | "done" | (string & {});
  /** Optional human label from the terminal snapshot (e.g. the agent's task). */
  label?: string;
  /** Optional phase grouping from the terminal snapshot. */
  phase?: string;
}

/**
 * A snapshot of a Claude dynamic-workflow (``Workflow`` tool) run, streamed
 * live by the kernel while the background runtime executes. The live snapshot
 * carries the fields below; the terminal ``wf_<id>.json`` result file is
 * forwarded raw and may carry extras (consumed defensively by the renderer).
 */
export interface WorkflowState {
  runId: string;
  workflowName: string | null;
  /** ``running`` during the run; ``completed`` / a terminal verb at the end. */
  status: "running" | "completed" | (string & {});
  agentCount: number;
  agentsDone: number;
  workflowProgress: WorkflowAgentProgress[];
  /** Path to the run's script on disk (first snapshot only). */
  scriptPath?: string;
  /** The workflow script content (first snapshot only); ``null`` if unread. */
  script?: string | null;
  /**
   * Path to the run's full result file (``wf_<id>.json``) — present on the
   * terminal snapshot so the UI can link to the complete output.
   */
  statePath?: string;
  /**
   * The run's returned ``result.question`` — the actual research question the
   * workflow answered (NOT the workflow's meta description). Terminal snapshot
   * only; ``null`` for runs that ended without a result (killed/aborted).
   */
  resultQuestion?: string | null;
  /** The run's returned ``result.summary`` — its answer. Terminal only. */
  resultSummary?: string | null;
}

/**
 * Decoded ``session.workflow_progress`` SSE frame. ``id`` is the ``Workflow``
 * launch tool_use_id — the frontend keys progress by it so the card attaches
 * to the matching tool call. The host JSON-stringifies ``state`` on the wire;
 * ``parseWorkflowProgress`` re-parses it.
 */
export interface WorkflowProgressEvent {
  id: string;
  run_id: string;
  state: WorkflowState;
  message_id: string;
}

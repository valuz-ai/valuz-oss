import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from "react";
import {
  useLocation,
  useParams,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowDown,
  ArrowLeft,
  Bot,
  GitBranch,
  ChevronDown,
  ChevronRight,
  FilePenLine,
  Settings,
  Sparkles,
  Trash2,
} from "lucide-react";
import {
  ApiError,
  sessionsApi,
  queueApi,
  type QueuedInput,
  type QueuedInputList,
  agentsApi,
  automationsApi,
  connectorsApi,
  getDefaultExecutionTarget,
  getEntityOrigin,
  recordEntityOrigin,
  refreshRunningRuns,
  resolveApiBase,
  useEntityOrigin,
  useExecutionTargets,
  useSessionStore,
  useProjectStore,
  projectsApi,
  skillsApi,
  usePanelStore,
  type SessionDetail,
  type SessionEventDTO,
  type SessionListItem,
  type TodoItem,
  type ProjectDetail,
  type ProjectListItem,
  type SkillView,
  type StagingSlugView,
  type StagingSyncStrategy,
  type ProjectFileNode,
  parseTodosUpdate,
  parseRequiresAction,
  parseActionResolved,
  parseWorkflowProgress,
  SESSION_REQUIRES_ACTION_EVENT,
  SESSION_ACTION_RESOLVED_EVENT,
  SESSION_WORKFLOW_PROGRESS_EVENT,
  type WorkflowState,
  useCapabilities,
  useComposerProviderChannelState,
  useComposerAgentLibrary,
  useComposerProviders,
  useModelDefaults,
  useRuntimes,
  useSessionArtifacts,
  useSessionAttachments,
  type RuntimeId,
  type MemberWithAgent,
} from "@valuz/core";
import {
  ApprovalCard,
  ApprovalResolvedStrip,
  AutoApprovedStrip,
  AskUserQuestionCard,
  ArtifactViewerShell,
  AutomationToolCard,
  Badge,
  Button,
  DeleteConfirmDialog,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  EmptyState,
  GenerativeUICard,
  UserAnswerSummaryCard,
  WorkflowProgressCard,
  cn,
  Composer,
  KnowledgeFileTreePicker,
  ProjectDetailContextPanel,
  SkillStagingPanel,
  SkillSubmissionCard,
  AgentProposalCard,
  AutomationProposalCard,
  parseAskUserQuestionInput,
  parseAutomationToolOutput,
  type ApprovalCardSubject,
  type ApprovalResolvedDecision,
  type ArtifactOpenTarget,
  type FileTreeNode,
  type SkillSubmissionState,
  type UploadedFileItem,
  type ComposerAgentItem,
  type ComposerConnector,
} from "@valuz/ui";
import { modelLabel } from "@valuz/shared";
import { t as _t } from "@valuz/shared/i18n";
import type { I18nKey } from "@valuz/shared";
import { useProjectOutlet } from "@valuz/app/layout";
import {
  mergeEventWindow,
  deriveBackgroundTasks,
  runningBackgroundTasks,
  useIncrementalTurns,
  type PlanSubtask,
} from "@valuz/core";
import { BackgroundTaskStrip, ConversationTurnList } from "@valuz/ui";
import { usePlatform } from "@valuz/app/platform";
import {
  useHasUsableChannel,
  useTranslation,
  markSessionNotificationsRead,
} from "@valuz/core";
import {
  useConversationLocalFileLinks,
  useProjectKbBindings,
  useKbDocTree,
} from "@valuz/app/hooks";
import {
  computePlanAnchors,
  extractToolOutputJson,
} from "./conversation-plan-anchors";
import {
  deriveTurnActive,
  shouldApplySessionStatus,
  shouldRefreshConversationHistory,
  shouldShowNoModelEmptyState,
} from "./conversation-loading";
import { createConversationBootstrapGuard } from "./conversation-bootstrap";
import { LiveTaskCard } from "../components/LiveTaskCard";
import { QueuedInputsBar } from "../components/QueuedInputsBar";
import { AttachmentParsingDialog } from "../components/AttachmentParsingDialog";
import { CreateAgentDialog } from "../components/CreateAgentDialog";
import { OriginBadge } from "../components/ExecutionLocationPicker";
import { ExecutionLocationBar } from "../components/ExecutionLocationBar";
import {
  resolveAgentSkillItems,
  type AgentSkillItem,
} from "../lib/agent-skill-items";
import { getLastTempAgent, setLastTempAgent } from "../lib/last-temp-agent";
import { useArtifactFile } from "../hooks/use-artifact-file";

/** True while a workflow snapshot's status denotes an in-flight run (vs a
 *  terminal ``completed`` / ``killed`` / ``failed`` verb). Used to decide
 *  whether the turn-end safety net should coerce a card to ``completed``. */
const isWorkflowRunning = (status: string): boolean =>
  status === "running" ||
  status === "active" ||
  status === "queued" ||
  status === "pending";

function toFileTree(nodes: ProjectFileNode[], prefix = ""): FileTreeNode[] {
  return nodes.map((n) => {
    const path = prefix ? `${prefix}/${n.name}` : n.name;
    const result: FileTreeNode = {
      name: n.name,
      type: n.type === "directory" ? "folder" : "file",
      path,
    };
    if (n.children) result.children = toFileTree(n.children, path);
    return result;
  });
}

function resolveConversationArtifactPath(
  path: string,
  rootPath: string,
): string {
  if (!path) return path;
  if (path.startsWith("/") || /^[a-zA-Z]:[\\/]/.test(path)) return path;
  if (!rootPath) return path;
  const sep = rootPath.includes("\\") ? "\\" : "/";
  const trimmed = rootPath.endsWith(sep) ? rootPath.slice(0, -1) : rootPath;
  return `${trimmed}${sep}${path}`;
}

function toConversationRelativeArtifactPath(
  path: string,
  rootPath: string,
): string | null {
  if (!path) return null;
  const normalizedPath = path.replace(/\\/g, "/");
  if (!normalizedPath.startsWith("/") && !/^[a-zA-Z]:\//.test(normalizedPath)) {
    return normalizedPath.replace(/^\/+/, "");
  }
  if (!rootPath) return null;
  const normalizedRoot = rootPath.replace(/\\/g, "/").replace(/\/+$/, "");
  if (normalizedPath === normalizedRoot) return null;
  if (!normalizedPath.startsWith(`${normalizedRoot}/`)) return null;
  return normalizedPath.slice(normalizedRoot.length + 1);
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── VALUZ-CHATPLAN S3 helpers ────────────────────────────────────────────

/** Compact one-line status pill for chatplan tool results. Each pill is a
 *  pure timeline anchor — the canonical "current state" view lives in the
 *  ``LiveTaskCard`` mounted at the task's first reference, which mutates
 *  in place via SSE. Handles draft_task / plan_task / modify_plan /
 *  commit_task / abandon_task / inject_into_task. */
function renderChatplanStatusPill(
  name: string,
  tool: { input?: string; output?: string },
  t: (
    key: I18nKey,
    fallback?: string | Record<string, string | number>,
    params?: Record<string, string | number>,
  ) => string,
  navigate: (path: string) => void,
): ReactElement | null {
  const matches = (k: string) => name === k || name.endsWith(`__${k}`);
  const isDraft = matches("draft_task");
  const isPlan = matches("plan_task");
  const isModify = matches("modify_plan");
  const isCommit = matches("commit_task");
  const isAbandon = matches("abandon_task");
  const isInject = matches("inject_into_task");
  if (!isDraft && !isPlan && !isModify && !isCommit && !isAbandon && !isInject)
    return null;
  if (!tool.output) return null;

  const output = extractToolOutputJson(tool.output) as {
    task_id?: string;
    title?: string;
    status?: string;
    delivered?: boolean;
    reason?: string;
    current_version?: number;
    subtasks?: PlanSubtask[];
    error?: string;
  } | null;
  if (!output) return null;
  if (output.error) return null;
  // ``plan_task`` / ``modify_plan`` responses don't echo task_id (they
  // return only ``{subtasks, ready, current_version}``); the id lives in
  // the tool input. Fall through to a tool-input parse when missing.
  let taskId = output.task_id;
  if (!taskId && tool.input) {
    const inputJson = extractToolOutputJson(tool.input) as {
      task_id?: string;
    } | null;
    taskId = inputJson?.task_id;
  }
  if (!taskId) return null;

  // Per-type accent: a colored dot + matching ring on hover. Keeps the
  // timeline scannable at a glance — green for go, rose for stop, etc.
  let icon = "";
  let label = "";
  let accent: "indigo" | "emerald" | "rose" | "amber" | "slate" = "slate";
  if (isDraft) {
    icon = "📝";
    label = t("conversation.pillDrafted" as I18nKey);
    accent = "indigo";
  } else if (isPlan) {
    icon = "📋";
    label = t("conversation.pillPlanned" as I18nKey, undefined, {
      version: output.current_version ?? 0,
      count: Array.isArray(output.subtasks) ? output.subtasks.length : 0,
    });
    accent = "indigo";
  } else if (isModify) {
    icon = "✏";
    label = t("conversation.pillModified" as I18nKey, undefined, {
      version: output.current_version ?? 0,
      count: Array.isArray(output.subtasks) ? output.subtasks.length : 0,
    });
    accent = "indigo";
  } else if (isCommit) {
    icon = "▶";
    label = t("conversation.pillCommitted" as I18nKey);
    accent = "emerald";
  } else if (isAbandon) {
    icon = "✕";
    label = t("conversation.pillAbandoned" as I18nKey);
    accent = "rose";
  } else if (isInject) {
    if (output.delivered) {
      icon = "💬";
      label = t("conversation.pillInjected" as I18nKey);
      accent = "amber";
    } else {
      icon = "⚠";
      label = t("conversation.pillInjectFailed" as I18nKey, undefined, {
        reason: output.reason ?? "unknown",
      });
      accent = "rose";
    }
  }

  const accentDot: Record<typeof accent, string> = {
    indigo: "bg-brand",
    emerald: "bg-success",
    rose: "bg-rose-500",
    amber: "bg-warning",
    slate: "bg-ink-muted",
  };

  return (
    <div className="group flex items-center gap-3 rounded-lg border border-surface-border bg-surface px-3.5 py-2 text-sm shadow-sm transition-colors hover:border-surface-border-strong hover:bg-surface-soft">
      <span
        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-base ${accentDot[accent]}/10`}
        aria-hidden
      >
        <span className="leading-none">{icon}</span>
      </span>
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate font-medium text-ink-heading">{label}</span>
        {output.title && (
          <span className="truncate text-xs text-ink-muted">
            {output.title}
          </span>
        )}
      </div>
      <button
        type="button"
        className="shrink-0 rounded-md border border-surface-border bg-surface px-2.5 py-1 text-xs text-ink-body transition-colors hover:border-brand/40 hover:bg-brand/5 hover:text-ink-heading"
        onClick={() => navigate(`/tasks/${encodeURIComponent(taskId)}`)}
      >
        {t("conversation.openTask" as I18nKey)}
      </button>
    </div>
  );
}

function sessionDetailToListItem(detail: SessionDetail): SessionListItem {
  return {
    id: detail.id,
    project_id: detail.project_id,
    name: detail.name,
    status: detail.status,
    origin: detail.origin,
    last_user_message_text: detail.last_user_message_text,
    locked_model_id: detail.locked_model_id,
    locked_provider_id: detail.locked_provider_id ?? null,
    runtime_provider: detail.runtime_provider,
    permission_mode: detail.permission_mode,
    effort: detail.effort ?? null,
    task_id: detail.task_id ?? null,
    // Carries ``exists`` (liveness) from the detail fetch — the header
    // worktree badge greys out on it.
    worktree: detail.worktree ?? null,
    updated_at: detail.updated_at,
  };
}

function makeLocalUserInterruptEvent(): SessionEventDTO {
  return {
    seq: 0,
    event: {
      event_type: "session.idle",
      payload: { stop_reason: "user_interrupt" },
    },
    timestamp: Date.now(),
  };
}

function isLocalUserInterruptEvent(event: SessionEventDTO): boolean {
  return (
    event.seq === 0 &&
    event.event.event_type === "session.idle" &&
    event.event.payload.stop_reason === "user_interrupt"
  );
}

function appendUniqueEvents(
  current: SessionEventDTO[],
  incoming: SessionEventDTO[],
): SessionEventDTO[] {
  // Dedup keys on the store-independent ``event_uid`` when present — history
  // reads and live frames use INDEPENDENT seq spaces, so a bare seq match is
  // only trustworthy between uid-less (legacy) rows. uid-less incoming keeps
  // the historical seq-based check, but only against uid-less rows (a
  // uid-bearing row's seq may be kernel-local and collide by accident).
  const seenUids = new Set<string>();
  const seenLegacySeqs = new Set<number>();
  for (const event of current) {
    if (event.event_uid) seenUids.add(event.event_uid);
    else if (event.seq > 0) seenLegacySeqs.add(event.seq);
  }
  const fresh = incoming.filter((event) =>
    event.event_uid
      ? !seenUids.has(event.event_uid)
      : event.seq <= 0 || !seenLegacySeqs.has(event.seq),
  );
  if (fresh.length === 0) return current;
  return [...current, ...fresh];
}

/**
 * Small status pill shown next to the conversation title in the page
 * header. Mirrors the sidebar's per-row indicator: ``running`` pulses
 * (the agent is mid-turn), ``failed``/``cancelled`` show a muted state.
 * Idle / archived / undefined render nothing — no point in chrome for
 * the steady state.
 */
const SessionStatusPill = ({
  status,
  cancelled,
  pending,
}: {
  status?: string;
  cancelled?: boolean;
  /** The transcript hasn't loaded yet, so ``cancelled`` isn't known. Suppresses
   *  the failure pill in the meantime so a stopped conversation doesn't flash a
   *  red 失败 for a beat before it resolves to the grey 已停止. */
  pending?: boolean;
}) => {
  const { t } = useTranslation();
  // A user-interrupted turn can leave the PERSISTED session status on
  // ``failed`` / ``terminated``: the interrupt's ``idle`` finalize races the
  // turn's own finalize, and when the turn finalize wins it maps the cut-short
  // run to a failure. When the transcript itself says the last turn was
  // cancelled, that's an interrupt, not a failure — show the quiet 已中断 pill
  // instead of a red 失败.
  if (cancelled) {
    return (
      <span
        className="flex h-5 shrink-0 items-center gap-1 rounded-[4px] bg-surface-soft px-2 py-0 text-2xs text-ink-meta"
        title="session status: cancelled"
      >
        {/* One label for a stopped conversation everywhere: matches the
            activity feed / project lists (activity.statusStopped) and the
            "停止" button, rather than a second word (已中断) only here. */}
        {t("activity.statusStopped" as Parameters<typeof t>[0])}
      </span>
    );
  }
  // A stopped conversation persists as ``failed``/``terminated``; whether it was
  // a user stop (grey) or a real error (red) is only known once the transcript
  // loads and ``cancelled`` resolves. Until then, show no pill rather than a red
  // 失败 that flips to grey a beat later.
  if (pending && (status === "failed" || status === "terminated")) return null;
  if (!status || status === "idle" || status === "archived") return null;
  const text =
    status === "running"
      ? t("common.running" as Parameters<typeof t>[0])
      : status === "created"
        ? t("common.waiting" as Parameters<typeof t>[0])
        : status === "failed"
          ? t("common.failed" as Parameters<typeof t>[0])
          : status === "cancelled"
            ? t("conversation.interrupted" as Parameters<typeof t>[0])
            : status;
  const cls =
    status === "running"
      ? "bg-brand/10 text-brand"
      : status === "created"
        ? "bg-brand/5 text-brand/80"
        : status === "failed"
          ? "bg-error-light text-error-text"
          : "bg-surface-soft text-ink-meta";
  return (
    <span
      className={`flex h-5 shrink-0 items-center gap-1 rounded-[4px] px-2 py-0 text-2xs ${cls}`}
      title={`session status: ${status}`}
    >
      {status === "running" ? (
        <span className="h-1.5 w-1.5 rounded-full bg-brand animate-pulse" />
      ) : null}
      {text}
    </span>
  );
};

/**
 * Sentinel ``id`` URL param used by the "fresh quick-chat" entry
 * (sidebar `+t("conversation.newChat" as Parameters<typeof t>[0])`, ⌘N, home page fallback). The route is
 * ``/conversation/new``; when the user actually sends a message,
 * ``ensureSession`` mints a real session and ``navigate(replace:true)``
 * swaps the URL to ``/conversation/{real-id}``. Centralizing the
 * literal here keeps every check (``id === NEW_SESSION_ID``) in
 * lock-step with the route definition.
 */
const NEW_SESSION_ID = "new";

/**
 * True when a tool title refers to *tool* regardless of how the runtime
 * namespaces MCP tools: bare ("automation"), Claude-style
 * ("mcp__valuz_automations__automation"), or slash-style
 * ("valuz_automations/automation" — the codex runtime; verified live).
 * The old `__`-suffix-only checks silently dropped every special card
 * (automation proposal, create_task, AskUserQuestion, …) back to the
 * generic tool renderer on slash-namespacing runtimes.
 */
function isToolNamed(title: unknown, tool: string): boolean {
  if (typeof title !== "string" || !title) return false;
  return (
    title === tool || title.endsWith(`__${tool}`) || title.endsWith(`/${tool}`)
  );
}

/**
 * Parse an ``automation`` tool call's INPUT into a create spec, or null if it
 * isn't a ``create`` action. ``create`` is the only action that renders a
 * propose→confirm card (others render ``AutomationToolCard``).
 *
 * We render the card from the input (not the tool output) because the output is
 * runtime-dependent: the Valuz/DeepAgents (LangChain) runtime wraps it in a
 * content envelope that isn't bare JSON, so ``parseAutomationToolOutput``
 * returns null there. The input is always clean — same reason ``AgentProposalCard``
 * renders from input. Note ``trigger`` may arrive as a JSON *string* (the model
 * sometimes stringifies it), so we parse it back into the discriminated union.
 */
function parseAutomationCreateInput(input: unknown): {
  name: string;
  prompt_template: string;
  trigger: import("@valuz/core").Trigger | null;
  agent_slug?: string;
  action_kind?: "chat" | "task";
  worktree?: boolean;
} | null {
  if (!input) return null;
  let parsed: unknown;
  try {
    parsed = typeof input === "string" ? JSON.parse(input) : input;
  } catch {
    return null;
  }
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    (parsed as { action?: unknown }).action !== "create"
  ) {
    return null;
  }
  const p = parsed as Record<string, unknown>;
  let trigger: unknown = p.trigger ?? null;
  if (typeof trigger === "string") {
    try {
      trigger = JSON.parse(trigger);
    } catch {
      trigger = null;
    }
  }
  const actionKind =
    p.action_kind === "task"
      ? "task"
      : p.action_kind === "chat"
        ? "chat"
        : undefined;
  // On envelope-wrapping runtimes (codex, DeepAgents) the tool OUTPUT parses to
  // null, so the proposal card renders from this INPUT — it must carry the
  // worktree flag or the chip vanishes and confirm silently drops it. Accept
  // the legacy ``task_worktree`` key too so already-recorded tool calls still
  // resolve after the field rename.
  const worktree =
    typeof p.worktree === "boolean"
      ? p.worktree
      : typeof p.task_worktree === "boolean"
        ? p.task_worktree
        : undefined;
  return {
    name: typeof p.name === "string" ? p.name : "",
    prompt_template:
      typeof p.prompt_template === "string" ? p.prompt_template : "",
    trigger:
      trigger && typeof trigger === "object"
        ? (trigger as import("@valuz/core").Trigger)
        : null,
    agent_slug: typeof p.agent_slug === "string" ? p.agent_slug : undefined,
    action_kind: actionKind,
    worktree,
  };
}

/** Localized schedule summary from a trigger — a fallback for when the server's
 *  ``trigger_human_readable`` isn't available (the tool output wasn't parseable
 *  on this runtime). Mirrors the activity/automation list cadence localization
 *  (每 30 分钟 / Every 30 minutes / 手动 / Manual) via the shared
 *  ``automation.intervalEvery*`` / ``triggerManual`` keys. */
function automationTriggerSummary(
  trigger: import("@valuz/core").Trigger | null,
  t: ReturnType<typeof useTranslation>["t"],
): string | undefined {
  if (!trigger) return undefined;
  const tk = (key: string) => key as Parameters<typeof t>[0];
  if (trigger.kind === "cron") {
    return trigger.timezone
      ? `${trigger.cron_expr} · ${trigger.timezone}`
      : trigger.cron_expr;
  }
  if (trigger.kind === "interval") {
    const s = trigger.seconds;
    if (s % 3600 === 0)
      return t(tk("automation.intervalEveryHours"), { count: s / 3600 });
    if (s % 60 === 0)
      return t(tk("automation.intervalEveryMinutes"), { count: s / 60 });
    return t(tk("automation.intervalEverySeconds"), { count: s });
  }
  return t(tk("automation.triggerManual"));
}

export const ConversationPage = () => {
  const { t } = useTranslation();
  const platform = usePlatform();
  const { revealInFinder } = platform;
  const { id = NEW_SESSION_ID } = useParams<{ id: string }>();
  const location = useLocation();
  const promotingSessionIdRef = useRef<string | null>(null);
  const consumedPromoteSessionIdsRef = useRef<Set<string>>(new Set());
  const previousRouteSessionIdRef = useRef(id);
  const skipNextSessionStateResetRef = useRef(false);
  const [conversationInstanceKey, setConversationInstanceKey] = useState(
    () => `conversation:${id}`,
  );
  useEffect(() => {
    const previousRouteId = previousRouteSessionIdRef.current;
    previousRouteSessionIdRef.current = id;

    if (previousRouteId === id) return;

    const isPromote =
      previousRouteId === NEW_SESSION_ID &&
      promotingSessionIdRef.current === id;
    if (isPromote) {
      promotingSessionIdRef.current = null;
      return;
    }

    setConversationInstanceKey(`conversation:${id}`);
  }, [id]);
  // Opening a conversation clears the unread badge for any notification that
  // points at it (a question answered elsewhere, a run failure the user is now
  // looking at). Covers every entry path — direct link, notification card,
  // conversation list — since they all land here. Skips the "new" sentinel.
  useEffect(() => {
    if (id && id !== NEW_SESSION_ID) markSessionNotificationsRead(id);
  }, [id]);
  const { directoryFieldMode, setRightPanel, setHeader, setHideHeader } =
    useProjectOutlet();
  const panelCollapsed = usePanelStore((s) => s.collapsed);
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);
  const [searchParams] = useSearchParams();
  // Mode hint persisted on the session row at creation time; lets us restore
  // the Skill-Creator banner / staging panel when the user re-enters the
  // session from history (where ?mode= is absent).
  const [sessionTriggerMode, setSessionTriggerMode] = useState<string | null>(
    null,
  );
  // Project-agent handle for the open session (Project Task lead/member).
  const [sessionAgentSlug, setSessionAgentSlug] = useState<string | null>(null);
  // Project conversations pick a configured project agent instead of a raw
  // model. ``projectAgents`` is the member roster for the active project;
  // ``selectedAgentSlug`` is the user's pick for the next (new) session.
  const [projectAgents, setProjectAgents] = useState<MemberWithAgent[]>([]);
  // 10-new-conversation-guidance: the 🤖 「+ Agent」 menu item opens a create
  // dialog for temp conversations (the project path navigates to the project).
  const [createAgentOpen, setCreateAgentOpen] = useState(false);
  // Invalidate the selected target's roster after an agent is created.
  const [agentLibraryRevision, setAgentLibraryRevision] = useState(0);
  // 10-new-conversation-guidance (slice 2): is there any usable model channel?
  // Drives the setup banner's "no channel" state.
  const {
    hasChannel,
    loaded: channelLoaded,
    refresh: refreshChannels,
  } = useHasUsableChannel();
  const { managedRuntimeSetup } = useCapabilities();
  // A managed install receives its channels and its built-in assistant, so
  // "none yet" is a delivery that has not landed — not a step the user skipped.
  // Both cases offer a retry instead of a setup screen they cannot act on.
  const channelsPending = managedRuntimeSetup && channelLoaded && !hasChannel;
  const [selectedAgentSlug, setSelectedAgentSlug] = useState<string | null>(
    null,
  );
  // 「去临时对话」after creating an agent navigates here with ?agent=<slug>;
  // honour it as the pre-selected agent for the next (new) chat. The roster
  // roster reload keys off the same param so the new agent is
  // actually present, and the defaulting effect below treats it as top priority.
  const agentParam = searchParams.get("agent");
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (agentParam) setSelectedAgentSlug(agentParam);
  }, [agentParam]);
  // Deep-link origin fast path (multi-target editions): a share/notification
  // link can carry ``?origin=cloud`` so the page routes to the owning backend
  // without a probe round-trip. The edition adapter validates the value;
  // single-target builds have no adapter → no-op.
  const originParam = searchParams.get("origin");
  useEffect(() => {
    if (originParam && id && id !== NEW_SESSION_ID) {
      recordEntityOrigin(id, originParam);
    }
  }, [originParam, id]);
  // Set when this conversation was opened from a task's "view session" link
  // (TaskDetailPage). Drives the breadcrumb back-to-task affordance in the
  // header so a subtask/lead session isn't a navigational dead end.
  const fromTaskId = searchParams.get("from_task");
  const isSkillCreatorMode =
    searchParams.get("mode") === "skill-creator" ||
    sessionTriggerMode === "skill-creator";
  // Draft-first skill creation (技能库 → 添加技能 → AI 创建): the entry no
  // longer pre-creates a session (that locked an empty agent binding — a
  // dead conversation). It lands here as a normal draft carrying the
  // creation context in the URL; ``ensureSession`` mints the session via
  // the skills launcher on the first send, with the agent the user picked.
  const skillKindParam = searchParams.get("skill_kind");
  const skillProjectParam = searchParams.get("skill_project");
  const shouldRevealPanelFromNavigation =
    (
      location.state as {
        revealSessionContext?: boolean;
      } | null
    )?.revealSessionContext === true;
  const revealPanelOnSessionChangeRef = useRef(false);

  // Staging panel (Skill Creator mode) ─────────────────────────────────
  const [stagingSlugs, setStagingSlugs] = useState<StagingSlugView[]>([]);
  const [stagingRefreshing, setStagingRefreshing] = useState(false);
  const [stagingSyncing, setStagingSyncing] = useState(false);

  const refreshStaging = useCallback(async () => {
    if (!isSkillCreatorMode) return;
    // Draft-first entry: no session exists yet — nothing staged to scan.
    if (id === NEW_SESSION_ID) return;
    setStagingRefreshing(true);
    try {
      const res = await skillsApi.scanStaging(id);
      setStagingSlugs(res.slugs);
    } catch {
      // Silent — most likely the session hasn't produced staging yet.
    } finally {
      setStagingRefreshing(false);
    }
  }, [id, isSkillCreatorMode]);

  const handleSyncStaging = useCallback(
    async (
      items: {
        slug: string;
        strategy: StagingSyncStrategy;
        newSlug?: string;
      }[],
    ) => {
      setStagingSyncing(true);
      try {
        const res = await skillsApi.syncStaging(id, {
          items: items.map((i) => ({
            slug: i.slug,
            strategy: i.strategy,
            new_slug: i.newSlug,
          })),
        });
        const written = res.results.filter((r) => !r.skipped).length;
        toast.success(
          t("skill.syncCount" as Parameters<typeof t>[0], {
            count: String(written),
          }),
        );
        await refreshStaging();
      } catch (err) {
        toast.error(
          t("common.saveFailed" as Parameters<typeof t>[0], {
            error: err instanceof Error ? err.message : "unknown",
          }),
        );
      } finally {
        setStagingSyncing(false);
      }
    },
    [id, refreshStaging],
  );

  useEffect(() => {
    if (!isSkillCreatorMode) return;
    void refreshStaging();
    const t = window.setInterval(() => {
      void refreshStaging();
    }, 3000);
    return () => window.clearInterval(t);
  }, [isSkillCreatorMode, refreshStaging]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Title-area Rename + Delete state. Rename swaps the header text for
  // an inline input; Delete opens a confirm dialog. Both are no-ops until
  // a session is loaded — guarded at the click sites.
  const [titleRenaming, setTitleRenaming] = useState(false);
  const [titleRenameValue, setTitleRenameValue] = useState("");
  // Width snapshot of the title trigger captured the moment the user
  // clicks Rename. The input swaps in with this exact width so it
  // doesn't suddenly balloon to the row's max width and push the status
  // pills around.
  const [titleRenameWidth, setTitleRenameWidth] = useState<number | null>(null);
  const titleTriggerRef = useRef<HTMLButtonElement>(null);
  const [titleDeleting, setTitleDeleting] = useState(false);
  const [titleDeleteInFlight, setTitleDeleteInFlight] = useState(false);
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(
    null,
  );
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    null,
  );
  // Mirror of ``selectedSessionId`` for callbacks that must read it
  // without declaring it in their useCallback deps (which would re-
  // create the callback on every session change). Used by
  // ``refreshActiveSession`` and ``bootstrap`` to decide whether to
  // re-fetch events: when the post-turn refresh lands on the same
  // session, refetching would clobber SSE-accumulated events with
  // the server's truncated list (``list_events_after`` caps at 500
  // rows ASC, so a refetch on a long session silently drops the most
  // recent turn — which is exactly what we just streamed).
  const selectedSessionIdRef = useRef<string | null>(null);
  const bootstrapGuardRef = useRef(createConversationBootstrapGuard());
  useEffect(() => {
    selectedSessionIdRef.current = selectedSessionId;
  }, [selectedSessionId]);
  const [events, setEvents] = useState<SessionEventDTO[]>([]);
  // Every ``event_uid`` this transcript has already consumed via the SSE
  // path. Reconnect gap-fills and the server's initial drain legitimately
  // redeliver frames (the history cursor only advances from REST reads; live
  // frames carry kernel-local seqs, a different space) — replays must stay
  // render-inert AND terminal-gate-inert (a redelivered old terminal frame
  // must not re-run turn-end handling). Uid-less legacy frames bypass the
  // guard. Bounded in ``appendEvent`` (a session-lifetime page keeps this
  // set for its whole stay); cleared wherever the transcript resets.
  const seenEventUidsRef = useRef<Set<string>>(new Set());
  // Live TODO list for the active session. Hydrated from
  // ``session.todos`` on session switch and from ``session.todos.update``
  // SSE frames during a turn. ``null`` means "agent has never emitted
  // a TodoWrite snapshot for this session"; an empty array means
  // "all done" (kernel preserves it as a meaningful state).
  const [todos, setTodos] = useState<TodoItem[] | null>(null);
  // Live progress of Claude dynamic-workflow (``Workflow`` tool) runs in this
  // session, keyed by the launch tool_use_id. Fed by ``session.workflow_progress``
  // SSE snapshots; ``renderToolCall`` reads it to render the progress card on the
  // matching Workflow tool block. Live-only (the kernel never persists these), so
  // it stays empty on history replay / reconnect — the persisted Workflow tool
  // call then renders as a plain tool card. Cleared on session switch.
  const [workflowStates, setWorkflowStates] = useState<
    Map<string, WorkflowState>
  >(() => new Map());
  // Optimistic pending user message — rendered IMMEDIATELY when the user
  // hits Send so they see their text in the conversation without waiting
  // for the (potentially multi-second) round-trip through ensureSession ⇒
  // attachment upload ⇒ POST /messages ⇒ Claude SDK init ⇒ first SSE
  // frame. Cleared when the live ``message.user`` event arrives (the
  // backend always fires this as the very first event of a turn) or when
  // send fails.
  const [pendingUserMessage, setPendingUserMessage] = useState<{
    text: string;
    attachments: Array<{ name: string; size: number }>;
    /**
     * HISTORY cursor (durable-store seq) at the moment ``handleSend`` set
     * the pending — used by ``effectiveTurns`` to disambiguate a server
     * echo for THIS send (history envelope seq > fromSeq) from a previous
     * turn that happens to share the exact same text. Only comparable to
     * HISTORY seqs; a LIVE echo (kernel-local seq) is recognized via the
     * ``sentAt`` timestamp instead. Without this, sending the identical
     * text twice in a row would falsely dedup the optimistic against the
     * prior turn and snap-to-top would land on the older turn's position.
     */
    fromSeq: number;
    /**
     * Local ISO timestamp captured when ``handleSend`` set the pending,
     * surfaced as the optimistic turn's ``userTimestamp`` so the user
     * message action bar can render its hover-only clock immediately
     * — without it, the freshly-sent message would have no timestamp
     * until the SSE ``message.user`` echo lands and replaces the
     * pending turn with the real one.
     */
    sentAt: number; // Unix epoch ms (UTC)
  } | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  // Session input queue (docs/design/session-input-queue.md): follow-up inputs
  // submitted while a turn is running, drained FIFO after it. ``queuePaused``
  // is set after an interrupt — the user resumes explicitly.
  const [queue, setQueue] = useState<QueuedInput[]>([]);
  const [queuePaused, setQueuePaused] = useState(false);
  // True while a host drain chain is in flight. A dispatched (in-flight) item
  // is invisible in ``queue`` (only queued/blocked list), so the drain-follower
  // keys on this to keep re-subscribing until the LAST drained turn finishes —
  // not just while ``queue`` is non-empty (session-input-queue §14.5).
  const [queueDraining, setQueueDraining] = useState(false);
  // The item the drain is executing RIGHT NOW (status ``dispatched``): already
  // out of ``queue`` but its turn may not have landed a durable user message
  // yet. Rendered as a non-editable bubble while nothing is streaming so the
  // accepted message is never invisible in BOTH the queue bar and the
  // transcript (session-input-queue §14.5 补强③).
  const [queueDispatching, setQueueDispatching] = useState<QueuedInput | null>(
    null,
  );
  // Monotonic ticket for queue-list responses. Every fetch/mutation takes a
  // ticket before its await; only the NEWEST ticket may write the four queue
  // states. Without this, a slow ``GET /queue`` issued before an enqueue could
  // resolve after the enqueue's response and clobber the fresh list — making
  // the just-accepted item vanish until the next turn boundary.
  const queueListTicketRef = useRef(0);
  // "The drain chain continues after this turn" — mirrored into a ref so the
  // turn-end bookkeeping effect reads the freshest value without re-arming.
  // Gates the per-boundary sidebar refetch: refetching between every drained
  // item is visible churn.
  const queueContinuesRef = useRef(false);
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(
    null,
  );
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  // Runtime / provider / model start as ``null`` — the Settings →
  // Default tuple seeds them via ``useModelDefaults`` below for new
  // sessions. For an existing session the locked_* sync effect later
  // in this file overrides these from session metadata, so the order
  // is: defaults → session-locked → user picker.
  const [selectedRuntimeId, setSelectedRuntimeId] = useState<RuntimeId | null>(
    null,
  );
  // ``true`` once the user touches any composer picker. Locks out
  // Settings-default reseeds so explicit choices survive re-renders.
  const [composerTouched, setComposerTouched] = useState(false);
  const { defaults: modelDefaults, loading: defaultsLoading } =
    useModelDefaults();
  // Seed the composer pickers from Settings → Default ONLY for new
  // sessions. The session-locked sync effect later in this file owns
  // the existing-session path; if we let defaults run there too, the
  // composer would briefly flash to the global default before snapping
  // back to whatever the session was created with.
  useEffect(() => {
    if (!modelDefaults) return;
    if (selectedSessionId) return;
    if (composerTouched) return;
    // Agent-bound conversations seed runtime / model / effort from the
    // agent's brain (the agent-brain effect later in this file), not from
    // Settings → Default. Only quick chats (no agent) use the global default.
    if (selectedAgentSlug) return;
    // Force-assign — must beat the runtime-fallback effect below, which
    // otherwise races in first because useRuntimes is module-cached.
    if (modelDefaults.default_runtime) {
      setSelectedRuntimeId(modelDefaults.default_runtime);
    }
    if (modelDefaults.default_provider_id) {
      setSelectedProviderId(modelDefaults.default_provider_id);
    }
    if (modelDefaults.default_model) {
      setSelectedModelId(modelDefaults.default_model);
    }
    // Effort is non-nullable on ``ModelDefaults`` (the backend coerces
    // unset / cleared rows to ``EFFORT_FALLBACK`` server-side), so the
    // composer always opens on the user's actual Settings pick — Max
    // means Max, not the prompt-cache-friendly fallback "high".
    setSelectedEffort(modelDefaults.default_effort);
  }, [modelDefaults, composerTouched, selectedSessionId]);
  const { runtimes: runtimeList } = useRuntimes();
  // Repair the default if claude_agent ever reports unavailable.
  // Waits for ``useModelDefaults`` so we don't race-overwrite the
  // user's configured default before it lands.
  useEffect(() => {
    if (defaultsLoading) return;
    if (runtimeList.length === 0) return;
    const current = runtimeList.find((rt) => rt.id === selectedRuntimeId);
    if (current && current.available) return;
    const firstAvailable = runtimeList.find((rt) => rt.available);
    if (firstAvailable) {
      setSelectedRuntimeId(firstAvailable.id as RuntimeId);
    }
  }, [runtimeList, selectedRuntimeId, defaultsLoading]);
  const [retryCounts, setRetryCounts] = useState<Record<string, number>>({});
  const [modelSelectorUnlocked, setModelSelectorUnlocked] = useState(false);

  // ADR-013 approval mode. ``full_access`` matches the host's backend
  // default — preserves prior behaviour for users who don't touch the
  // picker. For an active session this is reconciled from
  // ``selectedSession.permission_mode`` by the effect below. For a new
  // session, this value is forwarded into ``sessionsApi.create``.
  // Mid-session changes go through PATCH /permission-mode (effective on
  // the runtime's next cold load, per the ADR).
  const [selectedPermissionMode, setSelectedPermissionMode] = useState<
    "default" | "auto_review" | "full_access"
  >("full_access");

  // Reasoning-effort budget for the session (kernel V5+bba3014
  // ``ModelSettings.effort``). ``null`` = let the runtime fall through
  // to its SDK default. For a new session, this value is forwarded into
  // ``sessionsApi.create``; for an existing session, mid-session
  // changes PATCH ``/v1/sessions/{id}/effort`` (live-reconcile applies
  // on next Send).
  const [selectedEffort, setSelectedEffort] = useState<
    "low" | "medium" | "high" | "xhigh" | "max" | null
  >(null);

  // Multi-target editions: where a NEW quick/temp chat runs. ``null`` follows
  // the registered default; single-target builds register nothing and the
  // picker renders nothing. Locked at session creation (ADR-006 semantics) —
  // project conversations don't get a choice, they follow the project's
  // origin. See docs (commercial): execution-location-per-entity.
  const executionTargets = useExecutionTargets();
  const [execTargetId, setExecTargetId] = useState<string | null>(null);
  const resolveExecTarget = useCallback(() => {
    if (executionTargets.length === 0) return undefined;
    return (
      executionTargets.find((target) => target.id === execTargetId) ??
      getDefaultExecutionTarget()
    );
  }, [executionTargets, execTargetId]);

  // Connector selection — only meaningful for new sessions (locked at creation
  // per ADR-006). The picker UI was removed from the composer; we still
  // pre-select every connected connector at session-creation time so the
  // new session inherits the user's globally-enabled data sources.
  const [selectedMcpSlugs, setSelectedMcpSlugs] = useState<string[]>([]);
  // Connected connectors shown in the composer "+" menu. On a new conversation
  // they're toggleable (the selection is handed to the session at creation); on
  // an existing one they're read-only (connectors lock at creation). Fetched +
  // defaulted to all-on once on mount, so a new conversation keeps the user's
  // pick across the new→existing URL transition (which reuses this component).
  const [connectorOptions, setConnectorOptions] = useState<ComposerConnector[]>(
    [],
  );
  const isNewSession = id === NEW_SESSION_ID;
  useEffect(() => {
    connectorsApi
      .list()
      .then(({ connectors: list }) => {
        const connected = list.filter((c) => c.status === "connected");
        setConnectorOptions(
          connected.map((c) => ({
            slug: c.slug,
            label: c.display_name,
            description: c.description ?? undefined,
          })),
        );
        setSelectedMcpSlugs(connected.map((c) => c.slug));
      })
      .catch(() => {
        /* non-fatal */
      });
    // Mount-only: re-running on the new→existing id change would reset the
    // user's connector selection right after they created the session.
  }, []);
  const toggleConnector = useCallback((slug: string, enabled: boolean) => {
    setSelectedMcpSlugs((prev) =>
      enabled
        ? prev.includes(slug)
          ? prev
          : [...prev, slug]
        : prev.filter((s) => s !== slug),
    );
  }, []);
  // Per-``submit_skill`` tool_use state — keyed by tool_use id so multiple
  // submissions in the same conversation render independently. Persists
  // for the lifetime of the page; on refresh the cards re-render in
  // their initial "pending" state, which is acceptable for v1 (the
  // backend has the staged content + library state of record).
  type SubmissionEntry = {
    state: SkillSubmissionState;
    errorMessage?: string;
    boundToProjectLabel?: string | null;
    // Live snapshot of the staged slug's contents — populated by the
    // page's scan poll. Drives both the "save" gate (we only enable the
    // save button when files are actually present) and the file tree
    // the card surfaces inline.
    stagedFiles?: {
      path: string;
      type: "file" | "directory";
      size?: number | null;
    }[];
    stagingPath?: string;
  };
  const [submissionStates, setSubmissionStates] = useState<
    Record<string, SubmissionEntry>
  >({});
  // Per-``tool.id`` state for ``propose_agent`` cards (natural-language agent
  // creation). Unlike skills there's no server-side staging — the full spec
  // rides the tool input — so the card starts ``pending`` and dismiss is
  // purely client-side.
  type ProposalEntry = {
    state:
      | "pending"
      | "confirming"
      | "confirmed"
      | "dismissing"
      | "dismissed"
      | "error";
    errorMessage?: string;
    deployedProjectLabel?: string | null;
  };
  const [proposalStates, setProposalStates] = useState<
    Record<string, ProposalEntry>
  >({});
  // Per-``tool.id`` state for ``automation create`` proposal cards. Same
  // propose→confirm model as agents; ``automationId`` is filled on confirm /
  // re-entry so a confirmed card can deep-link into the automation page.
  type AutomationProposalEntry = {
    state:
      | "pending"
      | "confirming"
      | "confirmed"
      | "dismissing"
      | "dismissed"
      | "error";
    errorMessage?: string;
    automationId?: string | null;
  };
  const [automationProposalStates, setAutomationProposalStates] = useState<
    Record<string, AutomationProposalEntry>
  >({});
  // Local mirror of ``answers`` captured at submit time. Keyed by
  // ``tool_use_id`` (== renderer's ``tool.id``). Lets the renderer
  // swap to ``UserAnswerSummaryCard`` IMMEDIATELY on click — without
  // waiting for the paired ``session.action_resolved`` SSE frame to
  // round-trip. Once that frame lands ``askUserQuestionAnswersByToolId``
  // takes precedence (kernel is the authority); on submit failure the
  // entry is cleared and the interactive card returns.
  const [askUserQuestionLocalAnswers, setAskUserQuestionLocalAnswers] =
    useState<Record<string, Record<string, string | string[]>>>({});
  // Once ``action_resolved (decision="answer")`` lands for a parked
  // AskUserQuestion, we swap the interactive ``AskUserQuestionCard``
  // for a ``UserAnswerSummaryCard`` that shows each question → answer
  // pair. Derived from the event stream so live submits AND replay-
  // after-reload both flow through the same matching logic.
  //
  // We match by ``pending_id`` (carried on both
  // ``session.requires_action`` and ``session.action_resolved``) plus
  // a temporal pairing of the immediately-preceding AskUserQuestion
  // ``tool.call.started`` — kernel parks one clarifying pending at a
  // time per session (orchestrator.submit_action raises
  // ``PendingActionConflictError`` otherwise), so the most recent
  // AskUserQuestion tool_use before a ``clarifying_questions``
  // requires_action is unambiguously its source. This avoids relying
  // on ``message_id`` reaching ``tool.call.started`` over live SSE,
  // which has historically been fragile across the kernel
  // ``_MessageIdStampSink`` → bus → broadcast → SSE chain.
  const askUserQuestionAnswersByToolId = useMemo(() => {
    const out: Record<string, Record<string, string | string[]>> = {};
    const pendingIdToToolId = new Map<string, string>();
    let lastAskToolId: string | null = null;
    for (const ev of events) {
      const type = ev.event.event_type;
      const payload = ev.event.payload ?? {};
      if (type === "tool.call.started") {
        const name = payload.name;
        const isAsk = isToolNamed(name, "AskUserQuestion");
        if (isAsk) {
          const toolUseId = payload.tool_use_id || payload.id;
          if (toolUseId) lastAskToolId = toolUseId;
        }
      } else if (type === "session.requires_action") {
        if (
          payload.subject === "clarifying_questions" &&
          payload.pending_id &&
          lastAskToolId
        ) {
          pendingIdToToolId.set(payload.pending_id, lastAskToolId);
        }
      }
    }
    for (const ev of events) {
      if (ev.event.event_type !== SESSION_ACTION_RESOLVED_EVENT) continue;
      const ar = parseActionResolved(ev);
      if (!ar || ar.decision !== "answer") continue;
      const toolId = pendingIdToToolId.get(ar.pending_id);
      if (toolId) {
        out[toolId] = ar.answers;
      }
    }
    return out;
  }, [events]);
  // ADR-013 cross-runtime approval contract: the runtime parks on
  // ``clarifying_questions`` when AskUserQuestion is invoked, emitting a
  // ``session.requires_action`` with a ``pending_id``. Resolving it
  // requires POST /v1/sessions/{id}/actions with decision=answer + the
  // structured answers map. Since the runtime parks (only one
  // clarifying pending open at a time per session), we just track the
  // current unresolved one. ``action_resolved`` for the same pending_id
  // clears it. The kernel doesn't echo back a tool_use_id in the
  // pending payload so we can't key this by ``tool.id`` directly — the
  // FIFO is "the only open clarifying pending".
  const currentClarifyingPendingRef = useRef<string | null>(null);
  // ADR-013 — pending approve/reject cards for the 4 non-clarifying
  // subjects (shell_command / file_change / mcp_tool_call /
  // tool_input). Rendered as a tray above the Composer. Each card
  // shows the subject payload + Approve / Reject; user click POSTs
  // /actions and the paired action_resolved SSE frame flips the card
  // into its answered state and eventually removes it from the tray.
  interface PendingApprovalEntry {
    pendingId: string;
    subject: ApprovalCardSubject;
    payload: Record<string, unknown>;
    // V5+d008b53 (v2 approval contract):
    availableDecisions: string[];
    sessionRulePreviewDisplay: string | null;
    originalInput: Record<string, unknown> | null;
    receivedAt?: number; // Unix epoch ms (UTC)
    submitting?: boolean;
    // Resolution state. Populated when the matching
    // ``action_resolved`` lands. Drives the swap from the full
    // ``ApprovalCard`` to the compact ``ApprovalResolvedStrip``.
    answered?: boolean;
    decision?: ApprovalResolvedDecision;
    rejectMessage?: string | null;
  }
  const [pendingApprovals, setPendingApprovals] = useState<
    PendingApprovalEntry[]
  >([]);
  // V5+d008b53 — cache-hit notices pushed when the kernel emits
  // ``action_resolved(decision="auto_approved")``. The kernel pairs
  // every cache-hit with a preceding ``requires_action`` (see
  // ``claude_agent/runtime.py``), so by the time the resolved frame
  // lands we already have the originating ``subject`` + ``payload``
  // sitting in ``pendingApprovals``. The handler harvests those into
  // the notice before removing the pending entry, so the strip can
  // render a one-line summary of WHAT was approved (e.g.
  // ``reportify-stock.stock_quote(symbol=000858)``) instead of a
  // generic "auto-approved" label.
  //
  // Lifecycle: each push is paired with a ``setTimeout`` filter so
  // the tray auto-clears 5s later (symmetric with
  // ``ApprovalResolvedStrip``'s 2s, longer here because the user
  // didn't initiate the action and needs more time to notice).
  // Dedup by pending_id prevents an SSE retransmit / replay from
  // double-inserting before the timer fires.
  interface AutoApprovedNotice {
    pendingId: string;
    receivedAtLabel?: string;
    rulePreviewDisplay: string | null;
    // Captured from the matching ``requires_action`` row in
    // ``pendingApprovals`` at resolve time. ``null`` when the
    // requires_action couldn't be matched (rare — replay edge case).
    subject: ApprovalCardSubject | null;
    payload: Record<string, unknown> | null;
  }
  const [autoApprovedNotices, setAutoApprovedNotices] = useState<
    AutoApprovedNotice[]
  >([]);
  // ``rule_id → preview.display`` learned at ``approve_for_session``
  // commit time. Lets the auto_approved strip show the rule that
  // fired even though the kernel only re-emits the rule_id, not the
  // preview, on subsequent cache hits.
  const ruleIdToPreviewRef = useRef<Map<string, string>>(new Map());
  // Ref so renderToolCall (declared before handleSend) can invoke the
  // submit handler without a block-scope ordering error.
  const askUserQuestionSubmitRef = useRef<
    (toolId: string, answers: Record<string, string>) => void
  >(() => {});
  // The session-lifetime stream's controller. Owned by the session-open
  // effect via ``subscribeToSession`` (which supersedes the previous one);
  // aborted on unmount. Nothing else opens or closes streams.
  const abortRef = useRef<AbortController | null>(null);
  // Set while ``handleSend`` is between ``ensureSession`` and its POST
  // settling. Remaining consumer: the bootstrap promote-fast-path, which
  // skips the history refetch on the ``/conversation/new`` → real-id
  // promotion so the optimistic transcript isn't clobbered mid-send.
  const isSendInFlightRef = useRef(false);
  // HISTORY cursor — the highest DURABLE-store seq confirmed via REST
  // history reads (``listEvents`` / ``listEventsWindow`` responses). Used as
  // ``after_seq`` for history reads and for the SSE subscribe/reconnect
  // cursor. NEVER advanced from live SSE frames: those carry the kernel's
  // LOCAL seq — an independent space — and feeding one into this cursor
  // would skip (or endlessly replay) history. Live-path dedup is uid-based
  // (``event_uid``), not cursor-based.
  const historyCursorRef = useRef(0);
  // Consecutive unexpected-close reconnect attempts for the live events
  // stream. Reset whenever a live frame is delivered (a healthy stream)
  // and on every session switch; capped so a session whose stream is
  // repeatedly cut (dead proxy, wedged server) degrades to the
  // status-reconcile path instead of reconnecting forever.
  const streamReconnectAttemptsRef = useRef(0);
  // Earliest seq currently in ``events``. Used as the cursor for the
  // upward "load older turns" pager. Starts at ``Infinity`` so the first
  // ``listEventsWindow`` call (which omits ``before_seq``) targets the
  // latest window — the second call uses this value verbatim.
  const minSeqRef = useRef<number>(Number.POSITIVE_INFINITY);
  // Whether the server reports older turns are still available beyond
  // the current window. Drives the IntersectionObserver gating + the
  // sentinel's visibility — both are kept in sync via ``setHasMoreOlder``
  // wherever the ref is mutated. The ref form lets ``loadOlderTurns``
  // read the current value without taking a re-render dependency.
  const hasMoreOlderRef = useRef(false);
  const [hasMoreOlder, setHasMoreOlder] = useState(false);
  // Concurrent-load guard. The IntersectionObserver can fire several
  // times in quick succession as the sentinel enters/leaves the
  // viewport; without this guard we'd issue duplicate requests.
  const loadingOlderRef = useRef(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  // Becomes true once the user has issued a real scroll on the
  // conversation scroller (mousewheel / trackpad / arrow-key scroll).
  // Programmatic scroll-to-bottom-on-load and ResizeObserver-driven
  // re-measures don't flip it. ``loadOlderTurns`` short-circuits while
  // this is false so the IntersectionObserver can't cascade-load every
  // page in a single tick during the initial mount race (sentinel
  // briefly visible at scrollTop=0 before auto-scroll-to-bottom fires).
  const userScrolledRef = useRef(false);
  // One-shot scroll anchor: when the upward pager prepends events to
  // the front of the list, the scroll container's existing items shift
  // downward by the new content's height. This ref captures the pre-
  // prepend ``scrollHeight``/``scrollTop`` so a useLayoutEffect can
  // re-pin the scrollTop to the same logical content position before
  // the user perceives a jump.
  const pendingScrollAnchorRef = useRef<{
    oldScrollHeight: number;
    oldScrollTop: number;
  } | null>(null);
  const interruptRef = useRef<() => void>(() => {});
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const turnListVirtualApiRef = useRef<{
    scrollToTurnTop: (index: number) => void;
  } | null>(null);
  const topSentinelRef = useRef<HTMLButtonElement>(null);
  const pinNextTurnToTopRef = useRef(false);
  const keepCurrentTurnAtTopRef = useRef(false);
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const [kbPickerOpen, setKbPickerOpen] = useState(false);
  // Open while confirming a send that would ship still-parsing attachments.
  const [parsingConfirmOpen, setParsingConfirmOpen] = useState(false);
  const sidebarSessions = useSessionStore((state) => state.sessions);
  const setSidebarSessions = useSessionStore((state) => state.setSessions);
  const fetchSidebarSessions = useSessionStore((state) => state.fetchSessions);
  const setStoreActiveProjectId = useSessionStore(
    (state) => state.setActiveProjectId,
  );
  const upsertProject = useProjectStore((s) => s.upsertProject);
  // Server-stored attachments + async parse status, owned by the shared hook:
  // upload-on-attach, poll ``parsing → ready|failed``, and live progress for
  // the composer chips + context panel. ``setSessionAttachments`` is the
  // hook's own setter (kept aliased so existing optimistic splices still
  // work). Local files now upload the moment they're attached, so there is no
  // separate not-yet-uploaded ``File[]`` queue for them anymore.
  const {
    attachments: sessionAttachments,
    setAttachments: setSessionAttachments,
    hasParsing: attachmentsParsing,
    attachLocalFiles,
    attachKbDocs,
    remove: removeSessionAttachmentRow,
    markPendingConsumed,
  } = useSessionAttachments(selectedSessionId);
  // Agent-delivered artifacts (the "生成文件" list) — recorded by the
  // ``deliver_artifacts`` MCP tool. Loads on session change; refreshed on
  // turn-end (below) so newly delivered files appear without a manual reload.
  const { artifacts: sessionArtifacts, refresh: refreshArtifacts } =
    useSessionArtifacts(selectedSessionId);
  const navigate = useNavigate();

  const [availableSkills, setAvailableSkills] = useState<SkillView[]>([]);
  const [selectedComposerSkill, setSelectedComposerSkill] =
    useState<SkillView | null>(null);
  const [projectSkills, setProjectSkills] = useState<SkillView[]>([]);

  // Residual-state sweep for in-place session transitions. The layout used
  // to remount this whole page on every pathname change, which incidentally
  // wiped composer/turn-scoped state; conversation routes now transition in
  // place (so the ``new`` → ``{id}`` promotion survives), so anything the
  // remount used to clean must be cleared explicitly. Keyed on
  // ``conversationInstanceKey``, which changes exactly on TRUE session
  // switches — stable across the promotion (where ``handleSend`` owns this
  // state) and a no-op on mount (everything is still at its initial value).
  useEffect(() => {
    setDraft("");
    setSelectedComposerSkill(null);
    setRetryCounts({});
    setAutoApprovedNotices([]);
    userScrolledRef.current = false;
  }, [conversationInstanceKey]);

  const [fileTree, setFileTree] = useState<FileTreeNode[]>([]);

  const activeProject = useMemo(
    () =>
      (projects.find((w) => w.id === selectedProjectId) as
        ProjectDetail | undefined) ?? null,
    [selectedProjectId, projects],
  );
  const activeProjectRootPath = useMemo(() => {
    const detail = activeProject as ProjectDetail | null;
    return detail?.cwd ?? detail?.root_path ?? "";
  }, [activeProject]);

  // The worktree the open session runs in (creation-time snapshot on the list
  // item). When present, the right panel's file tree + artifact reads scope to
  // the worktree checkout (design D7) instead of the shared project cwd.
  // Derived from ``sessions`` (not ``selectedSession``, declared below) so the
  // callbacks above it can depend on it without a temporal-dead-zone error.
  const activeWorktree = useMemo(
    () => sessions.find((s) => s.id === selectedSessionId)?.worktree ?? null,
    [sessions, selectedSessionId],
  );

  const locateArtifactFile = useCallback(
    (path: string) => {
      // Session cwd = the worktree checkout when present, else the project cwd.
      const root = activeWorktree?.path ?? activeProjectRootPath;
      return {
        absolutePath: resolveConversationArtifactPath(path, root),
        relativePath: toConversationRelativeArtifactPath(path, root) ?? path,
      };
    },
    [activeProjectRootPath, activeWorktree],
  );
  const artifactFile = useArtifactFile({
    projectId:
      selectedProjectId && selectedProjectId !== "chat-default"
        ? selectedProjectId
        : null,
    platform,
    locate: locateArtifactFile,
    missingErrorMessage: t(
      "task.artifactOpenInFinder" as Parameters<typeof t>[0],
    ),
  });
  const {
    selectedPath: selectedArtifactPath,
    artifact,
    content: artifactContent,
    target: artifactTarget,
    loading: artifactLoading,
    error: artifactError,
    open: openArtifact,
    reload: reloadArtifact,
    close: closeArtifact,
  } = artifactFile;

  const openArtifactFile = useCallback(
    async (path: string, target?: ArtifactOpenTarget) => {
      await openArtifact(path, target);
    },
    [openArtifact],
  );

  const localFileLinkRootPath = activeWorktree?.path ?? activeProjectRootPath;
  const localFileLinks = useConversationLocalFileLinks({
    projectRootPath: localFileLinkRootPath,
    runtimeMode: directoryFieldMode === "managed" ? "managed" : "local",
    previewFile: (path, target) => {
      void openArtifactFile(path, target);
    },
    openFile: (path) => {
      void revealInFinder(path);
    },
    blockFile: () => {
      toast.info(t("project.managedDirHint" as Parameters<typeof t>[0]));
    },
  });

  const handleArtifactReload = useCallback(() => {
    void reloadArtifact();
  }, [reloadArtifact]);

  const handleArtifactClose = useCallback(() => {
    closeArtifact();
  }, [closeArtifact]);

  const handleArtifactCopy = useCallback(() => {
    if (artifactContent?.kind !== "text") return;
    void navigator.clipboard
      ?.writeText(artifactContent.content)
      .then(() => toast.success(t("common.copied" as Parameters<typeof t>[0])))
      .catch(() => toast.error(t("common.failed" as Parameters<typeof t>[0])));
  }, [artifactContent, t]);

  const handleArtifactOpenExternal = useCallback(() => {
    if (!selectedArtifactPath) return;
    void revealInFinder(locateArtifactFile(selectedArtifactPath).absolutePath);
  }, [locateArtifactFile, revealInFinder, selectedArtifactPath]);

  // Project KB bindings — loaded for project projects only and
  // rendered **read-only** in the session panel (per product rule,
  // binding edits happen on the project detail page and apply to the
  // next session). ``handleExpandKbFolder`` is still wired so the
  // user can drill folders to inspect what's selected; only the
  // toggle handler is withheld. Passing ``null`` for chat /
  // skill-creator projects makes the hook no-op so those panels
  // show no KB tree.
  const {
    kbTree: projectKbTree,
    bindings: projectKbBindings,
    handleExpandKbFolder: handleExpandProjectKbFolder,
  } = useProjectKbBindings(
    activeProject?.kind === "project" ? selectedProjectId : null,
  );

  // Global KB document tree for the attachment picker. Loads lazily —
  // only when the picker is open — and is independent of the
  // project (chat sessions have no project scope; the picker shows
  // every KB for both chat and project conversations).
  const {
    kbTree: pickerKbTree,
    loading: pickerKbLoading,
    expandFolder: pickerExpandFolder,
  } = useKbDocTree(kbPickerOpen);

  const selectedSession = useMemo(
    () => sessions.find((s) => s.id === selectedSessionId) ?? null,
    [selectedSessionId, sessions],
  );

  // The composer's loading / Stop state (and the streaming logo + "已处理 X 秒"
  // timer) is DERIVED (session-stream-lifetime §2.1): ``sending`` is only the
  // optimistic click → turn-start bridge (released by the turn's start /
  // terminal events or a send error); the reconciled session ``status``
  // carries busy for the turn itself — including turns started by the queue
  // drain, a schedule, or another client. The stream being open says nothing.
  const isBusy = deriveTurnActive(sending, selectedSession?.status);
  // Queue continuity for the LOADING AFFORDANCES only (shimmer / Stop /
  // timer): between two drained items the session is briefly idle — while the
  // host drain chain is in flight (``queueDraining``; authoritative, refreshed
  // at every boundary and by the backstop below) keep the affordances up
  // across those sub-second gaps. Display + send-routing only — the
  // turn-boundary effects (queue refetch, file-tree refresh, bookkeeping)
  // stay on raw ``isBusy`` because they must fire per drained turn.
  const displayBusy = isBusy || (queueDraining && !queuePaused);

  // The agent actually bound to this composer: an existing session is frozen to
  // its ``sessionAgentSlug`` (ADR-006), a fresh draft uses the picker's
  // ``selectedAgentSlug``. The Composer's ``selectedAgentSlug`` prop derives the
  // same value — skill resolution and the ``/`` gate must use it too, or they'd
  // read a null draft slug while viewing a live conversation.
  const effectiveAgentSlug = selectedSession
    ? sessionAgentSlug
    : selectedAgentSlug;

  // Publish the open conversation's project to the store so the sidebar keeps
  // that project's accordion expanded — authoritative and immediate (straight
  // from the loaded session detail), unlike the lagging runs list. Cleared when
  // the conversation is project-less or this page unmounts.
  const openConversationProjectId = selectedSession?.project_id ?? null;
  useEffect(() => {
    setStoreActiveProjectId(openConversationProjectId);
    return () => setStoreActiveProjectId(null);
  }, [openConversationProjectId, setStoreActiveProjectId]);
  // Incremental transcript build: folds only newly-appended events per render
  // (falls back to a full rebuild on any non-append change) and hands back
  // turns that already satisfy the stable-ref contract — so a long streamed
  // reply no longer re-walks the whole event history per token. Replaces the
  // old ``useStableTurns(buildTurns(events))`` O(N²) hot path.
  const turns = useIncrementalTurns(events);
  // Background tasks (run_in_background shell commands) — derived from the
  // same persisted event list, so the "still running" strip is correct on
  // live streams and after re-entering the page mid-run.
  const runningBgTasks = useMemo(
    () => runningBackgroundTasks(deriveBackgroundTasks(events)),
    [events],
  );
  // No idle-time bg-task poll anymore: the session-lifetime stream stays open
  // between turns (the server generator drains bus events between turns too),
  // so bg_task terminal frames and the CLI's spontaneous wake-up turn arrive
  // live on the same connection that carried the launching turn.

  // VALUZ-CHATPLAN — track the LATEST ``plan_task`` / ``modify_plan`` tool
  // result per task_id. That position in the conversation gets the rich
  // LiveTaskCard (subtask list + status + actions, SSE-subscribed). Earlier
  // plan writes for the same task degrade to compact history pills, so the
  // user always lands on the "current state" surface near the bottom of
  // the timeline. ``taskByRichTool`` also doubles as a "this is a plan
  // write for a known task" set for the pill renderer. The task id is
  // resolved from the lead session's ``metadata.valuz.task_id`` (the
  // authoritative binding) rather than the unreliable tool args — see
  // ``computePlanAnchors``.
  const planAnchors = useMemo(
    () => computePlanAnchors(turns, selectedSession?.task_id ?? null),
    [turns, selectedSession],
  );
  // Append the optimistic pending turn so the user sees their message +
  // a thinking hint immediately on Send. ``ConversationTurnList`` keys
  // off ``sending && isLatest`` to draw the waiting indicator on the
  // last turn — placing the optimistic one at the end gets that for
  // free without touching the renderer.
  const effectiveTurns = useMemo(() => {
    if (!pendingUserMessage) return turns;
    // Defensive dedup: if the kernel's ``message.user`` echo has already
    // been folded into ``turns`` but ``setPendingUserMessage(null)`` hasn't
    // landed yet (race between two batched setStates inside the SSE
    // callback), the latest real turn carries the same userText as the
    // optimistic — drop the optimistic so the user doesn't see two
    // identical bubbles in the same render.
    //
    // The ``fromSeq`` guard makes this dedup specific to THIS pending
    // send: only collapse when the latest turn was built from an event
    // with a seq STRICTLY LATER than the moment we set the pending. A
    // previous turn that happens to share the exact same text was
    // born from an event with seq <= fromSeq, so it stays + the new
    // optimistic still appends — otherwise re-sending identical text
    // would silently land snap-to-top on the older turn.
    const lastTurn = turns[turns.length - 1];
    const lastTurnSeq = lastTurn?.userMessageSeq ?? 0;
    // Two "the echo is newer than this send" signals, because the echo can
    // arrive from either seq space: a HISTORY row satisfies
    // ``seq > fromSeq`` (fromSeq is the history cursor at send time); a
    // LIVE frame carries a kernel-local seq that can't be compared to
    // fromSeq, so fall back to the store-independent event timestamp vs
    // the moment the pending was set. A previous turn with identical text
    // fails both (its history seq <= fromSeq; its timestamp < sentAt).
    if (
      lastTurn &&
      lastTurn.userText === pendingUserMessage.text &&
      (lastTurnSeq > pendingUserMessage.fromSeq ||
        (lastTurn.userTimestamp !== undefined &&
          lastTurn.userTimestamp >= pendingUserMessage.sentAt))
    ) {
      return turns;
    }
    return [
      ...turns,
      {
        id: "pending-turn",
        userMessageSeq: 0,
        userText: pendingUserMessage.text,
        blocks: [],
        failedMessage: null,
        attachments:
          pendingUserMessage.attachments.length > 0
            ? pendingUserMessage.attachments
            : undefined,
        userTimestamp: pendingUserMessage.sentAt,
      },
    ];
  }, [turns, pendingUserMessage]);

  // ── ``submit_skill`` tool_use → submission card wiring ──────────────
  //
  // The agent calls ``submit_skill`` once a draft is staged and ready
  // for the user to review. We replace the generic ToolCallCard for
  // that specific tool with the dedicated SkillSubmissionCard, which
  // surfaces save / dismiss buttons.
  //
  // Tool names off the kernel SSE stream come through the SDK MCP
  // bridge as ``mcp__harness__submit_skill`` (Claude Agent SDK) or
  // plain ``submit_skill`` (deepagents/codex). Match either — the
  // kernel doesn't normalise.
  const submissionProjectLabel = useMemo(() => {
    if (selectedSession?.name) return selectedSession.name;
    return null;
  }, [selectedSession]);

  const handleConfirmSubmission = useCallback(
    async (
      toolId: string,
      slug: string,
      summary: string | undefined,
      changeKind: "create" | "update",
      filesTouched: string[],
    ) => {
      const sid = selectedSessionIdRef.current;
      if (!sid) return;
      setSubmissionStates((prev) => ({
        ...prev,
        [toolId]: {
          ...(prev[toolId] || { state: "pending" }),
          state: "confirming",
        },
      }));
      try {
        const res = await skillsApi.confirmSubmission(sid, slug, {
          summary: summary ?? null,
          change_kind: changeKind,
          files_touched: filesTouched,
        });
        const boundLabel =
          res.bound_to_project_id && res.creation_context.kind === "project"
            ? submissionProjectLabel
            : null;
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: {
            state: "confirmed",
            boundToProjectLabel: boundLabel,
          },
        }));
        toast.success(t("skill.savedToLib" as Parameters<typeof t>[0]));
      } catch (cause) {
        const msg =
          cause instanceof Error
            ? cause.message
            : t("common.saveFailed" as Parameters<typeof t>[0]);
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: { state: "error", errorMessage: msg },
        }));
        toast.error(msg);
      }
    },
    [submissionProjectLabel],
  );

  const handleDismissSubmission = useCallback(
    async (toolId: string, slug: string) => {
      const sid = selectedSessionIdRef.current;
      if (!sid) return;
      setSubmissionStates((prev) => ({
        ...prev,
        [toolId]: {
          ...(prev[toolId] || { state: "pending" }),
          state: "dismissing",
        },
      }));
      try {
        await skillsApi.dismissSubmission(sid, slug);
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: { state: "dismissed" },
        }));
      } catch (cause) {
        const msg =
          cause instanceof Error
            ? cause.message
            : t("conversation.cancelFailed" as Parameters<typeof t>[0]);
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: { state: "error", errorMessage: msg },
        }));
        toast.error(msg);
      }
    },
    [],
  );

  // Create + deploy the agent the assistant proposed via ``propose_agent``.
  // The spec is replayed from the tool input (no server staging, unlike
  // skills); the backend derives the slug and deploys into the session's
  // project when there is one.
  const handleConfirmProposal = useCallback(
    async (
      toolId: string,
      spec: {
        name: string;
        instructions: string;
        description?: string;
        runtime?: string;
        model?: string;
        skills?: string[];
        connectors?: string[];
      },
    ) => {
      const sid = selectedSessionIdRef.current;
      if (!sid) return;
      setProposalStates((prev) => ({
        ...prev,
        [toolId]: {
          ...(prev[toolId] || { state: "pending" }),
          state: "confirming",
        },
      }));
      try {
        const res = await agentsApi.confirmProposal(sid, spec);
        setProposalStates((prev) => ({
          ...prev,
          [toolId]: {
            state: "confirmed",
            deployedProjectLabel:
              res.deployed && res.project_id ? submissionProjectLabel : null,
          },
        }));
        toast.success(t("agent.proposalCreated" as Parameters<typeof t>[0]));
      } catch (cause) {
        const msg =
          cause instanceof Error
            ? cause.message
            : t("common.saveFailed" as Parameters<typeof t>[0]);
        setProposalStates((prev) => ({
          ...prev,
          [toolId]: { state: "error", errorMessage: msg },
        }));
        toast.error(msg);
      }
    },
    [submissionProjectLabel],
  );

  const handleDismissProposal = useCallback((toolId: string) => {
    // Client-side only — nothing was written, so there's nothing to clean up.
    setProposalStates((prev) => ({
      ...prev,
      [toolId]: { state: "dismissed" },
    }));
  }, []);

  // Create the automation the assistant proposed via ``automation create``.
  // The confirmable spec is replayed from the parsed tool output; the backend
  // re-resolves project / bound-agent context from the session and stamps the
  // proposing ``tool_call_id`` so a reload can detect the row already exists.
  const handleConfirmAutomation = useCallback(
    async (
      toolId: string,
      spec: {
        name: string;
        prompt_template: string;
        trigger: import("@valuz/core").Trigger;
        agent_slug?: string | null;
        action_kind?: "chat" | "task";
        worktree?: boolean;
      },
    ) => {
      const sid = selectedSessionIdRef.current;
      if (!sid) return;
      setAutomationProposalStates((prev) => ({
        ...prev,
        [toolId]: {
          ...(prev[toolId] || { state: "pending" }),
          state: "confirming",
        },
      }));
      try {
        const res = await automationsApi.confirmProposal(sid, {
          tool_call_id: toolId,
          name: spec.name,
          prompt_template: spec.prompt_template,
          trigger: spec.trigger,
          agent_slug: spec.agent_slug ?? null,
          action_kind: spec.action_kind,
          worktree: spec.worktree ?? false,
        });
        setAutomationProposalStates((prev) => ({
          ...prev,
          [toolId]: { state: "confirmed", automationId: res.automation_id },
        }));
        toast.success(
          t("automation.proposalCreated" as Parameters<typeof t>[0]),
        );
      } catch (cause) {
        const msg =
          cause instanceof Error
            ? cause.message
            : t("common.saveFailed" as Parameters<typeof t>[0]);
        setAutomationProposalStates((prev) => ({
          ...prev,
          [toolId]: { state: "error", errorMessage: msg },
        }));
        toast.error(msg);
      }
    },
    [t],
  );

  const handleDismissAutomation = useCallback((toolId: string) => {
    // Client-side only — nothing was written, so there's nothing to clean up.
    setAutomationProposalStates((prev) => ({
      ...prev,
      [toolId]: { state: "dismissed" },
    }));
  }, []);

  // Stable signature of the propose_agent tool_use ids in this session, so the
  // re-entry detection below fetches only when the set of proposal cards
  // changes (not on every streamed token).
  const proposeAgentToolSig = useMemo(() => {
    const ids: string[] = [];
    for (const turn of turns) {
      for (const block of turn.blocks) {
        if (block.kind !== "tool") continue;
        const tname = block.tool.title || "";
        if (isToolNamed(tname, "propose_agent")) {
          ids.push(block.tool.id);
        }
      }
    }
    return ids.join(",");
  }, [turns]);

  // Reflect agents already created from a propose_agent card when the user
  // RE-ENTERS the session. In-memory ``proposalStates`` is lost on reload, so a
  // confirmed card would otherwise show "pending" again (and a second click
  // would create a duplicate). ``propose_agent`` always creates a
  // ``source=custom`` library agent named exactly as proposed, so a library
  // match means the proposal was confirmed. Best-effort + name-based; never
  // overwrites a live user transition (confirming/dismissing/terminal).
  useEffect(() => {
    if (!selectedSessionId || !proposeAgentToolSig) return;
    const proposeTools: { id: string; name: string }[] = [];
    for (const turn of turns) {
      for (const block of turn.blocks) {
        if (block.kind !== "tool") continue;
        const tname = block.tool.title || "";
        if (!isToolNamed(tname, "propose_agent")) {
          continue;
        }
        let nm = "";
        if (block.tool.input) {
          try {
            const parsed =
              typeof block.tool.input === "string"
                ? JSON.parse(block.tool.input)
                : block.tool.input;
            nm = String(parsed?.name || "");
          } catch {
            /* malformed/streaming input — skip */
          }
        }
        if (nm) proposeTools.push({ id: block.tool.id, name: nm });
      }
    }
    if (proposeTools.length === 0) return;

    let cancelled = false;
    void (async () => {
      try {
        const res = await agentsApi.listAgents("custom");
        if (cancelled) return;
        const names = new Set(res.agents.map((a) => a.name));
        setProposalStates((prev) => {
          let changed = false;
          const next = { ...prev };
          for (const { id, name } of proposeTools) {
            const cur = next[id];
            // Keep live (confirming/dismissing) and terminal (confirmed/
            // dismissed/error) states — only seed an untracked/pending card.
            if (cur && cur.state !== "pending") continue;
            if (names.has(name)) {
              next[id] = { state: "confirmed" };
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      } catch {
        /* non-fatal — list endpoint can fail transiently */
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSessionId, proposeAgentToolSig]);

  // Stable signature of the ``automation create`` proposal tool_use ids in this
  // session — only create-action calls render a proposal card.
  const automationCreateToolSig = useMemo(() => {
    const ids: string[] = [];
    for (const turn of turns) {
      for (const block of turn.blocks) {
        if (block.kind !== "tool") continue;
        const tname = block.tool.title || "";
        if (!isToolNamed(tname, "automation")) continue;
        if (parseAutomationCreateInput(block.tool.input))
          ids.push(block.tool.id);
      }
    }
    return ids.join(",");
  }, [turns]);

  // Reflect automations already created from a proposal card on session
  // RE-ENTRY (in-memory state is lost on reload). Unlike agents (matched by
  // name), automations are matched by ID: the confirm endpoint stamped the
  // proposing ``tool_call_id`` onto the row, so the status endpoint maps each
  // tool id → its created automation. Only seeds untracked/pending cards.
  useEffect(() => {
    if (!selectedSessionId || !automationCreateToolSig) return;
    const ids = automationCreateToolSig.split(",").filter(Boolean);
    if (ids.length === 0) return;

    let cancelled = false;
    void (async () => {
      try {
        const res = await automationsApi.proposalStatus(selectedSessionId, ids);
        if (cancelled) return;
        setAutomationProposalStates((prev) => {
          let changed = false;
          const next = { ...prev };
          for (const id of ids) {
            const cur = next[id];
            if (cur && cur.state !== "pending") continue;
            const hit = res.confirmed[id];
            if (hit) {
              next[id] = {
                state: "confirmed",
                automationId: hit.automation_id,
              };
              changed = true;
            }
          }
          return changed ? next : prev;
        });
      } catch {
        /* non-fatal — status endpoint can fail transiently */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedSessionId, automationCreateToolSig]);

  // Scan staging for every ``submit_skill`` tool_use we've seen, so the
  // card renders the actual file tree (not just the agent's
  // ``files_touched`` claim) and gates its save button on real file
  // presence. While the agent is still streaming, the staging dir
  // might be empty — we poll every 1.5s for up to ~30s and the card
  // shows "Waiting for AI". Once SKILL.md appears, the card flips
  // to "pending" with the file tree visible and save enabled.
  useEffect(() => {
    if (!selectedSessionId) return;
    // Find all submit_skill tool_use blocks in the current turn list.
    const submitTools: { id: string; slug: string }[] = [];
    for (const turn of turns) {
      for (const block of turn.blocks) {
        if (block.kind !== "tool") continue;
        const t = block.tool;
        const name = t.title || "";
        if (!isToolNamed(name, "submit_skill")) {
          continue;
        }
        let slug = "";
        if (t.input) {
          try {
            const parsed =
              typeof t.input === "string" ? JSON.parse(t.input) : t.input;
            slug = String(parsed?.slug || "");
          } catch {
            /* malformed input — skip; card will render with placeholder slug */
          }
        }
        if (slug) submitTools.push({ id: t.id, slug });
      }
    }
    if (submitTools.length === 0) return;

    let cancelled = false;
    const sid = selectedSessionId;

    const tick = async () => {
      if (cancelled) return;
      try {
        const res = await skillsApi.scanStaging(sid);
        const slugViewMap = new Map(res.slugs.map((s) => [s.slug, s]));
        setSubmissionStates((prev) => {
          let changed = false;
          const next: typeof prev = { ...prev };
          for (const { id, slug } of submitTools) {
            const view = slugViewMap.get(slug);
            const current = next[id];
            // Don't overwrite terminal states (confirmed / dismissed)
            // or in-flight states (confirming / dismissing) — those are
            // user-driven transitions that the scan must not stomp.
            if (
              current?.state === "confirmed" ||
              current?.state === "dismissed" ||
              current?.state === "confirming" ||
              current?.state === "dismissing"
            ) {
              continue;
            }
            const stagingPath = `${res.staging_path}/${slug}`;
            if (view && view.files.length > 0) {
              const stagedFiles = view.files.map((f) => ({
                path: f.path,
                type: f.type,
                size: f.size ?? null,
              }));
              const target: SubmissionEntry = {
                state: "pending",
                stagedFiles,
                stagingPath,
              };
              if (
                !current ||
                current.state !== "pending" ||
                current.stagedFiles?.length !== stagedFiles.length
              ) {
                next[id] = target;
                changed = true;
              }
            } else {
              const target: SubmissionEntry = {
                state: "awaiting_files",
                stagedFiles: [],
                stagingPath,
              };
              if (current?.state !== "awaiting_files") {
                next[id] = target;
                changed = true;
              }
            }
          }
          return changed ? next : prev;
        });
      } catch {
        // Non-fatal — scan endpoint can 404 transiently if the staging
        // dir hasn't been initialised yet. Next tick will retry.
      }
    };

    void tick();
    // Poll faster while a turn is active (agent actively writing), slower
    // once the turn is done. Derived ``isBusy``, not the raw pending flag —
    // the flag only bridges the click → turn-start window now.
    const intervalMs = isBusy ? 1500 : 5000;
    const interval = window.setInterval(() => void tick(), intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [selectedSessionId, turns, isBusy]);

  // Mark an AskUserQuestion card as *foldable* so it collapses away with the
  // process trail once the turn ends — but ONLY after it's been answered. An
  // unanswered, still-pending question stays pinned/visible (the run parks and
  // ``sending`` flips to false while awaiting the answer, which would otherwise
  // auto-fold and hide the card the user needs to act on). Other overridden
  // cards (proposals, skill submission, workflow/task) are never foldable.
  const isToolCardFoldable = useCallback(
    (tool: { id: string; title?: string }): boolean => {
      const name = tool.title ?? "";
      const isAsk = isToolNamed(name, "AskUserQuestion");
      if (!isAsk) return false;
      return Boolean(
        askUserQuestionAnswersByToolId[tool.id] ??
        askUserQuestionLocalAnswers[tool.id],
      );
    },
    [askUserQuestionAnswersByToolId, askUserQuestionLocalAnswers],
  );

  const renderToolCall = useCallback(
    (tool: {
      id: string;
      title: string;
      input?: string;
      output?: string;
      status?: string;
    }) => {
      const name = tool.title || "";

      // generate_ui — generative UI. The MCP tool returns OpenUI Lang as
      // ``tool.output`` (growing token-by-token while running, as the host
      // forwards ephemeral text_deltas as tool_output_delta). Render it with
      // OpenUI's <Renderer> via GenerativeUICard, including while running so the
      // UI paints progressively; only error falls through (return null) to the
      // generic ToolCallCard so the failure text stays visible.
      if (isToolNamed(name, "generate_ui")) {
        if (tool.status === "error") return null;
        return (
          <GenerativeUICard
            openui={tool.output}
            status={tool.status === "running" ? "running" : "success"}
          />
        );
      }

      // Claude dynamic-workflow launch → WorkflowProgressCard. The kernel
      // streams ``session.workflow_progress`` snapshots keyed by this tool's
      // tool_use_id while the background runtime executes; we render the live
      // overview (status + agents-done/total + per-agent list) in place of the
      // opaque generic tool card. When no snapshot exists yet (history replay /
      // reconnect — the progress is live-only and never persisted), fall through
      // to the generic ToolCallCard so the launch still shows.
      if (name === "Workflow") {
        const wfState = workflowStates.get(tool.id);
        if (wfState) {
          return (
            <WorkflowProgressCard
              state={wfState}
              fallbackTitle={name}
              onOpenStateFile={revealInFinder}
            />
          );
        }
      }

      // ADR-021: automation tool result → AutomationToolCard. The MCP
      // server returns a structured JSON blob as ``tool.output``; we
      // parse it and hand off to the card. If the output is missing
      // (still running) or unparseable, we fall through to the generic
      // tool renderer. ``isToolNamed`` covers every runtime's MCP
      // namespacing (bare / Claude ``mcp__server__tool`` / codex
      // ``server/tool``).
      const isAutomation = isToolNamed(name, "automation");
      if (isAutomation) {
        const result = parseAutomationToolOutput(tool.output);
        const openInAutomation = (automationId: string) => {
          // The automation page is at ``/automations`` and reads
          // ``?automation=<id>`` for direct linking. Soft navigation keeps the
          // conversation mounted in the project sidebar.
          navigate(
            `/automations?automation=${encodeURIComponent(automationId)}`,
          );
        };

        // ``create`` PROPOSES — render a propose→confirm card (mirrors
        // propose_agent). Every other action keeps the read-only tool card.
        // We render primarily from the INPUT (always clean) and enrich from the
        // OUTPUT proposal when it's parseable — the Valuz/DeepAgents runtime
        // wraps the output so ``result`` is null there, but the card must still
        // render and be confirmable.
        const inputSpec = parseAutomationCreateInput(tool.input);
        const proposal = result?.proposal ?? null;
        const isCreate = result?.action === "create" || inputSpec != null;
        if (isCreate) {
          // The create tool rejected the proposal (bad cron / task-in-chat).
          const validationError = result && !result.ok ? result.message : null;
          // Nothing to show yet (no parsed input, no proposal, no error) —
          // generic renderer until something lands.
          if (!inputSpec && !proposal && !validationError) return null;
          const cardName = proposal?.name ?? inputSpec?.name ?? "";
          const cardPrompt =
            proposal?.prompt_template ?? inputSpec?.prompt_template;
          const confirmTrigger =
            proposal?.trigger ?? inputSpec?.trigger ?? null;
          const cardTriggerHuman =
            proposal?.trigger_human_readable ??
            automationTriggerSummary(confirmTrigger, t);
          const cardActionKind =
            proposal?.action_kind ?? inputSpec?.action_kind ?? "chat";
          const cardWorktree =
            proposal?.worktree ?? inputSpec?.worktree ?? false;
          const cardAgentName =
            proposal?.agent_name ?? inputSpec?.agent_slug ?? null;
          const entry = automationProposalStates[tool.id] || {
            state: "pending" as const,
          };
          return (
            <AutomationProposalCard
              name={cardName}
              promptTemplate={cardPrompt}
              triggerHuman={cardTriggerHuman}
              agentName={cardAgentName}
              actionKind={cardActionKind}
              worktree={cardWorktree}
              state={entry.state}
              errorMessage={entry.errorMessage}
              validationError={validationError}
              onConfirm={() => {
                if (!confirmTrigger || !cardName) return;
                void handleConfirmAutomation(tool.id, {
                  name: cardName,
                  prompt_template: cardPrompt ?? "",
                  trigger: confirmTrigger,
                  agent_slug: proposal?.agent_slug ?? inputSpec?.agent_slug,
                  action_kind: cardActionKind,
                  worktree: cardWorktree,
                });
              }}
              onDismiss={() => handleDismissAutomation(tool.id)}
            />
          );
        }

        if (result) {
          return (
            <AutomationToolCard
              result={result}
              onOpenInAutomation={openInAutomation}
            />
          );
        }
        return null;
      }

      // VALUZ-CHATPLAN — the LATEST plan_task / modify_plan tool for a
      // given task renders the rich, SSE-subscribed LiveTaskCard. Every
      // other chatplan tool result (draft, earlier plan writes, commit,
      // abandon, inject) renders a compact polished pill. This lands the
      // "current state" surface at the most recent plan write so the
      // user sees subtask progress + execute/abandon controls without
      // scrolling.
      const richPlanTaskId = planAnchors.taskByRichTool.get(tool.id);
      if (richPlanTaskId) {
        return (
          <LiveTaskCard
            taskId={richPlanTaskId}
            callerSessionId={selectedSessionId ?? ""}
            onNavigate={navigate}
          />
        );
      }
      const chatplanPill = renderChatplanStatusPill(name, tool, t, navigate);
      if (chatplanPill) return chatplanPill;

      // v3 (M10 附录 E): create_task launcher result → a compact card with
      // the task title + a link into the task detail page. The handler
      // returns ``{task_id, title, status}`` but the kernel wraps tool output
      // as a content-block repr (``[{'type': 'text', 'text': '{...}'}]``), so
      // extract the fields by regex rather than JSON.parse-ing the whole blob.
      const isCreateTask = isToolNamed(name, "create_task");
      if (isCreateTask && tool.output) {
        const idMatch = tool.output.match(/"task_id"\s*:\s*"([^"]+)"/);
        const taskId = idMatch?.[1];
        if (taskId) {
          const titleMatch = tool.output.match(
            /"title"\s*:\s*"((?:[^"\\]|\\.)*)"/,
          );
          let taskTitle = "";
          if (titleMatch?.[1]) {
            try {
              taskTitle = JSON.parse(`"${titleMatch[1]}"`);
            } catch {
              taskTitle = titleMatch[1];
            }
          }
          return (
            <div className="flex items-center gap-3 rounded-lg border border-surface-border bg-surface-soft px-3 py-2.5 text-sm">
              <Sparkles className="h-4 w-4 shrink-0 text-brand" />
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="truncate font-medium text-ink-heading">
                  {t("conversation.taskCreated" as Parameters<typeof t>[0])}
                </span>
                {taskTitle && (
                  <span className="truncate text-xs text-ink-body">
                    {taskTitle}
                  </span>
                )}
              </div>
              <button
                type="button"
                className="shrink-0 rounded-md border border-surface-border px-2 py-1 text-xs text-ink-body transition-colors hover:bg-surface-muted hover:text-ink-heading"
                onClick={() => navigate(`/tasks/${encodeURIComponent(taskId)}`)}
              >
                {t("conversation.openTask" as Parameters<typeof t>[0])}
              </button>
            </div>
          );
        }
      }

      // AskUserQuestion tool. Two render modes:
      //   - Pre-answer: interactive ``AskUserQuestionCard`` with the
      //     option chooser.
      //   - Post-answer: compact ``UserAnswerSummaryCard`` showing
      //     each question → answer pair. The interactive card is
      //     dropped entirely so the turn transcript stays clean.
      //
      // Answers source priority: (1) kernel-confirmed
      // ``askUserQuestionAnswersByToolId`` (authoritative, populated
      // from ``session.action_resolved`` SSE — works for live + replay
      // uniformly via the pending_id bridge). (2)
      // ``askUserQuestionLocalAnswers`` (optimistic, populated on
      // submit click). The local mirror keeps the card swap latency
      // at zero — the user never sees the read-only fill-content card
      // between submit and the kernel ack.
      const isAskUserQuestion = isToolNamed(name, "AskUserQuestion");
      if (isAskUserQuestion) {
        const parsed = parseAskUserQuestionInput(tool.input);
        if (parsed && parsed.questions.length > 0) {
          const answers =
            askUserQuestionAnswersByToolId[tool.id] ??
            askUserQuestionLocalAnswers[tool.id];
          if (answers) {
            return (
              <UserAnswerSummaryCard
                questions={parsed.questions}
                answers={answers}
              />
            );
          }
          return (
            <AskUserQuestionCard
              questions={parsed.questions}
              onSubmit={(submitted) =>
                askUserQuestionSubmitRef.current(tool.id, submitted)
              }
            />
          );
        }
        return null;
      }

      // ``propose_agent`` — natural-language agent creation. Renders a card
      // letting the user create + deploy the proposed agent. Tool name comes
      // through plain or MCP-bridged (``mcp__harness__propose_agent``).
      const isProposeAgent = isToolNamed(name, "propose_agent");
      if (isProposeAgent) {
        let spec: {
          name?: string;
          instructions?: string;
          description?: string;
          runtime?: string;
          model?: string;
          effort?: string;
          skills?: string[];
          connectors?: string[];
        } = {};
        if (tool.input) {
          try {
            spec =
              typeof tool.input === "string"
                ? JSON.parse(tool.input)
                : tool.input;
          } catch {
            // Partial/malformed args (still streaming) — render with blanks;
            // the confirm button stays disabled until a name is present.
          }
        }
        const entry = proposalStates[tool.id] || { state: "pending" as const };
        const confirmSpec = {
          name: spec.name || "",
          instructions: spec.instructions || "",
          description: spec.description,
          runtime: spec.runtime,
          model: spec.model,
          skills: Array.isArray(spec.skills) ? spec.skills : [],
          connectors: Array.isArray(spec.connectors) ? spec.connectors : [],
        };
        return (
          <AgentProposalCard
            name={confirmSpec.name}
            description={spec.description}
            instructions={confirmSpec.instructions}
            runtime={spec.runtime || "claude_agent"}
            model={spec.model || "claude-sonnet-4-6"}
            skills={confirmSpec.skills}
            connectors={confirmSpec.connectors}
            state={entry.state}
            errorMessage={entry.errorMessage}
            deployedProjectLabel={entry.deployedProjectLabel}
            onConfirm={() => void handleConfirmProposal(tool.id, confirmSpec)}
            onDismiss={() => handleDismissProposal(tool.id)}
          />
        );
      }

      const isSubmit = isToolNamed(name, "submit_skill");
      if (!isSubmit) return null;
      let parsed: {
        slug?: string;
        summary?: string;
        change_kind?: "create" | "update";
        files_touched?: string[];
      } = {};
      if (tool.input) {
        try {
          parsed =
            typeof tool.input === "string"
              ? JSON.parse(tool.input)
              : tool.input;
        } catch {
          // Malformed args — fall through to defaults below; the user
          // will still see the slug/summary fields blank.
        }
      }
      const slug = parsed.slug || "(unknown-slug)";
      const summary = parsed.summary;
      const changeKind: "create" | "update" =
        parsed.change_kind === "update" ? "update" : "create";
      const filesTouched = Array.isArray(parsed.files_touched)
        ? parsed.files_touched
        : [];
      // Initial state on first render is "awaiting_files" — the scan
      // effect above flips us to "pending" once SKILL.md actually
      // exists in the staging dir. Pre-existing state (after user
      // interactions) takes precedence.
      const entry = submissionStates[tool.id] || {
        state: "awaiting_files" as const,
      };
      return (
        <SkillSubmissionCard
          slug={slug}
          summary={summary}
          changeKind={changeKind}
          filesTouched={filesTouched}
          state={entry.state}
          errorMessage={entry.errorMessage}
          boundToProjectLabel={entry.boundToProjectLabel}
          stagedFiles={entry.stagedFiles}
          stagingPath={entry.stagingPath}
          onConfirm={() =>
            void handleConfirmSubmission(
              tool.id,
              slug,
              summary,
              changeKind,
              filesTouched,
            )
          }
          onDismiss={() => void handleDismissSubmission(tool.id, slug)}
        />
      );
    },
    [
      submissionStates,
      handleConfirmSubmission,
      handleDismissSubmission,
      proposalStates,
      handleConfirmProposal,
      handleDismissProposal,
      automationProposalStates,
      handleConfirmAutomation,
      handleDismissAutomation,
      askUserQuestionAnswersByToolId,
      askUserQuestionLocalAnswers,
      askUserQuestionSubmitRef,
      planAnchors,
      workflowStates,
      revealInFinder,
      selectedSessionId,
      navigate,
      t,
    ],
  );

  const firstUserText = turns[0]?.userText;
  const headerTitle =
    selectedSession?.name ||
    firstUserText?.slice(0, 40) ||
    (isNewSession
      ? null
      : t("conversation.newChat" as Parameters<typeof t>[0]));

  // Existing sessions follow their observed origin. New project conversations
  // follow the selected project's origin; new temp chats follow the explicit
  // location chip (or the registered default). The catalog adapter owns
  // target resolution and routing.
  // The route id is authoritative during navigation. ``selectedSessionId``
  // intentionally lags until session detail resolves, so preferring it here
  // would briefly query the previous conversation's execution target.
  const providerSessionId = id !== NEW_SESSION_ID ? id : null;
  const sessionExecOrigin = useEntityOrigin(providerSessionId, "session");
  const selectedProviderProject = projects.find(
    (project) => project.id === selectedProjectId,
  );
  const selectedProjectOrigin = selectedProviderProject
    ? (selectedProviderProject.exec_origin ?? "local")
    : undefined;
  const providerTargetId =
    id !== NEW_SESSION_ID
      ? sessionExecOrigin
      : (selectedProjectOrigin ?? execTargetId);
  const providerTarget =
    executionTargets.find((target) => target.id === providerTargetId) ??
    getDefaultExecutionTarget();
  const providerChannelState =
    useComposerProviderChannelState(providerTargetId);
  const providers = providerChannelState.providers;
  const {
    agents: myAgents,
    loaded: myAgentsLoaded,
    failed: myAgentsFailed,
    settling: myAgentsSettling,
    refresh: refreshAgents,
  } = useComposerAgentLibrary(
    providerTargetId,
    `${agentParam ?? ""}:${agentLibraryRevision}`,
  );

  const composerProviders = useComposerProviders(
    providers,
    selectedRuntimeId ?? undefined,
  );

  // Adapter: shrink ``RuntimeListItem`` from @valuz/core into the
  // narrower ``RuntimeSelectorItem`` shape @valuz/ui consumes — keeps
  // the UI package free of cross-package runtime imports.
  const composerRuntimes = useMemo(
    () =>
      runtimeList.map((rt) => ({
        id: rt.id,
        displayName: rt.display_name,
        available: rt.available,
        unavailableReason: rt.unavailable_reason,
      })),
    [runtimeList],
  );

  // 09-assistant: the 📁 chip's dropdown options — every project project.
  // ``ProjectListItem`` carries no member count, so the count is left
  // undefined for now (chip renders fine without it).
  // 09-assistant: whether the conversation currently targets 临时对话
  // (chat-default / non-project). The page stores the ``"chat-default"``
  // sentinel for 临时, so derive temp-ness from the resolved project kind
  // rather than a literal null.
  const isTempConversation = activeProject?.kind !== "project";

  // Settled, trusted and still empty — see useComposerAgentLibrary for why the
  // first empty answer is not enough to say this.
  const rosterEmpty =
    isTempConversation &&
    myAgentsLoaded &&
    !myAgentsFailed &&
    !myAgentsSettling &&
    myAgents.length === 0;
  const agentPending = managedRuntimeSetup && rosterEmpty;
  const setupPending = channelsPending || agentPending;

  // The attached strip under the composer owns the 📁 project choice for a
  // NEW conversation (replacing the composer's old toolbar chip) and keeps
  // showing the bound context on existing ones. All editions render it; the
  // location chip inside it only appears on multi-target builds.
  const execBarLocked = !(selectedSession == null && isNewSession);
  const execBarProjects = useMemo(
    () =>
      projects
        .filter((w) => w.kind === "project")
        .map((w) => ({ id: w.id, name: w.name, execOrigin: w.exec_origin })),
    [projects],
  );

  // Agent options for the composer's 🤖 chip. Candidates depend on the 📁
  // chip: 临时对话 → the "我的" library (``myAgents``); a project → its
  // 派驻 member roster (``projectAgents``). Runtime ids are mapped to their
  // display names so the dropdown reads "Claude Agent · mimo-v2.5-pro".
  const composerAgents = useMemo<ComposerAgentItem[]>(() => {
    if (isTempConversation) {
      // Pin the onboarding-seeded Valuz 小助手 (``valuz-helper``) to the top of
      // the dropdown; keep the rest of the library in its existing order.
      const ordered = [
        ...myAgents.filter((a) => a.slug === "valuz-helper"),
        ...myAgents.filter((a) => a.slug !== "valuz-helper"),
      ];
      return ordered.map((a) => ({
        slug: a.slug,
        name: a.name,
        runtimeLabel:
          runtimeList.find((r) => r.id === a.runtime)?.display_name ??
          a.runtime,
        modelLabel: modelLabel(a.model),
      }));
    }
    return projectAgents.map((m) => ({
      slug: m.member.agent_slug,
      name: m.agent?.name ?? m.member.agent_slug,
      runtimeLabel:
        runtimeList.find((r) => r.id === m.agent?.runtime_provider)
          ?.display_name ??
        m.agent?.runtime_provider ??
        "",
      modelLabel: modelLabel(m.agent?.model ?? ""),
    }));
  }, [isTempConversation, myAgents, projectAgents, runtimeList]);

  // The brain (runtime / model / provider / effort) of the currently bound
  // agent. It seeds the override controls' defaults; an untouched override
  // therefore equals the agent's own config, which the backend treats as a
  // no-op (it only diverges from the agent when the user actually changes a
  // value). Temp conversations bind a library agent; projects bind a member.
  const selectedAgentBrain = useMemo<{
    runtime: RuntimeId | null;
    model: string;
    providerId: string | null;
    effort: "low" | "medium" | "high" | "xhigh" | "max" | null;
  } | null>(() => {
    if (!selectedAgentSlug) return null;
    if (isTempConversation) {
      const a = myAgents.find((x) => x.slug === selectedAgentSlug);
      return a
        ? {
            runtime: (a.runtime as RuntimeId) || null,
            model: a.model,
            providerId: a.provider_id,
            effort: a.effort,
          }
        : null;
    }
    const a = projectAgents.find(
      (m) => m.member.agent_slug === selectedAgentSlug,
    )?.agent;
    return a
      ? {
          runtime: (a.runtime_provider as RuntimeId) || null,
          model: a.model,
          providerId: a.provider_id,
          effort: a.effort,
        }
      : null;
  }, [selectedAgentSlug, isTempConversation, myAgents, projectAgents]);

  // Seed the override controls from the bound agent's brain for a NEW agent
  // conversation. Runs on first bind and whenever the agent changes (the
  // agent picker clears ``composerTouched``); a user override sets
  // ``composerTouched`` and is preserved until they switch agents. Existing
  // sessions are frozen (ADR-006), so this never runs for them.
  useEffect(() => {
    if (selectedSessionId) return;
    if (composerTouched) return;
    if (!selectedAgentBrain) return;
    if (selectedAgentBrain.runtime)
      setSelectedRuntimeId(selectedAgentBrain.runtime);
    setSelectedProviderId(selectedAgentBrain.providerId);
    if (selectedAgentBrain.model) setSelectedModelId(selectedAgentBrain.model);
    setSelectedEffort(selectedAgentBrain.effort);
  }, [selectedAgentBrain, composerTouched, selectedSessionId]);

  // Whether this conversation's model diverges from the bound agent's default
  // (i.e. the user actually overrode it). Drives the muted model hint in the
  // agent button — hidden until an override happens, so a default chat is clean.
  // Slug → display name, so the header chip shows the agent's full name
  // ("研究分析师") rather than the raw kernel slug.
  const agentNameBySlug = useMemo(
    () => new Map(composerAgents.map((a) => [a.slug, a.name])),
    [composerAgents],
  );

  // Auto-pick the first available (provider, model) for the current
  // runtime so ``selectedProviderId`` / ``selectedModelId`` are never
  // null when the user hits Send. Without this, the backend falls back
  // to the seed-time ``is_default=ch-anthropic`` provider — which has no
  // API key configured — and every "send with default" attempt 422s.
  // Skips the auto-pick if a session already exists (its model is
  // frozen at creation per ADR-006) or if the user has explicitly
  // chosen a (provider, model) that's still in the filtered list.
  useEffect(() => {
    if (selectedSession) return;
    if (defaultsLoading) return;
    if (composerProviders.length === 0) return;
    const stillValid =
      selectedProviderId &&
      selectedModelId &&
      composerProviders.some(
        (m) =>
          m.providerId === selectedProviderId && m.modelId === selectedModelId,
      );
    if (stillValid) return;
    const preferred =
      composerProviders.find((m) => m.isDefault) ?? composerProviders[0];
    setSelectedProviderId(preferred.providerId);
    setSelectedModelId(preferred.modelId);
  }, [
    composerProviders,
    selectedProviderId,
    selectedModelId,
    selectedSession,
    defaultsLoading,
  ]);

  // For project project "/" mention, only show enabled/bound skills
  // Resolve an agent's stored skill entries to ``/`` picker items via the loaded
  // catalogs (shared with ProjectDetailPage to avoid drift).
  const resolveSkillItems = useCallback(
    (entries: string[] | null | undefined) =>
      resolveAgentSkillItems(entries, [availableSkills, projectSkills]),
    [availableSkills, projectSkills],
  );

  // The bound skills of the currently selected member agent — the ``/`` picker
  // list for a PROJECT conversation. Project chats can't attach skills ad-hoc
  // (skills are the agent's equipment), so ``/`` surfaces exactly that agent's
  // skills.
  const selectedAgentSkillItems = useMemo(() => {
    if (!effectiveAgentSlug) return [];
    const agent = projectAgents.find(
      (m) => m.member.agent_slug === effectiveAgentSlug,
    )?.agent;
    return resolveSkillItems(agent?.skills);
  }, [effectiveAgentSlug, projectAgents, resolveSkillItems]);

  // The ``/`` picker list for a NEW (non-project) conversation: the union of
  // the library-ENABLED skills and the selected agent's bound skills, deduped
  // by slug. A new conversation may have no agent (library skills only); the
  // global library switch (``library_enabled``) is what the Skills page toggles.
  const composerMentionSkills = useMemo<AgentSkillItem[]>(() => {
    const libraryItems: AgentSkillItem[] = availableSkills
      .filter((s) => s.library_enabled !== false)
      .map((s) => ({
        id: s.id,
        name: s.name,
        slug: s.slug,
        description: s.description,
      }));
    const agentEntries = isTempConversation
      ? myAgents.find((a) => a.slug === effectiveAgentSlug)?.skills
      : undefined;
    const seen = new Set(
      libraryItems.map((i) => i.slug).filter((s): s is string => !!s),
    );
    const merged: AgentSkillItem[] = [...libraryItems];
    for (const it of resolveSkillItems(agentEntries)) {
      if (it.slug && seen.has(it.slug)) continue;
      merged.push(it);
      if (it.slug) seen.add(it.slug);
    }
    return merged;
  }, [
    availableSkills,
    isTempConversation,
    myAgents,
    effectiveAgentSlug,
    resolveSkillItems,
  ]);

  // Slug → display-name map for rendering inline ``/skill-slug`` chips
  // in past user messages. We blend availableSkills (the global picker
  // catalog) and projectSkills (project-bound skills) so chips render
  // even for project-only skills that wouldn't appear in the global
  // catalog.
  const skillsBySlug = useMemo(() => {
    const map: Record<string, { name: string }> = {};
    for (const s of availableSkills) {
      if (s.slug) map[s.slug] = { name: s.name };
    }
    for (const s of projectSkills) {
      if (s.slug) map[s.slug] = { name: s.name };
    }
    return map;
  }, [availableSkills, projectSkills]);

  // Initial load fetches the latest ``TURN_PAGE_SIZE`` turns through the
  // turn-aligned window endpoint instead of pulling every event. Earlier
  // turns are loaded on demand by ``loadOlderTurns`` when the user
  // scrolls toward the top. The old "fetch all events with no cursor"
  // flow silently truncated long sessions because the backend caps the
  // legacy linear endpoint at 500 rows ASC.
  const TURN_PAGE_SIZE = 20;

  // Settles when the CURRENT history-window load has finished (success or
  // failure). ``refreshEvents`` resets the seq cursors synchronously and only
  // hydrates them (and ``events``) when its fetch lands — a stream opened in
  // that gap would capture ``afterSeq = 0`` and make the SSE replay the
  // session's whole history over the wire (pure waste; the uid dedup would
  // absorb it, but the transfer + churn are avoidable).
  // ``subscribeToSession`` awaits this before opening the stream.
  const historyHydrationRef = useRef<Promise<void>>(Promise.resolve());
  // A same-session bootstrap may skip REST history only after that session's
  // window completed successfully. Keeping selection and hydration separate
  // makes a failed/blank load retryable without a hard refresh.
  const historyHydratedSessionIdRef = useRef<string | null>(null);

  const refreshEventsInner = useCallback(async (sessionId: string | null) => {
    if (sessionId === null || selectedSessionIdRef.current === sessionId) {
      historyHydratedSessionIdRef.current = null;
    }
    // Switching sessions invalidates any optimistic pending message —
    // it belongs to whatever session was active before, not this one.
    setPendingUserMessage(null);
    // CRITICAL: clear ``events`` synchronously BEFORE awaiting the
    // network fetch. The URL-change handler updates ``selectedSessionId``
    // and then calls this function, but ``selectedSessionId`` and
    // ``events`` would otherwise commit in different render cycles —
    // for the few hundred ms that the new ``listEventsWindow`` round-
    // trip takes, the page renders the NEW session's id (so the title,
    // sidebar highlight, right panel all flip) against the OLD
    // session's events (so the conversation body still shows the
    // previous chat). The user perceives this as the previous turns
    // "stacking" under the new session header.
    setEvents([]);
    setTodos(null);
    // Workflow progress is live-only and per-session — drop the previous
    // session's snapshots so a Workflow tool card can't leak across a switch.
    setWorkflowStates(new Map());
    // The clarifying-pending ref is keyed to whatever session we're
    // leaving. Reset it so a stale pending_id from the previous session
    // can't get POSTed against the new one — and so the post-fetch walk
    // below has a clean slate to repopulate from history.
    currentClarifyingPendingRef.current = null;
    // Same rationale for the approval tray: clear it so a card parked in the
    // session we're leaving can neither linger against — nor POST its
    // pending_id against — the session we're switching to. The post-fetch
    // walk below rebuilds it from the new session's own history.
    setPendingApprovals([]);
    historyCursorRef.current = 0;
    seenEventUidsRef.current.clear();
    minSeqRef.current = Number.POSITIVE_INFINITY;
    hasMoreOlderRef.current = false;
    setHasMoreOlder(false);
    streamReconnectAttemptsRef.current = 0;
    if (!sessionId) {
      return;
    }
    try {
      const response = await sessionsApi.listEventsWindow(sessionId, {
        turnLimit: TURN_PAGE_SIZE,
      });
      // Race guard: another session switch may have completed while
      // we were awaiting this fetch. Discard stale results so we
      // don't briefly render the previous session's events under the
      // new session's header. Without this, two fast-clicked switches
      // can resolve out of order and the slower fetch would overwrite
      // the faster one with mismatched events.
      if (selectedSessionIdRef.current !== sessionId) return;
      // Merge, don't clobber: a resume subscription may already be streaming
      // this session (its live deltas + replayed rows landed in ``events``
      // between our synchronous clear above and this resolve). Replacing with
      // the window snapshot wiped that streamed content — the refresh-mid-turn
      // blank/inconsistent transcript. ``mergeEventWindow`` only inserts the
      // persisted rows we don't have yet, in order.
      setEvents((prev) => mergeEventWindow(prev, response.items));
      if (response.items.length > 0) {
        // ``listEventsWindow`` items are HISTORY-space, so seeding the
        // history cursor from the last one is safe. Forward-only: a
        // concurrent REST reconcile/poll may have advanced the cursor past
        // this snapshot — never rewind it (a rewound cursor makes the next
        // gap-fill refetch rows we already rendered).
        historyCursorRef.current = Math.max(
          historyCursorRef.current,
          response.items[response.items.length - 1].seq,
        );
        minSeqRef.current = Math.min(minSeqRef.current, response.items[0].seq);
      }
      hasMoreOlderRef.current = response.has_more;
      setHasMoreOlder(response.has_more);
      // Walk the historical events for the most recent
      // ``session.todos.update`` snapshot so a re-entry after refresh
      // restores the panel's state without waiting for the next turn.
      // Same walk also rebuilds the clarifying-pending ref so the
      // AskUserQuestionCard's submit handler can POST /actions with the
      // right pending_id when the user opens a session that's *already*
      // parked on an unanswered question — without this, the SSE-only
      // ref stays null on cold open and the 回答 click silently fails
      // with a "common.error" toast (the handler early-returns at
      // ``pendingId`` is null).
      let lastTodos: TodoItem[] | null = null;
      let unresolvedClarifyingPending: string | null = null;
      const resolvedPendingIds = new Set<string>();
      // First pass — collect the set of pending_ids that have already
      // been resolved (so we don't carry a "live" pending forward when
      // the kernel has already moved past it).
      for (const ev of response.items) {
        if (ev.event.event_type === SESSION_ACTION_RESOLVED_EVENT) {
          const ar = parseActionResolved(ev);
          if (ar) resolvedPendingIds.add(ar.pending_id);
        }
      }
      // Rebuild the approval tray from history too (the sibling of the
      // clarifying-ref rebuild). ``pendingApprovals`` was otherwise
      // live-SSE-only: a card parked in a session that is later reopened —
      // or switched back to — would vanish with no way to approve it. Since
      // an ``exit_plan_mode`` pending always parks and waits on slow human
      // review, it is the one most likely to outlive its session context.
      const rebuiltApprovals: PendingApprovalEntry[] = [];
      for (const ev of response.items) {
        const parsedTodos = parseTodosUpdate(ev);
        if (parsedTodos) lastTodos = parsedTodos;
        if (ev.event.event_type === SESSION_REQUIRES_ACTION_EVENT) {
          const ra = parseRequiresAction(ev);
          if (!ra || resolvedPendingIds.has(ra.pending_id)) continue;
          if (ra.subject === "clarifying_questions") {
            unresolvedClarifyingPending = ra.pending_id;
          } else {
            rebuiltApprovals.push({
              pendingId: ra.pending_id,
              subject: ra.subject as ApprovalCardSubject,
              payload: ra.payload,
              availableDecisions: ra.available_decisions,
              sessionRulePreviewDisplay:
                ra.session_rule_preview?.display ?? null,
              originalInput: ra.original_input,
              receivedAt: ev.timestamp,
            });
          }
        }
      }
      // Carry-forward, matching the other two setTodos sites (detail hydrate
      // + live SSE): the persisted ``detail.todos`` snapshot is the
      // authoritative source and history/live frames only ever refine it.
      // The window covers the last TURN_PAGE_SIZE turns — on a long session
      // it may contain no parseable ``session.todos.update`` frame at all,
      // and an unconditional write here clobbered the detail hydrate with
      // null on every cold re-open ("No todos yet" while sessions.todos is
      // intact). An agent that *clears* its todos emits ``[]`` (truthy),
      // which still lands.
      if (lastTodos) setTodos(lastTodos);
      currentClarifyingPendingRef.current = unresolvedClarifyingPending;
      setPendingApprovals(rebuiltApprovals);
      historyHydratedSessionIdRef.current = sessionId;
    } catch {
      if (selectedSessionIdRef.current !== sessionId) return;
      setEvents([]);
      historyCursorRef.current = 0;
      seenEventUidsRef.current.clear();
      minSeqRef.current = Number.POSITIVE_INFINITY;
      hasMoreOlderRef.current = false;
      setHasMoreOlder(false);
      setTodos(null);
      // A failed history load used to be swallowed here, leaving an
      // existing session rendering a permanently blank transcript with
      // no error and no retry path (the empty state is welcome-gated to
      // ``/conversation/new`` only). Surface it so the user sees an
      // error card instead of a white page.
      setError(_t("conversation.historyLoadFailed" as I18nKey));
    }
  }, []);

  // Public wrapper: registers the in-flight load as the hydration gate
  // SYNCHRONOUSLY (so a subscription started in the same tick already waits on
  // it), then runs the load. Same signature and identity stability as before.
  const refreshEvents = useCallback(
    async (sessionId: string | null) => {
      const load = refreshEventsInner(sessionId);
      historyHydrationRef.current = load.catch(() => {});
      await load;
    },
    [refreshEventsInner],
  );

  // Upward pagination: triggered by the top sentinel's IntersectionObserver
  // (and safe to call manually). Captures the scroll anchor BEFORE the
  // state update so the layout effect below can restore the user's
  // scroll position once React commits the prepend.
  //
  // Skipped when the scroller is far from the top: the observer can fire
  // during transient layout shifts (initial render before the
  // auto-scroll-to-bottom effect lands; ResizeObserver-driven
  // re-measurement) where the sentinel is briefly visible but the user
  // hasn't actually scrolled there. Without this guard, those firings
  // chain into a cascade that pre-loads the entire session in one
  // microtask and defeats the whole point of pagination.
  const loadOlderTurns = useCallback(async () => {
    const sessionId = selectedSessionIdRef.current;
    if (!sessionId) return;
    if (loadingOlderRef.current) return;
    if (!hasMoreOlderRef.current) return;
    if (!Number.isFinite(minSeqRef.current) || minSeqRef.current <= 0) return;

    if (!userScrolledRef.current) return;
    const el = scrollContainerRef.current;
    if (el && el.scrollTop > 200) return;

    loadingOlderRef.current = true;
    setLoadingOlder(true);
    const oldScrollHeight = el?.scrollHeight ?? 0;
    const oldScrollTop = el?.scrollTop ?? 0;

    try {
      const response = await sessionsApi.listEventsWindow(sessionId, {
        beforeSeq: minSeqRef.current,
        turnLimit: TURN_PAGE_SIZE,
      });
      // Staleness guard: a session switch may have landed while this page
      // was in flight — prepending the OLD session's turns into the NEW
      // session's transcript (and poisoning ``minSeqRef``, which the switch
      // just reset) mixes histories across sessions.
      if (selectedSessionIdRef.current !== sessionId) return;
      if (response.items.length > 0) {
        // Defensive dedup: SSE shouldn't backfill historical events but
        // a slow turn could in theory race with this fetch.
        setEvents((prev) => {
          // uid-first dedup (seq fallback restricted to uid-less rows —
          // history and live seqs are independent spaces).
          const seenUids = new Set<string>();
          const seenLegacySeqs = new Set<number>();
          for (const p of prev) {
            if (p.event_uid) seenUids.add(p.event_uid);
            else if (p.seq > 0) seenLegacySeqs.add(p.seq);
          }
          const fresh = response.items.filter((e) =>
            e.event_uid
              ? !seenUids.has(e.event_uid)
              : !seenLegacySeqs.has(e.seq),
          );
          if (fresh.length === 0) return prev;
          return [...fresh, ...prev];
        });
        minSeqRef.current = response.items[0].seq;
        pendingScrollAnchorRef.current = { oldScrollHeight, oldScrollTop };
      }
      hasMoreOlderRef.current = response.has_more;
      setHasMoreOlder(response.has_more);
    } catch {
      // Non-fatal — the user can scroll away and back to retry the
      // sentinel observer; surfacing a toast for a background pager
      // would be noisier than helpful.
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, []);

  /**
   * Re-fetch the *one* session this page is currently rendering.
   *
   * Replaces the previous ``refreshSessions(projectId, preferredId)``
   * which did ``GET /v1/sessions?project_id=…`` and ``find()``-d the
   * row we cared about. That pattern had two problems:
   *
   *   1. It needed a project id to call the list endpoint, but the
   *      ``selectedProjectId`` captured in callback closures could be
   *      stale (the ``"chat-default"`` sentinel before
   *      ``ensureSession``'s state update settled). The post-turn
   *      refresh would then list against a non-existent project id,
   *      get back an empty list, and silently null-out
   *      ``selectedSessionId`` — the symptom users saw as "refresh"
   *      ".
   *   2. The page only ever renders one session at a time. Listing a
   *      whole project's sessions just to ``find()`` one was
   *      wasteful and brittle.
   *
   * The clean replacement uses ``GET /v1/sessions/{id}`` directly. No
   * project id needed; one round-trip; the result feeds both the
   * one-row ``sessions[]`` (the optimistic-merge code paths still
   * mutate this) and ``selectedSessionId``.
   */
  const refreshActiveSession = useCallback(
    async (sessionId: string | null) => {
      if (!sessionId) {
        setSessions([]);
        setSelectedSessionId(null);
        return;
      }
      try {
        const detail = await sessionsApi.get(sessionId);
        const item = sessionDetailToListItem(detail);
        setSessions([item]);
        const previousId = selectedSessionIdRef.current;
        setSelectedSessionId(detail.id);
        // Same session, no events refetch — SSE already accumulated
        // them locally and ``list_events_after`` caps at 500 rows ASC,
        // so a refetch on a long session silently drops the most
        // recent turn (which is exactly what we just streamed).
        if (detail.id !== previousId) {
          await refreshEvents(detail.id);
        }
      } catch {
        // Session not found / 4xx — clear selection so the UI doesn't
        // pretend we're still on a deleted row.
        setSessions([]);
        setSelectedSessionId(null);
      }
    },
    [refreshEvents],
  );

  const bootstrap = useCallback(
    async (isCurrent: () => boolean) => {
      const routeState = location.state as {
        promotedFromNew?: boolean;
        promotedSessionId?: string;
      } | null;
      // The promotion fast-path (skip the loading flash + skip the history
      // refetch) is only valid while a send is genuinely IN FLIGHT — that live
      // subscription is what fills ``events`` for the freshly promoted session,
      // so the refetch is redundant and the loader would just flash. On a cold
      // page reload there is no in-flight send: ``history.state`` still carries
      // ``promotedFromNew`` (the hash router restores it across refreshes) and
      // the ``promotingSessionIdRef`` / ``consumedPromoteSessionIdsRef`` refs
      // reset with the page, so without this ``isSendInFlightRef`` guard bootstrap
      // would replay the promotion skip on every refresh — never calling
      // ``refreshEvents`` — and leave the conversation body blank.
      const isPromoteBootstrap =
        isSendInFlightRef.current &&
        id !== NEW_SESSION_ID &&
        (promotingSessionIdRef.current === id ||
          (routeState?.promotedFromNew === true &&
            routeState.promotedSessionId === id &&
            !consumedPromoteSessionIdsRef.current.has(id)));
      if (!isPromoteBootstrap) {
        setLoading(true);
      }
      setError(null);
      try {
        const wsResponse = await projectsApi.list();
        if (!isCurrent()) return;
        setProjects(wsResponse.projects);

        // Two URL shapes drive the page:
        //
        //   /conversation/new           — fresh draft, no session yet
        //   /conversation/{session_id}  — existing session, fetch detail
        //
        // For the fresh-draft case we set ``selectedProjectId`` to the
        // ``"chat-default"`` sentinel so skill autocomplete still loads
        // (the backend skills router treats it as the global chat-scope
        // key); ``refreshFileTree`` skips the sentinel (no project row /
        // workdir exists yet) and renders an empty tree. The real project
        // materializes when the user sends the first message and
        // ``ensureSession`` navigates us to ``/conversation/{real-id}``.
        if (id === NEW_SESSION_ID) {
          setSessionTriggerMode(null);
          setSessionAgentSlug(null);
          // 09-assistant D4: ``/conversation/new?project=<id>`` (e.g. a
          // project-page "+ 新对话" entry) pre-selects that project so the 📁
          // chip lands on it instead of 临时对话. The user can flip back to
          // 临时 with one click. Falls back to the ``"chat-default"`` sentinel
          // (临时) when the query is absent or doesn't match a project.
          const projectParam = searchParams.get("project");
          const presetProject =
            projectParam &&
            wsResponse.projects.some(
              (w) => w.id === projectParam && w.kind === "project",
            )
              ? projectParam
              : null;
          setSelectedProjectId(presetProject ?? "chat-default");
          // Preset project (e.g. home-page footer bar hand-off): same reveal
          // rule as an in-page pick.
          if (presetProject) panelSetCollapsed(false);
          setSessions([]);
          selectedSessionIdRef.current = null;
          setSelectedSessionId(null);
          // CRITICAL: also clear the conversation state (events / todos
          // / optimistic pending message) so navigating from a real
          // session ``/conversation/{X}`` → ``/conversation/new`` shows
          // an empty composer instead of leaving session X's turns on
          // screen. ``refreshEvents(null)`` is the canonical "switch
          // away from any session" path — it nulls every per-session
          // ref + state synchronously.
          await refreshEvents(null);
          return;
        }

        // Existing session path — one round-trip via the dedicated
        // ``GET /v1/sessions/{id}`` endpoint feeds every piece of state
        // the page needs: trigger_meta (skill-creator banner restore),
        // project_id (right panel cwd / file tree), the session row
        // itself (composer status + optimistic-merge target).
        try {
          const sessionDetail = await sessionsApi.get(id);
          if (!isCurrent()) return;
          const routePromotedSession =
            routeState?.promotedFromNew === true &&
            routeState.promotedSessionId === sessionDetail.id &&
            !consumedPromoteSessionIdsRef.current.has(sessionDetail.id);
          // Same guard as ``isPromoteBootstrap`` above: only treat this as a
          // promotion — and therefore SKIP ``refreshEvents`` — while a send is
          // in flight (the live subscription is already streaming this turn's
          // events in). On a reload the restored ``promotedFromNew`` state must
          // NOT suppress the history load, or the transcript stays empty.
          const isPromotedNewSession =
            isSendInFlightRef.current &&
            (promotingSessionIdRef.current === sessionDetail.id ||
              routePromotedSession);
          setSessionTriggerMode(sessionDetail.trigger_meta?.mode ?? null);
          setSessionAgentSlug(sessionDetail.agent_slug ?? null);
          setSelectedProjectId(sessionDetail.project_id);
          // Origin inheritance: a session's project lives on the same backend
          // as the session (managed remote projects are list-hidden, so this
          // is often the ONLY chance to learn their origin). Also self-heals
          // entries lost before project recording existed at creation time.
          {
            const sessionOrigin = getEntityOrigin(sessionDetail.id, "session");
            if (
              sessionOrigin &&
              sessionDetail.project_id &&
              !getEntityOrigin(sessionDetail.project_id)
            ) {
              recordEntityOrigin(sessionDetail.project_id, sessionOrigin);
            }
          }
          setSessions([sessionDetailToListItem(sessionDetail)]);
          selectedSessionIdRef.current = sessionDetail.id;
          setSelectedSessionId(sessionDetail.id);
          if (isPromotedNewSession) {
            promotingSessionIdRef.current = null;
            consumedPromoteSessionIdsRef.current.add(sessionDetail.id);
          }
          // Selection alone is not proof that history loaded. Skip only when
          // this exact session already hydrated successfully, or while the
          // promoted session's live stream is supplying the first turn.
          if (
            shouldRefreshConversationHistory({
              hydratedSessionId: historyHydratedSessionIdRef.current,
              sessionId: sessionDetail.id,
              promotedWithLiveStream: isPromotedNewSession,
            })
          ) {
            await refreshEvents(sessionDetail.id);
          }
        } catch {
          if (!isCurrent()) return;
          // 404 / network — render the page in "no session" state and
          // surface an error banner. Don't fall back to the sentinel
          // because that would silently move the user off the URL they
          // typed / clicked.
          setSessionTriggerMode(null);
          setSessionAgentSlug(null);
          setSelectedProjectId(null);
          setSessions([]);
          selectedSessionIdRef.current = null;
          setSelectedSessionId(null);
          setError("Session not found.");
        }
      } catch (cause) {
        if (!isCurrent()) return;
        setError(
          cause instanceof Error ? cause.message : "Failed to load project.",
        );
      } finally {
        if (isCurrent() && !isPromoteBootstrap) {
          setLoading(false);
        }
      }
    },
    [id, location.state, refreshEvents, searchParams],
  );

  useEffect(() => {
    const request = bootstrapGuardRef.current.start();
    // Invalidate stale history/subscription writes as soon as the route effect
    // starts, rather than waiting for the new session detail request to land.
    selectedSessionIdRef.current = id === NEW_SESSION_ID ? null : id;
    void bootstrap(request.isCurrent);
    return request.cancel;
    // ``location.key`` changes even when the user clicks the currently-selected
    // history link again. Re-run bootstrap in that case so a failed/blank
    // hydration can recover without requiring a hard refresh.
  }, [bootstrap, id, location.key]);

  // Load all skills for composer autocomplete
  useEffect(() => {
    if (!selectedProjectId) return;
    skillsApi
      .list(selectedProjectId)
      .then((catalog) => {
        setAvailableSkills(catalog.skills);
      })
      .catch(() => {
        setAvailableSkills([]);
      });
  }, [selectedProjectId]);

  // Load the project's configured member agents. Project conversations
  // pick one of these (instead of a raw model); the composer renders them
  // as an Agent selector. Default the picker to the first agent.
  useEffect(() => {
    if (!selectedProjectId || activeProject?.kind !== "project") {
      setProjectAgents([]);
      return;
    }
    let cancelled = false;
    agentsApi
      .listMembers(selectedProjectId)
      .then((res) => {
        if (cancelled) return;
        setProjectAgents(res.agents);
        setSelectedAgentSlug((prev) => {
          if (prev && res.agents.some((a) => a.member.agent_slug === prev)) {
            return prev;
          }
          return res.agents[0]?.member.agent_slug ?? null;
        });
      })
      .catch(() => {
        if (!cancelled) setProjectAgents([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedProjectId, activeProject?.kind]);

  // 09-assistant: when the 📁 chip sits on 临时对话 (non-project project)
  // and no session exists yet, default the 🤖 chip to the first "我的" agent.
  // Empty library → null; handleSend then nudges the user to pick/create
  // (10-new-conversation-guidance). The project case is handled by the
  // listMembers effect above (first member).
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (selectedSession) return; // existing session is frozen (ADR-006)
    if (activeProject?.kind === "project") return; // project path elsewhere
    if (!myAgentsLoaded) return; // do not clear the old selection mid-switch
    setSelectedAgentSlug((prev) => {
      // A freshly-created agent handed off via ?agent= (「去临时对话」) wins as
      // soon as it appears in the reloaded roster.
      if (agentParam && myAgents.some((a) => a.slug === agentParam))
        return agentParam;
      if (prev && myAgents.some((a) => a.slug === prev)) return prev;
      // Skill-creator still needs an agent (its create flow binds one), so keep
      // the old defaulting there: last-used → Valuz 小助手 → first library agent.
      if (isSkillCreatorMode) {
        const lastUsed = getLastTempAgent();
        if (lastUsed && myAgents.some((a) => a.slug === lastUsed))
          return lastUsed;
        if (myAgents.some((a) => a.slug === "valuz-helper"))
          return "valuz-helper";
        return myAgents[0]?.slug ?? null;
      }
      // A normal new conversation now defaults to NO agent — an agentless quick
      // chat on the global default model (the model-defaults effect seeds it).
      return null;
    });
    // Re-run only on the data that decides the default — existence of a
    // session (frozen), the project kind, the candidate roster, the explicit
    // ?agent= hand-off, and whether this is the skill-creator flow.
  }, [
    activeProject?.kind,
    myAgents,
    myAgentsLoaded,
    selectedSession,
    agentParam,
    isSkillCreatorMode,
  ]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Load bound skills for project project context panel
  useEffect(() => {
    if (!selectedProjectId || activeProject?.kind !== "project") return;
    skillsApi
      .projectCatalog(selectedProjectId)
      .then((catalog) => {
        setProjectSkills(catalog.skills);
      })
      .catch(() => {
        setProjectSkills([]);
      });
  }, [selectedProjectId, activeProject?.kind]);

  // Load project file tree for the right context panel's Files tab.
  // Loaded for both project AND chat projects — chat sessions write
  // generated artifacts into their managed cwd, and the user wants
  // those visible in the right rail too (under the "Generated files" label).
  // The ``"chat-default"`` sentinel is NOT a real project (no row, no cwd —
  // the backend materializes a fresh chat project only when the first
  // session is created), so fetching its tree would just 404.
  const refreshFileTree = useCallback(() => {
    if (!selectedProjectId || selectedProjectId === "chat-default") {
      setFileTree([]);
      return;
    }
    projectsApi
      .listFiles(selectedProjectId, {
        depth: 3,
        // Worktree sessions show their own checkout, not the shared project cwd.
        worktree: activeWorktree?.name ?? undefined,
      })
      .then((res) => setFileTree(toFileTree(res.files)))
      .catch(() => setFileTree([]));
  }, [selectedProjectId, activeWorktree]);

  useEffect(() => {
    refreshFileTree();
  }, [refreshFileTree]);

  // Auto-refresh on turn end: when the derived ``isBusy`` flips false, the
  // agent has just finished writing whatever artifacts it was going to. Pull
  // a fresh tree so the panel reflects new files without the user
  // having to switch projects or hit refresh manually. (``isBusy`` rather
  // than raw ``sending`` so a stuck flag can't suppress the refresh.)
  const prevBusyRef = useRef(isBusy);
  useEffect(() => {
    if (prevBusyRef.current && !isBusy) {
      refreshFileTree();
      // The agent may have called ``deliver_artifacts`` during the turn —
      // pull the fresh 生成文件 list alongside the file tree.
      void refreshArtifacts();
    }
    prevBusyRef.current = isBusy;
  }, [isBusy, refreshFileTree, refreshArtifacts]);

  // Loading server-side attachments on session change + polling parse status
  // is owned by ``useSessionAttachments`` above.

  const ensureSession = useCallback(
    async (navigateOnCreate = false) => {
      if (selectedSession) return selectedSession;
      const sessionProjectId = selectedProjectId ?? "chat-default";
      // For quick-chat (kind="chat"), send the ``"chat-default"`` sentinel so
      // the backend allocates a fresh, isolated chat project + cwd for this
      // session. Project conversations keep passing their real project id.
      const isChat =
        sessionProjectId === "chat-default" || activeProject?.kind === "chat";
      // 09-assistant §2.1/§2.2: every session binds to an agent — project
      // conversations to the chosen 派驻 member, 临时对话 to the picked "我的"
      // agent. There is no agentless path; the backend derives
      // runtime/model/provider/effort/skills/connectors from the agent, so the
      // model-picker fields below are ignored when it resolves the brain.
      // Skill-creator must bind an agent; a normal conversation may be agentless
      // (the create below sends ``agent_slug: undefined`` → backend chat path).
      if (isSkillCreatorMode && !selectedAgentSlug) {
        throw new Error("No agent selected.");
      }
      let created: Awaited<ReturnType<typeof sessionsApi.create>>;
      if (isSkillCreatorMode) {
        // Skill-creator draft: mint through the skills launcher so the
        // session carries trigger_meta + creation_context (the
        // ``submit_skill`` confirm flow reads them), bound to the agent
        // picked in the composer — same UX as 新对话.
        const start = await skillsApi.startCreate({
          context:
            skillKindParam === "project" && skillProjectParam
              ? { kind: "project", project_id: skillProjectParam }
              : { kind: skillKindParam === "chat" ? "chat" : "skills_library" },
          agent_slug: selectedAgentSlug,
          provider_id: selectedProviderId ?? undefined,
          model_id: selectedModelId ?? undefined,
        });
        created = await sessionsApi.get(start.session_id);
      } else {
        // Multi-target routing: a quick/temp chat runs on the target the
        // composer picker chose; a project conversation always follows its
        // project's observed origin. Single-target builds resolve both to
        // ``undefined`` → module-default base, unchanged behaviour.
        const chatTarget = isChat ? resolveExecTarget() : undefined;
        const projectOrigin = !isChat
          ? getEntityOrigin(sessionProjectId, "project")
          : undefined;
        const createBaseUrl = isChat
          ? chatTarget?.baseUrl
          : resolveApiBase({ projectId: sessionProjectId }, "") || undefined;
        // Connector picks still come from the local backend. The model list is
        // loaded from ``createBaseUrl`` above, so provider/model/runtime picks
        // are valid on the selected remote backend and must be preserved.
        const remoteCreate = chatTarget?.remote === true;
        created = await sessionsApi.create(
          {
            project_id: isChat ? "chat-default" : sessionProjectId,
            agent_slug: selectedAgentSlug ?? undefined,
            provider_id: selectedProviderId ?? undefined,
            model_id: selectedModelId ?? undefined,
            runtime_id:
              selectedProviderId && selectedModelId
                ? (selectedRuntimeId ?? undefined)
                : undefined,
            mcp_provider_slugs:
              !remoteCreate && selectedMcpSlugs.length > 0
                ? selectedMcpSlugs
                : undefined,
            permission_mode: selectedPermissionMode,
            effort: selectedEffort,
          },
          createBaseUrl ? { baseUrl: createBaseUrl } : undefined,
        );
        const originTag = isChat ? chatTarget?.id : projectOrigin;
        if (originTag) {
          recordEntityOrigin(created.id, originTag);
          // A remote quick-chat mints a MANAGED project server-side. That
          // project appears in no local list (temp projects are list-hidden),
          // so no fan-out observation ever records its origin — without this
          // line every project-context fetch (detail / files / skills) routes
          // to the module-default backend and 404s, blanking the 云端对话 page.
          if (created.project_id && created.project_id !== "chat-default") {
            recordEntityOrigin(created.project_id, originTag);
          }
        }
      }
      // 10-new-conversation-guidance slice 3: remember which agent this 临时对话
      // used so the next new conversation pre-selects it.
      if (isChat && selectedAgentSlug) setLastTempAgent(selectedAgentSlug);
      // Update local state IMMEDIATELY (before navigate / sendMessage)
      // so the rest of ``handleSend`` and the SSE subscription closures
      // — which capture ``selectedProjectId`` and friends — see the
      // freshly minted ids. This is what the old ``refreshSessions``
      // race ultimately failed at.
      selectedSessionIdRef.current = created.id;
      if (id === NEW_SESSION_ID) {
        skipNextSessionStateResetRef.current = true;
      }
      setSelectedSessionId(created.id);
      // Seed the bound agent immediately. Attach-on-upload mints the session
      // WITHOUT navigating (``navigateOnCreate=false``), so the bootstrap effect
      // — which normally sets ``sessionAgentSlug`` from the fetched detail on a
      // URL change — never fires. Without this, the moment ``selectedSession``
      // turns truthy the composer flips ``selectedSession ? sessionAgentSlug :
      // selectedAgentSlug`` to a null agent and the picker looks deselected. The
      // session was just created with ``selectedAgentSlug``, so that is the
      // authoritative bound agent.
      setSessionAgentSlug(selectedAgentSlug);
      const createdItem = sessionDetailToListItem(created);
      setSessions([createdItem]);
      // Push the new session into the global session store IMMEDIATELY
      // so the sidebar's "New Chat" group renders it on this same render
      // tick — no waiting for the post-navigate ``fetchSidebarSessions``
      // round-trip. Same optimistic pattern the existing in-flight
      // message handler uses (around line 1450 below).
      setSidebarSessions(
        sidebarSessions.some((s) => s.id === createdItem.id)
          ? sidebarSessions.map((s) =>
              s.id === createdItem.id ? createdItem : s,
            )
          : [createdItem, ...sidebarSessions],
      );
      if (created.project_id !== selectedProjectId) {
        // Quick-chat: the backend just minted a fresh project + cwd
        // for this session. Pull the authoritative ``ProjectDetail``
        // (cwd lives on the backend — host writes flow through
        // ``fs_registry`` and we never derive it locally) and merge.
        try {
          const wsDetail = await projectsApi.get(created.project_id);
          setProjects((prev) => {
            const filtered = prev.filter((w) => w.id !== wsDetail.id);
            return [...filtered, wsDetail];
          });
          // Also push into the global project store so the layout's
          // "New Chat" filter (``allProjects.filter(kind === "chat")``)
          // immediately sees this row — without it, the new session
          // would still be filtered out of the chat group until the
          // path-change ``fetchProjects`` round-trip completes.
          upsertProject(wsDetail);
        } catch {
          /* non-fatal — file tree falls back gracefully */
        }
        setSelectedProjectId(created.project_id);
      }
      void fetchSidebarSessions();
      // Promote the URL from ``/conversation/new`` (or any other
      // fresh-entry path) to ``/conversation/{real-id}`` so the page
      // is no longer stuck on a sentinel. ``replace: true`` keeps the
      // back button from taking the user back to an empty draft.
      // Bootstrap re-fires under the new URL but its events refetch
      // is gated on ``sessionDetail.id !== selectedSessionIdRef.current``
      // — we just set the ref, so the in-flight SSE subscription is
      // not disturbed.
      // Attach-on-upload mints the session WITHOUT navigating (navigateOnCreate
      // = false): staying on ``/conversation/new`` until the user actually sends
      // mirrors the project-detail composer and avoids the navigate→bootstrap
      // churn that was dropping the freshly-attached file from the panel. The
      // send path navigates explicitly (see ``performSend``).
      if (id === NEW_SESSION_ID && navigateOnCreate) {
        navigate(
          `/conversation/${created.id}${isSkillCreatorMode ? "?mode=skill-creator" : ""}`,
          { replace: true },
        );
      }
      return created;
    },
    [
      selectedProjectId,
      activeProject?.kind,
      fetchSidebarSessions,
      sidebarSessions,
      setSidebarSessions,
      upsertProject,
      selectedSession,
      selectedProviderId,
      selectedModelId,
      selectedRuntimeId,
      selectedMcpSlugs,
      // ADR-013/ADR-006: ``selectedPermissionMode`` is read inside this
      // callback at session creation time. Without it in the deps array
      // the closure stays bound to the initial ``"full_access"`` value
      // and the chat-default session is silently created with the wrong
      // mode — the conversation page composer then reflects that wrong
      // value (locked) after navigate, which is the user-visible
      // "default permission picked, full access shown" regression.
      // Project-detail flow has no equivalent bug because its
      // ``handleSend`` is a plain function (no closure cache).
      selectedPermissionMode,
      selectedEffort,
      selectedAgentSlug,
      isSkillCreatorMode,
      skillKindParam,
      skillProjectParam,
      id,
      navigate,
      // Multi-target routing: the chosen execution target is read at
      // creation time — same closure-staleness trap as permission mode.
      resolveExecTarget,
    ],
  );

  // SESSION-LIFETIME data-plane stream (docs/design/session-stream-lifetime.md).
  // One call per opened session: the stream stays up across every turn — the
  // server generator never closes at turn end and the kernel bus survives
  // turns, so drained queue items, scheduled turns, bg-task wake-ups and
  // other-client turns all arrive on this one connection with no per-turn
  // re-subscription. Terminal frames are ordinary events (they update status
  // and bookkeeping; they do NOT tear the stream down). The stream ends only
  // when superseded by the next session's open, or on unmount.
  const subscribeToSession = useCallback(
    (
      sessionId: string,
      // HISTORY-space cursor (durable-store seq — pass ``historyCursorRef``):
      // the server backfills persisted events strictly after it. Live frames
      // on the stream carry kernel-LOCAL seqs and must never be compared to
      // (or folded into) this value.
      afterSeq: number,
    ) => {
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const abort = new AbortController();
      // Bounded open-reconcile burst (see below the appendEvent def), cancelled
      // once the stream settles or the subscription is superseded.
      const reconcileBurstTimers: number[] = [];
      abortRef.current = abort;

      const appendEvent = (event: SessionEventDTO) => {
        // Drop deliveries that outlive this subscription. A session switch
        // aborts the stream (the ``selectedSessionId`` effect) — but an
        // in-flight poll response, a gap-fill, or a frame parsed in the same
        // tick as the abort still resolves afterwards. Without this guard
        // the OLD session's events land in the NEW session's transcript and
        // pass the dedupe below (event identities are session-scoped only by
        // this check). The ref check also covers the abort effect's commit
        // lag: ``selectedSessionIdRef`` flips synchronously in bootstrap,
        // before the abort lands.
        if (abort.signal.aborted) return;
        if (selectedSessionIdRef.current !== sessionId) return;
        // Replay detection for the terminal gates below. Reconnect gap-fills
        // and the server's initial drain can legitimately redeliver frames
        // this transcript already consumed; replays must stay render-inert
        // AND gate-inert (a replayed terminal frame must not re-run turn-end
        // handling). Uid-less legacy frames keep the pre-uid behavior. The
        // set is bounded: a session-lifetime page accumulates uids for its
        // whole stay, so shed the oldest half at the cap.
        const frameUid = event.event_uid ?? null;
        const isReplayOfSeen =
          frameUid != null && seenEventUidsRef.current.has(frameUid);
        if (frameUid != null) {
          const seen = seenEventUidsRef.current;
          if (seen.size >= 8192) {
            let shed = 0;
            for (const uid of seen) {
              seen.delete(uid);
              if (++shed >= 4096) break;
            }
          }
          seen.add(frameUid);
        }
        // Deliberately NO history-cursor advancement here: SSE frames mix
        // server-side backfill (history seq) with live frames (kernel-LOCAL
        // seq) and the two are indistinguishable on the wire — feeding a
        // live seq into ``historyCursorRef`` would corrupt every later
        // ``after_seq`` read. The cursor advances only from REST history
        // responses (refreshEvents / reconcile / poll paths); the price is
        // that a reconnect may replay a little more history, which the
        // uid dedup below collapses.
        setEvents((prev) => {
          // uid-first dedup across segments; a uid-less persisted frame
          // falls back to the legacy seq check, but only against other
          // uid-less rows (a uid row's seq may be from the other space).
          const duplicate = event.event_uid
            ? prev.some((existing) => existing.event_uid === event.event_uid)
            : event.seq > 0 &&
              prev.some(
                (existing) => !existing.event_uid && existing.seq === event.seq,
              );
          if (duplicate) return prev;
          return [...prev, event];
        });
        // Once the kernel echoes the user's message back as the
        // first ``message.user`` event of the turn, the optimistic
        // pending card has served its purpose — drop it so we don't
        // double-render the same text.
        if (event.event.event_type === "message.user") {
          setPendingUserMessage(null);
        }
        // Live TODO panel update — kernel V5+messages emits
        // ``session.todos.update`` whenever the agent calls
        // TodoWrite. Replace the snapshot so completed/in-progress
        // states flow into the panel in real time.
        if (event.event.event_type === "session.todos.update") {
          const refreshed = parseTodosUpdate(event);
          console.debug(
            "[Conversation] session.todos.update parsed:",
            refreshed?.length ?? 0,
            "items",
            refreshed,
          );
          if (refreshed) setTodos(refreshed);
        }
        // Live workflow progress — Claude ``Workflow`` tool runs stream a
        // snapshot per tick (phases / per-agent state / status), keyed by the
        // launch tool_use_id. Merge into the per-tool map so the Workflow tool
        // card renders live progress. ``script`` / ``scriptPath`` arrive only on
        // the first snapshot, so carry them forward when a later tick omits them.
        if (event.event.event_type === SESSION_WORKFLOW_PROGRESS_EVENT) {
          const wp = parseWorkflowProgress(event);
          if (wp) {
            setWorkflowStates((prev) => {
              const prevState = prev.get(wp.id);
              const next = new Map(prev);
              next.set(wp.id, {
                ...wp.state,
                scriptPath: wp.state.scriptPath ?? prevState?.scriptPath,
                script: wp.state.script ?? prevState?.script,
              });
              return next;
            });
          }
        }
        // ADR-013 approval contract: track the current unresolved
        // clarifying_questions pending so the AskUserQuestionCard's
        // submit can POST /actions with the right pending_id. Non-
        // clarifying subjects (shell_command / file_change /
        // mcp_tool_call / tool_input) feed into the approval tray
        // rendered above the Composer.
        // ADR-013 approval contract v1 (kernel 1aae940) + v2 (kernel
        // d008b53): track parked pendings so the user can resolve
        // them, learn rule_id → preview mappings so subsequent
        // cache-hit ``auto_approved`` events show what rule fired,
        // and surface synthetic seals (``expired`` / ``interrupted``).
        if (event.event.event_type === SESSION_REQUIRES_ACTION_EVENT) {
          const ra = parseRequiresAction(event);
          if (ra) {
            if (ra.subject === "clarifying_questions") {
              currentClarifyingPendingRef.current = ra.pending_id;
            } else {
              const subject = ra.subject as ApprovalCardSubject;
              setPendingApprovals((prev) =>
                prev.some((p) => p.pendingId === ra.pending_id)
                  ? prev
                  : [
                      ...prev,
                      {
                        pendingId: ra.pending_id,
                        subject,
                        payload: ra.payload,
                        availableDecisions: ra.available_decisions,
                        sessionRulePreviewDisplay:
                          ra.session_rule_preview?.display ?? null,
                        originalInput: ra.original_input,
                        receivedAt: event.timestamp,
                      },
                    ],
              );
            }
          }
        }
        if (event.event.event_type === SESSION_ACTION_RESOLVED_EVENT) {
          const ar = parseActionResolved(event);
          if (ar) {
            if (currentClarifyingPendingRef.current === ar.pending_id) {
              currentClarifyingPendingRef.current = null;
            }
            // v2: kernel-synthesized cache-hit. The kernel ALWAYS emits
            // a paired ``requires_action`` immediately before the
            // synthetic ``action_resolved(auto_approved)`` (see
            // ``claude_agent/runtime.py``: ``event_sink.emit(...)`` then
            // ``cache_hit is not None`` branch), so the requires_action
            // handler above has already pushed a pending card into
            // ``pendingApprovals``. Harvest its subject + payload into
            // the notice so the strip can render a one-line summary
            // ("what was approved"), then remove the pending card —
            // otherwise it stays clickable and the user hits "批准",
            // which the kernel rejects with 409 "already resolved as
            // auto_approved". Finally schedule a 5s auto-remove on the
            // notice so the tray doesn't accumulate forever (symmetric
            // with ApprovalResolvedStrip's 2s, longer here because the
            // user didn't initiate the action).
            if (ar.decision === "auto_approved") {
              // Pull the harvested subject/payload BEFORE the pending
              // filter — once filtered out, the row is gone from
              // state and we can't read it.
              const sourceEntry = pendingApprovals.find(
                (p) => p.pendingId === ar.pending_id,
              );
              setPendingApprovals((prev) =>
                prev.filter((p) => p.pendingId !== ar.pending_id),
              );
              const ruleId = ar.auto_resolved_by_rule_id;
              const preview = ruleId
                ? (ruleIdToPreviewRef.current.get(ruleId) ?? null)
                : null;
              setAutoApprovedNotices((prev) =>
                prev.some((n) => n.pendingId === ar.pending_id)
                  ? prev
                  : [
                      ...prev,
                      {
                        pendingId: ar.pending_id,
                        receivedAtLabel: event.timestamp
                          ? new Date(event.timestamp).toLocaleTimeString()
                          : undefined,
                        rulePreviewDisplay: preview,
                        subject: sourceEntry?.subject ?? null,
                        payload: sourceEntry?.payload ?? null,
                      },
                    ],
              );
              // Auto-clear the notice after 5s so the tray stays
              // bounded. Idempotent: if the notice was already
              // removed (e.g. session switch wiped the state), the
              // filter is a no-op.
              window.setTimeout(() => {
                setAutoApprovedNotices((prev) =>
                  prev.filter((n) => n.pendingId !== ar.pending_id),
                );
              }, 5000);
              return;
            }
            // v2: ``approve_for_session`` commits a kernel-owned
            // rule. Snapshot rule_id → preview.display so any
            // subsequent ``auto_approved`` for the same rule can be
            // labelled even though the kernel doesn't re-emit the
            // preview.
            if (ar.decision === "approve_for_session" && ar.rule_id) {
              const match = pendingApprovals.find(
                (p) => p.pendingId === ar.pending_id,
              );
              if (match?.sessionRulePreviewDisplay) {
                ruleIdToPreviewRef.current.set(
                  ar.rule_id,
                  match.sessionRulePreviewDisplay,
                );
              }
            }
            // v1 + v2: AskUserQuestion answered, normal approve /
            // reject, plus the new ``approve_with_changes`` /
            // ``approve_for_session`` verbs and the synthetic seals
            // (``expired`` / ``interrupted``). Flip the matching
            // tray card into ``answered`` so the user sees the
            // outcome before it fades off the tray on the next
            // render tick. ``auto_approved`` is handled by the early
            // ``return`` above; this branch only sees the 7 user /
            // synthetic verbs that the strip renders.
            const resolvedDecision = ar.decision as ApprovalResolvedDecision;
            setPendingApprovals((prev) => {
              const match = prev.find((p) => p.pendingId === ar.pending_id);
              if (!match) return prev;
              return prev.map((p) =>
                p.pendingId === ar.pending_id
                  ? {
                      ...p,
                      answered: true,
                      submitting: false,
                      decision: resolvedDecision,
                      rejectMessage:
                        resolvedDecision === "reject" ? ar.message : null,
                    }
                  : p,
              );
            });
            // Drop the answered card after a short delay so the user
            // sees the confirmation badge before it disappears.
            window.setTimeout(() => {
              setPendingApprovals((prev) =>
                prev.filter((p) => p.pendingId !== ar.pending_id),
              );
            }, 2000);
          }
        }
        // Turn lifecycle on a SESSION-LIFETIME stream: terminal frames are
        // ordinary events. They update status/bookkeeping below but never
        // close the stream — the next turn (queue drain, schedule, another
        // client, a follow-up send) arrives on this same connection.
        const evType = event.event.event_type;
        // A non-replayed ``message.user`` means a turn genuinely started —
        // release the optimistic send-pending flag (its only job is bridging
        // the click → turn-start window; ``status === "running"`` carries the
        // busy state from here). Replays (reconnect backfill) stay inert.
        if (evType === "message.user" && !isReplayOfSeen) {
          setSending(false);
          // A turn starting is the authoritative "a queued head may have just
          // been dispatched" signal — the drain marks the row ``dispatched``
          // (a host-local write, no mirror lag) BEFORE emitting this event, so
          // refetching now always reads post-dispatch state and drops the
          // consumed item from the queue bar. This is timing-independent,
          // unlike the ``isBusy`` edge refetch below (which misses when the
          // idle→running transition is coalesced into one render and ``isBusy``
          // never dips — the "dispatched item keeps showing as queued during
          // its own turn" bug). Harmless for a direct (non-drain) send: the
          // refetch returns the same list. The ticket guard dedups overlap.
          void refreshQueueRef.current();
        }
        const status = event.event.payload.status;
        // Reconcile the authoritative status from the live frame so the derived
        // loading flag + header pill track the turn without waiting for a poll.
        // Replays are FULLY inert — non-terminal and terminal alike; the
        // rationale (an asymmetric gate deadlocked the pill at 运行中 on
        // cloud reconnect backfills) lives on ``shouldApplySessionStatus``.
        // The send path's stale-pre-turn-``idle`` hazard (image-upload slow
        // start) is handled by the busy derivation instead: ``sendPending``
        // overrides a terminal status until the turn's start event or a send
        // error (see ``deriveTurnActive``).
        if (evType === "session.update" && shouldApplySessionStatus(status, isReplayOfSeen)) {
          setSessions((prev) =>
            prev.map((s) =>
              s.id === sessionId
                ? { ...s, status: status as SessionListItem["status"] }
                : s,
            ),
          );
        }
        // ``!isReplayOfSeen``: a redelivered terminal frame must not re-run
        // turn-end handling for a turn that is long over.
        const terminal =
          !isReplayOfSeen &&
          (evType === "session.idle" ||
            evType === "run.failed" ||
            (evType === "session.update" &&
              status !== undefined &&
              status !== "" &&
              status !== "running" &&
              status !== "created"));
        if (terminal) {
          // The turn is over — release any lingering optimistic pending flag
          // (a send whose turn just finished, or a stale one after an error).
          setSending(false);
          // Safety net for workflow cards: the turn is over, so any Workflow
          // run is definitively finished (the kernel blocks the turn until the
          // run completes, then force-emits a terminal snapshot). The snapshot
          // is live-only; if it lost the race against this terminal event, a
          // still-"running" card would pulse forever. Coerce it to
          // ``completed``; cards that already received a terminal status are
          // left untouched. (With the stream no longer closing at terminal, a
          // late snapshot now also arrives on its own — this is pure backstop.)
          setWorkflowStates((prev) => {
            let changed = false;
            const next = new Map(prev);
            for (const [wfId, wfState] of prev) {
              if (isWorkflowRunning(wfState.status)) {
                next.set(wfId, { ...wfState, status: "completed" });
                changed = true;
              }
            }
            return changed ? next : prev;
          });
        }
      };

      // No forever-poll here — ongoing delivery is stream-driven: live frames
      // via the SSE reader, plus the server-side backfill inside
      // ``iter_events_sse`` re-emitting any missed persisted events on THIS
      // stream (~2s); a full stream drop is handled by the unconditional
      // reconnect below (gap-fill + backoff).
      //
      // On OPEN (this now runs once per session stay, not per turn) the
      // initial transcript window (``refreshEvents``) and this subscription
      // race — under some interleavings the loaded window lands empty /
      // superseded, leaving the transcript BLANK until a later event forces a
      // re-render (the "reload mid-turn shows nothing until you jostle it"
      // bug; the content IS persisted, just not painted). Close that race
      // deterministically with a BOUNDED, self-terminating reconcile burst:
      // re-fetch the transcript window a few times over the first ~2.5s and
      // idempotently merge it, forcing the persisted transcript to paint
      // regardless of which async path won.
      const reconcileTranscript = () => {
        if (abort.signal.aborted) return;
        if (selectedSessionIdRef.current !== sessionId) return;
        void sessionsApi
          .listEventsWindow(sessionId, { turnLimit: TURN_PAGE_SIZE })
          .then((resp) => {
            if (abort.signal.aborted) return;
            if (selectedSessionIdRef.current !== sessionId) return;
            if (resp.items.length === 0) return;
            // Order-safe merge, NOT ``appendEvent`` (tail-append would render
            // history after the current turn) and NOT a global seq sort (live
            // deltas are ``seq 0`` — a sort throws them to the FRONT of the
            // transcript, which rendered refreshed-mid-turn content into the
            // previous turn's area). ``mergeEventWindow`` inserts only the
            // genuinely-missing persisted rows at their seq position and keeps
            // live entries glued where they arrived.
            setEvents((prev) => mergeEventWindow(prev, resp.items));
            const top = resp.items[resp.items.length - 1].seq;
            if (top > historyCursorRef.current) historyCursorRef.current = top;
          })
          .catch(() => {});
      };
      for (const ms of [400, 1200, 2500]) {
        reconcileBurstTimers.push(window.setTimeout(reconcileTranscript, ms));
      }

      // Connection controller: the server generator never closes on its own,
      // so ANY stream end that we didn't abort ourselves is abnormal (proxy
      // cut, server restart, dropped socket). Recover unconditionally:
      // gap-fill what the stream missed from the DB, then reconnect with
      // exponential backoff — for as long as this subscription owns the page.
      // There is deliberately NO give-up cap: capping at N attempts turned a
      // flaky stream into a silent fake completion (verified incident: a long
      // quiet tool call kept the stream frame-free, five closes exhausted the
      // cap mid-turn, and the page showed copy/retry while the kernel was
      // still working). The delay is capped at 15s, and each cycle's gap-fill
      // keeps delivering content, so the steady state degrades to cheap
      // polling — never to a lie. No turn-liveness decision is needed anymore:
      // an idle session's open stream just sits on server heartbeats.
      const gapFillAndReconnect = async () => {
        if (abort.signal.aborted) return;
        try {
          const resp = await sessionsApi.listEvents(
            sessionId,
            historyCursorRef.current,
          );
          if (abort.signal.aborted) return;
          // ``listEvents`` results are HISTORY-space — advance the history
          // cursor here (``appendEvent`` deliberately never does: it also
          // handles live frames whose kernel-local seq must not leak in).
          for (const event of resp.items) {
            if (event.seq > 0) {
              historyCursorRef.current = Math.max(
                historyCursorRef.current,
                event.seq,
              );
            }
          }
          for (const event of resp.items) {
            // May deliver a missed terminal event → status + bookkeeping.
            appendEvent(event);
          }
          // Content flowed — the server is alive and producing; keep the
          // backoff snappy. (The live-frame reset can't cover this: a
          // stream that dies between frames never delivers one.)
          if (resp.items.length > 0) {
            streamReconnectAttemptsRef.current = 0;
          }
        } catch {
          // Gap-fill is best-effort — the reconnect below retries anyway.
        }
        if (abort.signal.aborted) return;
        const attempt = streamReconnectAttemptsRef.current;
        streamReconnectAttemptsRef.current = attempt + 1;
        const delay = Math.min(1000 * 2 ** Math.min(attempt, 4), 15000);
        // Diagnosability: unexpected closes are invisible otherwise — the
        // last verified incident could only be reconstructed from the DB.
        console.warn(
          `[Conversation] events stream closed unexpectedly (attempt ${attempt + 1}) — reconnecting in ${delay}ms`,
        );
        window.setTimeout(() => {
          if (abort.signal.aborted) return;
          if (selectedSessionIdRef.current !== sessionId) return;
          connect();
        }, delay);
      };
      // Never let the recovery chain escape as an unhandled rejection —
      // every recoverable branch inside is already try/caught.
      const safeRecover = () => gapFillAndReconnect().catch(() => {});

      const connect = () => {
        if (abort.signal.aborted) return;
        // Re-sample the cursor on every (re)connect so the server's initial
        // drain replays as little as possible; the uid dedup absorbs overlap.
        const startSeq = Math.max(afterSeq, historyCursorRef.current);
        sessionsApi
          .subscribeEvents(
            sessionId,
            (event) => {
              // A delivered frame means the stream is healthy — reset the
              // unexpected-close backoff.
              streamReconnectAttemptsRef.current = 0;
              appendEvent(event);
            },
            startSeq,
            abort.signal,
          )
          .then(safeRecover)
          .catch(safeRecover);
      };

      // Gate the FIRST open on the history-window hydration: ``refreshEvents``
      // resets the cursor synchronously and only hydrates it when its fetch
      // lands, so an ungated open could start at ``afterSeq = 0`` and replay
      // the whole session over SSE. Time-capped so a hung history fetch
      // degrades to an ungated open instead of never opening the stream.
      void Promise.race([
        historyHydrationRef.current,
        new Promise<void>((resolve) => window.setTimeout(resolve, 3000)),
      ]).then(() => {
        if (abort.signal.aborted) {
          reconcileBurstTimers.forEach((t) => window.clearTimeout(t));
          return;
        }
        connect();
      });
    },
    [],
  );

  const handleOpenKbPicker = useCallback(() => {
    // The ``useKbDocTree`` hook is gated on ``kbPickerOpen`` — opening
    // the picker triggers the tree fetch; no manual load needed here.
    setKbPickerOpen(true);
  }, []);

  const handleKbPickerConfirm = useCallback(
    async (ids: string[]) => {
      setKbPickerOpen(false);
      if (ids.length === 0) return;
      // KB picks land in the same session-attachment pipeline as local
      // uploads. The hook eager-mints the session if needed (new-conversation
      // entry), attaches each doc, re-reads the list, and starts polling the
      // async parse status — so the composer chips + panel show progress.
      try {
        // navigate:false — stay on /conversation/new while attaching.
        await attachKbDocs(ids, () => ensureSession(false));
      } catch {
        toast.error(t("common.failed" as Parameters<typeof t>[0]));
      }
    },
    [attachKbDocs, ensureSession, t],
  );

  // Local files upload the moment they're attached (drag-drop, file picker, or
  // the panel's upload button). The hook eager-mints the session if needed,
  // uploads each file, and polls its parse status for the progress UI.
  const handleLocalFilesAttach = useCallback(
    (files: File[]) => {
      // navigate:false — stay on /conversation/new while attaching.
      void attachLocalFiles(files, () => ensureSession(false));
    },
    [attachLocalFiles, ensureSession],
  );

  // Delete a persisted session attachment row (KB-sourced or local).
  // Hoisted so both the side panel's remove button and the composer's
  // pinned-chip remove button share it.
  const handleRemoveSessionAttachment = useCallback(
    async (attachmentId: string) => {
      await removeSessionAttachmentRow(attachmentId);
      // Attaching a file on a draft eagerly mints a session to hold the upload.
      // If the user removes the last file without ever sending a message, that
      // session is left behind as a statusless "New chat" orphan cluttering
      // Activity / recents (and, before the idempotent-delete fix, one the user
      // couldn't clear). Discard it: we're still on the draft URL, this was the
      // last attachment, and nothing was sent.
      const wasLastAttachment =
        sessionAttachments.filter((a) => a.id !== attachmentId).length === 0;
      if (
        id === NEW_SESSION_ID &&
        selectedSessionId &&
        effectiveTurns.length === 0 &&
        wasLastAttachment
      ) {
        const orphan = selectedSessionId;
        // Reset to a clean draft first so no per-session effect re-fetches the
        // session we're about to delete, then delete it best-effort.
        await refreshEvents(null);
        setSelectedSessionId(null);
        setSessions([]);
        void sessionsApi.delete(orphan).catch(() => {});
      }
    },
    [
      removeSessionAttachmentRow,
      sessionAttachments,
      id,
      selectedSessionId,
      effectiveTurns.length,
      refreshEvents,
    ],
  );

  // The actual send. Attachments are uploaded on attach, so this never
  // uploads — it just mints/reuses the session and posts the message.
  const performSend = async () => {
    // Re-entrancy guard on the derived ``isBusy`` (not raw ``sending``): a
    // stuck ``sending`` on a reconciled-idle session must not swallow the send.
    if (!draft.trim() || isBusy) return;
    // Skill-creator binds an agent (its create flow needs one) — nudge if none.
    // A normal new 临时对话 may now be agentless (a quick chat on the default
    // model), so it sends without an agent pick.
    if (!selectedSession && !selectedAgentSlug && isSkillCreatorMode) {
      toast.error(_t("conversation.selectAgentFirst" as I18nKey));
      return;
    }
    revealPanelOnSessionChangeRef.current = true;
    panelSetCollapsed(false);
    // ``draft`` already contains any inline ``/slug`` tokens because
    // Composer serializes its skill chips into the controlled value.
    // Don't prepend ``selectedComposerSkill`` again or the message
    // ships with ``/skill /skill ...``.
    const text = draft.trim();
    // Optimistic UI: clear the input and surface the message + a
    // "thinking" hint immediately so the user gets sub-frame feedback,
    // even while ensureSession + uploads + POST /messages are still
    // round-tripping. Without this the page sits idle for ~500-3000ms
    // depending on session creation + Claude SDK warm-up.
    // Attachments are already uploaded (on attach); the optimistic bubble
    // lists this turn's pending rows so chips show instantly.
    const queuedAttachmentMeta = sessionAttachments
      .filter((a) => !a.consumed_at)
      .map((a) => ({ name: a.filename, size: a.size_bytes }));
    pinNextTurnToTopRef.current = true;
    keepCurrentTurnAtTopRef.current = true;
    setPendingUserMessage({
      text,
      attachments: queuedAttachmentMeta,
      fromSeq: historyCursorRef.current,
      sentAt: Date.now(),
    });
    setSending(true);
    // Optimistically mark the active session running so the derived loading flag
    // shows immediately (an existing session re-send would otherwise read its
    // prior ``idle`` status for a beat and flicker). The real status is
    // reconciled from the POST response + SSE ``session.update`` right after.
    if (selectedSessionId) {
      setSessions((prev) =>
        prev.map((s) =>
          s.id === selectedSessionId ? { ...s, status: "running" } : s,
        ),
      );
    }
    setError(null);
    setDraft("");
    setSelectedComposerSkill(null);
    isSendInFlightRef.current = true;
    if (selectedComposerSkill) {
      console.debug(
        "[Conversation] skill selected:",
        selectedComposerSkill.name,
      );
    }
    try {
      const session = await ensureSession();
      if (!session?.id) throw new Error("Failed to create session.");
      // Land on the real session URL on SEND. ``ensureSession`` navigates
      // inline when it mints a brand-new session (no prior attach), but when
      // the session was pre-created by an attach (navigate:false — we stayed on
      // ``/conversation/new`` so the attachment panel/chips render without the
      // navigate→bootstrap churn) it returns cached without navigating, so do
      // the swap here. ``replace:true`` keeps Back from returning to the draft.
      if (id === NEW_SESSION_ID && session.id !== NEW_SESSION_ID) {
        promotingSessionIdRef.current = session.id;
        navigate(
          `/conversation/${session.id}${isSkillCreatorMode ? "?mode=skill-creator" : ""}`,
          {
            replace: true,
            state: {
              promotedFromNew: true,
              promotedSessionId: session.id,
            },
          },
        );
      }

      // Attachments were already uploaded (on attach) and the backend's
      // ``_run_agent_background`` reads this session's pending
      // SessionAttachmentRow rows, threading ``parsed_path`` (or the raw
      // ``stored_path`` when a parse hasn't finished) into kernel
      // ``UserMessage.attachments[]`` plus the ``additional-context`` hint —
      // so the prompt text stays clean. Nothing to upload here.

      // Prompt text is sent verbatim — no attachment hint appended.
      const outboundText = text;

      // No subscription here: the session-lifetime stream (opened by the
      // session-open effect, including right after the ``/conversation/new``
      // promotion above) carries this turn's events. ``sending`` bridges the
      // click → turn-start window; the turn's ``message.user`` echo on the
      // stream releases it.

      const detail = await sessionsApi.sendMessage(
        session.id,
        outboundText,
        selectedProviderId,
        selectedModelId,
      );
      if (!detail?.id) throw new Error("Failed to send message.");
      // The desktop sidebar's per-project session lists are derived from
      // ``/v1/runs`` (ProjectLayoutBase), NOT from the session store the
      // optimistic updates below write to — so without a poke here a brand-new
      // session only appears after the next 10s running poll (or a reload).
      // Force the shared poller now; the resulting ``liveRunIds`` transition
      // also triggers the layout's finished-runs refresh.
      refreshRunningRuns();
      // Attachments are per-turn: the backend ships this turn's pending
      // set with the message, then stamps those rows ``consumed_at`` once
      // the turn runs. Optimistically mark them consumed so they drop out
      // of the composer's staging chips immediately (they stay in the
      // panel's "uploaded files" history).
      markPendingConsumed();
      // ``send_message`` kicks the turn off in the BACKGROUND and returns
      // immediately — its status snapshot is stale-prone in BOTH directions:
      //  (a) taken before the kernel flips to "running" inside ``run_turn``
      //      → carries the PRE-turn "idle"/"created" (letting it through
      //      killed the whole loading UI for the turn: ``isBusy = sending &&
      //      !terminal(status)`` read a terminal status until a refresh);
      //  (b) taken mid-turn but delivered AFTER an ultra-fast turn already
      //      ended → carries a stale "running" that would resurrect the
      //      running pill on a finished turn (the terminal SSE frame that
      //      landed during the POST await already wrote "idle").
      // So the response never writes ``status`` for a row we already track:
      // the optimistic write at send start owns the turn's beginning and the
      // data-plane terminal frames (``session.update`` / ``session.idle``)
      // own its end. Only a row we DON'T have yet (fresh session) takes
      // "running" — a successful send means its turn is starting, and its
      // own terminal frames correct an instant failure.
      const startedSession: SessionListItem = {
        ...sessionDetailToListItem(detail),
        status: "running",
      };
      const keepLocalStatus = (s: SessionListItem): SessionListItem => ({
        ...startedSession,
        // Forward-upgrade only: a row still on the PRE-turn ``created`` takes
        // the send's ``running`` (a successful send means its turn is
        // starting — without this the header pill sat on 等待中 for the whole
        // first turn of a new session, since ``created`` isn't terminal and
        // nothing else wrote ``running`` mid-turn). Any other local status —
        // the optimistic ``running`` or a terminal frame that landed during
        // the POST await — stays authoritative, preserving both stale-status
        // protections documented above.
        status: s.status === "created" ? "running" : s.status,
      });
      setSessions((prev) =>
        prev.some((s) => s.id === startedSession.id)
          ? prev.map((s) =>
              s.id === startedSession.id ? keepLocalStatus(s) : s,
            )
          : [startedSession, ...prev],
      );
      setSidebarSessions(
        sidebarSessions.some((s) => s.id === startedSession.id)
          ? sidebarSessions.map((s) =>
              s.id === startedSession.id ? keepLocalStatus(s) : s,
            )
          : [startedSession, ...sidebarSessions],
      );
      setSelectedSessionId(detail.id);
    } catch (cause) {
      // The backend may attach an i18n key to a business error (structured
      // ``detail.key``) and let the client render it — e.g. a commercial
      // billing rejection. Prefer that key; otherwise show the raw message.
      const msg =
        cause instanceof ApiError && cause.i18nKey
          ? _t(
              cause.i18nKey as Parameters<typeof _t>[0],
              cause.i18nParams as Parameters<typeof _t>[1],
            )
          : cause instanceof Error
            ? cause.message
            : "Failed to send message.";
      setError(msg);
      toast.error(msg);
      setSending(false);
      pinNextTurnToTopRef.current = false;
      keepCurrentTurnAtTopRef.current = false;
      // Optimistic turn never got a real ``message.user`` echo because
      // the send failed — drop it so the user can retry without a
      // phantom card lingering. The session-lifetime stream stays up.
      setPendingUserMessage(null);
      // The optimistic ``running`` status write above must be reconciled:
      // under the derived busy (``sendPending || status === "running"``) a
      // stale optimistic ``running`` would pin the loading state forever.
      if (selectedSessionId) void refreshActiveSession(selectedSessionId);
    } finally {
      isSendInFlightRef.current = false;
    }
  };

  // Send entry point. Blocks on attachments that are still parsing — the
  // confirm dialog lets the user wait or submit with only the raw file
  // (no parsed content / doc-search until parsing finishes).
  // ---- Session input queue (docs/design/session-input-queue.md) ----

  // Single writer for the queue states. Every fetch/mutation takes a ticket
  // BEFORE its await and applies through here — a response whose ticket is no
  // longer the newest is stale (issued earlier, resolved later) and dropped,
  // so an in-flight refetch can never clobber a mutation's fresher list.
  const applyQueueList = useCallback(
    (list: QueuedInputList, ticket: number) => {
      if (ticket !== queueListTicketRef.current) return;
      setQueue(list.items);
      setQueuePaused(list.paused);
      setQueueDraining(list.draining ?? false);
      setQueueDispatching(list.dispatching ?? null);
    },
    [],
  );

  const refreshQueue = useCallback(async () => {
    if (!selectedSessionId) return;
    const ticket = ++queueListTicketRef.current;
    try {
      const list = await queueApi.list(selectedSessionId);
      applyQueueList(list, ticket);
    } catch {
      /* best-effort — a queue fetch failure must not break the conversation */
    }
  }, [selectedSessionId, applyQueueList]);

  // Kept current so the SSE ``appendEvent`` closure (created once inside the
  // deps-``[]`` ``subscribeToSession`` callback) always calls the latest
  // ``refreshQueue`` without re-subscribing.
  const refreshQueueRef = useRef(refreshQueue);
  useEffect(() => {
    refreshQueueRef.current = refreshQueue;
  }, [refreshQueue]);

  const performEnqueue = async () => {
    const text = draft.trim();
    if (!text || !selectedSessionId) return;
    setDraft("");
    setSelectedComposerSkill(null);
    const ticket = ++queueListTicketRef.current;
    try {
      const list = await queueApi.enqueue(selectedSessionId, text, {
        providerId: selectedProviderId,
        modelId: selectedModelId,
      });
      applyQueueList(list, ticket);
    } catch (cause) {
      toast.error(
        cause instanceof Error ? cause.message : "Failed to queue message.",
      );
      setDraft(text); // restore so the user can retry
    }
  };

  const handleEditQueued = async (queueId: string, text: string) => {
    if (!selectedSessionId) return;
    const ticket = ++queueListTicketRef.current;
    try {
      const list = await queueApi.edit(selectedSessionId, queueId, text);
      applyQueueList(list, ticket);
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Edit failed.");
    }
  };

  const handleDeleteQueued = async (queueId: string) => {
    if (!selectedSessionId) return;
    const ticket = ++queueListTicketRef.current;
    try {
      const list = await queueApi.remove(selectedSessionId, queueId);
      applyQueueList(list, ticket);
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Delete failed.");
    }
  };

  const handleResumeQueue = async () => {
    if (!selectedSessionId) return;
    const ticket = ++queueListTicketRef.current;
    try {
      const list = await queueApi.resume(selectedSessionId);
      applyQueueList(list, ticket);
      // The resumed drain's turns stream in on the session-lifetime stream.
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Resume failed.");
    }
  };

  const handleSteerQueued = async (queueId: string) => {
    if (!selectedSessionId) return;
    const ticket = ++queueListTicketRef.current;
    try {
      // Send-now: the backend silently interrupts the active turn and drains
      // this item. The steered turn streams in on the session-lifetime stream.
      const list = await queueApi.steer(selectedSessionId, queueId);
      applyQueueList(list, ticket);
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Send failed.");
    }
  };

  // Hydrate the persisted queue when the active session changes. Clear the
  // previous session's list synchronously first (ticket bump invalidates any
  // still-in-flight response of the old session) so its bubbles never bleed
  // into the new conversation while the fetch is out.
  useEffect(() => {
    queueListTicketRef.current++;
    setQueue([]);
    setQueuePaused(false);
    setQueueDraining(false);
    setQueueDispatching(null);
    void refreshQueue();
  }, [refreshQueue]);

  // NO drain-follower here anymore: drained queue turns arrive on the
  // session-lifetime stream like any other turn (the whole
  // resubscribe/checkNow/control-frame-trigger machinery this effect used to
  // carry existed only because the stream was torn down at every turn end —
  // see docs/design/session-stream-lifetime.md).

  // Refetch on BOTH turn boundaries (session-input-queue §8.4):
  // - busy → idle: drained items drop, blocked / paused state surfaces;
  // - idle → busy: the drain consumed the head to START this turn — without
  //   this edge the already-dispatched item kept rendering as "queued" under
  //   the composer for the WHOLE next turn (its message is simultaneously
  //   streaming in the transcript above), because the busy-gated 5s backstop
  //   is off while a turn runs and the fall-edge refetch raced the dispatch.
  //   The backend marks the row ``dispatched`` BEFORE the turn's first event,
  //   so a refetch triggered by the turn start always reads post-dispatch
  //   state; the ticket guard absorbs any stragglers.
  // Keyed on the derived ``isBusy`` (not raw ``sending``) so the resync still
  // fires when only the status reconciliation ends the turn.
  const prevQueueBusyRef = useRef(false);
  useEffect(() => {
    if (prevQueueBusyRef.current !== isBusy) void refreshQueue();
    prevQueueBusyRef.current = isBusy;
  }, [isBusy, refreshQueue]);

  // Turn-end bookkeeping, formerly smuggled into the per-turn stream teardown
  // (``stopSubscription``): reconcile the authoritative session row, and
  // refresh the sidebar — per QUEUE, not per turn (while more drained items
  // are about to run, reordering the sidebar at every sub-second boundary
  // reads as flicker; the drain-quiet effect below lands the final refresh).
  const prevTurnBusyRef = useRef(false);
  useEffect(() => {
    const sid = selectedSessionId;
    if (prevTurnBusyRef.current && !isBusy && sid) {
      void refreshActiveSession(sid);
      if (!queueContinuesRef.current) void fetchSidebarSessions();
    }
    prevTurnBusyRef.current = isBusy;
  }, [isBusy, selectedSessionId, refreshActiveSession, fetchSidebarSessions]);

  // Keep ``queueContinuesRef`` (read by the turn-end bookkeeping effect) in step with the
  // queue states so the per-boundary sidebar refetch is skipped only while the
  // chain genuinely continues.
  useEffect(() => {
    queueContinuesRef.current =
      !queuePaused &&
      (queueDraining || queue.some((i) => i.status === "queued"));
  }, [queue, queueDraining, queuePaused]);

  // Deferred sidebar refetch: ``stopSubscription`` skips it while the chain
  // continues, so land it once the drain goes quiet (draining true → false
  // with nothing streaming) — one reorder per queue instead of one per item.
  const prevQueueDrainingRef = useRef(false);
  useEffect(() => {
    if (prevQueueDrainingRef.current && !queueDraining && !isBusy) {
      void fetchSidebarSessions();
    }
    prevQueueDrainingRef.current = queueDraining;
  }, [queueDraining, isBusy, fetchSidebarSessions]);

  // Backstop: while the queue claims pending/in-flight work but nothing is
  // streaming locally, re-poll the list every 5s. This converges every stale
  // corner the event-driven paths can miss — a ``draining`` flag gone stale in
  // either direction (which would otherwise pin ``displayBusy``'s loading
  // affordances or hide them), a blocked head surfacing, another client
  // draining the same session. Bounded: idle sessions with an empty quiet
  // queue never poll.
  useEffect(() => {
    if (!selectedSessionId || isBusy) return;
    if (!queueDraining && !queue.some((i) => i.status === "queued")) return;
    const timer = window.setInterval(() => void refreshQueue(), 5000);
    return () => window.clearInterval(timer);
  }, [selectedSessionId, isBusy, queueDraining, queue, refreshQueue]);

  // Send entry point. While a turn is running, a follow-up is queued (drains
  // after the active turn). Otherwise it blocks on attachments still parsing —
  // the confirm dialog lets the user wait or submit with only the raw file.
  //
  // Routing MUST gate on the derived ``isBusy``, not the raw ``sending`` flag:
  // ``sending`` can stay stuck true after a missed terminal frame (the exact
  // case ``deriveTurnActive`` reconciles away for the DISPLAY). Gating here on
  // the raw flag made a follow-up on a visually-idle session silently detour
  // through the queue — the backend idle-kick drained it immediately (so it
  // ran), but a phantom queue bubble stayed pinned under the composer.
  const handleSend = () => {
    if (!draft.trim()) return;
    // Route on ``displayBusy`` (= isBusy OR an unpaused drain chain in
    // flight): between two drained items nothing is streaming (``isBusy``
    // false) but the backend still 409s a direct send (``is_draining_queue``
    // guards the gap) — a follow-up typed in that window must queue, exactly
    // as the Composer's queue affordance (also ``displayBusy``) advertises.
    if (displayBusy) {
      void performEnqueue();
      return;
    }
    if (attachmentsParsing) {
      setParsingConfirmOpen(true);
      return;
    }
    void performSend();
  };

  const handleInterrupt = async () => {
    const sessionId = selectedSessionId;
    if (!sessionId) return;
    const afterSeq = historyCursorRef.current;
    // Do NOT tear the session-lifetime stream down: the cancel tail
    // (partial assistant text, ``session.idle`` with the interrupt stop
    // reason) arrives on it, and follow-up turns keep streaming. The
    // explicit gap-fill below just renders the tail without waiting on
    // stream latency; uid dedup absorbs the overlap.
    try {
      const detail = await sessionsApi.interrupt(sessionId);
      const missed = await sessionsApi
        .listEvents(sessionId, afterSeq)
        .catch(() => ({ items: [] as SessionEventDTO[] }));
      if (selectedSessionIdRef.current !== sessionId) return;
      if (
        missed.items.some((event) => event.event.event_type === "message.user")
      ) {
        setPendingUserMessage(null);
      }
      for (const event of missed.items) {
        if (event.seq > 0) {
          historyCursorRef.current = Math.max(
            historyCursorRef.current,
            event.seq,
          );
        }
      }
      setEvents((prev) => {
        const shouldAddLocalInterrupt = !prev.some(isLocalUserInterruptEvent);
        const incoming = shouldAddLocalInterrupt
          ? [...missed.items, makeLocalUserInterruptEvent()]
          : missed.items;
        return appendUniqueEvents(prev, incoming);
      });
      const updatedSession = sessionDetailToListItem(detail);
      setSessions((prev) =>
        prev.some((s) => s.id === updatedSession.id)
          ? prev.map((s) => (s.id === updatedSession.id ? updatedSession : s))
          : [updatedSession, ...prev],
      );
      void fetchSidebarSessions();
      setSending(false);
      toast.success(t("conversation.interrupted" as Parameters<typeof t>[0]));
    } catch (cause) {
      toast.error(
        cause instanceof Error
          ? cause.message
          : t("conversation.interruptFailed" as Parameters<typeof t>[0]),
      );
    }
  };

  interruptRef.current = handleInterrupt;

  const handleRetry = useCallback(
    (turnId: string) => {
      const turn = turns.find((t) => t.id === turnId);
      if (!turn) return;
      setRetryCounts((prev) => {
        const next = { ...prev, [turnId]: (prev[turnId] ?? 0) + 1 };
        return next;
      });
      setDraft(turn.userText);
      setTimeout(() => void handleSend(), 0);
    },
    [turns, handleSend],
  );

  // ADR-013 v2 (kernel d008b53) — single dispatcher for all 4 user
  // verbs (``approve`` / ``approve_with_changes`` /
  // ``approve_for_session`` / ``reject``). Marks the entry as
  // submitting optimistically; the paired ``action_resolved`` SSE
  // frame from the kernel flips it to ``answered``. 409 conflicts
  // mean another reconnect already resolved this pending — we let
  // the incoming SSE settle the UI rather than rolling back here.
  const handleApprovalDecision = useCallback(
    (
      pendingId: string,
      decision:
        "approve" | "approve_with_changes" | "approve_for_session" | "reject",
      opts?: {
        message?: string;
        modifiedInput?: Record<string, unknown>;
      },
    ) => {
      const sessionId = selectedSessionId;
      if (!sessionId) return;
      setPendingApprovals((prev) =>
        prev.map((p) =>
          p.pendingId === pendingId ? { ...p, submitting: true } : p,
        ),
      );
      const request: {
        pending_id: string;
        decision: typeof decision;
        message?: string;
        modified_input?: Record<string, unknown>;
      } = {
        pending_id: pendingId,
        decision,
      };
      if (decision === "reject" && opts?.message && opts.message.length > 0) {
        request.message = opts.message;
      }
      if (decision === "approve_with_changes" && opts?.modifiedInput) {
        request.modified_input = opts.modifiedInput;
      }
      sessionsApi.submitAction(sessionId, request).catch((err: unknown) => {
        setPendingApprovals((prev) =>
          prev.map((p) =>
            p.pendingId === pendingId ? { ...p, submitting: false } : p,
          ),
        );
        toast.error(
          err instanceof Error
            ? err.message
            : t("common.saveFailed" as Parameters<typeof t>[0]),
        );
      });
    },
    [selectedSessionId, t],
  );

  const handleAskUserQuestionSubmit = useCallback(
    (toolId: string, answers: Record<string, string>) => {
      const sessionId = selectedSessionId;
      const pendingId = currentClarifyingPendingRef.current;
      if (!sessionId || !pendingId) {
        // No live pending — runtime has either advanced past this turn
        // (e.g. user reloaded after answer already resolved) or never
        // received it. Surface the error; nothing to submit.
        toast.error(t("common.error" as Parameters<typeof t>[0]));
        return;
      }
      // Optimistic swap: stash the answers locally so the renderer
      // flips to ``UserAnswerSummaryCard`` on this very tick. The
      // paired ``action_resolved`` SSE frame will land shortly and
      // ``askUserQuestionAnswersByToolId`` (event-derived) will take
      // precedence — same shape, just kernel-authoritative.
      setAskUserQuestionLocalAnswers((prev) => ({
        ...prev,
        [toolId]: answers,
      }));
      sessionsApi
        .submitAction(sessionId, {
          pending_id: pendingId,
          decision: "answer",
          answers,
        })
        .then(() => {
          // The kernel will emit a paired action_resolved event over
          // SSE which clears currentClarifyingPendingRef. The runtime
          // then resumes its turn and emits subsequent tool events.
        })
        .catch((err: unknown) => {
          // Submit failed — drop the optimistic answers so the
          // interactive card returns and the user can retry.
          setAskUserQuestionLocalAnswers((prev) => {
            const next = { ...prev };
            delete next[toolId];
            return next;
          });
          toast.error(
            err instanceof Error
              ? err.message
              : t("common.saveFailed" as Parameters<typeof t>[0]),
          );
        });
    },
    [selectedSessionId],
  );
  askUserQuestionSubmitRef.current = handleAskUserQuestionSubmit;

  const handleSwitchModel = useCallback((turnId: string) => {
    setRetryCounts((prev) => {
      const next = { ...prev, [turnId]: (prev[turnId] ?? 0) + 1 };
      return next;
    });
    setModelSelectorUnlocked(true);
  }, []);

  // Scroll to bottom
  const handleScrollToBottom = useCallback(() => {
    keepCurrentTurnAtTopRef.current = false;
    scrollContainerRef.current?.scrollTo({
      top: scrollContainerRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, []);

  const handleTurnListVirtualApiReady = useCallback(
    (api: { scrollToTurnTop: (index: number) => void } | null) => {
      turnListVirtualApiRef.current = api;
    },
    [],
  );

  // Show the scroll-to-bottom affordance whenever there's overflow not
  // currently in view — either because the user scrolled up (scroll
  // listener) or because the content grew past the viewport without any
  // user interaction (streaming output, new turns). The ResizeObserver
  // fires on the scroll container AND its inner content so we catch
  // both ``clientHeight`` shrinks and ``scrollHeight`` growths; without
  // observing the inner element, streaming text inflates scrollHeight
  // silently and the button only appears after the user nudges the
  // scroll position.
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const recompute = () => {
      const distanceFromBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight;
      setShowScrollBottom((prev) => {
        const next = distanceFromBottom > 120;
        return prev === next ? prev : next;
      });
    };
    recompute();
    el.addEventListener("scroll", recompute, { passive: true });
    // ResizeObserver watches the scroll container + first-child chain so
    // size growth from streaming text propagates here. The structure is:
    //   el (overflow-y-auto)
    //   └─ ConversationTurnList outer (mx-auto max-w-[760px] px-6)
    //      └─ virtualizer wrapper (style.height = totalSize)
    //         └─ virtual rows (absolutely positioned, don't affect flow)
    // Walk a few levels deep so the virtualizer-wrapper's inline-style
    // height change triggers recompute too.
    const ro = new ResizeObserver(recompute);
    let cursor: Element | null = el;
    for (let depth = 0; cursor && depth < 5; depth += 1) {
      ro.observe(cursor);
      cursor = cursor.firstElementChild;
    }
    // MutationObserver as a second source: turn-level fold/unfold and
    // segment expand/collapse add or remove DOM nodes inside virtual
    // rows. Those structural changes don't always reshape the outer
    // flow-positioned containers cleanly (rows are absolutely
    // positioned), so ResizeObserver can miss them. ``childList`` +
    // ``subtree`` fires whenever a segment / SegmentDetails body is
    // mounted or unmounted; we coalesce successive mutations into one
    // RAF tick to keep the work cheap during fast batches.
    let mutationScheduled = false;
    const scheduleMutationRecompute = () => {
      if (mutationScheduled) return;
      mutationScheduled = true;
      requestAnimationFrame(() => {
        mutationScheduled = false;
        recompute();
      });
    };
    const mo = new MutationObserver(scheduleMutationRecompute);
    mo.observe(el, { subtree: true, childList: true });
    // 250ms polling as a robustness fallback. SegmentDetails owns its own
    // ``open`` state — toggling it doesn't bubble a re-render to this page,
    // and the virtualizer's totalSize update chain (measureElement → cache
    // → re-render → outer wrapper resize → our RO) is async with RAF
    // boundaries that don't always line up with our recompute scheduling.
    // A coarse interval check guarantees the button visibility eventually
    // reflects reality even when observers race the layout.
    const pollInterval = window.setInterval(recompute, 250);
    return () => {
      el.removeEventListener("scroll", recompute);
      ro.disconnect();
      mo.disconnect();
      window.clearInterval(pollInterval);
    };
  }, []);

  // Track the scroll container's clientHeight. We size the latest
  // turn's ``min-height`` to this so a follow-up Send can snap the
  // user's new message to the viewport top in one commit — without it
  // ``scrollHeight - clientHeight`` clamps ``scrollTop`` and the
  // browser pins the new turn to the bottom instead. Re-measured via
  // ResizeObserver so window resizes and right-panel toggles keep the
  // layout consistent.
  const [containerHeight, setContainerHeight] = useState(0);
  useLayoutEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    setContainerHeight(el.clientHeight);
    const ro = new ResizeObserver(() => {
      setContainerHeight((prev) =>
        Math.abs(prev - el.clientHeight) < 4 ? prev : el.clientHeight,
      );
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useLayoutEffect(() => {
    if (!pinNextTurnToTopRef.current) return;
    if (effectiveTurns.length > 0) {
      turnListVirtualApiRef.current?.scrollToTurnTop(effectiveTurns.length - 1);
    }
    pinNextTurnToTopRef.current = false;
    setShowScrollBottom(false);
  }, [effectiveTurns.length, pendingUserMessage, containerHeight]);

  // Continuous scroll anchor for the latest turn while
  // ``keepCurrentTurnAtTopRef`` is active. The one-shot
  // ``scrollToTurnTop`` above pins the new turn at viewport top in the
  // first 8 frames after send, but layout settles asynchronously over
  // a much longer window:
  //   - markdown image/table inside an earlier turn finalising layout
  //     (RO fires seconds after the row mounted)
  //   - virtualizer measurement-cache key swap when the optimistic
  //     ``pending-turn`` is replaced by ``turn-X`` after ``message.user``
  //     echoes back
  //   - tail-spacer height recompute coupled with virtualizer wrapper
  //     resize on streaming content growth
  // Any of those can shift the latest turn's docY by tens or hundreds
  // of pixels; the user perceives "empty space above the new turn that
  // keeps growing during streaming".
  //
  // Strategy: capture the latest turn's docY at send time, then on
  // every layout change re-read it and shift ``scrollTop`` by the
  // delta so the docY ↔ viewport-top relationship stays fixed. Stops
  // on user-initiated scroll (wheel / touch / keyboard) so the
  // anchor doesn't fight the user's intent.
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    let lastTargetDocY: number | null = null;
    let anchoredIdx = -1;

    const release = () => {
      keepCurrentTurnAtTopRef.current = false;
      lastTargetDocY = null;
      anchoredIdx = -1;
    };

    const adjust = () => {
      if (!keepCurrentTurnAtTopRef.current) {
        lastTargetDocY = null;
        anchoredIdx = -1;
        return;
      }
      const lastIdx = effectiveTurnsRef.current.length - 1;
      if (lastIdx < 0) return;
      // A new send appends a turn → ``lastIdx`` advances. The OLD
      // baseline (``lastTargetDocY`` captured against the previous
      // latest turn) is meaningless against the NEW target — using
      // it would compute a delta in the hundreds and shove
      // ``scrollTop`` back where the new turn should NOT be. Re-arm
      // the baseline so the next adjust call captures the new
      // target's docY (which by then ``scrollToTurnTop`` has already
      // pinned to viewport top).
      if (lastIdx !== anchoredIdx) {
        anchoredIdx = lastIdx;
        lastTargetDocY = null;
      }
      const target = container.querySelector(
        `[data-index="${lastIdx}"]`,
      ) as HTMLElement | null;
      if (!target) return;
      const containerRect = container.getBoundingClientRect();
      const targetRect = target.getBoundingClientRect();
      const docY = container.scrollTop + (targetRect.top - containerRect.top);
      if (lastTargetDocY === null) {
        lastTargetDocY = docY;
        return;
      }
      const delta = docY - lastTargetDocY;
      if (Math.abs(delta) < 1) return;
      container.scrollTop += delta;
      // After the scroll commits the target is back at the anchored
      // viewport position, so the ``docY`` we just observed becomes the
      // new reference point.
      lastTargetDocY = docY;
    };

    const ro = new ResizeObserver(adjust);
    let cursor: Element | null = container;
    for (let depth = 0; cursor && depth < 5; depth += 1) {
      ro.observe(cursor);
      cursor = cursor.firstElementChild;
    }
    let mutationScheduled = false;
    const scheduleMutationAdjust = () => {
      if (mutationScheduled) return;
      mutationScheduled = true;
      requestAnimationFrame(() => {
        mutationScheduled = false;
        adjust();
      });
    };
    const mo = new MutationObserver(scheduleMutationAdjust);
    mo.observe(container, { subtree: true, childList: true });

    // User-initiated scroll cancels the anchor. Distinguish from our
    // own programmatic ``scrollTop +=`` writes by gating on the
    // physical input events rather than the ``scroll`` event itself.
    container.addEventListener("wheel", release, { passive: true });
    container.addEventListener("touchstart", release, { passive: true });
    const onKey = (e: KeyboardEvent) => {
      if (
        e.key === "PageDown" ||
        e.key === "PageUp" ||
        e.key === "ArrowUp" ||
        e.key === "ArrowDown" ||
        e.key === "Home" ||
        e.key === "End"
      ) {
        release();
      }
    };
    container.addEventListener("keydown", onKey);

    return () => {
      ro.disconnect();
      mo.disconnect();
      container.removeEventListener("wheel", release);
      container.removeEventListener("touchstart", release);
      container.removeEventListener("keydown", onKey);
    };
  }, []);

  // Mirror ``effectiveTurns`` into a ref so the anchor effect (set up
  // once on mount) can read the live last-index without re-subscribing.
  const effectiveTurnsRef = useRef(effectiveTurns);
  useEffect(() => {
    effectiveTurnsRef.current = effectiveTurns;
  }, [effectiveTurns]);

  // Restore scroll position after the upward pager prepended events.
  // Without this the browser keeps ``scrollTop`` constant while the new
  // content pushes existing items downward — visually the user sees a
  // sudden jump and loses their place.
  //
  // ``pendingScrollAnchorRef`` is set inside ``loadOlderTurns`` right
  // before the ``setEvents`` that prepends. After React commits and
  // reflows, ``scrollHeight`` reflects the new total height; we add the
  // delta to ``scrollTop`` to keep the previously-visible row in the
  // same screen position. Guard on the ref so this no-ops for SSE
  // appends and other ``events`` updates.
  useLayoutEffect(() => {
    const anchor = pendingScrollAnchorRef.current;
    if (!anchor) return;
    const el = scrollContainerRef.current;
    if (el) {
      const delta = el.scrollHeight - anchor.oldScrollHeight;
      el.scrollTop = anchor.oldScrollTop + delta;
    }
    pendingScrollAnchorRef.current = null;
  }, [events.length]);

  // First-real-scroll detector. Listens for ``wheel`` and ``keydown``
  // events on the scroll container — these fire only on user-initiated
  // scrolls. Native ``scroll`` events are unreliable here because both
  // programmatic scrolling (auto-scroll-to-bottom on initial load,
  // scroll-anchor restoration after prepend) and ResizeObserver
  // re-measurements emit them, which would falsely flip the flag during
  // the initial-mount race we're guarding against.
  //
  // Re-attached when the session changes so a freshly-loaded session
  // starts back at "needs first real scroll".
  useEffect(() => {
    userScrolledRef.current = false;
    const el = scrollContainerRef.current;
    if (!el) return;
    const handler = () => {
      userScrolledRef.current = true;
    };
    el.addEventListener("wheel", handler, { passive: true });
    el.addEventListener("keydown", handler);
    el.addEventListener("touchmove", handler, { passive: true });
    return () => {
      el.removeEventListener("wheel", handler);
      el.removeEventListener("keydown", handler);
      el.removeEventListener("touchmove", handler);
    };
  }, [selectedSessionId]);

  // Top sentinel observer — when it enters the scroll viewport (rootMargin
  // pulls the trigger down by 200 px so we start fetching just before
  // the user actually hits the top), kick off the next page of older
  // turns.
  //
  // ``hasMoreOlder`` is in the deps because the sentinel JSX is gated on
  // it: on first mount the sentinel isn't in the DOM yet (initial load
  // is in flight), so ``topSentinelRef.current`` is null and the effect
  // early-returns. Once the API resolves and ``hasMoreOlder`` flips
  // true the sentinel renders, the effect re-runs, and the observer
  // finally attaches. Without this dep the observer would never bind to
  // the post-load sentinel and pagination would silently never fire.
  useEffect(() => {
    const sentinel = topSentinelRef.current;
    const scroller = scrollContainerRef.current;
    if (!sentinel || !scroller) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          void loadOlderTurns();
        }
      },
      {
        root: scroller,
        rootMargin: "200px 0px 0px 0px",
        threshold: 0,
      },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [loadOlderTurns, hasMoreOlder]);

  // Auto-scroll during streaming — debounced + rAF batched so a burst of
  // SSE deltas (one per token, often 30+/sec from Claude) coalesces into
  // at most one scroll per 120ms. Use an instant jump for automatic
  // following: historical DB catch-up can append several events at once,
  // and smooth scrolling those updates makes the page visibly replay the
  // whole transcript before landing at the bottom.
  //
  // The two-RAF wait is preserved so the scroll waits for React to flush
  // the new blocks AND the browser to measure ``scrollHeight`` — without
  // it the very first historical-events batch can pin the viewport at
  // the top instead of the latest message.
  const scrollSettleTimerRef = useRef<number | null>(null);
  const scrollLastFiredRef = useRef(0);
  useEffect(() => {
    if (
      !scrollContainerRef.current ||
      showScrollBottom ||
      keepCurrentTurnAtTopRef.current
    )
      return;
    const el = scrollContainerRef.current;

    const fire = () => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          el.scrollTop = el.scrollHeight;
          scrollLastFiredRef.current = performance.now();
        });
      });
    };

    const elapsed = performance.now() - scrollLastFiredRef.current;
    const SCROLL_DEBOUNCE_MS = 120;

    if (elapsed >= SCROLL_DEBOUNCE_MS) {
      fire();
    } else if (scrollSettleTimerRef.current === null) {
      scrollSettleTimerRef.current = window.setTimeout(() => {
        scrollSettleTimerRef.current = null;
        fire();
      }, SCROLL_DEBOUNCE_MS - elapsed);
    }

    return () => {
      if (scrollSettleTimerRef.current !== null) {
        window.clearTimeout(scrollSettleTimerRef.current);
        scrollSettleTimerRef.current = null;
      }
    };
  }, [events, showScrollBottom]);

  // Initial mount / session switch: jump to bottom once the conversation
  // finishes loading the historical replay, even if ``events`` was already
  // populated before the container mounted.
  useEffect(() => {
    pinNextTurnToTopRef.current = false;
    keepCurrentTurnAtTopRef.current = false;
    if (!scrollContainerRef.current) return;
    const el = scrollContainerRef.current;
    const r = requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight });
    });
    return () => cancelAnimationFrame(r);
  }, [selectedSessionId]);

  // Entering a conversation that's already RUNNING: land on the live bottom so
  // the user sees the streaming output, not the top. The generic entry scroll
  // above fires a single rAF on session change, but a running session is still
  // replaying history / streaming, so its content settles over several frames —
  // and once it paints, ``showScrollBottom`` can latch true (first paint at the
  // top), which blocks the events-follow effect and strands the viewport up top.
  // A short burst of scroll-to-bottom + latch-clear across the load window pins
  // it to the newest message; the normal follow takes over afterwards.
  //
  // Only the FIRST status observation after entering a session counts (tracked
  // per session id): if it's already running, jump; otherwise leave scrolling to
  // the generic entry effect + the send-time pin-to-top, so a later idle→running
  // from the user's OWN send isn't yanked to the bottom.
  // Open every conversation on its newest message — running or ended alike. The
  // generic entry scroll above fires a single rAF on session change, but the
  // transcript settles over later frames (history replay / streaming), and once
  // it paints ``showScrollBottom`` can latch true (first paint at the top),
  // blocking the events-follow effect and stranding the viewport up top. Burst
  // scroll-to-bottom + latch-clear across the settle window guarantees the
  // bottom. Gated to the FIRST transcript load per session id via the ref, so a
  // later send / streaming delta (which also bumps ``events.length``) doesn't
  // re-trigger it and fight the send-time pin-to-top.
  const entryScrolledRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedSessionId) return;
    if (entryScrolledRef.current === selectedSessionId) return;
    if (events.length === 0) return; // wait for the transcript window to load
    entryScrolledRef.current = selectedSessionId;
    if (!scrollContainerRef.current) return;
    let cancelled = false;
    const jump = () => {
      const node = scrollContainerRef.current;
      if (cancelled || !node) return;
      // The burst exists to survive the multi-frame settle of the initial
      // transcript paint — not to fight the user. Once a real scroll gesture
      // has landed (wheel/keydown/touchmove; the ref resets on session
      // switch), the user owns the viewport: a late timer (up to 1s) yanking
      // them back to the bottom reads as the page "snapping away" from the
      // history they just scrolled up to.
      if (userScrolledRef.current) return;
      node.scrollTop = node.scrollHeight;
      setShowScrollBottom(false);
    };
    const raf = requestAnimationFrame(jump);
    const timers = [120, 300, 600, 1000].map((ms) =>
      window.setTimeout(jump, ms),
    );
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      timers.forEach((tm) => window.clearTimeout(tm));
    };
  }, [selectedSessionId, events.length]);

  // Mirror the kernel session's locked model/provider into the composer's
  // selector state so the UI shows what the session is actually using
  // (V5 freezes the model at session creation — picking a different one
  // mid-conversation would be a no-op anyway). Without this sync the
  // picker stays at whatever the page initialised to, which is typically
  // NOT the model the session was created with from the project page.
  useEffect(() => {
    if (!selectedSession) return;
    // Sync the composer selector to whatever the kernel locked at session
    // creation. Both ids must come from the session (not just locked_model_id)
    // — the composer matches on (providerId, modelId) pairs, so a missing
    // provider id makes the selector silently fall back to the project
    // default's display label even when the session is wired to a different
    // model end-to-end.
    if (selectedSession.locked_provider_id) {
      setSelectedProviderId(selectedSession.locked_provider_id);
    }
    if (selectedSession.locked_model_id) {
      setSelectedModelId(selectedSession.locked_model_id);
    }
    // REP-107: also sync the runtime selector to the session's frozen
    // runtime_provider. Without this the page-level ``selectedRuntimeId``
    // state leaks across session switches — switching from a Claude
    // Agent session to a Valuz Agent one would keep showing "Claude
    // Agent" until the user manually clicked the picker.
    if (selectedSession.runtime_provider) {
      setSelectedRuntimeId(selectedSession.runtime_provider as RuntimeId);
    }
    // ADR-013: reconcile the permission selector to the live session.
    if (selectedSession.permission_mode) {
      setSelectedPermissionMode(
        selectedSession.permission_mode as
          "default" | "auto_review" | "full_access",
      );
    }
    // Kernel V5+bba3014: reconcile the effort selector to the live
    // session so live-reconcile PATCHes start from the persisted value.
    setSelectedEffort(
      (selectedSession.effort as
        "low" | "medium" | "high" | "xhigh" | "max" | null | undefined) ?? null,
    );
  }, [
    selectedSession?.id,
    selectedSession?.locked_model_id,
    selectedSession?.locked_provider_id,
    selectedSession?.runtime_provider,
    selectedSession?.permission_mode,
    selectedSession?.effort,
  ]);

  // Reset per-session optimistic state on a real session→session switch.
  // The session-open effect below supersedes the previous stream on its own
  // (``subscribeToSession`` aborts the prior controller), so the only job
  // left here is releasing ``sending`` — the new session must not inherit
  // the previous one's click→turn-start pending flag. The ``null → id``
  // transition (the ``/conversation/new`` promotion) is skipped: the pending
  // flag there belongs to the send that minted the session.
  const prevSelectedSessionIdRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevSelectedSessionIdRef.current;
    prevSelectedSessionIdRef.current = selectedSessionId;
    if (prev === null || prev === selectedSessionId) return;
    setSending(false);
  }, [selectedSessionId]);

  // SESSION-OPEN effect — the ONE owner of the data-plane stream
  // (docs/design/session-stream-lifetime.md). Opening a session:
  //   1. hydrates ``todos`` from the canonical detail (persistent snapshot);
  //   2. opens the session-lifetime stream. It carries EVERY turn for as long
  //      as the page stays here — resumed mid-turn sessions, fresh sends,
  //      drained queue items, scheduled turns, bg-task wake-ups — so the old
  //      created→running control-plane bridge, the finished-turn reconcile
  //      and the "subscribe only while running" dance are all gone: an open
  //      stream on an idle session is harmless (it parks on server
  //      heartbeats; busy is derived from events + status, not from the
  //      stream being open).
  // The stream is superseded by the next session's open (subscribeToSession
  // aborts the previous controller) and torn down on unmount.
  //
  // CONNECTION-BUDGET guard: Chromium caps ~6 connections per origin
  // (HTTP/1.1), and held SSE streams count against it — a pool exhaustion
  // blocks every fetch (the verified white-screen incident class). A hidden
  // tab / minimized window doesn't need live paint, so release the held
  // stream on ``visibilitychange: hidden`` and reopen on return — the
  // (re)open path resumes from the history cursor and the server's initial
  // drain + reconcile burst deliver everything missed while hidden. This
  // keeps the always-open model's steady-state cost scoped to the ONE
  // visible conversation tab.
  useEffect(() => {
    if (!selectedSessionId) return;
    const sid = selectedSessionId;
    let cancelled = false;
    sessionsApi
      .get(sid)
      .then((detail) => {
        if (cancelled) return;
        if (detail.todos !== undefined && detail.todos !== null) {
          setTodos(detail.todos);
        }
      })
      .catch(() => {
        // Non-fatal — refreshEvents already hydrated todos from the
        // historical event log.
      });
    if (document.visibilityState !== "hidden") {
      subscribeToSession(sid, historyCursorRef.current);
    }
    const onVisibility = () => {
      if (cancelled) return;
      if (document.visibilityState === "hidden") {
        if (abortRef.current) {
          abortRef.current.abort();
          abortRef.current = null;
        }
      } else if (selectedSessionIdRef.current === sid && !abortRef.current) {
        subscribeToSession(sid, historyCursorRef.current);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [selectedSessionId, subscribeToSession]);

  // Tear down any in-flight SSE subscription when the page unmounts.
  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
    };
  }, []);

  // Context panel
  const contextPanelNode = useMemo(() => {
    if (isSkillCreatorMode) {
      return (
        <SkillStagingPanel
          slugs={stagingSlugs.map((s) => ({
            slug: s.slug,
            name: s.name,
            description: s.description,
            fileCount: s.file_count,
            totalBytes: s.total_bytes,
            conflictKind: s.conflict_kind,
            suggestedStrategy: s.suggested_strategy,
            suggestedNewSlug: s.suggested_new_slug,
            sourceSkillId: s.source_skill_id,
            version: s.version,
            files: s.files?.map((f) => ({
              path: f.path,
              type: f.type,
              size: f.size ?? null,
            })),
          }))}
          refreshing={stagingRefreshing}
          syncing={stagingSyncing}
          onRefresh={() => void refreshStaging()}
          onSync={(items) => void handleSyncStaging(items)}
          onLoadFile={async (slug, path) => {
            const res = await skillsApi.readStagingFile(id, slug, path);
            return res.content;
          }}
        />
      );
    }
    // Unified panel for both project and chat projects. Chat (kind="chat")
    // is treated as a "special project" — same component, same visual style,
    // but with KB binding / project instructions / file tree / scheduled
    // tasks omitted. The panel's section visibility is data-driven: pass
    // ``undefined`` for sections that don't apply.
    const isProject = activeProject?.kind === "project";
    // 09-assistant Phase B: 临时对话 collapses to a 2-column layout — no right
    // context panel until/unless a live session exists. Returning ``null``
    // makes the layout drop the aside column (ProjectLayoutBase reserves it
    // only when ``resolvedRightPanel`` is truthy). Flipping the 📁 chip back
    // to 临时 recomputes this to ``null`` and the column slides away. A live
    // chat session keeps the panel so its generated files / todos stay
    // reachable.
    if (!isProject && !selectedSession) {
      return null;
    }
    // Merge server-stored attachments (canonical) with locally-queued File
    // objects (not yet uploaded). Server side dedupes by filename if the user
    // queued the same name; keeping both keeps the panel honest before send.
    // Local + KB attachments, each with its live parse status so the panel
    // shows a "解析中" indicator while the backend parses (uploads happen on
    // attach now, so there is no separate not-yet-uploaded queue to merge).
    const uploadedFiles: UploadedFileItem[] = sessionAttachments.map((a) => ({
      id: a.id,
      name: a.filename,
      size: formatFileSize(a.size_bytes),
      sourceKind: a.source_kind,
      parseStatus: a.parse_status as
        "parsing" | "ready" | "failed" | "native" | undefined,
    }));
    // Agent-delivered artifacts → the curated "生成文件" panel section.
    const generatedFiles = sessionArtifacts.map((a) => ({
      id: a.id,
      name: a.file_name,
      size: formatFileSize(a.file_size),
      path: a.file_path,
    }));
    // Always render the panel — even when it has nothing in it — so the
    // right-side toggle button stays visible on every conversation page.
    // The layout hides the panel column when the user collapses it; the
    // user explicitly asked for the toggle to remain reachable so they
    // can re-expand later (and so they can attach a file via the empty
    // upload section before a session has produced any content).

    const handlePanelUpload = () => {
      const input = document.createElement("input");
      input.type = "file";
      input.multiple = true;
      input.style.display = "none";
      input.addEventListener("change", () => {
        const files = Array.from(input.files ?? []);
        // Upload-on-attach (same pipeline as the composer): the file uploads
        // immediately and the panel polls its parse status.
        if (files.length > 0) handleLocalFilesAttach(files);
        input.remove();
      });
      document.body.appendChild(input);
      input.click();
    };

    // All rows are persisted (uploaded on attach), so removal always routes
    // through the shared row-delete handler.
    const handleRemoveUploadedFile = (fileId: string) => {
      handleRemoveSessionAttachment(fileId);
    };

    return (
      <ProjectDetailContextPanel
        // Conversation page intentionally omits ``instructions`` so the
        // panel hides the "Instructions" card. The instructions surface lives
        // on the project home page only — duplicating it here was
        // confusing for chat (non-project) sessions, where there's no
        // owning project to edit, and noisy on every project session
        // for a value the user can't change from this page anyway.
        uploadedFiles={uploadedFiles}
        onUploadFile={handlePanelUpload}
        onRemoveUploadedFile={handleRemoveUploadedFile}
        // Agent-delivered deliverables (生成文件) — shown in both chat and
        // project sessions; rows open in the in-app artifact viewer.
        generatedFiles={generatedFiles}
        onOpenGeneratedFile={(path) => void openArtifactFile(path)}
        // KB binding tree — project sessions only, **read-only**: we
        // pass ``kbTree`` + ``bindings`` (so the checkbox state shows
        // which folders/files are bound) and ``onExpandKbFolder`` (so
        // the user can drill in to inspect), but deliberately omit
        // ``onToggleBinding`` — editing happens on the project detail
        // page and takes effect for the next session. Chat /
        // skill-creator projects pass ``undefined`` so the section
        // doesn't render at all.
        kbTree={isProject ? projectKbTree : undefined}
        bindings={isProject ? projectKbBindings : undefined}
        onExpandKbFolder={isProject ? handleExpandProjectKbFolder : undefined}
        fileTree={fileTree}
        fileTreeTitle={
          isProject
            ? t("project.fileTree" as Parameters<typeof t>[0])
            : // Chat sessions: the curated "生成文件" section now owns that
              // label (agent-delivered artifacts), so the raw cwd file tree
              // uses the neutral "文件" title to avoid two identical headers.
              t("conversation.files" as Parameters<typeof t>[0])
        }
        fileTreeInTab={isProject}
        rootPath={
          // A worktree session's tree is rooted at its checkout — show that
          // path so reveal / relative-path stripping match what's listed.
          activeWorktree?.path ??
          (isProject
            ? ((activeProject as ProjectDetail)?.root_path ?? undefined)
            : t("conversation.workDir" as Parameters<typeof t>[0]))
        }
        onOpenInFinder={() => {
          const ws = activeProject as ProjectDetail | null;
          // Prefer the worktree checkout for a worktree session; else the
          // kernel-resolved cwd (project + chat both have one); fall back to
          // root_path so a stale backend that hasn't populated ``cwd`` yet
          // still works for project projects.
          const path = activeWorktree?.path ?? ws?.cwd ?? ws?.root_path;
          if (!path) {
            toast.info(t("conversation.noWorkDir" as Parameters<typeof t>[0]));
            return;
          }
          void revealInFinder(path);
        }}
        onFileClick={(relPath) => {
          void openArtifactFile(relPath);
        }}
        onFileDoubleClick={(relPath) => {
          void openArtifactFile(relPath);
        }}
        onOpenInSystem={(relPath) => {
          void revealInFinder(
            resolveConversationArtifactPath(relPath, activeProjectRootPath),
          );
        }}
        onRefreshFiles={refreshFileTree}
        collapsed={panelCollapsed}
        onCollapsedChange={(c) => panelSetCollapsed(c)}
        todos={todos}
      />
    );
  }, [
    activeProject,
    fileTree,
    activeProjectRootPath,
    openArtifactFile,
    panelCollapsed,
    panelSetCollapsed,
    selectedComposerSkill,
    availableSkills,
    isSkillCreatorMode,
    stagingSlugs,
    stagingRefreshing,
    stagingSyncing,
    refreshStaging,
    sessionAttachments,
    sessionArtifacts,
    revealInFinder,
    handleRemoveSessionAttachment,
    handleSyncStaging,
    todos,
    id,
    navigate,
    selectedSession,
  ]);

  // The conversation page renders its own header inline (see JSX below) so the
  // scroll container can run edge-to-edge of the main card and the scrollbar
  // sits flush against the bordered card edge. We therefore tell the layout to
  // hide its header slot for this page; the layout still owns the right panel
  // and aside width.
  useEffect(() => {
    setHideHeader(true);
    setRightPanel(contextPanelNode);
    return () => {
      setHideHeader(false);
      setRightPanel(null);
      setHeader(null);
      // NOTE: don't reset setRightPanelCollapsed here — this effect re-runs
      // every time contextPanelNode changes, and contextPanelNode itself
      // depends on rightPanelCollapsed. Resetting in cleanup creates a
      // feedback loop that snaps the panel back to expanded immediately.
    };
  }, [contextPanelNode, setRightPanel, setHeader, setHideHeader]);

  // Synchronously reset all panel-driving data on session change.
  // ``fileTree`` / ``sessionAttachments`` / ``attachments`` are all
  // refreshed asynchronously (per-project fetch, per-session fetch,
  // user-queued composer state) so without this the right-panel
  // collapsed effect below would run a SECOND time with the previous
  // session's stale ``hasData = true`` and edge-trigger an unwanted
  // expand on the new empty session. Each downstream effect re-fetches
  // its own slice for the new session, so this only clears the visible
  // surface — no data is lost. ``todos`` is already cleared
  // synchronously by ``refreshEvents``.
  useEffect(() => {
    if (skipNextSessionStateResetRef.current) {
      skipNextSessionStateResetRef.current = false;
      return;
    }
    setSessionAttachments([]);
    setFileTree([]);
    closeArtifact();
  }, [closeArtifact, selectedSessionId, setSessionAttachments]);

  // Drive the right-panel collapsed state from per-session data:
  //   * Project projects always have meaningful panel content
  //     (instructions / skills / KB / file tree) — open by default,
  //     and re-open on every session switch within the project.
  //   * Chat projects start collapsed and auto-expand on the
  //     empty → has-data edge (todos / attachments / files). Manual
  //     collapses survive subsequent growth (we only edge-trigger).
  const isProjectProject = activeProject?.kind === "project";
  const prevSessionIdRef = useRef<string | null>(null);
  const prevHasDataRef = useRef(false);
  useEffect(() => {
    if (!shouldRevealPanelFromNavigation) return;
    revealPanelOnSessionChangeRef.current = true;
    panelSetCollapsed(false);
    navigate(`${location.pathname}${location.search}`, {
      replace: true,
      state: null,
    });
  }, [
    shouldRevealPanelFromNavigation,
    panelSetCollapsed,
    navigate,
    location.pathname,
    location.search,
  ]);

  useEffect(() => {
    const sid = selectedSessionId ?? "new";
    if (prevSessionIdRef.current !== sid) {
      const revealPanel = revealPanelOnSessionChangeRef.current;
      const waitingForRoutedSession =
        revealPanel && !selectedSessionId && id !== NEW_SESSION_ID;
      if (waitingForRoutedSession) {
        prevSessionIdRef.current = sid;
        prevHasDataRef.current = false;
        panelSetCollapsed(false);
        return;
      }
      revealPanelOnSessionChangeRef.current = false;
      prevSessionIdRef.current = sid;
      prevHasDataRef.current = false;
      panelSetCollapsed(!selectedSessionId);
      return;
    }
    if (isProjectProject) return;
    const hasData =
      (todos?.length ?? 0) > 0 ||
      sessionAttachments.length > 0 ||
      fileTree.length > 0;
    if (hasData && !prevHasDataRef.current) {
      panelSetCollapsed(false);
    }
    prevHasDataRef.current = hasData;
  }, [
    selectedSessionId,
    id,
    isProjectProject,
    todos,
    sessionAttachments,
    fileTree,
    panelSetCollapsed,
  ]);

  const artifactViewerOpen = Boolean(
    selectedArtifactPath || artifactLoading || artifactError,
  );

  return (
    <>
      <div className="relative flex h-full min-h-0 flex-col bg-surface">
        {/* Page header — rendered inline so the scroll container below can run
            edge-to-edge of the main card and its scrollbar sits flush against
            the bordered card edge. Layout-level header is hidden via
            setHideHeader(true). */}
        <header className="flex h-12 shrink-0 items-center px-5">
          <div className="flex w-full items-center justify-between">
            <div className="flex min-w-0 items-center gap-2">
              {fromTaskId ? (
                <>
                  <button
                    type="button"
                    onClick={() =>
                      navigate(`/tasks/${encodeURIComponent(fromTaskId)}`)
                    }
                    className="inline-flex shrink-0 items-center gap-1 text-[13px] text-ink-meta transition-colors hover:text-ink-heading"
                  >
                    <ArrowLeft className="h-3.5 w-3.5" />
                    <span>
                      {t("conversation.backToTask" as Parameters<typeof t>[0])}
                    </span>
                  </button>
                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
                </>
              ) : null}
              {isSkillCreatorMode ? (
                <Badge variant="metaBrand" className="shrink-0">
                  <Sparkles className="h-3 w-3" />
                  Skill Creator
                </Badge>
              ) : null}
              {headerTitle ? (
                titleRenaming ? (
                  // Inline rename. Confirm on Enter / blur, cancel on Esc.
                  // No menu accessible while editing so the click target
                  // stays clean. ``autoFocus`` + ``select()`` via the ref
                  // would race with the dropdown's close-auto-focus on
                  // some browsers — we open rename via menu select, which
                  // already does ``e.preventDefault()`` on close, so a
                  // plain ``autoFocus`` is enough.
                  <input
                    autoFocus
                    value={titleRenameValue}
                    onChange={(e) => setTitleRenameValue(e.target.value)}
                    onBlur={() => {
                      const trimmed = titleRenameValue.trim();
                      if (trimmed && trimmed !== selectedSession?.name) {
                        sessionsApi
                          .rename(selectedSessionId!, trimmed)
                          .then(() => {
                            toast.success(
                              t("sidebar.renamed" as Parameters<typeof t>[0]),
                            );
                            void refreshActiveSession(selectedSessionId);
                          })
                          .catch(() =>
                            toast.error(
                              t(
                                "sidebar.renameFailed" as Parameters<
                                  typeof t
                                >[0],
                              ),
                            ),
                          );
                      }
                      setTitleRenaming(false);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        (e.target as HTMLInputElement).blur();
                      } else if (e.key === "Escape") {
                        e.preventDefault();
                        setTitleRenaming(false);
                      }
                    }}
                    onFocus={(e) => e.currentTarget.select()}
                    style={
                      titleRenameWidth !== null
                        ? { width: titleRenameWidth }
                        : undefined
                    }
                    className="min-w-0 border-0 border-b border-brand bg-transparent px-1 text-sm font-medium text-ink-heading outline-none"
                  />
                ) : (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        ref={titleTriggerRef}
                        disabled={!selectedSessionId}
                        className="flex min-w-0 items-center gap-1 rounded-md px-1 py-0.5 text-sm font-medium text-ink-heading transition-colors hover:bg-surface-soft focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand/30 disabled:cursor-default disabled:hover:bg-transparent"
                      >
                        <span className="truncate">{headerTitle}</span>
                        <ChevronDown
                          className="h-3.5 w-3.5 shrink-0 text-ink-muted"
                          strokeWidth={2}
                          aria-hidden
                        />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent
                      // Anchor under the chevron (right end of the
                      // trigger), not the title's left edge — otherwise
                      // the menu drops down far away from where the user
                      // clicked.
                      align="end"
                      className="min-w-[160px]"
                      onCloseAutoFocus={(e) => e.preventDefault()}
                    >
                      <DropdownMenuItem
                        onSelect={() => {
                          // Snapshot the trigger's current width so the
                          // input occupies the same horizontal space the
                          // title button just had — otherwise the input
                          // expands to the row's max width and shoves the
                          // sibling status pills around.
                          const w =
                            titleTriggerRef.current?.getBoundingClientRect()
                              .width ?? null;
                          setTitleRenameWidth(w);
                          setTitleRenameValue(
                            selectedSession?.name ?? headerTitle,
                          );
                          setTitleRenaming(true);
                        }}
                      >
                        <FilePenLine />
                        {t("sidebar.rename" as Parameters<typeof t>[0])}
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => setTitleDeleting(true)}
                      >
                        <Trash2 />
                        {t("common.delete" as Parameters<typeof t>[0])}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )
              ) : null}
              <SessionStatusPill
                status={selectedSession?.status}
                cancelled={
                  effectiveTurns[effectiveTurns.length - 1]?.cancelled === true
                }
                pending={effectiveTurns.length === 0}
              />
              {/* Execution origin (multi-target editions): where this
                  session's backend lives. Locked at creation; renders
                  nothing on single-target builds. */}
              <OriginBadge entityId={selectedSessionId} kind="session" />
              {sessionAgentSlug ? (
                <Badge variant="metaBrand" className="shrink-0">
                  <Bot className="h-3 w-3" />
                  {agentNameBySlug.get(sessionAgentSlug) ?? sessionAgentSlug}
                </Badge>
              ) : null}
              {selectedSession?.worktree ? (
                // Worktree attribution (creation-time snapshot). Greys out
                // when the worktree no longer exists on disk — the next
                // send self-heals by recreating it, which the tooltip says.
                <Badge
                  variant="metaOutline"
                  className={cn(
                    "shrink-0",
                    selectedSession.worktree.exists === false && "opacity-60",
                  )}
                  title={
                    selectedSession.worktree.exists === false
                      ? t("conversation.worktreeBadgeGone")
                      : t("conversation.worktreeBadgeHint")
                  }
                >
                  <GitBranch className="h-3 w-3" />
                  {selectedSession.worktree.branch ??
                    selectedSession.worktree.name}
                </Badge>
              ) : null}
              {activeProject?.name && !isSkillCreatorMode ? (
                <Badge variant="metaOutline" className="shrink-0">
                  {activeProject.name}
                </Badge>
              ) : null}
            </div>
          </div>
        </header>

        {shouldShowNoModelEmptyState({
          isNewConversation: id === NEW_SESSION_ID,
          pageLoading: loading,
          providerCount: providers.length,
          providerStatus: providerChannelState.status,
        }) ? (
          <div className="flex flex-1 items-center justify-center p-8">
            <EmptyState
              icon={<Settings />}
              title={t("conversation.noModel" as Parameters<typeof t>[0])}
              message={t("conversation.noModelHint" as Parameters<typeof t>[0])}
              action={
                <Button
                  type="button"
                  size="sm"
                  variant="default"
                  onClick={() => navigate("/settings")}
                >
                  {t("conversation.goToSettings" as Parameters<typeof t>[0])}
                </Button>
              }
            />
          </div>
        ) : (
          <>
            <div
              ref={scrollContainerRef}
              className="min-h-0 flex-1 overflow-y-auto bg-surface pt-0 pb-7"
            >
              {/* Top sentinel — visible "Load older" pill that doubles
                  as the IntersectionObserver target. Two ways to fetch the
                  next page:
                    1. Click the pill (explicit affordance — what users
                       expect when they realise there's earlier history)
                    2. Scroll to the top with the wheel/trackpad — the
                       IntersectionObserver fires once the pill enters the
                       viewport (``rootMargin`` 200 px so the fetch starts
                       a hair before the user hits the literal top)
                  The pill text swaps to a loader during the in-flight
                  fetch. Whole element disappears when ``hasMoreOlder``
                  flips false so the observer stops firing past the
                  start of history. */}
              {hasMoreOlder || loadingOlder ? (
                <div className="flex justify-center py-2">
                  <button
                    ref={topSentinelRef}
                    type="button"
                    onClick={() => {
                      // Manual click bypasses the "user must scroll first"
                      // gate the observer needs — the click itself IS the
                      // user signal.
                      userScrolledRef.current = true;
                      void loadOlderTurns();
                    }}
                    disabled={loadingOlder}
                    className="rounded-full border border-surface-border bg-surface px-3 py-1 text-2xs text-ink-body shadow-xs transition-colors hover:bg-surface-soft disabled:cursor-default disabled:opacity-60"
                  >
                    {loadingOlder
                      ? `${t("conversation.loadOlder" as Parameters<typeof t>[0])}…`
                      : `↑ ${t("conversation.loadOlder" as Parameters<typeof t>[0])}`}
                  </button>
                </div>
              ) : null}
              <ConversationTurnList
                // Remount on true session switches so the virtualizer's
                // internal state starts fresh. The /conversation/new → real-id
                // promotion keeps this key stable so the first sent turn
                // doesn't look like a page refresh.
                key={conversationInstanceKey}
                turns={effectiveTurns}
                scrollContainerRef={scrollContainerRef}
                sending={displayBusy}
                loading={id === NEW_SESSION_ID ? false : loading}
                error={error}
                onRetry={handleRetry}
                onSwitchModel={handleSwitchModel}
                retryCounts={retryCounts}
                lastTurnMinHeight={
                  effectiveTurns.length > 1 ? containerHeight : 0
                }
                skillsBySlug={skillsBySlug}
                onVirtualApiReady={handleTurnListVirtualApiReady}
                renderToolCall={renderToolCall}
                isToolCardFoldable={isToolCardFoldable}
                onRevealFile={revealInFinder}
                isLocalFileHref={localFileLinks.isLocalFileHref}
                onLocalFileLinkClick={localFileLinks.openLocalFileHref}
                emptySuggestions={[
                  t(
                    "conversation.newChatSuggestion1" as Parameters<
                      typeof t
                    >[0],
                  ),
                  t(
                    "conversation.newChatSuggestion2" as Parameters<
                      typeof t
                    >[0],
                  ),
                  t(
                    "conversation.newChatSuggestion3" as Parameters<
                      typeof t
                    >[0],
                  ),
                ]}
                onEmptySuggestionClick={(text) => setDraft(text)}
                // Only a genuinely new chat (URL is /conversation/new) shows the
                // welcome. An existing conversation keyed by id has no turns yet
                // while its transcript loads — gate on the URL, not the transient
                // ``selectedSessionId`` (which briefly nulls mid-navigation), so
                // the mascot + suggestions don't flash before history lands.
                showWelcome={id === NEW_SESSION_ID}
              />
            </div>
          </>
        )}

        {/* ADR-013 v2 (kernel d008b53) approval tray — renders any
            unresolved session.requires_action pending whose subject
            is NOT ``clarifying_questions`` (those use
            AskUserQuestionCard inline in the turn stream). Sits
            directly above the Composer so the parked turn is in the
            user's line of sight. Each entry swaps between the full
            ApprovalCard (pending) and the compact
            ApprovalResolvedStrip (post-decision, before fadeout).
            AutoApprovedStrip rows render cache-hit notices that
            never had a preceding card. */}
        {(pendingApprovals.length > 0 || autoApprovedNotices.length > 0) && (
          <div className="mx-auto mb-2 w-full max-w-[760px] space-y-2 px-4">
            {pendingApprovals.map((entry) => {
              if (entry.answered && entry.decision) {
                return (
                  <ApprovalResolvedStrip
                    key={entry.pendingId}
                    decision={entry.decision}
                    rulePreviewDisplay={entry.sessionRulePreviewDisplay}
                    rejectMessage={entry.rejectMessage}
                    resolvedAtLabel={
                      entry.receivedAt
                        ? new Date(entry.receivedAt).toLocaleTimeString()
                        : undefined
                    }
                  />
                );
              }
              return (
                <ApprovalCard
                  key={entry.pendingId}
                  pendingId={entry.pendingId}
                  subject={entry.subject}
                  payload={entry.payload}
                  availableDecisions={entry.availableDecisions}
                  sessionRulePreviewDisplay={entry.sessionRulePreviewDisplay}
                  originalInput={entry.originalInput}
                  receivedAtLabel={
                    entry.receivedAt
                      ? new Date(entry.receivedAt).toLocaleTimeString()
                      : undefined
                  }
                  submitting={entry.submitting}
                  onApprove={() =>
                    handleApprovalDecision(entry.pendingId, "approve")
                  }
                  onReject={(reason) =>
                    handleApprovalDecision(entry.pendingId, "reject", {
                      message: reason,
                    })
                  }
                  onApproveWithChanges={(modifiedInput) =>
                    handleApprovalDecision(
                      entry.pendingId,
                      "approve_with_changes",
                      { modifiedInput },
                    )
                  }
                  onApproveForSession={() =>
                    handleApprovalDecision(
                      entry.pendingId,
                      "approve_for_session",
                    )
                  }
                />
              );
            })}
            {autoApprovedNotices.map((notice) => (
              <AutoApprovedStrip
                key={notice.pendingId}
                subject={notice.subject ?? undefined}
                payload={notice.payload ?? undefined}
                rulePreviewDisplay={notice.rulePreviewDisplay}
                resolvedAtLabel={notice.receivedAtLabel}
              />
            ))}
          </div>
        )}

        {/* Background-task strip — the turn that LAUNCHES a run_in_background
            command ends normally while the process keeps running for minutes;
            without this the conversation reads as "finished" with no cue that
            work is still in flight. Derived from persisted session.bg_task.*
            events (deriveBackgroundTasks), so it also survives re-entering the
            page mid-run; hides itself once every task reaches a terminal
            state (finished / stopped-on-runtime-close). */}
        <BackgroundTaskStrip tasks={runningBgTasks} />

        {/* Scroll-to-bottom button + Composer share a relative wrapper so the
            button anchors to the Composer's top edge (``bottom-full``) instead
            of a magic ``bottom: 150px``. The Composer's height varies a lot
            (skill chip, attachments, multi-line draft, model picker), so the
            old magic number sometimes left the button overlapping the Composer
            top border. Pulses while a turn is still streaming so the user
            knows the run hasn't stalled. */}
        <div className="relative">
          {showScrollBottom && (
            <button
              type="button"
              onClick={handleScrollToBottom}
              className={cn(
                "absolute bottom-full left-1/2 z-20 mb-3 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border border-surface-border bg-surface shadow-md transition-opacity hover:bg-surface-soft",
                displayBusy &&
                  "animate-[border-breathe_1.8s_ease-in-out_infinite] border-brand/60",
              )}
            >
              <ArrowDown className="h-4 w-4 text-ink-body" />
            </button>
          )}

          {!selectedSession && (rosterEmpty || (channelLoaded && !hasChannel)) && (
              <div className="mx-auto mb-2 flex w-full max-w-[760px] items-center justify-between gap-3 rounded-lg border border-info-border bg-info-light px-3 py-2 text-xs text-info-text">
                <span>
                  {channelsPending
                    ? t("conversation.channelsPendingBanner" as I18nKey)
                    : agentPending
                      ? t("conversation.agentPendingBanner" as I18nKey)
                      : channelLoaded && !hasChannel
                        ? rosterEmpty
                          ? t("conversation.noChannelAndAgentBanner" as I18nKey)
                          : t("conversation.noChannelBanner" as I18nKey)
                        : t("conversation.noAgentBanner" as I18nKey)}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    if (!setupPending) {
                      navigate("/welcome");
                      return;
                    }
                    if (channelsPending) refreshChannels();
                    if (rosterEmpty) refreshAgents();
                  }}
                  className="shrink-0 rounded-md bg-brand px-2.5 py-1 font-medium text-white transition-colors hover:bg-brand-hover"
                >
                  {setupPending
                    ? t("conversation.pendingBannerCta" as I18nKey)
                    : t("conversation.noAgentBannerCta" as I18nKey)}
                </button>
              </div>
            )}
          <CreateAgentDialog
            open={createAgentOpen}
            onOpenChange={setCreateAgentOpen}
            onCreated={(slug) => {
              setAgentLibraryRevision((revision) => revision + 1);
              setSelectedAgentSlug(slug);
              setComposerTouched(true);
            }}
          />
          {selectedSessionId ? (
            // Mirror the Composer root's horizontal inset (``px-5``) so the
            // queue lines up with the input box, which is its own
            // ``mx-auto max-w-[760px]`` inside that same px-5.
            <div className="px-5">
              <QueuedInputsBar
                queue={queue}
                // The dispatched head bridges the gap between "left the queue"
                // and "visible in the transcript": show it only while no turn
                // is active — once the drained turn's ``message.user`` streams
                // in (``isBusy`` via the running status), the transcript
                // renders it and the bubble would just duplicate it.
                dispatching={isBusy ? null : queueDispatching}
                paused={queuePaused}
                onEdit={handleEditQueued}
                onDelete={handleDeleteQueued}
                onResume={handleResumeQueue}
                onSteer={handleSteerQueued}
              />
            </div>
          ) : null}
          <Composer
            // Remount on true conversation switches so native autoFocus refires.
            // Keep the key stable during /conversation/new → real-id promotion
            // so first-send does not rebuild the composer.
            key={conversationInstanceKey}
            value={draft}
            onChange={setDraft}
            // Keep the composer usable while a turn runs — submitting queues a
            // follow-up (session-input-queue) instead of being blocked.
            queueWhileSending
            // Project conversations can't attach skills ad-hoc (skills are the
            // agent's equipment), so the toolbar "add skill" button stays hidden
            // there. The ``/`` picker, however, is enabled once a member agent
            // is selected so the user can invoke that agent's bound skills; the
            // assistant (non-project) chat keeps both for global ``/`` skills.
            showSkillButton={!isProjectProject}
            showSkillSlash={
              isProjectProject ? effectiveAgentSlug != null : undefined
            }
            autoFocus
            onSend={() => {
              void handleSend();
            }}
            sending={displayBusy}
            onStop={() => interruptRef.current()}
            // Upload cap counts only the *pending* server rows — the
            // ones staged for the next turn. Consumed rows live on in
            // the panel as history but don't eat the staging budget.
            // The composer adds its own not-yet-uploaded local queue on
            // top and greys the attachment menu once the total hits
            // ``MAX_SESSION_ATTACHMENTS``.
            existingAttachmentCount={
              sessionAttachments.filter((a) => !a.consumed_at).length
            }
            // Both local uploads and KB picks surface as chips in the
            // composer's attachment row, each with its async parse status
            // (spinner while ``parsing``). Only *pending* ones show: once a
            // turn consumes them they drop from the staging row (but stay in
            // the side panel's history).
            uploadOnAttach
            pinnedAttachments={sessionAttachments
              .filter((a) => !a.consumed_at)
              .map((a) => ({
                id: a.id,
                name: a.filename,
                parseStatus: a.parse_status as
                  "parsing" | "ready" | "failed" | "native" | undefined,
                sourceKind: a.source_kind,
              }))}
            onRemovePinnedAttachment={handleRemoveSessionAttachment}
            // 09-assistant §2.1/§2.2: every conversation — 临时 or project —
            // binds to an agent, so the 🤖 chip is always in agent mode. The
            // candidate roster comes from ``composerAgents`` (临时 → "我的"
            // library; project → 派驻 members). The session inherits
            // runtime/model/provider/effort/skills/connectors from the chosen
            // agent.
            agents={composerAgents}
            selectedAgentSlug={
              selectedSession ? sessionAgentSlug : selectedAgentSlug
            }
            // Surface the bound agent's runtime / model / effort in the agent
            // dropdown — temp / quick chats only. Project conversations are
            // driven by the deployed agent team, so they neither show the
            // model hint nor offer a per-conversation override. For a NEW temp
            // conversation the controls are an editable override (applied at
            // session creation; the agent itself is never modified); for an
            // EXISTING temp session runtime/model are read-only (frozen,
            // ADR-006) but visible, and effort stays editable (live-reconcile).
            allowAgentBrainOverride={!isProjectProject}
            // ADR-006: once a session exists both chips freeze (the locked
            // 🤖 chip shows the bound ``sessionAgentSlug``).
            agentLocked={selectedSession != null}
            onAgentChange={(slug) => {
              setSelectedAgentSlug(slug);
              // Switching to an agent re-seeds runtime/model/effort from that
              // agent's brain. Picking "Default" (slug = null) keeps whatever
              // you already chose in the rows below — don't reset it.
              if (slug) setComposerTouched(false);
            }}
            // 09-assistant 📁 project chip: switches the draft between 临时对话
            // (chat-default) and a project project. The page stores the
            // ``"chat-default"`` sentinel for 临时, so the chip sees ``null``
            // when the active project isn't a project, and a change to
            // ``null`` maps back to the sentinel. Frozen once a session exists.
            footerBar={
              <ExecutionLocationBar
                locked={execBarLocked}
                lockedOriginId={sessionExecOrigin}
                targetId={execTargetId}
                onTargetChange={(tid) => {
                  setExecTargetId(tid);
                  // Provider ids are backend-local. Clear the old pick while
                  // the newly selected service's list is loading.
                  setSelectedProviderId(null);
                  setSelectedModelId(null);
                  // A project belongs to ONE backend — switching location
                  // resets the pick back to 临时对话.
                  const current = projects.find(
                    (w) => w.id === selectedProjectId,
                  );
                  if (current && (current.exec_origin ?? "local") !== tid) {
                    setSelectedProjectId("chat-default");
                    setSelectedComposerSkill(null);
                  }
                  setComposerTouched(true);
                }}
                projects={execBarProjects}
                selectedProjectId={isProjectProject ? selectedProjectId : null}
                onProjectChange={(idOrNull) => {
                  const nextTargetId = idOrNull
                    ? (projects.find((w) => w.id === idOrNull)?.exec_origin ??
                      "local")
                    : (execTargetId ?? getDefaultExecutionTarget()?.id);
                  if (nextTargetId !== providerTarget?.id) {
                    setSelectedProviderId(null);
                    setSelectedModelId(null);
                  }
                  setSelectedProjectId(idOrNull ?? "chat-default");
                  // Same scope rule as the old toolbar chip: skills don't
                  // survive a project-scope change.
                  setSelectedComposerSkill(null);
                  setComposerTouched(true);
                  // A project always has meaningful panel content (file
                  // tree / KB / members) — reveal the right panel on pick.
                  if (idOrNull) panelSetCollapsed(false);
                }}
              />
            }
            onAddAgent={
              isProjectProject && selectedProjectId
                ? () =>
                    navigate(
                      `/projects/${encodeURIComponent(selectedProjectId)}`,
                    )
                : () => setCreateAgentOpen(true)
            }
            // Only the project path greys the send button (a project with no
            // deployed members / no pick). 临时 conversations stay clickable
            // even with an empty library — handleSend then nudges the user to
            // pick/create an agent (10-new-conversation-guidance).
            sendDisabled={
              isProjectProject &&
              !selectedSession &&
              (composerAgents.length === 0 || !selectedAgentSlug)
            }
            providers={composerProviders}
            selectedProviderId={selectedProviderId}
            selectedModelId={selectedModelId}
            runtimes={composerRuntimes}
            selectedRuntimeId={selectedRuntimeId}
            onRuntimeChange={(rt) => {
              setSelectedRuntimeId((rt as RuntimeId | null) ?? null);
              setComposerTouched(true);
            }}
            permissionMode={selectedPermissionMode}
            // Kernel V5+bba3014 live-reconciles ``permission_mode`` on
            // the next Send (Claude live ``set_permission_mode`` mutator
            // + fork-on-rebuild for the bypass tier; Codex per-turn
            // approval/sandbox kwargs; DeepAgents graph rebuild). The
            // pre-bba3014 lock-on-live-session has been dropped — the
            // picker is now interactive for both new and live sessions.
            permissionModeLocked={false}
            onPermissionModeChange={(mode) => {
              setSelectedPermissionMode(mode);
              // For a live session, persist via PATCH so the next Send
              // picks up the new mode. For new-session entry the value
              // is forwarded into ``sessionsApi.create`` from
              // ``handleSend`` instead.
              if (!isNewSession && id) {
                void sessionsApi.updatePermissionMode(id, mode).catch(() => {
                  /* non-fatal — surfaced by error toast pipeline */
                });
              }
            }}
            // Effort budget: seeded from the bound agent's brain for a new
            // agent conversation (overridable here — see
            // ``allowAgentBrainOverride`` below), or from Settings for quick
            // chats. For a live session it live-reconciles via PATCH.
            effort={selectedEffort}
            onEffortChange={(level) => {
              setSelectedEffort(level);
              setComposerTouched(true);
              if (!isNewSession && id) {
                void sessionsApi.updateEffort(id, level).catch(() => {
                  /* non-fatal — surfaced by error toast pipeline */
                });
              }
            }}
            // Session model is frozen at creation (V5 / ADR-006). Lock the
            // picker the moment a session exists — including freshly-created
            // sessions (e.g. Skill Creator opens a session before the user
            // can type), where the previous ``turns.length > 0`` guard let
            // the picker pretend it was effective. ``modelSelectorUnlocked``
            // is the manual escape hatch the retry-with-different-model flow
            // toggles via ``handleSwitchModel``. The same lock applies to
            // ``runtime`` per ADR-006 + REP-107 — no mid-session swaps.
            modelLocked={selectedSession != null && !modelSelectorUnlocked}
            onModelChange={(chId, mId) => {
              setSelectedProviderId(chId);
              setSelectedModelId(mId);
              setComposerTouched(true);
            }}
            skills={
              isProjectProject ? selectedAgentSkillItems : composerMentionSkills
            }
            onSkillSelect={(s) => {
              const skill =
                availableSkills.find((sk) => sk.id === s.id) ?? null;
              setSelectedComposerSkill(skill);
            }}
            onKBPick={() => {
              void handleOpenKbPicker();
            }}
            onLocalUpload={handleLocalFilesAttach}
            onFileDrop={handleLocalFilesAttach}
            connectors={connectorOptions}
            selectedConnectorSlugs={selectedMcpSlugs}
            onToggleConnector={toggleConnector}
            connectorsReadOnly={!isNewSession}
            onManageSkills={() => navigate("/skills")}
            onManageConnectors={() => navigate("/connectors")}
          />
          <AttachmentParsingDialog
            open={parsingConfirmOpen}
            onConfirm={() => {
              setParsingConfirmOpen(false);
              void performSend();
            }}
            onCancel={() => setParsingConfirmOpen(false)}
          />
        </div>
        {artifactViewerOpen ? (
          <div
            className="absolute inset-0 z-30 overflow-hidden overscroll-contain bg-surface p-3"
            onWheel={(event) => event.stopPropagation()}
            onTouchMove={(event) => event.stopPropagation()}
          >
            <ArtifactViewerShell
              artifact={artifact}
              content={artifactContent}
              target={artifactTarget}
              loading={artifactLoading}
              error={artifactError}
              onReload={handleArtifactReload}
              onClose={handleArtifactClose}
              onCopyContent={handleArtifactCopy}
              onOpenExternal={handleArtifactOpenExternal}
            />
          </div>
        ) : null}
      </div>

      {/* Knowledge Base file picker overlay — tree view: documents are
          organised under their KB and folders; folders are expandable
          for navigation but only files are selectable. */}
      {kbPickerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="flex h-[600px] max-h-[85vh] w-[720px] max-w-[92vw] flex-col rounded-xl border border-surface-border bg-card p-4 shadow-xl">
            <KnowledgeFileTreePicker
              kbTree={pickerKbTree}
              loading={pickerKbLoading}
              onExpandFolder={pickerExpandFolder}
              // Pre-check only the *pending* KB picks — the ones still
              // staged for the next turn. Already-consumed picks are
              // session history, not part of the current staging set,
              // so re-opening the picker shouldn't show them ticked.
              selected={sessionAttachments
                .filter(
                  (a) =>
                    a.source_kind === "kb_doc" &&
                    a.source_kb_doc_id &&
                    !a.consumed_at,
                )
                .map((a) => a.source_kb_doc_id as string)}
              onConfirm={handleKbPickerConfirm}
              onCancel={() => setKbPickerOpen(false)}
            />
          </div>
        </div>
      )}
      <DeleteConfirmDialog
        open={titleDeleting}
        onOpenChange={(open) => {
          if (!open && !titleDeleteInFlight) setTitleDeleting(false);
        }}
        itemName={
          selectedSession?.name ??
          (typeof headerTitle === "string" ? headerTitle : "")
        }
        loading={titleDeleteInFlight}
        onConfirm={() => {
          if (!selectedSessionId) return;
          setTitleDeleteInFlight(true);
          sessionsApi
            .delete(selectedSessionId)
            .then(() => {
              toast.success(t("common.deleted" as Parameters<typeof t>[0]));
              setTitleDeleting(false);
              navigate("/conversation/new");
            })
            .catch(() =>
              toast.error(t("common.deleteFailed" as Parameters<typeof t>[0])),
            )
            .finally(() => setTitleDeleteInFlight(false));
        }}
      />
    </>
  );
};

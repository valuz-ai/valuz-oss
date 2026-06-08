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
  ChevronDown,
  ChevronRight,
  FilePenLine,
  Settings,
  Sparkles,
  Trash2,
} from "lucide-react";
import {
  sessionsApi,
  agentsApi,
  connectorsApi,
  useSessionStore,
  useWorkspaceStore,
  workspacesApi,
  providersApi,
  skillsApi,
  usePanelStore,
  type SessionDetail,
  type SessionEventDTO,
  type SessionListItem,
  type TodoItem,
  type WorkspaceDetail,
  type WorkspaceListItem,
  type ProviderListItem,
  type ProviderDetail,
  type SkillView,
  type StagingSlugView,
  type StagingSyncStrategy,
  type WorkspaceFileNode,
  parseTodosUpdate,
  parseRequiresAction,
  parseActionResolved,
  SESSION_REQUIRES_ACTION_EVENT,
  SESSION_ACTION_RESOLVED_EVENT,
  useComposerProviders,
  useModelDefaults,
  useRuntimes,
  useSessionAttachments,
  type RuntimeId,
  type MemberWithAgent,
  type Agent,
} from "@valuz/core";
import {
  ApprovalCard,
  ApprovalResolvedStrip,
  AutoApprovedStrip,
  AskUserQuestionCard,
  AutomationToolCard,
  DeleteConfirmDialog,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  UserAnswerSummaryCard,
  cn,
  Composer,
  KnowledgeFileTreePicker,
  ProjectDetailContextPanel,
  SkillStagingPanel,
  SkillSubmissionCard,
  parseAskUserQuestionInput,
  parseAutomationToolOutput,
  type ApprovalCardSubject,
  type ApprovalResolvedDecision,
  type FileTreeNode,
  type SkillSubmissionState,
  type UploadedFileItem,
  type ComposerAgentItem,
  type ComposerProjectItem,
} from "@valuz/ui";
import { modelLabel } from "@valuz/shared";
import { t as _t } from "@valuz/shared/i18n";
import type { I18nKey } from "@valuz/shared";
import { useWorkspaceOutlet } from "@valuz/app/layout";
import { buildTurns, useStableTurns, type PlanSubtask } from "@valuz/core";
import { ConversationTurnList } from "@valuz/ui";
import { usePlatform } from "@valuz/app/platform";
import { useHasUsableChannel, useTranslation } from "@valuz/core";
import { useProjectKbBindings, useKbDocTree } from "@valuz/app/hooks";
import { LiveTaskCard } from "../components/LiveTaskCard";
import { AttachmentParsingDialog } from "../components/AttachmentParsingDialog";
import { CreateAgentDialog } from "../components/CreateAgentDialog";
import { getLastTempAgent, setLastTempAgent } from "../lib/last-temp-agent";

function toFileTree(nodes: WorkspaceFileNode[], prefix = ""): FileTreeNode[] {
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

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── VALUZ-CHATPLAN S3 helpers ────────────────────────────────────────────

/** Tool outputs from the kernel are wrapped as a content-block repr
 *  ``[{'type': 'text', 'text': '<json>'}]`` (Python repr, single-quoted
 *  on the outer wrapper, JSON-quoted on the inner ``text`` payload).
 *  Pull the inner JSON object back out:
 *    1. Try a direct ``JSON.parse`` first — some runtimes do skip the wrap.
 *    2. Otherwise walk for the first ``{"`` (canonical JSON-object start)
 *       and balance-count braces forward to find its matching ``}``.
 *  A naive ``/{[\s\S]*}/`` greedy regex would land on the OUTER Python dict
 *  (single-quoted) and JSON.parse would fail every time. */
function extractToolOutputJson(output: string): unknown | null {
  try {
    return JSON.parse(output);
  } catch {
    /* fall through */
  }
  const start = output.indexOf('{"');
  if (start < 0) return null;
  let depth = 0;
  let end = -1;
  let inString = false;
  let escape = false;
  for (let i = start; i < output.length; i++) {
    const ch = output[i];
    if (inString) {
      if (escape) {
        escape = false;
      } else if (ch === "\\") {
        escape = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === "{") {
      depth++;
    } else if (ch === "}") {
      depth--;
      if (depth === 0) {
        end = i + 1;
        break;
      }
    }
  }
  if (end < 0) return null;
  try {
    return JSON.parse(output.slice(start, end));
  } catch {
    return null;
  }
}

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
    emerald: "bg-emerald-500",
    rose: "bg-rose-500",
    amber: "bg-amber-500",
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
    workspace_id: detail.workspace_id,
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
    updated_at: detail.updated_at,
  };
}

/**
 * Small status pill shown next to the conversation title in the page
 * header. Mirrors the sidebar's per-row indicator: ``running`` pulses
 * (the agent is mid-turn), ``failed``/``cancelled`` show a muted state.
 * Idle / archived / undefined render nothing — no point in chrome for
 * the steady state.
 */
const SessionStatusPill = ({ status }: { status?: string }) => {
  const { t } = useTranslation();
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
          ? "bg-red-500/10 text-red-600"
          : "bg-surface-soft text-ink-meta";
  return (
    <span
      className={`flex shrink-0 items-center gap-1 rounded-md px-2 py-0.5 text-2xs ${cls}`}
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

export const ConversationPage = () => {
  const { t } = useTranslation();
  const { revealInFinder } = usePlatform();
  const { id = NEW_SESSION_ID } = useParams<{ id: string }>();
  const location = useLocation();
  const { setRightPanel, setHeader, setHideHeader } = useWorkspaceOutlet();
  const panelCollapsed = usePanelStore((s) => s.collapsed);
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);
  const [searchParams] = useSearchParams();
  // Mode hint persisted on the session row at creation time; lets us restore
  // the Skill-Creator banner / staging panel when the user re-enters the
  // session from history (where ?mode= is absent).
  const [sessionTriggerMode, setSessionTriggerMode] = useState<string | null>(
    null,
  );
  // Workspace-agent handle for the open session (Project Task lead/member).
  const [sessionAgentSlug, setSessionAgentSlug] = useState<string | null>(null);
  // Project conversations pick a configured project agent instead of a raw
  // model. ``projectAgents`` is the member roster for the active project;
  // ``selectedAgentSlug`` is the user's pick for the next (new) session.
  const [projectAgents, setProjectAgents] = useState<MemberWithAgent[]>([]);
  // 09-assistant: the "我的" agent library, candidates for the 🤖 chip when
  // the 📁 chip is on 临时对话 (no project). Loaded once on mount.
  const [myAgents, setMyAgents] = useState<Agent[]>([]);
  // Gates the empty-library banner so it doesn't flash before the first load
  // resolves (10-new-conversation-guidance).
  const [myAgentsLoaded, setMyAgentsLoaded] = useState(false);
  // 10-new-conversation-guidance: the 🤖 「+ Agent」 menu item opens a create
  // dialog for temp conversations (the project path navigates to the project).
  const [createAgentOpen, setCreateAgentOpen] = useState(false);
  // 10-new-conversation-guidance (slice 2): is there any usable model channel?
  // Drives the setup banner's "no channel" state.
  const { hasChannel, loaded: channelLoaded } = useHasUsableChannel();
  const [selectedAgentSlug, setSelectedAgentSlug] = useState<string | null>(
    null,
  );
  // 「去临时对话」after creating an agent navigates here with ?agent=<slug>;
  // honour it as the pre-selected agent for the next (new) chat. The roster
  // reload (myAgents effect) keys off the same param so the new agent is
  // actually present, and the defaulting effect below treats it as top priority.
  const agentParam = searchParams.get("agent");
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (agentParam) setSelectedAgentSlug(agentParam);
  }, [agentParam]);
  // Set when this conversation was opened from a task's "view session" link
  // (TaskDetailPage). Drives the breadcrumb back-to-task affordance in the
  // header so a subtask/lead session isn't a navigational dead end.
  const fromTaskId = searchParams.get("from_task");
  const isSkillCreatorMode =
    searchParams.get("mode") === "skill-creator" ||
    sessionTriggerMode === "skill-creator";
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
  const [workspaces, setWorkspaces] = useState<WorkspaceListItem[]>([]);
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(
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
  useEffect(() => {
    selectedSessionIdRef.current = selectedSessionId;
  }, [selectedSessionId]);
  const [events, setEvents] = useState<SessionEventDTO[]>([]);
  // Live TODO list for the active session. Hydrated from
  // ``session.todos`` on session switch and from ``session.todos.update``
  // SSE frames during a turn. ``null`` means "agent has never emitted
  // a TodoWrite snapshot for this session"; an empty array means
  // "all done" (kernel preserves it as a meaningful state).
  const [todos, setTodos] = useState<TodoItem[] | null>(null);
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
     * Highest event seq seen at the moment ``handleSend`` set the
     * pending — used by ``effectiveTurns`` to disambiguate a server
     * echo for THIS send (envelope seq > fromSeq) from a previous
     * turn that happens to share the exact same text. Without this,
     * sending the identical text twice in a row would falsely dedup
     * the optimistic against the prior turn and snap-to-top would
     * land on the older turn's position.
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
  const [providers, setProviders] = useState<ProviderDetail[]>([]);
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

  // Connector selection — only meaningful for new sessions (locked at creation
  // per ADR-006). The picker UI was removed from the composer; we still
  // pre-select every connected connector at session-creation time so the
  // new session inherits the user's globally-enabled data sources.
  const [selectedMcpSlugs, setSelectedMcpSlugs] = useState<string[]>([]);
  const isNewSession = id === NEW_SESSION_ID;
  useEffect(() => {
    if (!isNewSession) return;
    connectorsApi
      .list()
      .then(({ connectors: list }) => {
        setSelectedMcpSlugs(
          list
            .filter((c) => c.enabled && c.status === "connected")
            .map((c) => c.slug),
        );
      })
      .catch(() => {
        /* non-fatal */
      });
  }, [isNewSession]);
  // Per-``submit_skill`` tool_use state — keyed by tool_use id so multiple
  // submissions in the same conversation render independently. Persists
  // for the lifetime of the page; on refresh the cards re-render in
  // their initial "pending" state, which is acceptable for v1 (the
  // backend has the staged content + library state of record).
  type SubmissionEntry = {
    state: SkillSubmissionState;
    errorMessage?: string;
    boundToWorkspaceLabel?: string | null;
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
        const isAsk =
          name === "AskUserQuestion" ||
          (typeof name === "string" && name.endsWith("__AskUserQuestion"));
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
  const abortRef = useRef<AbortController | null>(null);
  // Set while ``handleSend`` is between ``ensureSession`` and its own
  // ``subscribeToSession`` call. The auto-resume effect (which fires when
  // ``selectedSessionId`` changes to the freshly-created session id) must
  // skip its own subscribe in that window, otherwise it races with
  // ``handleSend`` — both subscriptions deliver ``message.user`` before the
  // older one is aborted, the late ``.finally`` from the aborted promise
  // clobbers ``sending`` / ``abortRef`` set by the live one, and the user
  // sees their input rendered twice + the loading dots vanish mid-stream.
  const isSendInFlightRef = useRef(false);
  const maxSeqRef = useRef(0);
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
  const upsertWorkspace = useWorkspaceStore((s) => s.upsertWorkspace);
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
  const navigate = useNavigate();

  const [availableSkills, setAvailableSkills] = useState<SkillView[]>([]);
  const [selectedComposerSkill, setSelectedComposerSkill] =
    useState<SkillView | null>(null);
  const [workspaceSkills, setWorkspaceSkills] = useState<SkillView[]>([]);
  const [fileTree, setFileTree] = useState<FileTreeNode[]>([]);

  const activeWorkspace = useMemo(
    () =>
      (workspaces.find((w) => w.id === selectedWorkspaceId) as
        | WorkspaceDetail
        | undefined) ?? null,
    [selectedWorkspaceId, workspaces],
  );

  // Project KB bindings — loaded for project workspaces only and
  // rendered **read-only** in the session panel (per product rule,
  // binding edits happen on the project detail page and apply to the
  // next session). ``handleExpandKbFolder`` is still wired so the
  // user can drill folders to inspect what's selected; only the
  // toggle handler is withheld. Passing ``null`` for chat /
  // skill-creator workspaces makes the hook no-op so those panels
  // show no KB tree.
  const {
    kbTree: projectKbTree,
    bindings: projectKbBindings,
    handleExpandKbFolder: handleExpandProjectKbFolder,
  } = useProjectKbBindings(
    activeWorkspace?.kind === "project" ? selectedWorkspaceId : null,
  );

  // Global KB document tree for the attachment picker. Loads lazily —
  // only when the picker is open — and is independent of the
  // workspace (chat sessions have no project scope; the picker shows
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
  const rawTurns = useMemo(() => buildTurns(events), [events]);
  const turns = useStableTurns(rawTurns);

  // VALUZ-CHATPLAN — track the LATEST ``plan_task`` / ``modify_plan`` tool
  // result per task_id. That position in the conversation gets the rich
  // LiveTaskCard (subtask list + status + actions, SSE-subscribed). Earlier
  // plan writes for the same task degrade to compact history pills, so the
  // user always lands on the "current state" surface near the bottom of
  // the timeline. ``richPlanToolByTask`` also doubles as a "this is a plan
  // write for a known task" set for the pill renderer.
  const planAnchors = useMemo(() => {
    const PLAN_WRITE_NAMES = new Set(["plan_task", "modify_plan"]);
    const matchesPlanWrite = (n: string) => {
      if (PLAN_WRITE_NAMES.has(n)) return true;
      for (const k of PLAN_WRITE_NAMES) {
        if (n.endsWith(`__${k}`)) return true;
      }
      return false;
    };
    const richToolByTask = new Map<string, string>();
    const taskByRichTool = new Map<string, string>();
    for (const turn of turns) {
      for (const block of turn.blocks) {
        if (block.kind !== "tool") continue;
        const tool = block.tool;
        const name = tool.title || "";
        if (!matchesPlanWrite(name)) continue;
        // plan_task / modify_plan responses don't echo task_id; it lives
        // in the input arg. Falling back to output regardless covers
        // future runtimes that may echo it.
        let tid: string | undefined;
        if (tool.input) {
          const parsedIn = extractToolOutputJson(tool.input) as {
            task_id?: string;
          } | null;
          tid = parsedIn?.task_id;
        }
        if (!tid && tool.output) {
          const parsed = extractToolOutputJson(tool.output) as {
            task_id?: string;
          } | null;
          tid = parsed?.task_id;
        }
        if (!tid) continue;
        // Last write wins — drop any earlier tool_use_id for this task.
        const prev = richToolByTask.get(tid);
        if (prev) taskByRichTool.delete(prev);
        richToolByTask.set(tid, tool.id);
        taskByRichTool.set(tool.id, tid);
      }
    }
    return { taskByRichTool };
  }, [turns]);
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
    if (
      lastTurn &&
      lastTurn.userText === pendingUserMessage.text &&
      lastTurnSeq > pendingUserMessage.fromSeq
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
  const submissionWorkspaceLabel = useMemo(() => {
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
          res.bound_to_workspace_id && res.creation_context.kind === "project"
            ? submissionWorkspaceLabel
            : null;
        setSubmissionStates((prev) => ({
          ...prev,
          [toolId]: {
            state: "confirmed",
            boundToWorkspaceLabel: boundLabel,
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
    [submissionWorkspaceLabel],
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
        if (name !== "submit_skill" && !name.endsWith("__submit_skill")) {
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
    // Poll faster while sending (agent actively writing), slower once
    // the turn is done.
    const intervalMs = sending ? 1500 : 5000;
    const interval = window.setInterval(() => void tick(), intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [selectedSessionId, turns, sending]);

  const renderToolCall = useCallback(
    (tool: { id: string; title: string; input?: string; output?: string }) => {
      const name = tool.title || "";

      // ADR-021: automation tool result → AutomationToolCard. The MCP
      // server returns a structured JSON blob as ``tool.output``; we
      // parse it and hand off to the card. If the output is missing
      // (still running) or unparseable, we fall through to the generic
      // tool renderer. The ``__automation`` suffix match catches the
      // wrapped form FastMCP emits when the tool comes through a
      // namespaced provider.
      const isAutomation =
        name === "automation" || name.endsWith("__automation");
      if (isAutomation) {
        const result = parseAutomationToolOutput(tool.output);
        if (result) {
          return (
            <AutomationToolCard
              result={result}
              onOpenInAutomation={(automationId) => {
                // The automation page is at ``/automations`` and reads
                // ``?automation=<id>`` for direct linking. We use a soft
                // navigation so the conversation stays mounted in the
                // workspace sidebar.
                navigate(
                  `/automations?automation=${encodeURIComponent(automationId)}`,
                );
              }}
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
      const isCreateTask =
        name === "create_task" || name.endsWith("__create_task");
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
      const isAskUserQuestion =
        name === "AskUserQuestion" || name.endsWith("__AskUserQuestion");
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

      const isSubmit =
        name === "submit_skill" || name.endsWith("__submit_skill");
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
          boundToWorkspaceLabel={entry.boundToWorkspaceLabel}
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
      askUserQuestionAnswersByToolId,
      askUserQuestionLocalAnswers,
      askUserQuestionSubmitRef,
      planAnchors,
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

  // 09-assistant: the 📁 chip's dropdown options — every project workspace.
  // ``WorkspaceListItem`` carries no member count, so the count is left
  // undefined for now (chip renders fine without it).
  const composerProjects = useMemo<ComposerProjectItem[]>(
    () =>
      workspaces
        .filter((w) => w.kind === "project")
        .map((w) => ({ id: w.id, name: w.name })),
    [workspaces],
  );

  // 09-assistant: whether the conversation currently targets 临时对话
  // (chat-default / non-project). The page stores the ``"chat-default"``
  // sentinel for 临时, so derive temp-ness from the resolved workspace kind
  // rather than a literal null.
  const isTempConversation = activeWorkspace?.kind !== "project";

  // Agent options for the composer's 🤖 chip. Candidates depend on the 📁
  // chip: 临时对话 → the "我的" library (``myAgents``); a project → its
  // 派驻 member roster (``projectAgents``). Runtime ids are mapped to their
  // display names so the dropdown reads "Claude Agent · mimo-v2.5-pro".
  const composerAgents = useMemo<ComposerAgentItem[]>(() => {
    if (isTempConversation) {
      return myAgents.map((a) => ({
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

  // For project workspace "/" mention, only show enabled/bound skills
  const composerMentionSkills = useMemo(() => {
    if (activeWorkspace?.kind === "project") {
      return workspaceSkills
        .filter((s) => s.enabled)
        .map((s) => ({
          id: s.id,
          name: s.name,
          slug: s.slug,
          description: s.description,
        }));
    }
    return availableSkills.map((s) => ({
      id: s.id,
      name: s.name,
      slug: s.slug,
      description: s.description,
    }));
  }, [activeWorkspace?.kind, workspaceSkills, availableSkills]);

  // Slug → display-name map for rendering inline ``/skill-slug`` chips
  // in past user messages. We blend availableSkills (the global picker
  // catalog) and workspaceSkills (project-bound skills) so chips render
  // even for project-only skills that wouldn't appear in the global
  // catalog.
  const skillsBySlug = useMemo(() => {
    const map: Record<string, { name: string }> = {};
    for (const s of availableSkills) {
      if (s.slug) map[s.slug] = { name: s.name };
    }
    for (const s of workspaceSkills) {
      if (s.slug) map[s.slug] = { name: s.name };
    }
    return map;
  }, [availableSkills, workspaceSkills]);

  // Initial load fetches the latest ``TURN_PAGE_SIZE`` turns through the
  // turn-aligned window endpoint instead of pulling every event. Earlier
  // turns are loaded on demand by ``loadOlderTurns`` when the user
  // scrolls toward the top. The old "fetch all events with no cursor"
  // flow silently truncated long sessions because the backend caps the
  // legacy linear endpoint at 500 rows ASC.
  const TURN_PAGE_SIZE = 20;

  const refreshEvents = useCallback(async (sessionId: string | null) => {
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
    // The clarifying-pending ref is keyed to whatever session we're
    // leaving. Reset it so a stale pending_id from the previous session
    // can't get POSTed against the new one — and so the post-fetch walk
    // below has a clean slate to repopulate from history.
    currentClarifyingPendingRef.current = null;
    maxSeqRef.current = 0;
    minSeqRef.current = Number.POSITIVE_INFINITY;
    hasMoreOlderRef.current = false;
    setHasMoreOlder(false);
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
      setEvents(response.items);
      if (response.items.length > 0) {
        maxSeqRef.current = response.items[response.items.length - 1].seq;
        minSeqRef.current = response.items[0].seq;
      } else {
        maxSeqRef.current = 0;
        minSeqRef.current = Number.POSITIVE_INFINITY;
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
      for (const ev of response.items) {
        const parsedTodos = parseTodosUpdate(ev);
        if (parsedTodos) lastTodos = parsedTodos;
        if (ev.event.event_type === SESSION_REQUIRES_ACTION_EVENT) {
          const ra = parseRequiresAction(ev);
          if (
            ra &&
            ra.subject === "clarifying_questions" &&
            !resolvedPendingIds.has(ra.pending_id)
          ) {
            unresolvedClarifyingPending = ra.pending_id;
          }
        }
      }
      setTodos(lastTodos);
      currentClarifyingPendingRef.current = unresolvedClarifyingPending;
    } catch {
      if (selectedSessionIdRef.current !== sessionId) return;
      setEvents([]);
      maxSeqRef.current = 0;
      minSeqRef.current = Number.POSITIVE_INFINITY;
      hasMoreOlderRef.current = false;
      setHasMoreOlder(false);
      setTodos(null);
    }
  }, []);

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
      if (response.items.length > 0) {
        // Defensive dedup: SSE shouldn't backfill historical events but
        // a slow turn could in theory race with this fetch.
        setEvents((prev) => {
          const seen = new Set(prev.map((p) => p.seq));
          const fresh = response.items.filter((e) => !seen.has(e.seq));
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
   * Replaces the previous ``refreshSessions(workspaceId, preferredId)``
   * which did ``GET /v1/sessions?workspace_id=…`` and ``find()``-d the
   * row we cared about. That pattern had two problems:
   *
   *   1. It needed a workspace id to call the list endpoint, but the
   *      ``selectedWorkspaceId`` captured in callback closures could be
   *      stale (the ``"chat-default"`` sentinel before
   *      ``ensureSession``'s state update settled). The post-turn
   *      refresh would then list against a non-existent project id,
   *      get back an empty list, and silently null-out
   *      ``selectedSessionId`` — the symptom users saw as "refresh"
   *      ".
   *   2. The page only ever renders one session at a time. Listing a
   *      whole workspace's sessions just to ``find()`` one was
   *      wasteful and brittle.
   *
   * The clean replacement uses ``GET /v1/sessions/{id}`` directly. No
   * workspace id needed; one round-trip; the result feeds both the
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

  const bootstrap = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [wsResponse, chListResponse] = await Promise.all([
        workspacesApi.list(),
        providersApi
          .list()
          .catch(() => ({ providers: [] as ProviderListItem[] })),
      ]);
      setWorkspaces(wsResponse.workspaces);
      const details = await Promise.all(
        chListResponse.providers
          .filter((c) => c.enabled)
          .map((c) => providersApi.get(c.id).catch(() => null)),
      );
      setProviders(details.filter((d): d is ProviderDetail => d !== null));

      // Two URL shapes drive the page:
      //
      //   /conversation/new           — fresh draft, no session yet
      //   /conversation/{session_id}  — existing session, fetch detail
      //
      // For the fresh-draft case we set ``selectedWorkspaceId`` to the
      // ``"chat-default"`` sentinel so skill autocomplete still loads
      // (the backend skills router treats it as the global chat-scope
      // key); the file tree 404s and renders empty — correct for a
      // not-yet-allocated workdir. The real workspace materializes
      // when the user sends the first message and ``ensureSession``
      // navigates us to ``/conversation/{real-id}``.
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
          wsResponse.workspaces.some(
            (w) => w.id === projectParam && w.kind === "project",
          )
            ? projectParam
            : null;
        setSelectedWorkspaceId(presetProject ?? "chat-default");
        setSessions([]);
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
      // workspace_id (right panel cwd / file tree), the session row
      // itself (composer status + optimistic-merge target).
      try {
        const sessionDetail = await sessionsApi.get(id);
        setSessionTriggerMode(sessionDetail.trigger_meta?.mode ?? null);
        setSessionAgentSlug(sessionDetail.agent_slug ?? null);
        setSelectedWorkspaceId(sessionDetail.workspace_id);
        setSessions([sessionDetailToListItem(sessionDetail)]);
        // Skip the events refetch when bootstrap re-fires onto the
        // same session id we're already on — the URL change from
        // ``/conversation/new`` → ``/conversation/{real-id}`` (issued
        // by ``ensureSession`` after a fresh send) re-runs bootstrap
        // while an in-flight SSE subscription is already pushing
        // events into local state. Refetching here would clobber the
        // first few streamed deltas with the server's truncated list.
        const wasSameSession =
          selectedSessionIdRef.current === sessionDetail.id;
        setSelectedSessionId(sessionDetail.id);
        if (!wasSameSession) {
          await refreshEvents(sessionDetail.id);
        }
      } catch {
        // 404 / network — render the page in "no session" state and
        // surface an error banner. Don't fall back to the sentinel
        // because that would silently move the user off the URL they
        // typed / clicked.
        setSessionTriggerMode(null);
        setSessionAgentSlug(null);
        setSelectedWorkspaceId(null);
        setSessions([]);
        setSelectedSessionId(null);
        setError("Session not found.");
      }
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Failed to load workspace.",
      );
    } finally {
      setLoading(false);
    }
  }, [id, refreshEvents, searchParams]);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  // Load all skills for composer autocomplete
  useEffect(() => {
    if (!selectedWorkspaceId) return;
    skillsApi
      .list(selectedWorkspaceId)
      .then((catalog) => {
        setAvailableSkills(catalog.skills);
      })
      .catch(() => {
        setAvailableSkills([]);
      });
  }, [selectedWorkspaceId]);

  // Load the project's configured member agents. Project conversations
  // pick one of these (instead of a raw model); the composer renders them
  // as an Agent selector. Default the picker to the first agent.
  useEffect(() => {
    if (!selectedWorkspaceId || activeWorkspace?.kind !== "project") {
      setProjectAgents([]);
      return;
    }
    let cancelled = false;
    agentsApi
      .listMembers(selectedWorkspaceId)
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
  }, [selectedWorkspaceId, activeWorkspace?.kind]);

  // 09-assistant: load the "我的" agent library once. These back the 🤖 chip
  // when the 📁 chip is on 临时对话 (no project member roster).
  useEffect(() => {
    let cancelled = false;
    agentsApi
      .listAgents()
      .then((res) => {
        if (!cancelled) setMyAgents(res.agents);
      })
      .catch(() => {
        if (!cancelled) setMyAgents([]);
      })
      .finally(() => {
        if (!cancelled) setMyAgentsLoaded(true);
      });
    return () => {
      cancelled = true;
    };
    // Reload when 「去临时对话」 hands off a freshly-created agent via ?agent=,
    // so the new agent is present in the roster the composer renders from.
  }, [agentParam]);

  // 09-assistant: when the 📁 chip sits on 临时对话 (non-project workspace)
  // and no session exists yet, default the 🤖 chip to the first "我的" agent.
  // Empty library → null; handleSend then nudges the user to pick/create
  // (10-new-conversation-guidance). The project case is handled by the
  // listMembers effect above (first member).
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (selectedSession) return; // existing session is frozen (ADR-006)
    if (activeWorkspace?.kind === "project") return; // project path elsewhere
    setSelectedAgentSlug((prev) => {
      // A freshly-created agent handed off via ?agent= (「去临时对话」) wins as
      // soon as it appears in the reloaded roster.
      if (agentParam && myAgents.some((a) => a.slug === agentParam))
        return agentParam;
      if (prev && myAgents.some((a) => a.slug === prev)) return prev;
      // 10-new-conversation-guidance slice 3: prefer the agent from the last
      // 临时对话 (per-device memory), then fall back to the first library agent.
      const lastUsed = getLastTempAgent();
      if (lastUsed && myAgents.some((a) => a.slug === lastUsed))
        return lastUsed;
      // Then the onboarding-seeded Valuz 小助手 as the general default.
      if (myAgents.some((a) => a.slug === "valuz-helper"))
        return "valuz-helper";
      return myAgents[0]?.slug ?? null;
    });
    // Re-run only on the data that decides the default — existence of a
    // session (frozen), the workspace kind, the candidate roster, and the
    // explicit ?agent= hand-off.
  }, [activeWorkspace?.kind, myAgents, selectedSession, agentParam]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Load bound skills for project workspace context panel
  useEffect(() => {
    if (!selectedWorkspaceId || activeWorkspace?.kind !== "project") return;
    skillsApi
      .workspaceCatalog(selectedWorkspaceId)
      .then((catalog) => {
        setWorkspaceSkills(catalog.skills);
      })
      .catch(() => {
        setWorkspaceSkills([]);
      });
  }, [selectedWorkspaceId, activeWorkspace?.kind]);

  // Load workspace file tree for the right context panel's Files tab.
  // Loaded for both project AND chat workspaces — chat sessions write
  // generated artifacts into their managed cwd, and the user wants
  // those visible in the right rail too (under the "Generated files" label).
  const refreshFileTree = useCallback(() => {
    if (!selectedWorkspaceId) {
      setFileTree([]);
      return;
    }
    workspacesApi
      .listFiles(selectedWorkspaceId, { depth: 3 })
      .then((res) => setFileTree(toFileTree(res.files)))
      .catch(() => setFileTree([]));
  }, [selectedWorkspaceId]);

  useEffect(() => {
    refreshFileTree();
  }, [refreshFileTree]);

  // Auto-refresh on turn end: when ``sending`` flips false, the agent
  // has just finished writing whatever artifacts it was going to. Pull
  // a fresh tree so the panel reflects new files without the user
  // having to switch workspaces or hit refresh manually.
  const prevSendingRef = useRef(sending);
  useEffect(() => {
    if (prevSendingRef.current && !sending) {
      refreshFileTree();
    }
    prevSendingRef.current = sending;
  }, [sending, refreshFileTree]);

  // Loading server-side attachments on session change + polling parse status
  // is owned by ``useSessionAttachments`` above.

  const ensureSession = useCallback(async () => {
    if (selectedSession) return selectedSession;
    if (!selectedWorkspaceId) throw new Error("No active workspace.");
    // For quick-chat (kind="chat"), send the ``"chat-default"`` sentinel so
    // the backend allocates a fresh, isolated chat workspace + cwd for this
    // session. Project conversations keep passing their real workspace id.
    const isChat = activeWorkspace?.kind === "chat";
    // 09-assistant §2.1/§2.2: every session binds to an agent — project
    // conversations to the chosen 派驻 member, 临时对话 to the picked "我的"
    // agent. There is no agentless path; the backend derives
    // runtime/model/provider/effort/skills/connectors from the agent, so the
    // model-picker fields below are ignored when it resolves the brain.
    // handleSend guards the empty-library case; this throw is the
    // type-narrowing backstop (10-new-conversation-guidance).
    if (!selectedAgentSlug) {
      throw new Error("No agent selected.");
    }
    const created = await sessionsApi.create({
      workspace_id: isChat ? "chat-default" : selectedWorkspaceId,
      agent_slug: selectedAgentSlug,
      provider_id: selectedProviderId ?? undefined,
      model_id: selectedModelId ?? undefined,
      runtime_id: selectedRuntimeId ?? undefined,
      mcp_provider_slugs:
        selectedMcpSlugs.length > 0 ? selectedMcpSlugs : undefined,
      permission_mode: selectedPermissionMode,
      effort: selectedEffort,
    });
    // 10-new-conversation-guidance slice 3: remember which agent this 临时对话
    // used so the next new conversation pre-selects it.
    if (isChat) setLastTempAgent(selectedAgentSlug);
    // Update local state IMMEDIATELY (before navigate / sendMessage)
    // so the rest of ``handleSend`` and the SSE subscription closures
    // — which capture ``selectedWorkspaceId`` and friends — see the
    // freshly minted ids. This is what the old ``refreshSessions``
    // race ultimately failed at.
    setSelectedSessionId(created.id);
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
    if (created.workspace_id !== selectedWorkspaceId) {
      // Quick-chat: the backend just minted a fresh workspace + cwd
      // for this session. Pull the authoritative ``WorkspaceDetail``
      // (cwd lives on the backend — host writes flow through
      // ``fs_registry`` and we never derive it locally) and merge.
      try {
        const wsDetail = await workspacesApi.get(created.workspace_id);
        setWorkspaces((prev) => {
          const filtered = prev.filter((w) => w.id !== wsDetail.id);
          return [...filtered, wsDetail];
        });
        // Also push into the global workspace store so the layout's
        // "New Chat" filter (``allWorkspaces.filter(kind === "chat")``)
        // immediately sees this row — without it, the new session
        // would still be filtered out of the chat group until the
        // path-change ``fetchProjects`` round-trip completes.
        upsertWorkspace(wsDetail);
      } catch {
        /* non-fatal — file tree falls back gracefully */
      }
      setSelectedWorkspaceId(created.workspace_id);
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
    if (id === NEW_SESSION_ID) {
      navigate(`/conversation/${created.id}`, { replace: true });
    }
    return created;
  }, [
    selectedWorkspaceId,
    activeWorkspace?.kind,
    fetchSidebarSessions,
    sidebarSessions,
    setSidebarSessions,
    upsertWorkspace,
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
    id,
    navigate,
  ]);

  const subscribeToSession = useCallback(
    (
      sessionId: string,
      afterSeq: number,
      opts: {
        requireUserBeforeTerminal?: boolean;
        expectedUserText?: string;
      } = {},
    ) => {
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const abort = new AbortController();
      let sawTurnStart = !opts.requireUserBeforeTerminal;
      let stopped = false;
      let pollTimer: number | null = null;
      abortRef.current = abort;
      setSending(true);

      const stopSubscription = () => {
        if (stopped) return;
        stopped = true;
        if (pollTimer !== null) {
          window.clearInterval(pollTimer);
          pollTimer = null;
        }
        void refreshActiveSession(sessionId);
        void fetchSidebarSessions();
        abort.abort();
      };

      const appendEvent = (event: SessionEventDTO) => {
        if (event.seq > 0) {
          maxSeqRef.current = Math.max(maxSeqRef.current, event.seq);
        }
        setEvents((prev) => {
          if (
            event.seq > 0 &&
            prev.some((existing) => existing.seq === event.seq)
          ) {
            return prev;
          }
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
        // Terminal turn signals — close the SSE stream so ``sending``
        // releases (loading dots disappear). The backend SSE generator
        // loops forever, so without an explicit abort here the
        // connection hangs until the user navigates away, leaving the
        // UI stuck in "agent running" state long after the turn
        // finished.
        const evType = event.event.event_type;
        if (evType === "message.user") {
          sawTurnStart =
            !opts.requireUserBeforeTerminal ||
            opts.expectedUserText === undefined ||
            event.event.payload.text === opts.expectedUserText;
        }
        const status = event.event.payload.status;
        const terminal =
          sawTurnStart &&
          (evType === "session.idle" ||
            evType === "run.failed" ||
            (evType === "session.update" &&
              status !== undefined &&
              status !== "" &&
              status !== "running" &&
              status !== "created"));
        if (terminal) {
          stopSubscription();
        }
      };

      pollTimer = window.setInterval(() => {
        if (stopped) return;
        sessionsApi
          .listEvents(sessionId, maxSeqRef.current)
          .then((response) => {
            for (const event of response.items) {
              appendEvent(event);
            }
          })
          .catch(() => {});
      }, 500);

      sessionsApi
        .subscribeEvents(
          sessionId,
          (event) => {
            appendEvent(event);
          },
          afterSeq,
          abort.signal,
        )
        .then(() => {
          void refreshActiveSession(sessionId);
          void fetchSidebarSessions();
        })
        .catch(() => {})
        .finally(() => {
          if (pollTimer !== null) {
            window.clearInterval(pollTimer);
            pollTimer = null;
          }
          setSending(false);
          if (abortRef.current === abort) {
            abortRef.current = null;
          }
        });
    },
    [fetchSidebarSessions, refreshActiveSession],
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
        await attachKbDocs(ids, ensureSession);
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
      void attachLocalFiles(files, ensureSession);
    },
    [attachLocalFiles, ensureSession],
  );

  // Delete a persisted session attachment row (KB-sourced or local).
  // Hoisted so both the side panel's remove button and the composer's
  // pinned-chip remove button share it.
  const handleRemoveSessionAttachment = useCallback(
    (attachmentId: string) => {
      void removeSessionAttachmentRow(attachmentId);
    },
    [removeSessionAttachmentRow],
  );

  // The actual send. Attachments are uploaded on attach, so this never
  // uploads — it just mints/reuses the session and posts the message.
  const performSend = async () => {
    if (!draft.trim() || sending) return;
    // 10-new-conversation-guidance: every conversation binds an agent. A new
    // 临时对话 with an empty library / no pick has no agent — nudge the user to
    // pick (via the 🤖 「+ Agent」 menu or the banner) instead of creating a
    // session with a null agent.
    if (!selectedSession && !selectedAgentSlug) {
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
      fromSeq: maxSeqRef.current,
      sentAt: Date.now(),
    });
    setSending(true);
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
      // ``ensureSession`` itself takes care of swapping
      // ``/conversation/new`` → ``/conversation/{real-id}`` (replace:true)
      // when a brand-new session is minted, so handleSend doesn't need
      // its own navigate fallback here.

      // Attachments were already uploaded (on attach) and the backend's
      // ``_run_agent_background`` reads this session's pending
      // SessionAttachmentRow rows, threading ``parsed_path`` (or the raw
      // ``stored_path`` when a parse hasn't finished) into kernel
      // ``UserMessage.attachments[]`` plus the ``additional-context`` hint —
      // so the prompt text stays clean. Nothing to upload here.

      // Prompt text is sent verbatim — no attachment hint appended.
      const outboundText = text;

      subscribeToSession(session.id, maxSeqRef.current, {
        requireUserBeforeTerminal: true,
        expectedUserText: outboundText,
      });

      const detail = await sessionsApi.sendMessage(
        session.id,
        outboundText,
        selectedProviderId,
        selectedModelId,
      );
      if (!detail?.id) throw new Error("Failed to send message.");
      // Attachments are per-turn: the backend ships this turn's pending
      // set with the message, then stamps those rows ``consumed_at`` once
      // the turn runs. Optimistically mark them consumed so they drop out
      // of the composer's staging chips immediately (they stay in the
      // panel's "uploaded files" history).
      markPendingConsumed();
      const updatedSession = sessionDetailToListItem(detail);
      const mergeStartedSession = (current: SessionListItem) => ({
        ...updatedSession,
        status:
          current.status === "idle" && updatedSession.status === "running"
            ? current.status
            : updatedSession.status,
      });
      setSessions((prev) =>
        prev.some((s) => s.id === updatedSession.id)
          ? prev.map((s) =>
              s.id === updatedSession.id ? mergeStartedSession(s) : s,
            )
          : [updatedSession, ...prev],
      );
      setSidebarSessions(
        sidebarSessions.some((s) => s.id === updatedSession.id)
          ? sidebarSessions.map((s) =>
              s.id === updatedSession.id ? mergeStartedSession(s) : s,
            )
          : [updatedSession, ...sidebarSessions],
      );
      setSelectedSessionId(detail.id);
    } catch (cause) {
      const msg =
        cause instanceof Error ? cause.message : "Failed to send message.";
      setError(msg);
      toast.error(msg);
      setSending(false);
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
      pinNextTurnToTopRef.current = false;
      keepCurrentTurnAtTopRef.current = false;
      // Optimistic turn never got a real ``message.user`` echo because
      // the send failed — drop it so the user can retry without a
      // phantom card lingering.
      setPendingUserMessage(null);
    } finally {
      // ``subscribeToSession`` (success path) has already set abortRef, so
      // the auto-resume effect's ``!abortRef.current`` guard is now active
      // and the flag can be released. On the error path we abort the send
      // outright, so likewise safe to clear.
      isSendInFlightRef.current = false;
    }
  };

  // Send entry point. Blocks on attachments that are still parsing — the
  // confirm dialog lets the user wait or submit with only the raw file
  // (no parsed content / doc-search until parsing finishes).
  const handleSend = () => {
    if (!draft.trim() || sending) return;
    if (attachmentsParsing) {
      setParsingConfirmOpen(true);
      return;
    }
    void performSend();
  };

  const handleInterrupt = async () => {
    if (!selectedSessionId) return;
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    try {
      await sessionsApi.interrupt(selectedSessionId);
      await refreshEvents(selectedSessionId);
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
        | "approve"
        | "approve_with_changes"
        | "approve_for_session"
        | "reject",
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
    // provider id makes the selector silently fall back to the workspace
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
          | "default"
          | "auto_review"
          | "full_access",
      );
    }
    // Kernel V5+bba3014: reconcile the effort selector to the live
    // session so live-reconcile PATCHes start from the persisted value.
    setSelectedEffort(
      (selectedSession.effort as
        | "low"
        | "medium"
        | "high"
        | "xhigh"
        | "max"
        | null
        | undefined) ?? null,
    );
  }, [
    selectedSession?.id,
    selectedSession?.locked_model_id,
    selectedSession?.locked_provider_id,
    selectedSession?.runtime_provider,
    selectedSession?.permission_mode,
    selectedSession?.effort,
  ]);

  // Tear down the previous session's in-flight SSE stream on a real
  // session→session switch.
  //
  // Without this, a previous running session's ``AbortController`` stays
  // in ``abortRef`` after the user switches away, with two consequences:
  //   1. The old stream keeps emitting ``appendEvent`` into the shared
  //      ``events`` state, which now displays the NEW session — the old
  //      session's deltas leak into the new session's transcript.
  //   2. The auto-resume effect below gates on ``!abortRef.current``;
  //      with the stale controller still set, the new session never
  //      starts its own SSE, so neither historical replay (events the
  //      kernel persisted before we attached) nor live deltas reach
  //      the UI. Switching to a running session would silently strand.
  //
  // We deliberately skip the ``null → sessionId`` transition: ``handleSend``
  // creates a new session via ``ensureSession`` (which calls
  // ``setSelectedSessionId``), then synchronously calls
  // ``subscribeToSession`` for that same id BEFORE React commits the
  // state change. When the commit lands, ``abortRef.current`` already
  // belongs to the just-created session — aborting here would kill the
  // SSE we just opened. Page unmount is still covered by the ``[]``
  // cleanup further below.
  const prevSelectedSessionIdRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevSelectedSessionIdRef.current;
    prevSelectedSessionIdRef.current = selectedSessionId;
    if (prev === null || prev === selectedSessionId) return;
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    // Release the loading state so the new session doesn't inherit
    // the previous session's "agent running" composer disable.
    setSending(false);
  }, [selectedSessionId]);

  // Session-load side effects — refresh the canonical session detail
  // every time we navigate onto a session id. This used to only hydrate
  // ``todos`` from ``session.todos`` but is now also load-bearing for
  // SSE auto-reconnect: the sessions LIST that ``bootstrap`` populates
  // can be stale by the time React commits (the kernel may have flipped
  // ``status`` between the list fetch and our render), so we re-fetch
  // the detail to learn the current status before deciding to resume.
  //
  // Three things happen against the freshly-fetched detail:
  //   1. Hydrate ``todos`` from ``detail.todos`` (the persistent snapshot).
  //   2. If the agent is still ``running``, open an SSE stream from
  //      ``maxSeqRef.current`` so the page picks up the rest of the turn
  //      live. This is the path that makes refresh-mid-turn /
  //      leave-and-come-back keep streaming.
  //
  //      ``created`` is intentionally NOT a live status here: a freshly
  //      minted session (e.g. ``handleNewChat`` from the home page, or
  //      the skill-creator entry point) sits at ``created`` until its
  //      first user message lands. Subscribing in that window would call
  //      ``setSending(true)`` and leave the page stuck on the loading
  //      dots forever — there is nothing for the SSE stream to wait on.
  //      The ``handleSend`` race (send fired, kernel hasn't flipped to
  //      ``running`` yet) is already blocked by ``isSendInFlightRef``.
  useEffect(() => {
    if (!selectedSessionId) return;
    let cancelled = false;
    let pollTimer: number | null = null;
    const POLL_INTERVAL_MS = 2000;

    // The resume poll exists for one race: a session sits in
    // ``created`` for a hair before the kernel flips it to ``running``
    // (schedule-driven sessions and the moment between create + first
    // turn are the practical windows). Once the session is in any
    // terminal state — ``idle`` (turn finished, awaiting user input),
    // ``failed`` / ``cancelled`` / ``archived`` (will not transition
    // again without a fresh send) — there is nothing to wait for and
    // we'd just spin GETs every 2s forever.
    const TERMINAL_STATUSES = new Set([
      "idle",
      "failed",
      "cancelled",
      "archived",
    ]);
    const isTerminalStatus = (status: string) => TERMINAL_STATUSES.has(status);

    const stopPoll = () => {
      if (pollTimer !== null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    const tryResume = (detail: { id: string; status: string }) => {
      if (cancelled) return false;
      // Skip when ``handleSend`` is mid-flight: it will subscribe itself
      // once ``sendMessage`` returns, and a competing subscribe here
      // would double-deliver ``message.user`` and clobber the live
      // sending/abortRef state when its older promise finalises.
      if (
        detail.status === "running" &&
        !abortRef.current &&
        !isSendInFlightRef.current
      ) {
        subscribeToSession(detail.id, maxSeqRef.current);
        return true;
      }
      return false;
    };

    sessionsApi
      .get(selectedSessionId)
      .then((detail) => {
        if (cancelled) return;
        if (detail.todos !== undefined && detail.todos !== null) {
          setTodos(detail.todos);
        }
        if (tryResume(detail)) return;
        // The session has already settled — nothing to poll for.
        if (isTerminalStatus(detail.status)) return;

        // Status race window: a schedule-driven session (or any caller
        // that creates a session a hair before its first turn flips to
        // ``running``) may briefly read non-running on the initial GET.
        // Long-poll the status while the page is open so we still pick
        // up the turn whenever the kernel flips. The loop self-stops
        // when (a) we resume, (b) the status reaches a terminal state,
        // (c) ``handleSend`` claims the page via ``abortRef`` /
        // ``isSendInFlightRef``, or (d) the effect's cleanup runs
        // (session id change / unmount).
        pollTimer = window.setInterval(() => {
          if (cancelled) {
            stopPoll();
            return;
          }
          if (abortRef.current || isSendInFlightRef.current) {
            stopPoll();
            return;
          }
          sessionsApi
            .get(selectedSessionId)
            .then((next) => {
              if (cancelled) return;
              if (tryResume(next)) {
                stopPoll();
                return;
              }
              if (isTerminalStatus(next.status)) {
                stopPoll();
              }
            })
            .catch(() => {
              // Non-fatal — keep polling; transient errors shouldn't
              // strand the page in a non-resuming state.
            });
        }, POLL_INTERVAL_MS) as unknown as number;
      })
      .catch(() => {
        // Non-fatal — refreshEvents already hydrated todos from the
        // historical event log; we just won't auto-resume.
      });
    return () => {
      cancelled = true;
      stopPoll();
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
    // Unified panel for both project and chat workspaces. Chat (kind="chat")
    // is treated as a "special project" — same component, same visual style,
    // but with KB binding / project instructions / file tree / scheduled
    // tasks omitted. The panel's section visibility is data-driven: pass
    // ``undefined`` for sections that don't apply.
    const isProject = activeWorkspace?.kind === "project";
    // 09-assistant Phase B: 临时对话 collapses to a 2-column layout — no right
    // context panel until/unless a live session exists. Returning ``null``
    // makes the layout drop the aside column (WorkspaceLayoutBase reserves it
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
      parseStatus: a.parse_status as "parsing" | "ready" | "failed" | undefined,
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
        // KB binding tree — project sessions only, **read-only**: we
        // pass ``kbTree`` + ``bindings`` (so the checkbox state shows
        // which folders/files are bound) and ``onExpandKbFolder`` (so
        // the user can drill in to inspect), but deliberately omit
        // ``onToggleBinding`` — editing happens on the project detail
        // page and takes effect for the next session. Chat /
        // skill-creator workspaces pass ``undefined`` so the section
        // doesn't render at all.
        kbTree={isProject ? projectKbTree : undefined}
        bindings={isProject ? projectKbBindings : undefined}
        onExpandKbFolder={isProject ? handleExpandProjectKbFolder : undefined}
        fileTree={fileTree}
        fileTreeTitle={
          isProject
            ? t("project.fileTree" as Parameters<typeof t>[0])
            : t("conversation.generatedFiles" as Parameters<typeof t>[0])
        }
        fileTreeInTab={isProject}
        rootPath={
          isProject
            ? ((activeWorkspace as WorkspaceDetail)?.root_path ?? undefined)
            : t("conversation.workDir" as Parameters<typeof t>[0])
        }
        onOpenInFinder={() => {
          const ws = activeWorkspace as WorkspaceDetail | null;
          // Prefer the kernel-resolved cwd (project + chat both have one);
          // fall back to root_path so a stale backend that hasn't
          // populated ``cwd`` yet still works for project workspaces.
          const path = ws?.cwd ?? ws?.root_path;
          if (!path) {
            toast.info(t("conversation.noWorkDir" as Parameters<typeof t>[0]));
            return;
          }
          void revealInFinder(path);
        }}
        onFileDoubleClick={(relPath) => {
          // FileTreeNode.path is relative to the workspace cwd (built
          // by ``toFileTree`` from ``WorkspaceFileNode``). Resolve to
          // an absolute path before handing off to the main process,
          // which calls ``shell.openPath`` — opens files in their
          // OS-associated app (.py -> VSCode/PyCharm, .csv -> Excel/
          // Numbers, .md -> Typora/system editor, etc.).
          const ws = activeWorkspace as WorkspaceDetail | null;
          const cwd = ws?.cwd ?? ws?.root_path;
          if (!cwd) return;
          const sep = cwd.endsWith("/") ? "" : "/";
          void revealInFinder(`${cwd}${sep}${relPath}`);
        }}
        onRefreshFiles={refreshFileTree}
        collapsed={panelCollapsed}
        onCollapsedChange={(c) => panelSetCollapsed(c)}
        todos={todos}
      />
    );
  }, [
    activeWorkspace,
    fileTree,
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
  // refreshed asynchronously (per-workspace fetch, per-session fetch,
  // user-queued composer state) so without this the right-panel
  // collapsed effect below would run a SECOND time with the previous
  // session's stale ``hasData = true`` and edge-trigger an unwanted
  // expand on the new empty session. Each downstream effect re-fetches
  // its own slice for the new session, so this only clears the visible
  // surface — no data is lost. ``todos`` is already cleared
  // synchronously by ``refreshEvents``.
  useEffect(() => {
    setSessionAttachments([]);
    setFileTree([]);
  }, [selectedSessionId, setSessionAttachments]);

  // Drive the right-panel collapsed state from per-session data:
  //   * Project workspaces always have meaningful panel content
  //     (instructions / skills / KB / file tree) — open by default,
  //     and re-open on every session switch within the project.
  //   * Chat workspaces start collapsed and auto-expand on the
  //     empty → has-data edge (todos / attachments / files). Manual
  //     collapses survive subsequent growth (we only edge-trigger).
  const isProjectWorkspace = activeWorkspace?.kind === "project";
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
    if (isProjectWorkspace) return;
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
    isProjectWorkspace,
    todos,
    sessionAttachments,
    fileTree,
    panelSetCollapsed,
  ]);

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
                    className="inline-flex shrink-0 items-center gap-1 text-ink-meta transition-colors hover:text-ink-heading"
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
                <span className="flex shrink-0 items-center gap-1 rounded-md bg-brand/10 px-2 py-0.5 text-2xs text-brand">
                  <Sparkles className="h-3 w-3" />
                  Skill Creator {t("cron.model" as Parameters<typeof t>[0])}
                </span>
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
              <SessionStatusPill status={selectedSession?.status} />
              {sessionAgentSlug ? (
                <span className="flex shrink-0 items-center gap-1 rounded-md bg-brand/10 px-2 py-0.5 text-2xs text-brand">
                  <Bot className="h-3 w-3" />
                  {agentNameBySlug.get(sessionAgentSlug) ?? sessionAgentSlug}
                </span>
              ) : null}
              {activeWorkspace?.name && !isSkillCreatorMode ? (
                <span className="shrink-0 rounded-md bg-surface-soft px-2 py-0.5 text-2xs text-ink-meta">
                  {activeWorkspace.name}
                </span>
              ) : null}
            </div>
          </div>
        </header>

        {providers.length === 0 && !loading ? (
          <div className="flex flex-1 items-center justify-center p-8">
            <div className="max-w-sm text-center">
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-brand/10">
                <Settings className="h-6 w-6 text-brand" />
              </div>
              <h2 className="mb-2 text-base font-semibold text-ink-heading">
                {t("conversation.noModel" as Parameters<typeof t>[0])}
              </h2>
              <p className="mb-4 text-sm text-ink-body">
                {t("conversation.noModelHint" as Parameters<typeof t>[0])}
              </p>
              <button
                type="button"
                onClick={() => navigate("/settings")}
                className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90"
              >
                {t("conversation.goToSettings" as Parameters<typeof t>[0])}
              </button>
            </div>
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
                // Remount on session switch so the virtualizer's internal
                // state (scrollOffset, measurementsCache, itemSizeCache)
                // starts fresh — without this, residual offsets from the
                // previous session can position the new session's rows
                // before ResizeObserver has remeasured them, briefly
                // overlapping content visually.
                key={selectedSessionId ?? "new"}
                turns={effectiveTurns}
                scrollContainerRef={scrollContainerRef}
                sending={sending}
                loading={loading}
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
                onRevealFile={revealInFinder}
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
                sending &&
                  "animate-[border-breathe_1.8s_ease-in-out_infinite] border-brand/60",
              )}
            >
              <ArrowDown className="h-4 w-4 text-ink-body" />
            </button>
          )}

          {!selectedSession &&
            ((channelLoaded && !hasChannel) ||
              (isTempConversation &&
                myAgentsLoaded &&
                myAgents.length === 0)) && (
              <div className="mx-auto mb-2 flex w-full max-w-3xl items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs dark:border-amber-900/40 dark:bg-amber-950/30">
                <span className="text-ink-body">
                  {channelLoaded && !hasChannel
                    ? isTempConversation &&
                      myAgentsLoaded &&
                      myAgents.length === 0
                      ? t("conversation.noChannelAndAgentBanner" as I18nKey)
                      : t("conversation.noChannelBanner" as I18nKey)
                    : t("conversation.noAgentBanner" as I18nKey)}
                </span>
                <button
                  type="button"
                  onClick={() => navigate("/welcome")}
                  className="shrink-0 rounded-md bg-brand px-2.5 py-1 font-medium text-white transition-colors hover:bg-brand-hover"
                >
                  {t("conversation.noAgentBannerCta" as I18nKey)}
                </button>
              </div>
            )}
          <CreateAgentDialog
            open={createAgentOpen}
            onOpenChange={setCreateAgentOpen}
            onCreated={async (slug) => {
              try {
                const res = await agentsApi.listAgents();
                setMyAgents(res.agents);
              } catch {
                // best-effort refresh — the new agent still selects below
              }
              setSelectedAgentSlug(slug);
              setComposerTouched(true);
            }}
          />
          <Composer
            // Remount per route so the textarea's native autoFocus refires when
            // the user navigates to a different conversation (or back to the
            // "New Chat" entry); React Router otherwise keeps this page mounted.
            key={id ?? "new"}
            value={draft}
            onChange={setDraft}
            // Project conversations configure skills per-agent, so the inline
            // skill picker is hidden there; the assistant (non-project) chat
            // keeps it for global ``/`` skills.
            showSkillButton={!isProjectWorkspace}
            autoFocus
            onSend={() => {
              void handleSend();
            }}
            sending={sending}
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
                  | "parsing"
                  | "ready"
                  | "failed"
                  | undefined,
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
            // ADR-006: once a session exists both chips freeze (the locked
            // 🤖 chip shows the bound ``sessionAgentSlug``).
            agentLocked={selectedSession != null}
            onAgentChange={(slug) => {
              setSelectedAgentSlug(slug);
              setComposerTouched(true);
            }}
            // 09-assistant 📁 project chip: switches the draft between 临时对话
            // (chat-default) and a project workspace. The page stores the
            // ``"chat-default"`` sentinel for 临时, so the chip sees ``null``
            // when the active workspace isn't a project, and a change to
            // ``null`` maps back to the sentinel. Frozen once a session exists.
            projects={composerProjects}
            selectedWorkspaceId={
              isProjectWorkspace ? selectedWorkspaceId : null
            }
            workspaceLocked={selectedSession != null}
            onWorkspaceChange={(idOrNull) => {
              setSelectedWorkspaceId(idOrNull ?? "chat-default");
              // The picked skill belongs to the previous workspace scope —
              // drop it so a project-scoped skill can't leak into a 临时 send
              // (and vice versa). availableSkills reloads off the new id.
              setSelectedComposerSkill(null);
              setComposerTouched(true);
            }}
            onAddAgent={
              isProjectWorkspace && selectedWorkspaceId
                ? () =>
                    navigate(
                      `/projects/${encodeURIComponent(selectedWorkspaceId)}`,
                    )
                : () => setCreateAgentOpen(true)
            }
            // Only the project path greys the send button (a project with no
            // deployed members / no pick). 临时 conversations stay clickable
            // even with an empty library — handleSend then nudges the user to
            // pick/create an agent (10-new-conversation-guidance).
            sendDisabled={
              isProjectWorkspace &&
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
            // Project conversations read the reasoning-effort budget from the
            // bound agent (configured on the agent, not per-message), so the
            // Composer's effort selector is hidden for them — passing
            // ``undefined`` collapses the effort UI. Quick chats keep the
            // per-conversation picker.
            effort={isProjectWorkspace ? undefined : selectedEffort}
            onEffortChange={
              isProjectWorkspace
                ? undefined
                : (level) => {
                    setSelectedEffort(level);
                    if (!isNewSession && id) {
                      void sessionsApi.updateEffort(id, level).catch(() => {
                        /* non-fatal — surfaced by error toast pipeline */
                      });
                    }
                  }
            }
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
            skills={composerMentionSkills}
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

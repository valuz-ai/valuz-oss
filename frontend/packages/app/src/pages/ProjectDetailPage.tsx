import { useState, useEffect, useCallback, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Button,
  Composer,
  type ComposerAgentItem,
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
  DeleteConfirmDialog,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  ProjectDetailContextPanel,
  type FileTreeNode,
  type ProjectMemberItem,
  KnowledgeFileTreePicker,
  KnowledgeBaseAddDialog,
  StatusPill,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@valuz/ui";
import {
  CreateAutomationDialog,
  DeployAgentsDialog,
  TaskStatusLabel,
} from "@valuz/app/components";
import { FilePenLine, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  workspacesApi,
  sessionsApi,
  providersApi,
  automationsApi,
  connectorsApi,
  tasksApi,
  agentsApi,
  useComposerProviders,
  useModelDefaults,
  usePanelStore,
  useProjectLastUsed,
  useRuntimes,
  useSessionStore,
  type ActionKind,
  type AutomationItem,
  type RuntimeId,
  type Trigger,
  type WorkspaceDetail,
  type WorkspaceFileNode,
  type ProviderDetail,
  type ProviderListItem,
  type ConnectorItem,
  type Task,
  type TaskEvent,
  type Agent,
  type MemberWithAgent,
} from "@valuz/core";
import { modelLabel } from "@valuz/shared";
import type { SessionListItem } from "@valuz/shared";
import { useWorkspaceOutlet } from "@valuz/app/layout";
import { usePlatform } from "@valuz/app/platform";
import { useProjectKbBindings, useKbDocTree } from "@valuz/app/hooks";
import { RUNTIME_DISPLAY_NAME, useTranslation } from "@valuz/core";
import { toFileTree } from "../lib/file-tree";

const TEXT_EXTENSIONS = new Set([
  "txt",
  "md",
  "py",
  "ts",
  "tsx",
  "js",
  "jsx",
  "json",
  "yml",
  "yaml",
  "csv",
  "xml",
  "html",
  "css",
  "scss",
  "sh",
  "bash",
  "zsh",
  "toml",
  "ini",
  "cfg",
  "log",
  "sql",
  "go",
  "rs",
  "java",
  "c",
  "cpp",
  "h",
  "rb",
  "php",
  "swift",
  "kt",
  "vue",
  "svelte",
  "astro",
  "env",
  "gitignore",
  "dockerignore",
  "editorconfig",
  "prettierrc",
  "eslintrc",
]);

function isTextFile(name: string): boolean {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  // Handle dotfiles like .env, .gitignore
  if (name.startsWith(".") && TEXT_EXTENSIONS.has(name.slice(1))) return true;
  return TEXT_EXTENSIONS.has(ext);
}

/* ── Project Recents (PRD §03 2) ────────────────────────────── */

// Session status -> i18n key for the right-edge ``StatusPill`` on
// chat rows. The pill itself draws its color tone from the same
// status string via the shared ``status-tone`` palette.
const SESSION_STATUS_KEY: Record<string, string> = {
  running: "activity.statusRunning",
  idle: "activity.statusIdle",
  failed: "activity.statusFailed",
  cancelled: "activity.statusStopped",
  archived: "activity.statusStopped",
};

interface ProjectRecentsProps {
  sessions: SessionListItem[];
  onOpen: (sessionId: string) => void;
  onRename: (sessionId: string, currentLabel: string) => void;
  onDelete: (sessionId: string, label: string) => void;
}

const ProjectRecents = ({
  sessions,
  onOpen,
  onRename,
  onDelete,
}: ProjectRecentsProps) => {
  const { t } = useTranslation();
  if (sessions.length === 0) {
    return (
      <div className="px-3 py-12 text-center text-sm text-ink-meta">
        {t("project.noSessions" as Parameters<typeof t>[0])}
      </div>
    );
  }

  return (
    <ul className="flex flex-col gap-0.5">
      {sessions.map((s) => {
        const fallback = t("sidebar.newChat" as Parameters<typeof t>[0]);
        const title = s.name ?? s.last_user_message_text ?? fallback;
        return (
          <li key={s.id}>
            <ContextMenu>
              <ContextMenuTrigger asChild>
                <button
                  type="button"
                  onClick={() => onOpen(s.id)}
                  className="-mx-3 flex w-[calc(100%+24px)] items-center gap-2 rounded-xl bg-transparent px-3 py-2 text-left transition-colors hover:bg-surface-soft"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-base font-medium text-ink-heading">
                      {title}
                    </div>
                  </div>
                  {SESSION_STATUS_KEY[s.status] && (
                    <StatusPill
                      status={s.status}
                      label={t(
                        SESSION_STATUS_KEY[s.status] as Parameters<typeof t>[0],
                      )}
                    />
                  )}
                </button>
              </ContextMenuTrigger>
              <ContextMenuContent className="min-w-[140px]">
                <ContextMenuItem onSelect={() => onRename(s.id, s.name ?? "")}>
                  <FilePenLine />
                  {t("sidebar.rename" as Parameters<typeof t>[0])}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem
                  variant="destructive"
                  onSelect={() => onDelete(s.id, title)}
                >
                  <Trash2 />
                  {t("common.delete" as Parameters<typeof t>[0])}
                </ContextMenuItem>
              </ContextMenuContent>
            </ContextMenu>
          </li>
        );
      })}
    </ul>
  );
};

/* ── Project Tasks (PRD-NEXT §3.4 — lead-dispatch tasks) ─────── */

const TASK_STATUS_KEY: Record<string, string> = {
  draft: "task.statusDraft",
  active: "task.statusActive",
  paused: "task.statusPaused",
  stopped: "task.statusStopped",
  completed: "task.statusCompleted",
  failed: "task.statusFailed",
  blocked: "task.statusBlocked",
};

type TaskTiming = {
  created_at?: number;
  updated_at?: number;
  // Task status captured at fetch time. The event log is fetched lazily and
  // would otherwise go stale (a pause/resume/completion wouldn't refresh it),
  // leaving the elapsed clock walking an out-of-date event stream. Comparing
  // this against the live ``task.status`` lets us refetch on every status
  // change so ``pausedMillis`` always sees the latest ``paused`` event.
  status?: string;
  events?: TaskEvent[];
};

/** Total milliseconds the task spent paused, so the elapsed clock can
 * subtract it: a paused task's timer freezes, and a resumed one picks up
 * where it left off instead of jumping forward by the pause gap. Walks
 * ``paused`` → ``resumed`` pairs; an unmatched trailing ``paused`` means
 * the task is paused right now, so the open pause is counted up to ``end``
 * (which keeps the displayed elapsed pinned as wall-clock advances). */
function pausedMillis(events: TaskEvent[], end: number): number {
  let paused = 0;
  let pauseStart: number | null = null;
  for (const event of events) {
    const ts = new Date(event.created_at).getTime();
    if (Number.isNaN(ts)) continue;
    if (event.type === "paused") {
      pauseStart = ts;
    } else if (event.type === "resumed" && pauseStart !== null) {
      paused += Math.max(0, ts - pauseStart);
      pauseStart = null;
    }
  }
  if (pauseStart !== null) paused += Math.max(0, end - pauseStart);
  return paused;
}

function formatTaskElapsed(
  task: Task,
  timing: TaskTiming | undefined,
  now: Date,
  t: ReturnType<typeof useTranslation>["t"],
) {
  const events = timing?.events ?? [];
  const kickoff = events.find((event) => event.type === "kickoff") ?? events[0];
  const createdAt =
    kickoff?.created_at ?? task.created_at ?? timing?.created_at;
  if (!createdAt) return "";
  const start = new Date(createdAt).getTime();
  let end = now.getTime();
  if (["completed", "failed", "stopped"].includes(task.status)) {
    const terminal = [...events]
      .reverse()
      .find((event) =>
        ["task_completed", "kickoff_failed", "task_failed", "stopped"].includes(
          event.type,
        ),
      );
    const updatedAt =
      terminal?.created_at ?? task.updated_at ?? timing?.updated_at;
    if (updatedAt) end = new Date(updatedAt).getTime();
  } else if (task.status === "paused") {
    // Pin the clock at the pause moment. The event log on this page can lag
    // or cycle (pause/resume churn), so ``pausedMillis`` alone can miss the
    // current open pause and let the clock run. ``task.status`` is the live
    // signal, so freeze on the fresh ``task.updated_at`` (set on the pause)
    // rather than trusting the event stream to show the open pause.
    const pausedAt = task.updated_at ?? timing?.updated_at;
    if (pausedAt) end = new Date(pausedAt).getTime();
  }
  if (Number.isNaN(start) || Number.isNaN(end)) return "";
  const total = Math.max(
    0,
    Math.floor((end - start - pausedMillis(events, end)) / 1000),
  );
  if (total < 60) return t("task.durationSec", { sec: total });
  if (total < 3600) {
    return t("task.durationMinSec", {
      min: Math.floor(total / 60),
      sec: total % 60,
    });
  }
  return t("task.durationHourMin", {
    hour: Math.floor(total / 3600),
    min: Math.floor((total % 3600) / 60),
  });
}

function taskStatusLabel(
  task: Task,
  t: ReturnType<typeof useTranslation>["t"],
) {
  return TASK_STATUS_KEY[task.status]
    ? t(TASK_STATUS_KEY[task.status] as Parameters<typeof t>[0])
    : task.status;
}

interface ProjectTasksProps {
  tasks: Task[];
  members: ProjectMemberItem[];
  onOpen: (taskId: string) => void;
  onAddTask: () => void;
}

const ProjectTasks = ({
  tasks,
  members,
  onOpen,
  onAddTask,
}: ProjectTasksProps) => {
  const { t } = useTranslation();
  const [now, setNow] = useState(() => new Date());
  const [taskTimings, setTaskTimings] = useState<Record<string, TaskTiming>>(
    {},
  );
  const memberNameBySlug = useMemo(
    () => new Map(members.map((member) => [member.slug, member.name])),
    [members],
  );

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    // Refetch when we have no events yet OR the cached snapshot predates the
    // task's current status (e.g. it was just paused/resumed/completed) — so
    // the elapsed clock always reflects the latest pause boundaries.
    const stale = tasks.filter((task) => {
      const cached = taskTimings[task.id];
      return !cached?.events || cached.status !== task.status;
    });
    if (stale.length === 0) return;
    let cancelled = false;
    void Promise.all(
      stale.map(async (task) => {
        try {
          const detail = await tasksApi.getTask(task.id);
          return {
            id: task.id,
            timing: {
              created_at: detail.task.created_at,
              updated_at: detail.task.updated_at,
              // Pin to the prop status we compared against (not
              // ``detail.task.status``) so a fetch/prop race can't loop.
              status: task.status,
              events: detail.events,
            },
          };
        } catch {
          return null;
        }
      }),
    ).then((rows) => {
      if (cancelled) return;
      setTaskTimings((prev) => {
        const next = { ...prev };
        for (const row of rows) {
          if (row) next[row.id] = row.timing;
        }
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [tasks, taskTimings]);

  if (tasks.length === 0) {
    return (
      <div className="flex flex-col items-center px-3 pt-7 pb-12 text-center">
        <p className="max-w-[360px] text-sm text-ink-meta">
          {t("project.noTasksHint" as Parameters<typeof t>[0])}
        </p>
        <Button className="mt-4" size="sm" onClick={onAddTask}>
          <Plus className="h-3.5 w-3.5" />
          {t("project.addTaskBtn" as Parameters<typeof t>[0])}
        </Button>
      </div>
    );
  }

  return (
    <ul className="flex flex-col gap-0.5">
      {tasks.map((task) => {
        const elapsed = formatTaskElapsed(task, taskTimings[task.id], now, t);
        return (
          <li key={task.id}>
            <button
              type="button"
              onClick={() => onOpen(task.id)}
              className="-mx-3 flex w-[calc(100%+24px)] items-start gap-2 rounded-xl px-3 py-2 text-left transition-colors hover:bg-surface-soft"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-base font-medium text-ink-heading">
                    {task.title}
                  </span>
                </div>
                <p className="truncate text-[11px] leading-5 text-ink-meta">
                  {task.status === "active" && (
                    <span className="mr-1 inline-block h-[5px] w-[5px] rounded-full bg-[#725cf9] align-middle animate-pulse" />
                  )}
                  <span
                    className={
                      task.status === "active"
                        ? "text-[#725cf9]"
                        : "text-[#898f9c]"
                    }
                  >
                    <TaskStatusLabel status={task.status} />
                  </span>
                  {elapsed ? (
                    <>
                      {" · "}
                      {t("task.totalDuration" as Parameters<typeof t>[0], {
                        duration: elapsed,
                      })}
                    </>
                  ) : null}
                  <span className="mx-2 text-surface-border">|</span>
                  {memberNameBySlug.get(task.lead_agent_slug) ??
                    task.lead_agent_slug}
                </p>
              </div>
              <StatusPill
                status={task.status}
                label={taskStatusLabel(task, t)}
                className="mt-1"
              />
            </button>
          </li>
        );
      })}
    </ul>
  );
};

/* ── PLACEHOLDER_COMPONENT ──────────────────────────────────── */

export const ProjectDetailPage = () => {
  const { t } = useTranslation();
  const { deleteFile, revealInFinder } = usePlatform();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const {
    setRightPanel,
    setHeader,
    setMainClassName,
    setContentInnerClassName,
  } = useWorkspaceOutlet();
  const panelCollapsed = usePanelStore((s) => s.collapsed);
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);

  // Global sessions list — already fetched + kept fresh by
  // ``DesktopWorkspaceLayout``. Filter to this project to render the
  // per-project Recents below the composer (PRD §03 2).
  const allSessions = useSessionStore((s) => s.sessions);
  const renameSession = useSessionStore((s) => s.renameSession);
  const deleteSession = useSessionStore((s) => s.deleteSession);
  // Hide zombie sessions (a create that never sent a first turn) —
  // ``status === "created"`` is exactly that. Also hide task-internal
  // sessions (lead / dispatched sub-Runs, identified by
  // ``task_id != null``): they're an implementation detail of the
  // task run and live behind the task detail page, not on the
  // project's conversation list. Same filter pair as the sidebar
  // RECENTS in DesktopWorkspaceLayout.
  const projectSessions = useMemo(
    () =>
      allSessions.filter(
        (s) =>
          s.workspace_id === id && s.status !== "created" && s.task_id == null,
      ),
    [allSessions, id],
  );

  // Project detail page always has meaningful panel content
  // (instructions / skills / KB / file tree). Layout defaults the
  // panel to collapsed for chat workspaces; flip it open when the
  // user enters a project (or switches to another). Subsequent
  // manual collapses inside the same project are respected — the
  // effect only re-runs when ``id`` changes.
  useEffect(() => {
    if (id) panelSetCollapsed(false);
  }, [id, panelSetCollapsed]);

  const [workspace, setWorkspace] = useState<WorkspaceDetail | null>(null);
  // Lead-dispatch tasks for this project, shown in the centre history area
  // below Recents (PRD-NEXT §3.4). Non-critical for the project home.
  const [tasks, setTasks] = useState<Task[]>([]);
  // Member agents for the config panel's "Agents" section (PRD-NEXT §3.4).
  const [members, setMembers] = useState<ProjectMemberItem[]>([]);
  // Raw member rows kept alongside the panel-shaped ``members`` so the
  // hover-actions on each row can open the edit dialog with the agent's
  // full profile (model / instructions / skills / connectors / effort)
  // without a second fetch. Updated in the same callback as ``members``
  // so the two never drift.
  const [rawMembers, setRawMembers] = useState<MemberWithAgent[]>([]);
  // Member remove (解除派驻) dialog state. Editing is global — handled on the
  // agent detail page, not here (see openMember).
  const [memberDeleteTarget, setMemberDeleteTarget] = useState<string | null>(
    null,
  );
  const [memberDeleteBusy, setMemberDeleteBusy] = useState(false);
  // Project conversations bind to one of the project's configured agents
  // (instead of a raw model). This is the picker for the next session.
  const [selectedAgentSlug, setSelectedAgentSlug] = useState<string | null>(
    null,
  );
  // Library agents + add-agent dialog state. The config panel's
  // "Agents" [+] opens the same dialog the project tasks page uses.
  const [libraryAgents, setLibraryAgents] = useState<Agent[]>([]);
  const [addAgentOpen, setAddAgentOpen] = useState(false);

  const loadMembers = useCallback(async () => {
    if (!id) return;
    try {
      const res = await agentsApi.listMembers(id);
      const mapped = res.agents.map((m) => {
        const runtimeId = m.agent?.runtime_provider ?? null;
        return {
          id: m.member.id,
          name: m.agent?.name ?? m.member.agent_slug,
          slug: m.member.agent_slug,
          sourceAgentSlug: m.member.source_agent_slug,
          model: m.agent?.model ?? null,
          runtime: runtimeId,
          // Human-facing runtime label ("Claude Code") — keeps the
          // sidebar member row consistent with the task panel and
          // composer, instead of leaking the kernel id ("claude_agent").
          runtimeLabel: runtimeId
            ? (RUNTIME_DISPLAY_NAME[
                runtimeId as keyof typeof RUNTIME_DISPLAY_NAME
              ] ?? runtimeId)
            : undefined,
          // null agent = the shared library agent was removed (orphan).
          orphan: m.agent == null,
        };
      });
      setMembers(mapped);
      setRawMembers(res.agents);
      setSelectedAgentSlug((prev) =>
        prev && mapped.some((m) => m.slug === prev)
          ? prev
          : (mapped[0]?.slug ?? null),
      );
    } catch {
      setMembers([]);
      setRawMembers([]);
      setSelectedAgentSlug(null);
    }
  }, [id]);

  // ──────────────────────────────────────────────────────────────────
  // Member open / delete handlers. Live-reference 派驻 (08-agents-module
  // §派驻): a member is a *reference* to the shared library agent, so
  // "editing a member" === editing the global agent. Opening a member
  // navigates to the agent detail page (the single edit surface) with
  // ``fromProject`` state so it shows a "返回项目" back affordance. No
  // per-member fork/patch. ``useCallback`` keeps refs stable so the
  // ``setRightPanel`` effect (which lists them in deps) doesn't churn.
  // ──────────────────────────────────────────────────────────────────
  // Open the SHARED library agent detail. ``slug`` is the global library agent
  // slug (see ProjectMemberItem.sourceAgentSlug), not the project-local member
  // slug — using the member slug would 404 on /agents/:slug. AgentDetailPage
  // shows a "返回项目" affordance via the router state.
  const openMember = useCallback(
    (slug: string) => {
      if (!id) return;
      navigate(`/agents/${encodeURIComponent(slug)}`, {
        state: {
          fromProject: { id, name: workspace?.name ?? decodeURIComponent(id) },
        },
      });
    },
    [id, navigate, workspace?.name],
  );

  const confirmMemberDelete = useCallback(async () => {
    if (!memberDeleteTarget || !id) return;
    setMemberDeleteBusy(true);
    try {
      await agentsApi.deleteMember(id, memberDeleteTarget);
      toast.success(t("agent.memberDeleted", { slug: memberDeleteTarget }));
      setMemberDeleteTarget(null);
      await loadMembers();
    } catch (e) {
      toast.error(
        e instanceof Error && e.message
          ? e.message
          : t("agent.memberDeleteFailed"),
      );
    } finally {
      setMemberDeleteBusy(false);
    }
  }, [id, memberDeleteTarget, loadMembers, t]);

  // Load this project's tasks. Failures are swallowed — non-critical here.
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    tasksApi
      .listTasks(id)
      .then((res) => {
        if (!cancelled) setTasks(res.tasks);
      })
      .catch(() => {
        if (!cancelled) setTasks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Load this project's member agents + the Library agents the add-agent
  // dialog offers. Non-critical for the project home, so failures are quiet.
  useEffect(() => {
    void loadMembers();
  }, [loadMembers]);

  useEffect(() => {
    let cancelled = false;
    agentsApi
      .listAgents()
      .then((res) => {
        if (!cancelled) setLibraryAgents(res.agents);
      })
      .catch(() => {
        if (!cancelled) setLibraryAgents([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const [fileTree, setFileTree] = useState<FileTreeNode[]>([]);
  const [instructions, setInstructions] = useState("");
  // KB binding state — shared with the conversation page through this hook
  // so toggles made on either side stay in sync. The hook owns the load
  // lifecycle keyed off ``id``.
  const {
    kbTree,
    bindings,
    handleToggleBinding,
    handleExpandKbFolder,
    handleSetAddedKbs,
    handleRemoveKb,
    handleSelectAllInKb,
  } = useProjectKbBindings(id);
  const [kbAddDialogOpen, setKbAddDialogOpen] = useState(false);
  const [composerValue, setComposerValue] = useState("");
  // Files queued by the composer's attachment menu / drag-drop. Uploaded
  // during handleSend (after the session is created) and rendered in the
  // context panel's "Upload files" section in the meantime.
  const [pendingAttachments, setPendingAttachments] = useState<File[]>([]);
  // PRD-PAAT §3.2 unified composer mode. ``chat`` creates a normal
  // session; ``task`` kicks off a background Task via tasksApi.kickoff
  // and routes to the task detail page.
  const [composerMode, setComposerMode] = useState<"chat" | "task">("chat");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [connectors, setConnectors] = useState<ConnectorItem[]>([]);
  const [selectedMcpSlugs, setSelectedMcpSlugs] = useState<string[]>([]);
  const [providers, setProviders] = useState<ProviderDetail[]>([]);
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(
    null,
  );
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  // Runtime / provider / model start as ``null`` and are seeded by the
  // Settings → Default tuple via ``useModelDefaults`` (effect below).
  // Falling back to the first available runtime is handled by the
  // existing repair effect when the user's default isn't available.
  const [selectedRuntimeId, setSelectedRuntimeId] = useState<RuntimeId | null>(
    null,
  );
  // ``true`` once the user touches any composer picker (runtime / model
  // / provider). Locks out reseeds so explicit choices survive
  // re-renders.
  const [composerTouched, setComposerTouched] = useState(false);
  // ADR-013/014 permission mode picker for the new session created from
  // this page. Frozen at create time per ADR-006 — once the session
  // exists, mid-session changes go through PATCH /permission-mode on
  // the conversation page. Default ``full_access`` matches the kernel
  // and conversation-page fallback so CI / batch flows don't park.
  const [selectedPermissionMode, setSelectedPermissionMode] = useState<
    "default" | "auto_review" | "full_access"
  >("full_access");
  // Reasoning-effort budget for the new session created from this page
  // (kernel V5+bba3014 ``ModelSettings.effort``). ``null`` lets the
  // runtime fall through to its SDK default. Seeded from
  // ``modelDefaults.default_effort`` alongside the model picker below.
  const { defaults: modelDefaults, loading: defaultsLoading } =
    useModelDefaults();
  // Per-project memory: the picker seeds from the workspace's most
  // recent session before falling back to Settings → Default. So if
  // the user mostly drives this project with Deep Agents but their
  // global default is Claude Code, the project still opens in Deep
  // Agents next time.
  const { pick: lastPick, loading: lastPickLoading } = useProjectLastUsed(id);
  // Seed sequence (highest wins on first pass; once any source lands
  // we don't override until the user manually picks something else):
  //   1. Project last-used (per-workspace memory)
  //   2. Global Settings → Default
  // Either source is enough — we wait until both fetches are done
  // before deciding so we don't briefly flash the global default and
  // then snap to the per-project value.
  useEffect(() => {
    if (composerTouched) return;
    if (lastPickLoading || defaultsLoading) return;
    const runtime =
      lastPick?.runtime_provider ?? modelDefaults?.default_runtime ?? null;
    const providerId =
      lastPick?.provider_id ?? modelDefaults?.default_provider_id ?? null;
    const modelId = lastPick?.model_id ?? modelDefaults?.default_model ?? null;
    if (runtime) {
      setSelectedRuntimeId(runtime as RuntimeId);
    }
    if (providerId) {
      setSelectedProviderId(providerId);
    }
    if (modelId) {
      setSelectedModelId(modelId);
    }
  }, [
    lastPick,
    lastPickLoading,
    modelDefaults,
    defaultsLoading,
    composerTouched,
  ]);
  const { runtimes: runtimeList } = useRuntimes();
  useEffect(() => {
    // Wait for both the Settings-default fetch and the project last-pick
    // fetch — otherwise this effect races in first (runtimes are
    // module-cached) and locks the picker to ``firstAvailable`` before
    // the user's configured defaults land.
    if (defaultsLoading || lastPickLoading) return;
    if (runtimeList.length === 0) return;
    const current = runtimeList.find((rt) => rt.id === selectedRuntimeId);
    if (current && current.available) return;
    const firstAvailable = runtimeList.find((rt) => rt.available);
    if (firstAvailable) {
      setSelectedRuntimeId(firstAvailable.id as RuntimeId);
    }
  }, [runtimeList, selectedRuntimeId, defaultsLoading, lastPickLoading]);
  const [kbPickerOpen, setKbPickerOpen] = useState(false);
  // Global KB document tree for the attachment picker — loads lazily
  // when the picker opens. Distinct from ``useProjectKbBindings``:
  // that drives the workspace binding tree (kb/folder/document
  // granularity, editable here on the project page), this one is the
  // file picker's navigable source (every KB, file-selectable only).
  const {
    kbTree: pickerKbTree,
    loading: pickerKbLoading,
    expandFolder: pickerExpandFolder,
  } = useKbDocTree(kbPickerOpen);
  const [scheduledTasks, setScheduledTasks] = useState<AutomationItem[]>([]);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewFileName, setPreviewFileName] = useState("");
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [newTaskOpen, setNewTaskOpen] = useState(false);
  const composerProviders = useComposerProviders(
    providers,
    selectedRuntimeId ?? undefined,
  );

  // Project-agent options for the composer's Agent selector. The session
  // inherits runtime/model/provider/effort/skills/connectors from the
  // chosen agent, so this page no longer exposes a raw model picker.
  const composerAgents = useMemo<ComposerAgentItem[]>(
    () =>
      members.map((m) => ({
        slug: m.slug,
        name: m.name,
        runtimeLabel:
          runtimeList.find((r) => r.id === m.runtime)?.display_name ??
          m.runtime ??
          "",
        modelLabel: modelLabel(m.model ?? ""),
      })),
    [members, runtimeList],
  );

  // Auto-pick first usable (provider, model) so Send never falls
  // through to backend default and 422s. Waits for the Settings-default
  // fetch so the picker doesn't briefly flash to ``isDefault`` provider
  // before the configured global default lands.
  useEffect(() => {
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
  }, [composerProviders, selectedProviderId, selectedModelId, defaultsLoading]);

  // Standalone file tree refresh — mirrors the conversation page's
  // ``refreshFileTree`` so the panel's manual ``FileRefreshButton``
  // can re-list files without re-fetching skills / KB / providers
  // (which ``fetchData`` does). Same depth-3 listing as initial load.
  const refreshFileTree = useCallback(() => {
    if (!id) return;
    workspacesApi
      .listFiles(id, { depth: 3 })
      .then((res) => setFileTree(toFileTree(res.files)))
      .catch(() => setFileTree([]));
  }, [id]);

  const fetchData = useCallback(async () => {
    try {
      const ws = await workspacesApi.get(id);
      setWorkspace(ws);
      setInstructions(ws.instructions_md ?? "");

      const [filesRes, chListRes] = await Promise.all([
        workspacesApi
          .listFiles(id, { depth: 3 })
          .catch(() => ({ files: [] as WorkspaceFileNode[] })),
        providersApi
          .list()
          .catch(() => ({ providers: [] as ProviderListItem[] })),
      ]);
      setFileTree(toFileTree(filesRes.files));
      // Skills are bound on the Agent now (08-agents-module), not the
      // project — no per-workspace skill catalog fetch here.
      // KB tree + bindings are owned by ``useProjectKbBindings``.

      const details = await Promise.all(
        chListRes.providers
          .filter((c) => c.enabled)
          .map((c) => providersApi.get(c.id).catch(() => null)),
      );
      setProviders(details.filter((d): d is ProviderDetail => d !== null));

      // Load automations for this project
      try {
        const schedRes = await automationsApi.listGroups(id);
        const projectTasks = schedRes.groups.flatMap((g) => g.automations);
        setScheduledTasks(projectTasks);
      } catch (err) {
        console.warn("[ProjectDetail] Failed to load automations:", err);
      }

      // Load MCP connectors for the right panel
      try {
        const [connRes, mcpRes] = await Promise.all([
          connectorsApi.list(),
          workspacesApi
            .getMcpServers(id)
            .catch(() => ({ slugs: [] as string[] })),
        ]);
        const active = connRes.connectors.filter((c) => c.enabled);
        setConnectors(active);
        setSelectedMcpSlugs(mcpRes.slugs);
      } catch {
        /* non-fatal */
      }
    } catch {
      toast.error(t("project.loadFailed" as Parameters<typeof t>[0]));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  /* ── KB binding helpers — owned by ``useProjectKbBindings``. ──── */

  const handleImportFile = useCallback(() => {
    setKbAddDialogOpen(true);
  }, []);

  const handleOpenKbPicker = useCallback(() => {
    // ``useKbDocTree`` is gated on ``kbPickerOpen`` — opening the
    // picker triggers the tree fetch; no manual load needed here.
    setKbPickerOpen(true);
  }, []);

  const handleKbPickerConfirm = useCallback(
    async (ids: string[]) => {
      setKbPickerOpen(false);
      if (ids.length === 0) return;
      // Project-detail composer is pre-session: it mints a session
      // inside ``handleSend`` and then navigates to ``/conversation/{id}``.
      // KB picks reach attachments by the same unified pipeline as
      // ConversationPage, but we need a real session id first. We
      // create it lazily here too — the side effect is that the
      // user sees a draft "session" on the sidebar immediately, same
      // as if they had typed a message and pressed send. If the
      // user abandons the page without sending, the session sits
      // empty (no turns); valuz already handles cleanup of zero-turn
      // sessions on next index.
      try {
        const session = await sessionsApi.create({
          workspace_id: id ?? undefined,
          agent_slug: selectedAgentSlug ?? undefined,
          permission_mode: selectedPermissionMode,
        });
        await sessionsApi.addKbAttachments(session.id, ids);
        navigate(`/conversation/${session.id}`);
      } catch {
        toast.error(t("common.failed" as Parameters<typeof t>[0]));
      }
    },
    [id, selectedAgentSlug, selectedPermissionMode, navigate],
  );

  // NOTE: the legacy ``scheduleDefaultModelId / scheduleDefaultProviderId
  // / scheduleRuntimeOptions`` memos were removed when the create dialog
  // migrated to the agent-driven ``CreateAutomationDialog`` per ADR-021:
  // execution identity now travels with the bound agent, so model /
  // provider / runtime defaults aren't passed through this layer
  // anymore. Re-derive from the composer state only if a future field
  // genuinely needs them at this scope.

  const handleCreateTask = async (data: {
    name: string;
    prompt_template: string;
    agent_slug: string;
    trigger: Trigger;
    action_kind: ActionKind;
  }) => {
    // Project detail page is bound to a specific project workspace by URL —
    // ``workspace_kind="project"`` + the project's id is the only valid pair
    // here. agent_kind is "project_member" (chat-only library_agent has no
    // meaning inside a project).
    await automationsApi.create({
      name: data.name,
      workspace_kind: "project",
      workspace_id: id,
      agent_kind: "project_member",
      agent_slug: data.agent_slug,
      prompt_template: data.prompt_template,
      trigger: data.trigger,
      action_kind: data.action_kind,
    });
    toast.success(t("project.taskCreated" as Parameters<typeof t>[0]));
    const schedRes = await automationsApi.listGroups(id);
    setScheduledTasks(schedRes.groups.flatMap((g) => g.automations));
  };

  const reloadScheduledTasks = useCallback(async () => {
    const schedRes = await automationsApi.listGroups(id);
    setScheduledTasks(schedRes.groups.flatMap((g) => g.automations));
  }, [id]);

  const handleToggleScheduledTask = useCallback(
    async (taskId: string, nextStatus: "on" | "off") => {
      try {
        if (nextStatus === "on") {
          await automationsApi.resume(taskId);
          toast.success(t("common.created" as Parameters<typeof t>[0]));
        } else {
          await automationsApi.pause(taskId);
          toast.success(t("common.deleted" as Parameters<typeof t>[0]));
        }
        await reloadScheduledTasks();
      } catch {
        toast.error(t("common.failed" as Parameters<typeof t>[0]));
      }
    },
    [reloadScheduledTasks],
  );

  const handleDeleteScheduledTask = useCallback(
    async (taskId: string) => {
      const task = scheduledTasks.find((it) => it.automation_id === taskId);
      const confirmed = window.confirm(
        t("cron.confirmDeleteTaskDesc" as Parameters<typeof t>[0], {
          name: task?.name ?? "",
        }),
      );
      if (!confirmed) return;
      try {
        await automationsApi.delete(taskId);
        toast.success(t("common.deleted" as Parameters<typeof t>[0]));
        await reloadScheduledTasks();
      } catch {
        toast.error(t("common.deleteFailed" as Parameters<typeof t>[0]));
      }
    },
    [reloadScheduledTasks, scheduledTasks],
  );

  const handleOpenInFinder = () => {
    if (workspace?.root_path) {
      void revealInFinder(workspace.root_path);
    }
  };

  const handleFileDoubleClick = (relPath: string) => {
    const fileName = relPath.split("/").pop() ?? relPath;
    if (!isTextFile(fileName)) {
      if (workspace?.root_path) {
        void revealInFinder(`${workspace.root_path}/${relPath}`);
      }
      return;
    }
    // Text file: read content and show preview dialog
    if (workspace?.root_path) {
      setPreviewFileName(fileName);
      setPreviewContent(null);
      setPreviewOpen(true);
      (
        window as unknown as {
          valuzDesktop?: {
            invoke: <T>(ch: string, args?: unknown) => Promise<T>;
          };
        }
      ).valuzDesktop
        ?.invoke<{ content: string | null }>("read_file_content", {
          path: `${workspace.root_path}/${relPath}`,
        })
        .then((res) => setPreviewContent(res.content))
        .catch(() => setPreviewContent(null));
    }
  };

  const handleInstructionsChange = async (md: string) => {
    setInstructions(md);
    try {
      await workspacesApi.updateInstructions(id, md);
    } catch {
      toast.error(t("project.saveFailed" as Parameters<typeof t>[0]));
    }
  };

  const handleSend = async () => {
    const text = composerValue.trim();
    if (!text || sending) return;
    setSending(true);

    // PRD-PAAT §3.2 Task mode: treat the composer text as a task goal,
    // kick off via tasksApi.kickoff(), and route the user to the task
    // detail page. The composer's selected agent becomes the lead.
    // ``dispatch_mode: "async"`` (the default): the lead runs as a persistent
    // actor that can be re-woken across turns until finish_task — robust for
    // multi-turn orchestration / long-running members, and it gets the
    // host-side completion fallback. Title auto-derives from the first 60
    // chars of the goal so the task list stays readable.
    if (composerMode === "task") {
      if (!selectedAgentSlug) {
        toast.error(t("task.noLeadAgents" as Parameters<typeof t>[0]));
        setSending(false);
        return;
      }
      try {
        const task = await tasksApi.kickoff(id, {
          goal: text,
          lead_agent_slug: selectedAgentSlug,
          title: text.length > 60 ? text.slice(0, 60) : null,
          dispatch_mode: "async",
        });
        toast.success(t("task.kickedOff"));
        setComposerValue("");
        setPendingAttachments([]);
        navigate(`/tasks/${encodeURIComponent(task.id)}`);
      } catch (err) {
        // Surface the backend message (kickoff has a few well-known
        // 4xx reasons: lead agent has no model provider pinned, lead
        // not a member, workspace not found, ...). Logging too so the
        // dev console keeps the full stack for debugging.
        console.error("[tasksApi.kickoff] failed", err);
        const msg = err instanceof Error ? err.message : String(err);
        toast.error(`${t("task.kickoffFailed")}: ${msg}`);
      } finally {
        setSending(false);
      }
      return;
    }

    try {
      // ADR-006: model_provider is locked at session creation. Pass the
      // composer's pick here, not on the subsequent sendMessage (where the
      // backend ignores it).
      const session = await sessionsApi.create({
        workspace_id: id,
        agent_slug: selectedAgentSlug ?? undefined,
        permission_mode: selectedPermissionMode,
      });
      // Upload any queued attachments before sending so the kernel runtime
      // sees them on the first turn. Failures here don't block the message.
      for (const file of pendingAttachments) {
        try {
          await sessionsApi.uploadAttachment(session.id, file);
        } catch {
          toast.error(
            t("conversation.uploadFailed" as Parameters<typeof t>[0]),
          );
        }
      }
      setPendingAttachments([]);
      // ``text`` already contains any ``/slug`` tokens because Composer
      // serializes inline skill chips into its controlled value — don't
      // prepend ``selectedComposerSkill`` again or it ships duplicated.
      await sessionsApi.sendMessage(session.id, text);
      setComposerValue("");
      navigate(`/conversation/${session.id}`);
    } catch {
      toast.error(t("common.saveFailed" as Parameters<typeof t>[0]));
    } finally {
      setSending(false);
    }
  };

  const displayName = workspace?.name ?? decodeURIComponent(id);

  // Only show KBs that have at least one binding in the context panel.
  // A KB is "added" when any binding's target_id matches the kb id itself
  // or a node inside it (folder / document). We walk the tree the same
  // way containsNodeId does in the hook.
  const addedKbTree = useMemo(() => {
    const bindingIds = new Set(bindings.map((b) => b.target_id));
    return kbTree.filter((kb) => {
      if (bindingIds.has(kb.id)) return true;
      const walkNode = (node: (typeof kbTree)[0]): boolean => {
        if (bindingIds.has(node.id)) return true;
        return node.children?.some(walkNode) ?? false;
      };
      return walkNode(kb);
    });
  }, [kbTree, bindings]);

  // IDs of KBs currently added — passed to the add-dialog as pre-selected.
  const addedKbIds = useMemo(
    () => addedKbTree.map((kb) => kb.id),
    [addedKbTree],
  );

  useEffect(() => {
    setHeader(null);
    setMainClassName("min-w-[442px]");
    setContentInnerClassName("p-0");

    setRightPanel(
      <ProjectDetailContextPanel
        title={t("project.contextTab" as Parameters<typeof t>[0])}
        instructionsTitle={t("project.instruction" as Parameters<typeof t>[0])}
        scheduledTasksTitle={t("sidebar.automation" as Parameters<typeof t>[0])}
        showTodos={false}
        // Project home wants every rail section visible at a glance
        // (Project README, Agent Team, Files, KB, …). multiOpen turns
        // off the single-accordion exclusivity so each section is
        // independently toggleable and defaults to open.
        multiOpen
        initialOpenSection={null}
        instructions={instructions}
        onInstructionsChange={handleInstructionsChange}
        members={members}
        onAddMember={() => setAddAgentOpen(true)}
        onOpenMember={openMember}
        onRemoveMember={(slug) => setMemberDeleteTarget(slug)}
        // Skills + Connectors are configured per-Agent (in the agent editor),
        // not at the project level (PRD-NEXT §3.4) — so the project config
        // panel intentionally omits those sections.
        kbTree={addedKbTree}
        bindings={bindings.map((b) => ({
          binding_kind: b.binding_kind,
          target_id: b.target_id,
        }))}
        onToggleBinding={handleToggleBinding}
        onExpandKbFolder={handleExpandKbFolder}
        onRemoveKb={handleRemoveKb}
        onSelectAllInKb={handleSelectAllInKb}
        onImportFile={handleImportFile}
        scheduledTasks={scheduledTasks.map((it) => ({
          // Adapter: AutomationItem → the panel's generic row shape.
          // ``cron`` shows the technical expression (cron text / "Ns" /
          // "—" for manual) so the column stays font-mono parity;
          // ``humanReadable`` is the localised description.
          id: it.automation_id,
          name: it.name,
          cron:
            it.trigger.kind === "cron"
              ? it.trigger.cron_expr
              : it.trigger.kind === "interval"
                ? `${it.trigger.seconds}s`
                : "—",
          humanReadable: it.trigger_human_readable,
          status: (it.status === "enabled" ? "on" : "off") as "on" | "off",
          nextRun:
            it.next_run_at != null
              ? new Date(it.next_run_at).toLocaleString()
              : "—",
        }))}
        onAddScheduledTask={() => setNewTaskOpen(true)}
        onToggleScheduledTask={handleToggleScheduledTask}
        onDeleteScheduledTask={handleDeleteScheduledTask}
        fileTree={fileTree}
        fileTreeInTab
        rootPath={workspace?.root_path ?? ""}
        onRefreshFiles={refreshFileTree}
        onFileClick={(path) => {
          const fileName = path.split("/").pop() ?? path;
          setComposerValue((prev) => prev + (prev ? " " : "") + `@${fileName}`);
        }}
        onOpenInFinder={handleOpenInFinder}
        onFileDoubleClick={handleFileDoubleClick}
        onOpenInSystem={(path: string) => {
          void revealInFinder(path);
        }}
        onDeleteFile={async (path: string) => {
          const fullPath = workspace?.root_path
            ? `${workspace.root_path}/${path}`
            : path;
          const result = await deleteFile(fullPath);
          if (result.success) {
            toast.success(t("common.deleted" as Parameters<typeof t>[0]));
            void fetchData();
          } else {
            toast.error(
              `${t("common.deleteFailed" as Parameters<typeof t>[0])}: ${result.error}`,
            );
          }
        }}
        collapsed={panelCollapsed}
        onCollapsedChange={(c) => panelSetCollapsed(c)}
      />,
    );

    return () => {
      setRightPanel(null);
      setHeader(null);
      setMainClassName(undefined);
      setContentInnerClassName(undefined);
      // Don't reset setRightPanelCollapsed here — the effect's deps include
      // rightPanelCollapsed (the controlled prop on the panel rebuilds when
      // it changes), so resetting in cleanup creates a feedback loop that
      // immediately reverts the toggle. Conversation page hit the same bug.
    };
  }, [
    id,
    navigate,
    setRightPanel,
    setHeader,
    setMainClassName,
    setContentInnerClassName,
    panelCollapsed,
    panelSetCollapsed,
    instructions,
    members,
    addedKbTree,
    bindings,
    fileTree,
    workspace,
    displayName,
    pendingAttachments,
    scheduledTasks,
    handleToggleScheduledTask,
    handleDeleteScheduledTask,
    connectors,
    selectedMcpSlugs,
    refreshFileTree,
    openMember,
  ]);

  /* ── PLACEHOLDER_RENDER ─────────────────────────────────────── */

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <p className="text-sm text-muted-foreground">
          {t("common.loading" as Parameters<typeof t>[0])}
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Anchor the content stack at a stable top offset so the project title
          keeps a predictable visual position across desktop window sizes. */}
      <div className="flex flex-1 flex-col items-center px-6 pt-20">
        <div className="flex w-full min-w-[400px] max-w-[760px] flex-col items-center gap-5">
          <div className="text-center">
            <h2 className="text-2xl font-medium leading-tight text-ink-heading">
              {displayName}
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              {t("project.askAgent" as Parameters<typeof t>[0])}
            </p>
          </div>

          <div className="w-full" id="project-composer">
            <Composer
              autoFocus
              wrapperClassName="px-0"
              value={composerValue}
              onChange={setComposerValue}
              mode={composerMode}
              onModeChange={setComposerMode}
              onSend={() => {
                void handleSend();
              }}
              showSkillButton={false}
              onAttachmentsChange={(files) => setPendingAttachments(files)}
              onKBPick={() => void handleOpenKbPicker()}
              // Project conversations pick a configured agent instead of a
              // raw model. The session inherits runtime/model/provider/
              // effort/skills/connectors from the agent; the [+] opens the
              // same add-agent dialog the config panel uses.
              agents={composerAgents}
              selectedAgentSlug={selectedAgentSlug}
              onAgentChange={(slug) => {
                setSelectedAgentSlug(slug);
                setComposerTouched(true);
              }}
              onAddAgent={() => setAddAgentOpen(true)}
              sendDisabled={composerAgents.length === 0 || !selectedAgentSlug}
              permissionMode={selectedPermissionMode}
              onPermissionModeChange={(mode) => {
                setSelectedPermissionMode(mode);
                setComposerTouched(true);
              }}
            />
          </div>

          {/* Centre history area (PRD-NEXT §3.4): Chat (sessions) and Task
              (lead-dispatch tasks) split into two tabs. The Task tab always
              shows — empty state offers an "add task" affordance. */}
          <div className="mt-4 w-full pb-6">
            <Tabs defaultValue="tasks">
              <div className="flex items-center border-b border-surface-border">
                <TabsList
                  variant="line"
                  className="h-9 justify-start gap-4 border-0 p-0"
                >
                  <TabsTrigger value="tasks">
                    {t("project.tasksColumn" as Parameters<typeof t>[0])}
                  </TabsTrigger>
                  <TabsTrigger value="chat">
                    {t("project.chatTab" as Parameters<typeof t>[0])}
                  </TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="chat" className="mt-1">
                <ProjectRecents
                  sessions={projectSessions}
                  onOpen={(sid) => navigate(`/conversation/${sid}`)}
                  onRename={async (sid, current) => {
                    const next = window.prompt(
                      t("sidebar.rename" as Parameters<typeof t>[0]),
                      current,
                    );
                    if (next === null) return;
                    const trimmed = next.trim();
                    if (!trimmed || trimmed === current) return;
                    try {
                      await renameSession(sid, trimmed);
                      toast.success(
                        t("sidebar.renamed" as Parameters<typeof t>[0]),
                      );
                    } catch {
                      toast.error(
                        t("sidebar.renameFailed" as Parameters<typeof t>[0]),
                      );
                    }
                  }}
                  onDelete={async (sid, label) => {
                    if (
                      !window.confirm(
                        `${t("common.confirmDelete" as Parameters<typeof t>[0])}\n${label}`,
                      )
                    )
                      return;
                    try {
                      await deleteSession(sid);
                      toast.success(
                        t("sidebar.deleted" as Parameters<typeof t>[0]),
                      );
                    } catch {
                      toast.error(
                        t("sidebar.deleteFailed" as Parameters<typeof t>[0]),
                      );
                    }
                  }}
                />
              </TabsContent>
              <TabsContent value="tasks" className="mt-1">
                <ProjectTasks
                  tasks={tasks}
                  members={members}
                  onOpen={(taskId) => navigate(`/tasks/${taskId}`)}
                  onAddTask={() => {
                    // v2: there is no separate 新建任务 page anymore — the
                    // project composer's "task" mode is the new entry. Switch
                    // it and scroll the composer back into view.
                    setComposerMode("task");
                    setComposerValue("");
                    document
                      .getElementById("project-composer")
                      ?.scrollIntoView({ behavior: "smooth", block: "center" });
                  }}
                />
              </TabsContent>
            </Tabs>
          </div>
        </div>
      </div>

      {/* Project automation create — uses the same agent-driven dialog
          as the global Automation page, with task mode enabled (this is
          a project workspace) and candidates resolved from the project's
          members. ``description`` keeps the existing project-specific
          hint copy ("Tasks created here are linked to this project"). */}
      <CreateAutomationDialog
        open={newTaskOpen}
        onOpenChange={setNewTaskOpen}
        description={t("project.instruction" as Parameters<typeof t>[0])}
        onSubmit={handleCreateTask}
        agents={rawMembers.map((entry) => ({
          slug: entry.member.agent_slug,
          name: entry.agent?.name ?? entry.member.agent_slug,
        }))}
        allowTaskMode
      />

      <DeployAgentsDialog
        open={addAgentOpen}
        onOpenChange={setAddAgentOpen}
        workspaceId={id}
        agents={libraryAgents}
        members={rawMembers}
        onChanged={loadMembers}
        onCreateNew={() => navigate("/agents")}
      />

      {/* File Preview Dialog */}
      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="max-w-[640px]">
          <DialogHeader>
            <DialogTitle>{previewFileName}</DialogTitle>
          </DialogHeader>
          <div className="max-h-[480px] overflow-auto rounded-md border border-surface-border bg-surface-base p-3">
            {previewContent === null ? (
              <div className="flex items-center justify-center py-10 text-sm text-ink-meta">
                {t("common.loading" as Parameters<typeof t>[0])}
              </div>
            ) : (
              <pre className="whitespace-pre-wrap break-all font-mono text-xs leading-5 text-ink-body">
                {previewContent}
              </pre>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPreviewOpen(false)}>
              {t("common.close" as Parameters<typeof t>[0])}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Knowledge Base file picker overlay — tree view: documents
          organised under their KB and folders; folders expandable for
          navigation, only files selectable. */}
      {kbPickerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="flex h-[600px] max-h-[85vh] w-[720px] max-w-[92vw] flex-col rounded-xl border border-surface-border bg-card p-4 shadow-xl">
            <KnowledgeFileTreePicker
              kbTree={pickerKbTree}
              loading={pickerKbLoading}
              onExpandFolder={pickerExpandFolder}
              selected={[]}
              onCancel={() => setKbPickerOpen(false)}
              onConfirm={(ids) => {
                void handleKbPickerConfirm(ids);
              }}
            />
          </div>
        </div>
      )}

      {/* KB add/remove dialog — lists all KBs with checkboxes; added KBs
          pre-checked. Confirm atomically updates project bindings. */}
      <KnowledgeBaseAddDialog
        open={kbAddDialogOpen}
        onOpenChange={setKbAddDialogOpen}
        kbs={kbTree.map((kb) => ({
          id: kb.id,
          name: kb.name,
          documentCount: kb.documentCount,
        }))}
        selectedIds={addedKbIds}
        onConfirm={(ids) => {
          void handleSetAddedKbs(ids);
        }}
      />

      <DeleteConfirmDialog
        open={memberDeleteTarget !== null}
        onOpenChange={(v) => !v && setMemberDeleteTarget(null)}
        title={t("agent.confirmDeleteMember")}
        description={
          memberDeleteTarget
            ? t("agent.confirmDeleteMemberDesc", { slug: memberDeleteTarget })
            : undefined
        }
        confirmLabel={t("common.remove")}
        loading={memberDeleteBusy}
        onConfirm={confirmMemberDelete}
      />
    </div>
  );
};

import {
  useState,
  useEffect,
  useLayoutEffect,
  useCallback,
  useMemo,
  useRef,
} from "react";
import { useParams, useNavigate, useSearchParams } from "react-router-dom";
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
  ArtifactViewerShell,
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
  RenameInput,
  RowActionsMenu,
  formatCreatedAt,
} from "@valuz/app/components";
import {
  Clock3,
  FilePenLine,
  ChevronRight,
  ListChecks,
  MessageSquare,
  Plus,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import {
  projectsApi,
  ApiError,
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
  useSessionAttachments,
  useSessionStore,
  useProjectListAutoRefresh,
  useListScrollAnchor,
  type ActionKind,
  type AutomationItem,
  type RuntimeId,
  type Trigger,
  type ProjectDetail,
  type ProjectFileNode,
  type ArtifactDescriptor,
  type ArtifactContent,
  type LLMChannelDetail,
  type LLMChannel,
  type ConnectorItem,
  type Task,
  type Agent,
  type MemberWithAgent,
  skillsApi,
  type SkillView,
} from "@valuz/core";
import { modelLabel } from "@valuz/shared";
import { t as _t } from "@valuz/shared/i18n";
import type { SessionListItem } from "@valuz/shared";
import { useProjectOutlet } from "@valuz/app/layout";
import { usePlatform } from "@valuz/app/platform";
import { useProjectKbBindings, useKbDocTree } from "@valuz/app/hooks";
import { RUNTIME_DISPLAY_NAME, memoryApi, useTranslation } from "@valuz/core";
import {
  resolveAgentSkillItems,
  type AgentSkillItem,
} from "../lib/agent-skill-items";
import { toFileTree } from "../lib/file-tree";
import { BUCKET_KEY, groupByTimeBucket } from "../lib/time-buckets";
import { AttachmentParsingDialog } from "../components/AttachmentParsingDialog";

// Volatile fields that decide whether a polled task row differs from the one
// already on screen — if equal we reuse the old object reference so unaffected
// rows update in place (stable DOM nodes for the scroll anchor).
function taskEqual(a: Task, b: Task): boolean {
  return (
    a.id === b.id &&
    a.title === b.title &&
    a.status === b.status &&
    a.created_at === b.created_at &&
    a.updated_at === b.updated_at
  );
}

/**
 * Id-keyed in-place merge of a fresh full task list into the previous one
 * (plan §5 — replaces ``setTasks(res.tasks)`` whole-table overwrite). The
 * incoming list is the authoritative ``created_at``-DESC snapshot, so following
 * its order keeps existing rows in place (``created_at`` is immutable),
 * positions new rows by ``created_at``, drops rows that disappeared, and never
 * duplicates an id. Unchanged rows keep their previous reference.
 */
function mergeTasks(prev: Task[], incoming: Task[]): Task[] {
  const prevById = new Map(prev.map((t) => [t.id, t]));
  return incoming.map((t) => {
    const old = prevById.get(t.id);
    return old && taskEqual(old, t) ? old : t;
  });
}

/**
 * Walk up from ``el`` to the nearest ancestor that actually scrolls. The
 * project-detail page has no list-level scroller; the real one is the layout's
 * ``overflow-y-auto`` content container (plan review P2). An explicit
 * auto/scroll container is the intended scroller even before it overflows.
 */
function findScrollParent(el: HTMLElement | null): HTMLElement | null {
  let node = el?.parentElement ?? null;
  while (node) {
    const overflowY = getComputedStyle(node).overflowY;
    if (overflowY === "auto" || overflowY === "scroll") return node;
    node = node.parentElement;
  }
  return null;
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
  onRenameConfirm: (sessionId: string, name: string) => void;
  onDelete: (sessionId: string, label: string) => void;
}

const ProjectRecents = ({
  sessions,
  onOpen,
  onRenameConfirm,
  onDelete,
}: ProjectRecentsProps) => {
  const { t } = useTranslation();
  const [renamingId, setRenamingId] = useState<string | null>(null);
  if (sessions.length === 0) {
    return (
      <div className="px-3 py-12 text-center text-sm text-ink-meta">
        {t("project.noSessions" as Parameters<typeof t>[0])}
      </div>
    );
  }

  const grouped = groupByTimeBucket(sessions, (s) => s.updated_at);

  const renderRow = (s: SessionListItem) => {
    const fallback = t("sidebar.newChat" as Parameters<typeof t>[0]);
    const title = s.name ?? s.last_user_message_text ?? fallback;
    if (renamingId === s.id) {
      return (
        <li key={s.id} data-anchor-key={`chat-${s.id}`}>
          <div className="flex w-full items-center gap-2 rounded-xl px-3 py-3">
            <RenameInput
              initial={title}
              onConfirm={(v) => {
                onRenameConfirm(s.id, v);
                setRenamingId(null);
              }}
              onCancel={() => setRenamingId(null)}
            />
          </div>
        </li>
      );
    }
    return (
      <li key={s.id} data-anchor-key={`chat-${s.id}`} className="group relative">
        <ContextMenu>
          <ContextMenuTrigger asChild>
            <div
              role="button"
              tabIndex={0}
              onClick={() => onOpen(s.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onOpen(s.id);
                }
              }}
              className="flex w-full cursor-default items-center gap-2 rounded-xl bg-transparent px-3 py-3 text-left outline-none transition-colors hover:bg-surface-soft focus-visible:bg-surface-soft"
            >
              <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-heading">
                {title}
              </span>
              <span className="shrink-0 whitespace-nowrap text-[11px] text-ink-meta">
                {formatCreatedAt(s.updated_at, t)}
              </span>
              <span className="relative inline-flex min-w-6 shrink-0 items-center justify-center">
                {SESSION_STATUS_KEY[s.status] && (
                  <StatusPill
                    status={s.status}
                    label={t(
                      SESSION_STATUS_KEY[s.status] as Parameters<typeof t>[0],
                    )}
                    className="transition-opacity group-hover:opacity-0 group-has-[[data-state=open]]:opacity-0"
                  />
                )}
                <RowActionsMenu
                  onRename={() => setRenamingId(s.id)}
                  onDelete={() => onDelete(s.id, title)}
                />
              </span>
            </div>
          </ContextMenuTrigger>
          <ContextMenuContent className="min-w-[140px]">
            <ContextMenuItem onSelect={() => setRenamingId(s.id)}>
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
  };

  return (
    <div className="flex flex-col gap-5">
      {grouped.map(([bucket, bucketSessions]) => (
        <div key={bucket}>
          <div className="mb-1.5 px-3 text-[11.5px] font-normal uppercase tracking-[0.06em] text-ink-body">
            {t(BUCKET_KEY[bucket] as Parameters<typeof t>[0])}
          </div>
          <ul className="flex flex-col">{bucketSessions.map(renderRow)}</ul>
        </div>
      ))}
    </div>
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

function taskStatusLabel(
  task: Task,
  t: ReturnType<typeof useTranslation>["t"],
) {
  return TASK_STATUS_KEY[task.status]
    ? t(TASK_STATUS_KEY[task.status] as Parameters<typeof t>[0])
    : task.status;
}

/** "由 … 触发" provenance line under a task title; null for direct user actions. */
function taskTriggerLabel(
  task: Task,
  t: ReturnType<typeof useTranslation>["t"],
): string | null {
  const trig = task.trigger;
  if (!trig) return null;
  const k = (key: string) => key as Parameters<typeof t>[0];
  switch (trig.type) {
    case "automation":
      return t(k("task.triggeredByAutomation"), { name: trig.source_automation_name ?? "…" });
    case "agent":
      return trig.source_agent_slug
        ? t(k("task.triggeredByTask"), {
            title: trig.source_task_title ?? "…",
            agent: trig.source_agent_slug,
          })
        : t(k("task.triggeredByTaskNoAgent"), { title: trig.source_task_title ?? "…" });
    case "chat":
      return t(k("task.triggeredByChat"));
    default:
      return null; // "user" → no provenance line
  }
}

interface ProjectTasksProps {
  tasks: Task[];
  onOpen: (taskId: string) => void;
  onAddTask: () => void;
}

const ProjectTasks = ({ tasks, onOpen, onAddTask }: ProjectTasksProps) => {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  // Build the agent (task→task) dependency tree from the flat list: a task
  // triggered by another task that is also in the list nests UNDER that parent.
  // Everything else — chat / automation / user, or an agent-child whose parent
  // isn't loaded — stays a root and keeps its flat "由 … 触发" line.
  const { roots, childrenOf } = useMemo(() => {
    const present = new Set(tasks.map((tk) => tk.id));
    const kids = new Map<string, Task[]>();
    const rootList: Task[] = [];
    for (const tk of tasks) {
      // Nest under the originating task whenever one is recorded — directly
      // (agent create_task) OR transitively (an agent ran an automation that
      // spawned this task; trigger.type stays "automation" but source_task_id
      // points at the task whose agent invoked it).
      const parentId = tk.trigger?.source_task_id ?? null;
      if (parentId && present.has(parentId)) {
        const arr = kids.get(parentId) ?? [];
        arr.push(tk);
        kids.set(parentId, arr);
      } else {
        rootList.push(tk);
      }
    }
    return { roots: rootList, childrenOf: kids };
  }, [tasks]);

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

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const renderNode = (task: Task, depth: number) => {
    const kids = childrenOf.get(task.id) ?? [];
    const hasKids = kids.length > 0;
    const isCollapsed = collapsed.has(task.id);
    // Flat provenance line: always on roots; and keep the "由自动化…" line even
    // when nested (it explains HOW the parent task spawned this — via the
    // automation). Suppress the redundant "由任务…" on nested agent children
    // (the nesting itself already conveys the parent).
    const flatLabel =
      depth === 0 || task.trigger?.type === "automation"
        ? taskTriggerLabel(task, t)
        : null;
    return (
      <li key={task.id}>
        <div className="flex items-center">
          {hasKids ? (
            <button
              type="button"
              onClick={() => toggle(task.id)}
              aria-label={task.title}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft"
            >
              <ChevronRight
                className={`h-3.5 w-3.5 transition-transform ${isCollapsed ? "" : "rotate-90"}`}
              />
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => onOpen(task.id)}
            className="flex min-w-0 flex-1 cursor-default items-center gap-2 rounded-xl px-2 py-3 text-left transition-colors hover:bg-surface-soft"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-ink-heading">{task.title}</div>
              {flatLabel ? (
                <div className="mt-0.5 truncate text-[11px] text-ink-meta">{flatLabel}</div>
              ) : null}
            </div>
            <span className="shrink-0 whitespace-nowrap text-[11px] text-ink-meta">
              {formatCreatedAt(task.created_at, t)}
            </span>
            <StatusPill status={task.status} label={taskStatusLabel(task, t)} />
          </button>
        </div>
        {hasKids && !isCollapsed ? (
          <ul className="ml-[18px] flex flex-col border-l border-surface-border pl-1">
            {kids.map((kid) => renderNode(kid, depth + 1))}
          </ul>
        ) : null}
      </li>
    );
  };

  const grouped = groupByTimeBucket(roots, (task) => task.updated_at);

  return (
    <div className="flex flex-col gap-5">
      {grouped.map(([bucket, bucketRoots]) => (
        <div key={bucket}>
          <div className="mb-1.5 px-3 text-[11.5px] font-normal uppercase tracking-[0.06em] text-ink-body">
            {t(BUCKET_KEY[bucket] as Parameters<typeof t>[0])}
          </div>
          <ul className="flex flex-col">
            {bucketRoots.map((task) => renderNode(task, 0))}
          </ul>
        </div>
      ))}
    </div>
  );
};

/* ── Project home "All" — sessions + tasks merged, icon-prefixed ─── */

const ProjectAllList = ({
  sessions,
  tasks,
  onOpenSession,
  onOpenTask,
  onRenameConfirm,
  onDeleteSession,
  hideScopeTag = false,
}: {
  sessions: SessionListItem[];
  tasks: Task[];
  onOpenSession: (id: string) => void;
  onOpenTask: (id: string) => void;
  onRenameConfirm: (id: string, name: string) => void;
  onDeleteSession: (id: string, label: string) => void;
  /** Hide the leading icon + 对话/任务/自动化 chip — used by the 自动化 tab
   * where every row is automation, so the chip would be pure noise. */
  hideScopeTag?: boolean;
}) => {
  const { t } = useTranslation();
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const items = useMemo(() => {
    const merged = [
      ...sessions.map((s) => ({
        kind: "chat" as const,
        id: s.id,
        title:
          s.name ??
          s.last_user_message_text ??
          t("sidebar.newChat" as Parameters<typeof t>[0]),
        status: s.status as string,
        statusKey: SESSION_STATUS_KEY[s.status],
        // A session's list ``updated_at`` is its creation time (see
        // ``formatCreatedAt``) — used for both the displayed time and sorting.
        created: s.updated_at,
        sortAt: s.updated_at,
        isAuto: s.origin === "automation",
      })),
      ...tasks.map((tk) => ({
        kind: "task" as const,
        id: tk.id,
        title: tk.title,
        status: tk.status,
        statusKey: TASK_STATUS_KEY[tk.status],
        created: tk.created_at,
        sortAt: tk.updated_at,
        isAuto: tk.trigger?.type === "automation",
      })),
    ];
    // Most-recently-active first, matching the per-tab lists.
    return merged.sort((a, b) => b.sortAt - a.sortAt);
  }, [sessions, tasks, t]);

  if (items.length === 0) {
    return (
      <div className="px-3 py-12 text-center text-sm text-ink-meta">
        {t("project.noSessions" as Parameters<typeof t>[0])}
      </div>
    );
  }

  const grouped = groupByTimeBucket(items, (item) => item.sortAt);

  const renderItem = (item: (typeof items)[number]) => {
    // Leading icon + scope tag. Automation-triggered runs read as 自动化
    // (clock) in the 全部 list, marking provenance over surface type.
    const Icon = item.isAuto
      ? Clock3
      : item.kind === "task"
        ? ListChecks
        : MessageSquare;
    if (item.kind === "chat" && renamingId === item.id) {
      return (
        <li
          key={`${item.kind}-${item.id}`}
          data-anchor-key={`${item.kind}-${item.id}`}
        >
          <div className="flex w-full items-center gap-2 rounded-xl px-3 py-3">
            <RenameInput
              initial={item.title}
              onConfirm={(v) => {
                onRenameConfirm(item.id, v);
                setRenamingId(null);
              }}
              onCancel={() => setRenamingId(null)}
            />
          </div>
        </li>
      );
    }
    return (
      <li
        key={`${item.kind}-${item.id}`}
        data-anchor-key={`${item.kind}-${item.id}`}
        className="group relative"
      >
        <div
          role="button"
          tabIndex={0}
          onClick={() =>
            item.kind === "task" ? onOpenTask(item.id) : onOpenSession(item.id)
          }
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              if (item.kind === "task") onOpenTask(item.id);
              else onOpenSession(item.id);
            }
          }}
          className="flex w-full cursor-default items-center gap-2 rounded-xl px-3 py-3 text-left outline-none transition-colors hover:bg-surface-soft focus-visible:bg-surface-soft"
        >
          {!hideScopeTag && (
            <span className="inline-flex shrink-0 items-center gap-1 text-[11px] text-ink-muted">
              <Icon className="h-3 w-3" strokeWidth={2} />
              {item.isAuto
                ? t("activity.automationTag" as Parameters<typeof t>[0])
                : item.kind === "task"
                  ? t("project.tasksColumn" as Parameters<typeof t>[0])
                  : t("project.chatTab" as Parameters<typeof t>[0])}
            </span>
          )}
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-heading">
            {item.title}
          </span>
          <span className="shrink-0 whitespace-nowrap text-[11px] text-ink-meta">
            {formatCreatedAt(item.created, t)}
          </span>
          <span className="relative inline-flex min-w-6 shrink-0 items-center justify-center">
            {item.statusKey && (
              <StatusPill
                status={item.status}
                label={t(item.statusKey as Parameters<typeof t>[0])}
                className={
                  item.kind === "chat"
                    ? "transition-opacity group-hover:opacity-0 group-has-[[data-state=open]]:opacity-0"
                    : undefined
                }
              />
            )}
            {item.kind === "chat" && (
              <RowActionsMenu
                onRename={() => setRenamingId(item.id)}
                onDelete={() => onDeleteSession(item.id, item.title)}
              />
            )}
          </span>
        </div>
      </li>
    );
  };

  return (
    <div className="flex flex-col gap-5">
      {grouped.map(([bucket, bucketItems]) => (
        <div key={bucket}>
          <div className="mb-1.5 px-3 text-[11.5px] font-normal uppercase tracking-[0.06em] text-ink-body">
            {t(BUCKET_KEY[bucket] as Parameters<typeof t>[0])}
          </div>
          <ul className="flex flex-col">{bucketItems.map(renderItem)}</ul>
        </div>
      ))}
    </div>
  );
};

/* ── PLACEHOLDER_COMPONENT ──────────────────────────────────── */

export const ProjectDetailPage = () => {
  const { t } = useTranslation();
  const { deleteFile, revealInFinder } = usePlatform();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    setRightPanel,
    setHeader,
    setMainClassName,
    setContentInnerClassName,
  } = useProjectOutlet();
  const panelCollapsed = usePanelStore((s) => s.collapsed);
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);

  // Global sessions list — already fetched + kept fresh by
  // ``DesktopProjectLayout``. Filter to this project to render the
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
  // RECENTS in DesktopProjectLayout.
  const projectSessions = useMemo(
    () =>
      allSessions.filter(
        (s) =>
          s.project_id === id && s.status !== "created" && s.task_id == null,
      ),
    [allSessions, id],
  );

  // Project detail page always has meaningful panel content
  // (instructions / skills / KB / file tree). Layout defaults the
  // panel to collapsed for chat projects; flip it open when the
  // user enters a project (or switches to another). Subsequent
  // manual collapses inside the same project are respected — the
  // effect only re-runs when ``id`` changes.
  useEffect(() => {
    if (id) panelSetCollapsed(false);
  }, [id, panelSetCollapsed]);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  // Lead-dispatch tasks for this project, shown in the centre history area
  // below Recents (PRD-NEXT §3.4). Non-critical for the project home.
  const [tasks, setTasks] = useState<Task[]>([]);
  // Member agents for the config panel's "Agents" section (PRD-NEXT §3.4).
  const [members, setMembers] = useState<ProjectMemberItem[]>([]);

  // ── Project memory (auto-curated) — drives the project-memory tab ──────
  const [projectMemory, setProjectMemory] = useState<string[]>([]);
  useEffect(() => {
    if (!id) return;
    let alive = true;
    void memoryApi
      .getMemory(id)
      .then((v) => {
        if (alive) setProjectMemory(v.entries.project ?? []);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [id]);
  const handleMemoryDeleteEntry = useCallback(
    async (text: string) => {
      if (!id) return;
      try {
        const v = await memoryApi.deleteEntry({
          target: "project",
          old_text: text,
          project_id: id,
        });
        setProjectMemory(v.entries.project ?? []);
      } catch {
        // best-effort — leave the list as-is on failure
      }
    },
    [id],
  );
  const handleMemoryClear = useCallback(async () => {
    if (!id) return;
    try {
      const v = await memoryApi.clearScope({
        target: "project",
        project_id: id,
      });
      setProjectMemory(v.entries.project ?? []);
    } catch {
      // best-effort
    }
  }, [id]);
  // Raw member rows kept alongside the panel-shaped ``members`` so the
  // hover-actions on each row can open the edit dialog with the agent's
  // full profile (model / instructions / skills / connectors / effort)
  // without a second fetch. Updated in the same callback as ``members``
  // so the two never drift.
  const [rawMembers, setRawMembers] = useState<MemberWithAgent[]>([]);
  // Skill catalog for the draft composer's ``/`` picker — used only to map the
  // selected agent's skill slugs to display names (the picker itself shows the
  // selected agent's bound skills, see ``selectedAgentSkillItems``).
  const [skillCatalog, setSkillCatalog] = useState<SkillView[]>([]);
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    skillsApi
      .list(id)
      .then((c) => {
        if (!cancelled) setSkillCatalog(c.skills);
      })
      .catch(() => {
        if (!cancelled) setSkillCatalog([]);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);
  // Member remove (undeploy) dialog state. Editing is global — handled on the
  // agent detail page, not here (see openMember).
  const [memberDeleteTarget, setMemberDeleteTarget] = useState<string | null>(
    null,
  );
  const [memberDeleteBusy, setMemberDeleteBusy] = useState(false);
  // Scheduled-task / conversation deletion — confirmed via the unified
  // DeleteConfirmDialog instead of the browser's native window.confirm.
  const [pendingDelete, setPendingDelete] = useState<{
    kind: "task" | "session";
    id: string;
    name: string;
  } | null>(null);
  const [pendingDeleteBusy, setPendingDeleteBusy] = useState(false);
  // Shared conversation-row actions, used by both the "全部" and "对话" lists.
  // Rename happens inline (RenameInput, mirroring the sidebar) — this just
  // persists the confirmed name; delete goes through the unified confirm dialog.
  const handleRenameConfirm = useCallback(
    async (sid: string, name: string) => {
      try {
        await renameSession(sid, name);
        toast.success(t("sidebar.renamed" as Parameters<typeof t>[0]));
      } catch {
        toast.error(t("sidebar.renameFailed" as Parameters<typeof t>[0]));
      }
    },
    [t, renameSession],
  );
  const handleDeleteSession = useCallback((sid: string, label: string) => {
    setPendingDelete({ kind: "session", id: sid, name: label });
  }, []);
  // Project conversations bind to one of the project's configured agents
  // (instead of a raw model). The composer remembers the agent PER MODE —
  // Chat keeps the last chat agent, Task keeps the last Lead — because the
  // two roles usually differ (a Lead orchestrates; a chat agent is a
  // specialist). ``selectedAgentSlug`` is derived from the active mode below.
  const [agentByMode, setAgentByMode] = useState<{
    chat: string | null;
    task: string | null;
  }>({ chat: null, task: null });
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
      // Keep each mode's pick if it's still a valid member, else fall back to
      // the first member — applied independently to chat and task.
      const keepOrFirst = (prev: string | null) =>
        prev && mapped.some((m) => m.slug === prev)
          ? prev
          : (mapped[0]?.slug ?? null);
      setAgentByMode((prev) => ({
        chat: keepOrFirst(prev.chat),
        task: keepOrFirst(prev.task),
      }));
    } catch {
      setMembers([]);
      setRawMembers([]);
      setAgentByMode({ chat: null, task: null });
    }
  }, [id]);

  // ──────────────────────────────────────────────────────────────────
  // Member open / delete handlers. Live-reference deployment (08-agents-module
  // §deploy): a member is a *reference* to the shared library agent, so
  // "editing a member" === editing the global agent. Opening a member
  // navigates to the agent detail page (the single edit surface) with
  // ``fromProject`` state so it shows a "back to project" back affordance. No
  // per-member fork/patch. ``useCallback`` keeps refs stable so the
  // ``setRightPanel`` effect (which lists them in deps) doesn't churn.
  // ──────────────────────────────────────────────────────────────────
  // Open the SHARED library agent detail. ``slug`` is the global library agent
  // slug (see ProjectMemberItem.sourceAgentSlug), not the project-local member
  // slug — using the member slug would 404 on /agents/:slug. AgentDetailPage
  // shows a "back to project" affordance via the router state.
  const openMember = useCallback(
    (slug: string) => {
      if (!id) return;
      navigate(`/agents/${encodeURIComponent(slug)}`, {
        state: {
          fromProject: { id, name: project?.name ?? decodeURIComponent(id) },
        },
      });
    },
    [id, navigate, project?.name],
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
  // First load uses the same id-keyed in-place merge as the auto-refresh
  // poller (plan §4A.7) so the two paths are idempotent.
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    tasksApi
      .listTasks(id)
      .then((res) => {
        if (!cancelled) setTasks((prev) => mergeTasks(prev, res.tasks));
      })
      .catch(() => {
        if (!cancelled) setTasks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Automation-triggered subset for this project. Both signals are
  // authoritative and complete (no "recent runs" limit): the session carries
  // origin for chats, and the task list endpoint resolves origin for tasks
  // (lead session ∈ automation run index). The non-automation lists feed the
  // 全部/对话/任务 tabs; the automation subset feeds the dedicated 自动化 tab.
  const userSessions = useMemo(
    () => projectSessions.filter((s) => s.origin !== "automation"),
    [projectSessions],
  );
  const automationSessions = useMemo(
    () => projectSessions.filter((s) => s.origin === "automation"),
    [projectSessions],
  );
  const userTasks = useMemo(
    () => tasks.filter((tk) => tk.trigger?.type !== "automation"),
    [tasks],
  );
  const automationTasks = useMemo(
    () => tasks.filter((tk) => tk.trigger?.type === "automation"),
    [tasks],
  );

  // While the page stays mounted and the tab is visible, re-pull the two
  // already user_id+project_id-filtered list endpoints every 4s (+ on
  // visible/online) and merge the full snapshot back: sessions through the
  // store's ``mergeProjectSessions`` (subset merge, other projects untouched),
  // tasks through the id-keyed ``mergeTasks`` below.
  const mergeTasksInPlace = useCallback((incoming: Task[]) => {
    setTasks((prev) => mergeTasks(prev, incoming));
  }, []);
  useProjectListAutoRefresh(id, { onTasks: mergeTasksInPlace });

  // Scroll-position anchoring (plan §4B / §7.4). The real scroller is the
  // layout content container, found by walking up from this sentinel. A
  // fingerprint of the two lists (ids + the volatile updated_at/status) keys
  // the correction layout effect so a poll-driven reorder doesn't jump the
  // first visible row.
  const listRootRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    scrollContainerRef.current = findScrollParent(listRootRef.current);
  }, []);
  const anchorDataKey = useMemo(() => {
    const s = projectSessions
      .map((x) => `${x.id}:${x.updated_at}:${x.status}`)
      .join(",");
    const tk = tasks.map((x) => `${x.id}:${x.updated_at}:${x.status}`).join(",");
    return `${s}|${tk}`;
  }, [projectSessions, tasks]);
  useListScrollAnchor(scrollContainerRef, anchorDataKey);

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
  // Attachments upload-on-attach (chat mode), which needs a session up front.
  // The first attach eager-creates the project chat session (with the picked
  // agent), freezing it per ADR-006 — the agent picker locks once
  // ``chatSessionId`` is set. Parsing runs async on the backend; the hook
  // polls status for the composer progress chips.
  const [chatSessionId, setChatSessionId] = useState<string | null>(null);
  const {
    attachments: stagedAttachments,
    hasParsing,
    attachLocalFiles,
    remove: removeAttachment,
    markPendingConsumed,
  } = useSessionAttachments(chatSessionId);
  const [parsingConfirmOpen, setParsingConfirmOpen] = useState(false);
  // PRD-PAAT §3.2 unified composer mode. ``chat`` creates a normal
  // session; ``task`` kicks off a background Task via tasksApi.kickoff
  // and routes to the task detail page.
  const [composerMode, setComposerMode] = useState<"chat" | "task">("chat");
  // Active agent = the remembered pick for the current mode. Switching mode
  // swaps the agent to that mode's memory (Chat agent ↔ last Lead).
  const selectedAgentSlug = agentByMode[composerMode];
  // The selected member agent's bound skills — the draft composer's ``/`` list.
  // Projects can't attach skills ad-hoc, so ``/`` surfaces exactly that agent's
  // skills (resolved to display names via the project skill catalog).
  const selectedAgentSkillItems = useMemo<AgentSkillItem[]>(() => {
    if (!selectedAgentSlug) return [];
    const agent = rawMembers.find(
      (m) => m.member.agent_slug === selectedAgentSlug,
    )?.agent;
    return resolveAgentSkillItems(agent?.skills, [skillCatalog]);
  }, [selectedAgentSlug, rawMembers, skillCatalog]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [connectors, setConnectors] = useState<ConnectorItem[]>([]);
  const [selectedMcpSlugs, setSelectedMcpSlugs] = useState<string[]>([]);
  const [providers, setProviders] = useState<LLMChannelDetail[]>([]);
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
  // Per-project memory: the picker seeds from the project's most
  // recent session before falling back to Settings → Default. So if
  // the user mostly drives this project with Deep Agents but their
  // global default is Claude Code, the project still opens in Deep
  // Agents next time.
  const { pick: lastPick, loading: lastPickLoading } = useProjectLastUsed(id);
  // Seed sequence (highest wins on first pass; once any source lands
  // we don't override until the user manually picks something else):
  //   1. Project last-used (per-project memory)
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
  // Seed each mode's agent ONCE from the project's last-used picks: Chat from
  // the last conversation's agent, Task from the last Lead. Ref-gated so it
  // never clobbers a pick the user made afterwards or a later member reload.
  // Each mode only overrides when its seed is a current member; otherwise
  // loadMembers' ``mapped[0]`` fallback stands (fresh project / no prior run).
  const agentSeededRef = useRef(false);
  useEffect(() => {
    if (agentSeededRef.current) return;
    if (lastPickLoading || members.length === 0) return;
    agentSeededRef.current = true;
    const valid = (slug: string | null | undefined) =>
      slug && members.some((m) => m.slug === slug) ? slug : null;
    const chatSeed = valid(lastPick?.agent_slug);
    const taskSeed = valid(lastPick?.task_agent_slug);
    setAgentByMode((prev) => ({
      chat: chatSeed ?? prev.chat,
      task: taskSeed ?? prev.task,
    }));
  }, [members, lastPick, lastPickLoading]);
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
  // that drives the project binding tree (kb/folder/document
  // granularity, editable here on the project page), this one is the
  // file picker's navigable source (every KB, file-selectable only).
  const {
    kbTree: pickerKbTree,
    loading: pickerKbLoading,
    expandFolder: pickerExpandFolder,
  } = useKbDocTree(kbPickerOpen);
  const [scheduledTasks, setScheduledTasks] = useState<AutomationItem[]>([]);
  const [selectedArtifactPath, setSelectedArtifactPath] = useState<string | null>(
    null,
  );
  const [artifact, setArtifact] = useState<ArtifactDescriptor | null>(null);
  const [artifactContent, setArtifactContent] =
    useState<ArtifactContent | null>(null);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [artifactOpening, setArtifactOpening] = useState(false);
  const [artifactClosing, setArtifactClosing] = useState(false);
  const artifactRequestSeqRef = useRef(0);
  const artifactOpenFrameRef = useRef<number | null>(null);
  const artifactCloseTimerRef = useRef<number | null>(null);
  const selectedFileParam = searchParams.get("file");
  const [newTaskOpen, setNewTaskOpen] = useState(false);
  // When set, the automation dialog opens in edit mode (PATCH the row) instead
  // of create. Holds the fetched detail (prompt_template + trigger) so the
  // dialog can pre-fill via ``initial``.
  const [editTask, setEditTask] = useState<Awaited<
    ReturnType<typeof automationsApi.get>
  > | null>(null);
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
    projectsApi
      .listFiles(id, { depth: 3 })
      .then((res) => setFileTree(toFileTree(res.files)))
      .catch(() => setFileTree([]));
  }, [id]);

  const fetchData = useCallback(async () => {
    try {
      const ws = await projectsApi.get(id);
      setProject(ws);
      setInstructions(ws.instructions_md ?? "");

      const [filesRes, chListRes] = await Promise.all([
        projectsApi
          .listFiles(id, { depth: 3 })
          .catch(() => ({ files: [] as ProjectFileNode[] })),
        providersApi.list().catch(() => ({ providers: [] as LLMChannel[] })),
      ]);
      setFileTree(toFileTree(filesRes.files));
      // Skills are bound on the Agent now (08-agents-module), not the
      // project — no per-project skill catalog fetch here.
      // KB tree + bindings are owned by ``useProjectKbBindings``.

      const details = await Promise.all(
        chListRes.providers
          .filter((c) => c.enabled)
          .map((c) => providersApi.get(c.id).catch(() => null)),
      );
      setProviders(details.filter((d): d is LLMChannelDetail => d !== null));

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
          projectsApi
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
          project_id: id ?? undefined,
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

  const handleSubmitTask = async (data: {
    name: string;
    prompt_template: string;
    agent_slug: string;
    trigger: Trigger;
    action_kind: ActionKind;
  }) => {
    // Edit mode: PATCH the existing row. The dialog is stateless and calls the
    // same submit handler for create + edit; ``editTask`` decides which.
    if (editTask) {
      await automationsApi.update(editTask.automation_id, {
        name: data.name,
        prompt_template: data.prompt_template,
        agent_slug: data.agent_slug,
        trigger: data.trigger,
        action_kind: data.action_kind,
      });
      toast.success(t("common.saved" as Parameters<typeof t>[0]));
      await reloadScheduledTasks();
      return;
    }
    // Project detail page is bound to a specific project project by URL —
    // ``project_kind="project"`` + the project's id is the only valid pair
    // here. agent_kind is "project_member" (chat-only library_agent has no
    // meaning inside a project).
    await automationsApi.create({
      name: data.name,
      project_kind: "project",
      project_id: id,
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

  // Open the automation editor for a row: fetch the full detail (the list rows
  // carry no prompt_template) and open the dialog in edit mode.
  const openEditScheduledTask = useCallback(async (automationId: string) => {
    try {
      const detail = await automationsApi.get(automationId);
      setEditTask(detail);
      setNewTaskOpen(true);
    } catch {
      toast.error(t("common.failed" as Parameters<typeof t>[0]));
    }
  }, []);

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
    (taskId: string) => {
      const task = scheduledTasks.find((it) => it.automation_id === taskId);
      setPendingDelete({ kind: "task", id: taskId, name: task?.name ?? "" });
    },
    [scheduledTasks],
  );

  const handleRunScheduledTask = useCallback(async (taskId: string) => {
    try {
      await automationsApi.runNow(taskId);
      toast.success(t("automation.runQueued" as Parameters<typeof t>[0]));
    } catch (error) {
      toast.error(
        t("automation.runFailed" as Parameters<typeof t>[0], {
          error: String(error),
        }),
      );
    }
  }, []);

  const handleOpenInFinder = () => {
    if (project?.root_path) {
      void revealInFinder(project.root_path);
    }
  };

  const openArtifactFile = useCallback(
    async (relPath: string, options?: { syncUrl?: boolean }) => {
      if (!id) return;
      if (artifactCloseTimerRef.current != null) {
        window.clearTimeout(artifactCloseTimerRef.current);
        artifactCloseTimerRef.current = null;
      }
      if (artifactOpenFrameRef.current != null) {
        window.cancelAnimationFrame(artifactOpenFrameRef.current);
      }
      const shouldAnimateOpen =
        !selectedArtifactPath && !artifact && !artifactLoading && !artifactError;
      if (shouldAnimateOpen) {
        setArtifactOpening(true);
        artifactOpenFrameRef.current = window.requestAnimationFrame(() => {
          artifactOpenFrameRef.current = window.requestAnimationFrame(() => {
            setArtifactOpening(false);
            artifactOpenFrameRef.current = null;
          });
        });
      }
      setArtifactClosing(false);
      const requestSeq = artifactRequestSeqRef.current + 1;
      artifactRequestSeqRef.current = requestSeq;
      setSelectedArtifactPath(relPath);
      setArtifactLoading(true);
      setArtifactError(null);
      if (options?.syncUrl !== false && searchParams.get("file") !== relPath) {
        window.setTimeout(() => {
          setSearchParams(
            (current) => {
              const next = new URLSearchParams(current);
              next.set("file", relPath);
              return next;
            },
            { replace: false },
          );
        }, 0);
      }
      try {
        const result = await projectsApi.readFile(id, relPath);
        if (artifactRequestSeqRef.current !== requestSeq) return;
        setArtifact(result.artifact);
        setArtifactContent(result.content);
      } catch (error) {
        if (artifactRequestSeqRef.current !== requestSeq) return;
        setArtifact(null);
        setArtifactContent(null);
        setArtifactError(error instanceof Error ? error.message : String(error));
      } finally {
        if (artifactRequestSeqRef.current === requestSeq) {
          setArtifactLoading(false);
        }
      }
    },
    [
      artifact,
      artifactError,
      artifactLoading,
      id,
      searchParams,
      selectedArtifactPath,
      setSearchParams,
    ],
  );

  useEffect(() => {
    if (!selectedFileParam) {
      if (selectedArtifactPath && !artifactClosing) {
        const timer = window.setTimeout(() => {
          setSelectedArtifactPath(null);
          setArtifact(null);
          setArtifactContent(null);
          artifactRequestSeqRef.current += 1;
          setArtifactLoading(false);
          setArtifactError(null);
        }, 0);
        return () => window.clearTimeout(timer);
      }
      return;
    }
    if (
      selectedFileParam === selectedArtifactPath &&
      (artifact || artifactLoading || artifactError)
    ) {
      return;
    }
    const timer = window.setTimeout(() => {
      void openArtifactFile(selectedFileParam, { syncUrl: false });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [
    artifact,
    artifactError,
    artifactLoading,
    artifactClosing,
    openArtifactFile,
    selectedArtifactPath,
    selectedFileParam,
  ]);

  const handleArtifactReload = useCallback(() => {
    if (selectedArtifactPath) {
      void openArtifactFile(selectedArtifactPath);
    }
  }, [openArtifactFile, selectedArtifactPath]);

  const handleArtifactClose = useCallback(() => {
    artifactRequestSeqRef.current += 1;
    setArtifactLoading(false);
    setArtifactError(null);
    setArtifactClosing(true);
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("file");
        return next;
      },
      { replace: true },
    );
    if (artifactCloseTimerRef.current != null) {
      window.clearTimeout(artifactCloseTimerRef.current);
    }
    artifactCloseTimerRef.current = window.setTimeout(() => {
      setSelectedArtifactPath(null);
      setArtifact(null);
      setArtifactContent(null);
      setArtifactClosing(false);
      artifactCloseTimerRef.current = null;
    }, 150);
  }, [setSearchParams]);

  const handleArtifactCopy = useCallback(() => {
    if (artifactContent?.kind !== "text") return;
    void navigator.clipboard
      ?.writeText(artifactContent.content)
      .then(() => toast.success(t("common.copied" as Parameters<typeof t>[0])))
      .catch(() => toast.error(t("common.failed" as Parameters<typeof t>[0])));
  }, [artifactContent, t]);

  const handleArtifactOpenExternal = useCallback(() => {
    if (!project?.root_path || !selectedArtifactPath) return;
    void revealInFinder(`${project.root_path}/${selectedArtifactPath}`);
  }, [project, selectedArtifactPath, revealInFinder]);

  const handleInstructionsChange = async (md: string) => {
    setInstructions(md);
    try {
      await projectsApi.updateInstructions(id, md);
    } catch {
      toast.error(t("project.saveFailed" as Parameters<typeof t>[0]));
    }
  };

  // Mint (once) the project chat session attachments upload into and the send
  // reuses. Project sessions bind to an agent, so one must be picked first
  // (ADR-006: the agent is frozen at creation — the composer locks it once
  // ``chatSessionId`` is set).
  const ensureChatSession = async (): Promise<{ id: string }> => {
    if (chatSessionId) return { id: chatSessionId };
    if (!selectedAgentSlug) throw new Error("no-agent-selected");
    const session = await sessionsApi.create({
      project_id: id,
      agent_slug: selectedAgentSlug,
      permission_mode: selectedPermissionMode,
    });
    setChatSessionId(session.id);
    return { id: session.id };
  };

  // Upload-on-attach for the project chat composer. Needs an agent up front;
  // surface a hint instead of silently dropping the file when none is picked.
  const handleAttachFiles = (files: File[]) => {
    if (!selectedAgentSlug) {
      toast.error(
        t("conversation.selectAgentFirst" as Parameters<typeof t>[0]),
      );
      return;
    }
    void attachLocalFiles(files, ensureChatSession);
  };

  // The actual chat send. Attachments are already uploaded (on attach), so
  // this just reuses / mints the session and posts the message.
  const performChatSend = async () => {
    const text = composerValue.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const session = await ensureChatSession();
      markPendingConsumed();
      // ``text`` already contains any ``/slug`` tokens because Composer
      // serializes inline skill chips into its controlled value.
      await sessionsApi.sendMessage(session.id, text);
      setComposerValue("");
      navigate(`/conversation/${session.id}`);
    } catch (cause) {
      // A billing rejection (402) carries an i18n key the client renders;
      // otherwise fall back to the generic save-failed copy.
      toast.error(
        cause instanceof ApiError && cause.i18nKey
          ? _t(
              cause.i18nKey as Parameters<typeof _t>[0],
              cause.i18nParams as Parameters<typeof _t>[1],
            )
          : t("common.saveFailed" as Parameters<typeof t>[0]),
      );
    } finally {
      setSending(false);
    }
  };

  const handleSend = async () => {
    const text = composerValue.trim();
    if (!text || sending) return;

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
        return;
      }
      setSending(true);
      try {
        const task = await tasksApi.kickoff(id, {
          goal: text,
          lead_agent_slug: selectedAgentSlug,
          title: text.length > 60 ? text.slice(0, 60) : null,
          dispatch_mode: "async",
        });
        toast.success(t("task.kickedOff"));
        setComposerValue("");
        navigate(`/tasks/${encodeURIComponent(task.id)}`);
      } catch (err) {
        // Surface the backend message (kickoff has a few well-known
        // 4xx reasons: lead agent has no model provider pinned, lead
        // not a member, project not found, ...). Logging too so the
        // dev console keeps the full stack for debugging.
        console.error("[tasksApi.kickoff] failed", err);
        const msg = err instanceof Error ? err.message : String(err);
        toast.error(`${t("task.kickoffFailed")}: ${msg}`);
      } finally {
        setSending(false);
      }
      return;
    }

    // Chat mode — block on unfinished parsing, then send.
    if (hasParsing) {
      setParsingConfirmOpen(true);
      return;
    }
    void performChatSend();
  };

  const displayName = project?.name ?? decodeURIComponent(id);

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
        projectMemory={projectMemory}
        onMemoryDeleteEntry={handleMemoryDeleteEntry}
        onMemoryClear={handleMemoryClear}
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
        // Staged conversation attachments (upload-on-attach) — surface them in
        // the panel's "uploaded files" section with live parse status, same as
        // the conversation page. Without ``uploadedFiles`` the section is
        // hidden entirely (``showUploadedFiles = uploadedFiles !== undefined``).
        uploadedFiles={stagedAttachments.map((a) => ({
          id: a.id,
          name: a.filename,
          parseStatus: a.parse_status as
            | "parsing"
            | "ready"
            | "failed"
            | "native"
            | undefined,
          sourceKind: a.source_kind,
        }))}
        onRemoveUploadedFile={(attId) => void removeAttachment(attId)}
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
        onEditScheduledTask={openEditScheduledTask}
        onToggleScheduledTask={handleToggleScheduledTask}
        onDeleteScheduledTask={handleDeleteScheduledTask}
        onRunScheduledTask={handleRunScheduledTask}
        fileTree={fileTree}
        fileTreeInTab
        rootPath={project?.root_path ?? ""}
        onRefreshFiles={refreshFileTree}
        onFileClick={(path) => {
          void openArtifactFile(path);
        }}
        onOpenInFinder={handleOpenInFinder}
        onFileDoubleClick={(path) => void openArtifactFile(path)}
        onOpenInSystem={(path: string) => {
          void revealInFinder(path);
        }}
        onDeleteFile={async (path: string) => {
          const fullPath = project?.root_path
            ? `${project.root_path}/${path}`
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
    project,
    displayName,
    scheduledTasks,
    handleToggleScheduledTask,
    handleDeleteScheduledTask,
    handleRunScheduledTask,
    connectors,
    selectedMcpSlugs,
    refreshFileTree,
    openArtifactFile,
    openMember,
    // The right panel is rendered into a layout slot via ``setRightPanel`` —
    // it captures these by closure, so they MUST be deps or the panel shows a
    // stale snapshot. ``stagedAttachments`` (+ its remove handler) drive the
    // "uploaded files" section; without them the section stayed empty while
    // the composer chip updated live.
    stagedAttachments,
    removeAttachment,
    projectMemory,
    handleMemoryDeleteEntry,
    handleMemoryClear,
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
    <div className="relative flex h-full min-h-0 flex-col">
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
              // Projects can't attach skills ad-hoc, so the toolbar "add skill"
              // button stays hidden — but the ``/`` picker is enabled once an
              // agent is selected so its bound skills are invocable.
              showSkillButton={false}
              showSkillSlash={selectedAgentSlug != null}
              skills={selectedAgentSkillItems}
              uploadOnAttach
              existingAttachmentCount={
                stagedAttachments.filter((a) => !a.consumed_at).length
              }
              pinnedAttachments={stagedAttachments
                .filter((a) => !a.consumed_at)
                .map((a) => ({
                  id: a.id,
                  name: a.filename,
                  parseStatus: a.parse_status as
                    | "parsing"
                    | "ready"
                    | "failed"
                    | "native"
                    | undefined,
                  sourceKind: a.source_kind,
                }))}
              onRemovePinnedAttachment={(attId) => void removeAttachment(attId)}
              onLocalUpload={handleAttachFiles}
              onFileDrop={handleAttachFiles}
              onKBPick={() => void handleOpenKbPicker()}
              // Project conversations pick a configured agent instead of a
              // raw model. The session inherits runtime/model/provider/
              // effort/skills/connectors from the agent; the [+] opens the
              // same add-agent dialog the config panel uses.
              agents={composerAgents}
              selectedAgentSlug={selectedAgentSlug}
              // First attach mints the chat session, freezing the agent
              // (ADR-006) — lock the picker once that happens. Only the Chat
              // mode is frozen by a minted chat session; Task mode keeps its
              // own pick (kickoff navigates away, so it never mints one here).
              agentLocked={composerMode === "chat" && chatSessionId != null}
              onAgentChange={(slug) => {
                setAgentByMode((m) => ({ ...m, [composerMode]: slug }));
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
            <AttachmentParsingDialog
              open={parsingConfirmOpen}
              onConfirm={() => {
                setParsingConfirmOpen(false);
                void performChatSend();
              }}
              onCancel={() => setParsingConfirmOpen(false)}
            />
          </div>

          {/* Centre history area (PRD-NEXT §3.4): Chat (sessions) and Task
              (lead-dispatch tasks) split into two tabs. The Task tab always
              shows — empty state offers an "add task" affordance. The ref is
              the sentinel ``useListScrollAnchor`` walks up from to find the
              real scroll container (plan §4B). */}
          <div ref={listRootRef} className="mt-4 w-full pb-6">
            <Tabs defaultValue="all">
              <div className="flex items-center border-b border-surface-border">
                <TabsList
                  variant="line"
                  className="h-9 justify-start gap-4 border-0 p-0"
                >
                  <TabsTrigger value="all">
                    {t("activity.filterAll" as Parameters<typeof t>[0])}
                  </TabsTrigger>
                  <TabsTrigger value="chat">
                    {t("project.chatTab" as Parameters<typeof t>[0])}
                  </TabsTrigger>
                  <TabsTrigger value="tasks">
                    {t("project.tasksColumn" as Parameters<typeof t>[0])}
                  </TabsTrigger>
                  <TabsTrigger value="automation">
                    {t("activity.automationTag" as Parameters<typeof t>[0])}
                  </TabsTrigger>
                </TabsList>
              </div>
              <TabsContent value="all" className="mt-5">
                <ProjectAllList
                  sessions={projectSessions}
                  tasks={tasks}
                  onOpenSession={(sid) => navigate(`/conversation/${sid}`)}
                  onOpenTask={(taskId) => navigate(`/tasks/${taskId}`)}
                  onRenameConfirm={handleRenameConfirm}
                  onDeleteSession={handleDeleteSession}
                />
              </TabsContent>
              <TabsContent value="chat" className="mt-5">
                <ProjectRecents
                  sessions={userSessions}
                  onOpen={(sid) => navigate(`/conversation/${sid}`)}
                  onRenameConfirm={handleRenameConfirm}
                  onDelete={handleDeleteSession}
                />
              </TabsContent>
              <TabsContent value="tasks" className="mt-5">
                <ProjectTasks
                  tasks={userTasks}
                  onOpen={(taskId) => navigate(`/tasks/${taskId}`)}
                  onAddTask={() => {
                    // v2: there is no separate "new task" page anymore — the
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
              <TabsContent value="automation" className="mt-5">
                <ProjectAllList
                  sessions={automationSessions}
                  tasks={automationTasks}
                  onOpenSession={(sid) => navigate(`/conversation/${sid}`)}
                  onOpenTask={(taskId) => navigate(`/tasks/${taskId}`)}
                  onRenameConfirm={handleRenameConfirm}
                  onDeleteSession={handleDeleteSession}
                  hideScopeTag
                />
              </TabsContent>
            </Tabs>
          </div>
        </div>
      </div>

      {selectedArtifactPath ||
      artifactLoading ||
      artifactError ||
      artifactOpening ||
      artifactClosing ? (
        <div
          className={`absolute inset-0 z-20 flex items-center justify-center bg-surface/70 backdrop-blur-sm transition-opacity duration-150 ${
            artifactClosing ? "pointer-events-none opacity-0" : "opacity-100"
          }`}
        >
          <div
            className={`h-full w-full rounded-[14px] shadow-2xl transition duration-150 ${
              artifactOpening
                ? "pointer-events-none scale-[0.98] opacity-0"
                : "scale-100 opacity-100"
            }`}
          >
            <ArtifactViewerShell
              artifact={artifact}
              content={artifactContent}
              loading={artifactLoading}
              error={artifactError}
              onReload={handleArtifactReload}
              onClose={handleArtifactClose}
              onCopyContent={handleArtifactCopy}
              onOpenExternal={handleArtifactOpenExternal}
            />
          </div>
        </div>
      ) : null}

      {/* Project automation create — uses the same agent-driven dialog
          as the global Automation page, with task mode enabled (this is
          a project project) and candidates resolved from the project's
          members. ``description`` keeps the existing project-specific
          hint copy ("Tasks created here are linked to this project"). */}
      <CreateAutomationDialog
        open={newTaskOpen}
        onOpenChange={(open) => {
          // Reset edit context on close so the next "+ New" starts fresh in
          // create mode.
          if (!open) setEditTask(null);
          setNewTaskOpen(open);
        }}
        description={t("project.instruction" as Parameters<typeof t>[0])}
        onSubmit={handleSubmitTask}
        agents={rawMembers.map((entry) => ({
          slug: entry.member.agent_slug,
          name: entry.agent?.name ?? entry.member.agent_slug,
        }))}
        allowTaskMode
        fixedTargetName={displayName}
        initial={
          editTask
            ? {
                name: editTask.name,
                prompt_template: editTask.prompt_template,
                agent_slug: editTask.agent_slug,
                trigger: editTask.trigger,
                action_kind: (editTask.action_kind as ActionKind) ?? "chat",
              }
            : undefined
        }
      />

      <DeployAgentsDialog
        open={addAgentOpen}
        onOpenChange={setAddAgentOpen}
        projectId={id}
        agents={libraryAgents}
        members={rawMembers}
        onChanged={loadMembers}
        onCreateNew={() => navigate("/agents")}
      />

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
      <DeleteConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(v) => !v && setPendingDelete(null)}
        itemName={pendingDelete?.name || undefined}
        description={
          pendingDelete?.kind === "task"
            ? t("cron.confirmDeleteTaskDesc" as Parameters<typeof t>[0], {
                name: pendingDelete.name,
              })
            : undefined
        }
        loading={pendingDeleteBusy}
        onConfirm={async () => {
          if (!pendingDelete) return;
          const pd = pendingDelete;
          setPendingDeleteBusy(true);
          try {
            if (pd.kind === "task") {
              await automationsApi.delete(pd.id);
              toast.success(t("common.deleted" as Parameters<typeof t>[0]));
              await reloadScheduledTasks();
            } else {
              await deleteSession(pd.id);
              toast.success(t("sidebar.deleted" as Parameters<typeof t>[0]));
            }
            setPendingDelete(null);
          } catch {
            toast.error(
              t(
                (pd.kind === "task"
                  ? "common.deleteFailed"
                  : "sidebar.deleteFailed") as Parameters<typeof t>[0],
              ),
            );
          } finally {
            setPendingDeleteBusy(false);
          }
        }}
      />
    </div>
  );
};

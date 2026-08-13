import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { initI18n } from "@valuz/shared/i18n";
import {
  applyBrandColors,
  hydrateTheme,
  runsApi,
  sessionsApi,
  subscribeUserStream,
  useBranding,
  useGlobalShortcuts,
  usePanelStore,
  useConnectorAlert,
  refreshConnectorAlert,
  useRegistryStore,
  useRunningRuns,
  useSessionStore,
  useSettingsStore,
  useTaskStore,
  useTranslation,
  useProjectStore,
  useUpdaterStore,
  projectsApi,
  type RunSummary,
  useDegradedListTargets,
  getExecutionTargets,
} from "@valuz/core";
import {
  AppShell,
  AppToaster,
  Button,
  DesktopSidebar,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  DeleteConfirmDialog,
  ErrorBoundary,
  Input,
  OfflineBanner,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
  TopBar,
  WindowControls,
  type DesktopSidebarBottomItem,
  type DesktopSidebarProjectGroup,
  type DesktopSidebarRecentItem,
} from "@valuz/ui";
import {
  AppWindow,
  FolderOpen,
  HelpCircle,
  Home,
  LogOut,
  Maximize,
  Menu,
  MessageCirclePlus,
  RefreshCw,
  Settings,
  Terminal,
} from "lucide-react";
import { toast } from "sonner";
import {
  NotificationBadge,
  NotificationDrawer,
  NotificationProvider,
} from "../components/NotificationInbox";
import { usePlatform } from "../platform";
import { UpdateButton } from "../components/UpdateButton";
import { useAgentDeployPicker } from "../components/agent-deploy-picker";
import { AgentCheckboxList } from "../components/AgentDeployField";
import { ExportProjectDialog } from "../components/ExportProjectDialog";
import { ImportProjectDialog } from "../components/ImportProjectDialog";
import {
  ProjectLocationFields,
  useProjectExecutionLocation,
} from "../components/ProjectLocationFields";
import { OriginIcon } from "../components/ExecutionLocationPicker";
import { FORKABLE_RUNTIMES } from "../pages/conversation/useTitleActions";
import { outletTransitionKey } from "./outlet-key";
import { resolveRightPanelAutoFold } from "./right-panel-autofold";
import type { ProjectOutletContext } from "./types";

export type DirectoryFieldMode = "input" | "picker" | "managed";

export interface ProjectLayoutBaseProps {
  logoSrc: string;
  logoMenuContentStyle?: CSSProperties;
  directoryFieldMode?: DirectoryFieldMode;
  /** When ``directoryFieldMode="managed"``, invoked after a project is created
   * so the host can decide how to handle initial managed-cwd content. */
  onUploadInitialContent?: (projectId: string) => Promise<void>;
  /** Rendered at the very top of the sidebar, above "新对话". Overlay
   * editions inject an org / account switcher here. */
  sidebarHeader?: ReactNode;
  sidebarFooter?: ReactNode;
  sidebarExtraItems?: ReactNode;
  topbarActions?: ReactNode;
  projectDialogExtraFields?: ReactNode;
  rightPanel?: ReactNode;
  mascotSrc?: string | null;
}

// How many runs each project's own sidebar window asks for. The accordion
// shows 5 before "show more", and every row costs one kernel enrichment read
// server-side — a small window keeps N projects × one request cheap.
const PROJECT_RUNS_LIMIT = 20;

const NAV_ICON_MAP: Record<string, DesktopSidebarBottomItem["icon"]> = {
  assistant: "assistant",
  skills: "skills",
  scheduled: "scheduled",
  activity: "activity",
  knowledge: "knowledge",
  settings: "settings",
  agents: "agents",
  connectors: "connectors",
  marketplace: "marketplace",
};

function useNavItems(): DesktopSidebarBottomItem[] {
  const { t } = useTranslation();
  const navItems = useRegistryStore((state) => state.navItems);
  const { count: runningCount } = useRunningRuns();
  const { showDot: connectorAlert } = useConnectorAlert();
  return navItems.map((item) => ({
    id: item.id,
    label: t(item.label as Parameters<typeof t>[0]),
    href: item.href,
    // Profile-declared icon id wins (plugins bring their own); fall back to
    // the built-in per-id map, then to the generic gear.
    icon: item.icon ?? NAV_ICON_MAP[item.id] ?? "settings",
    group: item.navGroup ?? "main",
    badgeCount: item.id === "activity" ? runningCount : undefined,
    badgeDot: item.id === "connectors" ? connectorAlert : undefined,
  }));
}

/** Custom labeled sidebar groups from the active profile, labels translated. */
function useNavGroups(): { id: string; label: string }[] {
  const { t } = useTranslation();
  const navGroups = useRegistryStore((state) => state.navGroups);
  return navGroups.map((group) => ({
    id: group.id,
    label: t(group.label as Parameters<typeof t>[0]),
  }));
}

export function ProjectLayoutBase({
  logoSrc,
  logoMenuContentStyle,
  directoryFieldMode = "picker",
  onUploadInitialContent,
  sidebarHeader,
  sidebarFooter,
  sidebarExtraItems,
  topbarActions,
  projectDialogExtraFields,
  rightPanel: controlledRightPanel,
  mascotSrc = null,
}: ProjectLayoutBaseProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const platform = usePlatform();
  useGlobalShortcuts();

  const { t } = useTranslation();
  const branding = useBranding();
  const navItemsList = useNavItems();
  const navGroupsList = useNavGroups();
  const desktopRoutes = useRegistryStore((state) => state.desktopRoutes);
  const fetchSessions = useSessionStore((state) => state.fetchSessions);
  const openConversationProjectId = useSessionStore(
    (state) => state.activeProjectId,
  );
  const fetchAllTasks = useTaskStore((state) => state.fetchAllTasks);
  const allProjects = useProjectStore((state) => state.projects);
  const setAllProjects = useProjectStore((state) => state.setProjects);
  const rightPanelCollapsed = usePanelStore((state) => state.collapsed);
  const togglePanel = usePanelStore((state) => state.toggle);

  const [rightPanel, setRightPanel] = useState<ReactNode | null>(null);
  const [pageHeader, setPageHeader] = useState<ReactNode | null>(null);
  const [headerClassName, setHeaderClassName] = useState<string | undefined>();
  const [hideHeader, setHideHeader] = useState(false);
  const [asideClassName, setAsideClassName] = useState<string | undefined>();
  const [mainClassName, setMainClassName] = useState<string | undefined>();
  const [contentInnerClassName, setContentInnerClassName] = useState<
    string | undefined
  >();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(max-width: 1040px)").matches,
  );
  const [createOpen, setCreateOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<{
    id: string;
    name: string;
  } | null>(null);
  // Project export target — owns ExportProjectDialog open state.
  const [exportTarget, setExportTarget] = useState<{
    id: string;
    name: string;
  } | null>(null);
  // Project import — owns the hidden file input + ImportProjectDialog.
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);
  const [newName, setNewName] = useState("");
  const [newRootPath, setNewRootPath] = useState("");
  const [createError, setCreateError] = useState("");
  // Execution location for the create dialog (multi-target editions; inert
  // no-target state on single-backend builds).
  const execLocation = useProjectExecutionLocation();
  // Initial members for the create dialog (shared with the projects-page
  // entry). Source candidates from the chosen target's backend so a cloud-
  // bound project only lists cloud-deployable agents.
  const memberPicker = useAgentDeployPicker(
    execLocation.effectiveTarget?.baseUrl,
  );
  const [historyIdx, setHistoryIdx] = useState<number>(
    () => (window.history.state as { idx?: number } | null)?.idx ?? 0,
  );
  const [historyMaxIdx, setHistoryMaxIdx] = useState<number>(historyIdx);

  // The connector nav dot polls lazily (5 min). Landing on the home screen
  // (``/`` redirects to ``/conversation/new``) forces a fresh check so a status
  // that flipped during the gap shows up at once instead of on the next tick.
  useEffect(() => {
    if (
      location.pathname === "/conversation/new" ||
      location.pathname === "/"
    ) {
      refreshConnectorAlert();
    }
  }, [location.pathname]);

  // Window maximize state for the custom window controls (Windows/Linux).
  const [isMaximized, setIsMaximized] = useState(false);

  // Query initial maximize state on mount (non-mac Electron only).
  useEffect(() => {
    if (platform.isElectron && !platform.isMac && platform.windowIsMaximized) {
      void platform.windowIsMaximized().then(setIsMaximized);
    }
  }, [platform.isElectron, platform.isMac, platform.windowIsMaximized]);

  // Re-show the in-app update toast (bottom-left floating card) if the user
  // dismissed it. The standalone update window is no longer used.
  const handleOpenUpdateWindow = useCallback(() => {
    useUpdaterStore.getState().show();
  }, []);

  const fetchProjects = useCallback(async () => {
    try {
      const data = await projectsApi.list();
      setAllProjects(data.projects);
    } catch {
      // Sidebar projects are non-critical; page-level calls surface errors.
    }
  }, [setAllProjects]);

  useEffect(() => {
    hydrateTheme();
    applyBrandColors(branding);
    useSettingsStore
      .getState()
      .fetchFromBackend()
      .then(() => {
        const { locale } = useSettingsStore.getState();
        initI18n({
          locale: locale as "en-US" | "zh-CN",
          fallbackLocale: "zh-CN",
        });
        hydrateTheme();
      })
      .catch(() => {
        // The shell can load before the backend is ready.
      });
  }, [branding]);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1040px)");
    const sync = (matches: boolean) => setSidebarCollapsed(matches);
    sync(mq.matches);
    const handler = (event: MediaQueryListEvent) => sync(event.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  // See resolveRightPanelAutoFold for the fold/unfold rule; the ref carries the
  // "this collapse was ours" claim across width changes.
  const autoCollapsedRef = useRef(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1400px)");
    const sync = (matches: boolean) => {
      const { collapsed, setCollapsed } = usePanelStore.getState();
      const action = resolveRightPanelAutoFold({
        narrow: matches,
        collapsed,
        autoCollapsed: autoCollapsedRef.current,
      });
      autoCollapsedRef.current = action.autoCollapsed;
      if (action.setCollapsed !== null) setCollapsed(action.setCollapsed);
    };
    sync(mq.matches);
    const handler = (event: MediaQueryListEvent) => sync(event.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  // Any deliberate re-open drops our claim, so a later manual close can't be
  // undone by a resize.
  useEffect(
    () =>
      usePanelStore.subscribe((state, previous) => {
        if (previous.collapsed && !state.collapsed) {
          autoCollapsedRef.current = false;
        }
      }),
    [],
  );

  useEffect(() => {
    const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0;
    void Promise.resolve().then(() => {
      setHistoryIdx(idx);
      setHistoryMaxIdx((prev) => (idx > prev ? idx : prev));
    });
  }, [location.key]);

  useEffect(() => {
    void fetchProjects();
  }, [fetchProjects]);

  useEffect(() => {
    void fetchSessions();
  }, [fetchSessions]);

  useEffect(() => {
    void fetchAllTasks();
  }, [fetchAllTasks]);

  const prevPathRef = useRef(location.pathname);
  useEffect(() => {
    const prev = prevPathRef.current;
    prevPathRef.current = location.pathname;
    if (location.pathname === prev) return;
    // Conversation route changes refresh the chat-side rails; entering
    // or leaving a task / project view refreshes the TASKS rail so a
    // freshly kicked-off task appears in the sidebar without a reload.
    if (location.pathname.startsWith("/conversation/")) {
      void fetchSessions();
      void fetchProjects();
    }
    if (
      location.pathname.startsWith("/tasks/") ||
      location.pathname.startsWith("/projects/") ||
      prev.startsWith("/tasks/") ||
      prev.startsWith("/projects/")
    ) {
      void fetchAllTasks();
    }
  }, [location.pathname, fetchSessions, fetchProjects, fetchAllTasks]);

  // Set of real project ids — the grouping key for "does this run belong to a
  // project?". Runs whose ``project_id`` isn't in here (quick assistant chats,
  // project-less tasks) fall into the loose "Chats" group instead.
  const projectIdSet = useMemo(
    () =>
      new Set(
        allProjects
          .filter((project) => project.kind === "project")
          .map((project) => project.id),
      ),
    [allProjects],
  );

  // Sidebar RECENTS — merge the live-running and finished run lists, sort
  // by ``updated_at`` desc, hand the top-8 to the sidebar (it slices to 3
  // when folded). The fetch + refetch logic is intentionally lean:
  //
  // 1. ``liveRuns`` is the array returned by the global stream-driven
  //    ``useRunningRuns`` hook — its REFERENCE changes on every refresh
  //    even when the ids haven't, which would re-run any effect keyed
  //    on it. We collapse it to a stable comma-joined ``liveRunIds``
  //    string so downstream effects only fire on a real transition
  //    (someone started / finished), not on every refresh.
  // 2. One effect handles both initial fetch and transitions; the
  //    1.5s delayed retry covers the window where the DB hasn't yet
  //    flipped the status by the time the running pool drops the row.
  // 3. A slow safety-net interval (60s, paused while the tab is
  //    hidden) catches any transition the change-detect path missed
  //    — sub-2.5s turns whose ``running`` state was never seen.
  //
  // Three independent effects with 10s tickers + ``[liveRuns]`` deps
  // were the previous shape; HMR was accumulating zombie intervals
  // each save and saturating the browser's connection pool, which
  // visibly blocked the conversation page's ``bootstrap`` chain.
  const { runs: liveRuns } = useRunningRuns();
  const [finishedRuns, setFinishedRuns] = useState<RunSummary[]>([]);
  const refreshFinishedRuns = useCallback(() => {
    void runsApi
      .list({ status: "finished" })
      .then((res) => setFinishedRuns(res.runs))
      .catch(() => undefined);
  }, []);

  // Stable identity for the live-running set — only changes when the
  // *contents* (ids) change, not when the poller hands back a new
  // array. Sort first so reordering doesn't trip the comparison.
  const liveRunIds = useMemo(
    () =>
      liveRuns
        .map((r) => r.session_id)
        .sort()
        .join(","),
    [liveRuns],
  );

  // Initial fetch + transition fetch in one effect. Mount fires once
  // (liveRunIds == ""); subsequent runs fire only on real transitions.
  useEffect(() => {
    refreshFinishedRuns();
    const retry = window.setTimeout(refreshFinishedRuns, 1500);
    return () => window.clearTimeout(retry);
  }, [liveRunIds, refreshFinishedRuns]);

  // Refresh the finished list the instant a run completes. The control-plane
  // stream delivers a ``run.finished`` frame for EVERY run that ends —
  // including the sub-2.5s turns that never appeared in the running pool, which
  // the ``liveRunIds`` change-detect above misses. This replaces the old 60s
  // ``/v1/runs?status=finished`` safety-net poll with precise, event-driven
  // refreshes (no periodic polling). Debounced so a burst collapses to one.
  useEffect(() => {
    let debounce: number | null = null;
    const unsub = subscribeUserStream((frame) => {
      if (frame.eventType !== "run.finished") return;
      if (debounce !== null) return;
      debounce = window.setTimeout(() => {
        debounce = null;
        refreshFinishedRuns();
      }, 250);
    });
    return () => {
      unsub();
      if (debounce !== null) window.clearTimeout(debounce);
    };
  }, [refreshFinishedRuns]);

  // Client-side session creations that never produce a ``run.finished``
  // frame (today: fork — the new session is born idle WITH history) nudge
  // the finished-runs window explicitly; without this the forked chat
  // waits for the next unrelated turn to appear in the sidebar.
  useEffect(() => {
    const onRefresh = () => refreshFinishedRuns();
    window.addEventListener("valuz-runs-refresh", onRefresh);
    return () => window.removeEventListener("valuz-runs-refresh", onRefresh);
  }, [refreshFinishedRuns]);

  // Per-project runs for the sidebar accordion. The global finished-runs
  // window above is ONE recency list shared with quick chats, so an install
  // with a few hundred of those pushes every project conversation past its
  // tail — projects then render with nothing nested under them and no
  // expand affordance at all. Each project asks the same endpoint for its own
  // window (``project_id`` filters in SQL), which is bounded and independent
  // of how noisy the global list is.
  const [projectRuns, setProjectRuns] = useState<Map<string, RunSummary[]>>(
    () => new Map(),
  );
  const projectIdsKey = useMemo(
    () => [...projectIdSet].sort().join(","),
    [projectIdSet],
  );
  useEffect(() => {
    const ids = projectIdsKey ? projectIdsKey.split(",") : [];
    if (ids.length === 0) {
      setProjectRuns((prev) => (prev.size === 0 ? prev : new Map()));
      return;
    }
    let cancelled = false;
    void Promise.all(
      ids.map((id) =>
        runsApi
          .list({
            status: "finished",
            projectId: id,
            limit: PROJECT_RUNS_LIMIT,
          })
          .then((res) => [id, res.runs] as const)
          .catch(() => [id, [] as RunSummary[]] as const),
      ),
    ).then((entries) => {
      if (!cancelled) setProjectRuns(new Map(entries));
    });
    return () => {
      cancelled = true;
    };
  }, [projectIdsKey, liveRunIds]);

  // Merge live + finished runs (dedupe by session), newest first, then split
  // into per-project buckets and a loose "Chats" list. Each project's chats +
  // tasks nest under it in the sidebar; everything project-less goes to Chats.
  const { projectRunItems, chatItems } = useMemo(() => {
    const byId = new Map<string, RunSummary>();
    for (const r of liveRuns) byId.set(r.session_id, r);
    for (const r of finishedRuns) {
      if (!byId.has(r.session_id)) byId.set(r.session_id, r);
    }
    // Project-scoped rows fill in what the global window dropped. Added after
    // the global pass so a run already known there keeps that row (identical
    // payload either way — this is a de-dupe, not a precedence rule).
    for (const runs of projectRuns.values()) {
      for (const r of runs) {
        if (!byId.has(r.session_id)) byId.set(r.session_id, r);
      }
    }
    const liveSet = new Set(liveRuns.map((r) => r.session_id));
    const toItem = (r: RunSummary): DesktopSidebarRecentItem => ({
      id: r.session_id,
      title: r.title,
      // Tasks have their own page; chats route to the conversation view. Fall
      // back to the conversation if a task somehow lacks a ``task_id`` so the
      // row is always clickable.
      href:
        r.source_kind === "task" && r.task_id
          ? `/tasks/${encodeURIComponent(r.task_id)}`
          : `/conversation/${encodeURIComponent(r.session_id)}`,
      kind: r.source_kind === "task" ? "task" : "chat",
      isRunning: liveSet.has(r.session_id),
      // Whole-session fork availability (docs/design/session-fork.md D3):
      // user chats on a fork-wired runtime, not currently running.
      canFork:
        r.source_kind !== "task" &&
        r.origin === "user" &&
        FORKABLE_RUNTIMES.has(r.runtime ?? "") &&
        !liveSet.has(r.session_id),
      // Execution origin (multi-target editions; fan-out tags rows) — a
      // leading icon, not a pill, so the title keeps its width.
      leadingIcon: r.exec_origin ? (
        <OriginIcon origin={r.exec_origin} />
      ) : undefined,
    });
    const sorted = [...byId.values()].sort(
      (a, b) => b.updated_at - a.updated_at,
    );
    const byProject = new Map<string, DesktopSidebarRecentItem[]>();
    const loose: DesktopSidebarRecentItem[] = [];
    for (const r of sorted) {
      // Automation-triggered runs (chats AND tasks) live in the Activity
      // 自动化 tab, not the sidebar's conversation/task lists — skip them so
      // recurring fires don't flood the menu.
      if (r.origin === "automation") continue;
      const item = toItem(r);
      if (r.project_id && projectIdSet.has(r.project_id)) {
        const arr = byProject.get(r.project_id);
        if (arr) arr.push(item);
        else byProject.set(r.project_id, [item]);
      } else {
        loose.push(item);
      }
    }
    return { projectRunItems: byProject, chatItems: loose };
  }, [liveRuns, finishedRuns, projectRuns, projectIdSet]);

  const projectGroups: DesktopSidebarProjectGroup[] = useMemo(
    () =>
      allProjects
        .filter((project) => project.kind === "project")
        .map((project) => ({
          id: project.id,
          label: project.name,
          href: `/projects/${project.id}`,
          items: projectRunItems.get(project.id) ?? [],
          // Execution origin (multi-target editions; fan-out tags rows) —
          // replaces the folder glyph instead of appending a pill.
          icon: project.exec_origin ? (
            <OriginIcon origin={project.exec_origin} className="h-3.5 w-3.5" />
          ) : undefined,
        })),
    [allProjects, projectRunItems],
  );

  // The project that owns the current route — resolved fast so its accordion
  // auto-expands the instant you open a project conversation. A project landing
  // is read straight from the URL; a conversation maps its session id → the
  // owning project via the session store (populated the moment the session is
  // created), which runs well ahead of the runs list that backs
  // ``projectRunItems``. Tasks / anything else fall back to matching the route
  // against the run items.
  const activeProjectId = useMemo<string | null>(() => {
    const path = location.pathname;
    const projMatch = path.match(/^\/projects\/([^/]+)/);
    if (projMatch) return decodeURIComponent(projMatch[1]);
    // A conversation's owning project is published to the store by the
    // conversation page (authoritative, straight from the loaded session
    // detail) — immediate, unlike the lagging runs list.
    if (path.startsWith("/conversation/") && openConversationProjectId) {
      return openConversationProjectId;
    }
    // Tasks / anything else: match the route against the run items.
    for (const [pid, items] of projectRunItems) {
      if (
        items.some((it) => path === it.href || path.startsWith(`${it.href}/`))
      )
        return pid;
    }
    return null;
  }, [location.pathname, openConversationProjectId, projectRunItems]);

  const handleCreateProject = async () => {
    const trimmedName = newName.trim();
    const trimmedPath = newRootPath.trim();
    // A remote execution target has no access to this machine's paths — the
    // backend allocates a managed cwd and the picked folder uploads after.
    const managed =
      directoryFieldMode === "managed" || execLocation.isRemoteTarget;
    if (!trimmedName || (!managed && !trimmedPath)) return;
    setCreateError("");
    try {
      const payload = managed
        ? { name: trimmedName }
        : { name: trimmedName, root_path: trimmedPath };
      // Routes to the chosen execution target and records the project's
      // origin BEFORE the deploys below, so they hit the same backend.
      const ws = await execLocation.createProjectAt(payload);
      const failed = await memberPicker.deploy(ws.id);
      if (failed > 0) {
        toast.warning(t("project.deployPartialFail", { count: failed }));
      }
      toast.success(t("project.created", { name: trimmedName }));
      if (execLocation.isRemoteTarget && execLocation.initialFiles.length > 0) {
        toast.info(t("project.initialFilesUploading"));
        void execLocation
          .uploadInitialFiles(ws.id)
          .then((count) =>
            toast.success(t("project.initialFilesUploaded", { count })),
          )
          .catch(() => toast.error(t("project.initialFilesFailed")));
      } else if (managed && onUploadInitialContent) {
        void onUploadInitialContent(ws.id);
      }
      setNewName("");
      setNewRootPath("");
      memberPicker.reset();
      execLocation.reset();
      setCreateOpen(false);
      await fetchProjects();
      navigate(`/projects/${ws.id}`);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : t("project.createFailed");
      setCreateError(
        message.includes("409") ? t("project.dirAlreadyBound") : message,
      );
    }
  };

  const handleSelectDirectory = async () => {
    const path = await platform.selectDirectory();
    if (path) {
      setNewRootPath(path);
      setCreateError("");
    }
  };

  // Multi-target degraded mode: one side of the list fan-out failing means
  // the lists render but may be incomplete — surface a slim hint bar.
  const degradedTargets = useDegradedListTargets();
  const degradedLabels = useMemo(() => {
    if (degradedTargets.length === 0) return "";
    const registered = getExecutionTargets();
    return degradedTargets
      .map((id) => {
        const target = registered.find((candidate) => candidate.id === id);
        return target ? t(target.labelKey as Parameters<typeof t>[0]) : id;
      })
      .join(" / ");
  }, [degradedTargets, t]);

  const pageLabel = useMemo(() => {
    const match = desktopRoutes.find(
      (route) =>
        route.path === location.pathname ||
        location.pathname.startsWith(`${route.path}/`),
    );
    const raw = match?.label ?? branding.appName;
    return t(raw as Parameters<typeof t>[0]);
  }, [desktopRoutes, location.pathname, branding.appName, t]);

  const outletContext: ProjectOutletContext = {
    directoryFieldMode,
    setRightPanel,
    setHeader: setPageHeader,
    setHeaderClassName,
    setHideHeader,
    setAsideClassName,
    setMainClassName,
    setContentInnerClassName,
  };

  const suppressRouteHeader = location.pathname.startsWith("/projects");
  const header =
    pageHeader ??
    (suppressRouteHeader ? null : (
      <span className="text-base font-medium text-ink-heading">
        {pageLabel}
      </span>
    ));
  const resolvedRightPanel = controlledRightPanel ?? rightPanel;
  // Skills / Connectors / Agents use the right-panel slot for a master-detail layout
  // (list + detail), not a collapsible side panel — so the collapse toggle
  // is meaningless there and is hidden.
  const suppressRightPanelToggle =
    location.pathname.startsWith("/skills") ||
    location.pathname.startsWith("/connectors") ||
    location.pathname.startsWith("/agents");
  const rightPanelToggle =
    resolvedRightPanel && !suppressRightPanelToggle ? (
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label={
                rightPanelCollapsed
                  ? t("sidebar.expandPanel")
                  : t("sidebar.collapsePanel")
              }
              onClick={() => togglePanel()}
              className="flex h-[22px] w-[22px] items-center justify-center rounded-[5px] text-ink-body transition-colors hover:bg-surface-muted"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                <line
                  x1={rightPanelCollapsed ? 17 : 15}
                  y1={rightPanelCollapsed ? 7 : 3}
                  x2={rightPanelCollapsed ? 17 : 15}
                  y2={rightPanelCollapsed ? 17 : 21}
                />
              </svg>
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {rightPanelCollapsed
              ? t("sidebar.expandPanel")
              : t("sidebar.collapsePanel")}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    ) : null;
  // ADR-022: the decision-inbox badge always sits in the topbar control
  // group (it self-hides when there are no pendings), so the control is
  // present whenever the badge has something to show even if the page
  // contributes no topbarActions / rightPanelToggle.
  const topbarRightControl = (
    <div className="flex items-center gap-1">
      <NotificationBadge />
      {topbarActions}
      {rightPanelToggle}
    </div>
  );

  return (
    <ErrorBoundary>
      <OfflineBanner />
      <NotificationProvider />
      <NotificationDrawer />
      <AppShell
        appTitle={branding.appName}
        activePath={location.pathname}
        LinkComponent={Link}
        topBar={
          <TopBar
            sidebarCollapsed={sidebarCollapsed}
            onToggleSidebar={() => setSidebarCollapsed((value) => !value)}
            onGoBack={() => navigate(-1)}
            onGoForward={() => navigate(1)}
            canGoBack={historyIdx > 0}
            canGoForward={historyIdx < historyMaxIdx}
            trafficLightPad={platform.isMac}
            logo={
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    aria-label={`${branding.appName} menu`}
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-surface-soft focus:outline-none"
                  >
                    <img
                      src={logoSrc}
                      alt="Valuz"
                      className="h-5 w-5 object-contain"
                    />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="start"
                  className="min-w-[180px]"
                  style={logoMenuContentStyle}
                >
                  <DropdownMenuItem onSelect={() => navigate("/")}>
                    <Home className="mr-2 h-3.5 w-3.5" />
                    {t("sidebar.home")}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() => navigate("/conversation/new")}
                  >
                    <MessageCirclePlus className="mr-2 h-3.5 w-3.5" />
                    {t("sidebar.newChat")}
                  </DropdownMenuItem>
                  {platform.isElectron ? (
                    <DropdownMenuItem
                      onSelect={() => void platform.openNewWindow()}
                    >
                      <AppWindow className="mr-2 h-3.5 w-3.5" />
                      {t("sidebar.newWindow")}
                    </DropdownMenuItem>
                  ) : null}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={() => navigate("/settings")}>
                    <Settings className="mr-2 h-3.5 w-3.5" />
                    {t("nav.settings")}
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => navigate("/help")}>
                    <HelpCircle className="mr-2 h-3.5 w-3.5" />
                    {t("sidebar.help")}
                  </DropdownMenuItem>
                  {platform.isElectron ? (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        onSelect={() => void platform.quitApp()}
                      >
                        <LogOut className="mr-2 h-3.5 w-3.5" />
                        {t("sidebar.quit")}
                      </DropdownMenuItem>
                    </>
                  ) : null}
                </DropdownMenuContent>
              </DropdownMenu>
            }
            rightControl={topbarRightControl}
            extraLeft={
              <>
                {platform.isElectron && (
                  <UpdateButton onClick={handleOpenUpdateWindow} />
                )}
                {platform.isElectron && !platform.isMac && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        aria-label={t(
                          "cli.menuLabel" as Parameters<typeof t>[0],
                        )}
                        className="flex h-[22px] w-[22px] items-center justify-center rounded-[5px] text-ink-body transition-colors hover:bg-surface-muted"
                      >
                        <Menu className="h-4 w-4" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent
                      align="start"
                      className="min-w-[180px]"
                      style={logoMenuContentStyle}
                    >
                      <DropdownMenuItem
                        onSelect={() => void platform.windowReload?.()}
                      >
                        <RefreshCw className="mr-2 h-3.5 w-3.5" />
                        {t("common.reload" as Parameters<typeof t>[0])}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => void platform.windowToggleDevTools?.()}
                      >
                        <Terminal className="mr-2 h-3.5 w-3.5" />
                        Toggle DevTools
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        onSelect={() =>
                          void platform.windowToggleFullscreen?.()
                        }
                      >
                        <Maximize className="mr-2 h-3.5 w-3.5" />
                        Toggle Fullscreen
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        onSelect={() => void platform.cliInstallToPath?.()}
                      >
                        <Terminal className="mr-2 h-3.5 w-3.5" />
                        {t("cli.installToPath" as Parameters<typeof t>[0])}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => void platform.cliUninstallFromPath?.()}
                      >
                        <Terminal className="mr-2 h-3.5 w-3.5" />
                        {t("cli.uninstallFromPath" as Parameters<typeof t>[0])}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </>
            }
            windowControls={
              platform.isElectron && !platform.isMac ? (
                <WindowControls
                  onMinimize={() => void platform.windowMinimize?.()}
                  onMaximize={() =>
                    void platform.windowMaximize?.().then(setIsMaximized)
                  }
                  onClose={() => void platform.windowClose?.()}
                  isMaximized={isMaximized}
                />
              ) : undefined
            }
          />
        }
        sidebar={
          <DesktopSidebar
            activePath={location.pathname}
            activeProjectId={activeProjectId}
            projectGroups={projectGroups}
            bottomItems={navItemsList}
            navGroups={navGroupsList}
            chats={chatItems}
            onRecentRename={(sessionId, newName) => {
              const trimmed = newName.trim();
              if (!trimmed) return;
              sessionsApi
                .rename(sessionId, trimmed)
                .then(() => {
                  toast.success(t("sidebar.renamed"));
                  refreshFinishedRuns();
                })
                .catch(() => toast.error(t("sidebar.renameFailed")));
            }}
            onRecentFork={(sessionId) => {
              // Whole-session fork (docs/design/session-fork.md). Synchronous
              // by design (D5, ~1–2s); success lands in the new conversation.
              sessionsApi
                .fork(sessionId)
                .then((forked) => {
                  toast.success(
                    t("conversation.forked" as Parameters<typeof t>[0]),
                  );
                  refreshFinishedRuns();
                  navigate(`/conversation/${forked.id}`);
                })
                .catch(() =>
                  toast.error(
                    t("conversation.forkFailed" as Parameters<typeof t>[0]),
                  ),
                );
            }}
            onRecentDelete={(sessionId) => {
              // Optimistic local removal — the row disappears immediately
              // even though the backend round-trip is still in flight.
              // The 10s safety-net poll reconciles if the call fails
              // (which the catch toasts visibly anyway).
              setFinishedRuns((prev) =>
                prev.filter((r) => r.session_id !== sessionId),
              );
              sessionsApi
                .delete(sessionId)
                .then(() => {
                  toast.success(t("common.deleted"));
                  refreshFinishedRuns();
                  if (
                    location.pathname.startsWith(`/conversation/${sessionId}`)
                  ) {
                    navigate("/conversation/new");
                  }
                })
                .catch(() => {
                  toast.error(t("common.deleteFailed"));
                  refreshFinishedRuns();
                });
            }}
            sidebarHeader={sidebarHeader}
            sidebarFooter={sidebarFooter}
            sidebarExtraItems={sidebarExtraItems}
            mascotSrc={mascotSrc}
            LinkComponent={Link}
            primaryActionHref="/conversation/new"
            onPrimaryAction={refreshConnectorAlert}
            collapsed={sidebarCollapsed}
            onAddProject={() => setCreateOpen(true)}
            onImportProject={() => importInputRef.current?.click()}
            onProjectOpenInFinder={(projectId) => {
              const ws = allProjects.find(
                (project) => project.id === projectId,
              );
              if (!ws?.root_path) {
                toast.error(t("sidebar.projectRootNotSet"));
                return;
              }
              if (!platform.isElectron) {
                toast.error(t("sidebar.desktopOnly"));
                return;
              }
              void platform
                .revealInFinder(ws.root_path)
                .catch(() => toast.error(t("sidebar.openFailed")));
            }}
            onProjectRename={(projectId, newName) => {
              const trimmed = newName.trim();
              if (!trimmed) return;
              projectsApi
                .rename(projectId, trimmed)
                .then(() => {
                  toast.success(t("sidebar.renamed"));
                  void fetchProjects();
                })
                .catch(() => toast.error(t("sidebar.renameFailed")));
            }}
            onProjectRemove={(projectId) => {
              const ws = allProjects.find(
                (project) => project.id === projectId,
              );
              if (ws) setRemoveTarget({ id: ws.id, name: ws.name });
            }}
            onProjectExport={(projectId) => {
              const ws = allProjects.find(
                (project) => project.id === projectId,
              );
              if (ws) setExportTarget({ id: ws.id, name: ws.name });
            }}
          />
        }
        shellClassName="bg-background"
        hideHeader={hideHeader}
        contentClassName="overflow-y-auto p-0"
        asideClassName={
          resolvedRightPanel ? (asideClassName ?? "w-[345px]") : undefined
        }
        mainClassName={mainClassName}
        contentInnerClassName={contentInnerClassName}
        // Degraded multi-target hint rides the shell's notice slot — pinned
        // at the very top of the middle panel, above the header and outside
        // the page's padded/scrolling content, so every page (headered,
        // hidden-header, outer-scroll) shows it in the same place.
        notice={
          degradedLabels ? (
            <div className="shrink-0 border-b border-warning-border bg-warning-light px-4 py-1.5 text-xs text-warning-text">
              {t("system.execTargetUnreachable", { targets: degradedLabels })}
            </div>
          ) : null
        }
        header={header}
        headerClassName={headerClassName}
        aside={
          suppressRightPanelToggle || !rightPanelCollapsed
            ? resolvedRightPanel
            : null
        }
      >
        <div
          // Keyed so a page change replays the enter animation — except
          // within the conversation family, which transitions in place
          // (see ``outletTransitionKey``).
          key={outletTransitionKey(location.pathname)}
          className="h-full min-h-0 animate-page-enter"
        >
          <Outlet context={outletContext} />
        </div>
      </AppShell>
      <AppToaster />

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) {
            setCreateError("");
            memberPicker.reset();
            execLocation.reset();
          }
        }}
      >
        <DialogContent className="gap-0 p-0">
          <DialogHeader className="px-[18px] pt-[18px] pb-1">
            <DialogTitle className="text-sm leading-5">
              {t("project.createTitle")}
            </DialogTitle>
            <DialogDescription>{t("project.createDesc")}</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-[14px] px-[18px] py-[14px]">
            <div className="flex flex-col">
              <label className="mb-[5px] text-xs font-medium text-foreground">
                {t("project.projectName")}
              </label>
              <Input
                placeholder="my-project"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
              />
            </div>
            <ProjectLocationFields state={execLocation} />
            {execLocation.isRemoteTarget ? (
              // Remote target: managed cwd + the optional initial-folder
              // upload above replace the local directory binding entirely.
              createError ? (
                <p className="text-xs text-destructive">{createError}</p>
              ) : null
            ) : directoryFieldMode === "managed" ? (
              <div className="flex flex-col">
                <label className="mb-[5px] text-xs font-medium text-foreground">
                  {t("project.projectDir")}
                </label>
                <p className="text-xs text-muted-foreground">
                  {t("project.managedDirHint")}
                </p>
                {createError ? (
                  <p className="mt-[3px] text-xs text-destructive">
                    {createError}
                  </p>
                ) : null}
              </div>
            ) : (
              <div className="flex flex-col">
                <label className="mb-[5px] text-xs font-medium text-foreground">
                  {t("project.projectDir")}
                </label>
                <div className="flex items-center gap-2">
                  {directoryFieldMode === "picker" ? (
                    <button
                      type="button"
                      className="flex h-8 flex-1 items-center rounded-lg border border-input bg-surface px-2.5 text-sm text-foreground transition-[border-color,box-shadow,color,background-color] hover:border-ring focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/20 focus-visible:outline-none"
                      onClick={() => void handleSelectDirectory()}
                    >
                      <span
                        className={
                          newRootPath
                            ? "truncate text-foreground"
                            : "text-muted-foreground"
                        }
                      >
                        {newRootPath || t("project.selectDir")}
                      </span>
                    </button>
                  ) : (
                    <Input
                      value={newRootPath}
                      onChange={(event) => {
                        setNewRootPath(event.target.value);
                        setCreateError("");
                      }}
                      placeholder={t("project.selectDir")}
                      className="flex-1"
                    />
                  )}
                  {directoryFieldMode === "picker" || platform.isElectron ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-8 shrink-0"
                      onClick={() => void handleSelectDirectory()}
                    >
                      <FolderOpen className="mr-1.5 h-4 w-4" />
                      {t("project.browse")}
                    </Button>
                  ) : null}
                </div>
                <p className="mt-[3px] text-xs text-muted-foreground">
                  {t("project.dirHint")}
                </p>
                {createError ? (
                  <p className="mt-[3px] text-xs text-destructive">
                    {createError}
                  </p>
                ) : null}
              </div>
            )}
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground">
                {t("project.deployAgents")}
              </label>
              <AgentCheckboxList picker={memberPicker} />
            </div>
            {projectDialogExtraFields}
          </div>
          <DialogFooter className="px-[18px] pt-1 pb-4">
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void handleCreateProject()}
              disabled={
                !newName.trim() ||
                (directoryFieldMode !== "managed" &&
                  !execLocation.isRemoteTarget &&
                  !newRootPath.trim())
              }
            >
              {t("project.create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <DeleteConfirmDialog
        open={!!removeTarget}
        onOpenChange={(open) => {
          if (!open) setRemoveTarget(null);
        }}
        itemName={removeTarget?.name}
        onConfirm={() => {
          if (!removeTarget) return;
          const projectId = removeTarget.id;
          projectsApi
            .delete(projectId)
            .then(() => {
              toast.success(t("sidebar.removed"));
              void fetchProjects();
              if (location.pathname.startsWith(`/projects/${projectId}`)) {
                navigate("/projects");
              }
            })
            .catch(() => toast.error(t("sidebar.removeFailed")))
            .finally(() => setRemoveTarget(null));
        }}
      />

      {/* Project export/import — hidden file input + the two dialogs. The input
          is re-used for any "Import project…" entry point (sidebar, projects
          page). Same value-reset trick as AgentsPage so re-picking the same
          file still fires onChange. */}
      <input
        ref={importInputRef}
        type="file"
        accept=".valuzpack,.zip"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0] ?? null;
          e.target.value = "";
          if (f) {
            setImportFile(f);
            setImportOpen(true);
          }
        }}
      />
      <ExportProjectDialog
        projectId={exportTarget?.id ?? ""}
        projectName={exportTarget?.name ?? ""}
        open={!!exportTarget}
        onOpenChange={(open) => {
          if (!open) setExportTarget(null);
        }}
      />
      <ImportProjectDialog
        file={importFile}
        open={importOpen}
        onOpenChange={(open) => {
          setImportOpen(open);
          if (!open) setImportFile(null);
        }}
        onImported={(project) => {
          useProjectStore.getState().upsertProject(project);
          void fetchProjects();
          navigate(`/projects/${project.id}`);
        }}
      />
    </ErrorBoundary>
  );
}

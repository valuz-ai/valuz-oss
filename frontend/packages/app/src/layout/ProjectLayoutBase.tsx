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
  useBranding,
  useGlobalShortcuts,
  usePanelStore,
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
  ErrorBoundary,
  Input,
  OfflineBanner,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
  TopBar,
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
  MessageCirclePlus,
  Settings,
} from "lucide-react";
import { toast } from "sonner";
import {
  DecisionDrawer,
  DecisionInboxBadge,
  DecisionInboxProvider,
} from "../components/DecisionInbox";
import { usePlatform } from "../platform";
import { UpdateButton } from "../components/UpdateButton";
import type { ProjectOutletContext } from "./types";

export type DirectoryFieldMode = "input" | "picker";

export interface ProjectLayoutBaseProps {
  logoSrc: string;
  logoMenuContentStyle?: CSSProperties;
  directoryFieldMode?: DirectoryFieldMode;
  /** Rendered at the very top of the sidebar, above "新对话". Overlay
   * editions inject an org / account switcher here. */
  sidebarHeader?: ReactNode;
  sidebarExtraItems?: ReactNode;
  topbarActions?: ReactNode;
  projectDialogExtraFields?: ReactNode;
  rightPanel?: ReactNode;
  mascotSrc?: string | null;
}

const NAV_ICON_MAP: Record<string, DesktopSidebarBottomItem["icon"]> = {
  assistant: "assistant",
  skills: "skills",
  scheduled: "scheduled",
  activity: "activity",
  knowledge: "knowledge",
  settings: "settings",
  agents: "agents",
  connectors: "connectors",
};

function useNavItems(): DesktopSidebarBottomItem[] {
  const { t } = useTranslation();
  const navItems = useRegistryStore((state) => state.navItems);
  const { count: runningCount } = useRunningRuns();
  return navItems.map((item) => ({
    id: item.id,
    label: t(item.label as Parameters<typeof t>[0]),
    href: item.href,
    icon: NAV_ICON_MAP[item.id] ?? "settings",
    group: item.navGroup ?? "project",
    badgeCount: item.id === "activity" ? runningCount : undefined,
  }));
}

export function ProjectLayoutBase({
  logoSrc,
  logoMenuContentStyle,
  directoryFieldMode = "picker",
  sidebarHeader,
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
  const desktopRoutes = useRegistryStore((state) => state.desktopRoutes);
  const fetchSessions = useSessionStore((state) => state.fetchSessions);
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
  const [newName, setNewName] = useState("");
  const [newRootPath, setNewRootPath] = useState("");
  const [createError, setCreateError] = useState("");
  const [historyIdx, setHistoryIdx] = useState<number>(
    () => (window.history.state as { idx?: number } | null)?.idx ?? 0,
  );
  const [historyMaxIdx, setHistoryMaxIdx] = useState<number>(historyIdx);

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

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1400px)");
    const setCollapsed = usePanelStore.getState().setCollapsed;
    const sync = (matches: boolean) => {
      if (matches) setCollapsed(true);
    };
    sync(mq.matches);
    const handler = (event: MediaQueryListEvent) => sync(event.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

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

  const projectGroups: DesktopSidebarProjectGroup[] = useMemo(
    () =>
      allProjects
        .filter((project) => project.kind === "project")
        .map((project) => ({
          id: project.id,
          label: project.name,
          href: `/projects/${project.id}`,
        })),
    [allProjects],
  );

  // Sidebar RECENTS — merge the live-running and finished run lists, sort
  // by ``updated_at`` desc, hand the top-8 to the sidebar (it slices to 3
  // when folded). The fetch + refetch logic is intentionally lean:
  //
  // 1. ``liveRuns`` is the array returned by the global 2.5s
  //    ``useRunningRuns`` poller — its REFERENCE changes every tick
  //    even when the ids haven't, which would re-run any effect keyed
  //    on it. We collapse it to a stable comma-joined ``liveRunIds``
  //    string so downstream effects only fire on a real transition
  //    (someone started / finished), not on every poll tick.
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

  // Safety net for transitions both the change-detect missed (sub-2.5s
  // turns that never appeared in the running pool). Paused while the
  // tab is hidden so a backgrounded window doesn't keep hammering
  // ``/v1/runs`` for nothing.
  useEffect(() => {
    let handle: number | undefined;
    const start = () => {
      handle = window.setInterval(refreshFinishedRuns, 60000);
    };
    const stop = () => {
      if (handle !== undefined) {
        window.clearInterval(handle);
        handle = undefined;
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        if (handle === undefined) start();
      } else {
        stop();
      }
    };
    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [refreshFinishedRuns]);

  const recentItems: DesktopSidebarRecentItem[] = useMemo(() => {
    const byId = new Map<string, RunSummary>();
    for (const r of liveRuns) byId.set(r.session_id, r);
    for (const r of finishedRuns) {
      if (!byId.has(r.session_id)) byId.set(r.session_id, r);
    }
    const liveSet = new Set(liveRuns.map((r) => r.session_id));
    return [...byId.values()]
      .sort(
        (a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      )
      .slice(0, 8)
      .map((r) => ({
        id: r.session_id,
        title: r.title,
        // Tasks have their own page; chats route to the conversation
        // view. Fall back to the conversation if a task somehow lacks a
        // ``task_id`` so the row is always clickable.
        href:
          r.source_kind === "task" && r.task_id
            ? `/tasks/${encodeURIComponent(r.task_id)}`
            : `/conversation/${encodeURIComponent(r.session_id)}`,
        kind: r.source_kind === "task" ? "task" : "chat",
        isRunning: liveSet.has(r.session_id),
      }));
  }, [liveRuns, finishedRuns]);

  const handleCreateProject = async () => {
    const trimmedName = newName.trim();
    const trimmedPath = newRootPath.trim();
    if (!trimmedName || !trimmedPath) return;
    setCreateError("");
    try {
      const ws = await projectsApi.create({
        name: trimmedName,
        root_path: trimmedPath,
      });
      toast.success(t("project.created", { name: trimmedName }));
      setNewName("");
      setNewRootPath("");
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
  // Skills / Connectors use the right-panel slot for a master-detail layout
  // (list + detail), not a collapsible side panel — so the collapse toggle
  // is meaningless there and is hidden.
  const suppressRightPanelToggle =
    location.pathname.startsWith("/skills") ||
    location.pathname.startsWith("/connectors");
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
      <DecisionInboxBadge />
      {topbarActions}
      {rightPanelToggle}
    </div>
  );

  return (
    <ErrorBoundary>
      <OfflineBanner />
      <DecisionInboxProvider />
      <DecisionDrawer />
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
            trafficLightPad={platform.isElectron}
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
              platform.isElectron ? (
                <UpdateButton onClick={handleOpenUpdateWindow} />
              ) : undefined
            }
          />
        }
        sidebar={
          <DesktopSidebar
            activePath={location.pathname}
            projectGroups={projectGroups}
            bottomItems={navItemsList}
            recents={recentItems}
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
            sidebarExtraItems={sidebarExtraItems}
            mascotSrc={mascotSrc}
            LinkComponent={Link}
            primaryActionHref="/conversation/new"
            collapsed={sidebarCollapsed}
            onAddProject={() => setCreateOpen(true)}
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
              if (!ws) return;
              if (
                !window.confirm(
                  t("sidebar.removeProjectConfirm", { name: ws.name }),
                )
              ) {
                return;
              }
              projectsApi
                .delete(projectId)
                .then(() => {
                  toast.success(t("sidebar.removed"));
                  void fetchProjects();
                  if (location.pathname.startsWith(`/projects/${projectId}`)) {
                    navigate("/projects");
                  }
                })
                .catch(() => toast.error(t("sidebar.removeFailed")));
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
        header={header}
        headerClassName={headerClassName}
        aside={rightPanelCollapsed ? null : resolvedRightPanel}
      >
        <div
          key={location.pathname}
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
          if (!open) setCreateError("");
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("project.createTitle")}</DialogTitle>
            <DialogDescription>{t("project.createDesc")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground">
                {t("project.projectName")}
              </label>
              <Input
                placeholder="my-project"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground">
                {t("project.projectDir")}
              </label>
              <div className="flex items-center gap-2">
                {directoryFieldMode === "picker" && platform.isElectron ? (
                  <button
                    type="button"
                    className="flex h-9 flex-1 items-center rounded-md border border-input bg-transparent px-3 text-sm shadow-sm transition-colors hover:border-ring"
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
                {platform.isElectron ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-9 shrink-0"
                    onClick={() => void handleSelectDirectory()}
                  >
                    <FolderOpen className="mr-1.5 h-4 w-4" />
                    {t("project.browse")}
                  </Button>
                ) : null}
              </div>
              <p className="text-xs text-muted-foreground">
                {t("project.dirHint")}
              </p>
              {createError ? (
                <p className="text-xs text-destructive">{createError}</p>
              ) : null}
            </div>
            {projectDialogExtraFields}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void handleCreateProject()}
              disabled={!newName.trim() || !newRootPath.trim()}
            >
              {t("project.create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </ErrorBoundary>
  );
}

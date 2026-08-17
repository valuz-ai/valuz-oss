import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  Folder,
  MoreHorizontal,
  Plus,
  Store,
  Upload,
  X,
  type LucideIcon,
} from "lucide-react";
import {
  Button,
  CategorizedList,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  PageLoader,
  Popover,
  PopoverContent,
  PopoverTrigger,
  SegmentedControl,
} from "@valuz/ui";
import { ResourceActionSlot } from "../components/ResourceActionSlot";
import {
  agentsApi,
  projectsApi,
  usePanelStore,
  useResourceCategories,
  useTranslation,
  type Agent,
  type MemberWithAgent,
  type ProjectListItem,
} from "@valuz/core";
import type { ResourceCategory } from "@valuz/shared";
import { useProjectOutlet } from "@valuz/app/layout";
import { pickAgentIcon } from "../components/agent-icons";
import { AgentDetailView } from "../components/AgentDetailView";
import { CreateAgentDialog } from "../components/CreateAgentDialog";
import { ImportPackDialog } from "../components/ImportPackDialog";
import { ExportPackDialog } from "../components/ExportPackDialog";
import {
  compareAgentsWithValurionFirst,
  isCloudOnlyAgent,
  isSystemAgent,
} from "./agent-list-state";

/** Keep the always-present built-in agent separate from portable agents. */
function buildAgentCategories(
  t: ReturnType<typeof useTranslation>["t"],
): ResourceCategory<Agent>[] {
  return [
    {
      id: "system",
      label: t("agent.groupSystem" as Parameters<typeof t>[0]),
      order: 0,
      filter: isSystemAgent,
      sort: compareAgentsWithValurionFirst,
    },
    {
      id: "custom",
      label: t("agent.groupCustom" as Parameters<typeof t>[0]),
      order: 1,
      filter: (a: Agent) => !isSystemAgent(a) && a.source !== "official",
      sort: compareAgentsWithValurionFirst,
    },
    {
      id: "official",
      label: t("agent.groupOfficial" as Parameters<typeof t>[0]),
      order: 2,
      filter: (a: Agent) => !isSystemAgent(a) && a.source === "official",
      sort: compareAgentsWithValurionFirst,
    },
  ];
}

type AgentListView = "project" | "all";

interface ProjectAgentMember {
  key: string;
  projectId: string;
  memberSlug: string;
  sourceSlug: string;
  agent: Agent;
}

const AGENT_MARKETPLACE_GUIDE_MAX_COUNT = 3;

function getMemberSourceSlug(member: MemberWithAgent): string | null {
  return member.member.source_agent_slug ?? member.member.agent_slug ?? null;
}

export const AgentsPage = () => {
  const { t } = useTranslation();
  const {
    setHeader,
    setHideHeader,
    setRightPanel,
    setAsideClassName,
    setMainClassName,
  } = useProjectOutlet();
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);
  const navigate = useNavigate();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [projectMembers, setProjectMembers] = useState<ProjectAgentMember[]>(
    [],
  );
  const [loading, setLoading] = useState(true);
  const [activeSlug, setActiveSlug] = useState<string | null>(null);
  const [activeProjectMemberKey, setActiveProjectMemberKey] = useState<
    string | null
  >(null);
  const [listView, setListView] = useState<AgentListView>("all");
  const [collapsedProjectGroups, setCollapsedProjectGroups] = useState<
    Set<string>
  >(new Set());

  // Create-agent wizard (08-agents-module §3): lightweight — 起点 (空白/复制) +
  // name + 模型通道. Description / skills / connectors are filled later on the
  // detail page. Slug is backend-derived from the name (VALUZ-AGENT-SLUG): the
  // UI only sends the display name; the server produces a CJK-preserving,
  // collision-suffixed slug. No client-side slug computation.
  const [createOpen, setCreateOpen] = useState(false);
  // Copy source: when set, the create dialog pre-fills from this agent. Null =
  // blank create. The create form itself lives in the shared CreateAgentDialog.
  const [createSeed, setCreateSeed] = useState<Agent | null>(null);
  const openCreate = useCallback(() => {
    setCreateSeed(null);
    setCreateOpen(true);
  }, []);
  const openCopy = useCallback((agent: Agent) => {
    setCreateSeed(agent);
    setCreateOpen(true);
  }, []);

  // Import a .valuzpack uploaded by the user (preview → confirm).
  const importInputRef = useRef<HTMLInputElement>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  // Multi-select export: pick a set of agents and bundle them into one pack.
  const [selecting, setSelecting] = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [exportOpen, setExportOpen] = useState(false);
  const toggleSelecting = useCallback(() => {
    setSelecting((s) => !s);
    setChecked(new Set());
    setListView("all");
    setActiveSlug(null); // show the list, not a detail pane
    setActiveProjectMemberKey(null);
  }, []);
  const toggleChecked = useCallback((slug: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }, []);

  /* -- Data loading -- */

  const mountedRef = useRef(true);
  const loadData = useCallback(async () => {
    try {
      const [agentRes, projectRes] = await Promise.all([
        agentsApi.listAgents(),
        projectsApi.list(),
      ]);
      const projectRows = projectRes.projects.filter(
        (w) => w.kind === "project",
      );
      const agentsBySlug = new Map(agentRes.agents.map((a) => [a.slug, a]));
      const memberResults = await Promise.all(
        projectRows.map(async (project) => {
          try {
            const res = await agentsApi.listMembers(project.id);
            return res.agents.flatMap((member) => {
              const sourceSlug = getMemberSourceSlug(member);
              if (!sourceSlug) return [];
              const agent = agentsBySlug.get(sourceSlug);
              if (!agent) return [];
              return [
                {
                  key: `${project.id}:${member.member.agent_slug}:${sourceSlug}`,
                  projectId: project.id,
                  memberSlug: member.member.agent_slug,
                  sourceSlug,
                  agent,
                },
              ];
            });
          } catch {
            return [];
          }
        }),
      );
      if (mountedRef.current) {
        setAgents(agentRes.agents);
        setProjects(projectRows);
        setProjectMembers(memberResults.flat());
      }
    } catch {
      if (mountedRef.current) {
        toast.error(t("common.error"));
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    mountedRef.current = true;
    void Promise.resolve().then(loadData);
    return () => {
      mountedRef.current = false;
    };
  }, [loadData]);

  /* -- Layout: split panel -- */

  useEffect(() => {
    setHideHeader(true);
    setMainClassName("w-[345px] flex-none");
    setAsideClassName("flex-1 w-auto");
    return () => {
      setHideHeader(false);
      setHeader(null);
      setMainClassName(undefined);
      setAsideClassName(undefined);
    };
  }, [setHideHeader, setHeader, setMainClassName, setAsideClassName]);

  const didInitRightPanel = useRef(false);
  useEffect(() => {
    if (didInitRightPanel.current) return;
    didInitRightPanel.current = true;
    panelSetCollapsed(false);
  }, [panelSetCollapsed]);

  /* -- Derived state -- */

  const visibleAgents = agents;
  const localAgents = useMemo(
    () => agents.filter((agent) => !isCloudOnlyAgent(agent)),
    [agents],
  );

  const categories = useResourceCategories<Agent>(
    "agent",
    buildAgentCategories(t),
  );

  const deploymentCountBySlug = useMemo(() => {
    const counts = new Map<string, number>();
    for (const member of projectMembers) {
      counts.set(member.sourceSlug, (counts.get(member.sourceSlug) ?? 0) + 1);
    }
    return counts;
  }, [projectMembers]);

  const projectGroups = useMemo(() => {
    const byProjectId = new Map<string, ProjectAgentMember[]>();
    for (const member of projectMembers) {
      const members = byProjectId.get(member.projectId) ?? [];
      members.push(member);
      byProjectId.set(member.projectId, members);
    }
    return projects
      .map((project) => ({
        project,
        members: (byProjectId.get(project.id) ?? []).sort((a, b) =>
          compareAgentsWithValurionFirst(a.agent, b.agent),
        ),
      }))
      .filter(({ members }) => members.length > 0);
  }, [projectMembers, projects]);

  const unassignedAgents = useMemo(() => {
    const deployed = new Set(projectMembers.map((member) => member.sourceSlug));
    return agents
      .filter((agent) => !deployed.has(agent.slug))
      .sort(compareAgentsWithValurionFirst);
  }, [agents, projectMembers]);

  // Keep the detail panel stable across tab switches: honour the explicit
  // selection if it still exists, otherwise fall back to the first agent in
  // the active tab (then any agent at all).
  const currentAgent =
    localAgents.find((a) => a.slug === activeSlug) ?? localAgents[0] ?? null;
  const effectiveActiveSlug = currentAgent?.slug ?? null;
  const effectiveProjectMemberKey =
    activeProjectMemberKey ??
    projectMembers.find((member) => member.agent.slug === effectiveActiveSlug)
      ?.key ??
    null;

  /* -- Icon assignment -- */

  const agentIcons = useMemo(() => {
    const usedIcons = new Set<LucideIcon>();
    const iconsBySlug = new Map<string, LucideIcon>();

    for (const agent of agents) {
      const Icon = pickAgentIcon(agent, usedIcons);
      usedIcons.add(Icon);
      iconsBySlug.set(agent.slug, Icon);
    }

    return iconsBySlug;
  }, [agents]);

  useEffect(() => {
    if (!currentAgent) {
      setRightPanel(null);
      return;
    }
    setRightPanel(
      <div className="h-full overflow-y-auto">
        {/* key by slug: remount on agent change so per-agent dialog/draft
            state (delete confirm, edits, deploy) never leaks across agents. */}
        <AgentDetailView
          key={currentAgent.slug}
          slug={currentAgent.slug}
          onChanged={loadData}
        />
      </div>,
    );
    return () => setRightPanel(null);
  }, [currentAgent, setRightPanel, loadData]);

  const toggleProjectGroup = useCallback((groupId: string) => {
    setCollapsedProjectGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }, []);

  const renderAgentRow = useCallback(
    (
      agent: Agent,
      isSelected: boolean,
      opts?: { deploymentCount?: number },
    ) => {
      const AgentIcon = agentIcons.get(agent.slug) ?? Bot;
      const cloudOnly = isCloudOnlyAgent(agent);
      const isChecked = checked.has(agent.slug);
      const deploymentCount =
        opts?.deploymentCount ?? deploymentCountBySlug.get(agent.slug) ?? 0;
      return (
        <div
          className={`group flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 transition-colors select-none ${
            (selecting ? isChecked : isSelected)
              ? "bg-surface-soft"
              : "hover:bg-surface-soft/60"
          }`}
        >
          {selecting && !cloudOnly && !isSystemAgent(agent) && (
            <span
              className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors ${
                isChecked
                  ? "border-brand bg-brand text-white"
                  : "border-surface-border-strong"
              }`}
            >
              {isChecked && <Check className="h-3 w-3" />}
            </span>
          )}
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-body">
            <AgentIcon className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-1.5">
              <div className="truncate text-sm text-ink-heading">
                {agent.name}
              </div>
              {isSystemAgent(agent) && (
                <span className="inline-flex h-5 shrink-0 items-center rounded-[4px] bg-brand-light px-1.5 py-0 text-[10px] leading-none text-brand">
                  {t("agent.systemBadge" as Parameters<typeof t>[0])}
                </span>
              )}
              {deploymentCount > 0 && (
                <span className="inline-flex h-5 shrink-0 items-center rounded-[4px] bg-surface-soft px-1.5 py-0 text-[10px] leading-none text-ink-meta">
                  {t("agent.deployedInProjects" as Parameters<typeof t>[0], {
                    count: deploymentCount,
                  })}
                </span>
              )}
            </div>
            {agent.description && (
              <div className="truncate text-xs text-ink-meta">
                {agent.description}
              </div>
            )}
          </div>
          {!selecting && (
            <>
              {!cloudOnly && (
                <Popover>
                  <PopoverTrigger asChild>
                    <button
                      type="button"
                      aria-label={t(
                        "agent.copyAgent" as Parameters<typeof t>[0],
                      )}
                      onClick={(e) => e.stopPropagation()}
                      className="flex h-7 w-7 shrink-0 cursor-default items-center justify-center rounded-md text-ink-meta opacity-0 transition-opacity hover:bg-card hover:text-ink-body group-hover:opacity-100 data-[state=open]:opacity-100"
                    >
                      <MoreHorizontal className="h-3.5 w-3.5" />
                    </button>
                  </PopoverTrigger>
                  <PopoverContent align="end" className="w-32 p-1">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        openCopy(agent);
                      }}
                      className="flex w-full cursor-default items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-ink-body transition-colors hover:bg-surface-soft"
                    >
                      <Copy className="h-3.5 w-3.5" />
                      {t("agent.copyAgent" as Parameters<typeof t>[0])}
                    </button>
                  </PopoverContent>
                </Popover>
              )}
              <ResourceActionSlot
                resourceType="agent"
                resource={agent as unknown as Record<string, unknown>}
              />
            </>
          )}
        </div>
      );
    },
    [agentIcons, checked, deploymentCountBySlug, openCopy, selecting, t],
  );

  /* -- Render -- */

  return (
    <div className="relative flex h-full flex-col">
      {/* Page header -- title left, count badge + add button right. */}
      <header className="flex shrink-0 items-center justify-between gap-4 h-15 px-5">
        <div className="flex min-w-0 flex-col justify-center">
          <span className="text-base font-semibold leading-5 text-ink-heading">
            {t("agent.title")}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-brand transition-colors hover:bg-brand-light/60 hover:text-brand"
            onClick={() => navigate("/marketplace?tab=agents&from=agents")}
          >
            <Store className="h-3.5 w-3.5" />
            {t("marketplace.title" as Parameters<typeof t>[0])}
          </button>
          <input
            ref={importInputRef}
            type="file"
            accept=".valuzpack,.zip"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              e.target.value = ""; // allow re-picking the same file
              if (f) {
                setImportFile(f);
                setImportOpen(true);
              }
            }}
          />
          {/* Export (bundle a selection) — its own action, not a "create" path. */}
          <button
            type="button"
            onClick={toggleSelecting}
            title={t("agent.pack.exportMulti" as Parameters<typeof t>[0])}
            aria-label={t("agent.pack.exportMulti" as Parameters<typeof t>[0])}
            className={`flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md transition-colors hover:bg-surface-soft hover:text-ink-body ${
              selecting ? "text-brand" : "text-ink-meta"
            }`}
          >
            <Upload className="h-3.5 w-3.5" />
          </button>
          {/* Add menu — every way to put an agent into the library lives here. */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={t("agent.newAgent" as Parameters<typeof t>[0])}
                className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-[180px]">
              <DropdownMenuItem onSelect={openCreate}>
                <Plus className="h-4 w-4" />
                {t("agent.createAgent" as Parameters<typeof t>[0])}
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => importInputRef.current?.click()}
              >
                <Download className="h-4 w-4" />
                {t("agent.pack.import" as Parameters<typeof t>[0])}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>

      {/* Content area */}
      {loading ? (
        <PageLoader logo />
      ) : (
        <div className="flex-1 overflow-y-auto py-4">
          <div className="mb-4 px-4">
            <SegmentedControl
              value={listView}
              onValueChange={(view) => {
                setListView(view);
                setActiveProjectMemberKey(null);
              }}
              options={[
                {
                  value: "all",
                  label: t(
                    "agent.viewAll" as Parameters<typeof t>[0],
                    "全部 Agent",
                  ),
                  icon: Bot,
                },
                {
                  value: "project",
                  label: t(
                    "agent.viewByProject" as Parameters<typeof t>[0],
                    "按项目",
                  ),
                  icon: Folder,
                },
              ]}
              className="mb-4 h-9"
            />

            {listView === "project" ? (
              visibleAgents.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-center">
                  <Bot className="mb-3 h-10 w-10 text-ink-muted" />
                  <div className="text-sm text-ink-body">
                    {t("agent.emptyTitle")}
                  </div>
                  <div className="mt-1 max-w-[460px] text-xs leading-5 text-ink-body">
                    {t("agent.emptyDesc")}
                  </div>
                  <Button
                    className="mt-4"
                    variant="default"
                    size="sm"
                    onClick={openCreate}
                  >
                    <Plus className="h-3 w-3" />
                    {t("agent.createAgent" as Parameters<typeof t>[0])}
                  </Button>
                </div>
              ) : (
                <div className="flex flex-col gap-6">
                  {projectGroups.map(({ project, members }) => {
                    const collapsed = collapsedProjectGroups.has(project.id);
                    return (
                      <section key={project.id}>
                        <button
                          type="button"
                          onClick={() => toggleProjectGroup(project.id)}
                          className="label-mono mb-3 flex w-full cursor-default items-center gap-1.5 text-left transition-colors hover:text-ink-body"
                        >
                          {collapsed ? (
                            <ChevronRight className="h-3 w-3" />
                          ) : (
                            <ChevronDown className="h-3 w-3" />
                          )}
                          <span className="min-w-0 flex-1 truncate">
                            {project.name}
                          </span>
                          <span className="ml-1 text-ink-meta">
                            {members.length}
                          </span>
                        </button>
                        {!collapsed && (
                          <div className="flex flex-col gap-3">
                            {members.map((member) => (
                              <div
                                key={member.key}
                                onClick={() => {
                                  if (selecting) {
                                    if (!isSystemAgent(member.agent)) {
                                      toggleChecked(member.agent.slug);
                                    }
                                    return;
                                  }
                                  setActiveSlug(member.agent.slug);
                                  setActiveProjectMemberKey(member.key);
                                }}
                                className="cursor-pointer"
                              >
                                {renderAgentRow(
                                  member.agent,
                                  effectiveProjectMemberKey === member.key,
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </section>
                    );
                  })}
                  {unassignedAgents.length > 0 && (
                    <section>
                      <button
                        type="button"
                        onClick={() => toggleProjectGroup("_unassigned")}
                        className="label-mono mb-3 flex w-full cursor-default items-center gap-1.5 text-left transition-colors hover:text-ink-body"
                      >
                        {collapsedProjectGroups.has("_unassigned") ? (
                          <ChevronRight className="h-3 w-3" />
                        ) : (
                          <ChevronDown className="h-3 w-3" />
                        )}
                        <span className="min-w-0 flex-1 truncate">
                          {t(
                            "agent.unassigned" as Parameters<typeof t>[0],
                            "未派驻",
                          )}
                        </span>
                        <span className="ml-1 text-ink-meta">
                          {unassignedAgents.length}
                        </span>
                      </button>
                      {!collapsedProjectGroups.has("_unassigned") && (
                        <div className="flex flex-col gap-3">
                          {unassignedAgents.map((agent) => (
                            <div
                              key={agent.slug}
                              onClick={() => {
                                if (isCloudOnlyAgent(agent)) return;
                                if (selecting) {
                                  if (!isSystemAgent(agent)) {
                                    toggleChecked(agent.slug);
                                  }
                                  return;
                                }
                                setActiveSlug(agent.slug);
                                setActiveProjectMemberKey(null);
                              }}
                              className={
                                isCloudOnlyAgent(agent)
                                  ? "cursor-default"
                                  : "cursor-pointer"
                              }
                            >
                              {renderAgentRow(
                                agent,
                                activeSlug === agent.slug &&
                                  activeProjectMemberKey === null,
                                { deploymentCount: 0 },
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </section>
                  )}
                </div>
              )
            ) : (
              <CategorizedList
                items={visibleAgents}
                categories={categories}
                selectedId={effectiveActiveSlug}
                getId={(a: Agent) => a.slug}
                onSelect={(a: Agent) => {
                  if (isCloudOnlyAgent(a)) return;
                  if (selecting) {
                    if (!isSystemAgent(a)) toggleChecked(a.slug);
                    return;
                  }
                  setActiveSlug(a.slug);
                  setActiveProjectMemberKey(null);
                }}
                renderItem={(agent: Agent, isSelected: boolean) =>
                  renderAgentRow(agent, isSelected)
                }
                emptyState={
                  <div className="flex flex-col items-center justify-center py-16 text-center">
                    <Bot className="mb-3 h-10 w-10 text-ink-muted" />
                    <div className="text-sm text-ink-body">
                      {t("agent.emptyTitle")}
                    </div>
                    <div className="mt-1 max-w-[460px] text-xs leading-5 text-ink-body">
                      {t("agent.emptyDesc")}
                    </div>
                    <Button
                      className="mt-4"
                      variant="default"
                      size="sm"
                      onClick={openCreate}
                    >
                      <Plus className="h-3 w-3" />
                      {t("agent.createAgent" as Parameters<typeof t>[0])}
                    </Button>
                  </div>
                }
              />
            )}

            {visibleAgents.length <= AGENT_MARKETPLACE_GUIDE_MAX_COUNT && (
              <button
                type="button"
                onClick={() => navigate("/marketplace?tab=agents&from=agents")}
                className="mt-5 flex w-full items-center gap-2 border-t border-surface-border px-1 pt-4 text-left text-xs font-medium text-ink-body transition-colors hover:text-brand"
              >
                <Store className="h-4 w-4 shrink-0 text-brand" />
                <span className="min-w-0 flex-1">
                  {t(
                    "marketplace.installAgentTeamFromMarketplace" as Parameters<
                      typeof t
                    >[0],
                  )}
                </span>
                <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-meta" />
              </button>
            )}
          </div>
        </div>
      )}

      {/* Create-agent wizard (08-agents-module §3) — shared with the
          conversation 🤖 「+ Agent」 entry (10-new-conversation-guidance). */}
      <CreateAgentDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        seed={createSeed}
        onCreated={async (slug) => {
          // Land on the new agent's detail (default = 工作方法/instructions tab).
          await loadData();
          setActiveSlug(slug);
        }}
      />

      {/* Import a user-supplied .valuzpack (upload → preview → confirm). */}
      <ImportPackDialog
        file={importFile}
        open={importOpen}
        onOpenChange={(o) => {
          setImportOpen(o);
          if (!o) setImportFile(null);
        }}
        onImported={loadData}
      />

      {/* Multi-select export: floating action bar while picking agents. */}
      {selecting && (
        <div className="pointer-events-none absolute inset-x-0 bottom-4 flex justify-center">
          <div className="pointer-events-auto flex items-center gap-3 rounded-full border border-surface-border bg-surface px-4 py-2 shadow-lg">
            <span className="text-xs text-ink-meta">
              {t("agent.pack.countAgents" as Parameters<typeof t>[0], {
                count: checked.size,
              })}
            </span>
            <Button
              size="sm"
              disabled={checked.size === 0}
              onClick={() => setExportOpen(true)}
            >
              <Upload className="h-3.5 w-3.5" />
              {t("agent.pack.export" as Parameters<typeof t>[0])}
            </Button>
            <button
              type="button"
              onClick={toggleSelecting}
              className="flex h-6 w-6 items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
              aria-label={t(
                "agent.pack.cancelSelect" as Parameters<typeof t>[0],
              )}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      )}

      <ExportPackDialog
        open={exportOpen}
        onOpenChange={setExportOpen}
        agentSlugs={[...checked]}
        onExported={toggleSelecting}
      />
    </div>
  );
};

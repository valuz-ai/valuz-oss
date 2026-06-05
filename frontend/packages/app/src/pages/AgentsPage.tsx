import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Bot, Copy, MoreHorizontal, Plus, type LucideIcon } from "lucide-react";
import {
  Button,
  CategorizedList,
  PageLoader,
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@valuz/ui";
import { ResourceActionSlot } from "../components/ResourceActionSlot";
import {
  agentsApi,
  usePanelStore,
  useTranslation,
  type Agent,
} from "@valuz/core";
import type { ResourceCategory } from "@valuz/shared";
import { useWorkspaceOutlet } from "@valuz/app/layout";
import { pickAgentIcon } from "../components/agent-icons";
import { AgentDetailView } from "../components/AgentDetailView";
import { CreateAgentDialog } from "../components/CreateAgentDialog";

/** Group agents into 自定义 (user-created, ``source !== "official"``) then
 * 官方 (built-in, ``source === "official"``). Mirrors the Skills /
 * Connectors category model — same ``groupCustom`` / ``groupOfficial``
 * labels — so ``CategorizedList`` renders identical collapsible group
 * headers. Custom is listed first — it's the user's own work. */
function buildAgentCategories(
  t: ReturnType<typeof useTranslation>["t"],
): ResourceCategory<Agent>[] {
  const byName = (a: Agent, b: Agent) => a.name.localeCompare(b.name);
  return [
    {
      id: "custom",
      label: t("agent.groupCustom" as Parameters<typeof t>[0]),
      order: 0,
      filter: (a: Agent) => a.source !== "official",
      sort: byName,
    },
    {
      id: "official",
      label: t("agent.groupOfficial" as Parameters<typeof t>[0]),
      order: 1,
      filter: (a: Agent) => a.source === "official",
      sort: byName,
    },
  ];
}

export const AgentsPage = () => {
  const { t } = useTranslation();
  const {
    setHeader,
    setHideHeader,
    setRightPanel,
    setAsideClassName,
    setMainClassName,
  } = useWorkspaceOutlet();
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeSlug, setActiveSlug] = useState<string | null>(null);

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

  /* -- Data loading -- */

  const mountedRef = useRef(true);
  const loadData = useCallback(async () => {
    try {
      const res = await agentsApi.listAgents();
      if (mountedRef.current) setAgents(res.agents);
    } catch {
      if (mountedRef.current) {
        toast.error(t("common.error"));
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

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

  const categories = useMemo(() => buildAgentCategories(t), [t]);

  // Keep the detail panel stable across tab switches: honour the explicit
  // selection if it still exists, otherwise fall back to the first agent in
  // the active tab (then any agent at all).
  const currentAgent =
    agents.find((a) => a.slug === activeSlug) ??
    visibleAgents[0] ??
    agents[0] ??
    null;
  const effectiveActiveSlug = currentAgent?.slug ?? null;

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

  /* -- Render -- */

  return (
    <div className="flex h-full flex-col">
      {/* Page header -- title left, count badge + add button right. */}
      <header className="flex h-12 shrink-0 items-center gap-2 px-5">
        <span className="shrink-0 whitespace-nowrap text-base font-semibold text-ink-heading">
          {t("agent.title")}
        </span>
        <div className="flex min-w-0 flex-1 items-center justify-end gap-1">
          <button
            type="button"
            aria-label={t("agent.newAgent" as Parameters<typeof t>[0])}
            onClick={openCreate}
            className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      {/* Content area */}
      {loading ? (
        <PageLoader logo />
      ) : (
        <div className="flex-1 overflow-y-auto py-4">
          <div className="mb-4 px-4">
            <CategorizedList
              items={visibleAgents}
              categories={categories}
              selectedId={effectiveActiveSlug}
              getId={(a: Agent) => a.slug}
              onSelect={(a: Agent) => setActiveSlug(a.slug)}
              renderItem={(agent: Agent, isSelected: boolean) => {
                const AgentIcon = agentIcons.get(agent.slug) ?? Bot;
                return (
                  <div
                    className={`group flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 transition-colors select-none ${
                      isSelected
                        ? "bg-surface-soft"
                        : "hover:bg-surface-soft/60"
                    }`}
                  >
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-body">
                      <AgentIcon className="h-3.5 w-3.5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-ink-heading">
                        {agent.name}
                      </div>
                      {agent.description && (
                        <div className="truncate text-xs text-ink-meta">
                          {agent.description}
                        </div>
                      )}
                    </div>
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
                    <ResourceActionSlot
                      resourceType="agent"
                      resource={agent as unknown as Record<string, unknown>}
                    />
                  </div>
                );
              }}
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
    </div>
  );
};

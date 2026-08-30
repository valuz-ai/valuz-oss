import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Bot,
  CloudOff,
  Plug,
  Puzzle,
  Search,
  Sparkles,
  Zap,
} from "lucide-react";
import { SegmentedControl, cn } from "@valuz/ui";
import type {
  MarketplaceItem,
  MarketplacePluginComposition,
} from "@valuz/core";
import { marketplaceApi, useTranslation } from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";
import { MarketplaceImportDialog } from "../components/MarketplaceImportDialog";
import { MarketplaceConnectorDialog } from "../components/MarketplaceConnectorDialog";
import { MarketplacePluginDialog } from "../components/plugins/MarketplacePluginDialog";
import {
  MarketplaceBadgePill,
  MarketplaceSourcePill,
  marketplaceIcon,
  tintFor,
} from "../components/marketplace-ui";
import { MarketplaceCategoryRail } from "../components/MarketplaceCategoryRail";
import { useMarketplaceCategoryFilter } from "../hooks/useMarketplaceCategoryFilter";

export type MarketTab = "agents" | "skills" | "plugins" | "connectors";

// Display order: agents → plugins → skills → connectors (plugins sit right
// after agents as the larger install unit; skills follow).
const MARKET_TABS: readonly MarketTab[] = [
  "agents",
  "plugins",
  "skills",
  "connectors",
];

const isMarketTab = (value: string | null): value is MarketTab =>
  value !== null && (MARKET_TABS as readonly string[]).includes(value);

/** Header sub-tabs (D7): agents 单智能体 → 团队; skills 技能 → 套件;
 * plugins 全部 → 技能套件 → 含连接器. */
type AgentsSubtab = "single" | "teams";
type SkillsSubtab = "skills" | "suites";
type PluginFilter = "all" | MarketplacePluginComposition;

const SKILL_PAGE_SIZE = 30;
const CONNECTOR_PAGE_SIZE = 20;
const PLUGIN_PAGE_SIZE = 30;

/**
 * Auto-load the next page when a bottom sentinel scrolls near the viewport
 * (pre-fetched via ``rootMargin``), replacing a manual "load more" button.
 * ``onLoadMore`` is read through a ref so a fresh page/closure never
 * re-subscribes the observer; re-observing on ``count`` re-fires while the
 * sentinel stays visible, so a short page keeps filling until ``hasMore`` is
 * false. The ``loading`` guard disconnects mid-fetch, so a page already in
 * flight is never double-requested. Mirrors the infinite scroll in
 * ``ActivityFeedList``.
 */
function useInfiniteScroll(
  onLoadMore: () => void,
  {
    hasMore,
    loading,
    count,
  }: { hasMore: boolean; loading: boolean; count: number },
) {
  const sentinelRef = useRef<HTMLDivElement>(null);
  const onLoadMoreRef = useRef(onLoadMore);
  onLoadMoreRef.current = onLoadMore;
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || !hasMore || loading) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          onLoadMoreRef.current();
        }
      },
      { rootMargin: "300px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [hasMore, loading, count]);
  return sentinelRef;
}

/** Full-screen marketplace — four tabs (Agents / Skills / Plugins /
 * Connectors) per the product prototype
 * (docs/plans/2026-07-07-skillhub-marketplace-product-prototype.md) and the
 * Agent Plugins design (docs/cloud-marketplace/design/agent-plugins-support.md).
 * All data comes from the market index (Valuz cloud) via the backend. */
export function MarketplacePage() {
  const { t } = useTranslation();
  const {
    setHideHeader,
    setHeader,
    setRightPanel,
    setAsideClassName,
    setMainClassName,
    setContentInnerClassName,
  } = useProjectOutlet();
  const tr = useCallback(
    (key: string, params?: Record<string, string | number>) =>
      t(key as Parameters<typeof t>[0], params),
    [t],
  );
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const tab: MarketTab = isMarketTab(requestedTab) ? requestedTab : "agents";
  const [queries, setQueries] = useState<Record<MarketTab, string>>({
    agents: "",
    skills: "",
    plugins: "",
    connectors: "",
  });
  const [debouncedQueries, setDebouncedQueries] = useState<
    Record<MarketTab, string>
  >({
    agents: "",
    skills: "",
    plugins: "",
    connectors: "",
  });
  const query = queries[tab];
  const debouncedQuery = debouncedQueries[tab];
  const setQuery = (value: string) => {
    setQueries((prev) => ({ ...prev, [tab]: value }));
  };
  const setTab = (next: MarketTab) => {
    const params = new URLSearchParams(searchParams);
    params.set("tab", next);
    setSearchParams(params, { replace: true });
  };

  // Each catalog owns its query so a Skill keyword never filters Agent Teams.
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQueries((prev) => ({ ...prev, [tab]: query.trim() }));
    }, 300);
    return () => clearTimeout(timer);
  }, [query, tab]);

  const [dialogItem, setDialogItem] = useState<MarketplaceItem | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [connectorItem, setConnectorItem] = useState<MarketplaceItem | null>(
    null,
  );
  const [connectorOpen, setConnectorOpen] = useState(false);
  const [pluginItem, setPluginItem] = useState<MarketplaceItem | null>(null);
  const [pluginOpen, setPluginOpen] = useState(false);
  // Search is collapsed to an icon by default; clicking expands an inline
  // input (mirrors the resource page's header search).
  const [searchOpen, setSearchOpen] = useState(false);
  const openItem = (item: MarketplaceItem) => {
    if (item.type === "connector") {
      setConnectorItem(item);
      setConnectorOpen(true);
      return;
    }
    if (item.type === "plugin") {
      setPluginItem(item);
      setPluginOpen(true);
      return;
    }
    setDialogItem(item);
    setDialogOpen(true);
  };

  useEffect(() => {
    setHideHeader(true);
    setHeader(null);
    setRightPanel(null);
    setAsideClassName(undefined);
    setMainClassName("flex-1 w-auto bg-card");
    setContentInnerClassName("p-0");
    return () => {
      setHideHeader(false);
      setHeader(null);
      setRightPanel(null);
      setAsideClassName(undefined);
      setMainClassName(undefined);
      setContentInnerClassName(undefined);
    };
  }, [
    setAsideClassName,
    setContentInnerClassName,
    setHeader,
    setHideHeader,
    setMainClassName,
    setRightPanel,
  ]);

  // Flip the card state in place after an install (no full reload).
  const [installedIds, setInstalledIds] = useState<Set<string>>(new Set());
  const markInstalled = (item: MarketplaceItem) =>
    setInstalledIds((prev) => new Set(prev).add(item.id));
  const withInstalled = useCallback(
    (items: MarketplaceItem[]) =>
      items.map((i) =>
        installedIds.has(i.id) ? { ...i, installed: true } : i,
      ),
    [installedIds],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header — same structure as the resource page: a title-only row
          above an underline-tab row that carries the search on its right. */}
      <div className="shrink-0">
        <div className="flex min-w-0 items-center h-15 px-5">
          <span className="text-base font-semibold leading-5 text-ink-heading">
            {tr("marketplace.title")}
          </span>
        </div>

        <div className="flex items-center gap-2 border-b border-surface-border px-5">
          <nav className="flex items-center" role="tablist">
            {MARKET_TABS.map((key) => {
              const active = tab === key;
              return (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setTab(key)}
                  className={cn(
                    "relative px-3.5 py-2.5 text-sm font-medium transition-colors",
                    active
                      ? "text-ink-heading after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:bg-brand"
                      : "text-ink-meta hover:text-ink-body",
                  )}
                >
                  {key === "agents"
                    ? tr("marketplace.tabAgents")
                    : key === "skills"
                      ? tr("marketplace.tabSkills")
                      : key === "plugins"
                        ? tr("marketplace.tabPlugins")
                        : tr("marketplace.tabConnectors")}
                </button>
              );
            })}
          </nav>

          <div className="flex min-w-0 flex-1 items-center justify-end gap-1">
            {searchOpen ? (
              <input
                type="text"
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onBlur={() => {
                  if (!query) setSearchOpen(false);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setQuery("");
                    setSearchOpen(false);
                  }
                }}
                placeholder={
                  tab === "agents"
                    ? tr("marketplace.searchAgents")
                    : tab === "skills"
                      ? tr("marketplace.searchSkills")
                      : tab === "plugins"
                        ? tr("marketplace.searchPlugins")
                        : tr("marketplace.searchConnectors")
                }
                className="h-7 w-full min-w-0 max-w-[200px] rounded-none border-0 border-b border-brand bg-transparent px-1 text-xs text-ink-heading placeholder:text-ink-meta outline-none"
              />
            ) : null}
            <button
              type="button"
              aria-label={tr("common.search")}
              onClick={() => setSearchOpen((o) => !o)}
              className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
            >
              <Search className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>

      {tab === "agents" ? (
        <AgentsTab
          q={debouncedQuery}
          tr={tr}
          onOpen={openItem}
          withInstalled={withInstalled}
        />
      ) : tab === "skills" ? (
        <SkillsTab
          q={debouncedQuery}
          tr={tr}
          onOpen={openItem}
          withInstalled={withInstalled}
        />
      ) : tab === "plugins" ? (
        <PluginsTab
          q={debouncedQuery}
          tr={tr}
          onOpen={openItem}
          withInstalled={withInstalled}
        />
      ) : (
        <ConnectorsTab
          q={debouncedQuery}
          tr={tr}
          onOpen={openItem}
          withInstalled={withInstalled}
        />
      )}

      <MarketplaceImportDialog
        item={dialogItem}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onInstalled={markInstalled}
      />
      <MarketplaceConnectorDialog
        item={connectorItem}
        open={connectorOpen}
        onOpenChange={setConnectorOpen}
        onConnected={markInstalled}
      />
      <MarketplacePluginDialog
        item={pluginItem}
        open={pluginOpen}
        onOpenChange={setPluginOpen}
        onInstalled={markInstalled}
      />
    </div>
  );
}

type Tr = (key: string, params?: Record<string, string | number>) => string;

interface TabProps {
  q: string;
  tr: Tr;
  onOpen: (item: MarketplaceItem) => void;
  withInstalled: (items: MarketplaceItem[]) => MarketplaceItem[];
}

function TemplateCard({
  item,
  tr,
  onOpen,
}: {
  item: MarketplaceItem;
  tr: Tr;
  onOpen: (item: MarketplaceItem) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(item)}
      className="flex min-h-[150px] w-full flex-col rounded-xl border border-surface-border bg-surface p-3.5 text-left transition hover:-translate-y-px hover:shadow-md"
    >
      <div className="mb-2 flex items-start gap-2.5">
        <ItemIcon item={item} size="md" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold tracking-tight text-ink-heading">
            {item.title}
          </div>
          <div className="mt-1 flex items-center gap-1.5">
            <MarketplaceSourcePill source={item.source} itemType={item.type} />
            {item.category_label ? (
              <span className="truncate text-2xs text-ink-meta">
                {item.category_label}
              </span>
            ) : null}
          </div>
        </div>
        {item.installed && (
          <span className="rounded bg-surface-soft px-1.5 py-0.5 text-micro font-medium text-ink-meta">
            {tr("marketplace.installed")}
          </span>
        )}
      </div>
      <div className="mb-2.5 line-clamp-2 min-h-9 text-xs leading-relaxed text-ink-body">
        {item.description}
      </div>
      {/* Footer mirrors TeamCard: divider + version | add/added. */}
      <div className="mt-auto flex items-center justify-between border-t border-surface-border pt-2.5">
        <span className="font-mono text-2xs tabular-nums text-ink-body">
          {item.version ? `v${item.version}` : ""}
        </span>
        <span
          className={cn(
            "text-xs font-medium",
            item.installed ? "text-ink-meta" : "text-brand",
          )}
        >
          {item.installed ? tr("marketplace.added") : tr("marketplace.add")}
        </span>
      </div>
    </button>
  );
}

/* ── shared bits ─────────────────────────────────────────────── */

/** Compact list-header switcher (D7) — the same SegmentedControl the
 * library pages use, sized to sit on the section-head row. */
function SubtabBar<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <SegmentedControl
      value={value}
      options={options}
      onValueChange={onChange}
      className="mb-3 h-8 w-fit"
      buttonClassName="px-3"
    />
  );
}

function SectionHead({
  icon,
  title,
  count,
}: {
  icon: React.ReactNode;
  title: string;
  count?: string;
}) {
  return (
    <div className="mb-3 flex items-center gap-2">
      {icon}
      <span className="text-sm font-semibold tracking-tight text-ink-heading">
        {title}
      </span>
      {count ? (
        <span className="text-xs tabular-nums text-ink-muted">· {count}</span>
      ) : null}
    </div>
  );
}

function DegradedNotice({ tr }: { tr: Tr }) {
  return (
    <div className="mb-4 flex items-center gap-2 rounded-lg border border-surface-border bg-surface-soft px-3 py-2 text-xs text-ink-body">
      <CloudOff className="h-3.5 w-3.5 flex-none text-ink-meta" />
      {tr("marketplace.degradedNotice")}
    </div>
  );
}

function ItemIcon({
  item,
  size,
}: {
  item: MarketplaceItem;
  size: "sm" | "md";
}) {
  const isImage = !!item.icon && /^https?:\/\//.test(item.icon);
  const Icon = marketplaceIcon(item.icon);
  const tint = tintFor(item.id);
  const cls =
    size === "md" ? "h-[38px] w-[38px] rounded-[9px]" : "h-9 w-9 rounded-[9px]";
  return (
    <div
      className={cn(
        "flex flex-none items-center justify-center overflow-hidden",
        cls,
      )}
      style={isImage ? undefined : { background: tint.bg, color: tint.fg }}
    >
      {isImage ? (
        <img
          src={item.icon ?? undefined}
          alt=""
          className="h-full w-full object-cover"
        />
      ) : (
        <Icon
          className={size === "md" ? "h-[19px] w-[19px]" : "h-[18px] w-[18px]"}
        />
      )}
    </div>
  );
}

/* ── Agents tab ──────────────────────────────────────────────── */

function AgentsTab({ q, tr, onOpen, withInstalled }: TabProps) {
  const [subtab, setSubtab] = useState<AgentsSubtab>("single");
  const [teams, setTeams] = useState<MarketplaceItem[]>([]);
  const [templates, setTemplates] = useState<MarketplaceItem[]>([]);
  const categoryFilter = useMarketplaceCategoryFilter("agent");
  const { category, subcategory } = categoryFilter;

  useEffect(() => {
    let cancelled = false;
    const params = {
      category: category ?? undefined,
      subcategory: subcategory ?? undefined,
      q: q || undefined,
    };
    marketplaceApi
      .list({ type: "agent_team_template", ...params })
      .then((res) => {
        if (!cancelled) setTeams(res.items);
      })
      .catch(() => {
        if (!cancelled) setTeams([]);
      });
    marketplaceApi
      .list({ type: "agent_template", ...params })
      .then((res) => {
        if (!cancelled) setTemplates(res.items);
      })
      .catch(() => {
        if (!cancelled) setTemplates([]);
      });
    return () => {
      cancelled = true;
    };
  }, [q, category, subcategory]);

  const teamItems = withInstalled(teams);
  const templateItems = withInstalled(templates);

  return (
    <div className="flex min-h-0 flex-1">
      <MarketplaceCategoryRail
        heading={tr("marketplace.categories")}
        allLabel={tr("marketplace.filterAll")}
        categories={categoryFilter.categories}
        category={category}
        subcategory={subcategory}
        onCategoryChange={categoryFilter.selectCategory}
        onSubcategoryChange={categoryFilter.selectSubcategory}
      />

      {/* content — one list at a time, switched by the header sub-tabs
          (D7: 单智能体 first, 团队 second) instead of the old stacked
          sections where the lower one was easy to miss. */}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-4">
        <SubtabBar<AgentsSubtab>
          value={subtab}
          onChange={setSubtab}
          options={[
            { value: "single", label: tr("marketplace.agentsSubtabSingle") },
            { value: "teams", label: tr("marketplace.agentsSubtabTeams") },
          ]}
        />
        {subtab === "teams" ? (
          <section className="mb-7">
            <SectionHead
              icon={<Sparkles className="h-[15px] w-[15px] text-brand" />}
              title={tr("marketplace.teamsTitle")}
              count={tr("marketplace.teamsCount", { count: teamItems.length })}
            />
            <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
              {teamItems.map((team) => (
                <TeamCard key={team.id} team={team} tr={tr} onOpen={onOpen} />
              ))}
            </div>
          </section>
        ) : (
          <section className="mb-7">
            <SectionHead
              icon={<Bot className="h-[15px] w-[15px] text-brand" />}
              title={tr("marketplace.templatesTitle")}
              count={tr("marketplace.templatesCount", {
                count: templateItems.length,
              })}
            />
            <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
              {templateItems.map((item) => (
                <TemplateCard
                  key={item.id}
                  item={item}
                  tr={tr}
                  onOpen={onOpen}
                />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function TeamCard({
  team,
  tr,
  onOpen,
}: {
  team: MarketplaceItem;
  tr: Tr;
  onOpen: (item: MarketplaceItem) => void;
}) {
  const members = team.members ?? [];
  return (
    <button
      type="button"
      onClick={() => onOpen(team)}
      className="flex min-h-[154px] w-full flex-col rounded-xl border border-surface-border bg-surface p-3.5 text-left transition hover:-translate-y-px hover:shadow-md"
    >
      <div className="mb-2 flex items-start gap-2.5">
        <ItemIcon item={team} size="md" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold tracking-tight text-ink-heading">
            {team.title}
          </div>
          <div className="mt-1 flex items-center gap-1.5">
            <MarketplaceSourcePill source={team.source} itemType={team.type} />
            {team.category_label ? (
              <span className="truncate text-2xs text-ink-meta">
                {team.category_label}
              </span>
            ) : null}
          </div>
        </div>
        {team.installed && (
          <span className="rounded bg-surface-soft px-1.5 py-0.5 text-micro font-medium text-ink-meta">
            {tr("marketplace.installed")}
          </span>
        )}
      </div>
      <div className="mb-2 flex items-center">
        {members.slice(0, 4).map((m) => {
          const tint = tintFor(m.name);
          return (
            <div
              key={m.name}
              className="-ml-1.5 flex h-6 w-6 items-center justify-center rounded-full border-2 border-surface text-micro font-semibold first:ml-0"
              style={{ background: tint.bg, color: tint.fg }}
            >
              {m.name.slice(0, 1)}
            </div>
          );
        })}
        <span className="ml-2 text-[11.5px] text-ink-body">
          {tr("marketplace.membersAndSkills", {
            members: members.length,
            skills: team.skill_count ?? 0,
          })}
        </span>
      </div>
      <div className="mb-2.5 line-clamp-1 min-h-[18px] text-xs leading-relaxed text-ink-body">
        {team.description}
      </div>
      <div className="mt-auto flex items-center justify-between border-t border-surface-border pt-2.5">
        <span className="font-mono text-2xs tabular-nums text-ink-body">
          {team.version ? `v${team.version}` : ""}
        </span>
        <span
          className={cn(
            "text-xs font-medium",
            team.installed ? "text-ink-meta" : "text-brand",
          )}
        >
          {team.installed ? tr("marketplace.added") : tr("marketplace.add")}
        </span>
      </div>
    </button>
  );
}

/* ── Skills tab ──────────────────────────────────────────────── */

function SkillsTab({ q, tr, onOpen, withInstalled }: TabProps) {
  // 技能 | 套件 (D2/D7): 套件 = ``type=plugin&composition=skills_only`` — the
  // same rows the Plugins tab shows under 技能套件, surfaced here as well.
  const [subtab, setSubtab] = useState<SkillsSubtab>("skills");
  const categoryFilter = useMarketplaceCategoryFilter(
    subtab === "suites" ? "plugin" : "skill",
  );
  const { category, subcategory } = categoryFilter;
  const [items, setItems] = useState<MarketplaceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [degraded, setDegraded] = useState(false);
  const [loading, setLoading] = useState(true);
  const requestSeq = useRef(0);

  const switchSubtab = (next: SkillsSubtab) => {
    if (next === subtab) return;
    setSubtab(next);
    // Category keys differ between the skill and plugin catalogs.
    categoryFilter.reset();
  };

  const load = useCallback(
    (nextPage: number, append: boolean) => {
      const seq = (requestSeq.current += 1);
      setLoading(true);
      marketplaceApi
        .list({
          ...(subtab === "suites"
            ? { type: "plugin" as const, composition: "skills_only" as const }
            : { type: "skill" as const }),
          category: category ?? undefined,
          subcategory: subcategory ?? undefined,
          q: q || undefined,
          page: nextPage,
          page_size: SKILL_PAGE_SIZE,
        })
        .then((res) => {
          if (seq !== requestSeq.current) return;
          setItems((prev) => (append ? [...prev, ...res.items] : res.items));
          setTotal(res.total);
          setDegraded(res.degraded);
          setPage(nextPage);
        })
        .catch(() => {
          if (seq !== requestSeq.current) return;
          if (!append) {
            setItems([]);
            setTotal(0);
          }
        })
        .finally(() => {
          if (seq === requestSeq.current) setLoading(false);
        });
    },
    [category, q, subcategory, subtab],
  );

  useEffect(() => {
    load(1, false);
  }, [load]);

  const visible = withInstalled(items);
  const hasMore =
    !degraded && items.length < total && items.length >= SKILL_PAGE_SIZE;
  const sentinelRef = useInfiniteScroll(() => load(page + 1, true), {
    hasMore,
    loading,
    count: items.length,
  });

  return (
    <div className="flex min-h-0 flex-1">
      <MarketplaceCategoryRail
        heading={tr("marketplace.categories")}
        allLabel={tr("marketplace.filterAll")}
        categories={categoryFilter.categories}
        category={category}
        subcategory={subcategory}
        onCategoryChange={categoryFilter.selectCategory}
        onSubcategoryChange={categoryFilter.selectSubcategory}
      />

      {/* content */}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-4">
        {degraded && <DegradedNotice tr={tr} />}
        <SubtabBar<SkillsSubtab>
          value={subtab}
          onChange={switchSubtab}
          options={[
            { value: "skills", label: tr("marketplace.skillsSubtabSkills") },
            { value: "suites", label: tr("marketplace.skillsSubtabSuites") },
          ]}
        />
        <SectionHead
          icon={
            subtab === "suites" ? (
              <Puzzle className="h-[15px] w-[15px] text-brand" />
            ) : (
              <Zap className="h-[15px] w-[15px] text-brand" />
            )
          }
          title={
            q
              ? tr("marketplace.searchResultsTitle")
              : subtab === "suites"
                ? tr("marketplace.suitesShelfTitle")
                : tr("marketplace.skillsShelfTitle")
          }
          count={tr("marketplace.countTotal", { count: total })}
        />
        {
          <>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
              {visible.map((entry) =>
                entry.type === "plugin" ? (
                  <PluginMarketCard
                    key={entry.id}
                    plugin={entry}
                    tr={tr}
                    onOpen={onOpen}
                    suite
                  />
                ) : (
                  <SkillMarketCard
                    key={entry.id}
                    skill={entry}
                    tr={tr}
                    onOpen={onOpen}
                  />
                ),
              )}
            </div>
            {hasMore && <div ref={sentinelRef} aria-hidden className="h-px" />}
            {loading && items.length > 0 && (
              <div className="mt-5 flex justify-center text-sm text-ink-meta">
                {tr("marketplace.loading")}
              </div>
            )}
          </>
        }
      </div>
    </div>
  );
}

/* ── Plugins tab ─────────────────────────────────────────────── */

const PLUGIN_FILTERS: readonly PluginFilter[] = [
  "all",
  "skills_only",
  "with_connectors",
];

function PluginsTab({ q, tr, onOpen, withInstalled }: TabProps) {
  // 全部 | 技能套件 | 含连接器 (D3/D7) — a derived-composition filter over the
  // one ``plugin`` item type; 全部 is the default.
  const [filter, setFilter] = useState<PluginFilter>("all");
  const categoryFilter = useMarketplaceCategoryFilter("plugin");
  const { category, subcategory } = categoryFilter;
  const [items, setItems] = useState<MarketplaceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [degraded, setDegraded] = useState(false);
  const [loading, setLoading] = useState(true);
  const requestSeq = useRef(0);

  const load = useCallback(
    (nextPage: number, append: boolean) => {
      const seq = (requestSeq.current += 1);
      setLoading(true);
      marketplaceApi
        .list({
          type: "plugin",
          composition: filter === "all" ? undefined : filter,
          category: category ?? undefined,
          subcategory: subcategory ?? undefined,
          q: q || undefined,
          page: nextPage,
          page_size: PLUGIN_PAGE_SIZE,
        })
        .then((res) => {
          if (seq !== requestSeq.current) return;
          setItems((prev) => (append ? [...prev, ...res.items] : res.items));
          setTotal(res.total);
          setDegraded(res.degraded);
          setPage(nextPage);
        })
        .catch(() => {
          if (seq !== requestSeq.current) return;
          if (!append) {
            setItems([]);
            setTotal(0);
          }
        })
        .finally(() => {
          if (seq === requestSeq.current) setLoading(false);
        });
    },
    [category, filter, q, subcategory],
  );

  useEffect(() => {
    load(1, false);
  }, [load]);

  const visible = withInstalled(items);
  const hasMore =
    !degraded && items.length < total && items.length >= PLUGIN_PAGE_SIZE;
  const sentinelRef = useInfiniteScroll(() => load(page + 1, true), {
    hasMore,
    loading,
    count: items.length,
  });

  const filterLabel = (key: PluginFilter) =>
    key === "all"
      ? tr("marketplace.pluginFilterAll")
      : key === "skills_only"
        ? tr("marketplace.pluginFilterSkillSuites")
        : tr("marketplace.pluginFilterWithConnectors");

  return (
    <div className="flex min-h-0 flex-1">
      <MarketplaceCategoryRail
        heading={tr("marketplace.categories")}
        allLabel={tr("marketplace.filterAll")}
        categories={categoryFilter.categories}
        category={category}
        subcategory={subcategory}
        onCategoryChange={categoryFilter.selectCategory}
        onSubcategoryChange={categoryFilter.selectSubcategory}
      />

      {/* content */}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-4">
        {degraded && <DegradedNotice tr={tr} />}
        <SubtabBar<PluginFilter>
          value={filter}
          onChange={setFilter}
          options={PLUGIN_FILTERS.map((key) => ({
            value: key,
            label: filterLabel(key),
          }))}
        />
        <SectionHead
          icon={<Puzzle className="h-[15px] w-[15px] text-brand" />}
          title={
            q
              ? tr("marketplace.searchResultsTitle")
              : tr("marketplace.pluginsShelfTitle")
          }
          count={tr("marketplace.countTotal", { count: total })}
        />
        <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
          {visible.map((plugin) => (
            <PluginMarketCard
              key={plugin.id}
              plugin={plugin}
              tr={tr}
              onOpen={onOpen}
            />
          ))}
        </div>
        {hasMore ? (
          <div ref={sentinelRef} aria-hidden className="h-px" />
        ) : null}
        {loading && items.length > 0 ? (
          <div className="mt-5 flex justify-center text-sm text-ink-meta">
            {tr("marketplace.loading")}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function PluginMarketCard({
  plugin,
  tr,
  onOpen,
  suite = false,
}: {
  plugin: MarketplaceItem;
  tr: Tr;
  onOpen: (item: MarketplaceItem) => void;
  /** Skills-tab 套件 view: the row shows only the skill count (§6.2). */
  suite?: boolean;
}) {
  const skills = plugin.skill_count ?? 0;
  const connectors = plugin.connector_count ?? 0;
  const composition =
    plugin.composition ?? (connectors > 0 ? "with_connectors" : "skills_only");
  return (
    <button
      type="button"
      onClick={() => onOpen(plugin)}
      data-testid="plugin-market-card"
      className="flex min-h-[150px] w-full flex-col rounded-xl border border-surface-border bg-surface p-3.5 text-left transition hover:-translate-y-px hover:shadow-md"
    >
      <div className="mb-2 flex items-start gap-2.5">
        <ItemIcon item={plugin} size="md" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold tracking-tight text-ink-heading">
            {plugin.title}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <MarketplaceSourcePill source={plugin.source} itemType={plugin.type} />
            <span className="truncate text-2xs text-ink-meta">
              {composition === "with_connectors"
                ? tr("marketplace.compositionWithConnectors")
                : tr("marketplace.compositionSkillsOnly")}
            </span>
            {plugin.category_label ? (
              <span className="truncate text-2xs text-ink-meta">
                {plugin.category_label}
              </span>
            ) : null}
          </div>
        </div>
        {plugin.installed ? (
          <span className="rounded bg-surface-soft px-1.5 py-0.5 text-micro font-medium text-ink-meta">
            {tr("marketplace.installed")}
          </span>
        ) : null}
      </div>
      <div className="mb-2 text-xs text-ink-body">
        {suite
          ? tr("marketplace.pluginSkillCount", { count: skills })
          : tr("marketplace.pluginMembers", { skills, connectors })}
      </div>
      <div className="mb-2.5 line-clamp-2 min-h-9 text-xs leading-relaxed text-ink-body">
        {plugin.description}
      </div>
      <div className="mt-auto flex items-center justify-between border-t border-surface-border pt-2.5">
        <span className="font-mono text-2xs tabular-nums text-ink-body">
          {plugin.version ? `v${plugin.version}` : ""}
        </span>
        <span
          className={cn(
            "text-xs font-medium",
            plugin.installed ? "text-ink-meta" : "text-brand",
          )}
        >
          {plugin.installed ? tr("marketplace.added") : tr("marketplace.add")}
        </span>
      </div>
    </button>
  );
}

/* ── Connectors tab ──────────────────────────────────────────── */

function ConnectorsTab({ q, tr, onOpen, withInstalled }: TabProps) {
  const categoryFilter = useMarketplaceCategoryFilter("connector");
  const { category, subcategory } = categoryFilter;
  const [items, setItems] = useState<MarketplaceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [degraded, setDegraded] = useState(false);
  const [loading, setLoading] = useState(true);
  const requestSeq = useRef(0);

  const load = useCallback(
    (nextPage: number, append: boolean) => {
      const seq = (requestSeq.current += 1);
      setLoading(true);
      marketplaceApi
        .list({
          // No source filter: the index channel composes connectors from all
          // origins (crawled + manually curated), not just ModelScope.
          type: "connector",
          category: category ?? undefined,
          subcategory: subcategory ?? undefined,
          q: q || undefined,
          page: nextPage,
          page_size: CONNECTOR_PAGE_SIZE,
        })
        .then((res) => {
          if (seq !== requestSeq.current) return;
          setItems((prev) => (append ? [...prev, ...res.items] : res.items));
          setTotal(res.total);
          setPage(nextPage);
          setDegraded(res.degraded);
        })
        .catch(() => {
          if (seq !== requestSeq.current) return;
          if (!append) {
            setItems([]);
            setTotal(0);
          }
          setDegraded(true);
        })
        .finally(() => {
          if (seq === requestSeq.current) setLoading(false);
        });
    },
    [category, q, subcategory],
  );

  useEffect(() => {
    load(1, false);
  }, [load]);

  const visible = withInstalled(items);
  const hasMore =
    !degraded && items.length < total && page * CONNECTOR_PAGE_SIZE < 100;
  const sentinelRef = useInfiniteScroll(() => load(page + 1, true), {
    hasMore,
    loading,
    count: items.length,
  });
  return (
    <div className="flex min-h-0 flex-1">
      <MarketplaceCategoryRail
        heading={tr("marketplace.categories")}
        allLabel={tr("marketplace.filterAll")}
        categories={categoryFilter.categories}
        category={category}
        subcategory={subcategory}
        onCategoryChange={categoryFilter.selectCategory}
        onSubcategoryChange={categoryFilter.selectSubcategory}
      />

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-4">
        {degraded && <DegradedNotice tr={tr} />}
        <SectionHead
          icon={<Plug className="h-[15px] w-[15px] text-brand" />}
          title={
            q
              ? tr("marketplace.searchResultsTitle")
              : tr("marketplace.connectorsShelfTitle")
          }
          count={tr("marketplace.countShown", { count: visible.length })}
        />
        {
          <>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
              {visible.map((connector) => (
                <ConnectorMarketCard
                  key={connector.id}
                  connector={connector}
                  tr={tr}
                  onOpen={onOpen}
                />
              ))}
            </div>
            {hasMore ? (
              <div ref={sentinelRef} aria-hidden className="h-px" />
            ) : null}
            {loading && items.length > 0 ? (
              <div className="mt-5 flex justify-center text-sm text-ink-meta">
                {tr("marketplace.loading")}
              </div>
            ) : null}
          </>
        }
      </div>
    </div>
  );
}

function ConnectorMarketCard({
  connector,
  tr,
  onOpen,
}: {
  connector: MarketplaceItem;
  tr: Tr;
  onOpen: (item: MarketplaceItem) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(connector)}
      className="flex min-h-[150px] w-full flex-col rounded-xl border border-surface-border bg-surface p-3.5 text-left transition hover:-translate-y-px hover:shadow-md"
    >
      <div className="mb-2 flex items-start gap-2.5">
        <ItemIcon item={connector} size="md" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold tracking-tight text-ink-heading">
            {connector.title}
          </div>
          <div className="mt-1 flex items-center gap-1.5">
            <MarketplaceSourcePill source={connector.source} itemType={connector.type} />
            {connector.category_label ? (
              <span className="truncate text-2xs text-ink-meta">
                {connector.category_label}
              </span>
            ) : null}
          </div>
        </div>
        {connector.installed ? (
          <span className="rounded bg-surface-soft px-1.5 py-0.5 text-micro font-medium text-ink-meta">
            {tr("marketplace.connected")}
          </span>
        ) : null}
      </div>
      <div className="mb-2.5 line-clamp-2 min-h-9 text-xs leading-relaxed text-ink-body">
        {connector.description || tr("marketplace.connectorNoDescription")}
      </div>
      <div className="mt-auto flex items-center justify-between border-t border-surface-border pt-2.5">
        <span className="font-mono text-2xs tabular-nums text-ink-body">
          {connector.version ? `v${connector.version}` : ""}
        </span>
        <span
          className={cn(
            "text-xs font-medium",
            connector.installed ? "text-ink-meta" : "text-brand",
          )}
        >
          {connector.installed
            ? tr("marketplace.added")
            : tr("marketplace.add")}
        </span>
      </div>
    </button>
  );
}

function SkillMarketCard({
  skill,
  tr,
  onOpen,
}: {
  skill: MarketplaceItem;
  tr: Tr;
  onOpen: (item: MarketplaceItem) => void;
}) {
  const setupBadges = skill.badges.filter((badge) =>
    ["requires_api_key", "third_party_cost", "locked"].includes(badge),
  );
  return (
    <button
      type="button"
      onClick={() => onOpen(skill)}
      className="flex min-h-[150px] w-full flex-col rounded-xl border border-surface-border bg-surface p-3.5 text-left transition hover:-translate-y-px hover:shadow-md"
    >
      <div className="mb-2 flex items-start gap-2.5">
        <ItemIcon item={skill} size="sm" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold tracking-tight text-ink-heading">
            {skill.title}
          </div>
          <div className="mt-1 flex items-center gap-1.5">
            <MarketplaceSourcePill source={skill.source} itemType={skill.type} />
            {skill.category_label ? (
              <span className="truncate text-2xs text-ink-meta">
                {skill.category_label}
              </span>
            ) : null}
            {setupBadges.map((badge) => (
              <MarketplaceBadgePill key={badge} badge={badge} />
            ))}
          </div>
        </div>
      </div>
      <div className="mb-2.5 line-clamp-2 min-h-9 text-xs leading-relaxed text-ink-body">
        {skill.description}
      </div>
      <div className="mt-auto flex items-center justify-between border-t border-surface-border pt-2.5">
        <span className="font-mono text-2xs tabular-nums text-ink-body">
          {skill.version ? `v${skill.version}` : ""}
        </span>
        <span
          className={cn(
            "text-xs font-medium",
            skill.installed ? "text-ink-meta" : "text-brand",
          )}
        >
          {skill.installed ? tr("marketplace.added") : tr("marketplace.add")}
        </span>
      </div>
    </button>
  );
}

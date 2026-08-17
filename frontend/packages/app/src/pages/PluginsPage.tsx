import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  FileText,
  Link2,
  Package,
  Plus,
  Puzzle,
  Search,
  Sparkles,
  Store,
  Upload,
} from "lucide-react";
import {
  Badge,
  Button,
  DeleteConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  EmptyState,
  PageLoader,
  Switch,
  cn,
} from "@valuz/ui";
import type {
  AgentPluginMemberRef,
  AgentPluginOnConflict,
  AgentPluginUninstallResult,
  AgentPluginView,
} from "@valuz/core";
import { ApiError, pluginsApi, useTranslation } from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";
import { PluginConflictDialog } from "../components/plugins/PluginConflictDialog";
import { PluginDetailPanel } from "../components/plugins/PluginDetailPanel";
import { PluginInstallDialog } from "../components/plugins/PluginInstallDialog";
import { SkillsPane, type SkillAddMode } from "./SkillsPane";
import { ConnectorsPane, type ConnectorAddModeOrNull } from "./ConnectorsPane";
import type { PluginMemberRow } from "../components/plugins/PluginMembersList";
import { PLUGIN_COMPOSITION_LABEL_KEYS } from "../components/plugins/plugin-format";
import { downloadBlob } from "../components/pack-filename";
import { tintFor } from "../components/marketplace-ui";

type Busy = "update" | "uninstall" | "export" | "toggle" | null;

/** The three resource kinds this page hosts. Plugin is wired; skill and
 *  connector are folded in over the next steps (their libraries stay put). */
type ResourceType = "plugin" | "skill" | "connector";

const RESOURCE_TABS: { id: ResourceType; labelKey: string }[] = [
  { id: "plugin", labelKey: "resource.tabPlugin" },
  { id: "skill", labelKey: "resource.tabSkill" },
  { id: "connector", labelKey: "resource.tabConnector" },
];

/**
 * Resource center — one self-contained page (no global right panel, no
 * inverted 345/aside split): a full-width header switches between plugin /
 * skill / connector, and the list + detail below switch with it. Mirrors the
 * Knowledge Base's "wide main owns its own layout" shape. Step 1 wires the
 * plugin kind; skill and connector are folded into the same shell next.
 */
export const PluginsPage = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const {
    setHeader,
    setHideHeader,
    setRightPanel,
    setAsideClassName,
    setMainClassName,
  } = useProjectOutlet();
  const [searchParams] = useSearchParams();

  const [resourceType, setResourceType] = useState<ResourceType>("plugin");
  const [plugins, setPlugins] = useState<AgentPluginView[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(() =>
    searchParams.get("plugin"),
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [installOpen, setInstallOpen] = useState(false);
  // Add affordances for the skill / connector panes, driven from the
  // shared header dropdowns (null = closed).
  const [skillAddMode, setSkillAddMode] = useState<SkillAddMode>(null);
  const [connectorAddMode, setConnectorAddMode] =
    useState<ConnectorAddModeOrNull>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [uninstallTarget, setUninstallTarget] =
    useState<AgentPluginView | null>(null);
  const [uninstallSummary, setUninstallSummary] = useState<
    (AgentPluginUninstallResult & { name: string }) | null
  >(null);
  const [updateConflicts, setUpdateConflicts] = useState<
    AgentPluginMemberRef[]
  >([]);
  const [updateConflictTarget, setUpdateConflictTarget] =
    useState<AgentPluginView | null>(null);

  /* ── Data loading ──────────────────────────────────────────── */

  const mountedRef = useRef(true);
  const loadPlugins = useCallback(async () => {
    try {
      const res = await pluginsApi.list();
      if (mountedRef.current) setPlugins(res.items);
    } catch (err) {
      if (mountedRef.current) {
        console.error("[Plugins] load error", err);
        setPlugins([]);
        toast.error(t("plugin.loadFailed"));
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    mountedRef.current = true;
    void loadPlugins();
    return () => {
      mountedRef.current = false;
    };
  }, [loadPlugins]);

  // Self-contained: hide the layout header and keep the default full-width
  // main. Crucially we never call ``setRightPanel`` — the layout only renders
  // the shared aside when a right panel is set, so the detail lives inside this
  // page's own column instead of the global right panel. Reset on unmount.
  useEffect(() => {
    setHideHeader(true);
    setMainClassName(undefined);
    setAsideClassName(undefined);
    setRightPanel(null);
    return () => {
      setHideHeader(false);
      setHeader(null);
      setMainClassName(undefined);
      setAsideClassName(undefined);
      setRightPanel(null);
    };
  }, [
    setHideHeader,
    setHeader,
    setMainClassName,
    setAsideClassName,
    setRightPanel,
  ]);

  /* ── Derived state ─────────────────────────────────────────── */

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return plugins;
    return plugins.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.description ?? "").toLowerCase().includes(q) ||
        p.members.some(
          (m) => m.name.toLowerCase().includes(q) || m.slug.includes(q),
        ),
    );
  }, [plugins, searchQuery]);

  const current =
    filtered.find((p) => p.id === activeId || p.name === activeId) ??
    filtered[0] ??
    null;

  const replacePlugin = (next: AgentPluginView) =>
    setPlugins((prev) => prev.map((p) => (p.id === next.id ? next : p)));

  const showError = (err: unknown, fallbackKey: string) => {
    if (err instanceof ApiError && err.i18nKey) {
      toast.error(
        t(err.i18nKey as Parameters<typeof t>[0], err.i18nParams as never),
      );
    } else if (err instanceof ApiError && err.message) {
      toast.error(err.message);
    } else {
      toast.error(t(fallbackKey as Parameters<typeof t>[0]));
    }
  };

  /* ── Handlers ──────────────────────────────────────────────── */

  // Optimistic plugin-level toggle; reverts on failure.
  const handleToggle = async (plugin: AgentPluginView, enabled: boolean) => {
    replacePlugin({ ...plugin, enabled });
    setBusy("toggle");
    try {
      const view = enabled
        ? await pluginsApi.enable(plugin.id)
        : await pluginsApi.disable(plugin.id);
      replacePlugin(view);
    } catch (err) {
      replacePlugin(plugin);
      showError(err, "plugin.toggleFailed");
    } finally {
      setBusy(null);
    }
  };

  const runUpdate = async (
    plugin: AgentPluginView,
    onConflict?: AgentPluginOnConflict,
  ) => {
    setBusy("update");
    try {
      const result = await pluginsApi.update(plugin.id, onConflict);
      replacePlugin(result.plugin);
      if (result.status === "already_installed") {
        toast.info(t("plugin.alreadyInstalled", { name: plugin.name }));
      } else {
        toast.success(t("plugin.updated", { name: result.plugin.name }));
      }
      await loadPlugins();
    } catch (err) {
      showError(err, "plugin.updateFailed");
    } finally {
      setBusy(null);
    }
  };

  // Preview the source first so same-slug conflicts get the skip/overwrite
  // prompt instead of the silent default.
  const handleUpdate = async (plugin: AgentPluginView) => {
    if (!plugin.source_ref) {
      await runUpdate(plugin);
      return;
    }
    setBusy("update");
    try {
      const preview = await pluginsApi.preview(
        plugin.source === "market"
          ? { market_item_id: plugin.source_ref }
          : plugin.source === "url"
            ? { url: plugin.source_ref }
            : { path: plugin.source_ref },
      );
      if (preview.conflicts.length > 0) {
        setUpdateConflicts(preview.conflicts);
        setUpdateConflictTarget(plugin);
        setBusy(null);
        return;
      }
    } catch (err) {
      if (err instanceof ApiError && err.status !== 404) {
        showError(err, "plugin.updateFailed");
        setBusy(null);
        return;
      }
    }
    await runUpdate(plugin);
  };

  const handleUninstall = async () => {
    const target = uninstallTarget;
    if (!target) return;
    setBusy("uninstall");
    try {
      const result = await pluginsApi.uninstall(target.id);
      setUninstallTarget(null);
      setPlugins((prev) => prev.filter((p) => p.id !== target.id));
      if (activeId === target.id) setActiveId(null);
      toast.success(t("plugin.uninstalled", { name: target.name }));
      setUninstallSummary({ ...result, name: target.name });
    } catch (err) {
      showError(err, "plugin.uninstallFailed");
    } finally {
      setBusy(null);
    }
  };

  const handleExport = async (plugin: AgentPluginView) => {
    setBusy("export");
    try {
      const { blob, filename } = await pluginsApi.export(
        plugin.id,
        plugin.name,
      );
      downloadBlob(blob, filename);
    } catch (err) {
      showError(err, "plugin.exportFailed");
    } finally {
      setBusy(null);
    }
  };

  const openMember = (member: PluginMemberRow) => {
    if (member.kind === "skill") {
      navigate(`/skills?skill=${encodeURIComponent(member.slug)}`);
    } else {
      navigate(`/connectors?connector=${encodeURIComponent(member.slug)}`);
    }
  };

  /* ── Render ────────────────────────────────────────────────── */

  const isPlugin = resourceType === "plugin";
  const marketHref =
    resourceType === "skill"
      ? "/marketplace?tab=skills&from=skills"
      : resourceType === "connector"
        ? "/marketplace?tab=connectors&from=connectors"
        : "/marketplace?tab=plugins&from=plugins";
  const searchPlaceholder =
    resourceType === "skill"
      ? t("skill.searchPlaceholder")
      : resourceType === "connector"
        ? t("connector.searchPlaceholder")
        : t("plugin.searchPlaceholder");

  // Switching types resets the shared search so a stale query never
  // filters the newly-shown list.
  const switchType = (next: ResourceType) => {
    setResourceType(next);
    setSearchQuery("");
    setSearchOpen(false);
    setSkillAddMode(null);
    setConnectorAddMode(null);
  };

  return (
    <div className="flex h-full flex-col">
      {/* Full-width header: type switch + search + install/market (plugin). */}
      <div className="shrink-0">
        {/* Title row — title + description, no divider under it (mirrors
            the Knowledge Base header). */}
        <div className="flex min-w-0 items-center h-15 px-5">
          <span className="text-base font-semibold leading-5 text-ink-heading">
            {t("plugin.title")}
          </span>
        </div>

        {/* Tab row: underline tabs (left) + actions (right). The divider
            sits under this row, and the active tab's brand bar rides it. */}
        <div className="flex items-center gap-2 border-b border-surface-border px-5">
          <nav
            className="flex items-center"
            role="tablist"
            aria-label={t("plugin.title")}
          >
            {RESOURCE_TABS.map((tab) => {
              const active = resourceType === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => switchType(tab.id)}
                  className={cn(
                    "relative px-3.5 py-2.5 text-sm font-medium transition-colors",
                    active
                      ? "text-ink-heading after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:bg-brand"
                      : "text-ink-meta hover:text-ink-body",
                  )}
                >
                  {t(tab.labelKey as Parameters<typeof t>[0])}
                </button>
              );
            })}
          </nav>

          <div className="flex min-w-0 flex-1 items-center justify-end gap-1">
            <button
              type="button"
              className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-brand transition-colors hover:bg-brand-light/60 hover:text-brand"
              onClick={() => navigate(marketHref)}
            >
              <Store className="h-3.5 w-3.5" />
              {t("marketplace.title")}
            </button>
            {searchOpen ? (
              <input
                type="text"
                autoFocus
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onBlur={() => {
                  if (!searchQuery) setSearchOpen(false);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setSearchQuery("");
                    setSearchOpen(false);
                  }
                }}
                placeholder={searchPlaceholder}
                className="h-7 w-full min-w-0 max-w-[200px] rounded-none border-0 border-b border-brand bg-transparent px-1 text-xs text-ink-heading placeholder:text-ink-meta outline-none"
              />
            ) : null}
            <button
              type="button"
              aria-label={t("common.search")}
              onClick={() => setSearchOpen((o) => !o)}
              className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
            >
              <Search className="h-3.5 w-3.5" />
            </button>
            {isPlugin ? (
              <button
                type="button"
                aria-label={t("plugin.install")}
                title={t("plugin.install")}
                onClick={() => setInstallOpen(true)}
                className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            ) : resourceType === "skill" ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    aria-label={t("skill.addBtn" as Parameters<typeof t>[0])}
                    className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="min-w-[160px]">
                  <DropdownMenuItem
                    onSelect={() =>
                      navigate(
                        "/conversation/new?mode=skill-creator&skill_kind=skills_library",
                      )
                    }
                  >
                    <Sparkles className="h-4 w-4" />
                    {t("skill.aiCreate" as Parameters<typeof t>[0])}
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => setSkillAddMode("link")}>
                    <FileText className="h-4 w-4" />
                    {t("skill.linkImportShort" as Parameters<typeof t>[0])}
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => setSkillAddMode("upload")}>
                    <Upload className="h-4 w-4" />
                    {t("skill.upload" as Parameters<typeof t>[0])}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    aria-label={t(
                      "connector.addMenuTitle" as Parameters<typeof t>[0],
                    )}
                    className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="min-w-[180px]">
                  <DropdownMenuItem
                    onSelect={() => setConnectorAddMode("http")}
                  >
                    <Link2 className="h-4 w-4" />
                    {t("connector.addHttp" as Parameters<typeof t>[0])}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() => setConnectorAddMode("stdio")}
                  >
                    <Plus className="h-4 w-4" />
                    {t("connector.addStdio" as Parameters<typeof t>[0])}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>
        </div>
      </div>

      {/* Body: list | detail. Plugin renders its two columns inline; skill
          and connector delegate to their own self-contained panes. */}
      <div className="flex min-h-0 flex-1">
        {isPlugin ? (
          <>
            <div className="w-[345px] shrink-0 overflow-y-auto border-r border-surface-border">
              {loading ? (
                <PageLoader logo className="py-16" />
              ) : filtered.length === 0 ? (
                <div className="flex justify-center pt-24">
                  <EmptyState
                    icon={<Puzzle />}
                    title={
                      plugins.length === 0
                        ? t("plugin.emptyTitle")
                        : t("plugin.noMatch")
                    }
                    message={
                      plugins.length === 0 ? t("plugin.emptyDesc") : undefined
                    }
                    action={
                      plugins.length === 0 ? (
                        <div className="flex flex-wrap justify-center gap-2">
                          <Button
                            variant="default"
                            size="sm"
                            onClick={() =>
                              navigate("/marketplace?tab=plugins&from=plugins")
                            }
                          >
                            <Store className="h-3 w-3" />
                            {t("plugin.browseMarket")}
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setInstallOpen(true)}
                          >
                            <Plus className="h-3 w-3" />
                            {t("plugin.install")}
                          </Button>
                        </div>
                      ) : undefined
                    }
                  />
                </div>
              ) : (
                <div className="pt-6 pb-2">
                  {filtered.map((plugin) => (
                    <PluginRow
                      key={plugin.id}
                      plugin={plugin}
                      active={current?.id === plugin.id}
                      onClick={() => setActiveId(plugin.id)}
                      onToggle={(enabled) => void handleToggle(plugin, enabled)}
                    />
                  ))}
                </div>
              )}
            </div>

            <div className="min-w-0 flex-1">
              {current ? (
                <PluginDetailPanel
                  key={current.id}
                  plugin={current}
                  busy={busy}
                  onToggleEnabled={(enabled) =>
                    void handleToggle(current, enabled)
                  }
                  onUpdate={() => void handleUpdate(current)}
                  onUninstall={() => setUninstallTarget(current)}
                  onExport={() => void handleExport(current)}
                  onOpenMember={openMember}
                />
              ) : (
                <div className="flex justify-center pt-24">
                  <EmptyState
                    icon={<Puzzle />}
                    message={t("resource.emptyDetail")}
                  />
                </div>
              )}
            </div>
          </>
        ) : resourceType === "skill" ? (
          <SkillsPane
            query={searchQuery}
            addMode={skillAddMode}
            onAddModeChange={setSkillAddMode}
          />
        ) : (
          <ConnectorsPane
            query={searchQuery}
            addMode={connectorAddMode}
            onAddModeChange={setConnectorAddMode}
          />
        )}
      </div>

      <PluginInstallDialog
        open={installOpen}
        onOpenChange={setInstallOpen}
        onInstalled={(result) => {
          setActiveId(result.plugin.id);
          void loadPlugins();
        }}
      />

      <DeleteConfirmDialog
        open={!!uninstallTarget}
        onOpenChange={(open) => {
          if (!open) setUninstallTarget(null);
        }}
        title={
          uninstallTarget
            ? t("plugin.uninstallConfirmTitle", { name: uninstallTarget.name })
            : undefined
        }
        description={t("plugin.uninstallConfirmDesc")}
        confirmLabel={t("plugin.uninstall")}
        loading={busy === "uninstall"}
        onConfirm={handleUninstall}
      />

      <UninstallSummaryDialog
        summary={uninstallSummary}
        onClose={() => setUninstallSummary(null)}
      />

      <PluginConflictDialog
        open={updateConflictTarget !== null}
        conflicts={updateConflicts}
        busy={busy === "update"}
        onOpenChange={(next) => {
          if (!next) {
            setUpdateConflictTarget(null);
            setUpdateConflicts([]);
          }
        }}
        onChoose={(onConflict) => {
          const target = updateConflictTarget;
          setUpdateConflictTarget(null);
          setUpdateConflicts([]);
          if (target) void runUpdate(target, onConflict);
        }}
      />
    </div>
  );
};

/* ── Compact list row (mirrors ConnectorListItem density) ──── */

function PluginRow({
  plugin,
  active,
  onClick,
  onToggle,
}: {
  plugin: AgentPluginView;
  active: boolean;
  onClick: () => void;
  onToggle: (enabled: boolean) => void;
}) {
  const { t } = useTranslation();
  const tint = tintFor(plugin.name);
  return (
    <div
      role="option"
      aria-selected={active}
      data-testid="plugin-list-card"
      onClick={onClick}
      className={cn(
        "mx-4 flex cursor-pointer select-none items-center gap-2.5 rounded-lg px-2 py-2 transition-colors",
        active ? "bg-brand-light/60" : "hover:bg-surface-soft",
        !plugin.enabled && "opacity-70",
      )}
    >
      <div
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
        style={{ background: tint.bg, color: tint.fg }}
      >
        <Package className="h-4 w-4" />
      </div>
      <span className="min-w-0 flex-1 truncate text-sm text-ink-heading">
        {plugin.name}
      </span>
      {plugin.update_available ? (
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand"
          title={t("plugin.updateAvailable")}
        />
      ) : null}
      <Badge
        variant="metaOutline"
        className="h-4 shrink-0 px-1 text-2xs"
        title={t(
          PLUGIN_COMPOSITION_LABEL_KEYS[plugin.composition] as Parameters<
            typeof t
          >[0],
        )}
      >
        {t(
          PLUGIN_COMPOSITION_LABEL_KEYS[plugin.composition] as Parameters<
            typeof t
          >[0],
        )}
      </Badge>
      <div
        className="shrink-0"
        // The switch lives inside the row's click target; stop propagation so
        // toggling never changes the selection.
        onClick={(e) => e.stopPropagation()}
      >
        <Switch
          size="sm"
          checked={plugin.enabled}
          onCheckedChange={onToggle}
          aria-label={plugin.enabled ? t("plugin.disable") : t("plugin.enable")}
        />
      </div>
    </div>
  );
}

/* ── Uninstall summary ─────────────────────────────────────── */

function UninstallSummaryDialog({
  summary,
  onClose,
}: {
  summary: (AgentPluginUninstallResult & { name: string }) | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const kindLabel = (kind: AgentPluginMemberRef["kind"]) =>
    kind === "skill" ? t("plugin.skills") : t("plugin.connectors");
  return (
    <Dialog
      open={!!summary}
      onOpenChange={(open) => (!open ? onClose() : undefined)}
    >
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t("plugin.uninstallSummaryTitle")}</DialogTitle>
          <DialogDescription>
            {summary ? t("plugin.uninstalled", { name: summary.name }) : null}
          </DialogDescription>
        </DialogHeader>
        {summary ? (
          <div className="space-y-3 text-sm">
            <section>
              <div className="mb-1 text-xs font-semibold text-ink-heading">
                {t("plugin.removedMembers", {
                  count: summary.removed_members.length,
                })}
              </div>
              {summary.removed_members.length ? (
                <ul className="space-y-0.5 rounded-lg border border-surface-border px-3 py-2">
                  {summary.removed_members.map((m) => (
                    <li
                      key={`${m.kind}:${m.slug}`}
                      className="flex items-center gap-2 text-xs text-ink-heading"
                    >
                      <Badge
                        variant="metaOutline"
                        className="h-4 px-1 text-2xs"
                      >
                        {kindLabel(m.kind)}
                      </Badge>
                      <span className="font-mono">{m.slug}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-xs text-ink-meta">—</div>
              )}
            </section>
            <section>
              <div className="mb-1 text-xs font-semibold text-ink-heading">
                {t("plugin.keptMembers", {
                  count: summary.kept_members.length,
                })}
              </div>
              {summary.kept_members.length ? (
                <ul className="space-y-0.5 rounded-lg border border-surface-border px-3 py-2">
                  {summary.kept_members.map((m) => (
                    <li
                      key={`${m.kind}:${m.slug}`}
                      className="flex items-center gap-2 text-xs text-ink-heading"
                    >
                      <Badge
                        variant="metaOutline"
                        className="h-4 px-1 text-2xs"
                      >
                        {kindLabel(m.kind)}
                      </Badge>
                      <span className="font-mono">{m.slug}</span>
                      <span className="ml-auto text-2xs text-ink-meta">
                        {m.reason === "standalone"
                          ? t("plugin.keptReasonStandalone")
                          : t("plugin.keptReasonOther")}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-xs text-ink-meta">—</div>
              )}
            </section>
          </div>
        ) : null}
        <DialogFooter>
          <Button size="sm" onClick={onClose}>
            {t("common.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

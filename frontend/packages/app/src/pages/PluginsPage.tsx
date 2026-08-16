import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Package, Plus, Puzzle, Search, Store } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  DeleteConfirmDialog,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
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
import {
  ApiError,
  pluginsApi,
  usePanelStore,
  useTranslation,
} from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";
import { PluginConflictDialog } from "../components/plugins/PluginConflictDialog";
import { PluginDetailPanel } from "../components/plugins/PluginDetailPanel";
import { PluginInstallDialog } from "../components/plugins/PluginInstallDialog";
import type { PluginMemberRow } from "../components/plugins/PluginMembersList";
import { PLUGIN_COMPOSITION_LABEL_KEYS } from "../components/plugins/plugin-format";
import { downloadBlob } from "../components/pack-filename";
import { tintFor } from "../components/marketplace-ui";

type Busy = "update" | "uninstall" | "export" | "toggle" | null;

/**
 * Installed Agent Plugins library — narrow list on the left (name, version,
 * composition, member counts, plugin-level switch), detail in the project's
 * right panel (manifest, members, update / export / uninstall). Mirrors the
 * Skills / Connectors library layout.
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
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);
  const [searchParams] = useSearchParams();

  const [plugins, setPlugins] = useState<AgentPluginView[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(() =>
    searchParams.get("plugin"),
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [installOpen, setInstallOpen] = useState(false);
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

  // Same inverted proportions as the Skills / Connectors libraries: fixed
  // narrow list, wide detail aside; restored on unmount.
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

  /* ── Right panel ───────────────────────────────────────────── */

  useEffect(() => {
    if (!current) {
      setRightPanel(null);
      return;
    }
    setRightPanel(
      <PluginDetailPanel
        key={current.id}
        plugin={current}
        busy={busy}
        onToggleEnabled={(enabled) => void handleToggle(current, enabled)}
        onUpdate={() => void handleUpdate(current)}
        onUninstall={() => setUninstallTarget(current)}
        onExport={() => void handleExport(current)}
        onOpenMember={openMember}
      />,
    );
    return () => {
      setRightPanel(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, busy, setRightPanel]);

  /* ── Render ────────────────────────────────────────────────── */

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-2 px-5">
        <span className="shrink-0 whitespace-nowrap text-base font-semibold text-ink-heading">
          {t("plugin.title")}
        </span>
        <div className="flex min-w-0 flex-1 items-center justify-end gap-1">
          <button
            type="button"
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-brand transition-colors hover:bg-brand-light/60 hover:text-brand"
            onClick={() => navigate("/marketplace?tab=plugins&from=plugins")}
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
              placeholder={t("plugin.searchPlaceholder")}
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
          <button
            type="button"
            aria-label={t("plugin.install")}
            title={t("plugin.install")}
            onClick={() => setInstallOpen(true)}
            className="flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-body"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      {loading ? (
        <PageLoader logo />
      ) : (
        <div className="flex-1 overflow-y-auto py-4">
          <div className="mb-4 space-y-2 px-4">
            {filtered.length === 0 ? (
              <EmptyState
                className="py-16"
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
            ) : (
              filtered.map((plugin) => (
                <PluginListCard
                  key={plugin.id}
                  plugin={plugin}
                  active={current?.id === plugin.id}
                  onClick={() => setActiveId(plugin.id)}
                  onToggle={(enabled) => void handleToggle(plugin, enabled)}
                />
              ))
            )}
          </div>
        </div>
      )}

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

/* ── List card ─────────────────────────────────────────────── */

function PluginListCard({
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
    <Card
      onClick={onClick}
      data-testid="plugin-list-card"
      className={cn(
        "cursor-default rounded-lg border-surface-border bg-surface py-0 shadow-xs transition-[border-color,box-shadow] select-none",
        active ? "border-brand bg-surface shadow-sm" : "card-interactive",
        !plugin.enabled && "opacity-70",
      )}
    >
      <CardContent className="flex items-start gap-2 px-4 py-3">
        <div
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
          style={{ background: tint.bg, color: tint.fg }}
        >
          <Package className="h-[18px] w-[18px]" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-0.5 flex flex-wrap items-center gap-1.5">
            <span className="min-w-0 truncate text-sm font-medium text-ink-heading">
              {plugin.name}
            </span>
            {plugin.version ? (
              <Badge
                variant="metaNeutral"
                className="h-4 px-1 font-mono text-2xs text-ink-meta"
              >
                v{plugin.version}
              </Badge>
            ) : null}
            <Badge variant="metaOutline" className="h-4 px-1 text-2xs">
              {t(
                PLUGIN_COMPOSITION_LABEL_KEYS[plugin.composition] as Parameters<
                  typeof t
                >[0],
              )}
            </Badge>
            {plugin.update_available ? (
              <Badge variant="brand" className="h-4 px-1 text-2xs">
                {t("plugin.updateAvailable")}
              </Badge>
            ) : null}
          </div>
          <div className="mb-1 font-mono text-2xs text-ink-meta">
            {t("marketplace.pluginMembers", {
              skills: plugin.skill_count,
              connectors: plugin.connector_count,
            })}
          </div>
          {plugin.description ? (
            <p className="line-clamp-2 text-xs leading-relaxed text-ink-body">
              {plugin.description}
            </p>
          ) : null}
        </div>
        <div
          className="flex shrink-0 items-center"
          // The switch lives inside the card's click target; stop propagation
          // so toggling never changes the selection.
          onClick={(e) => e.stopPropagation()}
        >
          <Switch
            size="sm"
            checked={plugin.enabled}
            onCheckedChange={onToggle}
            aria-label={
              plugin.enabled ? t("plugin.disable") : t("plugin.enable")
            }
          />
        </div>
      </CardContent>
    </Card>
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

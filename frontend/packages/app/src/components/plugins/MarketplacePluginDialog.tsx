import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Download,
  ExternalLink,
  Info,
  LoaderCircle,
  Package,
  RefreshCw,
} from "lucide-react";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@valuz/ui";
import type {
  AgentPluginMemberRef,
  AgentPluginOnConflict,
  AgentPluginView,
  MarketplaceItem,
  MarketplaceItemDetail,
} from "@valuz/core";
import {
  ApiError,
  marketplaceApi,
  marketplacePluginMembers,
  pluginsApi,
  useTranslation,
} from "@valuz/core";
import {
  marketplaceIcon,
  tintFor,
} from "../marketplace-ui";
import { PluginConflictDialog } from "./PluginConflictDialog";
import { PluginMembersList } from "./PluginMembersList";
import { manifestKeywords, manifestString } from "./plugin-format";

interface MarketplacePluginDialogProps {
  item: MarketplaceItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after a successful install / update so the browse page can flip
   * the card's installed state without a full reload. */
  onInstalled: (item: MarketplaceItem) => void;
}

type PendingAction = "install" | "update";

/** Find the locally installed copy of a market plugin item — matched by the
 * recorded ``source_ref`` first, then by ``plugin.json.name``. */
function findInstalled(
  plugins: AgentPluginView[],
  item: MarketplaceItem,
  manifestName: string | null,
): AgentPluginView | null {
  return (
    plugins.find((p) => p.source_ref === item.id) ??
    plugins.find((p) => manifestName && p.name === manifestName) ??
    plugins.find((p) => p.name === item.source_ref) ??
    null
  );
}

/**
 * Plugin detail dialog for the market: ``plugin.json`` metadata, members
 * grouped skills / connectors, and an install / update action that runs
 * ``/v1/plugins/preview`` first and prompts skip / overwrite on same-slug
 * conflicts — never a silent overwrite.
 */
export function MarketplacePluginDialog({
  item,
  open,
  onOpenChange,
  onInstalled,
}: MarketplacePluginDialogProps) {
  const { t } = useTranslation();
  const tr = useCallback(
    (key: string, params?: Record<string, string | number>) =>
      t(key as Parameters<typeof t>[0], params),
    [t],
  );
  const [detail, setDetail] = useState<MarketplaceItemDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [installedPlugin, setInstalledPlugin] =
    useState<AgentPluginView | null>(null);
  const [busy, setBusy] = useState<PendingAction | "preview" | null>(null);
  const [conflicts, setConflicts] = useState<AgentPluginMemberRef[]>([]);
  const [conflictAction, setConflictAction] = useState<PendingAction | null>(
    null,
  );

  useEffect(() => {
    if (!open || !item) return;
    let cancelled = false;
    setDetail(null);
    setInstalledPlugin(null);
    setConflicts([]);
    setConflictAction(null);
    setLoading(true);
    const loadDetail = marketplaceApi
      .get(item.id)
      .then((d) => {
        if (!cancelled) setDetail(d);
        return d;
      })
      .catch(() => {
        if (!cancelled) toast.error(tr("marketplace.error.installFailed"));
        return null;
      });
    // Installed items: look the local copy up so the dialog can offer an
    // update (and know which id to update).
    const loadInstalled = item.installed
      ? pluginsApi.list().catch(() => ({ items: [] as AgentPluginView[] }))
      : Promise.resolve({ items: [] as AgentPluginView[] });
    Promise.all([loadDetail, loadInstalled])
      .then(([d, list]) => {
        if (cancelled || !item.installed) return;
        const manifestName = manifestString(d?.plugin_manifest, "name");
        setInstalledPlugin(findInstalled(list.items, item, manifestName));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, item?.id, item?.installed]);

  if (!item) return null;

  const view: MarketplaceItemDetail = detail
    ? {
        ...detail,
        description: detail.description || item.description,
        category_label: detail.category_label ?? item.category_label,
      }
    : (item as MarketplaceItemDetail);
  const manifest = view.plugin_manifest ?? null;
  const members = marketplacePluginMembers(view);
  const skillCount =
    view.skill_count ??
    (members.length ? members.filter((m) => m.kind === "skill").length : 0);
  const connectorCount =
    view.connector_count ??
    (members.length ? members.filter((m) => m.kind === "connector").length : 0);
  const composition =
    view.composition ??
    (connectorCount > 0 ? "with_connectors" : "skills_only");
  const version = view.version ?? manifestString(manifest, "version");
  const isImageIcon = !!view.icon && /^https?:\/\//.test(view.icon);
  const Icon = view.icon ? marketplaceIcon(view.icon) : Package;
  const tint = tintFor(item.id);

  const updateAvailable =
    !!installedPlugin &&
    (installedPlugin.update_available === true ||
      (!!version &&
        !!installedPlugin.version &&
        installedPlugin.version !== version));

  const showError = (err: unknown, fallbackKey: string) => {
    if (err instanceof ApiError && err.i18nKey) {
      toast.error(
        t(err.i18nKey as Parameters<typeof t>[0], err.i18nParams as never),
      );
    } else if (err instanceof ApiError && err.message) {
      toast.error(err.message);
    } else {
      toast.error(tr(fallbackKey));
    }
  };

  const runAction = async (
    action: PendingAction,
    onConflict?: AgentPluginOnConflict,
  ) => {
    setBusy(action);
    try {
      const result =
        action === "update" && installedPlugin
          ? await pluginsApi.update(installedPlugin.id, onConflict)
          : await pluginsApi.install({
              market_item_id: item.id,
              on_conflict: onConflict,
            });
      const name = result.plugin?.name ?? view.title;
      if (result.status === "already_installed") {
        toast.info(tr("marketplace.toastAlreadyInstalled", { name }));
      } else if (result.status === "updated") {
        toast.success(tr("marketplace.toastPluginUpdated", { name }));
      } else {
        toast.success(tr("marketplace.toastPluginInstalled", { name }));
      }
      onInstalled(item);
      onOpenChange(false);
    } catch (err) {
      showError(err, "marketplace.error.installFailed");
    } finally {
      setBusy(null);
    }
  };

  /** Preview first; prompt when the preview reports same-slug conflicts. */
  const startAction = async (action: PendingAction) => {
    setBusy("preview");
    try {
      const preview = await pluginsApi.preview({ market_item_id: item.id });
      if (preview.conflicts.length > 0) {
        setConflicts(preview.conflicts);
        setConflictAction(action);
        setBusy(null);
        return;
      }
    } catch (err) {
      // A missing / failing preview must not block installing — the install
      // endpoint applies the same default (skip) policy.
      if (err instanceof ApiError && err.status !== 404) {
        showError(err, "marketplace.error.installFailed");
        setBusy(null);
        return;
      }
    }
    await runAction(action);
  };

  const meta: { k: string; v: string; href?: string }[] = [];
  if (version) meta.push({ k: tr("marketplace.modalVersion"), v: version });
  meta.push({
    k: tr("marketplace.modalComposition"),
    v:
      composition === "with_connectors"
        ? tr("marketplace.compositionWithConnectors")
        : tr("marketplace.compositionSkillsOnly"),
  });
  const author = manifestString(manifest, "author") ?? view.owner ?? null;
  if (author) meta.push({ k: tr("marketplace.modalAuthor"), v: author });
  const license = manifestString(manifest, "license");
  if (license) meta.push({ k: tr("marketplace.modalLicense"), v: license });
  const homepage = manifestString(manifest, "homepage");
  if (homepage)
    meta.push({
      k: tr("marketplace.modalHomepage"),
      v: homepage,
      href: homepage,
    });
  const repository = manifestString(manifest, "repository");
  if (repository)
    meta.push({
      k: tr("marketplace.modalRepository"),
      v: repository,
      href: /^https?:\/\//.test(repository) ? repository : undefined,
    });
  if (view.updated_at)
    meta.push({ k: tr("marketplace.modalUpdated"), v: view.updated_at });
  const keywords = manifestKeywords(manifest);

  const primaryDisabled =
    !!busy || loading || (item.installed && !updateAvailable);
  const primaryLabel =
    busy === "preview"
      ? tr("marketplace.pluginPreviewing")
      : busy === "update"
        ? tr("marketplace.pluginUpdating")
        : busy === "install"
          ? tr("marketplace.installing")
          : updateAvailable
            ? tr("marketplace.pluginUpdate")
            : item.installed
              ? tr("marketplace.installed")
              : tr("marketplace.installPlugin");

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && busy) return;
          onOpenChange(nextOpen);
        }}
      >
        <DialogContent className="flex max-h-[88vh] w-[760px] max-w-[94vw] flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="border-b border-surface-border px-6 py-5 text-left">
            <div className="flex items-start gap-3.5">
              <div
                className="flex h-12 w-12 flex-none items-center justify-center overflow-hidden rounded-xl"
                style={
                  isImageIcon
                    ? undefined
                    : { background: tint.bg, color: tint.fg }
                }
              >
                {isImageIcon ? (
                  <img
                    src={view.icon ?? undefined}
                    alt=""
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <Icon className="h-6 w-6" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                {/* No source pill here: every plugin's source maps to
                    ``plugin``, so it only repeated the type chip beside it. */}
                <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                  <span className="rounded border border-surface-border bg-surface-soft px-1.5 py-px font-mono text-2xs text-ink-meta">
                    {tr("marketplace.modalTypePlugin")}
                  </span>
                  <Badge variant="metaOutline">
                    {composition === "with_connectors"
                      ? tr("marketplace.compositionWithConnectors")
                      : tr("marketplace.compositionSkillsOnly")}
                  </Badge>
                  {installedPlugin?.version ? (
                    <Badge variant="metaNeutral">
                      {tr("marketplace.pluginInstalledVersion", {
                        version: installedPlugin.version,
                      })}
                    </Badge>
                  ) : null}
                  {updateAvailable ? (
                    <Badge variant="brand">
                      {tr("marketplace.pluginUpdateAvailable")}
                    </Badge>
                  ) : null}
                </div>
                <DialogTitle
                  title={view.title}
                  className="line-clamp-2 min-w-0 break-words text-lg font-semibold leading-snug tracking-tight text-ink-heading"
                >
                  {view.title}
                </DialogTitle>
                {view.description ? (
                  <DialogDescription className="mt-2 line-clamp-3 max-w-[520px] text-xs leading-relaxed text-ink-body">
                    {view.description}
                  </DialogDescription>
                ) : null}
                <div className="mt-2 text-xs text-ink-body">
                  {tr("marketplace.pluginMembers", {
                    skills: skillCount,
                    connectors: connectorCount,
                  })}
                </div>
              </div>
            </div>
          </DialogHeader>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            {busy === "install" || busy === "update" ? (
              <div className="mb-5 flex items-start gap-2.5 rounded-lg border border-brand/20 bg-brand-light/40 px-3.5 py-3">
                <LoaderCircle className="mt-0.5 h-4 w-4 flex-none animate-spin text-brand" />
                <div className="text-xs leading-relaxed text-ink-body">
                  {tr("marketplace.installProgressPluginHint")}
                </div>
              </div>
            ) : null}
            {loading && !detail ? (
              <div className="py-8 text-center text-sm text-ink-meta">
                {tr("marketplace.loading")}
              </div>
            ) : (
              <>
                <section className="mb-5 rounded-lg border border-surface-border bg-surface px-3.5 py-3">
                  <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-ink-heading">
                    <Package className="h-3.5 w-3.5 text-ink-meta" />
                    {tr("marketplace.modalPluginManifest")}
                  </div>
                  <div className="flex flex-wrap gap-x-6 gap-y-2">
                    {meta.map((m) => (
                      <div key={m.k} className="min-w-0 max-w-full">
                        <div className="mb-0.5 font-mono text-2xs uppercase tracking-wider text-ink-meta">
                          {m.k}
                        </div>
                        {m.href ? (
                          <a
                            href={m.href}
                            target="_blank"
                            rel="noreferrer noopener"
                            className="inline-flex max-w-full items-center gap-1 truncate text-sm text-brand hover:underline"
                          >
                            <span className="truncate">{m.v}</span>
                            <ExternalLink className="h-3 w-3 flex-none" />
                          </a>
                        ) : (
                          <div className="truncate text-sm text-ink-heading">
                            {m.v}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                  {keywords.length ? (
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {keywords.slice(0, 12).map((kw) => (
                        <Badge
                          key={kw}
                          variant="metaOutline"
                          className="h-5 px-1.5 text-2xs font-normal"
                        >
                          {kw}
                        </Badge>
                      ))}
                    </div>
                  ) : null}
                </section>
                <div className="mb-2 text-xs font-semibold text-ink-heading">
                  {tr("marketplace.modalPluginMembers")}
                </div>
                <PluginMembersList
                  members={members}
                  emptyState={
                    <div className="rounded-lg border border-dashed border-surface-border px-3 py-4 text-center text-xs text-ink-meta">
                      {tr("marketplace.modalPluginNoMembers")}
                    </div>
                  }
                />
              </>
            )}
          </div>

          <div className="flex items-center justify-between gap-3 border-t border-surface-border bg-surface px-6 py-3.5">
            <div className="flex items-center gap-1.5 text-xs text-ink-meta">
              <Info className="h-3.5 w-3.5" />
              {tr("marketplace.installTargetPlugin")}
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!!busy}
                onClick={() => onOpenChange(false)}
              >
                {t("common.cancel")}
              </Button>
              <Button
                size="sm"
                disabled={primaryDisabled}
                onClick={() =>
                  void startAction(updateAvailable ? "update" : "install")
                }
              >
                {updateAvailable ? (
                  <RefreshCw className="mr-1 h-3.5 w-3.5" />
                ) : (
                  <Download className="mr-1 h-3.5 w-3.5" />
                )}
                {primaryLabel}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <PluginConflictDialog
        open={conflictAction !== null}
        conflicts={conflicts}
        busy={busy === "install" || busy === "update"}
        onOpenChange={(next) => {
          if (!next) {
            setConflictAction(null);
            setConflicts([]);
          }
        }}
        onChoose={(onConflict) => {
          const action = conflictAction ?? "install";
          setConflictAction(null);
          setConflicts([]);
          void runAction(action, onConflict);
        }}
      />
    </>
  );
}

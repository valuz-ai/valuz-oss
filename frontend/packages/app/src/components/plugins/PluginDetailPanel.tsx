import {
  Download,
  ExternalLink,
  Package,
  RefreshCw,
  Trash2,
} from "lucide-react";
import {
  Badge,
  Button,
  Switch,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@valuz/ui";
import type { AgentPluginView } from "@valuz/core";
import { useTranslation } from "@valuz/core";
import { PluginMembersList, type PluginMemberRow } from "./PluginMembersList";
import {
  PLUGIN_COMPOSITION_LABEL_KEYS,
  PLUGIN_SOURCE_LABEL_KEYS,
} from "./plugin-format";
import { tintFor } from "../marketplace-ui";

export interface PluginDetailPanelProps {
  plugin: AgentPluginView;
  busy?: "update" | "uninstall" | "export" | "toggle" | null;
  onToggleEnabled: (enabled: boolean) => void;
  onUpdate?: () => void;
  onUninstall: () => void;
  onExport: () => void;
  onOpenMember: (member: PluginMemberRow) => void;
}

function formatDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString();
}

/**
 * Right-panel detail for one installed plugin: manifest metadata, plugin-level
 * enable switch, update / export / uninstall actions and the members list
 * (grouped skills → connectors, each openable in its own library page).
 */
export function PluginDetailPanel({
  plugin,
  busy = null,
  onToggleEnabled,
  onUpdate,
  onUninstall,
  onExport,
  onOpenMember,
}: PluginDetailPanelProps) {
  const { t } = useTranslation();
  const tint = tintFor(plugin.name);
  const meta: { k: string; v: string; href?: string }[] = [];
  if (plugin.version) meta.push({ k: t("plugin.version"), v: plugin.version });
  meta.push({
    k: t("plugin.source"),
    v: t(PLUGIN_SOURCE_LABEL_KEYS[plugin.source] as Parameters<typeof t>[0]),
  });
  if (plugin.author?.name)
    meta.push({ k: t("plugin.author"), v: plugin.author.name });
  if (plugin.license) meta.push({ k: t("plugin.license"), v: plugin.license });
  if (plugin.homepage)
    meta.push({
      k: t("plugin.homepage"),
      v: plugin.homepage,
      href: plugin.homepage,
    });
  if (plugin.repository)
    meta.push({
      k: t("plugin.repository"),
      v: plugin.repository,
      href: /^https?:\/\//.test(plugin.repository)
        ? plugin.repository
        : undefined,
    });
  const installedAt = formatDate(plugin.installed_at);
  if (installedAt) meta.push({ k: t("plugin.installedAt"), v: installedAt });
  if (plugin.root_path)
    meta.push({ k: t("plugin.rootPath"), v: plugin.root_path });

  const showUpdate =
    !!onUpdate && (plugin.source === "market" || plugin.source === "url");

  return (
    <aside className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-surface-border px-4 pb-4 pt-4">
        <div className="mb-3 flex items-start gap-3">
          <div
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg"
            style={{ background: tint.bg, color: tint.fg }}
          >
            <Package className="h-[18px] w-[18px]" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="truncate text-base font-medium text-ink-heading">
                {plugin.name}
              </span>
              {plugin.version ? (
                <Badge variant="metaNeutral" className="font-mono">
                  v{plugin.version}
                </Badge>
              ) : null}
              <Badge variant="metaOutline">
                {t(
                  PLUGIN_COMPOSITION_LABEL_KEYS[
                    plugin.composition
                  ] as Parameters<typeof t>[0],
                )}
              </Badge>
              {plugin.update_available ? (
                <Badge variant="brand">{t("plugin.updateAvailable")}</Badge>
              ) : null}
              {!plugin.enabled ? (
                <Badge variant="metaNeutral">{t("plugin.disabled")}</Badge>
              ) : null}
            </div>
            <div className="mt-0.5 text-xs text-ink-body">
              {t("marketplace.pluginMembers", {
                skills: plugin.skill_count,
                connectors: plugin.connector_count,
              })}
            </div>
          </div>
          <TooltipProvider delayDuration={150}>
            <div className="flex shrink-0 items-center gap-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex">
                    <Switch
                      size="sm"
                      checked={plugin.enabled}
                      disabled={busy === "toggle"}
                      onCheckedChange={onToggleEnabled}
                      aria-label={
                        plugin.enabled
                          ? t("plugin.disable")
                          : t("plugin.enable")
                      }
                    />
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  {plugin.enabled ? t("plugin.disable") : t("plugin.enable")}
                </TooltipContent>
              </Tooltip>
              {showUpdate ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="text-ink-meta hover:text-ink-body"
                      loading={busy === "update"}
                      disabled={!!busy}
                      onClick={onUpdate}
                      aria-label={t("plugin.update")}
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>{t("plugin.update")}</TooltipContent>
                </Tooltip>
              ) : null}
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="text-ink-meta hover:text-ink-body"
                    loading={busy === "export"}
                    disabled={!!busy}
                    onClick={onExport}
                    aria-label={t("plugin.export")}
                  >
                    <Download className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{t("plugin.export")}</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="text-ink-meta hover:bg-error-light hover:text-error-text"
                    loading={busy === "uninstall"}
                    disabled={!!busy}
                    onClick={onUninstall}
                    aria-label={t("plugin.uninstall")}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{t("plugin.uninstall")}</TooltipContent>
              </Tooltip>
            </div>
          </TooltipProvider>
        </div>
        {plugin.description ? (
          <p className="text-xs leading-relaxed text-ink-body">
            {plugin.description}
          </p>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        <div className="mb-4 flex flex-wrap gap-x-6 gap-y-2">
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
                  className="inline-flex max-w-full items-center gap-1 text-sm text-brand hover:underline"
                >
                  <span className="truncate">{m.v}</span>
                  <ExternalLink className="h-3 w-3 flex-none" />
                </a>
              ) : (
                <div className="truncate text-sm text-ink-heading" title={m.v}>
                  {m.v}
                </div>
              )}
            </div>
          ))}
        </div>
        {plugin.keywords.length ? (
          <div className="mb-4 flex flex-wrap gap-1.5">
            {plugin.keywords.map((kw) => (
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
        <div className="mb-2 text-xs font-semibold text-ink-heading">
          {t("plugin.members")}
        </div>
        <PluginMembersList
          members={plugin.members}
          onOpenMember={onOpenMember}
        />
      </div>
    </aside>
  );
}

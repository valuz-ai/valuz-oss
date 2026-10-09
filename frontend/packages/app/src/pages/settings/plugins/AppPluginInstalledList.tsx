import { useState } from "react";
import { toast } from "sonner";
import { RefreshCw, ScrollText, Settings2, Trash2 } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  EmptyState,
  Spinner,
  Switch,
} from "@valuz/ui";
import { SlotRenderer, appPluginsApi, useTranslation } from "@valuz/core";
import type { LocalizedText, AppPlugin } from "@valuz/core";
import { getLocale } from "@valuz/shared/i18n";
import { AppPluginConfigDialog } from "./AppPluginConfigDialog";
import { AppPluginLogsDialog } from "./AppPluginLogsDialog";
import { AppPluginUninstallDialog } from "./AppPluginUninstallDialog";
import {
  APP_PLUGIN_STATUS_VARIANT,
  errorDetail,
  isKnownStatus,
  isUnrunnable,
  localizedText,
  sourceLocation,
} from "./app-plugin-helpers";
import {
  useAppPluginLoadStates,
  type AppPluginLoadState,
} from "./use-app-plugins";

const LOAD_VARIANT = {
  active: "success",
  failed: "error",
  loading: "brand",
  disposed: "metaNeutral",
  "backend-disabled": "metaNeutral",
} as const;

const loadErrorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error ?? "");

const AppPluginRow = ({
  plugin,
  load,
  busy,
  anyBusy,
  onToggle,
  onReload,
  onOpenConfig,
  onOpenLogs,
  onOpenUninstall,
  refresh,
}: {
  plugin: AppPlugin;
  load: AppPluginLoadState | undefined;
  busy: boolean;
  anyBusy: boolean;
  onToggle: (plugin: AppPlugin, enabled: boolean) => void;
  onReload: (plugin: AppPlugin) => void;
  onOpenConfig: (plugin: AppPlugin) => void;
  onOpenLogs: (plugin: AppPlugin) => void;
  onOpenUninstall: (plugin: AppPlugin) => void;
  refresh: () => void;
}) => {
  const { t } = useTranslation();
  const locale = getLocale();
  const name = localizedText(plugin.name as LocalizedText, locale) || plugin.id;
  const description = localizedText(
    plugin.description as LocalizedText | null | undefined,
    locale,
  );
  const status = isKnownStatus(plugin.status) ? plugin.status : null;
  const location = sourceLocation(plugin.source, plugin.dev_path);
  const scope =
    plugin.source.kind === "catalog" && plugin.source.scope
      ? plugin.source.scope
      : null;
  const isDev = plugin.source.kind === "dev";
  const hasConfig = Boolean(
    plugin.config_schema &&
    Object.keys(
      (plugin.config_schema.properties as
        Record<string, unknown> | undefined) ?? {},
    ).length > 0,
  );
  // Only an enabled plugin has a frontend the renderer would load.
  const showLoad = plugin.enabled && load !== undefined;
  const sourceKindLabel = t(
    `pluginSettings.appPlugins.source.${plugin.source.kind}` as Parameters<
      typeof t
    >[0],
  );

  return (
    <li className="py-3" data-app-plugin-id={plugin.id}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-sm font-medium text-ink-heading">{name}</span>
            <span className="font-mono text-xs text-ink-meta">{plugin.id}</span>
            <Badge variant="metaOutline">v{plugin.version}</Badge>
            <Badge
              variant={
                status ? APP_PLUGIN_STATUS_VARIANT[status] : "metaNeutral"
              }
              data-status={plugin.status}
            >
              {status
                ? t(`pluginSettings.appPlugins.status.${status}`)
                : plugin.status}
            </Badge>
            {showLoad ? (
              <Badge
                variant={LOAD_VARIANT[load.status] ?? "metaNeutral"}
                data-load-status={load.status}
              >
                {t(
                  `pluginSettings.appPlugins.load.${
                    load.status === "backend-disabled"
                      ? "disposed"
                      : load.status
                  }` as Parameters<typeof t>[0],
                )}
              </Badge>
            ) : null}
            <Badge variant="metaNeutral">
              {sourceKindLabel}
              {scope
                ? ` · ${t(`pluginSettings.appPlugins.source.scope.${scope}` as Parameters<typeof t>[0])}`
                : ""}
            </Badge>
          </div>
          {description ? (
            <p className="text-xs text-ink-body">{description}</p>
          ) : null}
          <p className="break-words text-xs text-ink-meta">
            {t("pluginSettings.appPlugins.meta.publisher", {
              name: plugin.publisher?.name ?? "—",
            })}
            {location ? (
              <>
                {" · "}
                <span className="break-all font-mono">{location}</span>
              </>
            ) : null}
          </p>
          <p className="break-words text-xs text-ink-meta">
            {plugin.permissions.length > 0
              ? t("pluginSettings.appPlugins.meta.permissions", {
                  list: plugin.permissions.join(", "),
                })
              : t("pluginSettings.appPlugins.meta.noPermissions")}
          </p>
          {plugin.status_reason ? (
            <p
              className="break-words text-xs text-warning-text"
              data-slot="status-reason"
            >
              {t("pluginSettings.appPlugins.meta.reason", {
                reason: plugin.status_reason,
              })}
            </p>
          ) : null}
          {plugin.unmet_requires.length > 0 ? (
            <p
              className="break-words text-xs text-warning-text"
              data-slot="unmet-requires"
            >
              {t("pluginSettings.appPlugins.meta.unmet", {
                list: plugin.unmet_requires.join(", "),
              })}
            </p>
          ) : null}
          {showLoad && load.status === "failed" ? (
            <p
              className="break-words text-xs text-error-text"
              data-slot="load-error"
            >
              {t("pluginSettings.appPlugins.load.error", {
                error: loadErrorText(load.error),
              })}
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {busy ? <Spinner className="text-ink-meta" /> : null}
          <SlotRenderer
            name="settings.plugins.app-plugins.item.actions"
            context={{ plugin, refresh }}
          />
          {isDev ? (
            <Button
              variant="ghost"
              size="icon-xs"
              disabled={anyBusy}
              title={t("pluginSettings.appPlugins.actions.reload")}
              aria-label={t("pluginSettings.appPlugins.actions.reload")}
              onClick={() => onReload(plugin)}
            >
              <RefreshCw />
            </Button>
          ) : null}
          {hasConfig ? (
            <Button
              variant="ghost"
              size="icon-xs"
              title={t("pluginSettings.appPlugins.actions.config")}
              aria-label={t("pluginSettings.appPlugins.actions.config")}
              onClick={() => onOpenConfig(plugin)}
            >
              <Settings2 />
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="icon-xs"
            title={t("pluginSettings.appPlugins.actions.logs")}
            aria-label={t("pluginSettings.appPlugins.actions.logs")}
            onClick={() => onOpenLogs(plugin)}
          >
            <ScrollText />
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            disabled={anyBusy}
            title={t("pluginSettings.appPlugins.actions.uninstall")}
            aria-label={t("pluginSettings.appPlugins.actions.uninstall")}
            onClick={() => onOpenUninstall(plugin)}
          >
            <Trash2 />
          </Button>
          <Switch
            size="sm"
            checked={plugin.enabled}
            // A plugin the backend cannot run cannot be switched on (it can
            // still be switched off).
            disabled={
              anyBusy || (!plugin.enabled && isUnrunnable(plugin.status))
            }
            aria-label={t("pluginSettings.appPlugins.actions.toggle", { name })}
            onCheckedChange={(next) => onToggle(plugin, next)}
          />
        </div>
      </div>
    </li>
  );
};

type Dialogs =
  | { kind: "config"; plugin: AppPlugin }
  | { kind: "logs"; plugin: AppPlugin }
  | { kind: "uninstall"; plugin: AppPlugin };

const nameOf = (plugin: AppPlugin): string =>
  localizedText(plugin.name as LocalizedText, getLocale()) || plugin.id;

/**
 * The installed list with its row actions (enable, reload for dev links,
 * config, logs, uninstall) and the dialogs behind them. ``refresh`` re-reads
 * the list; the generation poll would catch the change anyway, this just
 * makes it immediate.
 */
export const AppPluginInstalledList = ({
  plugins,
  refresh,
}: {
  plugins: AppPlugin[];
  refresh: () => void;
}) => {
  const { t } = useTranslation();
  const loadStates = useAppPluginLoadStates();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialogs | null>(null);

  const run = async (
    plugin: AppPlugin,
    action: () => Promise<unknown>,
    failedKey:
      | "pluginSettings.appPlugins.actions.toggleFailed"
      | "pluginSettings.appPlugins.actions.reloadFailed",
    doneKey?: "pluginSettings.appPlugins.actions.reloaded",
  ) => {
    setBusyId(plugin.id);
    try {
      await action();
      if (doneKey) toast.success(t(doneKey, { name: nameOf(plugin) }));
    } catch (error) {
      toast.error(
        t(failedKey, { name: nameOf(plugin), error: errorDetail(error) }),
      );
    } finally {
      setBusyId(null);
      // A refusal usually means the screen is stale; either way re-read.
      refresh();
    }
  };

  if (plugins.length === 0) {
    return <EmptyState message={t("pluginSettings.appPlugins.empty")} />;
  }

  return (
    <>
      <Card className="rounded-xl shadow-xs">
        <CardContent className="py-1">
          <ul className="divide-y divide-surface-border">
            {plugins.map((plugin) => (
              <AppPluginRow
                key={plugin.id}
                plugin={plugin}
                load={loadStates.get(plugin.id)}
                busy={busyId === plugin.id}
                anyBusy={busyId !== null}
                refresh={refresh}
                onToggle={(target, enabled) =>
                  void run(
                    target,
                    () => appPluginsApi.setEnabled(target.id, enabled),
                    "pluginSettings.appPlugins.actions.toggleFailed",
                  )
                }
                onReload={(target) =>
                  void run(
                    target,
                    () => appPluginsApi.reload(target.id),
                    "pluginSettings.appPlugins.actions.reloadFailed",
                    "pluginSettings.appPlugins.actions.reloaded",
                  )
                }
                onOpenConfig={(target) =>
                  setDialog({ kind: "config", plugin: target })
                }
                onOpenLogs={(target) =>
                  setDialog({ kind: "logs", plugin: target })
                }
                onOpenUninstall={(target) =>
                  setDialog({ kind: "uninstall", plugin: target })
                }
              />
            ))}
          </ul>
        </CardContent>
      </Card>

      {dialog?.kind === "config" ? (
        <AppPluginConfigDialog
          pluginId={dialog.plugin.id}
          name={nameOf(dialog.plugin)}
          schema={dialog.plugin.config_schema}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog?.kind === "logs" ? (
        <AppPluginLogsDialog
          pluginId={dialog.plugin.id}
          name={nameOf(dialog.plugin)}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog?.kind === "uninstall" ? (
        <AppPluginUninstallDialog
          pluginId={dialog.plugin.id}
          name={nameOf(dialog.plugin)}
          onClose={() => setDialog(null)}
          onUninstalled={refresh}
        />
      ) : null}
    </>
  );
};

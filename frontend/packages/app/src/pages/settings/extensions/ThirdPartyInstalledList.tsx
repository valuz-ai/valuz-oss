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
import { SlotRenderer, thirdPartyApi, useTranslation } from "@valuz/core";
import type { LocalizedText, ThirdPartyPlugin } from "@valuz/core";
import { getLocale } from "@valuz/shared/i18n";
import { ThirdPartyConfigDialog } from "./ThirdPartyConfigDialog";
import { ThirdPartyLogsDialog } from "./ThirdPartyLogsDialog";
import { ThirdPartyUninstallDialog } from "./ThirdPartyUninstallDialog";
import {
  THIRD_PARTY_STATUS_VARIANT,
  errorDetail,
  isKnownStatus,
  isUnrunnable,
  localizedText,
  sourceLocation,
} from "./third-party-helpers";
import {
  useThirdPartyLoadStates,
  type ThirdPartyLoadState,
} from "./use-third-party";

const LOAD_VARIANT = {
  active: "success",
  failed: "error",
  loading: "brand",
  disposed: "metaNeutral",
  "backend-disabled": "metaNeutral",
} as const;

const loadErrorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error ?? "");

const ThirdPartyRow = ({
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
  plugin: ThirdPartyPlugin;
  load: ThirdPartyLoadState | undefined;
  busy: boolean;
  anyBusy: boolean;
  onToggle: (plugin: ThirdPartyPlugin, enabled: boolean) => void;
  onReload: (plugin: ThirdPartyPlugin) => void;
  onOpenConfig: (plugin: ThirdPartyPlugin) => void;
  onOpenLogs: (plugin: ThirdPartyPlugin) => void;
  onOpenUninstall: (plugin: ThirdPartyPlugin) => void;
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
    `extensions.thirdParty.source.${plugin.source.kind}` as Parameters<
      typeof t
    >[0],
  );

  return (
    <li className="py-3" data-third-party-id={plugin.id}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-sm font-medium text-ink-heading">{name}</span>
            <span className="font-mono text-xs text-ink-meta">{plugin.id}</span>
            <Badge variant="metaOutline">v{plugin.version}</Badge>
            <Badge
              variant={
                status ? THIRD_PARTY_STATUS_VARIANT[status] : "metaNeutral"
              }
              data-status={plugin.status}
            >
              {status
                ? t(`extensions.thirdParty.status.${status}`)
                : plugin.status}
            </Badge>
            {showLoad ? (
              <Badge
                variant={LOAD_VARIANT[load.status] ?? "metaNeutral"}
                data-load-status={load.status}
              >
                {t(
                  `extensions.thirdParty.load.${
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
                ? ` · ${t(`extensions.thirdParty.source.scope.${scope}` as Parameters<typeof t>[0])}`
                : ""}
            </Badge>
          </div>
          {description ? (
            <p className="text-xs text-ink-body">{description}</p>
          ) : null}
          <p className="break-words text-xs text-ink-meta">
            {t("extensions.thirdParty.meta.publisher", {
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
              ? t("extensions.thirdParty.meta.permissions", {
                  list: plugin.permissions.join(", "),
                })
              : t("extensions.thirdParty.meta.noPermissions")}
          </p>
          {plugin.status_reason ? (
            <p
              className="break-words text-xs text-warning-text"
              data-slot="status-reason"
            >
              {t("extensions.thirdParty.meta.reason", {
                reason: plugin.status_reason,
              })}
            </p>
          ) : null}
          {plugin.unmet_requires.length > 0 ? (
            <p
              className="break-words text-xs text-warning-text"
              data-slot="unmet-requires"
            >
              {t("extensions.thirdParty.meta.unmet", {
                list: plugin.unmet_requires.join(", "),
              })}
            </p>
          ) : null}
          {showLoad && load.status === "failed" ? (
            <p
              className="break-words text-xs text-error-text"
              data-slot="load-error"
            >
              {t("extensions.thirdParty.load.error", {
                error: loadErrorText(load.error),
              })}
            </p>
          ) : null}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {busy ? <Spinner className="text-ink-meta" /> : null}
          <SlotRenderer
            name="settings.extensions.third-party.item.actions"
            context={{ plugin, refresh }}
          />
          {isDev ? (
            <Button
              variant="ghost"
              size="icon-xs"
              disabled={anyBusy}
              title={t("extensions.thirdParty.actions.reload")}
              aria-label={t("extensions.thirdParty.actions.reload")}
              onClick={() => onReload(plugin)}
            >
              <RefreshCw />
            </Button>
          ) : null}
          {hasConfig ? (
            <Button
              variant="ghost"
              size="icon-xs"
              title={t("extensions.thirdParty.actions.config")}
              aria-label={t("extensions.thirdParty.actions.config")}
              onClick={() => onOpenConfig(plugin)}
            >
              <Settings2 />
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="icon-xs"
            title={t("extensions.thirdParty.actions.logs")}
            aria-label={t("extensions.thirdParty.actions.logs")}
            onClick={() => onOpenLogs(plugin)}
          >
            <ScrollText />
          </Button>
          <Button
            variant="ghost"
            size="icon-xs"
            disabled={anyBusy}
            title={t("extensions.thirdParty.actions.uninstall")}
            aria-label={t("extensions.thirdParty.actions.uninstall")}
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
            aria-label={t("extensions.thirdParty.actions.toggle", { name })}
            onCheckedChange={(next) => onToggle(plugin, next)}
          />
        </div>
      </div>
    </li>
  );
};

type Dialogs =
  | { kind: "config"; plugin: ThirdPartyPlugin }
  | { kind: "logs"; plugin: ThirdPartyPlugin }
  | { kind: "uninstall"; plugin: ThirdPartyPlugin };

const nameOf = (plugin: ThirdPartyPlugin): string =>
  localizedText(plugin.name as LocalizedText, getLocale()) || plugin.id;

/**
 * The installed list with its row actions (enable, reload for dev links,
 * config, logs, uninstall) and the dialogs behind them. ``refresh`` re-reads
 * the list; the generation poll would catch the change anyway, this just
 * makes it immediate.
 */
export const ThirdPartyInstalledList = ({
  plugins,
  refresh,
}: {
  plugins: ThirdPartyPlugin[];
  refresh: () => void;
}) => {
  const { t } = useTranslation();
  const loadStates = useThirdPartyLoadStates();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialogs | null>(null);

  const run = async (
    plugin: ThirdPartyPlugin,
    action: () => Promise<unknown>,
    failedKey:
      | "extensions.thirdParty.actions.toggleFailed"
      | "extensions.thirdParty.actions.reloadFailed",
    doneKey?: "extensions.thirdParty.actions.reloaded",
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
    return <EmptyState message={t("extensions.thirdParty.empty")} />;
  }

  return (
    <>
      <Card className="rounded-xl shadow-xs">
        <CardContent className="py-1">
          <ul className="divide-y divide-surface-border">
            {plugins.map((plugin) => (
              <ThirdPartyRow
                key={plugin.id}
                plugin={plugin}
                load={loadStates.get(plugin.id)}
                busy={busyId === plugin.id}
                anyBusy={busyId !== null}
                refresh={refresh}
                onToggle={(target, enabled) =>
                  void run(
                    target,
                    () => thirdPartyApi.setEnabled(target.id, enabled),
                    "extensions.thirdParty.actions.toggleFailed",
                  )
                }
                onReload={(target) =>
                  void run(
                    target,
                    () => thirdPartyApi.reload(target.id),
                    "extensions.thirdParty.actions.reloadFailed",
                    "extensions.thirdParty.actions.reloaded",
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
        <ThirdPartyConfigDialog
          pluginId={dialog.plugin.id}
          name={nameOf(dialog.plugin)}
          schema={dialog.plugin.config_schema}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog?.kind === "logs" ? (
        <ThirdPartyLogsDialog
          pluginId={dialog.plugin.id}
          name={nameOf(dialog.plugin)}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog?.kind === "uninstall" ? (
        <ThirdPartyUninstallDialog
          pluginId={dialog.plugin.id}
          name={nameOf(dialog.plugin)}
          onClose={() => setDialog(null)}
          onUninstalled={refresh}
        />
      ) : null}
    </>
  );
};

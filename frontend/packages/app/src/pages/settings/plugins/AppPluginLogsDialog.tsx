import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  EmptyState,
  LoadingState,
} from "@valuz/ui";
import { appPluginsApi, useTranslation } from "@valuz/core";
import type { AppPluginLogEntry } from "@valuz/core";
import { errorDetail } from "./app-plugin-helpers";
import { Notice } from "./Notice";

type LogsState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; entries: AppPluginLogEntry[] };

const LEVEL_VARIANT = (level: string): "error" | "warning" | "metaNeutral" => {
  const value = level.toLowerCase();
  if (value === "error" || value === "critical" || value === "fatal") {
    return "error";
  }
  return value === "warn" || value === "warning" ? "warning" : "metaNeutral";
};

/** ``ts`` is epoch seconds (the plugin log's own clock) or milliseconds. */
const formatTime = (ts: number): string => {
  if (!Number.isFinite(ts)) return "";
  const date = new Date(ts < 1e12 ? ts * 1000 : ts);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString();
};

const SOURCES = ["frontend", "backend", "audit"] as const;

/** The plugin's log (``GET /logs``): what it wrote itself plus the audit trail. */
export const AppPluginLogsDialog = ({
  pluginId,
  name,
  onClose,
}: {
  pluginId: string;
  name: string;
  onClose: () => void;
}) => {
  const { t } = useTranslation();
  const [state, setState] = useState<LogsState>({ phase: "loading" });

  const load = useCallback(
    async (alive?: { current: boolean }) => {
      try {
        const { entries } = await appPluginsApi.logs(pluginId, 200);
        if (alive?.current !== false) setState({ phase: "ready", entries });
      } catch (error) {
        if (alive?.current !== false) {
          setState({ phase: "error", message: errorDetail(error) });
        }
      }
    },
    [pluginId],
  );

  useEffect(() => {
    const alive = { current: true };
    void load(alive);
    return () => {
      alive.current = false;
    };
  }, [load]);

  const refresh = () => {
    setState({ phase: "loading" });
    void load();
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader className="pr-8">
          <DialogTitle>
            {t("pluginSettings.appPlugins.logs.title", { name })}
          </DialogTitle>
          <DialogDescription>
            {t("pluginSettings.appPlugins.logs.desc")}
          </DialogDescription>
        </DialogHeader>
        <div className="flex justify-end">
          <Button
            variant="outline"
            size="sm"
            disabled={state.phase === "loading"}
            onClick={refresh}
          >
            <RefreshCw />
            {t("pluginSettings.appPlugins.logs.refresh")}
          </Button>
        </div>
        {state.phase === "loading" ? (
          <LoadingState variant="section" />
        ) : state.phase === "error" ? (
          <Notice tone="error">
            {t("pluginSettings.appPlugins.logs.loadFailed", {
              error: state.message,
            })}
          </Notice>
        ) : state.entries.length === 0 ? (
          <EmptyState message={t("pluginSettings.appPlugins.logs.empty")} />
        ) : (
          <ul
            aria-label={t("pluginSettings.appPlugins.logs.title", { name })}
            className="max-h-80 space-y-1 overflow-auto rounded-lg border border-surface-border bg-surface-soft px-3 py-2 font-mono text-2xs leading-4 text-ink-body"
          >
            {state.entries.map((entry, index) => (
              <li
                // Entries have no id; time plus file position is stable.
                key={`${entry.ts}-${index}`}
                data-log-source={entry.source}
                className="flex flex-wrap items-baseline gap-x-2"
              >
                <span className="text-ink-meta">{formatTime(entry.ts)}</span>
                <Badge variant={LEVEL_VARIANT(entry.level)}>
                  {entry.level}
                </Badge>
                <Badge variant="metaOutline">
                  {(SOURCES as readonly string[]).includes(entry.source)
                    ? t(
                        `pluginSettings.appPlugins.logs.source.${entry.source}` as Parameters<
                          typeof t
                        >[0],
                      )
                    : entry.source}
                </Badge>
                <span className="min-w-0 break-words whitespace-pre-wrap">
                  {entry.message}
                </span>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
};

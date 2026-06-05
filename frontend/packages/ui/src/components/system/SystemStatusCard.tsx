/**
 * Status card for the desktop ``服务`` panel.
 *
 * Reads ``SystemStatusResponse`` from a parent (the page passes it
 * down) and renders a compact dashboard:
 *
 *   - Health dot + state label + uptime in the header
 *   - Grid of metrics: pid / port / version / kernel pin / active
 *     sessions / data dir
 *   - Action buttons: 打开{t("system.logDir")} / 打开{t("system.logFile")} / 刷新
 *   - Warnings list (only when non-empty) at the bottom
 *
 * Pure presentation — no fetching here. The page wires up
 * ``useSystemStatus`` and pipes the ``status`` slice in.
 */

import { Folder, FileText, RefreshCw, AlertTriangle } from "lucide-react";
import type { SystemStatusResponse } from "@valuz/shared";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card } from "../ui/card";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

const formatUptime = (seconds: number): string => {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  if (h < 24) return mm === 0 ? `${h}h` : `${h}h ${mm}m`;
  const d = Math.floor(h / 24);
  const hh = h % 24;
  return hh === 0 ? `${d}d` : `${d}d ${hh}h`;
};

const STATE_STYLES: Record<
  NonNullable<SystemStatusResponse["status"]>,
  { dot: string; label: string }
> = {
  running: { dot: "bg-emerald-500", label: "system.running" },
  starting: { dot: "bg-amber-400 animate-pulse", label: "system.starting" },
  degraded: { dot: "bg-amber-500", label: "system.degraded" },
};

interface MetricProps {
  label: string;
  value: string;
  /** Make long paths break gracefully. */
  mono?: boolean;
}

const Metric = ({ label, value, mono = false }: MetricProps) => (
  <div className="min-w-0">
    <div className="label-mono text-2xs">{label}</div>
    <div
      className={cn(
        "mt-0.5 truncate text-sm text-ink-heading",
        mono && "font-mono",
      )}
      title={value}
    >
      {value}
    </div>
  </div>
);

export interface SystemStatusCardProps {
  status: SystemStatusResponse | null;
  loading: boolean;
  error: string | null;
  /** Imperative actions provided by ``useSystemActions``. ``null`` =
   *  not running inside Electron — buttons are hidden. */
  actions?: {
    openLogDir: () => Promise<unknown>;
    openLogFile: () => Promise<unknown>;
  } | null;
  onRefresh: () => void;
}

export const SystemStatusCard = ({
  status,
  loading,
  error,
  actions,
  onRefresh,
}: SystemStatusCardProps) => {
  const { t } = useI18n();
  if (error && !status) {
    return (
      <Card className="border-red-200 bg-red-50/50 p-4">
        <div className="flex items-center gap-2 text-sm text-red-700">
          <AlertTriangle className="h-4 w-4" />
          <span>{t("system.cannotConnect", { error })}</span>
        </div>
        <div className="mt-3">
          <Button size="sm" variant="outline" onClick={onRefresh}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            {t("system.retry")}
          </Button>
        </div>
      </Card>
    );
  }

  const stateStyle = status ? STATE_STYLES[status.status] : null;

  return (
    <Card className="p-4">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span
            className={cn(
              "h-2 w-2 shrink-0 rounded-full",
              stateStyle?.dot ?? "bg-ink-meta",
            )}
            aria-hidden
          />
          <h2 className="text-base font-medium text-ink-heading">
            valuz-agent backend
          </h2>
          {stateStyle && (
            <Badge
              variant="secondary"
              className="font-normal text-xs tabular-nums"
            >
              {t(stateStyle.label)}
              {status &&
                status.uptime_seconds > 0 &&
                ` · ${formatUptime(status.uptime_seconds)}`}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {actions && (
            <>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void actions.openLogDir()}
                title={t("system.openLogDir")}
              >
                <Folder className="mr-1.5 h-3.5 w-3.5" />
                {t("system.logDir")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => void actions.openLogFile()}
                title={t("system.openLogFile")}
              >
                <FileText className="mr-1.5 h-3.5 w-3.5" />
                {t("system.logFile")}
              </Button>
            </>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={onRefresh}
            disabled={loading}
          >
            <RefreshCw
              className={cn("h-3.5 w-3.5", loading && "animate-spin")}
            />
          </Button>
        </div>
      </div>

      {status ? (
        <>
          <div className="grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-3 lg:grid-cols-4">
            <Metric label="PID" value={String(status.pid)} mono />
            <Metric label={t("system.port")} value={String(status.port)} mono />
            <Metric label={t("system.version")} value={status.version} mono />
            <Metric label="Kernel" value={status.kernel_pin} mono />
            <Metric
              label={t("system.activeSessions")}
              value={String(status.active_session_count)}
            />
            <Metric
              label={t("system.availableRuntimes")}
              value={status.runtimes_available.join(" · ") || "—"}
            />
            <Metric label={t("system.dataDir")} value={status.data_dir} mono />
            <Metric label={t("system.logFile")} value={status.log_path} mono />
          </div>

          {status.warnings.length > 0 && (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50/50 p-3">
              <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-amber-800">
                <AlertTriangle className="h-3.5 w-3.5" />
                {t("system.warnings", { count: status.warnings.length })}
              </div>
              <ul className="space-y-1 text-xs text-amber-900">
                {status.warnings.map((w, i) => (
                  <li key={i} className="font-mono leading-tight">
                    • {w}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      ) : (
        <div className="text-sm text-ink-meta">{t("common.loading")}</div>
      )}
    </Card>
  );
};

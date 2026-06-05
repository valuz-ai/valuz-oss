/**
 * RapidOCR (and future) parser-setup authorization card.
 *
 * Renders one row per backend-known setup_id, with:
 * - Status badge (`needs_setup` / `running` / `succeeded` / `failed`).
 * - "Download & Enable" button → opens authorization dialog (license + source
 *   confirmation), POSTs ``/setup/{id}/start`` on confirm.
 * - Live progress bar polled every 2 seconds while a job is running.
 * - Cancel button mid-download.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
  parserApi,
  type SetupJobStatusResponse,
  type SetupRequirement,
} from "@valuz/core";
import { Badge, Button, Card, CardContent, cn } from "@valuz/ui";
import { Download, Loader2, RefreshCw, X } from "lucide-react";
import { useTranslation } from "@valuz/core";
import type { I18nKey } from "@valuz/shared";

const POLL_INTERVAL_MS = 2_000;

// Resolve a plugin-contributed i18n key with a literal-string fallback
// (mirror of the helper in ParserSettingsSection.tsx). Used for the
// setup-requirement label which plugins ship via locale JSON.
function _withFallback(
  t: (
    k: I18nKey,
    fallback?: string | Record<string, string | number>,
  ) => string,
  key: string | null | undefined,
  fallback: string,
): string {
  if (!key) return fallback;
  const resolved = t(key as I18nKey);
  return resolved === key ? fallback : resolved;
}

interface AuthDialogProps {
  job: SetupJobStatusResponse;
  onConfirm: () => Promise<void> | void;
  onCancel: () => void;
  t: (
    key: I18nKey,
    fallback?: string | Record<string, string | number>,
  ) => string;
}

function _formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "?";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function AuthDialog({ job, onConfirm, onCancel, t }: AuthDialogProps) {
  const requirement = job.requirement;
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);

  if (!requirement) return null;

  const handleConfirm = async () => {
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <Card className="w-full max-w-md rounded-2xl shadow-xl">
        <CardContent className="space-y-4 p-6">
          <div>
            <div className="text-base font-semibold text-ink-heading">
              {t("settings.parsing.setup.enableOcr")}
            </div>
            <div className="mt-1 text-xs text-ink-body">
              {t("settings.parsing.setup.ocrDesc")}
            </div>
          </div>
          <dl className="space-y-2 text-xs">
            <div className="flex justify-between gap-4">
              <dt className="text-ink-section">
                {t("settings.parsing.setup.model")}
              </dt>
              <dd className="text-ink-body">
                {_withFallback(t, requirement.label_key, requirement.label_zh)}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-ink-section">
                {t("settings.parsing.setup.source")}
              </dt>
              <dd className="text-ink-body">{requirement.source ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-ink-section">
                {t("settings.parsing.setup.size")}
              </dt>
              <dd className="text-ink-body">
                {t("settings.parsing.setup.approxSize", {
                  size: _formatBytes(requirement.size_bytes),
                })}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-ink-section">
                {t("settings.parsing.setup.license")}
              </dt>
              <dd className="text-ink-body">
                {requirement.license_url ? (
                  <a
                    href={requirement.license_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-brand hover:underline"
                  >
                    {requirement.license_name ?? "Apache License 2.0"}
                  </a>
                ) : (
                  (requirement.license_name ?? "—")
                )}
              </dd>
            </div>
          </dl>
          <label className="flex items-start gap-2 text-xs text-ink-body">
            <input
              type="checkbox"
              checked={accepted}
              onChange={(e) => setAccepted(e.target.checked)}
              className="mt-0.5 h-4 w-4"
            />
            <span>{t("settings.parsing.setup.agreement")}</span>
          </label>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onCancel} disabled={busy}>
              {t("common.cancel")}
            </Button>
            <Button onClick={handleConfirm} disabled={!accepted || busy}>
              {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
              {t("settings.parsing.setup.agreeAndDownload")}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

interface ProgressBarProps {
  downloaded: number;
  total: number | null;
}

function ProgressBar({ downloaded, total }: ProgressBarProps) {
  const pct =
    total && total > 0 ? Math.min(100, (downloaded / total) * 100) : null;
  return (
    <div className="mt-2 space-y-1">
      <div className="h-1.5 w-full overflow-hidden rounded bg-surface-soft">
        {pct === null ? (
          <div className="h-full w-1/3 animate-pulse rounded bg-brand/50" />
        ) : (
          <div
            className="h-full rounded bg-brand transition-all"
            style={{ width: `${pct}%` }}
          />
        )}
      </div>
      <div className="text-2xs text-ink-section">
        {_formatBytes(downloaded)}
        {total !== null ? ` / ${_formatBytes(total)}` : ""}
        {pct !== null ? ` · ${pct.toFixed(0)}%` : ""}
      </div>
    </div>
  );
}

const STATUS_KEY: Record<string, string> = {
  succeeded: "settings.parsing.setup.ready",
  running: "settings.parsing.setup.downloading",
  failed: "settings.parsing.setup.failed",
  cancelled: "settings.parsing.setup.cancelled",
};

function StatusBadge({
  status,
  t,
}: {
  status: SetupJobStatusResponse["status"];
  t: (key: I18nKey) => string;
}) {
  const variant =
    status === "succeeded"
      ? "brand"
      : status === "failed"
        ? "destructive"
        : status === "running"
          ? "default"
          : "outline";
  const label = STATUS_KEY[status]
    ? t(STATUS_KEY[status] as I18nKey)
    : t("settings.parsing.setup.needsSetup");
  return <Badge variant={variant as never}>{label}</Badge>;
}

interface JobRowProps {
  job: SetupJobStatusResponse;
  onAuthorize: (req: SetupRequirement) => void;
  onCancel: () => Promise<void> | void;
  t: (
    key: I18nKey,
    fallback?: string | Record<string, string | number>,
  ) => string;
}

function JobRow({ job, onAuthorize, onCancel, t }: JobRowProps) {
  const isRunning = job.status === "running";
  const isDone = job.status === "succeeded";
  return (
    <div className="rounded-xl border border-surface-border bg-surface px-5 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-ink-heading">
              {job.requirement
                ? _withFallback(
                    t,
                    job.requirement.label_key,
                    job.requirement.label_zh,
                  )
                : job.setup_id}
            </span>
            <StatusBadge status={job.status} t={t} />
          </div>
          <div className="mt-0.5 text-xs text-ink-body">
            {t("settings.parsing.setup.sourceAndLicense", {
              source: job.requirement?.source ?? "—",
              license: job.requirement?.license_name ?? "—",
            })}
          </div>
          {job.error ? (
            <div className="mt-1 text-xs text-red-600">{job.error}</div>
          ) : null}
          {isRunning ? (
            <ProgressBar
              downloaded={job.downloaded_bytes}
              total={job.total_bytes}
            />
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {isRunning ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onCancel()}
              className="text-ink-section"
            >
              <X className="mr-1 h-3 w-3" /> {t("common.cancel")}
            </Button>
          ) : isDone ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                job.requirement ? onAuthorize(job.requirement) : undefined
              }
              disabled={!job.requirement}
            >
              <RefreshCw className="mr-1 h-3 w-3" />{" "}
              {t("settings.parsing.setup.redownload")}
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={() =>
                job.requirement ? onAuthorize(job.requirement) : undefined
              }
              disabled={!job.requirement}
            >
              <Download className="mr-1 h-3 w-3" />{" "}
              {t("settings.parsing.setup.downloadAndEnable")}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

interface ParserSetupCardProps {
  onJobSucceeded?: (setupId: string) => void;
}

export function ParserSetupCard({ onJobSucceeded }: ParserSetupCardProps) {
  const { t } = useTranslation();
  const [jobs, setJobs] = useState<SetupJobStatusResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [authTarget, setAuthTarget] = useState<SetupJobStatusResponse | null>(
    null,
  );
  const lastStatusesRef = useRef<Map<string, SetupJobStatusResponse["status"]>>(
    new Map(),
  );

  const refresh = useCallback(async () => {
    try {
      const data = await parserApi.listSetupJobs();
      setJobs(data.jobs);
      if (onJobSucceeded) {
        for (const job of data.jobs) {
          const prev = lastStatusesRef.current.get(job.setup_id);
          if (prev !== "succeeded" && job.status === "succeeded") {
            onJobSucceeded(job.setup_id);
          }
          lastStatusesRef.current.set(job.setup_id, job.status);
        }
      }
    } catch (err) {
      console.error("parserApi.listSetupJobs failed", err);
    } finally {
      setLoading(false);
    }
  }, [onJobSucceeded]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const anyRunning = useMemo(
    () => jobs.some((j) => j.status === "running"),
    [jobs],
  );

  useEffect(() => {
    if (!anyRunning) return;
    const id = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [anyRunning, refresh]);

  const handleAuthorize = (req: SetupRequirement) => {
    const job = jobs.find((j) => j.setup_id === req.id);
    if (!job) return;
    setAuthTarget(job);
  };

  const handleConfirm = async () => {
    if (!authTarget || !authTarget.requirement) return;
    try {
      await parserApi.startSetupJob(authTarget.setup_id, {
        accept_license: true,
        confirmed_source: authTarget.requirement.source ?? "",
      });
      setAuthTarget(null);
      await refresh();
      toast.success(t("settings.parsing.setup.downloadStarted"));
    } catch (err) {
      toast.error(
        t("settings.parsing.setup.startFailed", {
          error: err instanceof Error ? err.message : String(err),
        }),
      );
    }
  };

  const handleCancel = async (setupId: string) => {
    try {
      await parserApi.cancelSetupJob(setupId);
      await refresh();
      toast.info(t("settings.parsing.setup.cancelSent"));
    } catch (err) {
      toast.error(
        t("settings.parsing.setup.cancelFailed", {
          error: err instanceof Error ? err.message : String(err),
        }),
      );
    }
  };

  return (
    <div className={cn("space-y-2")}>
      {loading ? (
        <div className="flex items-center gap-2 text-xs text-ink-section">
          <Loader2 className="h-3 w-3 animate-spin" />{" "}
          {t("settings.parsing.setup.loading")}
        </div>
      ) : jobs.length === 0 ? (
        <div className="text-xs text-ink-section">
          {t("settings.parsing.setup.noTasks")}
        </div>
      ) : (
        jobs.map((job) => (
          <JobRow
            key={job.setup_id}
            job={job}
            onAuthorize={handleAuthorize}
            onCancel={() => handleCancel(job.setup_id)}
            t={t}
          />
        ))
      )}
      {authTarget ? (
        <AuthDialog
          job={authTarget}
          onConfirm={handleConfirm}
          onCancel={() => setAuthTarget(null)}
          t={t}
        />
      ) : null}
    </div>
  );
}

/**
 * On-demand runtime binary install dialog (today: the codex CLI).
 *
 * Opened from a runtime picker when the user clicks an
 * unavailable-but-installable runtime (``RuntimeListItem.installable``).
 * Drives the generic setup-job endpoints (same surface as the RapidOCR
 * model download): license/source authorization gate → start → 2s
 * progress polling → succeeded/failed. On success the caller refreshes
 * ``useRuntimes`` so the picker flips to available without a restart.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  parserApi,
  useTranslation,
  type SetupJobStatusResponse,
} from "@valuz/core";
import { Button, Card, CardContent } from "@valuz/ui";
import { Loader2 } from "lucide-react";
import type { I18nKey } from "@valuz/shared";

const POLL_INTERVAL_MS = 2_000;

function _formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "?";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function _withFallback(
  t: (k: I18nKey) => string,
  key: string | null | undefined,
  fallback: string,
): string {
  if (!key) return fallback;
  const resolved = t(key as I18nKey);
  return resolved === key ? fallback : resolved;
}

export interface RuntimeInstallDialogProps {
  /** Setup-job id to drive (``RuntimeListItem.setup_id``); null = closed. */
  setupId: string | null;
  onClose: () => void;
  /** Fired once when the job reaches ``succeeded`` — refresh the runtime
   *  list here so the picker flips to available. */
  onSucceeded?: () => void;
}

export function RuntimeInstallDialog({
  setupId,
  onClose,
  onSucceeded,
}: RuntimeInstallDialogProps) {
  const { t } = useTranslation();
  const [job, setJob] = useState<SetupJobStatusResponse | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const succeededFired = useRef(false);

  const refresh = useCallback(async () => {
    if (!setupId) return;
    try {
      const status = await parserApi.getSetupJob(setupId);
      setJob(status);
      if (status.status === "succeeded" && !succeededFired.current) {
        succeededFired.current = true;
        onSucceeded?.();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [setupId, onSucceeded]);

  // Load on open; reset per setupId.
  useEffect(() => {
    setJob(null);
    setAccepted(false);
    setError(null);
    succeededFired.current = false;
    if (setupId) void refresh();
  }, [setupId, refresh]);

  // Poll while running.
  useEffect(() => {
    if (!setupId || job?.status !== "running") return;
    const timer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [setupId, job?.status, refresh]);

  if (!setupId) return null;

  const requirement = job?.requirement ?? null;
  const running = job?.status === "running";
  const succeeded = job?.status === "succeeded";

  const handleStart = async () => {
    if (!requirement) return;
    setBusy(true);
    setError(null);
    try {
      const status = await parserApi.startSetupJob(setupId, {
        accept_license: true,
        confirmed_source: requirement.source ?? "unknown",
      });
      setJob(status);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    try {
      const status = await parserApi.cancelSetupJob(setupId);
      setJob(status);
    } catch {
      // Next poll settles the real state.
    }
  };

  const pct =
    job && job.total_bytes
      ? Math.min(100, (job.downloaded_bytes / job.total_bytes) * 100)
      : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4">
      <Card className="w-full max-w-md rounded-2xl shadow-xl">
        <CardContent className="space-y-4 p-6">
          <div>
            <div className="text-base font-semibold text-ink-heading">
              {t("settings.runtimes.installTitle" as Parameters<typeof t>[0])}
            </div>
            <div className="mt-1 text-xs text-ink-body">
              {t("settings.runtimes.installDesc" as Parameters<typeof t>[0])}
            </div>
          </div>

          {!job && !error && (
            <div className="flex items-center gap-2 text-xs text-ink-body">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t("common.loading" as Parameters<typeof t>[0])}
            </div>
          )}

          {requirement && !running && !succeeded && (
            <>
              <dl className="space-y-2 text-xs">
                <div className="flex justify-between gap-4">
                  <dt className="text-ink-section">
                    {t("settings.parsing.setup.source" as Parameters<typeof t>[0])}
                  </dt>
                  <dd className="text-ink-body">{requirement.source ?? "—"}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-ink-section">
                    {t("settings.parsing.setup.size" as Parameters<typeof t>[0])}
                  </dt>
                  <dd className="text-ink-body">
                    {t("settings.parsing.setup.approxSize" as Parameters<typeof t>[0], {
                      size: _formatBytes(requirement.size_bytes),
                    })}
                  </dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-ink-section">
                    {t("settings.parsing.setup.license" as Parameters<typeof t>[0])}
                  </dt>
                  <dd className="text-ink-body">
                    {requirement.license_url ? (
                      <a
                        href={requirement.license_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-brand hover:underline"
                      >
                        {requirement.license_name ?? "License"}
                      </a>
                    ) : (
                      (requirement.license_name ?? "—")
                    )}
                  </dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-ink-section">
                    {t("settings.parsing.setup.model" as Parameters<typeof t>[0])}
                  </dt>
                  <dd className="text-ink-body">
                    {_withFallback(t, requirement.label_key, requirement.label_zh)}
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
                <span>
                  {t("settings.parsing.setup.agreement" as Parameters<typeof t>[0])}
                </span>
              </label>
            </>
          )}

          {running && (
            <div className="space-y-1">
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
                {_formatBytes(job?.downloaded_bytes ?? 0)}
                {job?.total_bytes ? ` / ${_formatBytes(job.total_bytes)}` : ""}
                {pct !== null ? ` · ${pct.toFixed(0)}%` : ""}
              </div>
            </div>
          )}

          {succeeded && (
            <div className="text-xs text-ink-body">
              {t("settings.parsing.setup.ready" as Parameters<typeof t>[0])}
            </div>
          )}

          {(error || job?.error) && (
            <div className="text-xs text-error-text">{error ?? job?.error}</div>
          )}

          <div className="flex justify-end gap-2">
            {running ? (
              <Button variant="outline" onClick={() => void handleCancel()}>
                {t("common.cancel" as Parameters<typeof t>[0])}
              </Button>
            ) : (
              <Button variant="outline" onClick={onClose} disabled={busy}>
                {succeeded
                  ? t("common.close" as Parameters<typeof t>[0])
                  : t("common.cancel" as Parameters<typeof t>[0])}
              </Button>
            )}
            {!running && !succeeded && (
              <Button
                onClick={() => void handleStart()}
                disabled={!requirement || !accepted || busy}
              >
                {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
                {t(
                  "settings.parsing.setup.agreeAndDownload" as Parameters<typeof t>[0],
                )}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

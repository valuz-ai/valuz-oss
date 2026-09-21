import { useEffect, useState } from "react";
import {
  automationsApi,
  useTranslation,
  type AutomationRunDetail,
} from "@valuz/core";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  LoadingState,
} from "@valuz/ui";
import {
  describeRunStatus,
  describeTriggerType,
} from "../components/describe-trigger";

type I18nKey = Parameters<ReturnType<typeof useTranslation>["t"]>[0];
const k = (key: string) => key as I18nKey;

function formatBytes(value: number | null): string | null {
  if (value == null) return null;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function prettyPrint(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export interface AutomationRunDetailPanelProps {
  automationId: string;
  /** ``null`` closes / clears the panel. */
  runId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Detail dialog for a single automation run — fetched on open via
 * ``automationsApi.getRun``. Shows the effective input, artifact, delivered
 * files, and log tail a code (or agent) run produced, plus its trigger type,
 * executor, and any error message.
 */
export const AutomationRunDetailPanel = ({
  automationId,
  runId,
  open,
  onOpenChange,
}: AutomationRunDetailPanelProps) => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<AutomationRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !runId) {
      setDetail(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    automationsApi
      .getRun(automationId, runId)
      .then((res) => {
        if (!cancelled) setDetail(res);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, runId, automationId]);

  const status = detail ? describeRunStatus(detail.status, t) : null;
  const triggerLabel = detail
    ? describeTriggerType(detail.trigger_type, t)
    : "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-xl">
        <DialogHeader className="border-b border-surface-border px-5 pb-4 pt-5">
          <DialogTitle>{t(k("automation.runDetailTitle"))}</DialogTitle>
          <DialogDescription className="sr-only">
            {t(k("automation.runDetailTitle"))}
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <LoadingState variant="section" />
          ) : error ? (
            <p className="text-xs text-error-text">
              {t(k("automation.runDetailLoadFailed"), { error })}
            </p>
          ) : detail ? (
            <div className="flex flex-col gap-4 text-sm">
              <div className="flex flex-wrap items-center gap-2 text-xs text-ink-meta">
                {status ? (
                  <span className="font-medium text-ink-heading">
                    {status.label}
                  </span>
                ) : null}
                <span>·</span>
                <span>{triggerLabel}</span>
                {detail.executor_ref ? (
                  <>
                    <span>·</span>
                    <span className="font-mono">{detail.executor_ref}</span>
                  </>
                ) : null}
              </div>

              {detail.error_message ? (
                <div>
                  <div className="mb-1 text-xs font-medium text-ink-label">
                    {t(k("automation.runDetailErrorLabel"))}
                  </div>
                  <p className="whitespace-pre-wrap rounded-lg border border-error-border bg-error-light px-3 py-2 text-xs text-error-text">
                    {detail.error_message}
                  </p>
                </div>
              ) : null}

              {detail.has_input || detail.input != null ? (
                <div>
                  <div className="mb-1 text-xs font-medium text-ink-label">
                    {t(k("automation.runDetailInputLabel"))}
                  </div>
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-surface-border bg-surface-soft px-3 py-2 font-mono text-2xs leading-4 text-ink-body">
                    {prettyPrint(detail.input) || "—"}
                  </pre>
                </div>
              ) : null}

              {detail.artifact ? (
                <div>
                  <div className="mb-1 text-xs font-medium text-ink-label">
                    {t(k("automation.runDetailArtifactLabel"))}
                  </div>
                  <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-surface-border bg-surface-soft px-3 py-2 font-mono text-2xs leading-4 text-ink-body">
                    {prettyPrint(detail.artifact)}
                  </pre>
                </div>
              ) : null}

              {detail.files.length > 0 ? (
                <div>
                  <div className="mb-1 text-xs font-medium text-ink-label">
                    {t(k("automation.runDetailFilesLabel"))}
                  </div>
                  <ul className="flex flex-col gap-1">
                    {detail.files.map((file) => (
                      <li
                        key={file.artifact_id}
                        className="flex items-center justify-between gap-2 rounded-lg border border-surface-border bg-surface-soft px-3 py-1.5 text-xs"
                      >
                        <span className="min-w-0 flex-1 truncate text-ink-body">
                          {file.name}
                        </span>
                        <span className="shrink-0 text-ink-meta">
                          {formatBytes(file.size_bytes) ?? ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {detail.log_tail ? (
                <div>
                  <div className="mb-1 text-xs font-medium text-ink-label">
                    {t(k("automation.runDetailLogTailLabel"))}
                  </div>
                  <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-surface-border bg-surface-2 px-3 py-2 font-mono text-2xs leading-4 text-ink-body">
                    {detail.log_tail}
                  </pre>
                </div>
              ) : null}

              {!detail.error_message &&
              !detail.has_input &&
              detail.input == null &&
              !detail.artifact &&
              detail.files.length === 0 &&
              !detail.log_tail ? (
                <p className="text-xs text-ink-meta">
                  {t(k("automation.runDetailEmpty"))}
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
};

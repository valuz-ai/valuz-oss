import {
  AlertTriangle,
  CheckCircle2,
  FolderOpen,
  Loader2,
  RotateCw,
  Trash2,
  XCircle,
} from "lucide-react";
import { cn } from "../../lib/cn";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { useI18n } from "../../hooks/use-i18n";

/** Mirror of the backend ``ParserAttempt`` row (one entry per plugin
 *  run for this doc — succeeded or failed). UI doesn't import the
 *  core API type to keep the package boundary one-way. */
export interface DocumentParserAttempt {
  pluginId: string;
  error: string;
  occurredAt: string;
  /** ``true`` for the plugin that succeeded; ``false`` for failed /
   *  fallback attempts. */
  ok: boolean;
}

export interface DocumentDetailPanelProps {
  doc: {
    name: string;
    format: string;
    status: string;
    chunks?: number;
    preview?: string;
  };
  meta?: {
    kbName?: string;
    relativePath?: string;
    sourcePath?: string;
    fileSize?: number;
    /** Unix epoch milliseconds (UTC); rendered via ``new Date(ms)``. */
    importedAt?: number;
  };
  /** Per-doc parser lifecycle data. Rendered as a "解析记录" section
   *  between the meta block and the action buttons. Omit to hide the
   *  section (legacy callers). All fields independently optional —
   *  the section degrades gracefully when only some are present. */
  parse?: {
    /** Final/current engine (e.g. ``light_local``, ``mineru``). */
    parserMode?: string | null;
    /** Full attempt history. The last entry is the most recent. */
    attempts?: DocumentParserAttempt[];
    /** Last error code (``PARSE_ERROR`` etc.). Defensive — most
     *  callers only need ``lastErrorMessage``. */
    lastErrorCode?: string | null;
    /** Most recent error message. Shown in a dedicated block when
     *  status is ``failed``; suppressed otherwise (it's noise once
     *  the doc is ready). */
    lastErrorMessage?: string | null;
  };
  onDelete?: () => void;
  onRegenerate?: () => void;
}

function _formatAttemptTime(iso: string): string {
  // Compact ``HH:mm:ss`` — the doc has its own importedAt above so
  // the day part would just be visual noise on most attempts.
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString();
  } catch {
    return iso;
  }
}

export const DocumentDetailPanel = ({
  doc,
  meta,
  parse,
  onDelete,
  onRegenerate,
}: DocumentDetailPanelProps) => {
  const { t } = useI18n();
  // Show the parse section as long as there's anything meaningful to
  // surface — either a current engine, an attempt history, or a
  // last-error to explain a failure. Skip the section entirely for
  // ready docs that never had a failed attempt (no story to tell).
  const hasParseInfo = !!(
    parse &&
    (parse.parserMode ||
      (parse.attempts && parse.attempts.length > 0) ||
      parse.lastErrorMessage)
  );
  const attempts = parse?.attempts ?? [];
  const isProcessing = doc.status === "indexing" || doc.status === "queued";
  const isFailed = doc.status === "failed";
  return (
    <div className={cn("flex h-full flex-col")}>
      <div className="flex h-12 shrink-0 items-center px-5">
        <span className="truncate text-[15px] font-medium text-ink-heading">
          {t("knowledge.docDetail")}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto px-4 pb-4 pt-1">
        <div className="mb-4">
          <div className="text-sm font-medium text-ink-heading">{doc.name}</div>
          <div className="mt-2 flex items-center gap-2">
            <Badge variant="outline">{doc.format}</Badge>
            <Badge
              variant={
                doc.status === "ready"
                  ? "success"
                  : doc.status === "indexing"
                    ? "brand"
                    : doc.status === "failed"
                      ? "error"
                      : doc.status === "missing"
                        ? "warning"
                        : "outline"
              }
            >
              {doc.status === "ready"
                ? t("knowledge.statusReady")
                : doc.status === "indexing"
                  ? t("knowledge.indexing")
                  : doc.status === "failed"
                    ? t("common.failed")
                    : doc.status === "missing"
                      ? t("knowledge.statusSourceMissing")
                      : t("knowledge.statusWaiting")}
            </Badge>
          </div>
        </div>

        {doc.preview ? (
          <div className="mb-4 rounded-lg border border-surface-border bg-surface-soft py-3 pl-3 pr-0">
            <div className="pr-3 text-[10px] uppercase tracking-[0.7px] text-ink-section">
              {t("knowledge.preview")}
            </div>
            <div className="mt-1.5 max-h-[320px] overflow-y-auto pr-3 text-xs leading-5 text-ink-body whitespace-pre-wrap break-words">
              {doc.preview.length > 2000
                ? `${doc.preview.slice(0, 2000)}…`
                : doc.preview}
            </div>
          </div>
        ) : null}

        {meta ? (
          <div className="mb-4 space-y-3">
            {meta.kbName ? (
              <div>
                <div className="text-[10px] uppercase tracking-[0.7px] text-ink-section">
                  {t("knowledge.parentKb")}
                </div>
                <div className="mt-1 flex items-center gap-1.5 text-xs text-ink-heading">
                  <FolderOpen className="h-3 w-3 text-ink-muted" />
                  {meta.kbName}
                </div>
              </div>
            ) : null}
            {meta.relativePath ? (
              <div>
                <div className="text-[10px] uppercase tracking-[0.7px] text-ink-section">
                  {t("knowledge.kbPath")}
                </div>
                <div className="mt-1 text-xs text-ink-heading font-mono">
                  {meta.relativePath}
                </div>
              </div>
            ) : null}
            {meta.sourcePath ? (
              <div>
                <div className="text-[10px] uppercase tracking-[0.7px] text-ink-section">
                  {t("knowledge.sourcePath")}
                </div>
                <div
                  className="mt-1 truncate text-xs text-ink-body font-mono"
                  title={meta.sourcePath}
                >
                  {meta.sourcePath}
                </div>
              </div>
            ) : null}
            {meta.fileSize != null ? (
              <div>
                <div className="text-[10px] uppercase tracking-[0.7px] text-ink-section">
                  {t("knowledge.fileSize")}
                </div>
                <div className="mt-1 text-xs text-ink-heading">
                  {meta.fileSize < 1024
                    ? `${meta.fileSize} B`
                    : meta.fileSize < 1024 * 1024
                      ? `${(meta.fileSize / 1024).toFixed(1)} KB`
                      : `${(meta.fileSize / (1024 * 1024)).toFixed(1)} MB`}
                </div>
              </div>
            ) : null}
            {meta.importedAt ? (
              <div>
                <div className="text-[10px] uppercase tracking-[0.7px] text-ink-section">
                  {t("knowledge.importTime")}
                </div>
                <div className="mt-1 text-xs text-ink-heading">
                  {new Date(meta.importedAt).toLocaleString("zh-CN", {
                    year: "numeric",
                    month: "2-digit",
                    day: "2-digit",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {hasParseInfo ? (
          <div className="mb-4 space-y-2">
            <div className="text-[10px] uppercase tracking-[0.7px] text-ink-section">
              {t("knowledge.parseHistory")}
            </div>
            {parse?.parserMode || isProcessing ? (
              <div className="flex items-center gap-2 text-xs text-ink-heading">
                {isProcessing ? (
                  <Loader2 className="h-3 w-3 shrink-0 animate-spin text-brand" />
                ) : null}
                <span>
                  {t("knowledge.parserEngine")}:{" "}
                  <span className="font-mono">
                    {parse?.parserMode ?? t("knowledge.parserEnginePending")}
                  </span>
                </span>
                {attempts.length > 0 ? (
                  <span className="text-ink-meta">
                    ·{" "}
                    {t("knowledge.parserAttempts", {
                      count: String(attempts.length),
                    })}
                  </span>
                ) : null}
              </div>
            ) : null}

            {attempts.length > 0 ? (
              <ol className="space-y-1.5 rounded-md border border-surface-border bg-surface-soft px-3 py-2">
                {attempts.map((a, idx) => {
                  // Each ``ParserAttempt`` is one plugin run: ``ok``
                  // entries succeeded (green check, no error line); the
                  // rest failed / fell back (red X + upstream error).
                  // The list reads as a timeline — e.g. "mineru ✗ →
                  // light_local ✓".
                  return (
                    <li
                      key={`${a.pluginId}-${a.occurredAt}-${idx}`}
                      className="flex items-start gap-2 text-[11px]"
                    >
                      {a.ok ? (
                        <CheckCircle2 className="mt-0.5 h-3 w-3 shrink-0 text-success-text" />
                      ) : (
                        <XCircle className="mt-0.5 h-3 w-3 shrink-0 text-error-text" />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline gap-2">
                          <span className="font-mono text-ink-heading">
                            {a.pluginId}
                          </span>
                          <span className="text-ink-meta/80">
                            {_formatAttemptTime(a.occurredAt)}
                          </span>
                        </div>
                        {a.error ? (
                          <div className="mt-0.5 break-words text-ink-body">
                            {a.error}
                          </div>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ol>
            ) : null}

            {isFailed && parse?.lastErrorMessage ? (
              <div className="flex items-start gap-2 rounded-md border border-error-text/30 bg-error-text/5 px-3 py-2 text-[11px] text-ink-body">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-error-text" />
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-error-text">
                    {parse.lastErrorCode || t("knowledge.parserLastError")}
                  </div>
                  <div className="mt-0.5 break-words font-mono">
                    {parse.lastErrorMessage}
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="mt-6 space-y-2">
          {onRegenerate ? (
            <Button
              variant="outline"
              size="sm"
              className="w-full justify-start"
              onClick={onRegenerate}
            >
              <RotateCw className="h-3.5 w-3.5" />
              {t("knowledge.rebuildIndex")}
            </Button>
          ) : null}
          {onDelete ? (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start text-error-text hover:text-error-text"
              onClick={onDelete}
            >
              <Trash2 className="h-3.5 w-3.5" />
              {t("knowledge.deleteDoc")}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
};

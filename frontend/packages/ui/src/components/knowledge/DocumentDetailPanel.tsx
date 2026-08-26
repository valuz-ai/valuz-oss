import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Loader2,
  RotateCw,
  Trash2,
  XCircle,
} from "lucide-react";
import { getLocale } from "@valuz/shared/i18n";
import { cn } from "../../lib/cn";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { useI18n } from "../../hooks/use-i18n";
import { ArtifactRenderer } from "../artifacts/ArtifactViewerShell";
import type {
  ArtifactContent,
  ArtifactDescriptor,
} from "../artifacts/artifact-viewer.types";

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

/** One window of a document's parsed text, as the docs API returns it. */
export interface DocumentPreviewSlice {
  markdown: string;
  truncated: boolean;
}

export interface DocumentDetailPanelProps {
  doc: {
    name: string;
    format: string;
    status: string;
    chunks?: number;
    preview?: DocumentPreviewSlice;
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
  /** Open the ORIGINAL file — the uploaded pdf/xlsx/…, as opposed to the
   *  parsed markdown the preview below shows. */
  onViewSource?: () => void;
}

function _formatAttemptTime(iso: string): string {
  // Compact ``HH:mm:ss`` — the doc has its own importedAt above so
  // the day part would just be visual noise on most attempts.
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString(getLocale(), {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return iso;
  }
}

/** The parsed markdown, dressed as an artifact for the shared viewer. */
function _previewArtifact(name: string): ArtifactDescriptor {
  return {
    id: `kb-preview:${name}`,
    kind: "file",
    name,
    previewKind: "markdown",
    capabilities: {
      canPreview: true,
      canEdit: false,
      canOpenExternal: false,
      canCopyContent: true,
      canDownload: false,
    },
  };
}

function _previewContent(preview: DocumentPreviewSlice): ArtifactContent {
  return {
    kind: "text",
    encoding: "utf-8",
    content: preview.markdown,
    // Measured by the server, not asserted here. This was a hardcoded
    // ``false`` on text read whole off disk, and one 1.05 MB spreadsheet
    // preview was enough to hang the tab — the flag claimed completeness for
    // something nothing had bounded.
    truncated: preview.truncated,
  };
}

export const DocumentDetailPanel = ({
  doc,
  meta,
  parse,
  onDelete,
  onRegenerate,
  onViewSource,
}: DocumentDetailPanelProps) => {
  const { t } = useI18n();
  // The attempt history is a support artifact; the latest entry answers the
  // common question ("what parsed this / why did it fail") and the rest is
  // behind 查看全部.
  const [showAllAttempts, setShowAllAttempts] = useState(false);
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
  const attempts = [...(parse?.attempts ?? [])].reverse(); // latest first
  const visibleAttempts = showAllAttempts ? attempts : attempts.slice(0, 1);
  const isProcessing = doc.status === "indexing" || doc.status === "queued";
  const isFailed = doc.status === "failed";
  return (
    <div className={cn("flex h-full min-h-0 flex-col")}>
      {/* Defensive, and deliberately not the fix for anything: a ``flex-1``
          item in a column flex defaults to ``min-height: auto`` and a scroll
          container with no ``overscroll-behavior`` hands its overflow to
          whatever ancestor can take it. Neither was what made the shell
          scroll — that was an absolutely-positioned ``sr-only`` node escaping
          its clip, fixed in ``MarkdownContent``. These stay so a future
          scrollable ancestor cannot resurrect the symptom. */}
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 pb-5 pt-4">
        <div className="mb-3">
          <div className="flex items-start gap-1">
            <div className="min-w-0 flex-1 wrap-anywhere text-sm font-medium text-ink-heading">
              {doc.name}
            </div>
            {onViewSource ? (
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                title={t("knowledge.viewSourceFile")}
                aria-label={t("knowledge.viewSourceFile")}
                onClick={onViewSource}
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </Button>
            ) : null}
            {onRegenerate ? (
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0"
                title={t("knowledge.rebuildIndex")}
                aria-label={t("knowledge.rebuildIndex")}
                onClick={onRegenerate}
              >
                <RotateCw className="h-3.5 w-3.5" />
              </Button>
            ) : null}
            {onDelete ? (
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0 text-error-text hover:text-error-text"
                title={t("knowledge.deleteDoc")}
                aria-label={t("knowledge.deleteDoc")}
                onClick={onDelete}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            ) : null}
          </div>
          {/* One strip carries every scalar fact — type, size, import time,
              index status — so the parsed content below gets the panel. The
              old layout spent ~15 stacked rows on these and pushed the
              preview off screen. */}
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-ink-body">
            <Badge variant="outline">{doc.format}</Badge>
            {meta?.fileSize != null ? (
              <span>
                {meta.fileSize < 1024
                  ? `${meta.fileSize} B`
                  : meta.fileSize < 1024 * 1024
                    ? `${(meta.fileSize / 1024).toFixed(1)} KB`
                    : `${(meta.fileSize / (1024 * 1024)).toFixed(1)} MB`}
              </span>
            ) : null}
            {meta?.importedAt ? (
              <span className="text-ink-meta">
                {new Date(meta.importedAt).toLocaleString(getLocale(), {
                  year: "numeric",
                  month: "2-digit",
                  day: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                  hour12: false,
                })}
              </span>
            ) : null}
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
          <section className="mt-3 flex min-h-0 flex-col">
            {/* The system file viewer (artifacts / reader), framed like every
                other embedded document surface. No section label — the frame
                and the viewer's own kind row already say what this is. */}
            <div className="overflow-hidden rounded-[14px] border border-surface-border bg-surface">
              <ArtifactRenderer
                artifact={_previewArtifact(doc.name)}
                content={_previewContent(doc.preview)}
              />
            </div>
          </section>
        ) : null}
        {hasParseInfo ? (
          <div className="mt-4 space-y-2 border-t border-surface-border pt-4">
            <div className="flex items-baseline justify-between">
              <div className="text-2xs font-medium text-ink-section">
                {t("knowledge.parseHistory")}
              </div>
              {attempts.length > 1 ? (
                <button
                  type="button"
                  className="text-2xs text-ink-meta transition-colors hover:text-ink-body"
                  onClick={() => setShowAllAttempts((v) => !v)}
                >
                  {showAllAttempts
                    ? t("common.collapse")
                    : `${t("knowledge.parseHistoryAll")} (${attempts.length})`}
                </button>
              ) : null}
            </div>
            {parse?.parserMode || isProcessing ? (
              <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-heading">
                {isProcessing ? (
                  <Loader2 className="h-3 w-3 shrink-0 animate-spin text-brand" />
                ) : null}
                <span>
                  {t("knowledge.parserEngine")}:{" "}
                  <span className="font-mono">
                    {parse?.parserMode ?? t("knowledge.parserEnginePending")}
                  </span>
                </span>
              </div>
            ) : null}

            {visibleAttempts.length > 0 ? (
              <ol className="space-y-1.5 rounded-md border border-surface-border bg-surface-soft px-3 py-2">
                {visibleAttempts.map((a, idx) => {
                  // One plugin run per entry; latest first when collapsed.
                  return (
                    <li
                      key={`${a.pluginId}-${a.occurredAt}-${idx}`}
                      className="flex items-start gap-2 text-2xs"
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
              <div className="flex items-start gap-2 rounded-md border border-error-text/30 bg-error-text/5 px-3 py-2 text-2xs text-ink-body">
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
      </div>
    </div>
  );
};

import { useState, useMemo, type FC } from "react";
import { ChevronDown, ExternalLink, FileEdit, AlertCircle } from "lucide-react";
import type { TurnDiffSummary } from "./diff-aggregator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "../ui/sheet";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface TurnDiffSummaryCardProps {
  summary: TurnDiffSummary;
  /** Called when the user clicks the per-row external-link icon. The
   *  desktop app wires this to ``shell.showItemInFolder`` via IPC; the
   *  webui simply omits the prop and the icon hides. */
  onRevealFile?: (filePath: string) => void;
}

interface UnifiedDiffViewProps {
  diff: string;
}

/**
 * Render a unified-diff string with simple +/- background coloring.
 * No syntax highlighting, no line numbers — the goal is a quick visual
 * scan, not a full code-review surface.
 */
const UnifiedDiffView: FC<UnifiedDiffViewProps> = ({ diff }) => {
  const lines = useMemo(() => diff.split("\n"), [diff]);
  return (
    <div className="overflow-x-auto rounded-md border border-surface-border bg-surface-soft text-2xs">
      <pre className="m-0 px-3 py-2 font-mono leading-relaxed">
        {lines.map((line, idx) => {
          // Skip the patch's own file headers — they were stripped by
          // ``createPatch`` to "" but the leading "===" + "---"/"+++"
          // separators still appear; render them muted so they don't
          // visually compete with the actual content.
          if (line.startsWith("+++") || line.startsWith("---")) {
            return (
              <span
                key={idx}
                className="block whitespace-pre-wrap text-ink-meta"
              >
                {line || " "}
              </span>
            );
          }
          if (line.startsWith("@@")) {
            return (
              <span
                key={idx}
                className="block whitespace-pre-wrap text-brand/80"
              >
                {line}
              </span>
            );
          }
          if (line.startsWith("+")) {
            return (
              <span
                key={idx}
                className="block whitespace-pre-wrap bg-success/10 text-success"
              >
                {line || " "}
              </span>
            );
          }
          if (line.startsWith("-")) {
            return (
              <span
                key={idx}
                className="block whitespace-pre-wrap bg-error-light text-error-text"
              >
                {line || " "}
              </span>
            );
          }
          return (
            <span key={idx} className="block whitespace-pre-wrap text-ink-body">
              {line || " "}
            </span>
          );
        })}
      </pre>
    </div>
  );
};

export const TurnDiffSummaryCard: FC<TurnDiffSummaryCardProps> = ({
  summary,
  onRevealFile,
}) => {
  const { t } = useI18n();
  // Card-level fold — independent of the segment "已处理 N 秒" fold the
  // turn list owns. Header (totals + 审核) stays visible; the file-row
  // body collapses behind the header chevron.
  const [cardOpen, setCardOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // Right-side drawer holds the full multi-file diff view. Stays
  // accessible whether the card body is folded or not — clicking
  // 审核 in the header is the canonical "show me everything" gesture.
  const [reviewOpen, setReviewOpen] = useState(false);

  const toggleRow = (filePath: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(filePath)) next.delete(filePath);
      else next.add(filePath);
      return next;
    });
  };

  return (
    <div
      className="rounded-xl border border-surface-border bg-surface text-xs"
      data-testid="turn-diff-summary"
    >
      <div
        className={cn(
          "flex items-center gap-2 px-3 py-2",
          cardOpen && "border-b border-surface-border",
        )}
      >
        <FileEdit className="h-3.5 w-3.5 shrink-0 text-ink-meta" />
        <span className="font-medium text-ink-heading">
          {t("conversation.filesChanged", "{count} 个文件已更改", {
            count: summary.changes.length,
          })}
        </span>
        <span className="ml-1 font-mono text-success">
          +{summary.total_additions}
        </span>
        <span className="font-mono text-error-text">
          −{summary.total_deletions}
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => setReviewOpen(true)}
          className="rounded-md px-2 py-0.5 text-2xs text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-heading"
          data-testid="turn-diff-review"
        >
          {t("conversation.review", t("conversation.review"))}
        </button>
        <button
          type="button"
          onClick={() => setCardOpen((v) => !v)}
          className="shrink-0 rounded p-1 text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-heading"
          aria-label={
            cardOpen ? t("common.collapse", t("common.collapse")) : t("common.expand", t("common.expand"))
          }
          data-testid="turn-diff-card-toggle"
        >
          <ChevronDown
            className={cn(
              "h-3 w-3 transition-transform",
              !cardOpen && "-rotate-90",
            )}
          />
        </button>
      </div>
      {cardOpen ? (
        <div data-testid="turn-diff-body">
          {summary.changes.map((change, idx) => {
            const isOpen = expanded.has(change.file_path);
            const isLast = idx === summary.changes.length - 1;
            return (
              <div
                key={change.file_path}
                className={cn("border-surface-border", !isLast && "border-b")}
              >
                <div className="flex items-center gap-2 px-3 py-2">
                  <button
                    type="button"
                    onClick={() => toggleRow(change.file_path)}
                    className="flex shrink-0 items-center text-ink-meta hover:text-ink-heading"
                    aria-label={
                      isOpen
                        ? t("common.collapse", t("common.collapse"))
                        : t("common.expand", t("common.expand"))
                    }
                  >
                    <ChevronDown
                      className={cn(
                        "h-3 w-3 transition-transform",
                        !isOpen && "-rotate-90",
                      )}
                    />
                  </button>
                  {/* Truncate-from-start: ``rtl`` direction with
                    ``unicode-bidi: plaintext`` keeps the leaf filename
                    visible and ellipsises the leading path the way
                    Cursor does. ``min-w-0`` is mandatory for flex
                    truncation to engage. */}
                  <span
                    className="block min-w-0 flex-1 overflow-hidden font-mono text-ink-body"
                    style={{
                      direction: "rtl",
                      textAlign: "left",
                      unicodeBidi: "plaintext",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={change.file_path}
                  >
                    {change.file_path}
                  </span>
                  {change.has_error ? (
                    <span className="flex shrink-0 items-center gap-1 rounded-md bg-error-light px-1.5 py-0.5 text-2xs text-error-text">
                      <AlertCircle className="h-2.5 w-2.5" />
                      {t("common.failed", t("common.failed"))}
                    </span>
                  ) : null}
                  <span className="shrink-0 font-mono text-2xs text-success">
                    +{change.additions}
                  </span>
                  <span className="shrink-0 font-mono text-2xs text-error-text">
                    −{change.deletions}
                  </span>
                  {onRevealFile ? (
                    <button
                      type="button"
                      onClick={() => onRevealFile(change.file_path)}
                      className="shrink-0 rounded p-1 text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-heading"
                      aria-label={t(
                        "conversation.showInFinder",
                        t("conversation.showInFinder"),
                      )}
                    >
                      <ExternalLink className="h-3 w-3" />
                    </button>
                  ) : null}
                </div>
                {isOpen ? (
                  <div className="px-3 pb-2">
                    <UnifiedDiffView diff={change.unified_diff} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
      <Sheet open={reviewOpen} onOpenChange={setReviewOpen}>
        <SheetContent
          side="right"
          // Override the default ``sm:max-w-sm`` — diffs need more room to
          // breathe than the default narrow side panel allows. ``max-w-3xl``
          // keeps the panel readable on wide displays without occupying the
          // whole viewport.
          className="w-full sm:max-w-3xl"
          data-testid="turn-diff-review-drawer"
        >
          <SheetHeader>
            <SheetTitle>
              {t("conversation.reviewDiff", "审核 · {count} 个文件", {
                count: summary.changes.length,
              })}{" "}
              · +{summary.total_additions} −{summary.total_deletions}
            </SheetTitle>
            <SheetDescription className="sr-only">
              {t(
                "conversation.reviewDiffDesc",
                "逐文件查看本轮对话产生的代码差异。",
              )}
            </SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
            <div className="space-y-4">
              {summary.changes.map((change) => (
                <div
                  key={change.file_path}
                  className="rounded-lg border border-surface-border bg-surface"
                >
                  <div className="flex items-center gap-2 border-b border-surface-border px-3 py-2 text-xs">
                    <span
                      className="block min-w-0 flex-1 overflow-hidden font-mono text-ink-body"
                      style={{
                        direction: "rtl",
                        textAlign: "left",
                        unicodeBidi: "plaintext",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={change.file_path}
                    >
                      {change.file_path}
                    </span>
                    {change.has_error ? (
                      <span className="flex shrink-0 items-center gap-1 rounded-md bg-error-light px-1.5 py-0.5 text-2xs text-error-text">
                        <AlertCircle className="h-2.5 w-2.5" />
                        {t("common.failed", t("common.failed"))}
                      </span>
                    ) : null}
                    <span className="shrink-0 font-mono text-2xs text-success">
                      +{change.additions}
                    </span>
                    <span className="shrink-0 font-mono text-2xs text-error-text">
                      −{change.deletions}
                    </span>
                    {onRevealFile ? (
                      <button
                        type="button"
                        onClick={() => onRevealFile(change.file_path)}
                        className="shrink-0 rounded p-1 text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-heading"
                        aria-label={t(
                          "conversation.showInFinder",
                          t("conversation.showInFinder"),
                        )}
                      >
                        <ExternalLink className="h-3 w-3" />
                      </button>
                    ) : null}
                  </div>
                  <div className="px-3 py-2">
                    <UnifiedDiffView diff={change.unified_diff} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
};

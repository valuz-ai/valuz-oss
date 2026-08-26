import {
  memo,
  useCallback,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
} from "react";
import type {
  CitationBundleV1,
  CitationClaimAuditV1,
  CitationQualityIssueV1,
  ClaimLocationV1,
  OpenCitationInput,
} from "@valuz/shared";
import { Streamdown, defaultUrlTransform, type UrlTransform } from "streamdown";
import { code } from "@streamdown/code";
import { mermaid } from "@streamdown/mermaid";
import { math } from "@streamdown/math";
import { cjk } from "@streamdown/cjk";
import {
  AlertTriangle,
  Calculator,
  Check,
  Copy,
  Download,
  ExternalLink,
  Link2Off,
  Loader2,
  Maximize,
  RotateCcw,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import "streamdown/styles.css";
import "katex/dist/katex.min.css";

import { cn } from "../../lib/cn";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Button } from "../ui/button";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "../ui/hover-card";
import { useI18n } from "../../hooks/use-i18n";
import {
  citationDisplayOrder,
  citationIdFromHref,
  offsetAfterEnclosingText,
  citationOccurrences,
  citationOffsetFromHref,
  CitationPill,
  CitationSourceCards,
  projectCitationTextCorrections,
  projectCitationSidecarAnchors,
  projectEvidenceMarkdownLinks,
  rewriteCitationMarkdownLinks,
  stripStreamingEvidenceLinkTail,
  type CitationQualityDisplayIssue,
} from "./CitationInline";

/** Icon overrides so Streamdown's built-in toolbar buttons (copy /
 * download / fullscreen / etc.) draw from the same lucide set we use
 * everywhere else. Without this, Streamdown's defaults (its own SVG
 * set) render at a slightly different stroke weight / proportion,
 * which is jarring next to the lucide icons we put in our own panel
 * headers. Keys must match Streamdown's ``IconMap`` interface. */
const STREAMDOWN_ICONS = {
  CheckIcon: Check,
  CopyIcon: Copy,
  DownloadIcon: Download,
  ExternalLinkIcon: ExternalLink,
  Loader2Icon: Loader2,
  Maximize2Icon: Maximize,
  RotateCcwIcon: RotateCcw,
  XIcon: X,
  ZoomInIcon: ZoomIn,
  ZoomOutIcon: ZoomOut,
};

const LOCAL_FILE_HREF_PREFIX = "https://valuz.local-file.invalid/";
const QUALITY_CLAIM_HREF_PREFIX = "https://valuz.quality-claim.invalid/";

interface LocalizedClaimQualityEntry {
  targetId: string;
  claimId?: string;
  exact: string;
  location?: ClaimLocationV1;
  citationIds: string[];
  issues: CitationQualityDisplayIssue[];
}

const CRITICAL_CITATION_ISSUE_CODES = new Set([
  "claim_evidence_conflict",
  "claim_source_entity_conflict",
  "claim_source_period_conflict",
  "structured_source_conflict",
  "cross_source_value_conflict",
  "conflicting_values_must_not_be_averaged",
  "calculation_result_mismatch",
  "calculation_input_value_mismatch",
  "calculation_input_unit_mismatch",
  "calculation_input_metric_mismatch",
  "calculation_input_entity_mismatch",
  "calculation_input_scope_mismatch",
  "calculation_input_basis_mismatch",
  "calculation_input_period_mismatch",
  "claim_before_evidence_coverage",
  "claim_after_evidence_coverage",
]);

/** Statements the audit found carrying no binding at all. They never have a
 * citation card to hang off, so they are marked in place instead of being
 * dropped — a reader otherwise cannot tell a fully sourced answer from one
 * where every number is unsourced. */
const UNSOURCED_CITATION_ISSUE_CODES = new Set([
  "claim_without_citation",
  "numeric_claim_without_citation",
  "date_claim_without_citation",
]);
const LOCAL_CONFLICT_VERIFIER_REVISION = "claim-verifier-local-v3";

function qualityIssueTone(
  issue: CitationQualityIssueV1,
  verifierRevision?: string,
): CitationQualityDisplayIssue["tone"] {
  if (
    verifierRevision !== LOCAL_CONFLICT_VERIFIER_REVISION &&
    ["claim_source_entity_conflict", "claim_source_period_conflict"].includes(
      issue.code,
    )
  ) {
    // v2 allowed remote inherited context and multi-period normalization to
    // prove a hard conflict. Keep legacy results inspectable, but never retain
    // their orange warning treatment after the verifier was corrected.
    return "advisory";
  }
  if (CRITICAL_CITATION_ISSUE_CODES.has(issue.code)) {
    return "critical";
  }
  return UNSOURCED_CITATION_ISSUE_CODES.has(issue.code)
    ? "unsourced"
    : "advisory";
}

function claimEvidenceSummary(
  citation: CitationBundleV1["citations"][number],
): string {
  const evidence = citation.evidence;
  if (evidence.kind === "text") {
    const text = (evidence.quote || evidence.snippet).trim();
    return text.length > 420 ? `${text.slice(0, 420).trimEnd()}…` : text;
  }
  if (evidence.kind === "structured-data") {
    return `${readableEvidenceField(evidence.field)}: ${String(evidence.value)}${
      evidence.unit ? ` ${evidence.unit}` : ""
    }`;
  }
  return `${evidence.expression} = ${String(evidence.result)}${
    evidence.unit ? ` ${evidence.unit}` : ""
  }`;
}

function ClaimQualityMarker({
  entry,
  citationsById,
  citationOrder,
  messageId,
  citationMessageIdByCitationId,
  onCitationClick,
}: {
  entry: LocalizedClaimQualityEntry;
  citationsById: ReadonlyMap<string, CitationBundleV1["citations"][number]>;
  citationOrder: ReadonlyMap<string, number>;
  messageId?: string;
  citationMessageIdByCitationId?: ReadonlyMap<string, string | undefined>;
  onCitationClick?: (input: OpenCitationInput) => void;
}) {
  const { t } = useI18n();
  const label = entry.issues.map((issue) => issue.label).join("；");
  const citations = entry.citationIds.flatMap((citationId) => {
    const citation = citationsById.get(citationId);
    return citation ? [{ citationId, citation }] : [];
  });
  // A coverage gap is not a defect claim. Reserve the warning palette for
  // entries that carry a real conflict so an unsourced sentence reads as
  // "no source attached" rather than "this looks wrong".
  const unsourcedOnly = entry.issues.every(
    (issue) => issue.tone === "unsourced",
  );
  const hasCriticalIssue = entry.issues.some(
    (issue) => issue.tone === "critical",
  );

  return (
    <HoverCard openDelay={0} closeDelay={180}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          data-citation-claim-quality
          data-citation-claim-tone={
            hasCriticalIssue ? "critical" : "unsourced"
          }
          data-quality-claim-id={entry.targetId}
          aria-label={`${
            unsourcedOnly
              ? t("ui.citation.qualityClaimUnsourcedTitle")
              : t("ui.citation.qualityNeedsReview")
          } · ${label}`}
          className={
            hasCriticalIssue
              ? "relative -top-px mx-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full border border-warning/50 bg-warning-light/70 align-middle text-warning-text no-underline transition hover:bg-warning-light focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warning/20"
              : "relative -top-px mx-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full border border-surface-border bg-surface-soft align-middle text-ink-muted no-underline transition hover:bg-surface-soft/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/20"
          }
        >
          {hasCriticalIssue ? (
            <AlertTriangle className="h-2.5 w-2.5" aria-hidden="true" />
          ) : (
            <Link2Off className="h-2.5 w-2.5" aria-hidden="true" />
          )}
        </button>
      </HoverCardTrigger>
      <HoverCardContent
        data-citation-claim-quality-card
        side="bottom"
        sideOffset={8}
        className="max-h-[min(420px,calc(100vh-32px))] w-[min(380px,calc(100vw-32px))] overflow-y-auto rounded-lg border-surface-border bg-surface p-3 text-xs text-ink-body shadow-xl"
      >
        <div
          className={
            hasCriticalIssue
              ? "flex items-center gap-1.5 font-medium text-warning-text"
              : "flex items-center gap-1.5 font-medium text-ink-heading"
          }
        >
          {hasCriticalIssue ? (
            <AlertTriangle
              className="h-3.5 w-3.5 shrink-0"
              aria-hidden="true"
            />
          ) : (
            <Link2Off className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          )}
          <span>
            {unsourcedOnly
              ? t("ui.citation.qualityClaimUnsourcedTitle")
              : t("ui.citation.qualityCheckTitle")}
          </span>
        </div>
        <div
          className={
            hasCriticalIssue
              ? "mt-2 border-l-2 border-warning/40 pl-2.5 leading-5 text-ink-heading"
              : "mt-2 border-l-2 border-surface-border pl-2.5 leading-5 text-ink-heading"
          }
        >
          {entry.exact}
        </div>
        <ul className="mt-2 space-y-1 pl-4 leading-5">
          {entry.issues.map((issue) => (
            <li key={`${issue.label}:${issue.severity}`} className="list-disc">
              {issue.label}
            </li>
          ))}
        </ul>
        {citations.length ? (
          <div className="mt-3 border-t border-surface-border pt-2">
            <div className="text-2xs font-medium text-ink-meta">
              {t("ui.citation.evidenceTitle")}
            </div>
            <div className="mt-1.5 divide-y divide-surface-border">
              {citations.map(({ citationId, citation }) => {
                const displayIndex = citationOrder.get(citationId);
                const calculationEvidence =
                  citation.evidence.kind === "calculation"
                    ? citation.evidence
                    : undefined;
                const calculation = Boolean(calculationEvidence);
                const canOpen =
                  !calculation &&
                  citation.resolutionStatus !== "forbidden" &&
                  citation.resolutionStatus !== "missing" &&
                  Boolean(onCitationClick);
                return (
                  <div
                    key={citationId}
                    data-citation-claim-evidence={citationId}
                    className="py-2 first:pt-0 last:pb-0"
                  >
                    <div className="flex items-start gap-1.5">
                      {calculation ? (
                        <Calculator
                          className="mt-0.5 h-3 w-3 shrink-0 text-ink-meta"
                          aria-hidden="true"
                        />
                      ) : null}
                      <div className="min-w-0 flex-1">
                        <div className="font-medium text-ink-heading">
                          {displayIndex ? `${displayIndex} ` : ""}
                          {citation.source.title}
                        </div>
                        <div className="mt-1 line-clamp-4 whitespace-pre-wrap leading-5 text-ink-meta">
                          {claimEvidenceSummary(citation)}
                        </div>
                      </div>
                    </div>
                    {calculationEvidence?.inputs.length ? (
                      <div className="mt-1.5 space-y-1 border-t border-surface-border pt-1.5 text-2xs text-ink-meta">
                        {calculationEvidence.inputs.map((input) => {
                          const inputCitation = citationsById.get(
                            input.citationId,
                          );
                          const inputCanOpen =
                            Boolean(inputCitation) &&
                            inputCitation?.evidence.kind !== "calculation" &&
                            inputCitation?.resolutionStatus !== "forbidden" &&
                            inputCitation?.resolutionStatus !== "missing" &&
                            Boolean(onCitationClick);
                          const inputLabel = `${input.name}: ${String(input.value)}${
                            input.unit ? ` ${input.unit}` : ""
                          }`;
                          return inputCanOpen ? (
                            <button
                              key={`${input.name}:${input.citationId}`}
                              type="button"
                              className="block w-full rounded px-1 py-0.5 text-left hover:bg-surface-soft"
                              onClick={(event) => {
                                event.stopPropagation();
                                onCitationClick?.({
                                  messageId:
                                    citationMessageIdByCitationId?.get(
                                      input.citationId,
                                    ) ?? messageId,
                                  citationId: input.citationId,
                                });
                              }}
                            >
                              {inputLabel}
                              {inputCitation
                                ? ` · ${inputCitation.source.title}`
                                : ""}
                            </button>
                          ) : (
                            <div
                              key={`${input.name}:${input.citationId}`}
                              className="px-1 py-0.5"
                            >
                              {inputLabel}
                              {inputCitation
                                ? ` · ${inputCitation.source.title}`
                                : ""}
                            </div>
                          );
                        })}
                      </div>
                    ) : null}
                    {canOpen ? (
                      <button
                        type="button"
                        className="mt-1.5 inline-flex items-center gap-1 font-medium text-primary hover:underline"
                        onClick={(event) => {
                          event.stopPropagation();
                          onCitationClick?.({
                            messageId:
                              citationMessageIdByCitationId?.get(citationId) ??
                              messageId,
                            citationId,
                          });
                        }}
                      >
                        {t("ui.citation.viewEvidence", "View evidence")}
                        <ExternalLink className="h-3 w-3" aria-hidden="true" />
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}
      </HoverCardContent>
    </HoverCard>
  );
}

function readableEvidenceField(field: string): string {
  return (field.split(/[./]/u).at(-1) ?? field)
    .replace(/([a-z0-9])([A-Z])/gu, "$1 $2")
    .replace(/[_-]+/gu, " ")
    .trim();
}

function evidenceValueLabel(
  citation: CitationBundleV1["citations"][number],
): string {
  const evidence = citation.evidence;
  if (evidence.kind !== "structured-data") return "";
  const value = String(evidence.value ?? "");
  const unit = evidence.unit ?? evidence.currency;
  return unit ? `${value} ${unit}` : value;
}

function selectUserFacingQualityIssues(
  issues: CitationQualityDisplayIssue[],
): CitationQualityDisplayIssue[] {
  const hasClaimMismatch = issues.some(
    (issue) => issue.code === "claim_evidence_mismatch",
  );
  const hasCalculationValueMismatch = issues.some(
    (issue) => issue.code === "calculation_input_value_mismatch",
  );
  const seen = new Set<string>();
  return issues.filter((issue) => {
    if (
      hasClaimMismatch &&
      [
        "structured_value_not_present_in_answer",
        "numeric_unit_missing",
        "calculation_input_value_mismatch",
        "calculation_input_unit_mismatch",
      ].includes(issue.code ?? "")
    ) {
      return false;
    }
    if (
      hasCalculationValueMismatch &&
      issue.code === "calculation_input_unit_mismatch"
    ) {
      return false;
    }
    const key = `${issue.claimId ?? ""}\0${issue.label}\0${issue.severity}\0${issue.tone}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function qualityClaimIdFromHref(href?: string): string | null {
  if (!href?.startsWith(QUALITY_CLAIM_HREF_PREFIX)) return null;
  try {
    return decodeURIComponent(href.slice(QUALITY_CLAIM_HREF_PREFIX.length));
  } catch {
    return null;
  }
}

function claimSourceEnd(location?: ClaimLocationV1): number | undefined {
  return location && location.kind !== "legacy"
    ? location.sourceEnd
    : undefined;
}

function citationOccurrenceKey(
  citationId: string,
  sourceOffset: number,
): string {
  return `${citationId}\0${sourceOffset}`;
}

function stripProtocolSourcePlaceholders(content: string): string {
  return content
    .split("\n")
    .map((line) => {
      const match = line.match(
        /(?:[ \t]+|(?<=[。！？；;]))source([.!?。！？；;]?)\s*$/i,
      );
      if (!match || match.index === undefined) return line;
      const prefix = line.slice(0, match.index);
      if (!/[\u4e00-\u9fff]/.test(prefix) && !prefix.includes("citation://")) {
        return line;
      }
      return `${prefix.trimEnd()}${match[1]}`;
    })
    .join("\n")
    .trimEnd();
}

function stripStandaloneCitationLines(content: string): string {
  const citationOnlyLine =
    /^\s*(?:\[[^\]\n]+\]\(<?citation:\/\/[^)>\s]+>?\)[\s,，;；]*)+\s*$/;
  return content
    .split("\n")
    .filter((line) => !citationOnlyLine.test(line))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trimEnd();
}

function stripDecorativeHeadingCitations(content: string): string {
  const citationLink = String.raw`\[[^\]\n]+\]\(<?citation:\/\/[^)>\s]+>?\)`;
  const boldHeading = new RegExp(
    String.raw`^(\s*(?:\*\*|__)([^*_\n]{1,80})(?:\*\*|__))\s*(?:${citationLink}\s*)+$`,
  );
  const markdownHeading = new RegExp(
    String.raw`^(\s*#{1,6}\s+([^\n]{1,80}?))\s*(?:${citationLink}\s*)+$`,
  );
  return content
    .split("\n")
    .map((line) => {
      for (const pattern of [boldHeading, markdownHeading]) {
        const match = line.match(pattern);
        if (match && !/\d/.test(match[2] ?? "")) return match[1] ?? line;
      }
      return line;
    })
    .join("\n");
}

// Numbers (``13.82%``, ``$1,234.5``, ``60``) and words, so a marker can be
// kept out of the middle of one.
const ATOMIC_TOKEN_RE = /\$?\d[\d.,]*\d?%?|[A-Za-z][A-Za-z0-9_-]*/g;

/** Move an offset that fell inside a number or word to the end of it. */
function tokenBoundary(content: string, offset: number): number {
  // A claim's source offset is computed against the text the audit saw. When
  // that text and the rendered text have drifted — an unrewritten protocol
  // link is enough — the offset can land mid-token, and "13.82%" is shown as
  // "1 ⊘ 3.82%", which reads as two different numbers. Snapping forward keeps
  // the marker attached to the whole value.
  ATOMIC_TOKEN_RE.lastIndex = 0;
  for (
    let match = ATOMIC_TOKEN_RE.exec(content);
    match !== null;
    match = ATOMIC_TOKEN_RE.exec(content)
  ) {
    const end = match.index + match[0].length;
    if (match.index >= offset) break;
    if (offset < end) return end;
  }
  return offset;
}

function safeQualityMarkerInsertion(
  content: string,
  requestedOffset: number,
  targetId: string,
): { offset: number; marker: string } {
  // Put the marker behind the text it marks, then make sure that landing
  // spot is not inside a value or a link.
  let offset = tokenBoundary(
    content,
    offsetAfterEnclosingText(content, requestedOffset),
  );
  let movedOutsideBlock = false;
  // Leaving a block means the marker starts its own line; leaving an inline
  // span only needs a space. A newline inside a table row would break the row
  // apart, so the two cases must not share a separator.
  for (const [pattern, isBlock] of [
    [/\$\$[\s\S]*?\$\$/g, true],
    [/\\\[[\s\S]*?\\\]/g, true],
    [/```[\s\S]*?```/g, true],
    // A whole markdown link. The offset is measured against the text the audit
    // saw, but markers are injected after protocol links have been projected
    // into shorter citation links, so it can land inside one — splitting a
    // label into "[sourc ⊘ e]" or a URL into "citation:/ ⊘ /cit_…", which
    // breaks the link outright. Balanced parentheses are matched because a
    // Collection Address' pointer can contain them.
    [/\[[^\]\n]{0,240}\]\((?:[^()\s\n]|\([^()\n]{0,200}\)){1,2100}\)/g, false],
  ] as const) {
    for (const match of content.matchAll(pattern)) {
      const start = match.index;
      const end = start + match[0].length;
      if (start < offset && offset < end) {
        offset = end;
        if (isBlock) {
          movedOutsideBlock = true;
          const followingNewline = content.slice(offset).match(/^[ \t]*\r?\n/);
          if (followingNewline) offset += followingNewline[0].length;
        }
        break;
      }
    }
  }
  const separator = movedOutsideBlock ? "\n" : " ";
  return {
    offset,
    marker: `${separator}[!](<${QUALITY_CLAIM_HREF_PREFIX}${encodeURIComponent(targetId)}>)`,
  };
}

function normalizedMarkdownTableCell(value: string): string {
  return value
    .replace(/\[([^\]\n]+)\]\((?:[^()\s\n]|\([^()\n]*\))+\)/gu, "$1")
    .replace(/[*_`~]/gu, "")
    .replace(/\s+/gu, "")
    .trim();
}

function markdownTableCellSpans(line: string): Array<{ start: number; end: number }> {
  const pipes: number[] = [];
  for (let index = 0; index < line.length; index += 1) {
    if (line[index] === "|" && line[index - 1] !== "\\") pipes.push(index);
  }
  if (pipes.length < 2) return [];
  if (pipes[pipes.length - 1] !== line.length - 1) pipes.push(line.length);
  return pipes.slice(0, -1).map((start, index) => ({
    start: start + 1,
    end: pipes[index + 1]!,
  }));
}

function isMarkdownTableDelimiter(line: string): boolean {
  const spans = markdownTableCellSpans(line);
  return (
    spans.length > 0 &&
    spans.every(({ start, end }) => /^:?-{3,}:?$/u.test(line.slice(start, end).trim()))
  );
}

/** Resolve a table-cell Claim against table structure, never a stale offset.
 *
 * The Guard computes locations after normalising long Evidence Collection
 * addresses. The renderer still starts from the streamed text, then projects
 * those links to Citation ids. A raw source offset can consequently drift into
 * a later table's delimiter row and injecting a marker there invalidates the
 * entire Markdown table. Row/header/value coordinates survive that rewrite,
 * so use them as the only authority for table-cell markers.
 */
function markdownTableCellClaimEnd(
  content: string,
  entry: LocalizedClaimQualityEntry,
): number | undefined {
  const location = entry.location;
  if (location?.kind !== "table-cell") return undefined;
  const match = entry.exact.match(/^(.+?)\s+—\s+(.+?)[:：]\s*([\s\S]+)$/u);
  if (!match) return undefined;
  const [, rowLabel = "", columnLabel = "", claimedValue = ""] = match;
  const expectedRow = normalizedMarkdownTableCell(rowLabel);
  const expectedColumn = normalizedMarkdownTableCell(columnLabel);
  const expectedValue = claimedValue.trim();
  const lines: Array<{ text: string; start: number }> = [];
  let sourceStart = 0;
  for (const text of content.split("\n")) {
    lines.push({ text, start: sourceStart });
    sourceStart += text.length + 1;
  }

  for (let headerIndex = 0; headerIndex + 2 < lines.length; headerIndex += 1) {
    const header = lines[headerIndex]!;
    const delimiter = lines[headerIndex + 1]!;
    const headerSpans = markdownTableCellSpans(header.text);
    if (
      headerSpans.length === 0 ||
      !isMarkdownTableDelimiter(delimiter.text) ||
      location.columnIndex >= headerSpans.length
    ) {
      continue;
    }
    const headerCell = headerSpans[location.columnIndex]!;
    if (
      normalizedMarkdownTableCell(
        header.text.slice(headerCell.start, headerCell.end),
      ) !== expectedColumn
    ) {
      continue;
    }
    const row = lines[headerIndex + 2 + location.rowIndex];
    if (!row) continue;
    const rowSpans = markdownTableCellSpans(row.text);
    if (rowSpans.length <= location.columnIndex) continue;
    const rowCell = rowSpans[0]!;
    if (
      normalizedMarkdownTableCell(row.text.slice(rowCell.start, rowCell.end)) !==
      expectedRow
    ) {
      continue;
    }
    const valueCell = rowSpans[location.columnIndex]!;
    const rawValueCell = row.text.slice(valueCell.start, valueCell.end);
    const valueOffset = rawValueCell.indexOf(expectedValue);
    return (
      row.start +
      (valueOffset >= 0
        ? valueCell.start + valueOffset + expectedValue.length
        : valueCell.end)
    );
  }
  return undefined;
}

function injectQualityClaimMarkers(
  content: string,
  entries: LocalizedClaimQualityEntry[],
): string {
  let result = content;
  const positioned = entries
    .map((entry) => {
      // The claim's own text beats its offset. Offsets are measured against
      // the text the audit judged, which has been normalised and has had its
      // protocol links rewritten, so replaying one against the streamed text
      // can land in a different paragraph — a sentence about FCF margin was
      // marking a cell three sections further down. Locating the sentence is
      // coordinate-independent and lands on the statement by construction.
      // Table-cell claims read "row — column: value" and never appear
      // verbatim, so those still fall back to the offset.
      const tableCellEnd = markdownTableCellClaimEnd(content, entry);
      const found = entry.exact ? content.indexOf(entry.exact) : -1;
      const requestedOffset =
        entry.location?.kind === "table-cell"
          ? tableCellEnd
          : found >= 0
          ? found + entry.exact.length
          : claimSourceEnd(entry.location);
      return {
        entry,
        insertion:
          requestedOffset === undefined
            ? undefined
            : safeQualityMarkerInsertion(
                content,
                requestedOffset,
                entry.targetId,
              ),
      };
    })
    .filter(
      (
        item,
      ): item is {
        entry: LocalizedClaimQualityEntry;
        insertion: { offset: number; marker: string };
      } =>
        item.insertion !== undefined &&
        Number.isInteger(item.insertion.offset) &&
        item.insertion.offset >= 0 &&
        item.insertion.offset <= content.length,
    )
    .sort((left, right) => right.insertion.offset - left.insertion.offset);
  const inserted = new Set<string>();
  for (const { entry, insertion } of positioned) {
    result = `${result.slice(0, insertion.offset)}${insertion.marker}${result.slice(insertion.offset)}`;
    inserted.add(entry.targetId);
  }
  let fallbackCursor = 0;
  for (const entry of entries) {
    if (inserted.has(entry.targetId)) continue;
    const start = result.indexOf(entry.exact, fallbackCursor);
    if (start < 0) continue;
    const insertion = safeQualityMarkerInsertion(
      result,
      start + entry.exact.length,
      entry.targetId,
    );
    result = `${result.slice(0, insertion.offset)}${insertion.marker}${result.slice(insertion.offset)}`;
    fallbackCursor = insertion.offset + insertion.marker.length;
  }
  return result;
}

function encodeLocalFileHref(href: string): string {
  return `${LOCAL_FILE_HREF_PREFIX}${encodeURIComponent(href)}`;
}

function decodeLocalFileHref(href: string): string {
  if (!href.startsWith(LOCAL_FILE_HREF_PREFIX)) return href;
  try {
    return decodeURIComponent(href.slice(LOCAL_FILE_HREF_PREFIX.length));
  } catch {
    return href;
  }
}

function rewriteLocalFileMarkdownLinks(
  content: string,
  isLocalFileHref?: (href: string) => boolean,
): string {
  if (!isLocalFileHref) return content;
  return content.replace(/(\[[^\]\n]+\]\()([^)\n]+)(\))/g, (match) =>
    rewriteMarkdownLinkMatch(match, isLocalFileHref),
  );
}

function rewriteMarkdownLinkMatch(
  match: string,
  isLocalFileHref: (href: string) => boolean,
): string {
  const destinationStart = match.lastIndexOf("(");
  if (destinationStart === -1 || !match.endsWith(")")) return match;

  const prefix = match.slice(0, destinationStart + 1);
  const destination = match.slice(destinationStart + 1, -1);
  return `${prefix}${rewriteMarkdownLinkDestination(
    destination,
    isLocalFileHref,
  )})`;
}

function rewriteMarkdownLinkDestination(
  destination: string,
  isLocalFileHref: (href: string) => boolean,
): string {
  const leading = destination.match(/^\s*/)?.[0] ?? "";
  const trailing = destination.match(/\s*$/)?.[0] ?? "";
  const body = destination.slice(
    leading.length,
    destination.length - trailing.length,
  );
  if (!body) return destination;

  if (body.startsWith("<")) {
    const end = body.indexOf(">");
    if (end <= 0) return destination;
    const href = body.slice(1, end);
    if (!isLocalFileHref(href)) return destination;
    return `${leading}<${encodeLocalFileHref(href)}>${body.slice(
      end + 1,
    )}${trailing}`;
  }

  const [href = "", ...rest] = body.split(/(\s[\s\S]*)/);
  if (!href || !isLocalFileHref(href)) return destination;
  return `${leading}${encodeLocalFileHref(href)}${rest.join("")}${trailing}`;
}

interface MarkdownContentProps {
  content: string;
  className?: string;
  /**
   * Whether the content is still arriving.
   *
   * Streaming is the default because the conversation is the common caller and
   * its markdown grows a token at a time. It is not free: the renderer splits
   * the text into blocks so it can re-render incrementally, and repairs
   * half-written constructs (unclosed fences, a table whose last row stops
   * mid-cell) on every pass. Both are the right thing to do to a prefix of a
   * document and pure overhead on a whole one.
   *
   * A viewer showing a document that is already on disk should say
   * ``"static"``. Correctness first, not speed: repairing a complete document
   * can only alter it — an intentionally unmatched ``*`` gets closed for the
   * author. The measured saving is small and does not scale (1.3x on a
   * 500-row table, none at 2,000); what makes a large table slow is the
   * number of DOM nodes, which is a different fix.
   */
  mode?: "static" | "streaming";
  isAnimating?: boolean;
  isLocalFileHref?: (href: string) => boolean;
  onLocalFileLinkClick?: (href: string) => void;
  citationBundle?: CitationBundleV1;
  messageId?: string;
  onCitationClick?: (input: OpenCitationInput) => void;
  /**
   * A conversation turn may contain more than one durable assistant message
   * (for example a completed answer followed by a repair patch).  The turn
   * owns citation numbering and the single source list in that case; each
   * Markdown fragment still renders its own audited claim locations.
   */
  citationDisplayOrderOverride?: ReadonlyMap<string, number>;
  citationLookupBundleOverride?: CitationBundleV1;
  citationMessageIdByCitationIdOverride?: ReadonlyMap<
    string,
    string | undefined
  >;
  showCitationSources?: boolean;
}

/**
 * Shared style overrides applied around every Streamdown render so
 * fenced code blocks and tables land in the same "card" look across
 * the whole app (conversation transcript, skill detail panel, anything
 * else that uses MarkdownContent).
 *
 * The selectors target Streamdown's stable ``data-streamdown="..."``
 * markers; rules sit on the wrapper div and reach into Streamdown's
 * internals so the chrome (header bar, toolbar buttons, dropdown
 * items, body padding) all match.
 */
const RICH_TEXT_OVERRIDES = [
  // ── Fenced code blocks ────────────────────────────────────────
  // Card chrome only — the action overlay positioning is too complex
  // for Tailwind arbitrary variants (needs ``:has()`` on a parent
  // div) so it lives in the global ``CODE_ACTIONS_CSS`` block below.
  "[&_[data-streamdown='code-block']]:relative",
  "[&_[data-streamdown='code-block']]:overflow-hidden",
  "[&_[data-streamdown='code-block']]:rounded-lg",
  "[&_[data-streamdown='code-block']]:border",
  "[&_[data-streamdown='code-block']]:border-surface-border",
  "[&_[data-streamdown='code-block-header']]:bg-surface-soft",
  "[&_[data-streamdown='code-block-header']]:border-b",
  "[&_[data-streamdown='code-block-header']]:border-surface-border",
  "[&_[data-streamdown='code-block-header']]:rounded-none",
  "[&_[data-streamdown='code-block-header']]:px-3",
  // Streamdown's language label span has ``ml-1`` which adds another
  // 4px on top of the header's left padding; zero it so the label sits
  // flush against the header padding.
  "[&_[data-streamdown='code-block-header']>span]:ml-0",
  "[&_[data-streamdown='code-block-body']]:bg-surface",
  "[&_[data-streamdown='code-block-body']]:border-0",
  "[&_[data-streamdown='code-block-body']]:rounded-none",
  "[&_[data-streamdown='code-block-download-button']]:hidden",
  // Pull padding off Shiki's <pre data-language>; put it on the body.
  "[&_[data-language]]:p-0",
  "[&_[data-streamdown='code-block-body']]:px-4",
  "[&_[data-streamdown='code-block-body']]:pb-4",
  "[&_[data-streamdown='code-block-body']]:pt-2",
  "[&_pre]:m-0",

  // ── Tables ────────────────────────────────────────────────────
  "[&_[data-streamdown='table-wrapper']]:relative",
  "[&_[data-streamdown='table-wrapper']]:rounded-lg",
  "[&_[data-streamdown='table-wrapper']]:border",
  "[&_[data-streamdown='table-wrapper']]:border-[color:var(--fg-3)]",
  "[&_[data-streamdown='table-wrapper']]:bg-surface",
  "[&_[data-streamdown='table-wrapper']]:overflow-hidden",
  "[&_[data-streamdown='table-wrapper']]:p-0",
  "[&_[data-streamdown='table-wrapper']]:gap-0",
  "[&_[data-streamdown='table-wrapper']]:text-sm",
  // Toolbar region — overlay only, so it does not create a second header row.
  // Do not identify it merely by the presence of a button: citation-quality
  // markers inside table cells are buttons too.  Streamdown's data region is
  // the direct child that owns ``[data-streamdown='table']``; the toolbar is
  // the other direct child.
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:absolute",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:right-2",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:top-1.5",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:z-10",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:h-5",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:bg-transparent",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:border-0",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:p-0",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:items-center",
  // The toolbar sits on top of the last header cell, so the two cannot both
  // be visible. Resting state belongs to the header: the last column keeps
  // its title like every other column, and the buttons only fade in while
  // the reader is on the table (hover, or keyboard focus inside it — without
  // the focus case a tabbed-to button would stay invisible).
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:opacity-0",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:pointer-events-none",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))]:transition-opacity",
  "[&_[data-streamdown='table-wrapper']:hover>div:not(:has([data-streamdown='table']))]:opacity-100",
  "[&_[data-streamdown='table-wrapper']:hover>div:not(:has([data-streamdown='table']))]:pointer-events-auto",
  "[&_[data-streamdown='table-wrapper']:focus-within>div:not(:has([data-streamdown='table']))]:opacity-100",
  "[&_[data-streamdown='table-wrapper']:focus-within>div:not(:has([data-streamdown='table']))]:pointer-events-auto",
  // Table region — flat white, no inner border, bottom rounded.
  "[&_[data-streamdown='table-wrapper']>div:has([data-streamdown='table'])]:border-0",
  "[&_[data-streamdown='table-wrapper']>div:has([data-streamdown='table'])]:rounded-none",
  "[&_[data-streamdown='table-wrapper']>div:has([data-streamdown='table'])]:rounded-b-lg",
  "[&_[data-streamdown='table-wrapper']>div:has([data-streamdown='table'])]:bg-surface",
  "[&_[data-streamdown='table']]:w-full",
  "[&_[data-streamdown='table']]:border-collapse",
  "[&_[data-streamdown='table']]:tabular-nums",
  "[&_[data-streamdown='table-header']]:bg-[color:var(--fg-1)]",
  "[&_[data-streamdown='table-header']]:border-b",
  "[&_[data-streamdown='table-header']]:border-[color:var(--fg-3)]",
  "[&_[data-streamdown='table-header-cell']]:px-[14px]",
  "[&_[data-streamdown='table-header-cell']]:py-2",
  "[&_[data-streamdown='table-header-cell']]:text-2xs",
  "[&_[data-streamdown='table-header-cell']]:font-medium",
  "[&_[data-streamdown='table-header-cell']]:text-[color:var(--fg-60)]",
  "[&_[data-streamdown='table-header-cell']]:text-right",
  "[&_[data-streamdown='table-header-cell']:first-child]:text-left",
  // Counterpart to the toolbar fade above: the last column reads as a normal
  // header until the toolbar takes that spot, then yields to it. Fading the
  // colour (rather than hiding the cell) keeps the column widths fixed, so
  // the swap does not reflow the table under the cursor.
  "[&_[data-streamdown='table-header-cell']:last-child]:transition-colors",
  "[&_[data-streamdown='table-wrapper']:hover_[data-streamdown='table-header-cell']:last-child]:text-transparent",
  "[&_[data-streamdown='table-wrapper']:hover_[data-streamdown='table-header-cell']:last-child]:select-none",
  "[&_[data-streamdown='table-wrapper']:focus-within_[data-streamdown='table-header-cell']:last-child]:text-transparent",
  "[&_[data-streamdown='table-wrapper']:focus-within_[data-streamdown='table-header-cell']:last-child]:select-none",
  "[&_[data-streamdown='table-row']]:border-b",
  "[&_[data-streamdown='table-row']]:border-[color:var(--fg-3)]",
  "[&_[data-streamdown='table-row']:last-child]:border-b-0",
  "[&_[data-streamdown='table-cell']]:px-[14px]",
  "[&_[data-streamdown='table-cell']]:py-[9px]",
  "[&_[data-streamdown='table-cell']]:text-sm",
  "[&_[data-streamdown='table-cell']]:text-ink-heading",
  "[&_[data-streamdown='table-cell']]:text-right",
  "[&_[data-streamdown='table-cell']:first-child]:text-left",
  // Dropdown items (Markdown / CSV / TSV) inside copy/download dropdowns.
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>div>button:hover]:bg-surface-muted",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>div>button]:cursor-default",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>div>button]:text-ink-heading",
  // Toolbar icons — direct-button case (fullscreen).
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>button]:flex",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>button]:h-5",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>button]:w-5",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>button]:items-center",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>button]:justify-center",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>button]:p-0",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>button]:cursor-default",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>button]:text-ink-muted",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>button>svg]:h-3",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>button>svg]:w-3",
  // Wrapped-button case (copy / download dropdowns).
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>button]:flex",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>button]:h-5",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>button]:w-5",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>button]:items-center",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>button]:justify-center",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>button]:p-0",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>button]:cursor-default",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>button]:text-ink-muted",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>button>svg]:h-3",
  "[&_[data-streamdown='table-wrapper']>div:not(:has([data-streamdown='table']))>div>button>svg]:w-3",

  // ── Lists ──────────────────────────────────────────────────────
  // Streamdown's default list margins / line-height produce a
  // double-spaced look that reads more like slide bullets than body
  // copy. Tighten:
  //   - block margin between list and surrounding paragraphs
  //   - per-item margin (the gap between adjacent bullets)
  //   - item line-height (was inheriting container ``leading-[1.7]``)
  // Rhythm strategy: tight lines (1.55) + generous block spacing
  // (16px between paragraphs / list blocks). Inside-paragraph density
  // gives every block a clear silhouette; the wide gutters between
  // blocks supply the "breathing" the reference screenshot has.
  "[&_ul]:my-4",
  "[&_ol]:my-4",
  // Switch to ``list-outside`` so markers occupy the padding region
  // instead of riding inline with the text. With ``list-inside`` the
  // marker (``•`` / ``1.``) butts straight against the first
  // character with no controllable gap; with ``list-outside`` the
  // ``pl-*`` becomes "marker column + gutter", giving each row a
  // proper hanging-indent feel like Notion / GitLab.
  "[&_ul]:list-outside",
  "[&_ol]:list-outside",
  // ``pl-7`` (28px): with ``list-outside`` the marker rides in the
  // padding region, so this value sets both the indent depth AND
  // the marker → text gap. ~28px overall indent, ~20px between
  // marker and the first character — airy hanging indent like
  // Notion / GitLab.
  "[&_ul]:pl-7",
  "[&_ol]:pl-7",
  "[&_li_ul]:pl-7",
  "[&_li_ol]:pl-7",
  "[&_li]:my-0",
  // Streamdown ships ``py-1`` baked into its MarkdownLi component
  // (4px above + below every bullet). Override the padding too —
  // ``my-*`` alone leaves the 4px gap intact.
  "[&_[data-streamdown='list-item']]:py-0",
  "[&_li]:leading-[1.7]",
  // Nested lists ride tight too — a sub-list under a parent item
  // shouldn't open a gap that competes with the 16px gutter
  // between top-level blocks.
  "[&_li>ul]:my-0",
  "[&_li>ol]:my-0",

  // ── Paragraphs ────────────────────────────────────────────────
  "[&_p]:my-4",
  "[&_p]:leading-[1.7]",

  // ── Inline code ───────────────────────────────────────────────
  // Long unbroken strings inside inline ``<code>`` (file paths, URLs,
  // hashes …) overflowed the message column because the default
  // ``white-space`` for ``<code>`` lets them push past the right edge
  // when there's no whitespace to break on. ``break-all`` lets the
  // browser break the run anywhere — fine for paths/URLs which are
  // already opaque blobs. The ``:not(pre)`` guard keeps fenced code
  // blocks (which have their own scrollable layout) untouched.
  "[&_:not(pre)>code]:break-all",
];

/**
 * Global style block (rendered once with each MarkdownContent mount;
 * duplicates collapse via identical CSS) for two cases the Tailwind
 * arbitrary variants above can't cleanly cover:
 *
 *   1. Streamdown code blocks render their actions toolbar as a
 *      ``sticky top-2 -mt-10`` overlay floating ABOVE the body so it
 *      visually overlaps the (default) header. With our own header
 *      bg the floating capsule lands awkwardly outside the title
 *      strip and gets clipped by ``overflow-hidden`` on the card. We
 *      pin the overlay ``absolute top:0 right:0`` inside the
 *      ``relative`` code-block container so the buttons land in the
 *      header row regardless of header styling. Also drop the
 *      capsule's pill chrome (border / bg / blur) so the buttons
 *      read as plain icon controls in the header bar.
 *
 *   2. Streamdown's table fullscreen view is portaled to
 *      ``document.body`` so the wrapper-scoped Tailwind selectors
 *      can't reach it; mirrors the inline card look here.
 */
const GLOBAL_RICH_TEXT_CSS = `
  /* ── Code-block actions overlay ──────────────────────────────── */
  /* Streamdown stamps the floating div with a long compound class
     list (.pointer-events-none.sticky.top-2.z-10.-mt-10...) that
     out-specifies our attribute selector. !important is the cheapest
     way to win without inflating selector weight artificially. */
  [data-streamdown="code-block"] > div:has(> [data-streamdown="code-block-actions"]) {
    position: absolute !important;
    top: 0 !important;
    right: 0 !important;
    left: auto !important;
    bottom: auto !important;
    margin: 0 !important;
    height: 32px !important;
    padding: 0 8px !important;
    display: flex !important;
    align-items: center !important;
    pointer-events: auto !important;
    z-index: 1 !important;
  }
  [data-streamdown="code-block-actions"] {
    background: transparent !important;
    border: 0 !important;
    padding: 0 !important;
    backdrop-filter: none !important;
    box-shadow: none !important;
    gap: 4px;
  }
  [data-streamdown="code-block-copy-button"] {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    padding: 0;
    cursor: default;
    color: var(--color-ink-muted, #b6b7bc);
  }
  [data-streamdown="code-block-copy-button"] svg {
    width: 12px;
    height: 12px;
  }

  /* ── Table fullscreen ────────────────────────────────────────── */`;

const FULLSCREEN_TABLE_CSS = `
  [data-streamdown="table-fullscreen"] > div {
    background: var(--surface);
    border-radius: 8px;
    border: 1px solid var(--fg-3);
    overflow: hidden;
    /* Top offset = project TopBar height (36px). AppShell's inner
       flex uses p-4 pt-0, so the main card sits flush under the
       topbar; matching that here puts the fullscreen card on the
       same baseline. Other sides match AppShell's 16px outer gutter. */
    margin: 36px 16px 16px 16px;
    height: calc(100% - 52px);
  }
  [data-streamdown="table-fullscreen"] > div > div:first-child {
    background: var(--fg-1);
    border-bottom: 1px solid var(--fg-3);
    padding: 0 14px;
    height: 34px;
    align-items: center;
    gap: 4px;
  }
  [data-streamdown="table-fullscreen"] > div > div:first-child button {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    padding: 0;
    color: var(--color-ink-muted, #b6b7bc);
    cursor: default;
  }
  [data-streamdown="table-fullscreen"] > div > div:first-child button > svg {
    width: 12px;
    height: 12px;
  }
  [data-streamdown="table-fullscreen"] > div > div:nth-child(2) {
    padding: 0;
    background: var(--surface);
  }
  [data-streamdown="table-fullscreen"] table[data-streamdown="table"] {
    border: none;
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    font-feature-settings: "tnum";
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-header"] {
    background: var(--fg-1);
    border-bottom: 1px solid var(--fg-3);
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-header-cell"] {
    padding: 8px 14px;
    text-align: right;
    font-weight: 500;
    font-size: 11px;
    color: var(--fg-60);
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-header-cell"]:first-child {
    text-align: left;
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-row"] {
    border-bottom: 1px solid var(--fg-3);
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-row"]:last-child {
    border-bottom: none;
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-cell"] {
    padding: 9px 14px;
    font-size: 13px;
    color: var(--color-ink-heading, #131313);
    text-align: right;
  }
  [data-streamdown="table-fullscreen"] [data-streamdown="table-cell"]:first-child {
    text-align: left;
  }
`;

/**
 * Hook + dialog for confirming external (http/https) link navigation.
 *
 * Streamdown ships its own link-safety modal but renders it inline
 * (``position: fixed`` inside the markdown subtree). The conversation
 * uses a virtualized list whose rows carry ``transform: translateY``,
 * which establishes a containing block for ``fixed`` and pins the
 * modal to the row instead of the viewport. We sidestep that by
 * overriding Streamdown's ``a`` component and presenting our own
 * radix Dialog — radix portals to ``document.body``, so transformed
 * ancestors don't affect positioning.
 */
const isExternalHref = (href: string | undefined): href is string =>
  typeof href === "string" && /^https?:\/\//i.test(href);

const openExternalUrl = async (url: string): Promise<void> => {
  const desktopApi = (
    window as Window & {
      valuzDesktop?: {
        invoke: <T>(
          channel: string,
          payload?: Record<string, unknown>,
        ) => Promise<T>;
      };
    }
  ).valuzDesktop;
  if (desktopApi) {
    try {
      const opened = await desktopApi.invoke<boolean>("open_external_url", {
        url,
      });
      if (opened) return;
    } catch {
      /* fall through to browser open */
    }
  }
  window.open(url, "_blank", "noopener,noreferrer");
};

const ExternalLinkConfirmDialog = ({
  url,
  onClose,
}: {
  url: string | null;
  onClose: () => void;
}) => {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard denied — silent */
    }
  }, [url]);

  const handleConfirm = useCallback(() => {
    if (!url) return;
    void openExternalUrl(url);
    onClose();
  }, [url, onClose]);

  return (
    <Dialog
      open={url !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ExternalLink className="h-4 w-4" />
            <span>{t("conversation.openExternalLink")}</span>
          </DialogTitle>
          <DialogDescription>
            {t("conversation.openExternalLinkDesc")}
          </DialogDescription>
        </DialogHeader>
        <div
          className={cn(
            "break-all rounded-md bg-muted p-3 font-mono text-xs",
            url && url.length > 100 && "max-h-32 overflow-y-auto",
          )}
        >
          {url ?? ""}
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => void handleCopy()}
          >
            {copied ? (
              <>
                <Check className="h-4 w-4" />
                <span>{t("common.copied")}</span>
              </>
            ) : (
              <>
                <Copy className="h-4 w-4" />
                <span>{t("conversation.copyLink")}</span>
              </>
            )}
          </Button>
          <Button type="button" onClick={handleConfirm}>
            <ExternalLink className="h-4 w-4" />
            <span>{t("conversation.openLink")}</span>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export const MarkdownContent = memo(function MarkdownContent({
  content,
  className,
  isAnimating,
  mode = "streaming",
  isLocalFileHref,
  onLocalFileLinkClick,
  citationBundle,
  messageId,
  onCitationClick,
  citationDisplayOrderOverride,
  citationLookupBundleOverride,
  citationMessageIdByCitationIdOverride,
  showCitationSources = true,
}: MarkdownContentProps) {
  const { t } = useI18n();
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);
  // Citation verification is additive guidance. Even legacy messages marked
  // ``blocked`` must keep the answer the user already paid and waited for;
  // the UI communicates concrete issues at the relevant citation instead of
  // replacing the entire response with a generic failure sentence.
  const displayContent = stripStandaloneCitationLines(
    stripDecorativeHeadingCitations(
      stripProtocolSourcePlaceholders(
        projectEvidenceMarkdownLinks(
          projectCitationSidecarAnchors(
            projectCitationTextCorrections(
              stripStreamingEvidenceLinkTail(content),
              citationBundle,
            ),
            citationBundle,
          ),
          citationBundle,
        ),
      ),
    ),
  );
  const localCitationOrder = useMemo(
    () => citationDisplayOrder(displayContent, citationBundle),
    [citationBundle, displayContent],
  );
  const citationOrder = citationDisplayOrderOverride ?? localCitationOrder;
  const citationOccurrenceOffsets = useMemo(
    () => citationOccurrences(displayContent),
    [displayContent],
  );
  const citationsById = useMemo(() => {
    const citations = new Map<string, CitationBundleV1["citations"][number]>();
    // A single user turn can contain several durable assistant messages
    // (for example, an answer followed by a local repair). Citations are
    // numbered and listed across that whole trailing answer run, so inline
    // pills must resolve against the same turn-level registry as the source
    // list. Keep the local bundle last so its current annotations win when
    // both registries contain the same citation.
    for (const citation of citationLookupBundleOverride?.citations ?? []) {
      citations.set(citation.citationId, citation);
    }
    for (const citation of citationBundle?.citations ?? []) {
      citations.set(citation.citationId, citation);
    }
    return citations;
  }, [citationBundle, citationLookupBundleOverride]);
  const qualityIssueLabel = useCallback(
    (issue: CitationQualityIssueV1): string => {
      const code = issue.code;
      const cited = (issue.citationIds ?? [])
        .map((citationId) => citationsById.get(citationId))
        .filter((citation): citation is CitationBundleV1["citations"][number] =>
          Boolean(citation),
        );
      const structured = cited.find(
        (citation) => citation.evidence.kind === "structured-data",
      );
      const calculation = cited.find(
        (citation) => citation.evidence.kind === "calculation",
      );

      if (
        code === "claim_without_citation" ||
        code === "numeric_claim_without_citation" ||
        code === "date_claim_without_citation"
      ) {
        return t("ui.citation.qualityClaimSourceMissing");
      }
      if (code === "claim_evidence_mismatch") {
        if (structured?.evidence.kind === "structured-data") {
          return t("ui.citation.qualityClaimStructuredMismatch", {
            field: readableEvidenceField(structured.evidence.field),
            value: evidenceValueLabel(structured),
          });
        }
        return t("ui.citation.qualityClaimMismatch");
      }
      if (code === "claim_partially_supported") {
        return t("ui.citation.qualityClaimPartial");
      }
      if (code === "claim_translation_not_verified") {
        return t("ui.citation.qualityClaimTranslationReview");
      }
      if (code === "structured_value_not_present_in_answer" && structured) {
        return t("ui.citation.qualityStructuredValueMismatch", {
          value: evidenceValueLabel(structured),
        });
      }
      if (code === "numeric_unit_missing") {
        return t("ui.citation.qualityStructuredUnitMissing");
      }
      if (code === "calculation_input_value_mismatch") {
        if (calculation?.evidence.kind === "calculation") {
          const mismatchedInput = calculation.evidence.inputs.find((input) => {
            const inputCitation = citationsById.get(input.citationId);
            return (
              inputCitation?.evidence.kind === "structured-data" &&
              String(inputCitation.evidence.value) !== String(input.value)
            );
          });
          const inputCitation = mismatchedInput
            ? citationsById.get(mismatchedInput.citationId)
            : undefined;
          if (
            mismatchedInput &&
            inputCitation?.evidence.kind === "structured-data"
          ) {
            return t("ui.citation.qualityCalculationInputMismatchDetail", {
              input: `${String(mismatchedInput.value)}${
                mismatchedInput.unit ? ` ${mismatchedInput.unit}` : ""
              }`,
              evidence: evidenceValueLabel(inputCitation),
            });
          }
        }
        return t("ui.citation.qualityCalculationInputMismatch");
      }
      if (code === "calculation_input_unit_mismatch") {
        return t("ui.citation.qualityCalculationUnitMismatch");
      }
      if (
        code === "claim_after_evidence_coverage" ||
        code === "evidence_after_coverage"
      ) {
        const coverageEnd = cited
          .map((citation) =>
            citation.evidence.kind === "structured-data"
              ? citation.evidence.coverage?.end
              : undefined,
          )
          .find(Boolean);
        return coverageEnd
          ? t("ui.citation.qualityCoverageEnded", { date: coverageEnd })
          : t("ui.citation.qualityIssueFreshness");
      }
      if (code === "low_tier_without_cross_check") {
        return t("ui.citation.qualityIssueLowTier");
      }
      if (code === "claim_source_entity_conflict") {
        return t("ui.citation.qualityIssueEntityConflict");
      }
      if (code === "claim_source_period_conflict") {
        return t("ui.citation.qualityIssuePeriodConflict");
      }
      if (code.includes("conflict")) {
        return t("ui.citation.qualityIssueConflict");
      }
      if (code.includes("ambiguous")) {
        return t("ui.citation.qualityIssueAmbiguous");
      }
      if (code.includes("cross_check")) {
        return t("ui.citation.qualityIssueCrossCheck");
      }
      if (
        code.startsWith("calculation_") ||
        code.startsWith("numeric_") ||
        code === "derived_claim_without_calculation_evidence"
      ) {
        return t("ui.citation.qualityIssueNumeric");
      }
      if (
        code.includes("coverage") ||
        code === "evidence_before_coverage" ||
        code === "evidence_after_coverage"
      ) {
        return t("ui.citation.qualityIssueFreshness");
      }
      if (code === "source_tier_unmatched") {
        return t("ui.citation.qualityIssueSource");
      }
      if (
        code.startsWith("evidence_") ||
        code === "claim_without_citation" ||
        code === "date_claim_without_citation" ||
        code === "claim_evidence_mismatch" ||
        code === "claim_partially_supported" ||
        code === "claim_translation_not_verified" ||
        code === "text_quote_missing" ||
        code === "structured_value_missing"
      ) {
        return t("ui.citation.qualityIssueEvidence");
      }
      if (code === "claim_audit_truncated") {
        return t("ui.citation.qualityIssueIntegrity");
      }
      const keyByLayer: Record<string, string> = {
        L0: "ui.citation.qualityIssueIntegrity",
        L1: "ui.citation.qualityIssueEvidence",
        L2: "ui.citation.qualityIssueSource",
        L3: "ui.citation.qualityIssueCrossCheck",
        L4: "ui.citation.qualityIssueNumeric",
        L5: "ui.citation.qualityIssueFreshness",
      };
      return t(keyByLayer[issue.layer] ?? "ui.citation.qualityIssueGeneric");
    },
    [citationsById, t],
  );
  const qualityIssuePlacement = useMemo(() => {
    const byCitationId = new Map<string, CitationQualityDisplayIssue[]>();
    const byCitationOccurrence = new Map<
      string,
      CitationQualityDisplayIssue[]
    >();
    const claimEntriesById = new Map<string, LocalizedClaimQualityEntry>();
    const unlocalized: CitationQualityIssueV1[] = [];
    const auditedClaims = citationBundle?.quality?.claims ?? [];
    const auditedClaimsById = new Map<string, CitationClaimAuditV1>(
      auditedClaims.map((claim) => [claim.claimId, claim]),
    );
    const issues = citationBundle?.quality?.issues ?? [];
    for (const [issueIndex, issue] of issues.entries()) {
      const auditedClaim = issue.claimId
        ? auditedClaimsById.get(issue.claimId)
        : undefined;
      // Calculation Evidence intentionally has no numbered source index, but
      // it is still a concrete Evidence object and can carry a quality issue.
      // Do not erase that association merely because citationOrder excludes
      // derivations from the numbered source list.
      const localIds = Array.from(new Set(issue.citationIds ?? [])).filter(
        (citationId) => citationsById.has(citationId),
      );
      const displayIssue = {
        code: issue.code,
        claimId: auditedClaim?.claimId ?? issue.claimId,
        label: qualityIssueLabel(issue),
        severity: issue.severity,
        tone: qualityIssueTone(
          issue,
          citationBundle?.quality?.verifierRevision,
        ),
      };
      if (!localIds.length) {
        // Weak support is not itself proof that a statement is wrong, and
        // without a citation card there is nothing for an advisory marker to
        // show — keep those suppressed. A statement carrying no binding at
        // all is different: it has no card by definition, and dropping it
        // would leave a fully unsourced answer looking exactly like a fully
        // sourced one. Mark those in place, in a neutral tone.
        if (
          displayIssue.tone !== "critical" &&
          displayIssue.tone !== "unsourced"
        ) {
          unlocalized.push(issue);
          continue;
        }
        const exact = (auditedClaim?.exact ?? issue.claim?.exact)?.trim();
        const location = issue.location ?? auditedClaim?.location;
        const sourceEnd = claimSourceEnd(location);
        const hasStableSourceLocation =
          Number.isInteger(sourceEnd) &&
          sourceEnd! >= 0 &&
          sourceEnd! <= displayContent.length;
        if (
          exact &&
          (hasStableSourceLocation ||
            (!exact.includes("\n") &&
              !exact.includes("|") &&
              displayContent.includes(exact)))
        ) {
          const entryKey =
            auditedClaim?.claimId ??
            issue.claimId ??
            `legacy-${issueIndex + 1}`;
          const entry = claimEntriesById.get(entryKey) ?? {
            targetId: `quality-claim-${entryKey}`,
            claimId: auditedClaim?.claimId ?? issue.claimId,
            exact,
            location,
            citationIds: [],
            issues: [],
          };
          if (
            !entry.issues.some(
              (item) =>
                item.label === displayIssue.label &&
                item.severity === displayIssue.severity,
            )
          ) {
            entry.issues.push(displayIssue);
          }
          claimEntriesById.set(entryKey, entry);
          continue;
        }
        unlocalized.push(issue);
        continue;
      }
      const location = issue.location ?? auditedClaim?.location;
      const sourceStart =
        location && location.kind !== "legacy"
          ? location.sourceStart
          : undefined;
      const sourceEnd = claimSourceEnd(location);
      let localizedToOccurrence = false;
      for (const citationId of localIds) {
        const offsets = citationOccurrenceOffsets.get(citationId) ?? [];
        const scopedOffsets =
          Number.isInteger(sourceStart) && Number.isInteger(sourceEnd)
            ? offsets.filter(
                (offset) => offset >= sourceStart! && offset < sourceEnd!,
              )
            : [];
        if (scopedOffsets.length > 0) {
          localizedToOccurrence = true;
          for (const offset of scopedOffsets) {
            const key = citationOccurrenceKey(citationId, offset);
            const current = byCitationOccurrence.get(key) ?? [];
            if (
              !current.some(
                (item) =>
                  item.label === displayIssue.label &&
                  item.severity === displayIssue.severity,
              )
            ) {
              current.push(displayIssue);
            }
            byCitationOccurrence.set(key, current);
          }
        }
      }
      if (localizedToOccurrence) continue;

      // A Claim-scoped issue must never fall back to every occurrence of a
      // reused citation id. If its citation is not physically adjacent to the
      // Claim (common after sidecar auto-binding), place one marker at the
      // Claim and retain the concrete Evidence inside that marker.
      const exact = (auditedClaim?.exact ?? issue.claim?.exact)?.trim();
      const hasClaimScope = Boolean(auditedClaim?.claimId ?? issue.claimId);
      const hasStableSourceLocation =
        Number.isInteger(sourceEnd) &&
        sourceEnd! >= 0 &&
        sourceEnd! <= displayContent.length;
      const canPlaceAtClaim = Boolean(
        hasClaimScope &&
          exact &&
          (hasStableSourceLocation ||
            (!exact.includes("\n") &&
              !exact.includes("|") &&
              displayContent.includes(exact))),
      );
      // Advisory support/translation uncertainty is useful inside an actual
      // citation hover card, but must not create a standalone orange warning
      // when the sidecar can place the issue only at the Claim.
      if (
        canPlaceAtClaim &&
        displayIssue.tone !== "critical" &&
        displayIssue.tone !== "unsourced"
      ) {
        unlocalized.push(issue);
        continue;
      }
      if (canPlaceAtClaim && exact) {
        const entryKey = auditedClaim?.claimId ?? issue.claimId!;
        const entry = claimEntriesById.get(entryKey) ?? {
          targetId: `quality-claim-${entryKey}`,
          claimId: auditedClaim?.claimId ?? issue.claimId,
          exact,
          location,
          citationIds: [],
          issues: [],
        };
        entry.citationIds = Array.from(
          new Set([...entry.citationIds, ...localIds]),
        );
        if (
          !entry.issues.some(
            (item) =>
              item.label === displayIssue.label &&
              item.severity === displayIssue.severity,
          )
        ) {
          entry.issues.push(displayIssue);
        }
        claimEntriesById.set(entryKey, entry);
        continue;
      }
      for (const citationId of localIds) {
        const current = byCitationId.get(citationId) ?? [];
        if (
          !current.some(
            (item) =>
              item.label === displayIssue.label &&
              item.severity === displayIssue.severity,
          )
        ) {
          current.push(displayIssue);
        }
        byCitationId.set(citationId, current);
      }
    }
    for (const [citationId, citationIssues] of byCitationId) {
      byCitationId.set(
        citationId,
        selectUserFacingQualityIssues(citationIssues),
      );
    }
    for (const [key, citationIssues] of byCitationOccurrence) {
      byCitationOccurrence.set(
        key,
        selectUserFacingQualityIssues(citationIssues),
      );
    }
    const claimEntries = Array.from(claimEntriesById.values())
      .map((entry) => ({
        ...entry,
        issues: selectUserFacingQualityIssues(entry.issues),
      }))
      .filter((entry) => entry.issues.length > 0)
      .sort((left, right) => {
        const leftEnd = claimSourceEnd(left.location);
        const rightEnd = claimSourceEnd(right.location);
        if (typeof leftEnd === "number" && typeof rightEnd === "number") {
          return leftEnd - rightEnd;
        }
        return left.targetId.localeCompare(right.targetId);
      });
    return {
      byCitationId,
      byCitationOccurrence,
      claimEntries,
      unlocalized,
    };
  }, [
    citationBundle?.quality?.claims,
    citationBundle?.quality?.issues,
    citationOrder,
    citationOccurrenceOffsets,
    citationsById,
    displayContent,
    qualityIssueLabel,
  ]);
  const claimQualityById = useMemo(
    () =>
      new Map(
        qualityIssuePlacement.claimEntries.map((entry) => [
          entry.targetId,
          entry,
        ]),
      ),
    [qualityIssuePlacement.claimEntries],
  );
  const renderedContent = useMemo(
    () =>
      rewriteLocalFileMarkdownLinks(
        rewriteCitationMarkdownLinks(
          injectQualityClaimMarkers(
            displayContent,
            qualityIssuePlacement.claimEntries,
          ),
        ),
        isLocalFileHref,
      ),
    [displayContent, isLocalFileHref, qualityIssuePlacement.claimEntries],
  );
  const urlTransform = useCallback<UrlTransform>(
    (url, key, node) => {
      if (key === "href" && isLocalFileHref?.(decodeLocalFileHref(url))) {
        return url;
      }
      return defaultUrlTransform(url, key, node);
    },
    [isLocalFileHref],
  );

  const components = useMemo(
    () => ({
      a: ({
        href,
        children,
        onClick,
        className: anchorClassName,
        ...rest
      }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
        const baseClass = cn(
          "wrap-anywhere font-medium text-primary underline",
          anchorClassName,
        );
        const localHref = href ? decodeLocalFileHref(href) : href;
        const qualityClaimId = qualityClaimIdFromHref(href);
        if (qualityClaimId) {
          const entry = claimQualityById.get(qualityClaimId);
          if (!entry) return null;
          return (
            <ClaimQualityMarker
              entry={entry}
              citationsById={citationsById}
              citationOrder={citationOrder}
              messageId={messageId}
              citationMessageIdByCitationId={
                citationMessageIdByCitationIdOverride
              }
              onCitationClick={onCitationClick}
            />
          );
        }
        const citationId = citationIdFromHref(href);
        if (citationId) {
          const sourceOffset = citationOffsetFromHref(href);
          const occurrenceIssues =
            sourceOffset === null
              ? undefined
              : qualityIssuePlacement.byCitationOccurrence.get(
                  citationOccurrenceKey(citationId, sourceOffset),
                );
          return (
            <CitationPill
              citationId={citationId}
              displayIndex={citationOrder.get(citationId)}
              citation={citationsById.get(citationId)}
              citationById={citationsById}
              qualityIssues={
                occurrenceIssues ??
                qualityIssuePlacement.byCitationId.get(citationId)
              }
              messageId={
                citationMessageIdByCitationIdOverride?.get(citationId) ??
                messageId
              }
              onCitationClick={onCitationClick}
            />
          );
        }
        if (localHref && isLocalFileHref?.(localHref) && onLocalFileLinkClick) {
          return (
            <a
              {...rest}
              href={localHref}
              className={baseClass}
              onClick={(event) => {
                event.preventDefault();
                onClick?.(event);
                onLocalFileLinkClick(localHref);
              }}
            >
              {children}
            </a>
          );
        }
        if (isExternalHref(href)) {
          return (
            <a
              {...rest}
              href={href}
              className={baseClass}
              onClick={(event) => {
                event.preventDefault();
                onClick?.(event);
                setPendingUrl(href);
              }}
            >
              {children}
            </a>
          );
        }
        return (
          <a {...rest} href={href} className={baseClass} onClick={onClick}>
            {children}
          </a>
        );
      },
    }),
    [
      citationOrder,
      citationsById,
      claimQualityById,
      isLocalFileHref,
      messageId,
      citationMessageIdByCitationIdOverride,
      onCitationClick,
      onLocalFileLinkClick,
      qualityIssuePlacement.byCitationId,
      qualityIssuePlacement.byCitationOccurrence,
    ],
  );

  return (
    <>
      <style>{GLOBAL_RICH_TEXT_CSS + FULLSCREEN_TABLE_CSS}</style>
      <div
        id="streamdown"
        className={cn(
          // ``break-words`` (inherited by every descendant) breaks long
          // unbreakable runs — API-error JSON blobs, URLs, hashes in plain
          // paragraphs — so they wrap instead of pushing past the message
          // column's right edge. Inline ``<code>`` keeps its own ``break-all``.
          //
          // ``relative`` is load-bearing, not decoration. Rendered markdown
          // contains absolutely-positioned descendants — the ``sr-only``
          // heading the footnotes section emits is one — and without a
          // positioned ancestor their containing block is the INITIAL one, so
          // they resolve against the document instead of this box. They then
          // escape every ``overflow`` clip between here and the viewport and
          // push ``document.documentElement.scrollHeight`` out to wherever
          // they happen to land: a 1px screen-reader label 1000px down made
          // the whole app shell scrollable. Measured — a footnoted document in
          // the knowledge-base panel took the document from 720 to 1784, and
          // the symptom was the page sliding under the cursor whenever the
          // wheel went over a list that had nothing of its own to scroll.
          "relative",
          "text-base leading-[1.7] text-ink-heading break-words",
          ...RICH_TEXT_OVERRIDES,
          className,
        )}
      >
        <Streamdown
          plugins={{ code, mermaid, math, cjk }}
          icons={STREAMDOWN_ICONS}
          mode={mode}
          // Only a prefix can be malformed by being a prefix, so repairing a
          // whole document can only misread deliberate markup as damage.
          parseIncompleteMarkdown={mode === "streaming"}
          isAnimating={isAnimating}
          components={components}
          urlTransform={urlTransform}
        >
          {renderedContent}
        </Streamdown>
      </div>
      {showCitationSources ? (
        <CitationSourceCards
          content={displayContent}
          citationBundle={citationBundle}
          messageId={messageId}
          onCitationClick={onCitationClick}
          displayOrder={citationOrder}
        />
      ) : null}
      <ExternalLinkConfirmDialog
        url={pendingUrl}
        onClose={() => setPendingUrl(null)}
      />
    </>
  );
});

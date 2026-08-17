import {
  Children,
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type MouseEvent as ReactMouseEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  Calculator,
  ChevronDown,
  ExternalLink,
  Info,
} from "lucide-react";
import { Streamdown, type Components } from "streamdown";
import type {
  CitationBundleV1,
  CitationRefV1,
  OpenCitationInput,
} from "@valuz/shared";

import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

const CITATION_HREF_PREFIX = "https://valuz.citation.invalid/";
const CITATION_URI_PATTERN = /citation:\/\/([A-Za-z0-9._~:-]+)/g;
// A Collection Address' JSON Pointer can carry balanced parentheses, and a
// space inside them — Reportify names indicator keys after their call
// signature, giving ``#/datas/0/indicators/ma(close, 20)``. Matching the
// fragment as ``[^\s)]`` failed on the whole link, so it was never projected
// and the reader saw the raw ``evidence://`` protocol in the answer. A space is
// only accepted inside a parenthesised group, so the pattern still stops at the
// link instead of running into the prose after it.
const ADDRESS_FRAGMENT = String.raw`(?:[^\s()\[\]\n]|\([^()\n]{0,200}\)){1,2048}`;
const EVIDENCE_LINK_PATTERN = new RegExp(
  String.raw`\[([^\]\n]{0,240})\]\(evidence:\/\/([A-Za-z0-9_-]{1,160})(#${ADDRESS_FRAGMENT})?\)`,
  "gu",
);
// The tail of a binding the model is still writing. Streamdown completes a
// half-written link while streaming, then rejects the unknown ``evidence:``
// protocol and paints "[blocked]" in the middle of the sentence until the
// sidecar arrives. A binding in flight is protocol noise the reader should
// never see, so drop the partial exactly like the finished form is dropped.
const PARTIAL_EVIDENCE_LINK_PATTERN =
  /\[([^\]\n]{0,240})\]\(evidence:(?:\/(?:\/[A-Za-z0-9_-]{0,160}(?:#[^\s)\n]{0,2048})?)?)?$/;
const HOVER_CLOSE_DELAY_MS = 150;
const HOVER_CARD_GAP_PX = 8;
const VIEWPORT_PADDING_PX = 16;

type CitationCardSide = "bottom" | "top";
type CitationCardPosition = { left: number; top: number };

export interface CitationQualityDisplayIssue {
  code?: string;
  claimId?: string;
  label: string;
  severity: string;
  /** ``unsourced`` marks a statement that carries no binding at all. It is a
   * coverage gap, not evidence that the statement is wrong, so it renders in
   * a neutral tone rather than the warning tone reserved for conflicts. */
  tone: "advisory" | "critical" | "unsourced";
}

function comparableEvidenceText(value: string): string {
  return value
    .replace(/\s+/gu, " ")
    .replace(/\s*\|\s*/gu, "|")
    .trim();
}

function redundantEvidenceSnippet(quote: string, snippet?: string): boolean {
  if (!snippet) return true;
  const comparableQuote = comparableEvidenceText(quote);
  const comparableSnippet = comparableEvidenceText(snippet);
  if (
    comparableSnippet === comparableQuote ||
    comparableQuote.includes(comparableSnippet)
  ) {
    return true;
  }

  // Search/index APIs commonly return a character-limited prefix ending in an
  // ellipsis while ``quote`` carries the complete PDF/table chunk. After
  // normalizing Markdown table pipes, treat that prefix as the same evidence.
  const snippetPrefix = comparableSnippet
    .replace(/(?:\.{3}|…)\s*$/u, "")
    .trim();
  return (
    snippetPrefix.length >= 40 && comparableQuote.startsWith(snippetPrefix)
  );
}

export function rewriteCitationMarkdownLinks(content: string): string {
  return content.replace(
    CITATION_URI_PATTERN,
    (_whole, citationId: string, sourceOffset: number) =>
      `${CITATION_HREF_PREFIX}${encodeURIComponent(citationId)}?offset=${sourceOffset}`,
  );
}

export function projectEvidenceMarkdownLinks(
  content: string,
  bundle?: CitationBundleV1,
): string {
  const projection = new Map<string, string>(
    Object.entries(bundle?.projection?.evidenceHandleToCitationId ?? {}),
  );
  for (const citation of bundle?.citations ?? []) {
    const binding = citation.annotations?.binding;
    if (!binding || typeof binding !== "object") continue;
    const handle = (binding as Record<string, unknown>).evidenceHandle;
    if (typeof handle === "string" && handle) {
      projection.set(handle, citation.citationId);
    }
  }
  return content.replace(
    EVIDENCE_LINK_PATTERN,
    (_whole, label: string, handle: string, fragment?: string) => {
      // An address names one field inside a collection. If that exact address
      // is unknown, the collection's other citations are evidence for other
      // fields — attaching one of them puts a confident, unrelated number
      // behind the value, which is worse than showing no marker. Only a
      // pointerless handle may resolve to the collection itself.
      const citationId = fragment
        ? projection.get(`${handle}${fragment}`)
        : projection.get(handle);
      if (citationId) return `[${label}](citation://${citationId})`;
      const normalized = label.replace(/\s+/gu, "").toLocaleLowerCase();
      return [
        "source",
        "sources",
        "citation",
        "reference",
        "来源",
        "引用",
        "出处",
      ].includes(normalized)
        ? ""
        : label;
    },
  );
}

/** Remove a binding the model has only half-written at the end of the stream. */
export function stripStreamingEvidenceLinkTail(content: string): string {
  return content.replace(PARTIAL_EVIDENCE_LINK_PATTERN, "");
}

function codePointOffsetToCodeUnit(
  content: string,
  sourceOffset: number,
): number {
  if (!Number.isInteger(sourceOffset) || sourceOffset < 0) return -1;
  const points = Array.from(content);
  if (sourceOffset > points.length) return -1;
  return points.slice(0, sourceOffset).join("").length;
}

export function projectCitationTextCorrections(
  content: string,
  bundle?: CitationBundleV1,
): string {
  const knownCitationIds = new Set(
    bundle?.citations.map((citation) => citation.citationId) ?? [],
  );
  const corrections = [...(bundle?.projection?.textCorrections ?? [])].sort(
    (left, right) => right.sourceStart - left.sourceStart,
  );
  let projected = content;
  let followingStart = Number.POSITIVE_INFINITY;
  for (const correction of corrections) {
    if (
      ![
        "structured-value-conflict",
        "document-value-conflict",
      ].includes(correction.reason) ||
      !knownCitationIds.has(correction.citationId) ||
      correction.sourceEnd > followingStart
    ) {
      continue;
    }
    const start = codePointOffsetToCodeUnit(content, correction.sourceStart);
    const end = codePointOffsetToCodeUnit(content, correction.sourceEnd);
    if (
      start < 0 ||
      end <= start ||
      projected.slice(start, end) !== correction.originalText
    ) {
      continue;
    }
    projected = `${projected.slice(0, start)}${correction.replacementText}${projected.slice(end)}`;
    followingStart = correction.sourceStart;
  }
  return projected;
}

function projectedSourceOffset(
  content: string,
  sourceOffset: number,
  bundle?: CitationBundleV1,
): number {
  const points = Array.from(content);
  let cumulativeDelta = 0;
  let projectedOffset = sourceOffset;
  const corrections = [...(bundle?.projection?.textCorrections ?? [])].sort(
    (left, right) => left.sourceStart - right.sourceStart,
  );
  for (const correction of corrections) {
    const replacement = Array.from(correction.replacementText);
    const adjustedStart = correction.sourceStart + cumulativeDelta;
    if (
      points.slice(adjustedStart, adjustedStart + replacement.length).join("") !==
      correction.replacementText
    ) {
      continue;
    }
    const delta =
      replacement.length - Array.from(correction.originalText).length;
    if (correction.sourceEnd <= sourceOffset) projectedOffset += delta;
    cumulativeDelta += delta;
  }
  return projectedOffset;
}

// A whole markdown link, and a number or word — the spans a marker must never
// be dropped into the middle of.
const UNSPLITTABLE_SPAN = new RegExp(
  [
    String.raw`\[[^\]\n]{0,240}\]\((?:[^()\s\n]|\([^()\n]{0,200}\)){1,2100}\)`,
    String.raw`[-+]?\$?\d[\d.,]*\d?%?`,
    String.raw`[A-Za-z][A-Za-z0-9_-]*`,
  ].join("|"),
  "gu",
);

/** Move an offset forward to the end of the text it sits in.
 *
 * A marker belongs after the thing it marks. Offsets drift — they are measured
 * against the text the guard normalised, not the text the reader was streamed —
 * so a raw offset can land mid-phrase and the marker reads as if it annotates
 * the words that follow it rather than the statement it belongs to. Snapping to
 * the end of the enclosing cell or sentence puts it back behind the text.
 */
export function offsetAfterEnclosingText(
  content: string,
  offset: number,
): number {
  const newline = content.indexOf("\n", offset);
  const lineEnd = newline < 0 ? content.length : newline;
  const lineStart = content.lastIndexOf("\n", Math.max(0, offset - 1)) + 1;
  let end: number;
  if (/^\s*\|/u.test(content.slice(lineStart, offset + 1))) {
    // A table row: stay inside the cell, otherwise the marker lands outside
    // the row and breaks it apart.
    const cellEnd = content.indexOf("|", offset);
    end = cellEnd < 0 || cellEnd > lineEnd ? lineEnd : cellEnd;
  } else {
    // Prose and list items: after the statement but before its full stop,
    // which is where a citation reads naturally and where the existing markers
    // already sit.
    // An ASCII full stop only ends a sentence when whitespace or the line end
    // follows it — otherwise it is the decimal point in "18.5".
    const stop = content
      .slice(offset, lineEnd)
      .search(/[。！？；]|[.!?;](?=\s|$)/u);
    end = stop < 0 ? lineEnd : offset + stop;
  }
  while (end > offset && /\s/u.test(content[end - 1] ?? "")) end -= 1;
  // Only ever move forward: a correct offset must survive this untouched.
  return Math.max(offset, end);
}

/** Move an offset that fell inside a link, number or word to just past it. */
export function offsetOutsideMarkdownLink(
  content: string,
  offset: number,
): number {
  // An anchor's offset is measured against the text the guard normalised, where
  // a Collection Address has already shrunk to its materialized handle. The
  // client still holds the text the model streamed, so every offset after the
  // first address is shifted. Landing inside a link splits the URL and leaves
  // the protocol on screen; landing inside a value renders "+$19.16" as
  // "+$19. ③ 16", which reads as two different numbers.
  UNSPLITTABLE_SPAN.lastIndex = 0;
  for (
    let match = UNSPLITTABLE_SPAN.exec(content);
    match !== null;
    match = UNSPLITTABLE_SPAN.exec(content)
  ) {
    if (match.index >= offset) break;
    const end = match.index + match[0].length;
    if (offset < end) return end;
  }
  return offset;
}

export function projectCitationSidecarAnchors(
  content: string,
  bundle?: CitationBundleV1,
): string {
  const knownCitationIds = new Set(
    bundle?.citations.map((citation) => citation.citationId) ?? [],
  );
  const insertions = new Map<number, string[]>();
  const seen = new Set<string>();
  const append = (sourceOffset: number, citationId: string) => {
    if (!knownCitationIds.has(citationId)) return;
    const offset = codePointOffsetToCodeUnit(
      content,
      projectedSourceOffset(content, sourceOffset, bundle),
    );
    if (offset < 0) return;
    const key = `${offset}\0${citationId}`;
    if (seen.has(key)) return;
    // A producer may replay a bundle against text that has already been
    // projected. Only de-duplicate a link already present at this exact raw
    // claim boundary. The same source can legitimately support two adjacent
    // claims, so a broad nearby-text check would silently drop the latter.
    const escapedCitationId = citationId.replace(
      /[.*+?^${}()|[\]\\]/gu,
      "\\$&",
    );
    const atBoundary = content.slice(offset, offset + 280);
    if (
      new RegExp(
        `^\\s*\\[[^\\]]+\\]\\(citation:\\/\\/${escapedCitationId}\\)`,
        "u",
      ).test(atBoundary)
    ) {
      return;
    }
    seen.add(key);
    const ids = insertions.get(offset) ?? [];
    ids.push(citationId);
    insertions.set(offset, ids);
  };

  for (const region of bundle?.projection?.provenanceRegions ?? []) {
    for (const citationId of region.citationIds) {
      append(region.sourceOffset, citationId);
    }
  }
  for (const anchor of bundle?.projection?.anchors ?? []) {
    append(anchor.sourceOffset, anchor.citationId);
  }

  let projected = content;
  for (const [offset, citationIds] of Array.from(insertions.entries()).sort(
    ([left], [right]) => right - left,
  )) {
    const markdown = citationIds
      .map((citationId) => `[source](citation://${citationId})`)
      .join(" ");
    const at = offsetOutsideMarkdownLink(
      projected,
      offsetAfterEnclosingText(projected, offset),
    );
    projected = `${projected.slice(0, at)} ${markdown}${projected.slice(at)}`;
  }
  return projected;
}

export function citationIdFromHref(href?: string): string | null {
  if (!href?.startsWith(CITATION_HREF_PREFIX)) return null;
  try {
    const encodedCitationId = href
      .slice(CITATION_HREF_PREFIX.length)
      .split("?", 1)[0];
    const citationId = decodeURIComponent(encodedCitationId ?? "");
    return citationId || null;
  } catch {
    return null;
  }
}

export function citationOffsetFromHref(href?: string): number | null {
  if (!href?.startsWith(CITATION_HREF_PREFIX)) return null;
  try {
    const offset = Number(new URL(href).searchParams.get("offset"));
    return Number.isInteger(offset) && offset >= 0 ? offset : null;
  } catch {
    return null;
  }
}

export function citationOccurrences(content: string): Map<string, number[]> {
  const occurrences = new Map<string, number[]>();
  for (const match of content.matchAll(CITATION_URI_PATTERN)) {
    const citationId = match[1];
    if (!citationId || match.index === undefined) continue;
    const offsets = occurrences.get(citationId) ?? [];
    offsets.push(match.index);
    occurrences.set(citationId, offsets);
  }
  return occurrences;
}

export function citationDisplayOrder(
  content: string,
  bundle?: CitationBundleV1,
): Map<string, number> {
  const derivationIds = new Set(
    bundle?.citations
      .filter((citation) => citation.evidence.kind === "calculation")
      .map((citation) => citation.citationId) ?? [],
  );
  const order = new Map<string, number>();
  for (const match of content.matchAll(CITATION_URI_PATTERN)) {
    const citationId = match[1];
    if (
      citationId &&
      !derivationIds.has(citationId) &&
      !order.has(citationId)
    ) {
      order.set(citationId, order.size + 1);
    }
  }
  return order;
}

export function usedCitations(
  content: string,
  bundle?: CitationBundleV1,
  displayOrder?: ReadonlyMap<string, number>,
): Array<{ displayIndex: number; citation: CitationRefV1 }> {
  if (!bundle) return [];
  const byId = new Map(
    bundle.citations.map((citation) => [citation.citationId, citation]),
  );
  const localOrder = citationDisplayOrder(content, bundle);
  return Array.from(localOrder, ([citationId, localDisplayIndex]) => {
    const citation = byId.get(citationId);
    const displayIndex = displayOrder?.get(citationId) ?? localDisplayIndex;
    return citation ? { displayIndex, citation } : null;
  }).filter(
    (item): item is { displayIndex: number; citation: CitationRefV1 } =>
      item !== null,
  );
}

function groupedCitationSources(
  used: Array<{ displayIndex: number; citation: CitationRefV1 }>,
): Array<{
  key: string;
  displayIndexes: number[];
  citation: CitationRefV1;
}> {
  const groups = new Map<
    string,
    { key: string; displayIndexes: number[]; citation: CitationRefV1 }
  >();
  for (const item of used) {
    const source = item.citation.source;
    const key = `${source.providerId}\0${source.documentId ?? source.sourceId}`;
    const group = groups.get(key);
    if (group) {
      group.displayIndexes.push(item.displayIndex);
    } else {
      groups.set(key, {
        key,
        displayIndexes: [item.displayIndex],
        citation: item.citation,
      });
    }
  }
  return Array.from(groups.values());
}

function citationIndexLabel(indexes: number[]): string {
  if (indexes.length <= 1) return String(indexes[0] ?? "");
  const consecutive = indexes.every(
    (value, index) => index === 0 || value === indexes[index - 1]! + 1,
  );
  return consecutive
    ? `${indexes[0]}–${indexes[indexes.length - 1]}`
    : indexes.join(", ");
}

function evidenceText(
  citation: CitationRefV1,
  documentCoverageLabel: string,
): {
  quote: string;
  snippet?: string;
  time?: string;
} {
  const evidence = citation.evidence;
  if (evidence.kind === "text") {
    return {
      quote: evidence.quote,
      snippet: redundantEvidenceSnippet(evidence.quote, evidence.snippet)
        ? undefined
        : evidence.snippet,
      time: citation.source.publishedAt ?? evidence.capturedAt,
    };
  }
  if (evidence.kind === "structured-data") {
    if (
      evidence.field === "document_coverage_complete" &&
      evidence.basis === "full-document" &&
      evidence.value === true
    ) {
      return {
        quote: documentCoverageLabel,
        time: evidence.capturedAt,
      };
    }
    const field = (evidence.field.split(/[./]/u).at(-1) ?? evidence.field)
      .replace(/([a-z0-9])([A-Z])/gu, "$1 $2")
      .replace(/[_-]+/gu, " ")
      .trim();
    return {
      quote: `${field}: ${String(evidence.value)}${evidence.unit ? ` ${evidence.unit}` : ""}`,
      time: evidence.asOf ?? evidence.period ?? evidence.capturedAt,
    };
  }
  return {
    quote: `${evidence.expression} = ${String(evidence.result)}${
      evidence.unit ? ` ${evidence.unit}` : ""
    }`,
    time: evidence.calculatedAt,
  };
}

function qualityBadge(
  citation: CitationRefV1,
): { label: string; status?: string } | null {
  const value = citation.annotations?.quality;
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const label = typeof record.label === "string" ? record.label : "";
  const status = typeof record.status === "string" ? record.status : undefined;
  return label ? { label, status } : null;
}

const SOURCE_TIER_IDS = ["T1", "T2", "T3", "T4", "T5"] as const;
type SourceTierId = (typeof SOURCE_TIER_IDS)[number];

function sourceTierId(label: string): SourceTierId | null {
  return SOURCE_TIER_IDS.includes(label as SourceTierId)
    ? (label as SourceTierId)
    : null;
}

function SourceTierLegendTooltip({
  tooltipId,
  activeTier,
  align = "right",
}: {
  tooltipId: string;
  activeTier?: SourceTierId;
  align?: "left" | "right";
}) {
  const { t } = useI18n();
  const legend = [
    { id: "T1", description: t("ui.citation.sourceTierT1") },
    { id: "T2", description: t("ui.citation.sourceTierT2") },
    { id: "T3", description: t("ui.citation.sourceTierT3") },
    { id: "T4", description: t("ui.citation.sourceTierT4") },
    { id: "T5", description: t("ui.citation.sourceTierT5") },
  ] satisfies Array<{ id: SourceTierId; description: string }>;

  return (
    <span
      id={tooltipId}
      role="tooltip"
      data-citation-source-tier-tooltip
      className={cn(
        "invisible absolute top-full z-50 w-72 rounded-lg border border-surface-border bg-surface p-2.5 text-left text-2xs font-normal text-ink-body opacity-0 shadow-lg transition-opacity group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100",
        align === "left" ? "left-0" : "right-0",
      )}
    >
      <span className="block font-medium text-ink-heading">
        {t("ui.citation.sourceTierTitle")}
      </span>
      <span className="mt-1.5 flex flex-col gap-1">
        {legend.map((item) => (
          <span
            key={item.id}
            data-citation-source-tier-row={item.id}
            data-active={item.id === activeTier || undefined}
            className={cn(
              "flex gap-2 rounded px-1.5 py-1 leading-4",
              item.id === activeTier && "bg-surface-muted text-ink-heading",
            )}
          >
            <span className="w-5 shrink-0 font-semibold">{item.id}</span>
            <span>{item.description}</span>
          </span>
        ))}
      </span>
      <span className="mt-1.5 block border-t border-surface-border pt-1.5 leading-4 text-ink-meta">
        {t("ui.citation.sourceTierCaveat")}
      </span>
    </span>
  );
}

function SourceTierBadge({
  quality,
  instanceId,
  focusable = true,
  triggerClassName,
  labelClassName,
}: {
  quality: { label: string; status?: string };
  instanceId: string;
  focusable?: boolean;
  triggerClassName?: string;
  labelClassName?: string;
}) {
  const sourceTier = sourceTierId(quality.label);
  if (!sourceTier) return null;

  const tooltipId = `citation-source-tier-${instanceId.replace(/[^a-zA-Z0-9_-]/gu, "-")}`;

  return (
    <span
      data-citation-source-tier-trigger={sourceTier}
      className={cn(
        "group relative inline-flex cursor-help rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30",
        triggerClassName,
      )}
      tabIndex={focusable ? 0 : undefined}
      aria-describedby={focusable ? tooltipId : undefined}
    >
      <span
        data-citation-quality={quality.status}
        className={labelClassName}
      >
        {quality.label}
      </span>
      <SourceTierLegendTooltip
        tooltipId={tooltipId}
        activeTier={sourceTier}
      />
    </span>
  );
}

function citationCorrections(
  citation: CitationRefV1,
): Array<{
  originalText: string;
  replacementText: string;
  reason: "structured-value-conflict" | "document-value-conflict";
}> {
  const value = citation.annotations?.corrections;
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as Record<string, unknown>;
    return (record.reason === "structured-value-conflict" ||
      record.reason === "document-value-conflict") &&
      typeof record.originalText === "string" &&
      typeof record.replacementText === "string"
      ? [
          {
            originalText: record.originalText,
            replacementText: record.replacementText,
            reason: record.reason,
          },
        ]
      : [];
  });
}

function containsMarkdownTable(content: string): boolean {
  return /(?:^|\n)\s*\|.+\|\s*\n\s*\|(?:\s*:?-+:?\s*\|)+/u.test(content);
}

function reactNodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (!isValidElement<{ children?: ReactNode }>(node)) return "";
  return Children.toArray(node.props.children).map(reactNodeText).join("");
}

/* eslint-disable @typescript-eslint/no-unused-vars -- Streamdown passes AST
   node props that must be stripped before forwarding attributes to the DOM. */
function CitationTableRow({
  children,
  className,
  node: _node,
  ...props
}: ComponentPropsWithoutRef<"tr"> & { node?: unknown }) {
  const cells = Children.toArray(children);
  const populatedCells = cells.filter((cell) => reactNodeText(cell).trim());
  const populatedCellIndex = cells.findIndex((cell) =>
    Boolean(reactNodeText(cell).trim()),
  );

  // PDF extractors represent section titles as a row whose first cell contains
  // text and whose remaining cells are empty. Render that row as a single
  // spanning section label instead of seven visually unrelated cells.
  if (
    cells.length > 1 &&
    populatedCells.length === 1 &&
    populatedCellIndex === 0 &&
    isValidElement(populatedCells[0])
  ) {
    const sectionCell = populatedCells[0] as ReactElement<{
      className?: string;
      colSpan?: number;
    }>;
    return (
      <tr
        {...props}
        className={cn("border-b border-surface-border", className)}
        data-citation-table-section
      >
        {cloneElement(sectionCell, {
          colSpan: cells.length,
          className: cn(
            "bg-surface-muted font-semibold text-ink-heading",
            sectionCell.props.className,
          ),
        })}
      </tr>
    );
  }

  return (
    <tr
      {...props}
      className={cn(
        "border-b border-surface-border last:border-b-0",
        className,
      )}
    >
      {children}
    </tr>
  );
}

const CITATION_MARKDOWN_COMPONENTS = {
  table: ({
    className,
    node: _node,
    ...props
  }: ComponentPropsWithoutRef<"table"> & { node?: unknown }) => (
    <table
      {...props}
      className={cn(
        "w-max min-w-full border-collapse text-2xs leading-4",
        className,
      )}
    />
  ),
  thead: ({
    className,
    node: _node,
    ...props
  }: ComponentPropsWithoutRef<"thead"> & { node?: unknown }) => (
    <thead {...props} className={cn("bg-surface-muted", className)} />
  ),
  tbody: ({
    className,
    node: _node,
    ...props
  }: ComponentPropsWithoutRef<"tbody"> & { node?: unknown }) => (
    <tbody {...props} className={className} />
  ),
  tr: CitationTableRow,
  th: ({
    className,
    node: _node,
    ...props
  }: ComponentPropsWithoutRef<"th"> & { node?: unknown }) => (
    <th
      {...props}
      className={cn(
        "whitespace-nowrap px-2 py-1.5 text-left text-2xs font-semibold leading-4",
        className,
      )}
    />
  ),
  td: ({
    className,
    node: _node,
    ...props
  }: ComponentPropsWithoutRef<"td"> & { node?: unknown }) => (
    <td
      {...props}
      className={cn(
        "whitespace-nowrap px-2 py-1 align-top text-2xs leading-4",
        className,
      )}
    />
  ),
  p: ({
    className,
    node: _node,
    ...props
  }: ComponentPropsWithoutRef<"p"> & { node?: unknown }) => (
    <p {...props} className={cn("m-0 [&+p]:mt-2", className)} />
  ),
} satisfies Components;
/* eslint-enable @typescript-eslint/no-unused-vars */

function CitationEvidenceMarkdown({ content }: { content: string }) {
  const hasTable = containsMarkdownTable(content);
  return (
    <div
      data-citation-evidence-text
      data-citation-evidence-table={hasTable || undefined}
      className={cn(
        "mt-1.5 text-ink-heading",
        hasTable
          ? cn(
              "rounded-md border border-surface-border",
              "[&_[data-streamdown=table-wrapper]]:!m-0",
              "[&_[data-streamdown=table-wrapper]]:!gap-0",
              "[&_[data-streamdown=table-wrapper]]:!border-0",
              "[&_[data-streamdown=table-wrapper]]:!bg-transparent",
              "[&_[data-streamdown=table-wrapper]]:!p-0",
              "[&_[data-streamdown=table-wrapper]>div]:!rounded-none",
              "[&_[data-streamdown=table-wrapper]>div]:!border-0",
            )
          : "border-l-2 border-primary/40 pl-2 pr-1 leading-5",
      )}
    >
      <Streamdown
        mode="static"
        controls={false}
        components={CITATION_MARKDOWN_COMPONENTS}
        skipHtml
        disallowedElements={["a", "img"]}
        unwrapDisallowed
      >
        {content}
      </Streamdown>
    </div>
  );
}

function CitationHoverCard({
  displayIndex,
  citation,
  side,
  position,
  canOpen,
  canOpenLinkedEvidence,
  onOpen,
  citationById,
  onOpenCitation,
  qualityIssues,
  cardRef,
  onMouseEnter,
  onMouseLeave,
}: {
  displayIndex?: number;
  citation: CitationRefV1;
  side: CitationCardSide;
  position: CitationCardPosition;
  canOpen: boolean;
  canOpenLinkedEvidence: boolean;
  onOpen: () => void;
  citationById?: ReadonlyMap<string, CitationRefV1>;
  onOpenCitation: (citationId: string) => void;
  qualityIssues?: CitationQualityDisplayIssue[];
  cardRef: (node: HTMLDivElement | null) => void;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}) {
  const { t } = useI18n();
  const [showFullEvidence, setShowFullEvidence] = useState(false);
  const evidenceScrollRef = useRef<HTMLDivElement>(null);
  const detail = evidenceText(
    citation,
    t("ui.citation.documentCoverageComplete"),
  );
  const attribution =
    citation.source.organization ??
    citation.source.author ??
    citation.source.providerId;
  const quality = qualityBadge(citation);
  const sourceTier = quality ? sourceTierId(quality.label) : null;
  const corrections = citationCorrections(citation);
  const qualityTone = qualityIssues?.some((issue) => issue.tone === "critical")
    ? "critical"
    : qualityIssues?.length
      ? "advisory"
      : undefined;
  const hasTable = containsMarkdownTable(detail.quote);
  const calculationInputs =
    citation.evidence.kind === "calculation"
      ? citation.evidence.inputs.flatMap((input) => {
          const source = citationById?.get(input.citationId);
          return source ? [{ input, source }] : [];
        })
      : [];
  const hasFocusedExcerpt = Boolean(detail.snippet);
  const primaryEvidence = detail.snippet ?? detail.quote;
  const isSearchSummary = citation.source.sourceCategory === "search_summary";
  const evidenceTitle =
    citation.evidence.kind === "structured-data"
      ? t("ui.citation.dataTitle")
      : citation.evidence.kind === "calculation"
        ? t("ui.citation.calculationTitle")
        : isSearchSummary
          ? t("ui.citation.searchSummaryTitle")
          : hasFocusedExcerpt
            ? t("ui.citation.relatedExcerptTitle")
            : t("ui.citation.evidenceTitle");
  const displayedEvidence =
    hasFocusedExcerpt && showFullEvidence ? detail.quote : primaryEvidence;
  const displayedEvidenceTitle =
    hasFocusedExcerpt && showFullEvidence
      ? t("ui.citation.fullEvidenceTitle")
      : evidenceTitle;
  const openActionLabel =
    citation.evidence.kind === "structured-data"
      ? t("ui.citation.viewData", "View data")
      : citation.evidence.kind === "calculation"
        ? t("ui.citation.calculationDetails", "View calculation")
        : t("ui.citation.openSource", "View original");
  const openSource = (event: ReactMouseEvent<HTMLElement>) => {
    event.stopPropagation();
    onOpen();
  };

  return (
    <div
      ref={cardRef}
      role="tooltip"
      data-citation-hover-card
      data-side={side}
      style={{ left: position.left, top: position.top }}
      className={cn(
        "fixed z-50 flex max-h-[min(440px,calc(100vh-32px))] flex-col overflow-visible rounded-lg border border-surface-border bg-surface text-left text-xs font-normal text-ink-body shadow-xl",
        hasTable
          ? "w-[min(680px,calc(100vw-32px))]"
          : "w-[min(420px,calc(100vw-32px))]",
      )}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <span
        data-citation-card-header
        className="flex shrink-0 items-start gap-2 border-b border-surface-border px-3 py-2.5"
      >
        <span className="min-w-0 flex-1">
          {canOpen ? (
            <button
              type="button"
              data-citation-title-link
              className="block w-full rounded-sm text-left font-medium text-ink-heading transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
              onClick={openSource}
            >
              {displayIndex ? `${displayIndex} ` : ""}
              {citation.source.title}
            </button>
          ) : (
            <span className="block font-medium text-ink-heading">
              {displayIndex ? `${displayIndex} ` : ""}
              {citation.source.title}
            </span>
          )}
          <span className="mt-0.5 block text-ink-meta">
            {[attribution, detail.time].filter(Boolean).join(" · ")}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-1">
          {quality ? (
            sourceTier ? (
              <SourceTierBadge
                quality={quality}
                instanceId={`card-${citation.citationId}`}
                labelClassName={cn(
                  "rounded px-1.5 py-0.5 text-2xs",
                  quality.status === "passed"
                    ? "bg-success-light text-success"
                    : qualityTone === "critical"
                      ? "bg-warning-light text-warning-text"
                      : "bg-surface-muted text-ink-meta",
                )}
              />
            ) : (
              <span
                data-citation-quality={quality.status}
                className={cn(
                  "rounded px-1.5 py-0.5 text-2xs",
                  quality.status === "passed"
                    ? "bg-success-light text-success"
                    : qualityTone === "critical"
                      ? "bg-warning-light text-warning-text"
                      : "bg-surface-muted text-ink-meta",
                )}
              >
                {quality.label}
              </span>
            )
          ) : null}
          {citation.resolutionStatus &&
          citation.resolutionStatus !== "ready" ? (
            <span className="rounded bg-surface-muted px-1.5 py-0.5 text-2xs text-ink-meta">
              {citation.resolutionStatus}
            </span>
          ) : null}
        </span>
      </span>
      <div
        data-citation-card-body
        className="flex min-h-0 flex-1 flex-col overflow-hidden px-3 py-2.5"
      >
        {corrections.length ? (
          <div
            data-citation-corrections
            className="mb-2 max-h-24 shrink-0 overflow-y-auto rounded-md bg-surface-muted/70 px-2.5 py-2 leading-5 text-ink-body"
          >
            {corrections.map((correction) => (
              <span
                key={`${correction.originalText}:${correction.replacementText}`}
                className="block"
              >
                {t(
                  correction.reason === "document-value-conflict"
                    ? "ui.citation.sourceValueCorrected"
                    : "ui.citation.structuredValueCorrected",
                  {
                    original: correction.originalText,
                    replacement: correction.replacementText,
                  },
                )}
              </span>
            ))}
          </div>
        ) : null}
        <div
          data-citation-evidence-section
          className="flex min-h-0 flex-1 flex-col"
        >
          <span className="block text-2xs font-medium text-ink-meta">
            {displayedEvidenceTitle}
          </span>
          <div
            ref={evidenceScrollRef}
            data-citation-evidence-scroll
            className="min-h-0 flex-1 overflow-y-auto pr-1"
          >
            <div
              data-citation-displayed-evidence={
                showFullEvidence ? "full" : "focused"
              }
            >
              <CitationEvidenceMarkdown content={displayedEvidence} />
            </div>
          </div>
          {hasFocusedExcerpt ? (
            <div className="mt-2 shrink-0 border-t border-surface-border pt-2">
              <button
                type="button"
                aria-expanded={showFullEvidence}
                data-citation-full-evidence-toggle
                className="flex w-full items-center justify-between rounded-sm text-left text-2xs font-medium text-ink-body transition-colors hover:text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
                onClick={(event) => {
                  event.stopPropagation();
                  if (evidenceScrollRef.current) {
                    evidenceScrollRef.current.scrollTop = 0;
                  }
                  setShowFullEvidence((current) => !current);
                }}
              >
                <span>
                  {showFullEvidence
                    ? t("ui.citation.hideFullEvidence")
                    : t("ui.citation.showFullEvidence")}
                </span>
                <ChevronDown
                  className={cn(
                    "h-3 w-3 text-ink-meta transition-transform",
                    showFullEvidence && "rotate-180",
                  )}
                  aria-hidden="true"
                />
              </button>
            </div>
          ) : null}
        </div>
        {qualityIssues?.length ? (
          <div
            data-citation-quality-issues={qualityTone}
            className={cn(
              "mt-2 flex max-h-24 shrink-0 items-start gap-1.5 overflow-y-auto rounded-md px-2.5 py-2 leading-5 text-ink-body",
              qualityTone === "critical"
                ? "bg-warning-light/50"
                : "bg-surface-muted/70",
            )}
          >
            <span className="flex h-5 shrink-0 items-center" aria-hidden="true">
              {qualityTone === "critical" ? (
                <AlertTriangle className="h-3.5 w-3.5 text-warning-text" />
              ) : (
                <Info className="h-3.5 w-3.5 text-ink-meta" />
              )}
            </span>
            <span>
              <span
                className={cn(
                  "font-medium",
                  qualityTone === "critical"
                    ? "text-warning-text"
                    : "text-ink-body",
                )}
              >
                {qualityTone === "critical"
                  ? t("ui.citation.qualityNeedsReview")
                  : t("ui.citation.qualityCheckSuggested")}
              </span>
              <span className="mx-1 text-ink-meta" aria-hidden="true">
                ·
              </span>
              {qualityIssues.map((issue) => issue.label).join(" · ")}
            </span>
          </div>
        ) : null}
        {calculationInputs.length ? (
          <span className="mt-2 block max-h-24 shrink-0 overflow-y-auto border-t border-surface-border pt-2">
            <span className="block text-2xs font-medium uppercase tracking-wide text-ink-meta">
              {t("ui.citation.calculationInputs", "Calculation inputs")}
            </span>
            <span className="mt-1 flex flex-col gap-1">
              {calculationInputs.map(({ input, source }) => {
                const disabled =
                  !canOpenLinkedEvidence ||
                  source.resolutionStatus === "forbidden" ||
                  source.resolutionStatus === "missing";
                return (
                  <button
                    key={`${input.name}:${input.citationId}`}
                    type="button"
                    disabled={disabled}
                    className="rounded px-1.5 py-1 text-left text-2xs text-ink-body hover:bg-surface-muted disabled:cursor-default disabled:opacity-60"
                    onClick={(event) => {
                      event.stopPropagation();
                      onOpenCitation(input.citationId);
                    }}
                  >
                    <span className="font-medium">{input.name}</span>
                    <span className="text-ink-meta">
                      {" "}
                      · {String(input.value)}
                      {input.unit ? ` ${input.unit}` : ""} · {source.source.title}
                    </span>
                  </button>
                );
              })}
            </span>
          </span>
        ) : null}
      </div>
      {canOpen ? (
        <div
          data-citation-card-footer
          className="flex shrink-0 justify-end border-t border-surface-border px-3 py-2"
        >
          <button
            type="button"
            className="group inline-flex items-center gap-1 rounded-sm font-medium text-primary no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
            onClick={openSource}
          >
            <span className="border-b border-dotted border-transparent leading-5 group-hover:border-current group-focus-visible:border-current">
              {openActionLabel}
            </span>
            <ExternalLink className="h-3 w-3" aria-hidden="true" />
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function CitationPill({
  citationId,
  displayIndex,
  citation,
  citationById,
  qualityIssues,
  messageId,
  onCitationClick,
  variant = "pill",
  sourceLabel,
}: {
  citationId: string;
  displayIndex?: number;
  citation?: CitationRefV1;
  citationById?: ReadonlyMap<string, CitationRefV1>;
  qualityIssues?: CitationQualityDisplayIssue[];
  messageId?: string;
  onCitationClick?: (input: OpenCitationInput) => void;
  variant?: "pill" | "source-row";
  sourceLabel?: string;
}) {
  const { t } = useI18n();
  const [hovered, setHovered] = useState(false);
  const [cardSide, setCardSide] = useState<CitationCardSide>("bottom");
  const [cardPosition, setCardPosition] = useState<CitationCardPosition>({
    left: VIEWPORT_PADDING_PX,
    top: VIEWPORT_PADDING_PX,
  });
  const triggerRef = useRef<HTMLElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const closeTimerRef = useRef<number | null>(null);
  const canOpen =
    Boolean(citation) &&
    citation?.evidence.kind !== "calculation" &&
    citation?.resolutionStatus !== "forbidden" &&
    citation?.resolutionStatus !== "missing" &&
    Boolean(onCitationClick);
  const isCalculation = citation?.evidence.kind === "calculation";
  const qualityStatus = qualityIssues?.some(
    (issue) => issue.tone === "critical",
  )
    ? "critical"
    : undefined;
  // Numbering belongs to the message body, not the sidecar.  A newer/missing
  // bundle must still render a stable, non-interactive number instead of
  // replacing the user's citation position with an ambiguous question mark.
  const indexLabel = displayIndex ? String(displayIndex) : "?";
  const open = () => {
    if (!canOpen) return;
    onCitationClick?.({ messageId, citationId });
  };
  const openCitation = (nextCitationId: string) => {
    if (!onCitationClick) return;
    onCitationClick({ messageId, citationId: nextCitationId });
  };
  const cancelScheduledClose = useCallback(() => {
    if (closeTimerRef.current === null) return;
    window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current = null;
  }, []);
  const showCard = useCallback(() => {
    cancelScheduledClose();
    setHovered(true);
  }, [cancelScheduledClose]);
  const scheduleClose = useCallback(() => {
    cancelScheduledClose();
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null;
      setHovered(false);
    }, HOVER_CLOSE_DELAY_MS);
  }, [cancelScheduledClose]);
  const updateCardPlacement = useCallback(() => {
    const trigger = triggerRef.current;
    const card = cardRef.current;
    if (!trigger || !card) return;

    const triggerRect = trigger.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const cardHeight = cardRect.height;
    const cardWidth = cardRect.width;
    const spaceBelow =
      window.innerHeight - triggerRect.bottom - VIEWPORT_PADDING_PX;
    const spaceAbove = triggerRect.top - VIEWPORT_PADDING_PX;
    const requiredSpace = cardHeight + HOVER_CARD_GAP_PX;
    const nextSide: CitationCardSide =
      spaceBelow >= requiredSpace || spaceBelow >= spaceAbove
        ? "bottom"
        : "top";
    setCardSide(nextSide);

    const preferredTop =
      nextSide === "bottom"
        ? triggerRect.bottom + HOVER_CARD_GAP_PX
        : triggerRect.top - cardHeight - HOVER_CARD_GAP_PX;
    const maxTop = Math.max(
      VIEWPORT_PADDING_PX,
      window.innerHeight - cardHeight - VIEWPORT_PADDING_PX,
    );
    const maxLeft = Math.max(
      VIEWPORT_PADDING_PX,
      window.innerWidth - cardWidth - VIEWPORT_PADDING_PX,
    );
    setCardPosition({
      left: Math.min(
        Math.max(
          triggerRect.left + triggerRect.width / 2 - cardWidth / 2,
          VIEWPORT_PADDING_PX,
        ),
        maxLeft,
      ),
      top: Math.min(Math.max(preferredTop, VIEWPORT_PADDING_PX), maxTop),
    });
  }, []);

  useLayoutEffect(() => {
    if (!hovered) return;
    updateCardPlacement();
    window.addEventListener("resize", updateCardPlacement);
    window.addEventListener("scroll", updateCardPlacement, true);
    return () => {
      window.removeEventListener("resize", updateCardPlacement);
      window.removeEventListener("scroll", updateCardPlacement, true);
    };
  }, [hovered, updateCardPlacement]);

  useEffect(() => cancelScheduledClose, [cancelScheduledClose]);

  return (
    <span
      className={cn(
        variant === "source-row"
          ? "relative block w-full"
          : "relative -top-px inline-flex align-middle leading-none",
        variant === "pill" ? (qualityStatus ? "mx-1" : "mx-0.5") : undefined,
      )}
      onMouseEnter={showCard}
      onMouseLeave={scheduleClose}
      onFocusCapture={showCard}
      onBlurCapture={(event) => {
        const next = event.relatedTarget;
        if (
          !(next instanceof Node) ||
          (!event.currentTarget.contains(next) &&
            !cardRef.current?.contains(next))
        ) {
          scheduleClose();
        }
      }}
    >
      {variant === "source-row" ? (
        <span
          ref={(node) => {
            triggerRef.current = node;
          }}
          tabIndex={0}
          aria-label={
            citation && displayIndex
              ? t("ui.citation.ariaLabel", "Citation {index}", {
                  index: displayIndex,
                })
              : t("ui.citation.unavailable", "Citation unavailable")
          }
          data-citation-id={citationId}
          data-citation-calculation-source
          className="flex w-full max-w-full cursor-default items-center rounded-md bg-transparent px-2 py-1 text-left text-xs text-ink-body outline-none transition hover:bg-surface-muted focus-visible:ring-2 focus-visible:ring-primary/20"
        >
          <span className="mr-1 font-semibold text-primary">{indexLabel}</span>
          <span className="min-w-0 truncate">{sourceLabel}</span>
        </span>
      ) : (
        <button
          ref={(node) => {
            triggerRef.current = node;
          }}
          type="button"
          aria-label={
            isCalculation
              ? t("ui.citation.calculationDetails", "Calculation details")
              : citation && displayIndex
                ? [
                    t("ui.citation.ariaLabel", "Citation {index}", {
                      index: displayIndex,
                    }),
                    qualityStatus ? t("ui.citation.qualityNeedsReview") : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")
                : t("ui.citation.unavailable", "Citation unavailable")
          }
          aria-disabled={!canOpen}
          data-citation-id={citationId}
          data-citation-derivation={isCalculation || undefined}
          data-citation-quality={qualityStatus}
          className={cn(
            "inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border p-0 font-medium leading-none tabular-nums no-underline transition-colors",
            qualityStatus === "critical"
              ? "border-warning/50 bg-warning-light/70 text-warning-text hover:bg-warning-light focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warning/20"
              : citation
                ? "border-surface-border bg-surface-muted text-ink-body hover:text-ink-heading focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/20"
                : "cursor-default border-surface-border bg-surface-muted text-ink-meta",
          )}
          onClick={open}
        >
          {isCalculation ? (
            <Calculator className="h-2.5 w-2.5" aria-hidden="true" />
          ) : (
            <span
              className={cn(
                "inline-flex h-full w-full items-center justify-center leading-none",
                indexLabel.length > 1 ? "text-micro" : "text-2xs",
              )}
              // At this 16px control size, a whole CSS pixel crosses the optical
              // centre on Retina displays. Half a pixel centres two tabular
              // digits without moving the circle or single-digit labels.
              style={
                indexLabel.length > 1
                  ? { transform: "translateX(-0.5px) scale(0.9)" }
                  : undefined
              }
            >
              {indexLabel}
            </span>
          )}
        </button>
      )}
      {hovered &&
      citation &&
      (displayIndex || isCalculation) &&
      typeof document !== "undefined"
        ? createPortal(
            <CitationHoverCard
              displayIndex={displayIndex}
              citation={citation}
              side={cardSide}
              position={cardPosition}
              canOpen={canOpen}
              canOpenLinkedEvidence={Boolean(onCitationClick)}
              onOpen={open}
              citationById={citationById}
              onOpenCitation={openCitation}
              qualityIssues={qualityIssues}
              cardRef={(node) => {
                cardRef.current = node;
              }}
              onMouseEnter={cancelScheduledClose}
              onMouseLeave={scheduleClose}
            />,
            document.body,
          )
        : null}
    </span>
  );
}

export function CitationSourceCards({
  content,
  citationBundle,
  messageId,
  onCitationClick,
  displayOrder,
  messageIdByCitationId,
}: {
  content: string;
  citationBundle?: CitationBundleV1;
  messageId?: string;
  onCitationClick?: (input: OpenCitationInput) => void;
  displayOrder?: ReadonlyMap<string, number>;
  messageIdByCitationId?: ReadonlyMap<string, string | undefined>;
}) {
  const { t } = useI18n();
  const sourceTierHelpTooltipId = `citation-source-tier-help-${useId().replace(/[^a-zA-Z0-9_-]/gu, "-")}`;
  const used = useMemo(
    () => usedCitations(content, citationBundle, displayOrder),
    [content, citationBundle, displayOrder],
  );
  const sourceGroups = useMemo(() => groupedCitationSources(used), [used]);
  if (!used.length) return null;

  return (
    <section
      data-citation-source-list
      className="mt-3 border-t border-surface-border pt-2"
    >
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-medium text-ink-meta">
          {t("ui.citation.sources", "Sources")}
        </h3>
        <span
          data-citation-source-tier-help
          className="group relative inline-flex cursor-help rounded text-2xs text-ink-meta transition-colors hover:text-ink-body focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
          tabIndex={0}
          aria-describedby={sourceTierHelpTooltipId}
        >
          {t("ui.citation.sourceTierHelp")}
          <SourceTierLegendTooltip
            tooltipId={sourceTierHelpTooltipId}
            align="left"
          />
        </span>
      </div>
      <div className="mt-1.5 flex flex-col gap-1.5">
        {sourceGroups.map(({ key, displayIndexes, citation }) => {
          const displayIndex = citationIndexLabel(displayIndexes);
          const citationMessageId =
            messageIdByCitationId?.get(citation.citationId) ?? messageId;
          const disabled =
            !onCitationClick ||
            citation.resolutionStatus === "forbidden" ||
            citation.resolutionStatus === "missing";
          const quality = qualityBadge(citation);
          return (
            <button
              key={key}
              type="button"
              disabled={disabled}
              onClick={() =>
                onCitationClick?.({
                  messageId: citationMessageId,
                  citationId: citation.citationId,
                })
              }
              className="w-full max-w-full truncate rounded-md bg-transparent px-2 py-1 text-left text-xs text-ink-body transition enabled:hover:bg-surface-muted disabled:cursor-default disabled:opacity-60"
            >
              <span className="mr-1 font-semibold text-primary">
                {displayIndex}
              </span>
              {citation.source.title}
              {quality ? (
                <>
                  <span
                    aria-hidden="true"
                    className="ml-1 text-2xs text-ink-meta"
                  >
                    ·
                  </span>
                  <span
                    data-citation-quality={quality.status}
                    className="ml-1 text-2xs text-ink-meta"
                  >
                    {quality.label}
                  </span>
                </>
              ) : null}
            </button>
          );
        })}
      </div>
    </section>
  );
}

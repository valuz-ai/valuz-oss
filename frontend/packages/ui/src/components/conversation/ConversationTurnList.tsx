import {
  memo,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Check,
  ChevronRight,
  Copy,
  FileText,
  Globe,
  Minimize2,
  RotateCw,
  Sparkles,
  Terminal,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { MarkdownContent } from "./MarkdownContent";
import {
  CitationSourceCards,
  citationDisplayOrder,
  projectEvidenceMarkdownLinks,
} from "./CitationInline";
import { ToolCallCard } from "../ToolCallCard";
import { ErrorMessageCard } from "./ErrorMessageCard";
import { FileUploadMessage } from "./FileUploadMessage";
import { TurnDiffSummaryCard } from "./TurnDiffSummaryCard";
import {
  aggregateTurnFileChanges,
  type TurnDiffSummary,
} from "./diff-aggregator";
import { SuggestionList } from "../common/SuggestionList";
import { LogoShimmer } from "../common/PageLoader";
import type {
  CitationBundleV1,
  ConversationTurn,
  OpenCitationInput,
  PrototypeToolCall,
} from "@valuz/shared";
import {
  assetUrl,
  summarizeSegmentPhrase,
  type ProcessingItem,
  type ToolCategory,
} from "@valuz/shared";
import { useI18n } from "../../hooks/use-i18n";
import { t as _t } from "@valuz/shared/i18n";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const MessageActions = ({
  text,
  onRetry,
}: {
  text: string;
  onRetry?: () => void;
}) => {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard denied — silent */
    }
  };

  return (
    <div className="mt-1 flex items-center gap-1">
      <button
        type="button"
        onClick={() => void handleCopy()}
        title={t("common.copy")}
        className="flex h-7 w-7 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
      >
        {copied ? (
          <Check className="h-3.5 w-3.5 text-success" />
        ) : (
          <Copy className="h-3.5 w-3.5" />
        )}
      </button>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          title={t("common.retry")}
          className="flex h-7 w-7 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
        >
          <RotateCw className="h-3.5 w-3.5" />
        </button>
      ) : null}
    </div>
  );
};

/** Conditional ``shouldAdjustScrollPositionOnItemSizeChange``: adjust
 * scrollTop only when the resizing row is ENTIRELY above the viewport.
 *
 *  - Row entirely above viewport (item.end ≤ scrollOffset): the user
 *    can't see the change, so it would visually look like the page
 *    drifted. Adjust scrollTop by the size delta to keep what IS in
 *    view stable. This fixes the "send a follow-up, then a previous
 *    turn's late layout (markdown table/image) shifts everything down
 *    and the new turn drifts out of viewport-top" bug.
 *  - Row partially or fully visible: don't adjust. Expanding a
 *    fully-visible toggle then settles by extending downward into the
 *    rows below — the natural chat-app behavior. The earlier
 *    unconditional ``() => false`` suppressed the first case along with
 *    the second.
 *
 * The signature matches tanstack-virtual's runtime hook. */
const VIRTUAL_SCROLL_ADJUSTMENT = (
  item: { start: number; size: number },
  _delta: number,
  instance: { scrollOffset: number | null },
): boolean => {
  const offset = instance.scrollOffset ?? 0;
  return item.start + item.size <= offset;
};

/** Format the turn-level total elapsed time. ``< 60s`` keeps seconds as-is;
 * once we cross a minute boundary we switch to ``M 分 S 秒`` so the user
 * doesn't have to count past 90+ seconds. */
const formatTurnElapsed = (elapsedMs: number | undefined): string => {
  const totalSec = Math.max(0, Math.round((elapsedMs ?? 0) / 1000));
  if (totalSec < 60)
    return _t("conversation.processedSeconds" as Parameters<typeof _t>[0], {
      count: String(totalSec),
    });
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return s === 0
    ? _t("conversation.processedMinutes" as Parameters<typeof _t>[0], {
        m: String(m),
      })
    : _t("conversation.processedMinutesSeconds" as Parameters<typeof _t>[0], {
        m: String(m),
        s: String(s),
      });
};

/** Bare duration, no leading verb — the ``{elapsed}`` slot of the
 * runtime-startup labels. Same M/S rounding as {@link formatTurnElapsed} so
 * the number doesn't change shape when the header flips phase. */
const formatDuration = (elapsedMs: number | undefined): string => {
  const totalSec = Math.max(0, Math.round((elapsedMs ?? 0) / 1000));
  if (totalSec < 60)
    return _t("conversation.durationSeconds" as Parameters<typeof _t>[0], {
      count: String(totalSec),
    });
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return s === 0
    ? _t("conversation.durationMinutes" as Parameters<typeof _t>[0], {
        m: String(m),
      })
    : _t("conversation.durationMinutesSeconds" as Parameters<typeof _t>[0], {
        m: String(m),
        s: String(s),
      });
};

/** Where the agent runtime for this turn is coming up — the renderer only
 * picks a string; the host page decides.
 *
 * ``"cloud"`` is unreachable in a plain OSS build: it is derived from the
 * session's execution origin, and origin comes from the ``entity-origin``
 * edition seam, which OSS leaves unregistered (see
 * ``core/src/edition/entity-origin.ts``). Single-backend OSS therefore always
 * reads ``"local"``; a multi-target edition registers the adapter and this
 * turns two-valued. The value name matches the target id it comes from
 * (``"cloud"``), so there is one word for the concept end to end. */
export type RuntimeStartLocation = "local" | "cloud";

/** Header text for the pre-run phase: the message is sent but the runtime is
 * still coming up, so "已处理" would be a lie. The counter itself keeps
 * running — only the verb changes when the runtime reports in. */
const formatRuntimeStarting = (
  location: RuntimeStartLocation,
  elapsedMs: number | undefined,
): string =>
  _t(
    (location === "cloud"
      ? "conversation.startingCloudRuntime"
      : "conversation.startingLocalRuntime") as Parameters<typeof _t>[0],
    { elapsed: formatDuration(elapsedMs) },
  );

/** How long a turn must stay in flight before its header appears at all.
 *
 * A local OSS runtime usually has the session created and the turn started
 * well inside this window, so without the delay the startup label renders for
 * a few frames and vanishes — worse than never showing it. Anything the user
 * can actually read takes longer than this; below it the composer's own
 * loading state is the feedback. */
const HEADER_REVEAL_DELAY_MS = 500;

const ICON_BY_CATEGORY: Record<ToolCategory, LucideIcon> = {
  search: Globe,
  fetch: Globe,
  shell: Terminal,
  read: FileText,
  write: FileText,
  edit: FileText,
  skill: Zap,
  mcp: Wrench,
  other: Wrench,
};

/** Build a Codex-style verb-phrase summary plus the leading icon for a
 * segment's tool/thinking trail. Data layer (bucket keys, render
 * templates, phrase joining) lives in ``@valuz/shared``; this thin
 * wrapper only resolves the icon since lucide-react isn't a shared dep.
 *
 *  ``浏览了 1 个网页，做了 3 次搜索，运行了 1 个命令``
 *
 * Pure-thinking / empty segments fall back to ``Sparkles`` — the shared
 * layer doesn't know about lucide and leaves that choice to the UI.
 */
const summarizeSegmentTools = (
  items: ProcessingItem[],
): { phrase: string; icon: LucideIcon } => {
  const { phrase, dominantCategory } = summarizeSegmentPhrase(items);
  const hasTool = items.some((i) => i.kind === "tool");
  if (!hasTool) {
    return { phrase, icon: Sparkles };
  }
  return { phrase, icon: ICON_BY_CATEGORY[dominantCategory] };
};

/**
 * A "segment" pairs an assistant message (the narration / what's about to
 * happen) with the thinking + tool calls that follow it before the next
 * assistant message, treating intermediate assistant text as the *header*
 * of the work that comes after it. The agent's natural rhythm is
 *
 *     assistant("plan") → thinking → tool → tool → assistant("next plan")
 *       → thinking → tool → ... → assistant("final answer")
 *
 * Pre-segment rendering lifted every assistant up to the top level and
 * folded *all* thinking/tool into a single block, so the user saw a
 * disembodied list of "let me do X" lines with no apparent work between
 * them. Segmented rendering keeps each plan attached to its own work.
 */
type DisplayBlock =
  | {
      kind: "segment";
      /** Intermediate / final assistant text, or ``null`` when the turn
       * starts with thinking/tool before any assistant has spoken. */
      header: string | null;
      /** Folded-by-default body. Empty when the segment is the final
       * answer (header text only, no further work). */
      items: ProcessingItem[];
      elapsedMs?: number;
      messageId?: string;
      citationBundle?: CitationBundleV1;
      /** ``true`` when this segment is the LAST assistant in the turn AND
       * has no trailing items — i.e. the actual final answer. The renderer
       * shows the header expanded as full Markdown without any fold UI. */
      final: boolean;
    }
  // Tool block whose rendering is overridden by the caller (e.g. the
  // SkillSubmissionCard for ``submit_skill`` tool_use). Lifted out of
  // the segment fold so the user can actually see and interact with it.
  | { kind: "tool-overridden"; tool: PrototypeToolCall; node: ReactNode }
  // Aggregated per-turn file-change card. Always sits at the END of the
  // turn (after every segment) and replaces the per-tool ToolCallCard
  // rendering for Edit / MultiEdit / Write blocks within that turn.
  | { kind: "turn-diff-summary"; summary: TurnDiffSummary }
  // Context-compaction divider (``/compact`` or autocompact). Meta, like the
  // diff summary: renders inline at the point compaction occurred, always
  // visible (never folded), and transparent to the fold-boundary walk.
  | { kind: "compaction" };

/**
 * Foldable strip showing the segment's tool/thinking trail. The trigger
 * label is a Codex-style tool summary (``搜索网页 6 次 · 执行命令 2 次``);
 * the chevron toggles the body. Header text (the assistant message that
 * opened the segment) is rendered separately by the caller — this
 * component owns only the chevron + body.
 */
const SegmentDetails = ({
  items,
  inProgress = false,
}: {
  items: ProcessingItem[];
  /** ``true`` when this is the turn's currently-running segment (the
   * agent is still firing tools inside it). Drives the shimmer sweep on
   * the summary phrase so the user sees the count is "still updating",
   * not a finished tally. */
  inProgress?: boolean;
}) => {
  const [open, setOpen] = useState(false);
  // Codex-style verb-phrase summary + leading icon — e.g.
  //   <Globe/> 浏览了 1 个网页，做了 3 次搜索，运行了 1 个命令
  // Replaces the old "已处理 N 秒 · X 次工具调用" pill. The elapsed time
  // now lives at the turn-level header where it belongs as turn-wide
  // metadata; the per-segment label focuses on what the agent did.
  const { phrase, icon: Icon } = summarizeSegmentTools(items);

  // Shimmer style — applied only while inProgress. A wider-than-text
  // linear gradient is clipped to the glyphs via ``background-clip:
  // text``; animating ``background-position`` then slides the highlight
  // band across the letters. Same 2s rhythm as the LogoLoader.
  const shimmerStyle: React.CSSProperties | undefined = inProgress
    ? {
        backgroundImage:
          "linear-gradient(90deg, #6e7481 0%, #6e7481 35%, #c1c4cc 50%, #6e7481 65%, #6e7481 100%)",
        backgroundSize: "200% 100%",
        backgroundClip: "text",
        WebkitBackgroundClip: "text",
        color: "transparent",
        WebkitTextFillColor: "transparent",
      }
    : undefined;

  return (
    <div className="font-sans text-xs leading-[1.7] text-ink-body">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-1.5 py-1 text-left text-xs font-normal text-ink-body transition-colors hover:text-ink-heading"
        aria-expanded={open}
      >
        <Icon
          className={`h-3 w-3 shrink-0 ${
            inProgress ? "animate-[shimmer-icon_2s_linear_infinite]" : ""
          }`}
          aria-hidden="true"
        />
        <span
          className={
            inProgress ? "animate-[shimmer-text_2s_linear_infinite]" : undefined
          }
          // Stagger the text shimmer behind the icon by 300ms so the
          // highlight reads as "icon flashes → sweep enters text", not
          // a simultaneous peak. The icon's keyframe peaks at 0–25% of
          // its 2s cycle (i.e. up to t≈0.5s), so a 0.3s text delay puts
          // the text peak right on the tail of the icon flash.
          style={
            inProgress
              ? { ...shimmerStyle, animationDelay: "0.3s" }
              : shimmerStyle
          }
        >
          {phrase}
        </span>
        <ChevronRight
          className={`h-3 w-3 shrink-0 transition-transform ${
            open ? "rotate-90" : ""
          }`}
          aria-hidden="true"
        />
      </button>
      {/* Divider line removed — the chevron itself signals foldability now.
          The turn-level TurnProcessHeader keeps the divider so the boundary
          between user input and process is still clear. */}
      {open ? (
        <div className="space-y-3 py-2">
          {items.map((item, idx) =>
            item.kind === "thinking" ? (
              <div
                key={`thinking-${idx}`}
                className="whitespace-pre-wrap text-ink-body"
              >
                {item.text}
              </div>
            ) : (
              <ToolCallCard key={`tool-${item.tool.id}`} tc={item.tool} />
            ),
          )}
        </div>
      ) : null}
    </div>
  );
};

/** Single unified marker for a context compaction (``/compact`` or
 *  autocompact), for either runtime. Intentionally label-only — the kernel
 *  ``compaction`` event's raw data is not parsed for display here. */
const CompactionDivider = () => {
  const { t } = useI18n();
  return (
    <div
      className="flex items-center gap-2 py-1 text-xs text-[#6e7481]"
      role="status"
    >
      <span className="h-px flex-1 bg-border" />
      <span className="inline-flex shrink-0 items-center gap-1.5">
        <Minimize2 className="h-3 w-3" aria-hidden="true" />
        <span>{t("conversation.contextCompacted")}</span>
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
};

const buildDisplayBlocks = (
  turn: ConversationTurn,
  renderToolCall?: (tool: PrototypeToolCall) => ReactNode | null,
): DisplayBlock[] => {
  // Edit / MultiEdit / Write tool blocks render through the regular
  // per-segment ToolCallCard path AND get aggregated into the turn-level
  // diff summary card at the end — the summary is purely additive, not
  // a replacement, so users keep their per-tool detail and gain a
  // one-glance file/diff overview.
  const blocks = turn.blocks;
  // Phase 1: identify caller-overridden tools (rendered inline as their own
  // block; never folded into a segment body).
  const overrideMap = new Map<string, ReactNode>();
  for (const block of blocks) {
    if (block.kind === "tool" && renderToolCall) {
      const node = renderToolCall(block.tool);
      if (node) overrideMap.set(block.tool.id, node);
    }
  }

  // Phase 2: locate the LAST assistant block in the turn — it owns the
  // "final" flag IF nothing tool-related follows it. Used after the walk.
  let lastAssistantIdx = -1;
  for (let i = blocks.length - 1; i >= 0; i -= 1) {
    if (blocks[i]!.kind === "assistant") {
      lastAssistantIdx = i;
      break;
    }
  }

  // Phase 3: walk blocks, accumulate one segment at a time. Each new
  // ``assistant`` block flushes the in-flight segment and opens a new one.
  const result: DisplayBlock[] = [];
  let cur: {
    header: string | null;
    items: ProcessingItem[];
    elapsedMs: number | undefined;
    messageId?: string;
    citationBundle?: CitationBundleV1;
    /** Index into ``blocks`` where this segment's assistant header sits;
     * -1 when the segment opened with thinking/tool before any assistant. */
    headerIdx: number;
  } | null = null;
  let lastFlushedHeaderIdx = -1;

  const flush = () => {
    if (cur === null) return;
    // Empty (no header text and no items) — drop it; it carries no info.
    if (cur.header === null && cur.items.length === 0) {
      cur = null;
      return;
    }
    result.push({
      kind: "segment",
      header: cur.header,
      items: cur.items,
      elapsedMs: cur.elapsedMs,
      messageId: cur.messageId,
      citationBundle: cur.citationBundle,
      final: false, // patched after the loop, only for the very last segment
    });
    lastFlushedHeaderIdx = cur.headerIdx;
    cur = null;
  };

  for (let i = 0; i < blocks.length; i += 1) {
    const block = blocks[i]!;

    if (block.kind === "tool" && overrideMap.has(block.tool.id)) {
      // Override tools break the segment: flush, emit the inline node,
      // then leave ``cur`` empty so the next assistant / tool starts a
      // fresh segment after the override card.
      flush();
      result.push({
        kind: "tool-overridden",
        tool: block.tool,
        node: overrideMap.get(block.tool.id)!,
      });
      continue;
    }

    if (block.kind === "assistant") {
      flush();
      cur = {
        header: block.text,
        items: [],
        elapsedMs: undefined,
        headerIdx: i,
        messageId: block.messageId,
        citationBundle: block.citationBundle,
      };
      continue;
    }

    if (block.kind === "thinking") {
      if (cur === null) {
        cur = { header: null, items: [], elapsedMs: undefined, headerIdx: -1 };
      }
      if (block.text) cur.items.push({ kind: "thinking", text: block.text });
      if (block.elapsedMs !== undefined) {
        cur.elapsedMs = Math.max(cur.elapsedMs ?? 0, block.elapsedMs);
      }
      continue;
    }

    if (block.kind === "tool") {
      // (overridden case handled above)
      if (cur === null) {
        cur = { header: null, items: [], elapsedMs: undefined, headerIdx: -1 };
      }
      cur.items.push({ kind: "tool", tool: block.tool });
      if (block.elapsedMs !== undefined) {
        cur.elapsedMs = Math.max(cur.elapsedMs ?? 0, block.elapsedMs);
      }
      continue;
    }

    if (block.kind === "compaction") {
      // Break the segment so the divider renders inline at the point the
      // context was compacted, then leave ``cur`` empty so the next
      // assistant/tool opens a fresh segment after it.
      flush();
      result.push({ kind: "compaction" });
      continue;
    }
  }
  flush();

  // Phase 4: mark the very last segment as "final" iff it owns the
  // turn-final assistant AND has no trailing work — i.e. the answer the
  // user is here to read. Otherwise (turn ended on a tool, run was
  // cancelled mid-step, etc.) the trailing segment stays foldable.
  for (let i = result.length - 1; i >= 0; i -= 1) {
    const block = result[i]!;
    if (block.kind !== "segment") continue;
    if (
      block.items.length === 0 &&
      block.header !== null &&
      lastFlushedHeaderIdx === lastAssistantIdx
    ) {
      result[i] = { ...block, final: true };
    }
    break;
  }

  // Phase 5: aggregate file changes from the turn's Edit/MultiEdit/Write
  // tool blocks (the originals from ``turn.blocks``, not the filtered
  // ``blocks`` we walked above) and append a single diff-summary card
  // at the end. ``aggregateTurnFileChanges`` returns ``null`` when the
  // turn made no file changes, so non-coding turns get no card.
  const diffSummary = aggregateTurnFileChanges(turn);
  if (diffSummary) {
    result.push({ kind: "turn-diff-summary", summary: diffSummary });
  }

  return result;
};

function buildTrailingCitationContext(
  blocks: DisplayBlock[],
  startIndex: number,
): {
  content: string;
  bundle?: CitationBundleV1;
  displayOrder: ReadonlyMap<string, number>;
  messageIdByCitationId: ReadonlyMap<string, string | undefined>;
} {
  const content: string[] = [];
  const citations = new Map<
    string,
    CitationBundleV1["citations"][number]
  >();
  const displayOrder = new Map<string, number>();
  const messageIdByCitationId = new Map<string, string | undefined>();

  for (let index = startIndex; index < blocks.length; index += 1) {
    const block = blocks[index];
    if (block?.kind !== "segment" || block.header === null) continue;
    const projectedHeader = projectEvidenceMarkdownLinks(
      block.header,
      block.citationBundle,
    );
    content.push(projectedHeader);

    const localCitations = new Map(
      block.citationBundle?.citations.map((citation) => [
        citation.citationId,
        citation,
      ]) ?? [],
    );
    for (const citation of block.citationBundle?.citations ?? []) {
      if (!citations.has(citation.citationId)) {
        citations.set(citation.citationId, citation);
        messageIdByCitationId.set(citation.citationId, block.messageId);
      }
    }
    for (const citationId of citationDisplayOrder(projectedHeader).keys()) {
      if (!displayOrder.has(citationId)) {
        displayOrder.set(citationId, displayOrder.size + 1);
      }
      const citation = localCitations.get(citationId);
      if (citation && !citations.has(citationId)) {
        citations.set(citationId, citation);
        messageIdByCitationId.set(citationId, block.messageId);
      }
    }
  }

  return {
    content: content.join("\n\n"),
    bundle: citations.size
      ? { version: 1, citations: Array.from(citations.values()) }
      : undefined,
    displayOrder,
    messageIdByCitationId,
  };
}

const formatTurnTime = (ms: number | undefined): string => {
  if (!ms) return "";
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "";
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return `${hh}:${mi}`;
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
};

const UserMessageActions = ({
  text,
  timestamp,
}: {
  text: string;
  timestamp?: number;
}) => {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard denied — silent */
    }
  };

  const formatted = formatTurnTime(timestamp);

  return (
    <div className="mt-0.5 flex items-center gap-1">
      {formatted ? (
        <span className="px-1 text-[11px] text-ink-muted opacity-0 transition-opacity group-hover:opacity-100">
          {formatted}
        </span>
      ) : null}
      <button
        type="button"
        onClick={() => void handleCopy()}
        title={t("common.copy")}
        className="flex h-7 w-7 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted"
      >
        {copied ? (
          <Check className="h-3.5 w-3.5 text-success" />
        ) : (
          <Copy className="h-3.5 w-3.5" />
        )}
      </button>
    </div>
  );
};

const UserMessageBody = ({
  text,
  skillsBySlug,
}: {
  text: string;
  skillsBySlug?: Record<string, { name: string }>;
}) => {
  const skillTokenRe = /(^|\s)\/([a-zA-Z0-9_-]+)(?=\s|$)/g;
  // The message's leading run of ``/command`` tokens — what the composer
  // prepends and the backend's ``_SKILL_PREFIX_RE`` strips for the title
  // (``/goal ``, ``/skill ``, …). Tokens inside it are genuine command / skill
  // invocations and chip even when they aren't in the skill catalogue.
  const prefixLen =
    text.match(/^\s*(?:\/[a-zA-Z0-9_-]+(?:\s+|$))+/)?.[0].length ?? 0;
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = skillTokenRe.exec(text)) !== null) {
    const [whole, leading, slug] = match;
    const tokenStart = match.index + leading.length;
    const entry = skillsBySlug?.[slug];
    const isLeadingCommand = tokenStart < prefixLen;
    // Chip a ``/word`` only when it's a known skill OR a leading command. A
    // bare ``/word`` in the body that's neither — a file-path segment
    // (``/Users/pawa/Data/…``), a CLI flag, a URL path — is left as literal
    // text, never a phantom skill tag. (Whitespace, incl. a stray ``\r``
    // between path segments, is what let the token regex bite a directory
    // listing in the first place.)
    if (!entry && !isLeadingCommand) continue;
    if (tokenStart > lastIndex) {
      parts.push(text.slice(lastIndex, tokenStart));
    }
    parts.push(
      <span
        key={`s-${key++}`}
        className="mr-0.5 inline-flex h-5 items-center gap-1 rounded-[4px] border border-brand/20 bg-brand-100 px-2 py-0 text-2xs text-brand-700 align-middle select-none"
      >
        <Zap className="h-3 w-3" />
        {entry?.name ?? slug}
      </span>,
    );
    lastIndex = match.index + whole.length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return <>{parts}</>;
};

interface TurnRowProps {
  turn: ConversationTurn;
  isLatest: boolean;
  sending: boolean;
  skillsBySlug?: Record<string, { name: string }>;
  onRetry?: (turnId: string) => void;
  onSwitchModel?: (turnId: string) => void;
  retryCount: number;
  /** Optional override for rendering a tool block. Returning ``null``
   * (or omitting the prop) falls back to the generic ToolCallCard.
   * Used by the conversation page to render the SkillSubmissionCard
   * for ``submit_skill`` tool_use events. */
  renderToolCall?: (tool: PrototypeToolCall) => ReactNode | null;
  /** Predicate marking an overridden tool card as *foldable* — it collapses
   * away with the process trail when the turn ends (visible while running or
   * when the turn is expanded), instead of staying pinned at its position.
   * Returns ``false``/omitted → the card stays pinned (the default for
   * proposals, skill submissions, workflow & task cards). The conversation
   * page marks ``AskUserQuestion`` cards foldable. */
  isToolCardFoldable?: (tool: PrototypeToolCall) => boolean;
  /** Reveal a file in the host OS (Finder on macOS, Explorer on
   * Windows). Wired by the desktop app to the ``open_in_finder`` IPC;
   * webui omits this and the per-row external-link icon hides. */
  onRevealFile?: (filePath: string) => void;
  /** Predicate + handler for local-path markdown links emitted by an agent. */
  isLocalFileHref?: (href: string) => boolean;
  onLocalFileLinkClick?: (href: string) => void;
  onCitationClick?: (input: OpenCitationInput) => void;
  /** See ``ConversationTurnListProps.startingRuntime``. */
  startingRuntime?: RuntimeStartLocation | null;
}

const TurnRow = memo(
  function TurnRow({
    turn,
    isLatest,
    sending,
    skillsBySlug,
    onRetry,
    onSwitchModel,
    retryCount,
    renderToolCall,
    isToolCardFoldable,
    onRevealFile,
    isLocalFileHref,
    onLocalFileLinkClick,
    onCitationClick,
    startingRuntime,
  }: TurnRowProps) {
    const { t } = useI18n();
    const inFlight = sending && isLatest;
    const lastBlock = turn.blocks[turn.blocks.length - 1];
    const showStreamingCaret = inFlight && lastBlock?.kind === "assistant";
    const showLoadingDots = inFlight && !turn.failedMessage;
    const displayBlocks = buildDisplayBlocks(turn, renderToolCall);
    const assistantText = turn.blocks
      .filter((b) => b.kind === "assistant")
      .map((b) => b.text)
      .join("\n\n");
    // A user cancel and a runtime/system interruption both render as a quiet
    // grey line, but with distinct labels — a system interruption must NOT read
    // as "用户取消了当前对话".
    const interruptLabel = turn.cancelled
      ? t("conversation.userCancelled")
      : turn.interrupted
        ? t("conversation.runtimeInterrupted")
        : null;
    const actionText = assistantText || interruptLabel || "";

    // Turn-level meta: total elapsed (max of any block's elapsedMs) and
    // whether the turn has any process content worth surfacing as a
    // "Worked for X" header. Always present so direct one-shot
    // answers (no thinking / tool work) read consistently with
    // tool-using turns. Falls back to the wall-clock between
    // ``userTimestamp`` and ``endTimestamp`` when no block carries
    // an elapsedMs.
    const totalElapsedMs = useMemo(() => {
      let max = 0;
      for (const block of turn.blocks) {
        if (
          (block.kind === "thinking" || block.kind === "tool") &&
          block.elapsedMs !== undefined
        ) {
          if (block.elapsedMs > max) max = block.elapsedMs;
        }
      }
      if (max === 0 && turn.userTimestamp && turn.endTimestamp) {
        const start = new Date(turn.userTimestamp).getTime();
        const end = new Date(turn.endTimestamp).getTime();
        if (!Number.isNaN(start) && !Number.isNaN(end)) {
          max = Math.max(0, end - start);
        }
      }
      return max;
    }, [turn.blocks, turn.userTimestamp, turn.endTimestamp]);
    const hasProcess = useMemo(() => {
      return turn.blocks.some(
        (b) => b.kind === "thinking" || b.kind === "tool",
      );
    }, [turn.blocks]);
    // First index of the "trailing content run" — every block at this
    // index or later stays visible when the turn-level header is
    // folded; everything before it is process work that gets hidden.
    //
    // Walk backwards and stop at the first segment that either:
    //   (a) has no header — a pure process wrapper (thinking-only
    //       turns put their thinking here BEFORE the answer surface),
    //   (b) contains a *tool* call — that segment's assistant text
    //       (if any) is intermediate narration spoken WHILE the agent
    //       was working, not the final answer; folding hides it
    //       along with the tool churn.
    // Segments with a header but only thinking items survive the
    // walk (case "long answer + brief thinking + closing remark"),
    // since their text is part of the answer narration.
    const trailingContentStart = useMemo(() => {
      for (let i = displayBlocks.length - 1; i >= 0; i -= 1) {
        const b = displayBlocks[i];
        if (!b) continue;
        // The turn-level diff summary card is meta — it sits at the very
        // end of the turn, never participates in the fold, and must be
        // transparent to this walk. If we let it bail out the loop the
        // boundary would land at ``displayBlocks.length`` and the actual
        // answer segment(s) before it would get folded away.
        if (b.kind === "turn-diff-summary") continue;
        // The compaction divider is meta — always visible, never folded —
        // so it must be transparent to this walk (same as the diff summary).
        if (b.kind === "compaction") continue;
        // A caller-overridden card (AskUserQuestion, agent/automation proposals,
        // SkillSubmission, workflow & task cards…) is NOT transparent here: it
        // acts as a fold boundary so the narration/process *before* it folds
        // away, leaving just the card. The card itself is never hidden — it is
        // rendered before the fold check below.
        if (b.kind !== "segment") return i + 1;
        if (b.header === null) return i + 1;
        if (b.items.some((item) => item.kind === "tool")) return i + 1;
      }
      return 0;
    }, [displayBlocks]);
    const trailingCitationContext = useMemo(
      () => buildTrailingCitationContext(displayBlocks, trailingContentStart),
      [displayBlocks, trailingContentStart],
    );

    // Auto-fold the process trail when the turn finishes streaming. The
    // header is informational during streaming (no chevron, no fold);
    // becomes foldable + defaults to ``folded=true`` once inFlight flips
    // to ``false``. History-loaded turns (never inFlight) start folded.
    const [turnFolded, setTurnFolded] = useState(!inFlight);
    const prevInFlightRef = useRef(inFlight);
    useEffect(() => {
      if (prevInFlightRef.current && !inFlight) {
        // Turn finished → auto-fold the process trail.
        setTurnFolded(true);
      } else if (!prevInFlightRef.current && inFlight) {
        // Turn became live (e.g. VIEWING a running task session: the first
        // render had sending=false so turnFolded initialised to true, then
        // the live subscribe flipped sending→true). UNFOLD so the streaming
        // process blocks (thinking/tool) render instead of staying hidden —
        // otherwise a viewed live turn shows only the header + shimmer.
        setTurnFolded(false);
      }
      prevInFlightRef.current = inFlight;
    }, [inFlight]);
    const headerFoldable = !inFlight && hasProcess;

    // While streaming, tick a 1Hz wall-clock interval so the header advances
    // every second even between SSE event arrivals (otherwise the displayed
    // elapsed only updates when a new tool/thinking block lands, which feels
    // stuck during e.g. a long Bash run).
    const [tick, setTick] = useState(0);
    useEffect(() => {
      if (!inFlight) return;
      const interval = window.setInterval(() => {
        setTick((t) => t + 1);
      }, 1000);
      return () => window.clearInterval(interval);
    }, [inFlight]);

    // TWO counters, deliberately not one.
    //
    // The startup phase is measured on the CLIENT clock (Send → now) and the
    // processing phase on the SERVER one (the kernel's ``message.user`` stamp
    // → now). Splitting them is what keeps a live turn and a reloaded one
    // agreeing: ``clientSentAtMs`` is React state, so it is gone after a
    // refresh, and a single counter spanning both phases would therefore show
    // the startup window before a refresh and hide it after — a gap of tens of
    // seconds on a cold sandbox. Restarting at zero when the runtime reports
    // in also makes "已处理" mean what it says, and it removes the only place
    // the two clocks were ever subtracted from each other.
    const startupElapsedMs = useMemo(() => {
      void tick;
      if (turn.clientSentAtMs === undefined) return 0;
      return Math.max(0, Date.now() - turn.clientSentAtMs);
    }, [tick, turn.clientSentAtMs]);
    const processedElapsedMs = useMemo(() => {
      void tick;
      if (!inFlight || !turn.userTimestamp) return totalElapsedMs;
      const startMs = new Date(turn.userTimestamp).getTime();
      if (Number.isNaN(startMs)) return totalElapsedMs;
      // Never go backwards from the canonical block-derived elapsed: on rare
      // wall-clock skew ``Date.now()`` can sit below the latest block stamp.
      return Math.max(Date.now() - startMs, totalElapsedMs);
    }, [tick, inFlight, turn.userTimestamp, totalElapsedMs]);

    // Hold the header back for the first {@link HEADER_REVEAL_DELAY_MS} of a
    // turn so a fast local runtime doesn't flash "正在启动本地运行环境" for a
    // few frames on its way to "已处理". The composer's own loading state
    // covers the gap. Only in-flight turns the host page stamped are held —
    // history has no Send time and renders immediately.
    const [headerRevealed, setHeaderRevealed] = useState(
      () => turn.clientSentAtMs === undefined,
    );
    useEffect(() => {
      if (!inFlight || turn.clientSentAtMs === undefined) {
        setHeaderRevealed(true);
        return;
      }
      const remaining =
        HEADER_REVEAL_DELAY_MS - (Date.now() - turn.clientSentAtMs);
      if (remaining <= 0) {
        setHeaderRevealed(true);
        return;
      }
      const handle = window.setTimeout(
        () => setHeaderRevealed(true),
        remaining,
      );
      return () => window.clearTimeout(handle);
    }, [inFlight, turn.clientSentAtMs]);

    // Phase copy. Before the runtime reports in there is nothing being
    // "processed" yet, so the header names what IS happening (a local or cloud
    // runtime coming up) on its own counter. ``startingRuntime`` is
    // null/undefined once the host page sees the kernel's ``message.user``
    // echo — and for every settled turn.
    const isStartingUp = inFlight && startingRuntime != null;
    const headerLabel = !headerRevealed
      ? null
      : isStartingUp
        ? formatRuntimeStarting(startingRuntime, startupElapsedMs)
        : formatTurnElapsed(processedElapsedMs);
    return (
      <div data-conversation-turn className="space-y-[26px]">
        {turn.userText || (turn.attachments && turn.attachments.length > 0) ? (
          <div className="group flex flex-col items-end gap-1">
            {turn.userText ? (
              <div className="max-w-[78%]">
                <div className="whitespace-pre-wrap rounded-xl bg-surface-soft px-3.5 py-3 text-base leading-[1.6] text-ink-heading">
                  <UserMessageBody
                    text={turn.userText}
                    skillsBySlug={skillsBySlug}
                  />
                </div>
              </div>
            ) : null}
            {turn.attachments?.map((att, i) => (
              <FileUploadMessage
                key={`att-${turn.id}-${i}`}
                fileName={att.name}
                fileSize={att.size > 0 ? formatFileSize(att.size) : undefined}
                status="ready"
              />
            ))}
            {turn.userText ? (
              <UserMessageActions
                text={turn.userText}
                timestamp={turn.userTimestamp}
              />
            ) : null}
          </div>
        ) : null}

        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1 space-y-3">
            {/* Turn-level "Worked for Xm Ys" header — always visible when the
                turn has any thinking/tool work. While streaming it's a
                static label; once the turn finishes it gains a chevron and
                auto-folds, hiding all segments except the final answer.
                Divider line removed: it visually competed with the ``<hr>``
                markdown the agent often emits at the top of the final
                answer. The grey chevron strip alone is enough boundary. */}
            {/* Omitted entirely (not rendered empty) for the first
                ``HEADER_REVEAL_DELAY_MS`` of a turn: an empty wrapper would
                still take a ``space-y-3`` gap and the row would visibly shift
                when the label appeared. */}
            {headerLabel === null ? null : (
              <div className="font-sans text-[13px] leading-[1.6] text-[#6e7481]">
                {headerFoldable ? (
                  <button
                    type="button"
                    onClick={() => setTurnFolded((value) => !value)}
                    className="inline-flex items-center py-1 text-left text-[13px] font-normal text-[#6e7481] transition-colors hover:text-[#525860]"
                    aria-expanded={!turnFolded}
                  >
                    <span>{headerLabel}</span>
                    <ChevronRight
                      className={`ml-1 h-3.5 w-3.5 shrink-0 transition-transform ${
                        !turnFolded ? "rotate-90" : ""
                      }`}
                      aria-hidden="true"
                    />
                  </button>
                ) : (
                  <div className="inline-flex items-center py-1 text-[13px] font-normal text-[#6e7481]">
                    <span>{headerLabel}</span>
                  </div>
                )}
              </div>
            )}

            {displayBlocks.map((block, blockIndex) => {
              const isLastBlock = blockIndex === displayBlocks.length - 1;
              if (block.kind === "compaction") {
                // Meta marker — render the divider before the fold check so
                // it stays visible even when the process trail is folded.
                return (
                  <CompactionDivider
                    key={`compaction-${turn.id}-${blockIndex}`}
                  />
                );
              }
              if (
                block.kind === "tool-overridden" &&
                !isToolCardFoldable?.(block.tool)
              ) {
                // Pinned caller card (agent/automation proposals, SkillSubmission,
                // workflow & task cards…). It appears mid-process but must stay at
                // its original position after the turn ends — render BEFORE the
                // fold check (like the compaction divider) so the auto-fold never
                // hides it.
                return <div key={`tool-${block.tool.id}`}>{block.node}</div>;
              }
              // When the turn-level header is folded, hide every block
              // before ``trailingContentStart`` — that's the process work
              // (tool calls + their narration). Blocks at or after it are
              // "answer content" and stay visible.
              if (turnFolded && blockIndex < trailingContentStart) {
                return null;
              }
              if (block.kind === "tool-overridden") {
                // Foldable caller card (e.g. AskUserQuestion) — rendered AFTER
                // the fold check, so it collapses away with the process trail
                // once the turn ends; visible while running or when the user
                // expands the turn.
                return <div key={`tool-${block.tool.id}`}>{block.node}</div>;
              }
              if (block.kind === "turn-diff-summary") {
                return (
                  <TurnDiffSummaryCard
                    key={`diff-summary-${turn.id}`}
                    summary={block.summary}
                    onRevealFile={onRevealFile}
                  />
                );
              }
              // Segment: an intermediate / final assistant message paired
              // with its trailing thinking + tool work. Header (Markdown)
              // is always inline so the user can read the agent's plan;
              // the trailing work folds behind a "已处理 N 秒" chevron.
              // The final-answer segment (last assistant + no work after)
              // skips the fold strip entirely and just renders the text.
              const animateHeader =
                isLastBlock &&
                showStreamingCaret &&
                block.items.length === 0 &&
                block.header !== null;
              const isTrailingAnswer = blockIndex >= trailingContentStart;
              return (
                <div
                  key={`segment-${turn.id}-${blockIndex}`}
                  className="space-y-3"
                >
                  {block.header !== null ? (
                    <MarkdownContent
                      content={block.header}
                      isAnimating={animateHeader}
                      isLocalFileHref={isLocalFileHref}
                      onLocalFileLinkClick={onLocalFileLinkClick}
                      citationBundle={block.citationBundle}
                      messageId={block.messageId}
                      onCitationClick={onCitationClick}
                      citationDisplayOrderOverride={
                        isTrailingAnswer
                          ? trailingCitationContext.displayOrder
                          : undefined
                      }
                      citationLookupBundleOverride={
                        isTrailingAnswer
                          ? trailingCitationContext.bundle
                          : undefined
                      }
                      citationMessageIdByCitationIdOverride={
                        isTrailingAnswer
                          ? trailingCitationContext.messageIdByCitationId
                          : undefined
                      }
                      showCitationSources={!isTrailingAnswer}
                    />
                  ) : null}
                  {block.items.length > 0 ? (
                    <SegmentDetails
                      items={block.items}
                      // The agent is still firing tools inside the LAST
                      // segment of an in-flight turn. Earlier segments
                      // are already "closed" because the agent moved on
                      // (a new assistant message opened the next segment).
                      inProgress={inFlight && isLastBlock}
                    />
                  ) : null}
                </div>
              );
            })}

            <CitationSourceCards
              content={trailingCitationContext.content}
              citationBundle={trailingCitationContext.bundle}
              displayOrder={trailingCitationContext.displayOrder}
              messageIdByCitationId={
                trailingCitationContext.messageIdByCitationId
              }
              onCitationClick={onCitationClick}
            />

            {showLoadingDots ? (
              <div className="flex items-center py-2.5">
                <LogoShimmer />
              </div>
            ) : null}

            {interruptLabel ? (
              <div className="py-1.5 text-[13px] italic text-ink-muted">
                {interruptLabel}
              </div>
            ) : null}

            {!inFlight &&
            !turn.failedMessage &&
            (assistantText || turn.cancelled || turn.interrupted) ? (
              <MessageActions
                text={actionText}
                onRetry={onRetry ? () => onRetry(turn.id) : undefined}
              />
            ) : null}

            {turn.failedMessage ? (
              <ErrorMessageCard
                message={turn.failedMessage}
                retryCount={retryCount}
                onRetry={onRetry ? () => onRetry(turn.id) : undefined}
                onSwitchModel={
                  onSwitchModel ? () => onSwitchModel(turn.id) : undefined
                }
              />
            ) : null}
          </div>
        </div>
      </div>
    );
  },
  (prev, next) => {
    if (!prev.isLatest && !next.isLatest) {
      return prev.turn === next.turn && prev.retryCount === next.retryCount;
    }
    return false;
  },
);

interface ConversationTurnListProps {
  turns: ConversationTurn[];
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  sending: boolean;
  loading: boolean;
  error: string | null;
  onRetry?: (turnId: string) => void;
  onSwitchModel?: (turnId: string) => void;
  retryCounts?: Record<string, number>;
  lastTurnMinHeight?: number;
  skillsBySlug?: Record<string, { name: string }>;
  onVirtualApiReady?: (
    api: { scrollToTurnTop: (index: number) => void } | null,
  ) => void;
  /** See ``TurnRowProps.renderToolCall``. */
  renderToolCall?: (tool: PrototypeToolCall) => ReactNode | null;
  /** See ``TurnRowProps.isToolCardFoldable``. */
  isToolCardFoldable?: (tool: PrototypeToolCall) => boolean;
  /** See ``TurnRowProps.onRevealFile``. */
  onRevealFile?: (filePath: string) => void;
  /** See ``TurnRowProps.isLocalFileHref``. */
  isLocalFileHref?: (href: string) => boolean;
  /** See ``TurnRowProps.onLocalFileLinkClick``. */
  onLocalFileLinkClick?: (href: string) => void;
  /** Opens a structured citation in the host document preview. */
  onCitationClick?: (input: OpenCitationInput) => void;
  emptyTitle?: string;
  emptySuggestions?: string[];
  onEmptySuggestionClick?: (text: string) => void;
  /** Show the new-chat welcome (mascot + title + suggestions) when there are no
   *  turns. Only true for a genuinely fresh conversation — an existing
   *  conversation whose transcript is still loading has no turns yet either, and
   *  must NOT flash the welcome before its history lands. The error card renders
   *  regardless. */
  showWelcome?: boolean;
  /** Non-null while the message has been sent but the agent runtime has not
   * reported in yet — the window in which the host is creating the session,
   * warming the local kernel or booting a remote sandbox. The latest turn's
   * header then names that instead of claiming to be processing, while the
   * elapsed counter runs on unbroken. The host page clears it when the
   * kernel's ``message.user`` echo lands; the value says WHERE the runtime is
   * coming up (OSS is single-target and always ``"local"``). */
  startingRuntime?: RuntimeStartLocation | null;
}

export function ConversationTurnList({
  turns,
  scrollContainerRef,
  sending,
  loading,
  error,
  onRetry,
  onSwitchModel,
  retryCounts,
  lastTurnMinHeight,
  skillsBySlug,
  onVirtualApiReady,
  renderToolCall,
  isToolCardFoldable,
  onRevealFile,
  isLocalFileHref,
  onLocalFileLinkClick,
  onCitationClick,
  emptyTitle,
  emptySuggestions,
  onEmptySuggestionClick,
  showWelcome,
  startingRuntime,
}: ConversationTurnListProps) {
  const { t } = useI18n();
  const rowVirtualizer = useVirtualizer({
    count: turns.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => 220,
    overscan: 3,
    getItemKey: (index) => turns[index]?.id ?? String(index),
  });

  // Conditional auto-adjust on row resize. See
  // ``VIRTUAL_SCROLL_ADJUSTMENT`` above for the rationale: adjust only
  // when the resizing row is entirely above the viewport so the user
  // doesn't perceive a phantom drift, while still letting visible
  // expand/collapse toggles unfold in place. Upward-pagination scroll
  // anchoring is still handled manually by
  // ``DesktopConversationPage.pendingScrollAnchorRef``. The option
  // exists at runtime as an instance property (not in the
  // ``useVirtualizer`` opts type), so we assign it directly.
  rowVirtualizer.shouldAdjustScrollPositionOnItemSizeChange =
    VIRTUAL_SCROLL_ADJUSTMENT;
  const virtualItems = rowVirtualizer.getVirtualItems();

  // ONE-SHOT re-measure when turns first populate after mount.
  // ``useVirtualizer`` reads ``getScrollElement`` during render, but the ref
  // (owned by the page) only attaches during commit — so on a FRESH mount the
  // virtualizer can initialize with a null/zero-height scroll element and
  // produce an empty ``getVirtualItems()`` → blank conversation until reload
  // (the "空白 / 多次刷新才出来" history). A single re-measure once the element
  // is laid out + turns exist fixes that. CRITICAL: do this exactly ONCE per
  // mount — calling ``measure()`` on every turns change resets the measurement
  // cache mid-stream during a live turn, collapsing rows to the estimate and
  // jumping scroll, which looked like events "展示即消失". The component is
  // remounted on session switch (``key={selectedSessionId}``), so the ref
  // resets per session.
  const measuredOnceRef = useRef(false);
  useLayoutEffect(() => {
    if (
      !measuredOnceRef.current &&
      scrollContainerRef.current &&
      turns.length > 0
    ) {
      measuredOnceRef.current = true;
      rowVirtualizer.measure();
    }
  }, [turns.length, scrollContainerRef, rowVirtualizer]);

  useLayoutEffect(() => {
    if (!onVirtualApiReady) return;
    onVirtualApiReady({
      scrollToTurnTop: (index: number) => {
        if (index < 0 || index >= turns.length) return;
        // Iterative scroll-and-correct loop. A single scrollTop
        // assignment isn't reliable when the target is the last
        // row of a freshly-appended turn:
        //   1. measureElement RO callbacks for the *previous* turn
        //      may still be in-flight (e.g. markdown image/table
        //      late layout, fold/unfold animations, font reflow).
        //      Each fired RO shifts subsequent rows' translateY,
        //      which moves our target *after* we've already set
        //      scrollTop.
        //   2. The new turn's own measureElement may not have
        //      fired yet on frame 0, so totalSize underestimates
        //      and the browser clamps scrollTop below the target.
        //
        // So: every frame, recompute delta = target.top - container.top.
        // If delta is essentially zero, we're done. Otherwise apply
        // it and try again next frame, up to 8 frames (~133ms).
        // This converges as the layout settles.
        let attempt = 0;
        const MAX_ATTEMPTS = 8;
        const tryAlign = () => {
          attempt += 1;
          const container = scrollContainerRef.current;
          if (!container) return;
          const target = container.querySelector(
            `[data-index="${index}"]`,
          ) as HTMLElement | null;
          if (!target) {
            // Row not yet rendered — prime the virtualizer to
            // mount it. Estimated offset may be wrong, but the
            // subsequent iterations will correct.
            rowVirtualizer.scrollToIndex(index, {
              align: "start",
              behavior: "auto",
            });
            if (attempt < MAX_ATTEMPTS) {
              requestAnimationFrame(tryAlign);
            }
            return;
          }
          const containerRect = container.getBoundingClientRect();
          const targetRect = target.getBoundingClientRect();
          const delta = targetRect.top - containerRect.top;
          if (Math.abs(delta) < 1) return;
          container.scrollTop += delta;
          if (attempt < MAX_ATTEMPTS) {
            requestAnimationFrame(tryAlign);
          }
        };
        // Start after one frame so React has flushed the new
        // turn into the DOM at least once.
        requestAnimationFrame(tryAlign);
      },
    });
    return () => onVirtualApiReady(null);
  }, [onVirtualApiReady, rowVirtualizer, turns.length, scrollContainerRef]);

  return (
    <div className="mx-auto max-w-[760px] px-6">
      {turns.length > 0 ? (
        <div
          style={{
            height: `${rowVirtualizer.getTotalSize()}px`,
            position: "relative",
            width: "100%",
          }}
        >
          {virtualItems.map((virtualRow) => {
            const turn = turns[virtualRow.index];
            if (!turn) return null;
            return (
              <div
                key={turn.id}
                ref={rowVirtualizer.measureElement}
                data-index={virtualRow.index}
                // Opaque row background (matches the scroll container's
                // ``bg-surface``, so invisible in the steady state). Rows are
                // absolutely positioned from CACHED heights: when an earlier
                // turn grows via late layout (markdown table / image load /
                // code highlight / live streaming), the rows below keep their
                // stale ``translateY`` for the frame(s) before the
                // ResizeObserver re-measures — so they briefly land INSIDE the
                // grown turn. In DOM order a later row paints on top, so the
                // opaque background makes it cleanly cover the overflow instead
                // of rendering text-on-text; the re-measure then separates them
                // with no content lost. Does NOT touch the measurement path
                // (calling ``measure()`` mid-stream regressed to "展示即消失").
                className="absolute left-0 top-0 w-full bg-surface"
                style={{
                  transform: `translateY(${virtualRow.start}px)`,
                }}
              >
                <div className={virtualRow.index === 0 ? "" : "pt-[26px]"}>
                  <TurnRow
                    turn={turn}
                    isLatest={virtualRow.index === turns.length - 1}
                    sending={sending}
                    skillsBySlug={skillsBySlug}
                    onRetry={onRetry}
                    onSwitchModel={onSwitchModel}
                    retryCount={retryCounts?.[turn.id] ?? 0}
                    renderToolCall={renderToolCall}
                    isToolCardFoldable={isToolCardFoldable}
                    onRevealFile={onRevealFile}
                    isLocalFileHref={isLocalFileHref}
                    onLocalFileLinkClick={onLocalFileLinkClick}
                    onCitationClick={onCitationClick}
                    startingRuntime={startingRuntime}
                  />
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      {/* Tail spacer — ensures the scroll container's ``scrollHeight``
          is large enough to put the last turn's top at the viewport
          top via ``scrollTop`` adjustment. Replaces the previous
          per-row ``minHeight`` approach which forced the latest turn
          row itself to be at least ``containerHeight`` tall — that
          left a fixed empty band INSIDE the row that didn't shrink
          predictably as the row grew (depending on virtualizer
          measurement timing) and visually drifted during streaming.
          With an external spacer, the row's measured size always
          reflects pure content; the spacer absorbs the slack and
          shrinks to zero once content exceeds ``containerHeight``. */}
      {(() => {
        if (turns.length < 2 || !lastTurnMinHeight) return null;
        const lastVirtualItem = virtualItems[virtualItems.length - 1];
        const lastSize = lastVirtualItem?.size ?? 0;
        // Subtract the inter-turn pt-26 gap because it's already
        // included in the last row's measured size (the pt-26
        // wrapper sits inside ``data-index`` which measureElement
        // observes).
        const spacerHeight = Math.max(0, lastTurnMinHeight - lastSize);
        if (spacerHeight === 0) return null;
        return <div style={{ height: spacerHeight }} aria-hidden />;
      })()}

      {!turns.length && !loading ? (
        <div className="pt-[120px]">
          {error ? (
            <div className="mx-auto mb-5 max-w-[520px]">
              <ErrorMessageCard message={error} />
            </div>
          ) : showWelcome ? (
            <>
              {/* Friendly mascot above the title — the same illustration that
                  used to sit at the bottom of the sidebar, moved here so the
                  empty new-chat page feels less bare. Gated on ``showWelcome``
                  so an existing conversation still fetching its transcript (no
                  turns yet) doesn't flash this new-chat state mid-load. */}
              <img
                src={assetUrl("mascot.png")}
                alt=""
                aria-hidden="true"
                className="pointer-events-none mx-auto mb-6 h-[160px] w-auto select-none opacity-80"
              />
              <div className="text-center text-2xl font-medium leading-tight text-ink-heading">
                {emptyTitle ?? t("conversation.startHere")}
              </div>
              {emptySuggestions && emptySuggestions.length > 0 ? (
                <div className="mx-auto mt-5 max-w-[750px]">
                  <SuggestionList
                    suggestions={emptySuggestions}
                    onClick={onEmptySuggestionClick}
                  />
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}

      {sending && turns.length === 0 ? (
        <div className="mt-[26px] flex items-start gap-3">
          <div className="flex items-center py-2.5">
            <LogoShimmer />
          </div>
        </div>
      ) : null}

      {/* Transcript still loading with nothing on screen yet: show the
          shimmer instead of literally nothing. Every history load used to
          render an empty body for its whole duration (the welcome state is
          gated to new chats), which read as a blank white page whenever the
          bootstrap fetch chain was slow. Mutually exclusive with the
          ``sending`` shimmer above. */}
      {!sending && loading && turns.length === 0 ? (
        <div className="mt-[26px] flex items-start gap-3">
          <div className="flex items-center py-2.5">
            <LogoShimmer />
          </div>
        </div>
      ) : null}
    </div>
  );
}

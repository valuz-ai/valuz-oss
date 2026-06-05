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
  RotateCw,
  Sparkles,
  Terminal,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { MarkdownContent } from "./MarkdownContent";
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
import type { ConversationTurn, PrototypeToolCall } from "@valuz/shared";
import {
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
  | { kind: "turn-diff-summary"; summary: TurnDiffSummary };

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
    <div className="font-sans text-[12.5px] leading-[1.7] text-[#6e7481]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-1.5 py-1 text-left text-[12px] font-normal text-[#6e7481] transition-colors hover:text-[#525860]"
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
                className="whitespace-pre-wrap text-[#6e7481]"
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
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = skillTokenRe.exec(text)) !== null) {
    const [whole, leading, slug] = match;
    const tokenStart = match.index + leading.length;
    if (tokenStart > lastIndex) {
      parts.push(text.slice(lastIndex, tokenStart));
    }
    const name = skillsBySlug?.[slug]?.name ?? slug;
    parts.push(
      <span
        key={`s-${key++}`}
        className="mr-0.5 inline-flex items-center gap-1 rounded-full border border-brand/20 bg-brand-light px-2 py-0.5 text-2xs text-brand align-middle select-none"
      >
        <Zap className="h-3 w-3" />
        {name}
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
  /** Reveal a file in the host OS (Finder on macOS, Explorer on
   * Windows). Wired by the desktop app to the ``open_in_finder`` IPC;
   * webui omits this and the per-row external-link icon hides. */
  onRevealFile?: (filePath: string) => void;
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
    onRevealFile,
  }: TurnRowProps) {
    const inFlight = sending && isLatest;
    const lastBlock = turn.blocks[turn.blocks.length - 1];
    const showStreamingCaret = inFlight && lastBlock?.kind === "assistant";
    const showLoadingDots = inFlight && !turn.failedMessage;
    const displayBlocks = buildDisplayBlocks(turn, renderToolCall);

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
        if (b.kind !== "segment") return i + 1;
        if (b.header === null) return i + 1;
        if (b.items.some((item) => item.kind === "tool")) return i + 1;
      }
      return 0;
    }, [displayBlocks]);

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

    // While streaming, tick a 1Hz wall-clock interval so "已处理 X 秒"
    // advances every second even between SSE event arrivals (otherwise
    // the displayed elapsed only updates when a new tool/thinking block
    // lands, which feels stuck during e.g. a long Bash run). Computes
    // ``Date.now() - turn.userTimestamp`` so the displayed value tracks
    // real time, not the latest block's stamp. Once the turn settles we
    // freeze on ``totalElapsedMs`` (the canonical max from blocks).
    const [tick, setTick] = useState(0);
    useEffect(() => {
      if (!inFlight) return;
      const interval = window.setInterval(() => {
        setTick((t) => t + 1);
      }, 1000);
      return () => window.clearInterval(interval);
    }, [inFlight]);
    const displayedElapsedMs = useMemo(() => {
      // ``tick`` only matters when streaming — referenced so React
      // re-evaluates this memo each second while inFlight.
      void tick;
      if (!inFlight) return totalElapsedMs;
      if (!turn.userTimestamp) return totalElapsedMs;
      const startMs = new Date(turn.userTimestamp).getTime();
      if (Number.isNaN(startMs)) return totalElapsedMs;
      const live = Date.now() - startMs;
      // Guard against clock skew: never go backwards from the
      // canonical block-derived elapsed. If for some reason ``Date.now``
      // < block stamp (rare wall-clock skew), keep the higher number.
      return Math.max(live, totalElapsedMs);
    }, [tick, inFlight, turn.userTimestamp, totalElapsedMs]);
    return (
      <div data-conversation-turn className="space-y-[26px]">
        {turn.userText || (turn.attachments && turn.attachments.length > 0) ? (
          <div className="group flex flex-col items-end gap-1">
            {turn.userText ? (
              <div className="max-w-[78%]">
                <div className="whitespace-pre-wrap rounded-xl bg-surface-soft px-3.5 py-3 text-[13.5px] leading-[1.6] text-ink-heading">
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
            <div className="font-sans text-[13px] leading-[1.6] text-[#6e7481]">
              {headerFoldable ? (
                <button
                  type="button"
                  onClick={() => setTurnFolded((value) => !value)}
                  className="inline-flex items-center py-1 text-left text-[13px] font-normal text-[#6e7481] transition-colors hover:text-[#525860]"
                  aria-expanded={!turnFolded}
                >
                  <span>{formatTurnElapsed(displayedElapsedMs)}</span>
                  <ChevronRight
                    className={`ml-1 h-3.5 w-3.5 shrink-0 transition-transform ${
                      !turnFolded ? "rotate-90" : ""
                    }`}
                    aria-hidden="true"
                  />
                </button>
              ) : (
                <div className="inline-flex items-center py-1 text-[13px] font-normal text-[#6e7481]">
                  <span>{formatTurnElapsed(displayedElapsedMs)}</span>
                </div>
              )}
            </div>

            {displayBlocks.map((block, blockIndex) => {
              const isLastBlock = blockIndex === displayBlocks.length - 1;
              // When the turn-level header is folded, hide every block
              // before ``trailingContentStart`` — that's the process
              // work (tool calls + their narration). Blocks at or after
              // it are "answer content": one or more trailing assistant
              // messages, possibly with a small thinking block between
              // them. The whole tail stays visible so a long report +
              // closing remark both survive the fold.
              if (turnFolded && blockIndex < trailingContentStart) {
                return null;
              }
              if (block.kind === "tool-overridden") {
                // Caller-supplied node (e.g. SkillSubmissionCard for the
                // ``submit_skill`` tool) — stays visible at the top level
                // instead of being folded into a segment body.
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
              return (
                <div
                  key={`segment-${turn.id}-${blockIndex}`}
                  className="space-y-3"
                >
                  {block.header !== null ? (
                    <MarkdownContent
                      content={block.header}
                      isAnimating={animateHeader}
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

            {showLoadingDots ? (
              <div className="flex items-center py-2.5">
                <LogoShimmer />
              </div>
            ) : null}

            {!inFlight &&
            !turn.failedMessage &&
            turn.blocks.some((b) => b.kind === "assistant") ? (
              <MessageActions
                text={turn.blocks
                  .filter((b) => b.kind === "assistant")
                  .map((b) => (b as { text: string }).text)
                  .join("\n\n")}
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
  /** See ``TurnRowProps.onRevealFile``. */
  onRevealFile?: (filePath: string) => void;
  emptyTitle?: string;
  emptySuggestions?: string[];
  onEmptySuggestionClick?: (text: string) => void;
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
  onRevealFile,
  emptyTitle,
  emptySuggestions,
  onEmptySuggestionClick,
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
                className="absolute left-0 top-0 w-full"
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
                    onRevealFile={onRevealFile}
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
          ) : null}
          {/* Friendly mascot above the title — the same illustration that
              used to sit at the bottom of the sidebar, moved here so the
              empty new-chat page feels less bare. */}
          <img
            src="./mascot.png"
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
        </div>
      ) : null}

      {sending && turns.length === 0 ? (
        <div className="mt-[26px] flex items-start gap-3">
          <div className="flex items-center py-2.5">
            <LogoShimmer />
          </div>
        </div>
      ) : null}
    </div>
  );
}

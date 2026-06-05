/**
 * Scrolling log viewport with KV-aware rendering.
 *
 * Renders ``LogLine[]`` straight (no virtualization yet — the parent
 * caps the buffer at LOG_BUFFER_SIZE so we're always under a few
 * thousand DOM rows; cheap on modern Electron).
 *
 * Behaviours borrowed from multica's ``daemon-panel.tsx``:
 *
 *   - Pinned auto-scroll to bottom while ``followTail`` is on; user
 *     scrolling away releases the pin and surfaces a "Jump to latest"
 *     button at the bottom-right of the viewport.
 *   - Consecutive lines with the same ``msg`` collapse into a single
 *     row showing ``+N more``; click to expand. Saves a ton of room
 *     on heartbeat / poll-loop noise.
 *   - Each row shows a compact inline KV preview (key=value pairs);
 *     clicking the row expands the structured fields into a grid.
 *   - Search hits get yellow-mark highlights inside the message text.
 *
 * Pure presentation — filter / search state lives upstream.
 */

import {
  forwardRef,
  Fragment,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ArrowDownToLine } from "lucide-react";
import type { LogLevel, LogLine } from "@valuz/shared";
import { Button } from "../ui/button";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

const LEVEL_TEXT: Record<LogLevel, string> = {
  DEBUG: "text-ink-meta",
  INFO: "text-emerald-700",
  WARNING: "text-amber-700",
  ERROR: "text-red-700",
  CRITICAL: "text-red-800 font-semibold",
  RAW: "text-ink-meta",
};

const LEVEL_BADGE_BG: Record<LogLevel, string> = {
  DEBUG: "bg-ink-meta/10",
  INFO: "bg-emerald-100",
  WARNING: "bg-amber-100",
  ERROR: "bg-red-100",
  CRITICAL: "bg-red-200",
  RAW: "bg-surface-muted",
};

const LEVEL_LABEL: Record<LogLevel, string> = {
  DEBUG: "DBG",
  INFO: "INF",
  WARNING: "WRN",
  ERROR: "ERR",
  CRITICAL: "FTL",
  RAW: "RAW",
};

// ── Helpers ─────────────────────────────────────────────────────────

const formatTime = (iso: string): string => {
  // The backend emits UTC; show local time HH:MM:SS.mmm so it matches
  // what the user sees in their other terminals / log files.
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso.slice(11, 23);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    const ss = String(d.getSeconds()).padStart(2, "0");
    const ms = String(d.getMilliseconds()).padStart(3, "0");
    return `${hh}:${mm}:${ss}.${ms}`;
  } catch {
    return iso.slice(11, 23);
  }
};

/** Trim ``foo.bar.baz`` to ``baz`` for compact display. */
const shortLogger = (name: string): string => {
  if (!name) return "";
  const parts = name.split(".");
  return parts[parts.length - 1] ?? name;
};

const stringifyValue = (v: unknown): string => {
  if (v === null) return "null";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
};

interface HighlightProps {
  text: string;
  query: string;
  className?: string;
}

const Highlight = ({ text, query, className }: HighlightProps) => {
  if (!query) return <span className={className}>{text}</span>;
  const lower = text.toLowerCase();
  const needle = query.toLowerCase();
  const segments: { text: string; highlight: boolean }[] = [];
  let cursor = 0;
  while (cursor < text.length) {
    const hit = lower.indexOf(needle, cursor);
    if (hit === -1) {
      segments.push({ text: text.slice(cursor), highlight: false });
      break;
    }
    if (hit > cursor) {
      segments.push({ text: text.slice(cursor, hit), highlight: false });
    }
    segments.push({
      text: text.slice(hit, hit + needle.length),
      highlight: true,
    });
    cursor = hit + needle.length;
  }
  return (
    <span className={className}>
      {segments.map((seg, i) =>
        seg.highlight ? (
          <mark key={i} className="rounded bg-amber-200 text-ink-heading">
            {seg.text}
          </mark>
        ) : (
          <Fragment key={i}>{seg.text}</Fragment>
        ),
      )}
    </span>
  );
};

// ── Group-collapse: condense consecutive identical msg rows ─────────

interface RenderGroup {
  /** First line of the run — its level/logger/ts/fields are the
   *  representative shown by default. */
  head: LogLine;
  /** Followers (run length = 1 + followers.length). Empty when no
   *  collapse happened. */
  tail: LogLine[];
}

const groupRepeats = (lines: LogLine[], collapse: boolean): RenderGroup[] => {
  if (!collapse) {
    return lines.map((line) => ({ head: line, tail: [] }));
  }
  const groups: RenderGroup[] = [];
  for (const line of lines) {
    const last = groups[groups.length - 1];
    if (
      last &&
      last.head.msg === line.msg &&
      last.head.level === line.level &&
      last.head.logger === line.logger
    ) {
      last.tail.push(line);
    } else {
      groups.push({ head: line, tail: [] });
    }
  }
  return groups;
};

// ── Imperative handle: parent calls scrollToBottom() ────────────────

export interface SystemLogListHandle {
  scrollToBottom: () => void;
}

// ── Main component ──────────────────────────────────────────────────

export interface SystemLogListProps {
  lines: LogLine[];
  searchQuery: string;
  collapseRepeats: boolean;
  followTail: boolean;
  onUserScrolledAway: () => void;
}

export const SystemLogList = forwardRef<
  SystemLogListHandle,
  SystemLogListProps
>(
  (
    { lines, searchQuery, collapseRepeats, followTail, onUserScrolledAway },
    ref,
  ) => {
    const { t } = useI18n();
    const scrollerRef = useRef<HTMLDivElement>(null);
    const [expandedGroups, setExpandedGroups] = useState<Set<number>>(
      () => new Set(),
    );
    const [expandedRows, setExpandedRows] = useState<Set<number>>(
      () => new Set(),
    );
    const [isPinnedToBottom, setIsPinnedToBottom] = useState(true);

    const groups = useMemo(
      () => groupRepeats(lines, collapseRepeats),
      [lines, collapseRepeats],
    );

    const scrollToBottom = useCallback(() => {
      const el = scrollerRef.current;
      if (el) {
        el.scrollTop = el.scrollHeight;
        setIsPinnedToBottom(true);
      }
    }, []);

    useImperativeHandle(ref, () => ({ scrollToBottom }), [scrollToBottom]);

    // Auto-scroll: when followTail is on and we're already pinned,
    // jump on every render that adds rows.
    useLayoutEffect(() => {
      if (!followTail) return;
      if (isPinnedToBottom) {
        const el = scrollerRef.current;
        if (el) el.scrollTop = el.scrollHeight;
      }
    }, [groups.length, followTail, isPinnedToBottom]);

    // Detect manual scroll-up — release the pin so new lines don't
    // yank the user back down. ``onUserScrolledAway`` lets the parent
    // surface the "jump to latest" affordance.
    useEffect(() => {
      const el = scrollerRef.current;
      if (!el) return;
      const handler = () => {
        const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
        const atBottom = distance < 24; // forgiveness for sub-pixel
        setIsPinnedToBottom(atBottom);
        if (!atBottom) onUserScrolledAway();
      };
      el.addEventListener("scroll", handler, { passive: true });
      return () => el.removeEventListener("scroll", handler);
    }, [onUserScrolledAway]);

    const toggleGroup = (idx: number) => {
      setExpandedGroups((prev) => {
        const next = new Set(prev);
        if (next.has(idx)) next.delete(idx);
        else next.add(idx);
        return next;
      });
    };

    const toggleRow = (id: number) => {
      setExpandedRows((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
    };

    return (
      <div className="relative flex-1 min-h-0">
        <div
          ref={scrollerRef}
          className="absolute inset-0 overflow-auto bg-surface-base font-mono text-xs leading-snug"
        >
          {groups.length === 0 ? (
            <div className="flex h-full items-center justify-center text-ink-meta">
              {t("system.noMatchLogs")}
            </div>
          ) : (
            <ul className="divide-y divide-surface-border/40">
              {groups.map((group, groupIdx) => {
                const isExpandedGroup = expandedGroups.has(groupIdx);
                const repeats = group.tail.length;
                return (
                  <Fragment key={group.head.id}>
                    <LogRow
                      line={group.head}
                      searchQuery={searchQuery}
                      expanded={expandedRows.has(group.head.id)}
                      onToggle={() => toggleRow(group.head.id)}
                      repeatCount={repeats}
                      onToggleGroup={() => toggleGroup(groupIdx)}
                      groupExpanded={isExpandedGroup}
                    />
                    {isExpandedGroup &&
                      group.tail.map((follower) => (
                        <LogRow
                          key={follower.id}
                          line={follower}
                          searchQuery={searchQuery}
                          expanded={expandedRows.has(follower.id)}
                          onToggle={() => toggleRow(follower.id)}
                          indentRepeat
                        />
                      ))}
                  </Fragment>
                );
              })}
            </ul>
          )}
        </div>

        {!isPinnedToBottom && (
          <Button
            size="sm"
            onClick={scrollToBottom}
            className="absolute bottom-3 right-3 shadow-md"
          >
            <ArrowDownToLine className="mr-1 h-3.5 w-3.5" />
            {t("system.jumpToLatest")}
          </Button>
        )}
      </div>
    );
  },
);

SystemLogList.displayName = "SystemLogList";

// ── Single row ──────────────────────────────────────────────────────

interface LogRowProps {
  line: LogLine;
  searchQuery: string;
  expanded: boolean;
  onToggle: () => void;
  /** Set when this row is part of an expanded repeat group's tail. */
  indentRepeat?: boolean;
  /** Set on a group head — drives the ``+N more`` chip. */
  repeatCount?: number;
  onToggleGroup?: () => void;
  groupExpanded?: boolean;
}

const LogRow = ({
  line,
  searchQuery,
  expanded,
  onToggle,
  indentRepeat = false,
  repeatCount = 0,
  onToggleGroup,
  groupExpanded,
}: LogRowProps) => {
  const { t } = useI18n();
  const fieldEntries = Object.entries(line.fields);
  const hasFields = fieldEntries.length > 0;
  // Compact KV preview: first 3 fields inline as ``k=v``; rest hidden
  // behind "+N" until expanded.
  const previewFields = fieldEntries.slice(0, 3);
  const hiddenFieldCount = fieldEntries.length - previewFields.length;

  return (
    <li
      className={cn(
        "group cursor-default px-3 py-1 hover:bg-surface-muted/40",
        indentRepeat && "pl-12",
      )}
    >
      <div
        className={cn(
          "flex items-baseline gap-2 leading-snug",
          hasFields && "cursor-pointer",
        )}
        onClick={hasFields ? onToggle : undefined}
      >
        <span className="shrink-0 text-ink-meta tabular-nums">
          {formatTime(line.ts)}
        </span>
        <span
          className={cn(
            "shrink-0 rounded px-1 text-2xs",
            LEVEL_BADGE_BG[line.level],
            LEVEL_TEXT[line.level],
          )}
        >
          {LEVEL_LABEL[line.level]}
        </span>
        {line.logger && (
          <span className="shrink-0 text-ink-meta" title={line.logger}>
            {shortLogger(line.logger)}
          </span>
        )}
        <span className="min-w-0 flex-1 break-words text-ink-heading">
          <Highlight
            text={line.msg || line.raw}
            query={searchQuery}
            className="whitespace-pre-wrap"
          />
          {previewFields.length > 0 && (
            <span className="ml-2 text-ink-meta">
              {previewFields.map(([k, v]) => (
                <span key={k} className="mr-2">
                  <span className="opacity-60">{k}=</span>
                  <span>{stringifyValue(v)}</span>
                </span>
              ))}
              {hiddenFieldCount > 0 && (
                <span className="opacity-60">+{hiddenFieldCount}</span>
              )}
            </span>
          )}
        </span>

        {repeatCount > 0 && onToggleGroup && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onToggleGroup();
            }}
            className="shrink-0 rounded bg-surface-muted px-1.5 py-0.5 text-2xs text-ink-meta hover:bg-surface-border"
            title={
              groupExpanded
                ? t("system.collapseRepeats")
                : t("system.expandRepeats", { count: String(repeatCount) })
            }
          >
            {groupExpanded
              ? t("system.collapseShort")
              : t("system.expandShort", { count: String(repeatCount) })}
          </button>
        )}
      </div>

      {expanded && hasFields && (
        <div className="mt-1.5 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 rounded border border-surface-border bg-surface-soft/50 p-2 text-2xs">
          {fieldEntries.map(([k, v]) => (
            <Fragment key={k}>
              <span className="text-ink-meta">{k}</span>
              <span className="break-all text-ink-heading">
                {stringifyValue(v)}
              </span>
            </Fragment>
          ))}
        </div>
      )}
    </li>
  );
};

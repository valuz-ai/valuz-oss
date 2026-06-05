/**
 * Filter / action bar above the log viewport.
 *
 * Search box (substring, case-insensitive) + level chips + utility
 * buttons (clear, follow tail, copy filtered, collapse repeats).
 *
 * Pure presentation: every state lives in the parent (the page passes
 * the ``useSystemStore`` view slice down). The toolbar only emits
 * change callbacks.
 */

import {
  Copy,
  Trash2,
  ArrowDownToLine,
  Layers,
  Search as SearchIcon,
} from "lucide-react";
import type { LogLevel } from "@valuz/shared";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Toggle } from "../ui/toggle";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

const LEVEL_ORDER: LogLevel[] = [
  "DEBUG",
  "INFO",
  "WARNING",
  "ERROR",
  "CRITICAL",
  "RAW",
];

const LEVEL_LABELS: Record<LogLevel, string> = {
  DEBUG: "DEBUG",
  INFO: "INFO",
  WARNING: "WARN",
  ERROR: "ERROR",
  CRITICAL: "FATAL",
  RAW: "RAW",
};

const LEVEL_TEXT_COLOR: Record<LogLevel, string> = {
  DEBUG: "text-ink-meta",
  INFO: "text-emerald-600",
  WARNING: "text-amber-600",
  ERROR: "text-red-600",
  CRITICAL: "text-red-700",
  RAW: "text-ink-meta",
};

export interface SystemLogToolbarProps {
  searchQuery: string;
  onSearchChange: (q: string) => void;
  enabledLevels: Set<LogLevel>;
  onToggleLevel: (level: LogLevel) => void;
  followTail: boolean;
  onToggleFollowTail: (v: boolean) => void;
  collapseRepeats: boolean;
  onToggleCollapseRepeats: (v: boolean) => void;
  /** Counts per level (post-search-filter, pre-level-filter) — drives
   *  the chip's count badge. */
  levelCounts: Record<LogLevel, number>;
  totalShown: number;
  totalBuffered: number;
  bufferFull: boolean;
  onClear: () => void;
  onCopy: () => void;
}

export const SystemLogToolbar = ({
  searchQuery,
  onSearchChange,
  enabledLevels,
  onToggleLevel,
  followTail,
  onToggleFollowTail,
  collapseRepeats,
  onToggleCollapseRepeats,
  levelCounts,
  totalShown,
  totalBuffered,
  bufferFull,
  onClear,
  onCopy,
}: SystemLogToolbarProps) => {
  const { t } = useI18n();
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-surface-border bg-surface-soft px-3 py-2">
      <div className="relative flex-1 min-w-[200px] max-w-md">
        <SearchIcon className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-meta" />
        <Input
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={t("system.searchPlaceholder")}
          className="h-8 pl-7 text-sm"
        />
      </div>

      <div className="flex items-center gap-1">
        {LEVEL_ORDER.map((level) => {
          const enabled = enabledLevels.has(level);
          const count = levelCounts[level] ?? 0;
          return (
            <button
              key={level}
              type="button"
              onClick={() => onToggleLevel(level)}
              className={cn(
                "rounded px-1.5 py-0.5 font-mono text-2xs tabular-nums transition-colors",
                enabled
                  ? cn(
                      "bg-surface-base ring-1 ring-surface-border",
                      LEVEL_TEXT_COLOR[level],
                    )
                  : "text-ink-meta opacity-40 hover:opacity-70",
              )}
              title={
                enabled
                  ? t("system.hideLevel", { level })
                  : t("system.showLevel", { level })
              }
            >
              {LEVEL_LABELS[level]}
              {count > 0 && <span className="ml-1 opacity-60">{count}</span>}
            </button>
          );
        })}
      </div>

      <div className="ml-auto flex items-center gap-1">
        <span className="text-2xs tabular-nums text-ink-meta">
          {totalShown}/{totalBuffered}
          {bufferFull && t("system.bufferFull")}
        </span>
        <Toggle
          size="sm"
          pressed={collapseRepeats}
          onPressedChange={onToggleCollapseRepeats}
          title={
            collapseRepeats
              ? t("system.expandRepeats", { count: "0" })
              : t("system.collapseRepeats")
          }
          aria-label={t("system.collapseRepeats")}
        >
          <Layers className="h-3.5 w-3.5" />
        </Toggle>
        <Toggle
          size="sm"
          pressed={followTail}
          onPressedChange={onToggleFollowTail}
          title={
            followTail ? t("system.stopAutoScroll") : t("system.autoScroll")
          }
          aria-label={t("system.autoFollow")}
        >
          <ArrowDownToLine className="h-3.5 w-3.5" />
        </Toggle>
        <Button
          size="sm"
          variant="ghost"
          onClick={onCopy}
          title={t("system.copyFiltered")}
        >
          <Copy className="h-3.5 w-3.5" />
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onClear}
          title={t("system.clearDisplay")}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
};

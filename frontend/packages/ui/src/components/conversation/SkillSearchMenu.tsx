import { useState, useEffect, useRef, useCallback } from "react";
import { Search, Zap } from "lucide-react";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";
import { filterSkillItems, type SkillSearchItem } from "./skill-search-filter";

// Re-exported for back-compat: existing consumers import the type (and the
// barrel re-exports it) from this module. The value/logic lives in
// ``skill-search-filter`` so this component file only exports a component.
export type { SkillSearchItem } from "./skill-search-filter";

export interface SkillSearchMenuProps {
  skills: SkillSearchItem[];
  query: string;
  onSelect: (skill: SkillSearchItem) => void;
  onClose: () => void;
  /** Position relative to anchor (CSS top/left) */
  position?: { top: number; left: number };
  /**
   * Which way the menu opens relative to the anchor when ``position`` is not
   * given: ``"up"`` (above the input — the default, for a bottom-anchored
   * composer) or ``"down"`` (below it, for a top-anchored composer like the
   * project detail page, where opening up would overflow the page header).
   */
  direction?: "up" | "down";
}

export const SkillSearchMenu = ({
  skills,
  query,
  onSelect,
  onClose,
  position,
  direction = "up",
}: SkillSearchMenuProps) => {
  const { t } = useI18n();
  const [highlightState, setHighlightState] = useState({
    index: 0,
    query,
  });
  const menuRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = filterSkillItems(skills, query);

  const highlightIndex =
    highlightState.query === query ? highlightState.index : 0;

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (filtered.length === 0) {
        if (e.key === "Escape") {
          e.preventDefault();
          onClose();
        }
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setHighlightState({
          index: (highlightIndex + 1) % filtered.length,
          query,
        });
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlightState({
          index: (highlightIndex - 1 + filtered.length) % filtered.length,
          query,
        });
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (filtered[highlightIndex]) onSelect(filtered[highlightIndex]);
      } else if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    },
    [filtered, highlightIndex, onSelect, onClose, query],
  );

  useEffect(() => {
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current?.contains(e.target as Node)) return;
      onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  useEffect(() => {
    const el = listRef.current?.children[highlightIndex] as
      | HTMLElement
      | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [highlightIndex]);

  // Zero matches is no longer a dead-end. A ``/xxx`` that matches no skill is
  // treated as a pass-through slash command: the composer stops rendering this
  // menu (see ``skillMenuOpen``) and lets Enter send the command normally,
  // instead of showing a discouraging "no matching skill". Render nothing if we
  // ever get here with an empty list so the component stays self-consistent.
  if (filtered.length === 0) return null;

  return (
    <div
      ref={menuRef}
      className="absolute z-50 min-w-[280px] max-w-[340px] rounded-lg border border-surface-border bg-surface p-1 shadow-lg"
      style={
        position
          ? { top: position.top, left: position.left }
          : direction === "down"
            ? { top: "calc(100% + 4px)", left: 0 }
            : { bottom: "calc(100% + 4px)", left: 0 }
      }
    >
      <div className="flex items-center gap-2 border-b border-surface-border px-2 py-1.5">
        <Search className="h-3.5 w-3.5 text-ink-meta" />
        <span className="text-base text-ink-meta">
          {t("conversation.searchSkill")}
        </span>
      </div>
      <div ref={listRef} className="max-h-[260px] overflow-y-auto py-0.5">
        {filtered.map((skill, idx) => (
          <button
            key={skill.id}
            type="button"
            className={cn(
              "flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left transition-colors",
              idx === highlightIndex ? "bg-surface-muted" : "hover:bg-surface-muted",
            )}
            onClick={() => onSelect(skill)}
            onMouseEnter={() => setHighlightState({ index: idx, query })}
          >
            <Zap className="h-3.5 w-3.5 shrink-0 text-ink-heading" />
            <div className="min-w-0">
              <div className="truncate text-base text-ink-heading">
                {skill.name}
              </div>
              {skill.description && (
                <div className="truncate text-2xs text-ink-meta">
                  {skill.description}
                </div>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
};

import { Loader2, X } from "lucide-react";
import type { MouseEvent } from "react";
import { useEffect, useRef } from "react";

import { useI18n } from "../../hooks/use-i18n";
import { cn } from "../../lib/utils";
import { ArtifactIcon } from "./ArtifactViewerShell";
import type { ArtifactPreviewKind } from "./artifact-viewer.types";

/** Softens the last few pixels of an over-long file name. */
const FADE_MASK =
  "linear-gradient(to right, #000 calc(100% - 1.5rem), transparent 100%)";

export interface ArtifactTabItem {
  /** Stable identity — the document's path. */
  path: string;
  /** Label. Callers pass the file name, not the whole path. */
  name: string;
  /** Drives the leading icon; omit while the descriptor is still resolving. */
  previewKind?: ArtifactPreviewKind | null;
  loading?: boolean;
  error?: boolean;
}

export interface ArtifactTabBarProps {
  tabs: ArtifactTabItem[];
  activePath: string | null;
  onActivate: (path: string) => void;
  onClose: (path: string) => void;
  className?: string;
}

/**
 * Editor-style strip for the open-documents set.
 *
 * The active tab reads as a raised card continuous with the content below it
 * (no bottom border), inactive ones sit flat behind a hairline. Close buttons
 * only materialise on the active tab and on hover, so a crowded strip stays
 * readable instead of turning into a row of ✕.
 */
export function ArtifactTabBar({
  tabs,
  activePath,
  onActivate,
  onClose,
  className,
}: ArtifactTabBarProps) {
  const { t } = useI18n();
  const stripRef = useRef<HTMLDivElement | null>(null);
  const activeRef = useRef<HTMLButtonElement | null>(null);

  // Opening a document from the file tree can land a tab outside the visible
  // strip; pull it back into view so the user sees what they just opened.
  useEffect(() => {
    if (!activePath) return;
    activeRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activePath]);

  if (tabs.length === 0) return null;

  const handleAuxClick = (event: MouseEvent<HTMLElement>, path: string) => {
    // Middle-click closes, the way every editor does it.
    if (event.button !== 1) return;
    event.preventDefault();
    onClose(path);
  };

  return (
    <div
      ref={stripRef}
      role="tablist"
      aria-label={t("ui.artifact.openDocuments")}
      className={cn(
        // Inverted scheme: strip and body share the same white, and the only
        // structure is the filled chip plus the hairlines between neighbours.
        "flex shrink-0 items-center gap-2 overflow-x-auto bg-surface px-1 pt-2 pb-1",
        // The strip is chrome, not content — a visible scrollbar here reads as
        // clutter, so scrolling stays available but unpainted.
        "[-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
        className,
      )}
    >
      {tabs.map((tab, index) => {
        const active = tab.path === activePath;
        // A hairline belongs between two inactive neighbours only. Drawing one
        // beside the filled chip would box it in on that side.
        const divider =
          !active && tabs[index + 1] && tabs[index + 1].path !== activePath;
        return (
          <button
            key={tab.path}
            ref={active ? activeRef : undefined}
            type="button"
            role="tab"
            aria-selected={active}
            title={tab.path}
            onClick={() => onActivate(tab.path)}
            onAuxClick={(event) => handleAuxClick(event, tab.path)}
            className={cn(
              // Width is a ceiling, not a fixed size: tabs give ground evenly as the
              // pane narrows and stop at min-w-20, after which the strip scrolls
              // rather than grinding the labels down to nothing.
              "group relative flex h-7 w-36 min-w-20 shrink items-center gap-1.5 rounded-lg px-2.5 text-2xs transition-colors",
              // Hover and selection are deliberately the same chip — same fill,
              // same box, no weight change. Only one tab is ever under the
              // pointer, so the gap between pills is what keeps them legible
              // rather than a second visual language.
              active
                ? "bg-surface-muted text-ink-heading"
                : "text-ink-body hover:bg-surface-muted hover:text-ink-heading",
              divider &&
                "after:absolute after:inset-y-1.5 after:-right-1 after:w-px after:bg-surface-border",
            )}
          >
            <span className="flex h-3 w-3 shrink-0 items-center justify-center [&_svg]:h-3 [&_svg]:w-3">
              {tab.loading ? (
                <Loader2 className="h-3 w-3 animate-spin text-ink-meta" />
              ) : (
                <ArtifactIcon kind={tab.previewKind ?? "unsupported"} />
              )}
            </span>
            <span
              className={cn(
                "min-w-0 flex-1 overflow-hidden whitespace-nowrap text-left",
                tab.error && "text-error-text",
              )}
              // A mask, not an ellipsis: it fades whatever is underneath, so it
              // works on the transparent, hovered and selected pill alike
              // without having to know which background it sits on. Short names
              // end before the fade zone and are untouched.
              style={{
                maskImage: FADE_MASK,
                WebkitMaskImage: FADE_MASK,
              }}
            >
              {tab.name}
            </span>
            {/* Out of flow on purpose. Reserving a slot for it would cost every
                tab ~22px of label — brutal at the narrow end — and revealing it
                on hover would shove the text sideways. Sitting over the fade
                zone, it costs nothing and never jitters. */}
            <span
              role="button"
              tabIndex={-1}
              aria-label={t("ui.artifact.closeTab")}
              title={t("ui.artifact.closeTab")}
              onClick={(event) => {
                event.stopPropagation();
                onClose(tab.path);
              }}
              className={cn(
                "absolute top-1/2 right-1.5 flex h-4 w-4 -translate-y-1/2 items-center justify-center rounded-sm text-ink-meta transition",
                // Same fill as the pill it floats on, so it punches a clean
                // hole in the label rather than tangling with the glyphs it
                // overlaps. It is only ever visible on a filled pill.
                "bg-surface-muted hover:bg-surface hover:text-ink-heading",
                active ? "opacity-100" : "opacity-0 group-hover:opacity-100",
              )}
            >
              <X className="h-3 w-3" />
            </span>
          </button>
        );
      })}
    </div>
  );
}

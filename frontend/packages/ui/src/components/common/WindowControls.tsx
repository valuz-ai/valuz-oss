import type { CSSProperties } from "react";
import { Minus, Square, X } from "lucide-react";

/**
 * Windows "restore down" glyph (Segoe Fluent ``ChromeRestore``): a square in
 * the lower-left with the top and right edges of a second square showing
 * behind it.  lucide ships no equivalent — its ``Copy`` is the same
 * construction mirrored (front square lower-*right*), which reads as the
 * wrong window, and ``Maximize2``'s outward arrows say "enlarge" on a window
 * that is already maximized.  Drawn to lucide's own conventions (24×24 box,
 * ``currentColor``, stroke width inherited) so it sits with ``Minus`` /
 * ``Square`` / ``X`` in the same control strip.
 */
const RestoreIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <rect x="3" y="8" width="13" height="13" rx="2" />
    <path d="M8 8V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-3" />
  </svg>
);

/**
 * Window control buttons (minimize, maximize/restore, close) for the
 * frameless Electron window on Windows and Linux.  macOS uses native
 * traffic-light buttons instead.
 *
 * Each button is marked ``WebkitAppRegion: "no-drag"`` so it remains
 * clickable even when rendered inside a drag region.
 */
export interface WindowControlsProps {
  onMinimize: () => void;
  onMaximize: () => void;
  onClose: () => void;
  isMaximized?: boolean;
}

const btnBase =
  "inline-flex h-full w-[36px] flex-shrink-0 items-center justify-center text-ink-body transition-colors hover:bg-surface-muted";

const closeBtnBase =
  "inline-flex h-full w-[36px] flex-shrink-0 items-center justify-center text-ink-body transition-colors hover:bg-destructive hover:text-destructive-foreground";

const noDragStyle = { WebkitAppRegion: "no-drag" } as CSSProperties;

export const WindowControls = ({
  onMinimize,
  onMaximize,
  onClose,
  isMaximized = false,
}: WindowControlsProps) => (
  <div
    className="flex h-full shrink-0 pr-2"
    style={{ WebkitAppRegion: "no-drag" } as CSSProperties}
  >
    {/* Minimize */}
    <button
      type="button"
      aria-label="Minimize"
      onClick={onMinimize}
      className={btnBase}
      style={noDragStyle}
    >
      <Minus className="h-[16px] w-[16px]" strokeWidth={2} />
    </button>

    {/* Maximize / Restore */}
    <button
      type="button"
      aria-label={isMaximized ? "Restore" : "Maximize"}
      onClick={onMaximize}
      className={btnBase}
      style={noDragStyle}
    >
      {isMaximized ? (
        <RestoreIcon className="h-[14px] w-[14px]" />
      ) : (
        <Square className="h-[14px] w-[14px]" strokeWidth={2} />
      )}
    </button>

    {/* Close — red background + white text on hover */}
    <button
      type="button"
      aria-label="Close"
      onClick={onClose}
      className={closeBtnBase}
      style={noDragStyle}
    >
      <X className="h-[16px] w-[16px]" strokeWidth={2} />
    </button>
  </div>
);

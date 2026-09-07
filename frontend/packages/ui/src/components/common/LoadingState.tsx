import { Loader2 } from "lucide-react";

import { cn } from "../../lib/cn";

export interface LoadingStateProps {
  /**
   * `page`: a page or pane's first load — anchored to the top of the
   * content area (horizontally centred, 64px down), where the content will
   * appear; never vertically centred, so the spinner lands at the same
   * height on every page and pane regardless of the container. 24px muted
   * spinner, optional label underneath.
   * `section`: a card / list section reloading — centred in the section,
   * 16px muted spinner with the label inline.
   */
  variant?: "page" | "section";
  label?: string;
  className?: string;
}

/**
 * The two block-level loading tiers of the design system. Inline (button /
 * row) loading uses `Spinner`. Colours are fixed (muted), sizes are fixed
 * per tier, position comes from the tier — callers only choose the tier
 * and the copy.
 */
export const LoadingState = ({
  variant = "section",
  label,
  className,
}: LoadingStateProps) =>
  variant === "page" ? (
    <div
      role="status"
      aria-label={label ?? "Loading"}
      className={cn(
        "flex w-full flex-col items-center gap-2 py-16 text-ink-muted",
        className,
      )}
    >
      <Loader2 className="size-6 shrink-0 animate-spin" />
      {label ? <span className="text-xs text-ink-meta">{label}</span> : null}
    </div>
  ) : (
    <div
      role="status"
      aria-label={label ?? "Loading"}
      className={cn(
        "flex w-full items-center justify-center gap-2 py-8 text-sm text-ink-meta",
        className,
      )}
    >
      <Loader2 className="size-4 shrink-0 animate-spin" />
      {label ? <span>{label}</span> : null}
    </div>
  );

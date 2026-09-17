import type { ReactNode } from "react";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../ui/tooltip";

/**
 * One icon button in a turn's action row — copy, 👍, 👎, retry, fork, share.
 *
 * Every control in that row is a bare glyph, so each one has to say what it
 * does on hover. A native ``title`` did that unevenly (some buttons had one,
 * some had only an ``aria-label``) and always a second late; this renders the
 * design system's tooltip instead, and carries the label to screen readers in
 * the same breath.
 *
 * It also owns the row's shared geometry, which is why the host's own actions
 * (the share button the commercial overlay injects, the fork button
 * ``@valuz/app`` renders) use it too: six buttons sized and hovered from one
 * place cannot drift apart.
 */
export function TurnActionButton({
  label,
  onClick,
  children,
  disabled,
  /**
   * The thumb that currently carries the turn's rating. It darkens to the
   * heading ink and thickens its stroke — a brand fill at this size read as a
   * block of colour rather than a selected icon.
   */
  active,
  /** ``aria-pressed`` for the two toggles; omitted for plain actions. */
  pressed,
}: {
  label: string;
  onClick?: () => void;
  children: ReactNode;
  disabled?: boolean;
  active?: boolean;
  pressed?: boolean;
}) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            aria-label={label}
            aria-pressed={pressed}
            className={`flex h-7 w-7 items-center justify-center rounded transition-colors hover:bg-surface-muted disabled:cursor-default disabled:opacity-60 ${
              active ? "text-ink-heading/65" : "text-ink-body"
            }`}
          >
            {children}
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom" align="center">
          {label}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

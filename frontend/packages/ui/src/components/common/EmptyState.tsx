import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface EmptyStateProps {
  /** Optional icon or illustration above the message */
  icon?: ReactNode;
  /** Primary empty-state message */
  message: string;
  /** Optional call-to-action below the message */
  action?: ReactNode;
  /** Extra class on the outer container */
  className?: string;
}

/**
 * Dashed-border empty state card.
 * Used when a list or grid has no items to display.
 */
export const EmptyState = ({
  icon,
  message,
  action,
  className,
}: EmptyStateProps) => (
  <div
    className={cn(
      "rounded-[10px] border border-dashed border-surface-border-hover bg-surface-soft px-5 py-6 text-center",
      className,
    )}
  >
    {icon && (
      <div className="mb-2 flex justify-center text-ink-meta">{icon}</div>
    )}
    <p className="text-sm text-ink-body">{message}</p>
    {action && <div className="mt-3">{action}</div>}
  </div>
);

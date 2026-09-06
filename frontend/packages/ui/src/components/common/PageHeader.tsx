import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface PageHeaderProps {
  /** Plain text, or a node such as a title-level mode switch. */
  title: ReactNode;
  description?: string;
  navigation?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export const PageHeader = ({
  title,
  description,
  navigation,
  action,
  className,
}: PageHeaderProps) => (
  <div
    data-slot="page-header"
    className={cn("flex w-full items-center justify-between gap-4", className)}
  >
    <div className="flex min-w-0 items-center gap-4">
      <div className="flex min-w-0 shrink-0 flex-col justify-center">
        <span className="text-base font-semibold leading-5 text-ink-heading">
          {title}
        </span>
        {description && (
          <span className="truncate text-xs leading-4 text-ink-body">
            {description}
          </span>
        )}
      </div>
      {navigation && <div className="min-w-0 shrink-0">{navigation}</div>}
    </div>
    {action && <div className="shrink-0">{action}</div>}
  </div>
);

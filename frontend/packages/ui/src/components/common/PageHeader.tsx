import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface PageHeaderProps {
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export const PageHeader = ({
  title,
  description,
  action,
  className,
}: PageHeaderProps) => (
  <div
    data-slot="page-header"
    className={cn("flex w-full items-center justify-between gap-4", className)}
  >
    <div className="flex min-w-0 flex-col justify-center">
      <span className="text-base font-semibold leading-5 text-ink-heading">
        {title}
      </span>
      {description && (
        <span className="truncate text-xs leading-4 text-ink-body">
          {description}
        </span>
      )}
    </div>
    {action && <div className="shrink-0">{action}</div>}
  </div>
);

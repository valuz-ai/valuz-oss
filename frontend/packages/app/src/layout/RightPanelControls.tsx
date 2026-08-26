import { Maximize2, Minimize2 } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@valuz/ui";

type RightPanelControlLabels = {
  collapse: string;
  expand: string;
  maximize: string;
  restore: string;
};

export function RightPanelControls({
  collapsed,
  labels,
  maximized,
  onToggleCollapsed,
  onToggleMaximized,
}: {
  collapsed: boolean;
  labels: RightPanelControlLabels;
  maximized: boolean;
  onToggleCollapsed: () => void;
  onToggleMaximized: () => void;
}) {
  const collapseLabel = collapsed ? labels.expand : labels.collapse;
  const maximizeLabel = maximized ? labels.restore : labels.maximize;
  const buttonClassName =
    "flex h-[22px] w-[22px] items-center justify-center rounded-[5px] text-ink-body transition-colors hover:bg-surface-muted";

  return (
    <TooltipProvider delayDuration={150}>
      {!collapsed ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label={maximizeLabel}
              onClick={onToggleMaximized}
              className={buttonClassName}
            >
              {maximized ? (
                <Minimize2 className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">{maximizeLabel}</TooltipContent>
        </Tooltip>
      ) : null}

      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={collapseLabel}
            onClick={onToggleCollapsed}
            className={buttonClassName}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
              <line
                x1={collapsed ? 17 : 15}
                y1={collapsed ? 7 : 3}
                x2={collapsed ? 17 : 15}
                y2={collapsed ? 17 : 21}
              />
            </svg>
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">{collapseLabel}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

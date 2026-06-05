import { type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Badge } from "../ui/badge";
import { ConnectorIcon } from "./ConnectorIcon";

export interface ConnectorListItemProps {
  name: string;
  iconUrl?: string | null;
  /** Small chip after the name — transport ("HTTP"/"Stdio"), "Custom",
   *  or a live status word. Omitted when null/empty. */
  badge?: string | null;
  active?: boolean;
  onClick?: () => void;
  /** Optional action slot rendered at the trailing edge of the list item. */
  actions?: ReactNode;
}

// Compact left-rail row: icon + name + optional badge. Mirrors the
// density of the mockup's connector list (no description in the row).
export const ConnectorListItem = ({
  name,
  iconUrl,
  badge,
  active,
  onClick,
  actions,
}: ConnectorListItemProps) => {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full cursor-default items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors select-none",
        active ? "bg-surface-soft" : "hover:bg-surface-soft/60",
      )}
    >
      <ConnectorIcon
        name={name}
        iconUrl={iconUrl}
        className="h-7 w-7 text-sm"
      />
      <span className="min-w-0 flex-1 truncate text-sm text-ink-heading">
        {name}
      </span>
      {badge ? (
        <Badge
          variant="brand"
          className="shrink-0 rounded-[4px] bg-surface-soft px-1 py-0 text-[10px] leading-4 font-normal text-ink-meta"
        >
          {badge}
        </Badge>
      ) : null}
      {actions}
    </button>
  );
};

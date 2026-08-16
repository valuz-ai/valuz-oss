import { type KeyboardEvent, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Badge } from "../ui/badge";
import { PluginBadge, type PluginBadgeInfo } from "../common/PluginBadge";
import { StatusPill } from "../common/StatusPill";
import { ConnectorIcon } from "./ConnectorIcon";

export interface ConnectorListItemProps {
  name: string;
  iconUrl?: string | null;
  /** Small chip after the name — transport ("HTTP"/"Stdio"), "Custom",
   *  or a live status word. Omitted when null/empty. */
  badge?: string | null;
  /** Raw connector status (``connected`` / ``error`` / ``pending_auth`` …).
   *  When set together with {@link statusLabel}, renders a colored status
   *  pill (via the shared status-tone palette) instead of the plain badge,
   *  making "已连接 / 出错 / 未连接" obvious at a glance. */
  status?: string | null;
  /** Localized label for the status pill (callers own the i18n). */
  statusLabel?: string | null;
  /** Plugin ownership chip ("插件：X +n") — omitted when the connector
   * belongs to no plugin (D6). */
  pluginBadge?: PluginBadgeInfo | null;
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
  status,
  statusLabel,
  pluginBadge,
  active,
  onClick,
  actions,
}: ConnectorListItemProps) => {
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!onClick || event.target !== event.currentTarget) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onClick();
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={handleKeyDown}
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
      {pluginBadge ? (
        <PluginBadge
          name={pluginBadge.name}
          more={pluginBadge.more}
          className="shrink-0"
        />
      ) : null}
      {status && statusLabel ? (
        <StatusPill
          status={status}
          label={statusLabel}
          className="shrink-0 px-1.5 py-0 text-[10px] leading-4"
        />
      ) : badge ? (
        <Badge
          variant="metaNeutral"
          className="h-4 shrink-0 px-1 text-[10px] leading-4 font-normal text-ink-meta"
        >
          {badge}
        </Badge>
      ) : null}
      {actions}
    </div>
  );
};

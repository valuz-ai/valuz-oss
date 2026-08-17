/**
 * Topbar notification badge (docs/design/notifications.md). Renders nothing
 * while there are no notifications at all; with items but nothing unread it
 * shows a bare bell (history stays reachable); with unread items it shows the
 * UNREAD count in a brand pill — the mail-app convention the number is read
 * as. No extra corner dot: the pill's presence IS the unread signal (the old
 * total-count chip + dot double-signalled the same condition and read as a
 * rendering glitch). The total lives in the drawer title instead.
 */

import { type ReactElement } from "react";
import { Bell } from "lucide-react";

import {
  useNotificationStore,
  useNotificationTotalCount,
  useNotificationUnreadCount,
  useTranslation,
} from "@valuz/core";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@valuz/ui";
import type { I18nKey } from "@valuz/shared";

export function NotificationBadge(): ReactElement | null {
  const { t } = useTranslation();
  const total = useNotificationTotalCount();
  const unread = useNotificationUnreadCount();
  const setOpen = useNotificationStore((s) => s.setOpen);
  const clearFresh = useNotificationStore((s) => s.clearFresh);

  if (total === 0) return null;

  const handleClick = () => {
    setOpen(true);
    clearFresh();
  };

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={handleClick}
            aria-label={t("notification.inboxTitle" as I18nKey)}
            className="relative flex h-[22px] items-center gap-1 rounded-[5px] px-1.5 text-ink-body transition-colors hover:bg-surface-muted"
          >
            <Bell className="h-3.5 w-3.5" />
            {unread > 0 && (
              // Flex centering (not line-height) so the digit's baseline can't
              // sink the pill below the bell's optical center — plus a 0.5px
              // optical lift: the lucide bell's visual mass sits above its box
              // center (the bottom row is only the thin clapper stroke), so a
              // geometrically centered pill reads as hanging low next to it.
              // Same box as the bell (h-3.5): the filled pill carries more
              // visual weight than the outlined icon, so equal boxes read as
              // equal size. 10px digits keep two-digit counts breathable.
              <span className="flex h-3.5 min-w-3.5 -translate-y-[0.5px] items-center justify-center rounded-full bg-brand px-1 text-micro font-semibold text-white">
                {unread}
              </span>
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {unread > 0
            ? t("notification.badgeTooltip" as I18nKey).replace(
                "{count}",
                String(unread),
              )
            : t("notification.inboxTitle" as I18nKey)}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

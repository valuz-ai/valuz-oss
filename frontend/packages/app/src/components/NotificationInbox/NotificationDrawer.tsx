/**
 * Right-side slide-over over the notification ledger
 * (docs/design/notifications.md). Store-driven open state so the topbar badge
 * toggles it. Two tabs: the OPEN set (one actionable ``NotificationCard`` per
 * entry, dispatched by kind, plus a clear-all) and the read-only History list
 * behind ``GET /v1/notifications/history``.
 */

import { useEffect, useState, type ReactElement } from "react";

import {
  dismissAllNotifications,
  useNotifications,
  useNotificationIsOpen,
  useNotificationStore,
  useTranslation,
} from "@valuz/core";
import {
  Button,
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  Tabs,
  TabsList,
  TabsTrigger,
} from "@valuz/ui";
import type { I18nKey } from "@valuz/shared";

import { NotificationCard } from "./NotificationCard";
import { NotificationHistoryList } from "./NotificationHistoryList";

type DrawerTab = "open" | "history";

export function NotificationDrawer(): ReactElement {
  const { t } = useTranslation();
  const isOpen = useNotificationIsOpen();
  const entries = useNotifications();
  const setOpen = useNotificationStore((s) => s.setOpen);
  const [tab, setTab] = useState<DrawerTab>("open");

  // Every open lands on the open set — the badge click is always "what needs
  // me now"; History stays one tap away.
  useEffect(() => {
    if (isOpen) setTab("open");
  }, [isOpen]);

  return (
    <Sheet open={isOpen} onOpenChange={setOpen}>
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-md">
        <SheetHeader className="gap-1 border-b border-surface-border px-4 pt-3 pb-0">
          <div className="flex items-center justify-between pr-8">
            <SheetTitle className="text-base">
              {t("notification.inboxTitle" as I18nKey)}
              {entries.length > 0 && (
                <span className="ml-2 text-sm font-normal text-ink-muted">
                  · {entries.length}
                </span>
              )}
            </SheetTitle>
            {tab === "open" && entries.length > 0 && (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 text-xs text-ink-muted"
                onClick={dismissAllNotifications}
              >
                {t("notification.clearAll" as I18nKey)}
              </Button>
            )}
          </div>
          {/* Line-tab switcher — same pattern as the activity page filter, so
              the drawer reads as part of the same design system. The header's
              own border-b doubles as the tabs' baseline. */}
          <Tabs value={tab} onValueChange={(v) => setTab(v as DrawerTab)}>
            <TabsList
              variant="line"
              className="h-9 justify-start gap-4 border-0 p-0"
            >
              <TabsTrigger value="open">
                {t("notification.tabOpen" as I18nKey)}
              </TabsTrigger>
              <TabsTrigger value="history">
                {t("notification.tabHistory" as I18nKey)}
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </SheetHeader>

        {tab === "history" ? (
          <NotificationHistoryList onNavigateAway={() => setOpen(false)} />
        ) : entries.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
            <span className="text-3xl opacity-40">🔔</span>
            <p className="text-sm font-medium text-ink-body">
              {t("notification.emptyTitle" as I18nKey)}
            </p>
            <p className="text-xs text-ink-muted">
              {t("notification.emptyHint" as I18nKey)}
            </p>
          </div>
        ) : (
          <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
            {entries.map((entry) => (
              <NotificationCard
                key={entry.id}
                entry={entry}
                onNavigateAway={() => setOpen(false)}
              />
            ))}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

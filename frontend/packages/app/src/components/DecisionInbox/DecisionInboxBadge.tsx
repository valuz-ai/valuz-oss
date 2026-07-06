/**
 * Topbar badge for the Decision Inbox (ADR-022). Renders nothing when
 * there are zero pendings — no "0" chip. Clicking opens the drawer and
 * marks everything read (clears the unread accent).
 */

import { type ReactElement } from "react";

import {
  useDecisionPending,
  useDecisionStore,
  useDecisionTotalCount,
  useDecisionUnreadCount,
  useTranslation,
} from "@valuz/core";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@valuz/ui";
import type { I18nKey } from "@valuz/shared";

export function DecisionInboxBadge(): ReactElement | null {
  const { t } = useTranslation();
  const total = useDecisionTotalCount();
  const unread = useDecisionUnreadCount();
  const pending = useDecisionPending();
  const setOpen = useDecisionStore((s) => s.setOpen);
  const markAllRead = useDecisionStore((s) => s.markAllRead);

  if (total === 0) return null;

  const agentPreview = pending
    .slice(0, 3)
    .map((e) => e.agent_slug)
    .join("、");

  const handleClick = () => {
    setOpen(true);
    markAllRead();
  };

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={handleClick}
            aria-label={t("decisionInbox.title" as I18nKey)}
            className={`flex h-6 items-center gap-1.5 rounded-md bg-warning-light px-2 text-xs font-medium text-warning-text transition-[filter] hover:brightness-95 ${
              unread > 0 ? "animate-attention-pulse" : ""
            }`}
          >
            <span className="text-sm leading-none">📥</span>
            <span>{t("decisionInbox.badgeLabel" as I18nKey)}</span>
            <span className="min-w-[15px] text-center font-semibold">
              {total}
            </span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {t("decisionInbox.badgeTooltip" as I18nKey).replace(
            "{count}",
            String(total),
          )}
          {agentPreview ? ` · ${agentPreview}` : ""}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

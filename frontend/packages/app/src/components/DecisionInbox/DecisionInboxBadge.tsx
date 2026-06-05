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
            className="relative flex h-[22px] items-center gap-1 rounded-[5px] px-1.5 text-ink-body transition-colors hover:bg-surface-muted"
          >
            <span className="text-sm leading-none">📥</span>
            <span
              className={`min-w-[16px] rounded-full px-1 text-center text-2xs font-semibold leading-[16px] ${
                unread > 0
                  ? "bg-brand text-white"
                  : "bg-surface-soft text-ink-muted"
              }`}
            >
              {total}
            </span>
            {unread > 0 && (
              <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-brand" />
            )}
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

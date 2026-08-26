/**
 * Plan-mode proposal card (``session.plan_proposed`` — codex's plan
 * thread item).
 *
 * NON-blocking, unlike the ``exit_plan_mode`` ``ApprovalCard``: on codex
 * the turn has already ended when the proposal arrives, and approval is
 * client-driven (the host PATCHes the session mode to ``default`` and
 * starts an execution turn). The card therefore renders the plan
 * markdown plus a host-provided ``actions`` node — the host passes the
 * "approve & start executing" button only for the LATEST proposal while
 * the session is still in plan mode; historical proposals render as
 * plain records. "Keep planning" needs no button here: the user simply
 * types feedback and the next plan turn revises the proposal.
 */
import type { ReactNode } from "react";
import { ClipboardList } from "lucide-react";

import { MarkdownContent } from "./MarkdownContent";
import { useI18n } from "../../hooks/use-i18n";

interface PlanProposalCardProps {
  plan: string;
  /** Host-wired footer (approve button). ``null`` renders a plain record. */
  actions?: ReactNode | null;
}

export function PlanProposalCard({ plan, actions }: PlanProposalCardProps) {
  const { t } = useI18n();
  return (
    <div className="rounded-lg border-l-2 border-brand bg-surface-soft shadow-sm">
      <div className="flex items-center gap-2 px-3 py-2">
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-brand-light text-brand">
          <ClipboardList className="h-3.5 w-3.5" />
        </div>
        <span className="flex-1 text-sm font-medium text-ink-heading">
          {t("conversation.planProposalTitle" as Parameters<typeof t>[0])}
        </span>
      </div>
      <div className="px-3 pb-2">
        <div className="max-h-64 overflow-auto rounded-md bg-surface-2 px-3 py-2">
          <MarkdownContent content={plan || "(empty plan)"} />
        </div>
      </div>
      {actions ? (
        <div className="flex items-center justify-between gap-2 border-t border-surface-border px-3 py-2">
          <span className="text-2xs text-ink-meta">
            {t("conversation.planProposalHint" as Parameters<typeof t>[0])}
          </span>
          <div className="flex items-center gap-2">{actions}</div>
        </div>
      ) : null}
    </div>
  );
}

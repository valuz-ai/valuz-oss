/**
 * 「等你处理」— the pinned attention group at the top of the Activity page
 * (question-attention, PRD §界面与交互).
 *
 * Renders every pending decision as an inline-answerable card (the same
 * ``DecisionEntryCard`` the drawer uses — one answer UX everywhere).
 * Renders nothing when there is no pending, so the Activity page keeps its
 * usual quiet layout. Data source is the global decision store; answering
 * removes the card via the SSE ``resolved`` frame, no local state.
 */

import { type ReactElement } from "react";

import { useDecisionPending, useTranslation } from "@valuz/core";
import type { I18nKey } from "@valuz/shared";

import { DecisionEntryCard } from "./DecisionEntryCard";

export function AttentionGroup(): ReactElement | null {
  const { t } = useTranslation();
  const pending = useDecisionPending();

  if (pending.length === 0) return null;

  return (
    <section className="mt-5">
      <div className="mb-2 flex items-center gap-2 px-3">
        <span className="text-[11.5px] font-medium uppercase tracking-[0.06em] text-warning-text">
          {t("decisionInbox.attentionGroupTitle" as I18nKey)}
        </span>
        <span className="text-[11.5px] font-medium text-warning-text">
          · {pending.length}
        </span>
      </div>
      <div className="flex flex-col gap-3">
        {pending.map((entry) => (
          <DecisionEntryCard key={entry.pending_id} entry={entry} />
        ))}
      </div>
      <div className="my-6 border-t border-surface-border" />
    </section>
  );
}

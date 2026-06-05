/**
 * Right-side slide-over listing every pending decision across all
 * task-driven sessions (ADR-022). Open/close is store-driven (not a
 * trigger) so the topbar badge and ⌘-anything can both toggle it.
 *
 * Renders one ``DecisionEntryCard`` per pending, oldest-first. Empty
 * state is a gentle hint — the drawer can be opened with zero pendings
 * (e.g. the user clicked the badge just as the last one resolved).
 */

import { type ReactElement } from "react";

import {
  useDecisionIsOpen,
  useDecisionPending,
  useDecisionStore,
  useTranslation,
} from "@valuz/core";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@valuz/ui";
import type { I18nKey } from "@valuz/shared";

import { DecisionEntryCard } from "./DecisionEntryCard";

export function DecisionDrawer(): ReactElement {
  const { t } = useTranslation();
  const isOpen = useDecisionIsOpen();
  const pending = useDecisionPending();
  const setOpen = useDecisionStore((s) => s.setOpen);

  return (
    <Sheet open={isOpen} onOpenChange={setOpen}>
      <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-md">
        <SheetHeader className="border-b border-surface-border px-4 py-3">
          <SheetTitle className="text-base">
            {t("decisionInbox.title" as I18nKey)}
            {pending.length > 0 && (
              <span className="ml-2 text-sm font-normal text-ink-muted">
                · {pending.length}
              </span>
            )}
          </SheetTitle>
        </SheetHeader>

        {pending.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
            <span className="text-3xl opacity-40">📭</span>
            <p className="text-sm font-medium text-ink-body">
              {t("decisionInbox.emptyTitle" as I18nKey)}
            </p>
            <p className="text-xs text-ink-muted">
              {t("decisionInbox.emptyHint" as I18nKey)}
            </p>
          </div>
        ) : (
          <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
            {pending.map((entry) => (
              <DecisionEntryCard
                key={entry.pending_id}
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

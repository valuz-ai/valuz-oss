/**
 * Mount-once provider for the global Decision Inbox (ADR-022).
 *
 * Two jobs:
 * 1. Kick off the singleton SSE subscription via ``useDecisionInbox``.
 * 2. Fire ONE light toast per genuinely-new pending — NOT for entries
 *    already present in the cold-start snapshot (those populate the
 *    store via ``reset`` which never touches ``unreadIds``), and never
 *    twice for the same ``pending_id`` (guarded by ``toastedIds``).
 *
 * MUST be mounted at the AppShell / layout level so the subscription
 * persists across route changes. Renders ``null``.
 */

import { useEffect, type ReactElement } from "react";

import { useDecisionInbox, useDecisionStore } from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";
import type { I18nKey } from "@valuz/shared";
import { toast } from "sonner";

export function DecisionInboxProvider(): ReactElement | null {
  // Singleton subscription (idempotent — safe to also mount elsewhere).
  useDecisionInbox();

  useEffect(() => {
    // Subscribe to store changes; for every unread pending we haven't
    // toasted yet, fire one light toast and mark it toasted. Unread is
    // only set by ``add()`` (live SSE), so snapshot-loaded entries
    // never toast.
    const unsub = useDecisionStore.subscribe((state) => {
      if (state.unreadIds.size === 0) return;
      for (const pendingId of state.unreadIds) {
        if (state.toastedIds.has(pendingId)) continue;
        const entry = state.pending.get(pendingId);
        if (!entry) continue;
        // Mark first so a re-entrant subscribe (from markToasted's own
        // set()) doesn't double-fire.
        state.markToasted(pendingId);
        toast.info(
          _t("decisionInbox.toastNew" as I18nKey).replace(
            "{agent}",
            entry.agent_slug,
          ),
        );
      }
    });
    return unsub;
  }, []);

  return null;
}

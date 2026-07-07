/**
 * Mount-once provider for the global Decision Inbox (ADR-022 +
 * question-attention).
 *
 * Three jobs:
 * 1. Kick off the singleton SSE subscription via ``useDecisionInbox``.
 * 2. Dispatch ONE reminder per genuinely-new pending through the channel
 *    the attention policy picks (``decideAttentionChannel``):
 *    - watched session → silent (the inline card is already on screen)
 *    - app focused     → clickable toast (jumps to the pending's route)
 *    - app in background → OS notification (main process; click focuses +
 *      deep-links back)
 *    Never twice for the same ``pending_id`` (guarded by ``toastedIds`` —
 *    shared across toast AND system notification).
 * 3. Mirror the pending count onto the dock badge (``setAttentionBadge``).
 *
 * MUST be mounted at the AppShell / layout level (inside the router) so
 * the subscription persists across route changes. Renders ``null``.
 */

import { useEffect, useRef, type ReactElement } from "react";
import { useNavigate } from "react-router-dom";

import {
  attentionContextLabel,
  attentionRoute,
  decideAttentionChannel,
  isSessionWatched,
  sendAttentionNotification,
  setAttentionBadge,
  useDecisionInbox,
  useDecisionStore,
} from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";
import type { I18nKey } from "@valuz/shared";
import { toast } from "sonner";

export function DecisionInboxProvider(): ReactElement | null {
  // Singleton subscription (idempotent — safe to also mount elsewhere).
  useDecisionInbox();
  const navigate = useNavigate();
  // navigate is not referentially stable across navigations — ref it so the
  // store subscription below registers once for the app's lifetime instead
  // of churning per route change.
  const navigateRef = useRef(navigate);
  useEffect(() => {
    navigateRef.current = navigate;
  }, [navigate]);
  const lastBadgeCount = useRef(-1);

  useEffect(() => {
    const unsub = useDecisionStore.subscribe((state) => {
      // Dock badge mirrors the pending count — only when it actually
      // changes (the store also mutates on drawer toggles / read marks,
      // which must not round-trip IPC).
      if (state.pending.size !== lastBadgeCount.current) {
        lastBadgeCount.current = state.pending.size;
        setAttentionBadge(state.pending.size);
      }

      if (state.unreadIds.size === 0) return;
      for (const pendingId of state.unreadIds) {
        if (state.toastedIds.has(pendingId)) continue;
        const entry = state.pending.get(pendingId);
        if (!entry) continue;
        // Mark first so a re-entrant subscribe (from markToasted's own
        // set()) doesn't double-fire.
        state.markToasted(pendingId);

        const channel = decideAttentionChannel(
          isSessionWatched(entry.session_id),
          typeof document !== "undefined" && document.hasFocus(),
        );
        if (channel === "silent") continue;

        const title = _t("decisionInbox.toastNew" as I18nKey).replace(
          "{agent}",
          entry.agent_slug,
        );
        const context = attentionContextLabel(entry);
        const route = attentionRoute(entry);
        if (channel === "toast") {
          toast.info(title, {
            description: context || undefined,
            action: {
              label: _t("decisionInbox.toastAction" as I18nKey),
              onClick: () => navigateRef.current(route),
            },
          });
        } else {
          sendAttentionNotification({ title, body: context, route });
        }
      }
    });
    return unsub;
  }, []);

  return null;
}

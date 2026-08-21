import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, sessionsApi, useTranslation } from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";

/**
 * Shared session-fork action behind every fork entry point (conversation
 * header, message hover, sidebar recents, activity rows).
 *
 * Fork is synchronous by design (docs/design/session-fork.md D5): the
 * runtime-native fork and the history copy run inside the POST, which on
 * remote-kernel deployments routinely takes seconds (#879). This hook owns
 * the two client-side consequences:
 *
 * - **Single flight** — a ref rejects re-entry synchronously, so a second
 *   click that lands before React re-renders still produces zero extra
 *   forks. State alone can't do this: the first click's ``setState`` isn't
 *   visible to a same-tick second click's closure.
 * - **Pending state** — ``forkingSessionId`` / ``forkingMessageId`` let each
 *   entry point render a spinner and disable its trigger while in flight.
 *
 * On success: toast, nudge the sidebar's finished-runs window (a fork is
 * born idle WITH history, so no ``run.finished`` frame ever announces it),
 * and navigate into the new conversation.
 */
export function useForkSession() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const inFlightRef = useRef(false);
  const [forking, setForking] = useState<{
    sessionId: string;
    messageId?: string;
  } | null>(null);

  const fork = useCallback(
    async (sessionId: string, messageId?: string) => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      setForking({ sessionId, messageId });
      try {
        const forked = await sessionsApi.fork(sessionId, messageId);
        toast.success(t("conversation.forked" as Parameters<typeof t>[0]));
        window.dispatchEvent(new CustomEvent("valuz-runs-refresh"));
        navigate(`/conversation/${encodeURIComponent(forked.id)}`);
      } catch (cause) {
        // Prefer a backend-attached i18n key; else map the actionable
        // status (409 = invalid anchor / turn in flight) to local copy.
        const msg =
          cause instanceof ApiError && cause.i18nKey
            ? _t(
                cause.i18nKey as Parameters<typeof _t>[0],
                cause.i18nParams as Parameters<typeof _t>[1],
              )
            : cause instanceof ApiError && cause.status === 409
              ? t("conversation.forkConflict" as Parameters<typeof t>[0])
              : t("conversation.forkFailed" as Parameters<typeof t>[0]);
        toast.error(msg);
      } finally {
        inFlightRef.current = false;
        setForking(null);
      }
    },
    [navigate, t],
  );

  return {
    fork,
    forkInFlight: forking !== null,
    /** Session whose fork is in flight — rows matching it show a spinner. */
    forkingSessionId: forking?.sessionId ?? null,
    /** Anchor message of an in-flight message-granularity fork, if any. */
    forkingMessageId: forking?.messageId ?? null,
  };
}

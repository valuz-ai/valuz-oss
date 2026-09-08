import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError, sessionsApi } from "@valuz/core";
import type {
  ConversationTurn,
  FeedbackValue,
  TurnFeedbackDetails,
} from "@valuz/shared";
import { t as _t } from "@valuz/shared/i18n";
import { NEW_SESSION_ID } from "./session-events";

/**
 * Per-session 👍/👎 state + the two client-side feedback writes
 * (docs/design/feedback-signals.md).
 *
 * Ratings rehydrate from ``GET /v1/sessions/{id}/feedback`` whenever the
 * session changes, so a reload shows the same thumbs. Writes are optimistic
 * and roll back on failure. A rating lands the moment a side is chosen; the
 * optional "提交反馈" dialog then re-records the same row with chips + text
 * (``details``). ``copy`` is fire-and-forget — the server counts repeats on
 * one row, so there is nothing to show and nothing to retry.
 */
export function useSessionFeedback(sessionId: string | null | undefined) {
  const [ratings, setRatings] = useState<Record<string, FeedbackValue>>({});
  const ratingsRef = useRef(ratings);
  ratingsRef.current = ratings;
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;

  useEffect(() => {
    setRatings({});
    if (!sessionId || sessionId === NEW_SESSION_ID) return;
    let cancelled = false;
    sessionsApi
      .listFeedback(sessionId)
      .then((response) => {
        if (cancelled) return;
        const next: Record<string, FeedbackValue> = {};
        for (const item of response.items) {
          if (item.action === "rating" && item.value && !item.block_ref) {
            next[item.message_id] = item.value;
          }
        }
        setRatings(next);
      })
      .catch(() => {
        /* rehydration is best-effort — the thumbs just start unset */
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const rateTurn = useCallback(
    async (
      turn: ConversationTurn,
      value: FeedbackValue | null,
      details?: TurnFeedbackDetails,
    ) => {
      const sid = sessionRef.current;
      const messageId = turn.messageId;
      if (!sid || sid === NEW_SESSION_ID || !messageId) return;
      const previous = ratingsRef.current[messageId];
      setRatings((current) => {
        const next = { ...current };
        if (value) next[messageId] = value;
        else delete next[messageId];
        return next;
      });
      try {
        if (value) {
          const reasonCodes = details?.reasonCodes?.length
            ? details.reasonCodes
            : null;
          await sessionsApi.recordFeedback(sid, {
            message_id: messageId,
            action: "rating",
            value,
            reason_code: reasonCodes?.[0] ?? null,
            reason_codes: reasonCodes,
            reason: details?.reason?.trim() || null,
            source: "ui",
            surface: "chat",
          });
          if (details) {
            toast.success(
              _t("conversation.feedback.thanks" as Parameters<typeof _t>[0]),
            );
          }
        } else {
          await sessionsApi.withdrawFeedback(sid, {
            message_id: messageId,
            action: "rating",
          });
        }
      } catch (error) {
        // Withdrawing a row that is already gone is the state we wanted.
        if (!value && error instanceof ApiError && error.status === 404) return;
        setRatings((current) => {
          const next = { ...current };
          if (previous) next[messageId] = previous;
          else delete next[messageId];
          return next;
        });
        toast.error(
          _t("conversation.feedback.saveFailed" as Parameters<typeof _t>[0]),
        );
      }
    },
    [],
  );

  const reportCopy = useCallback((turn: ConversationTurn) => {
    const sid = sessionRef.current;
    const messageId = turn.messageId;
    if (!sid || sid === NEW_SESSION_ID || !messageId) return;
    void sessionsApi
      .recordFeedback(sid, {
        message_id: messageId,
        action: "copy",
        source: "ui",
        surface: "chat",
      })
      .catch(() => {
        /* a lost copy signal is not worth a toast */
      });
  }, []);

  return { ratings, rateTurn, reportCopy };
}

import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import { toast } from "sonner";
import { sessionsApi, useTranslation } from "@valuz/core";
import type { PendingApprovalEntry } from "./useConversationHistory";

type ApprovalActionsParams = {
  selectedSessionId: string | null;
  currentClarifyingPendingRef: { current: string | null };
  /** Ref ``renderToolCall`` invokes; re-assigned on every render below. */
  askUserQuestionSubmitRef: {
    current: (toolId: string, answers: Record<string, string>) => void;
  };
  setPendingApprovals: Dispatch<SetStateAction<PendingApprovalEntry[]>>;
  setAskUserQuestionLocalAnswers: Dispatch<
    SetStateAction<Record<string, Record<string, string | string[]>>>
  >;
};

/**
 * ── Approval + clarifying-question actions ───────────────────────────
 *
 * Owns the ADR-013 user-decision dispatchers of the conversation page:
 * ``handleApprovalDecision`` (the four approve/reject verbs) and
 * ``handleAskUserQuestionSubmit`` (clarifying answers), plus the
 * render-time ``askUserQuestionSubmitRef`` assignment that lets
 * ``renderToolCall`` (declared before these handlers) invoke the submit
 * handler without a block-scope ordering error. Bodies are moved
 * verbatim from ``ConversationPage``.
 */
export function useApprovalActions({
  selectedSessionId,
  currentClarifyingPendingRef,
  askUserQuestionSubmitRef,
  setPendingApprovals,
  setAskUserQuestionLocalAnswers,
}: ApprovalActionsParams) {
  const { t } = useTranslation();

  // ADR-013 v2 (kernel d008b53) — single dispatcher for all 4 user
  // verbs (``approve`` / ``approve_with_changes`` /
  // ``approve_for_session`` / ``reject``). Marks the entry as
  // submitting optimistically; the paired ``action_resolved`` SSE
  // frame from the kernel flips it to ``answered``. 409 conflicts
  // mean another reconnect already resolved this pending — we let
  // the incoming SSE settle the UI rather than rolling back here.
  const handleApprovalDecision = useCallback(
    (
      pendingId: string,
      decision:
        "approve" | "approve_with_changes" | "approve_for_session" | "reject",
      opts?: {
        message?: string;
        modifiedInput?: Record<string, unknown>;
      },
    ) => {
      const sessionId = selectedSessionId;
      if (!sessionId) return;
      setPendingApprovals((prev) =>
        prev.map((p) =>
          p.pendingId === pendingId ? { ...p, submitting: true } : p,
        ),
      );
      const request: {
        pending_id: string;
        decision: typeof decision;
        message?: string;
        modified_input?: Record<string, unknown>;
      } = {
        pending_id: pendingId,
        decision,
      };
      if (decision === "reject" && opts?.message && opts.message.length > 0) {
        request.message = opts.message;
      }
      if (decision === "approve_with_changes" && opts?.modifiedInput) {
        request.modified_input = opts.modifiedInput;
      }
      sessionsApi.submitAction(sessionId, request).catch((err: unknown) => {
        setPendingApprovals((prev) =>
          prev.map((p) =>
            p.pendingId === pendingId ? { ...p, submitting: false } : p,
          ),
        );
        toast.error(
          err instanceof Error
            ? err.message
            : t("common.saveFailed" as Parameters<typeof t>[0]),
        );
      });
    },
    [selectedSessionId, t],
  );

  const handleAskUserQuestionSubmit = useCallback(
    (toolId: string, answers: Record<string, string>) => {
      const sessionId = selectedSessionId;
      const pendingId = currentClarifyingPendingRef.current;
      if (!sessionId || !pendingId) {
        // No live pending — runtime has either advanced past this turn
        // (e.g. user reloaded after answer already resolved) or never
        // received it. Surface the error; nothing to submit.
        toast.error(t("common.error" as Parameters<typeof t>[0]));
        return;
      }
      // Optimistic swap: stash the answers locally so the renderer
      // flips to ``UserAnswerSummaryCard`` on this very tick. The
      // paired ``action_resolved`` SSE frame will land shortly and
      // ``askUserQuestionAnswersByToolId`` (event-derived) will take
      // precedence — same shape, just kernel-authoritative.
      setAskUserQuestionLocalAnswers((prev) => ({
        ...prev,
        [toolId]: answers,
      }));
      sessionsApi
        .submitAction(sessionId, {
          pending_id: pendingId,
          decision: "answer",
          answers,
        })
        .then(() => {
          // The kernel will emit a paired action_resolved event over
          // SSE which clears currentClarifyingPendingRef. The runtime
          // then resumes its turn and emits subsequent tool events.
        })
        .catch((err: unknown) => {
          // Submit failed — drop the optimistic answers so the
          // interactive card returns and the user can retry.
          setAskUserQuestionLocalAnswers((prev) => {
            const next = { ...prev };
            delete next[toolId];
            return next;
          });
          toast.error(
            err instanceof Error
              ? err.message
              : t("common.saveFailed" as Parameters<typeof t>[0]),
          );
        });
    },
    [selectedSessionId],
  );
  askUserQuestionSubmitRef.current = handleAskUserQuestionSubmit;

  return { handleApprovalDecision, handleAskUserQuestionSubmit };
}

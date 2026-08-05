import {
  ApprovalCard,
  ApprovalResolvedStrip,
  AutoApprovedStrip,
} from "@valuz/ui";
import type { useApprovalActions } from "./useApprovalActions";
import type { PendingApprovalEntry } from "./useConversationHistory";
import type { AutoApprovedNotice } from "./useSessionSubscription";

type ApprovalTrayProps = {
  pendingApprovals: PendingApprovalEntry[];
  autoApprovedNotices: AutoApprovedNotice[];
  handleApprovalDecision: ReturnType<
    typeof useApprovalActions
  >["handleApprovalDecision"];
};

/**
 * ── ADR-013 approval tray ────────────────────────────────────────────
 *
 * The pending-approval cards / resolved strips / auto-approved notices
 * rendered directly above the Composer. Extracted verbatim from
 * ConversationPage's return JSX — behavior and markup unchanged; every
 * referenced page value arrives as a same-named prop.
 */
export function ApprovalTray({
  pendingApprovals,
  autoApprovedNotices,
  handleApprovalDecision,
}: ApprovalTrayProps) {
  return (
    <>
      {/* ADR-013 v2 (kernel d008b53) approval tray — renders any
            unresolved session.requires_action pending whose subject
            is NOT ``clarifying_questions`` (those use
            AskUserQuestionCard inline in the turn stream). Sits
            directly above the Composer so the parked turn is in the
            user's line of sight. Each entry swaps between the full
            ApprovalCard (pending) and the compact
            ApprovalResolvedStrip (post-decision, before fadeout).
            AutoApprovedStrip rows render cache-hit notices that
            never had a preceding card. */}
      {(pendingApprovals.length > 0 || autoApprovedNotices.length > 0) && (
        <div className="mx-auto mb-2 w-full max-w-[760px] space-y-2 px-4">
          {pendingApprovals.map((entry) => {
            if (entry.answered && entry.decision) {
              return (
                <ApprovalResolvedStrip
                  key={entry.pendingId}
                  decision={entry.decision}
                  rulePreviewDisplay={entry.sessionRulePreviewDisplay}
                  rejectMessage={entry.rejectMessage}
                  resolvedAtLabel={
                    entry.receivedAt
                      ? new Date(entry.receivedAt).toLocaleTimeString()
                      : undefined
                  }
                />
              );
            }
            return (
              <ApprovalCard
                key={entry.pendingId}
                pendingId={entry.pendingId}
                subject={entry.subject}
                payload={entry.payload}
                availableDecisions={entry.availableDecisions}
                sessionRulePreviewDisplay={entry.sessionRulePreviewDisplay}
                originalInput={entry.originalInput}
                receivedAtLabel={
                  entry.receivedAt
                    ? new Date(entry.receivedAt).toLocaleTimeString()
                    : undefined
                }
                submitting={entry.submitting}
                onApprove={() =>
                  handleApprovalDecision(entry.pendingId, "approve")
                }
                onReject={(reason) =>
                  handleApprovalDecision(entry.pendingId, "reject", {
                    message: reason,
                  })
                }
                onApproveWithChanges={(modifiedInput) =>
                  handleApprovalDecision(
                    entry.pendingId,
                    "approve_with_changes",
                    { modifiedInput },
                  )
                }
                onApproveForSession={() =>
                  handleApprovalDecision(entry.pendingId, "approve_for_session")
                }
              />
            );
          })}
          {autoApprovedNotices.map((notice) => (
            <AutoApprovedStrip
              key={notice.pendingId}
              subject={notice.subject ?? undefined}
              payload={notice.payload ?? undefined}
              rulePreviewDisplay={notice.rulePreviewDisplay}
              resolvedAtLabel={notice.receivedAtLabel}
            />
          ))}
        </div>
      )}
    </>
  );
}

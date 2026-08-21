/**
 * V5+d008b53 — compact one-line strip rendered after a pending lands.
 *
 * Replaces the full ApprovalCard when ``action_resolved`` arrives so
 * the conversation doesn't keep a tall card on screen forever. Each
 * decision verb gets its own subtle color band so a user scanning the
 * timeline can tell at a glance how the previous prompt resolved.
 */
import { memo } from "react";
import { CheckCircle2, Clock, Sparkles, XCircle } from "lucide-react";

import { cn } from "../../lib/utils";
import { useI18n } from "../../hooks/use-i18n";

export type ApprovalResolvedDecision =
  | "approve"
  | "approve_with_changes"
  | "approve_for_session"
  | "reject"
  | "answer"
  | "expired"
  | "interrupted";

interface ApprovalResolvedStripProps {
  decision: ApprovalResolvedDecision;
  /**
   * For ``approve_for_session``: the display string from
   * ``RequiresActionEvent.session_rule_preview.display`` (e.g.
   * ``Bash(npm test:*)``). Shown after the verb label.
   */
  rulePreviewDisplay?: string | null;
  /** For ``reject``: the user's rejection reason if they entered one. */
  rejectMessage?: string | null;
  /** Wall-clock string (HH:mm) for the muted timestamp. */
  resolvedAtLabel?: string;
}

const _STYLE: Record<
  ApprovalResolvedDecision,
  { tint: string; icon: typeof CheckCircle2 | null }
> = {
  approve: {
    tint: "border-success-border bg-success-light text-success-text",
    icon: CheckCircle2,
  },
  approve_with_changes: {
    tint: "border-success-border bg-success-light text-success-text",
    icon: CheckCircle2,
  },
  approve_for_session: {
    tint: "border-info-border bg-info-light text-info-text",
    icon: Sparkles,
  },
  reject: {
    tint: "border-error-border bg-error-light text-error-text",
    icon: XCircle,
  },
  answer: {
    tint: "border-success-border bg-success-light text-success-text",
    icon: CheckCircle2,
  },
  expired: {
    tint: "border-surface-border bg-surface-2 text-ink-body",
    icon: Clock,
  },
  interrupted: {
    tint: "border-surface-border bg-surface-2 text-ink-body",
    icon: Clock,
  },
};

export const ApprovalResolvedStrip = memo(function ApprovalResolvedStrip({
  decision,
  rulePreviewDisplay,
  rejectMessage,
  resolvedAtLabel,
}: ApprovalResolvedStripProps) {
  const { t } = useI18n();
  const style = _STYLE[decision] ?? _STYLE.approve;
  const Icon = style.icon;

  let label: string;
  switch (decision) {
    case "approve":
    case "answer":
      label = t("conversation.decisionApproved");
      break;
    case "approve_with_changes":
      label = t("conversation.approvalDecisionApprovedWithChanges");
      break;
    case "approve_for_session":
      label = t("conversation.approvalDecisionApprovedForSession");
      break;
    case "reject":
      label = t("conversation.decisionRejected");
      break;
    case "expired":
      label = t("conversation.decisionExpired");
      break;
    case "interrupted":
      label = t("conversation.decisionInterrupted");
      break;
    default:
      label = t("conversation.decisionHandled");
  }

  const suffix =
    decision === "approve_for_session" && rulePreviewDisplay
      ? ` · ${rulePreviewDisplay}`
      : decision === "reject" && rejectMessage
        ? ` · ${rejectMessage}`
        : "";

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border-l-2 px-2.5 py-1.5",
        style.tint,
      )}
    >
      {Icon ? <Icon className="h-3.5 w-3.5 shrink-0" /> : null}
      <span className="flex-1 truncate text-xs font-medium">
        {label}
        {suffix}
      </span>
      {resolvedAtLabel ? (
        <span className="shrink-0 text-2xs text-ink-meta/80">
          {resolvedAtLabel}
        </span>
      ) : null}
    </div>
  );
});

/**
 * V5+1aae940 / A2 — inline reject reason composer.
 *
 * Slides down below the main action row when the user clicks
 * "Reject". Carries an optional ``message`` that the kernel hands to
 * the runtime (Claude ``PermissionResultDeny.message``, DeepAgents
 * ``RejectDecision.message``; codex 0.116.0a1 logs only). Empty
 * textarea = bare reject, same as v1.
 *
 * Submit / Cancel are explicit buttons so a stray "Enter" while the
 * user is composing doesn't fire the decision mid-thought.
 */
import { memo, useState } from "react";
import { Loader2, XCircle } from "lucide-react";

import { Textarea } from "../ui/textarea";
import { Button } from "../ui/button";
import { cn } from "../../lib/utils";
import { useI18n } from "../../hooks/use-i18n";

interface ApprovalRejectInlineProps {
  submitting?: boolean;
  onSubmit: (reason: string) => void;
  onCancel: () => void;
}

export const ApprovalRejectInline = memo(function ApprovalRejectInline({
  submitting,
  onSubmit,
  onCancel,
}: ApprovalRejectInlineProps) {
  const { t } = useI18n();
  const [reason, setReason] = useState("");

  return (
    <div className={cn("space-y-2 rounded-md bg-rose-50/60 p-2.5")}>
      <Textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        disabled={submitting}
        placeholder={t("conversation.approvalRejectReasonPlaceholder")}
        className="min-h-[64px] resize-y text-[12px] leading-snug"
        spellCheck={false}
        autoFocus
      />
      <div className="flex items-center justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={onCancel}
          disabled={submitting}
        >
          {t("conversation.approvalRejectCancel")}
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => onSubmit(reason.trim())}
          disabled={submitting}
        >
          {submitting ? (
            <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
          ) : (
            <XCircle className="mr-1.5 h-3 w-3" />
          )}
          {t("conversation.approvalRejectSubmit")}
        </Button>
      </div>
    </div>
  );
});

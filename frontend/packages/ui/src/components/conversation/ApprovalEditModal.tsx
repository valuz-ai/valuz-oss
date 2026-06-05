/**
 * V5+d008b53 / A1 — "Edit & Approve" JSON editor.
 *
 * Opens from ``ApprovalCard``'s overflow menu when the runtime
 * advertises ``approve_with_changes`` in ``available_decisions``.
 * Seeds the textarea with ``original_input`` so the user mutates the
 * complete args dict rather than typing one from scratch.
 *
 * Validation matches upstream's reference impl:
 *   1. ``JSON.parse`` must succeed.
 *   2. The parsed value must be an object (``[]`` / scalar rejected).
 * No schema-aware editing in v1 — the user gets an opaque text box
 * and the agent's error message if the args are nonsensical.
 */
import { memo, useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { Textarea } from "../ui/textarea";
import { Button } from "../ui/button";
import { cn } from "../../lib/utils";
import { useI18n } from "../../hooks/use-i18n";

interface ApprovalEditModalProps {
  open: boolean;
  /** Seed value for the editor; pretty-printed at mount. */
  originalInput: Record<string, unknown> | null;
  submitting?: boolean;
  onSubmit: (modifiedInput: Record<string, unknown>) => void;
  onCancel: () => void;
}

export const ApprovalEditModal = memo(function ApprovalEditModal({
  open,
  originalInput,
  submitting,
  onSubmit,
  onCancel,
}: ApprovalEditModalProps) {
  const { t } = useI18n();
  const seed = useMemo(
    () => JSON.stringify(originalInput ?? {}, null, 2),
    [originalInput],
  );
  const [draft, setDraft] = useState(seed);
  const [error, setError] = useState<string | null>(null);

  // Re-seed every time the modal opens (covers the case where the
  // user opens it for pending A, cancels, opens it for pending B —
  // without re-seeding they'd see A's args).
  useEffect(() => {
    if (open) {
      setDraft(seed);
      setError(null);
    }
  }, [open, seed]);

  const handleSubmit = () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(draft);
    } catch {
      setError(t("conversation.approvalEditInvalidJson"));
      return;
    }
    if (
      parsed === null ||
      typeof parsed !== "object" ||
      Array.isArray(parsed)
    ) {
      setError(t("conversation.approvalEditMustBeObject"));
      return;
    }
    setError(null);
    onSubmit(parsed as Record<string, unknown>);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>{t("conversation.approvalEditTitle")}</DialogTitle>
          <DialogDescription>
            {t("conversation.approvalEditHint")}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Textarea
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              if (error) setError(null);
            }}
            disabled={submitting}
            className={cn(
              "min-h-[220px] font-mono text-[12px] leading-snug",
              error && "border-rose-400 focus-visible:ring-rose-400/30",
            )}
            spellCheck={false}
          />
          {error ? <p className="text-[12px] text-rose-600">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={submitting}>
            {t("conversation.approvalEditCancel")}
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : null}
            {t("conversation.approvalEditSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
});

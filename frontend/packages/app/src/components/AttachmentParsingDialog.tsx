import { useRef } from "react";
import { useTranslation } from "@valuz/core";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@valuz/ui";

export interface AttachmentParsingDialogProps {
  /** Whether the confirm is shown. */
  open: boolean;
  /** "Submit anyway" — proceed with the send despite unfinished parsing. */
  onConfirm: () => void;
  /** Cancel / dismiss — abort the send so the user can wait for parsing. */
  onCancel: () => void;
}

/**
 * Shown when the user submits while one or more attachments are still parsing.
 *
 * Submitting anyway ships only the raw file reference: the agent can read the
 * original file but cannot use the parsed text or document search until parsing
 * finishes. Cancelling lets the user wait for the parse to complete.
 */
export function AttachmentParsingDialog({
  open,
  onConfirm,
  onCancel,
}: AttachmentParsingDialogProps) {
  const { t } = useTranslation();
  // Radix fires ``onOpenChange(false)`` for EVERY close — including the
  // Confirm/Cancel button clicks — so wiring ``onCancel`` straight onto it
  // would double-fire (and fire onCancel on a confirm). The ref lets the
  // single ``onOpenChange`` cancel-path skip the close that a confirm caused,
  // while still treating ESC / overlay / Cancel-button dismissals as cancel.
  const confirmingRef = useRef(false);
  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (next) return;
        if (confirmingRef.current) {
          confirmingRef.current = false;
          return;
        }
        onCancel();
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {t("conversation.attachmentParsingTitle")}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {t("conversation.attachmentParsingBody")}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          {/* No ``onClick`` — the close flows through ``onOpenChange`` → onCancel,
              avoiding a double-fire. */}
          <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => {
              confirmingRef.current = true;
              onConfirm();
            }}
          >
            {t("conversation.attachmentParsingConfirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

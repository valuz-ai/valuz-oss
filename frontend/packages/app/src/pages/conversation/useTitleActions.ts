import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { sessionsApi, useTranslation } from "@valuz/core";

type TitleActionsParams = {
  selectedSessionId: string | null;
};

/**
 * ── Title rename / delete cluster ────────────────────────────────────
 *
 * Owns the header title's Rename + Delete state of the conversation
 * page: the inline-rename swap state, the trigger-width snapshot, the
 * trigger ref, and the delete-confirm flow feeding
 * ``DeleteConfirmDialog`` (whose JSX stays in the page). Bodies are
 * moved verbatim from ``ConversationPage``.
 */
export function useTitleActions({ selectedSessionId }: TitleActionsParams) {
  const { t } = useTranslation();
  const navigate = useNavigate();


  // Title-area Rename + Delete state. Rename swaps the header text for
  // an inline input; Delete opens a confirm dialog. Both are no-ops until
  // a session is loaded — guarded at the click sites.
  const [titleRenaming, setTitleRenaming] = useState(false);
  const [titleRenameValue, setTitleRenameValue] = useState("");
  // Width snapshot of the title trigger captured the moment the user
  // clicks Rename. The input swaps in with this exact width so it
  // doesn't suddenly balloon to the row's max width and push the status
  // pills around.
  const [titleRenameWidth, setTitleRenameWidth] = useState<number | null>(null);
  const titleTriggerRef = useRef<HTMLButtonElement>(null);
  const [titleDeleting, setTitleDeleting] = useState(false);
  const [titleDeleteInFlight, setTitleDeleteInFlight] = useState(false);

  // ``DeleteConfirmDialog``'s onConfirm, moved verbatim from the page's
  // inline closure (the dialog itself stays in the page).
  const handleTitleDeleteConfirm = () => {
    if (!selectedSessionId) return;
    setTitleDeleteInFlight(true);
    sessionsApi
      .delete(selectedSessionId)
      .then(() => {
        toast.success(t("common.deleted" as Parameters<typeof t>[0]));
        setTitleDeleting(false);
        navigate("/conversation/new");
      })
      .catch(() =>
        toast.error(t("common.deleteFailed" as Parameters<typeof t>[0])),
      )
      .finally(() => setTitleDeleteInFlight(false));
  };

  return {
    titleRenaming,
    setTitleRenaming,
    titleRenameValue,
    setTitleRenameValue,
    titleRenameWidth,
    setTitleRenameWidth,
    titleTriggerRef,
    titleDeleting,
    setTitleDeleting,
    titleDeleteInFlight,
    handleTitleDeleteConfirm,
  };
}

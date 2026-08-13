import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, sessionsApi, useTranslation } from "@valuz/core";
import { t as _t } from "@valuz/shared/i18n";

type TitleActionsParams = {
  selectedSessionId: string | null;
};

/** Runtimes whose native fork is wired end-to-end
 *  (docs/design/session-fork.md §6): codex ``thread/fork``, claude_agent
 *  offline transcript fork, deepagents checkpoint-thread copy. */
export const FORKABLE_RUNTIMES = new Set<string>([
  "codex",
  "claude_agent",
  "deepagents",
]);

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

  const [forkInFlight, setForkInFlight] = useState(false);

  // Fork the session — whole-session when ``messageId`` is omitted,
  // through that message (inclusive) otherwise — then jump to the new
  // conversation. Synchronous by design (docs/design/session-fork.md D5):
  // the runtime-native fork runs inside the call (~1–2s), so callers
  // disable their trigger on ``forkInFlight``.
  const handleFork = async (messageId?: string) => {
    if (!selectedSessionId || forkInFlight) return;
    setForkInFlight(true);
    try {
      const forked = await sessionsApi.fork(selectedSessionId, messageId);
      toast.success(t("conversation.forked" as Parameters<typeof t>[0]));
      // The fork is born idle (it carries history) but produces no
      // ``run.finished`` frame — nudge the sidebar's finished-runs window
      // so the new chat shows up immediately.
      window.dispatchEvent(new CustomEvent("valuz-runs-refresh"));
      navigate(`/conversation/${forked.id}`);
    } catch (cause) {
      // Prefer a backend-attached i18n key; else map the two actionable
      // statuses (409 = invalid anchor / turn in flight) to local copy.
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
      setForkInFlight(false);
    }
  };

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
    forkInFlight,
    handleFork,
  };
}

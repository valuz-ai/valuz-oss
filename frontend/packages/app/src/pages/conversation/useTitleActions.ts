import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { sessionsApi, useTranslation } from "@valuz/core";
import { useForkSession } from "../../hooks/use-fork-session";

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
 * Whether this session may be forked at all — the ONE predicate every fork
 * affordance on the conversation page asks.
 *
 * It exists because the conversation page offers fork twice (the header's
 * whole-session item and the per-message "Fork from here"), each had its own
 * copy of the condition, and only one of them got the task rule when it was
 * added — so the header kept offering fork inside a task conversation. Two
 * copies of one rule is how that happens; there is now one.
 *
 * **Task sessions are excluded.** A task's lead and members share ONE
 * task-scoped sandbox, so a deployment that hands a fork's state across
 * scopes cannot serve them, and the forked session comes up with no history
 * at all. The recents row has always applied the same exclusion
 * (`source_kind !== "task"` in ProjectLayoutBase); `task_id` is the same
 * signal, and it rides the list shape, so asking costs no fetch.
 *
 * The conversation page's session is hydrated from `GET /v1/sessions/{id}`,
 * whose mapper had to be fixed to populate `task_id` — it was answering
 * `null` for every session, which is why the per-turn gate looked correct
 * and still rendered the button.
 */
export function canForkSession(
  session: { task_id?: string | null; runtime_provider?: string } | null,
): boolean {
  if (!session || session.task_id) return false;
  return FORKABLE_RUNTIMES.has(session.runtime_provider ?? "");
}

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

  // Fork the session — whole-session when ``messageId`` is omitted,
  // through that message (inclusive) otherwise — then jump to the new
  // conversation. Pending state, duplicate-click suppression, toasts, and
  // navigation all live in the shared hook (#879).
  const { fork, forkInFlight, forkingMessageId } = useForkSession();
  const handleFork = async (messageId?: string) => {
    if (!selectedSessionId) return;
    await fork(selectedSessionId, messageId);
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
    forkingMessageId,
    handleFork,
  };
}

import type { Dispatch, SetStateAction } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  ArrowLeft,
  Bot,
  GitBranch,
  ChevronDown,
  ChevronRight,
  FilePenLine,
  Sparkles,
  Trash2,
} from "lucide-react";
import {
  sessionsApi,
  useTranslation,
  type ProjectDetail,
  type SessionListItem,
} from "@valuz/core";
import type { ConversationTurn } from "@valuz/shared";
import {
  Badge,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  ForkIcon,
  cn,
} from "@valuz/ui";
import { SlotRenderer } from "@valuz/core";
import { FORKABLE_RUNTIMES } from "./useTitleActions";
import { SessionStatusPill } from "./SessionStatusPill";
import type { useComposerConfig } from "./useComposerConfig";
import type { useConversationHistory } from "./useConversationHistory";

type ComposerConfig = ReturnType<typeof useComposerConfig>;

type ConversationHeaderProps = {
  fromTaskId: string | null;
  isSkillCreatorMode: boolean;
  headerTitle: string | null;
  titleRenaming: boolean;
  titleRenameValue: string;
  setTitleRenameValue: Dispatch<SetStateAction<string>>;
  selectedSession: SessionListItem | null;
  selectedSessionId: string | null;
  refreshActiveSession: ReturnType<
    typeof useConversationHistory
  >["refreshActiveSession"];
  setTitleRenaming: Dispatch<SetStateAction<boolean>>;
  titleRenameWidth: number | null;
  setTitleRenameWidth: Dispatch<SetStateAction<number | null>>;
  titleTriggerRef: { current: HTMLButtonElement | null };
  setTitleDeleting: Dispatch<SetStateAction<boolean>>;
  draftSendInFlight: boolean;
  effectiveTurns: ConversationTurn[];
  /** Bring the loaded top of the conversation into view. Lent to the title
   *  slot: an action there can switch the page into a mode whose per-turn
   *  controls start at the first turn (share selection's checkboxes), and the
   *  title is normally clicked from the bottom of a long transcript. */
  scrollToTop: () => void;
  headerAgentSlug: string | null;
  agentNameBySlug: ComposerConfig["agentNameBySlug"];
  activeProject: ProjectDetail | null;
  /** Whole-session fork (docs/design/session-fork.md). The item renders
   *  only for runtimes with a wired native fork (codex today) and is
   *  disabled while a turn is running or a fork is already in flight. */
  onFork: () => void;
  forkInFlight: boolean;
};

/**
 * ── Conversation page header ─────────────────────────────────────────
 *
 * The h-12 header row: back-to-task breadcrumb, Skill-Creator badge,
 * session title (with inline rename + delete menu), status pill, origin
 * badge, and the agent / worktree / project badges. Extracted verbatim
 * from ConversationPage's return JSX — behavior and markup unchanged;
 * every referenced page value arrives as a same-named prop.
 */
export function ConversationHeader({
  fromTaskId,
  isSkillCreatorMode,
  headerTitle,
  titleRenaming,
  titleRenameValue,
  setTitleRenameValue,
  selectedSession,
  selectedSessionId,
  refreshActiveSession,
  setTitleRenaming,
  titleRenameWidth,
  setTitleRenameWidth,
  titleTriggerRef,
  setTitleDeleting,
  draftSendInFlight,
  effectiveTurns,
  scrollToTop,
  headerAgentSlug,
  agentNameBySlug,
  activeProject,
  onFork,
  forkInFlight,
}: ConversationHeaderProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  return (
    <>
      {/* Page header — rendered inline so the scroll container below can run
            edge-to-edge of the main card and its scrollbar sits flush against
            the bordered card edge. Layout-level header is hidden via
            setHideHeader(true). */}
      <header className="flex h-12 shrink-0 items-center px-5">
        <div className="flex w-full items-center justify-between">
          <div className="flex min-w-0 items-center gap-2">
            {fromTaskId ? (
              <>
                <button
                  type="button"
                  onClick={() =>
                    navigate(`/tasks/${encodeURIComponent(fromTaskId)}`)
                  }
                  className="inline-flex shrink-0 items-center gap-1 text-[13px] text-ink-meta transition-colors hover:text-ink-heading"
                >
                  <ArrowLeft className="h-3.5 w-3.5" />
                  <span>
                    {t("conversation.backToTask" as Parameters<typeof t>[0])}
                  </span>
                </button>
                <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
              </>
            ) : null}
            {isSkillCreatorMode ? (
              <Badge variant="metaBrand" className="shrink-0">
                <Sparkles className="h-3 w-3" />
                Skill Creator
              </Badge>
            ) : null}
            {headerTitle ? (
              titleRenaming ? (
                // Inline rename. Confirm on Enter / blur, cancel on Esc.
                // No menu accessible while editing so the click target
                // stays clean. ``autoFocus`` + ``select()`` via the ref
                // would race with the dropdown's close-auto-focus on
                // some browsers — we open rename via menu select, which
                // already does ``e.preventDefault()`` on close, so a
                // plain ``autoFocus`` is enough.
                <input
                  autoFocus
                  value={titleRenameValue}
                  onChange={(e) => setTitleRenameValue(e.target.value)}
                  onBlur={() => {
                    const trimmed = titleRenameValue.trim();
                    if (trimmed && trimmed !== selectedSession?.name) {
                      sessionsApi
                        .rename(selectedSessionId!, trimmed)
                        .then(() => {
                          toast.success(
                            t("sidebar.renamed" as Parameters<typeof t>[0]),
                          );
                          void refreshActiveSession(selectedSessionId);
                        })
                        .catch(() =>
                          toast.error(
                            t(
                              "sidebar.renameFailed" as Parameters<typeof t>[0],
                            ),
                          ),
                        );
                    }
                    setTitleRenaming(false);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      (e.target as HTMLInputElement).blur();
                    } else if (e.key === "Escape") {
                      e.preventDefault();
                      setTitleRenaming(false);
                    }
                  }}
                  onFocus={(e) => e.currentTarget.select()}
                  style={
                    titleRenameWidth !== null
                      ? { width: titleRenameWidth }
                      : undefined
                  }
                  className="min-w-0 border-0 border-b border-brand bg-transparent px-1 text-sm font-medium text-ink-heading outline-none"
                />
              ) : (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      ref={titleTriggerRef}
                      disabled={!selectedSessionId}
                      className="flex min-w-0 items-center gap-1 rounded-md px-1 py-0.5 text-sm font-medium text-ink-heading transition-colors hover:bg-surface-soft focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand/30 disabled:cursor-default disabled:hover:bg-transparent"
                    >
                      <span className="truncate">{headerTitle}</span>
                      <ChevronDown
                        className="h-3.5 w-3.5 shrink-0 text-ink-muted"
                        strokeWidth={2}
                        aria-hidden
                      />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    // Anchor under the chevron (right end of the
                    // trigger), not the title's left edge — otherwise
                    // the menu drops down far away from where the user
                    // clicked.
                    align="end"
                    className="min-w-[160px]"
                    onCloseAutoFocus={(e) => e.preventDefault()}
                  >
                    <DropdownMenuItem
                      onSelect={() => {
                        // Snapshot the trigger's current width so the
                        // input occupies the same horizontal space the
                        // title button just had — otherwise the input
                        // expands to the row's max width and shoves the
                        // sibling status pills around.
                        const w =
                          titleTriggerRef.current?.getBoundingClientRect()
                            .width ?? null;
                        setTitleRenameWidth(w);
                        setTitleRenameValue(
                          selectedSession?.name ?? headerTitle,
                        );
                        setTitleRenaming(true);
                      }}
                    >
                      <FilePenLine />
                      {t("sidebar.rename" as Parameters<typeof t>[0])}
                    </DropdownMenuItem>
                    {FORKABLE_RUNTIMES.has(
                      selectedSession?.runtime_provider ?? "",
                    ) && (
                      <DropdownMenuItem
                        disabled={
                          forkInFlight ||
                          selectedSession?.status === "running"
                        }
                        onSelect={() => onFork()}
                      >
                        <ForkIcon />
                        {t("conversation.fork" as Parameters<typeof t>[0])}
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      variant="destructive"
                      onSelect={() => setTitleDeleting(true)}
                    >
                      <Trash2 />
                      {t("common.delete" as Parameters<typeof t>[0])}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              )
            ) : null}
            <SessionStatusPill
              // ``created`` is exactly the state a not-yet-minted session is
              // in: accepted, not running. It is the same pill the promoted
              // page shows a beat later, so nothing changes on handover.
              status={
                selectedSession?.status ??
                (draftSendInFlight ? "created" : undefined)
              }
              cancelled={
                effectiveTurns[effectiveTurns.length - 1]?.cancelled === true
              }
              pending={effectiveTurns.length === 0}
              // Server-side flag, the SAME one the sidebar pulse and the
              // Activity page read (``bg_busy_session_ids()``), so one
              // session can't read "background running" in one surface and
              // idle in another. The strip below the transcript keeps
              // deriving from events — it renders the task LIST, which the
              // flag doesn't carry.
              background={selectedSession?.background === true}
            />
            {/* No execution-origin badge here. The context bar attached to
                the composer already names the location for this conversation,
                and it is the surface that OWNS the choice — repeating it by
                the title made the same fact compete with the session's own
                identity. Origin badges stay where the reader is scanning many
                rows (Activity, Projects, Conversations). */}
            {headerAgentSlug ? (
              <Badge variant="metaBrand" className="shrink-0">
                <Bot className="h-3 w-3" />
                {agentNameBySlug.get(headerAgentSlug) ?? headerAgentSlug}
              </Badge>
            ) : null}
            {selectedSession?.forked_from_session_id ? (
              // Fork provenance chip (design doc D6): jump back to the
              // source conversation. The source may since have been
              // deleted — the target page's own not-found handling covers
              // that; the chip is a hint, not a guarantee.
              <button
                type="button"
                onClick={() =>
                  navigate(
                    `/conversation/${selectedSession.forked_from_session_id}`,
                  )
                }
                title={t("conversation.forkedFrom" as Parameters<typeof t>[0])}
                className="shrink-0"
              >
                <Badge
                  variant="metaOutline"
                  className="cursor-pointer transition-colors hover:bg-surface-soft"
                >
                  <ForkIcon className="h-3 w-3" />
                  {t("conversation.forkedFrom" as Parameters<typeof t>[0])}
                </Badge>
              </button>
            ) : null}
            {selectedSession?.worktree ? (
              // Worktree attribution (creation-time snapshot). Greys out
              // when the worktree no longer exists on disk — the next
              // send self-heals by recreating it, which the tooltip says.
              <Badge
                variant="metaOutline"
                className={cn(
                  "shrink-0",
                  selectedSession.worktree.exists === false && "opacity-60",
                )}
                title={
                  selectedSession.worktree.exists === false
                    ? t("conversation.worktreeBadgeGone")
                    : t("conversation.worktreeBadgeHint")
                }
              >
                <GitBranch className="h-3 w-3" />
                {selectedSession.worktree.branch ??
                  selectedSession.worktree.name}
              </Badge>
            ) : null}
            {activeProject?.name && !isSkillCreatorMode ? (
              <Badge variant="metaOutline" className="shrink-0">
                {activeProject.name}
              </Badge>
            ) : null}
          </div>
          {/* Host actions for THIS session (share, export…) — the row's right
              end, opposite the identity cluster, rather than wedged between
              the title and its status. Kept in this header rather than the app
              top bar: that bar is shared by every page, so a session-scoped
              control there would have to keep proving which session it means.
              Rendered through the slot registry — this package depends on
              ``@valuz/core``, unlike ``@valuz/ui`` where the same need is
              served by a ReactNode prop. */}
          <div className="flex shrink-0 items-center gap-2 pl-2">
            <SlotRenderer
              name="conversation.title.actions"
              context={{
                sessionId: selectedSessionId,
                session: selectedSession,
                scrollToTop,
                // The WHOLE loaded transcript, not what is on screen. An action
                // here can act on the conversation as a unit (share it, export
                // it), and the only per-turn slot is rendered inside the
                // virtualised list — so an overlay that collected turns from
                // there saw the few rows the virtualizer had mounted and
                // silently acted on those alone.
                turns: effectiveTurns,
              }}
            />
          </div>
        </div>
      </header>
    </>
  );
}

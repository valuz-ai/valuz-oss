import { useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { useNavigate } from "react-router-dom";
import { CheckCircle2, Loader2, Settings } from "lucide-react";
import { sessionsApi, useTranslation } from "@valuz/core";
import type { ConversationTurn } from "@valuz/shared";
import {
  Button,
  ConversationIndexRail,
  ConversationTurnList,
  EmptyState,
  ForkIcon,
} from "@valuz/ui";
import type { usePlatform } from "@valuz/app/platform";
import type { useCitationDocumentPreview } from "../../components/CitationDocumentPreviewProvider";
import { shouldShowNoModelEmptyState } from "../conversation-loading";
import { SelectionActionsOverlay } from "./SelectionActionsOverlay";
import { NEW_SESSION_ID } from "./session-events";
import type { useArtifactPane } from "./useArtifactPane";
import type { useComposerConfig } from "./useComposerConfig";
import type { useConversationHistory } from "./useConversationHistory";
import type { useConversationScroll } from "./useConversationScroll";
import type { useConversationSend } from "./useConversationSend";
import type { useToolCallCards } from "./useToolCallCards";
import { SlotRenderer } from "@valuz/core";

type ComposerConfig = ReturnType<typeof useComposerConfig>;
type ConversationHistory = ReturnType<typeof useConversationHistory>;
type ConversationScroll = ReturnType<typeof useConversationScroll>;
type ConversationSend = ReturnType<typeof useConversationSend>;
type ToolCallCards = ReturnType<typeof useToolCallCards>;

type ConversationBodyProps = {
  /** Route param (``/conversation/{id}``), defaulted to ``NEW_SESSION_ID``. */
  id: string;
  loading: boolean;
  providers: ComposerConfig["providers"];
  providerChannelState: ComposerConfig["providerChannelState"];
  scrollContainerRef: { current: HTMLDivElement | null };
  hasMoreOlder: boolean;
  loadingOlder: boolean;
  topSentinelRef: { current: HTMLButtonElement | null };
  userScrolledRef: { current: boolean };
  loadOlderTurns: ConversationHistory["loadOlderTurns"];
  conversationInstanceKey: string;
  effectiveTurns: ConversationTurn[];
  displayBusy: boolean;
  postRunVerificationActive: boolean;
  error: string | null;
  handleRetry: ConversationSend["handleRetry"];
  retryCounts: Record<string, number>;
  containerHeight: ConversationScroll["containerHeight"];
  skillsBySlug: ComposerConfig["skillsBySlug"];
  handleTurnListVirtualApiReady: ConversationScroll["handleTurnListVirtualApiReady"];
  scrollToTurnIndex: ConversationScroll["scrollToTurnIndex"];
  renderToolCall: ToolCallCards["renderToolCall"];
  isToolCardFoldable: ToolCallCards["isToolCardFoldable"];
  revealInFinder: ReturnType<typeof usePlatform>["revealInFinder"];
  localFileLinks: ReturnType<typeof useArtifactPane>["localFileLinks"];
  selectedSessionId: string | null;
  openCitation: ReturnType<typeof useCitationDocumentPreview>["openCitation"];
  setDraft: Dispatch<SetStateAction<string>>;
  hasPendingProjectSend: boolean;
  startingRuntime: ComposerConfig["startingRuntime"];
  /** Embedding-host override for the new-chat welcome: custom title,
   *  custom suggestion list (replaces the three generic new-chat
   *  suggestions), and mascot suppression. Absent → system defaults. */
  emptyStateOverride?: {
    title?: string;
    suggestions?: string[];
    hideMascot?: boolean;
  };
  /** Message-granularity fork (docs/design/session-fork.md): fork the
   *  session through the hovered turn, inclusive. Rendered only when the
   *  session's runtime has a wired native fork (codex today); a stale
   *  turn without a stored anchor is caught server-side (409 → toast).
   *  Absent (embedded hosts) → no fork affordance. */
  canForkFromTurn?: boolean;
  forkInFlight?: boolean;
  /** Anchor message of an in-flight message-granularity fork — that turn's
   * hover button swaps to a spinner while the request runs (#879). */
  forkingMessageId?: string | null;
  onForkFromTurn?: (messageId: string) => void;
  /** Index of the turn at the viewport top, from ``useConversationScroll``.
   *  Its presence is also what opts a host into the message index rail —
   *  the embedded ``variant="panel"`` conversation omits it (a 345px
   *  workbench panel has no gutter to put a rail in). */
  activeTurnIndex?: number;
  /** Session working mode — gates the plan-proposal approve button
   *  (rendered only while the session is still in plan mode). */
  selectedSessionMode?: "default" | "plan" | "goal";
  /** Optimistic mode update after the approve PATCH lands. */
  setSelectedSessionMode?: Dispatch<
    SetStateAction<"default" | "plan" | "goal">
  >;
  /** Send path for the auto "start executing" turn after plan approval. */
  performSend?: (overrideText?: string) => Promise<void>;
};

/**
 * ── Conversation body ────────────────────────────────────────────────
 *
 * The main region between the header and the approval tray: either the
 * no-model empty state or the transcript scroll container (load-older
 * sentinel + ``ConversationTurnList``). Extracted verbatim from
 * ConversationPage's return JSX — behavior and markup unchanged; every
 * referenced page value arrives as a same-named prop.
 */
export function ConversationBody({
  id,
  loading,
  providers,
  providerChannelState,
  scrollContainerRef,
  hasMoreOlder,
  loadingOlder,
  topSentinelRef,
  userScrolledRef,
  loadOlderTurns,
  conversationInstanceKey,
  effectiveTurns,
  displayBusy,
  postRunVerificationActive,
  error,
  handleRetry,
  retryCounts,
  containerHeight,
  skillsBySlug,
  handleTurnListVirtualApiReady,
  scrollToTurnIndex,
  renderToolCall,
  isToolCardFoldable,
  revealInFinder,
  localFileLinks,
  selectedSessionId,
  openCitation,
  setDraft,
  hasPendingProjectSend,
  startingRuntime,
  emptyStateOverride,
  canForkFromTurn,
  forkInFlight,
  forkingMessageId,
  onForkFromTurn,
  activeTurnIndex,
  selectedSessionMode,
  setSelectedSessionMode,
  performSend,
}: ConversationBodyProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  // Plan-proposal approval (codex plan mode — docs/design/session-modes.md
  // §codex). Approval is CLIENT-driven: no runtime round-trip exists, so
  // "批准并开始执行" = PATCH the session mode back to default, then start
  // an execution turn. The button renders only on the newest proposal
  // while the session is still in plan mode; older proposals (and every
  // proposal after approval) are plain records.
  const [planApproving, setPlanApproving] = useState(false);
  const lastPlanTurnId = useMemo(() => {
    for (let i = effectiveTurns.length - 1; i >= 0; i -= 1) {
      const turn = effectiveTurns[i]!;
      if (turn.blocks.some((b) => b.kind === "plan_proposal")) return turn.id;
    }
    return null;
  }, [effectiveTurns]);
  const planActionable =
    lastPlanTurnId !== null &&
    selectedSessionMode === "plan" &&
    !displayBusy &&
    selectedSessionId !== null &&
    performSend !== undefined;
  const handleApprovePlan = async () => {
    if (!planActionable || !selectedSessionId || planApproving) return;
    setPlanApproving(true);
    try {
      await sessionsApi.updateMode(selectedSessionId, "default");
      setSelectedSessionMode?.("default");
      await performSend?.(
        t("conversation.planApprovedRun" as Parameters<typeof t>[0]),
      );
    } catch {
      /* non-fatal — surfaced by the error toast pipeline */
    } finally {
      setPlanApproving(false);
    }
  };

  return (
    <>
      {shouldShowNoModelEmptyState({
        isNewConversation: id === NEW_SESSION_ID,
        pageLoading: loading,
        providerCount: providers.length,
        providerStatus: providerChannelState.status,
      }) ? (
        <div className="flex flex-1 items-center justify-center p-8">
          <EmptyState
            icon={<Settings />}
            title={t("conversation.noModel" as Parameters<typeof t>[0])}
            message={t("conversation.noModelHint" as Parameters<typeof t>[0])}
            action={
              <Button
                type="button"
                size="sm"
                variant="default"
                onClick={() => navigate("/settings")}
              >
                {t("conversation.goToSettings" as Parameters<typeof t>[0])}
              </Button>
            }
          />
        </div>
      ) : (
        <>
          {/* Positioning context for the index rail. Deliberately OUTSIDE
              the scroll container: the rail must stay put while the
              transcript scrolls, and it must not extend over the header
              or the composer. Also keeps the scroll container's
              first-child chain (walked by ``useConversationScroll``'s
              ResizeObserver) untouched. */}
          <div className="relative flex min-h-0 flex-1 flex-col">
            {activeTurnIndex !== undefined ? (
              <ConversationIndexRail
                turns={effectiveTurns}
                activeIndex={activeTurnIndex}
                onSelect={scrollToTurnIndex}
              />
            ) : null}
            {/* Floating actions for assistant-text selections — renders only
                when an overlay registered ``conversation.selection-actions``. */}
            <SelectionActionsOverlay
              sessionId={selectedSessionId}
              containerRef={scrollContainerRef}
              // Appends below any text already staged, so acting on a
              // selection never clobbers a draft in progress.
              insertDraft={(text) =>
                setDraft((previous) =>
                  previous.trim() ? `${previous.trimEnd()}\n\n${text}` : text,
                )
              }
            />
            <div
              ref={scrollContainerRef}
              className="min-h-0 flex-1 overflow-y-auto bg-surface pt-0 pb-7"
            >
              {/* Top sentinel — visible "Load older" pill that doubles
                  as the IntersectionObserver target. Two ways to fetch the
                  next page:
                    1. Click the pill (explicit affordance — what users
                       expect when they realise there's earlier history)
                    2. Scroll to the top with the wheel/trackpad — the
                       IntersectionObserver fires once the pill enters the
                       viewport (``rootMargin`` 200 px so the fetch starts
                       a hair before the user hits the literal top)
                  The pill text swaps to a loader during the in-flight
                  fetch. Whole element disappears when ``hasMoreOlder``
                  flips false so the observer stops firing past the
                  start of history. */}
              {hasMoreOlder || loadingOlder ? (
                <div className="flex justify-center py-2">
                  <button
                    ref={topSentinelRef}
                    type="button"
                    onClick={() => {
                      // Manual click bypasses the "user must scroll first"
                      // gate the observer needs — the click itself IS the
                      // user signal.
                      userScrolledRef.current = true;
                      void loadOlderTurns();
                    }}
                    disabled={loadingOlder}
                    className="rounded-full border border-surface-border bg-surface px-3 py-1 text-2xs text-ink-body shadow-xs transition-colors hover:bg-surface-soft disabled:cursor-default disabled:opacity-60"
                  >
                    {loadingOlder
                      ? `${t("conversation.loadOlder" as Parameters<typeof t>[0])}…`
                      : `↑ ${t("conversation.loadOlder" as Parameters<typeof t>[0])}`}
                  </button>
                </div>
              ) : null}
              <ConversationTurnList
                // Non-latest rows are memoized on their ``turn`` object — this
                // key folds the fork pending state into the comparator so their
                // action row re-renders (spinner/disabled) while a fork runs
                // (#879). "session" covers a whole-session (header) fork.
                turnActionsKey={
                  [
                    forkInFlight ? (forkingMessageId ?? "session") : "",
                    // Plan-approval state lives outside the turn object —
                    // fold it in so memoized rows re-render when the
                    // button appears/disables (same contract as the fork
                    // spinner above).
                    planActionable
                      ? `plan:${lastPlanTurnId}:${planApproving}`
                      : "",
                  ]
                    .filter(Boolean)
                    .join("|") || null
                }
                // Completes the slot added in #744: the prop existed but nothing
                // passed it, so the slot was unreachable. Overlays register
                // under ``conversation.turn.actions``.
                renderTurnActions={(turn) => (
                  <>
                    {canForkFromTurn &&
                    onForkFromTurn &&
                    !turn.cancelled &&
                    !turn.interrupted &&
                    turn.forkAnchor !== false &&
                    // Fork addresses a Message, so it needs one of this
                    // turn's own. A turn that failed before producing
                    // anything (pre-flight: sandbox, credentials) never
                    // got one — whatever ``messageId`` it carries was
                    // borrowed from the neighbouring turn by the recovery
                    // write, so forking "from here" would fork from there.
                    turn.messageId &&
                    !(turn.failedMessage && turn.blocks.length === 0) ? (
                      <button
                        type="button"
                        disabled={forkInFlight}
                        onClick={() => onForkFromTurn(turn.messageId!)}
                        title={t(
                          "conversation.forkFromHere" as Parameters<
                            typeof t
                          >[0],
                        )}
                        className="flex h-7 w-7 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-muted disabled:cursor-default disabled:opacity-60"
                      >
                        {forkInFlight && forkingMessageId === turn.messageId ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <ForkIcon className="h-3.5 w-3.5" />
                        )}
                      </button>
                    ) : null}
                    <SlotRenderer
                      name="conversation.turn.actions"
                      context={{
                        turn,
                        // An action here may switch the page into a mode whose
                        // per-turn control sits at the TOP of this turn (share
                        // selection's checkbox lives beside the user message).
                        // Clicked from the reply footer that control is off-screen
                        // above, so the host lends the overlay the same scroll it
                        // uses for a new turn.
                        scrollToTurn: () =>
                          scrollToTurnIndex(
                            effectiveTurns.findIndex((x) => x.id === turn.id),
                          ),
                      }}
                    />
                  </>
                )}
                renderTurnLeading={(turn, role) => (
                  <SlotRenderer
                    name="conversation.turn.leading"
                    context={{ turn, role }}
                  />
                )}
                renderPlanActions={(turn) =>
                  planActionable && turn.id === lastPlanTurnId ? (
                    <Button
                      size="sm"
                      onClick={() => void handleApprovePlan()}
                      disabled={planApproving}
                      className="bg-brand text-white hover:bg-brand-hover"
                    >
                      {planApproving ? (
                        <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                      ) : (
                        <CheckCircle2 className="mr-1.5 h-3 w-3" />
                      )}
                      {t(
                        "conversation.planApproveAndRun" as Parameters<
                          typeof t
                        >[0],
                      )}
                    </Button>
                  ) : null
                }
                // Remount on true session switches so the virtualizer's
                // internal state starts fresh. The /conversation/new → real-id
                // promotion keeps this key stable so the first sent turn
                // doesn't look like a page refresh.
                key={conversationInstanceKey}
                turns={effectiveTurns}
                scrollContainerRef={scrollContainerRef}
                sending={displayBusy}
                postRunVerificationActive={postRunVerificationActive}
                loading={id === NEW_SESSION_ID ? false : loading}
                error={error}
                onRetry={handleRetry}
                retryCounts={retryCounts}
                lastTurnMinHeight={
                  effectiveTurns.length > 1 ? containerHeight : 0
                }
                skillsBySlug={skillsBySlug}
                onVirtualApiReady={handleTurnListVirtualApiReady}
                renderToolCall={renderToolCall}
                isToolCardFoldable={isToolCardFoldable}
                onRevealFile={revealInFinder}
                isLocalFileHref={localFileLinks.isLocalFileHref}
                onLocalFileLinkClick={localFileLinks.openLocalFileHref}
                onCitationClick={({ messageId, citationId }) => {
                  if (!selectedSessionId || !messageId) return;
                  openCitation({
                    sessionId: selectedSessionId,
                    messageId,
                    citationId,
                  });
                }}
                emptyTitle={emptyStateOverride?.title}
                emptySuggestions={
                  emptyStateOverride?.suggestions ?? [
                    t(
                      "conversation.newChatSuggestion1" as Parameters<
                        typeof t
                      >[0],
                    ),
                    t(
                      "conversation.newChatSuggestion2" as Parameters<
                        typeof t
                      >[0],
                    ),
                    t(
                      "conversation.newChatSuggestion3" as Parameters<
                        typeof t
                      >[0],
                    ),
                  ]
                }
                onEmptySuggestionClick={(text) => setDraft(text)}
                hideEmptyMascot={emptyStateOverride?.hideMascot}
                // Only a genuinely new chat (URL is /conversation/new) shows the
                // welcome. An existing conversation keyed by id has no turns yet
                // while its transcript loads — gate on the URL, not the transient
                // ``selectedSessionId`` (which briefly nulls mid-navigation), so
                // the mascot + suggestions don't flash before history lands.
                // …and not while a project-detail send is still landing: that
                // arrives at /conversation/new with no turns yet and waits for
                // bootstrap to bind the project before it can fire, so the
                // mascot + suggestions would flash in the gap — on a page the
                // user reached by SENDING something, which reads as the message
                // having been dropped.
                showWelcome={id === NEW_SESSION_ID && !hasPendingProjectSend}
                startingRuntime={startingRuntime}
              />
            </div>
          </div>
        </>
      )}
    </>
  );
}

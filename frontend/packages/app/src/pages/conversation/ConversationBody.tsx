import type { Dispatch, SetStateAction } from "react";
import { useNavigate } from "react-router-dom";
import { Settings } from "lucide-react";
import { useTranslation } from "@valuz/core";
import type { ConversationTurn } from "@valuz/shared";
import { Button, ConversationTurnList, EmptyState } from "@valuz/ui";
import type { usePlatform } from "@valuz/app/platform";
import type { useCitationDocumentPreview } from "../../components/CitationDocumentPreviewProvider";
import { shouldShowNoModelEmptyState } from "../conversation-loading";
import { NEW_SESSION_ID } from "./session-events";
import type { useArtifactPane } from "./useArtifactPane";
import type { useComposerConfig } from "./useComposerConfig";
import type { useConversationHistory } from "./useConversationHistory";
import type { useConversationScroll } from "./useConversationScroll";
import type { useConversationSend } from "./useConversationSend";
import type { useToolCallCards } from "./useToolCallCards";

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
  error: string | null;
  handleRetry: ConversationSend["handleRetry"];
  handleSwitchModel: (turnId: string) => void;
  retryCounts: Record<string, number>;
  containerHeight: ConversationScroll["containerHeight"];
  skillsBySlug: ComposerConfig["skillsBySlug"];
  handleTurnListVirtualApiReady: ConversationScroll["handleTurnListVirtualApiReady"];
  renderToolCall: ToolCallCards["renderToolCall"];
  isToolCardFoldable: ToolCallCards["isToolCardFoldable"];
  revealInFinder: ReturnType<typeof usePlatform>["revealInFinder"];
  localFileLinks: ReturnType<typeof useArtifactPane>["localFileLinks"];
  selectedSessionId: string | null;
  openCitation: ReturnType<typeof useCitationDocumentPreview>["openCitation"];
  setDraft: Dispatch<SetStateAction<string>>;
  hasPendingProjectSend: boolean;
  startingRuntime: ComposerConfig["startingRuntime"];
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
  error,
  handleRetry,
  handleSwitchModel,
  retryCounts,
  containerHeight,
  skillsBySlug,
  handleTurnListVirtualApiReady,
  renderToolCall,
  isToolCardFoldable,
  revealInFinder,
  localFileLinks,
  selectedSessionId,
  openCitation,
  setDraft,
  hasPendingProjectSend,
  startingRuntime,
}: ConversationBodyProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

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
              // Remount on true session switches so the virtualizer's
              // internal state starts fresh. The /conversation/new → real-id
              // promotion keeps this key stable so the first sent turn
              // doesn't look like a page refresh.
              key={conversationInstanceKey}
              turns={effectiveTurns}
              scrollContainerRef={scrollContainerRef}
              sending={displayBusy}
              loading={id === NEW_SESSION_ID ? false : loading}
              error={error}
              onRetry={handleRetry}
              onSwitchModel={handleSwitchModel}
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
              emptySuggestions={[
                t("conversation.newChatSuggestion1" as Parameters<typeof t>[0]),
                t("conversation.newChatSuggestion2" as Parameters<typeof t>[0]),
                t("conversation.newChatSuggestion3" as Parameters<typeof t>[0]),
              ]}
              onEmptySuggestionClick={(text) => setDraft(text)}
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
        </>
      )}
    </>
  );
}

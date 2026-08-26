import { useSurfaceSuppressed, type SessionMessageHostRef } from "@valuz/core";
import { DeleteConfirmDialog, BackgroundTaskStrip } from "@valuz/ui";
import { useProjectOutlet } from "@valuz/app/layout";
import { ArtifactSplitPane } from "../../components/ArtifactSplitPane";
import { useProjectHandoff } from "./useProjectHandoff";
import { canForkSession, useTitleActions } from "./useTitleActions";
import { useContextPanel } from "./useContextPanel";
import {
  useConversationOrchestration,
  type ConversationOrchestrationParams,
} from "./useConversationOrchestration";
import {
  useConversationRouting,
  type ConversationViewVariant,
} from "./useConversationRouting";
import { ApprovalTray } from "./ApprovalTray";
import { ConversationBody } from "./ConversationBody";
import { ConversationHeader } from "./ConversationHeader";
import { ComposerPane } from "./ComposerPane";
import { KbPickerOverlay } from "./KbPickerOverlay";

export interface ConversationViewProps {
  /** Controlled session id. Omit (or ``NEW_SESSION_ID``) for a fresh draft. */
  sessionId?: string;
  /** Client-declared host location — threaded onto ``sendMessage``'s
   *  ``host_ref`` (direct-send path only; the queue-drain path doesn't carry
   *  it yet). */
  hostRef?: SessionMessageHostRef | null;
  /** Seeds for a session that doesn't exist yet. ``page`` variant ignores
   *  this (its own agent/project pickers own that choice). */
  createDefaults?: { agentSlug?: string; projectId?: string };
  /** Fired the moment a ``panel``-variant draft is minted into a real
   *  session, so the embedding host can persist the id. Unused by ``page``
   *  (the URL is the persistence). */
  onSessionCreated?: (sessionId: string) => void;
  /** Embedded hosts may persist a session id beyond the lifetime of the
   *  backend/database it came from. When that id is confirmed missing (404),
   *  the panel asks its host to discard the id and falls back to a fresh
   *  draft. Page routes intentionally keep their explicit not-found state. */
  onSessionUnavailable?: (sessionId: string) => void;
  /** ``panel``-variant "starter" affordance — fills the composer draft with
   *  this text once, then calls ``onPrefillConsumed``. */
  prefillDraft?: string | null;
  onPrefillConsumed?: () => void;
  /** Embedding-host override for the new-chat welcome (title, suggestion
   *  list, mascot suppression). Absent → the system defaults, so the
   *  conversation ROUTE is unchanged. Suggestion clicks fill the composer
   *  draft (same behavior as the system suggestions). */
  emptyState?: { title?: string; suggestions?: string[]; hideMascot?: boolean };
  /** ``page`` (default) — the full conversation route, with header, artifact
   *  split pane, and project-handoff/context-panel chrome. ``panel`` — the
   *  bare conversation (message stream + composer) for embedding into a
   *  fixed-width host surface (e.g. a 345px edition workbench panel); no
   *  chrome hooks (``useProjectOutlet`` / ``useContextPanel`` /
   *  ``useProjectHandoff`` / ``useTitleActions``) run in this variant. */
  variant?: ConversationViewVariant;
  /** Enables the composer's chat/task mode toggle (the project-home
   *  composer's). In task mode Send hands the draft to this callback —
   *  typically a ``tasksApi.kickoff`` — instead of the conversation send;
   *  return ``true`` to clear the draft. Absent → chat-only, unchanged. */
  onSendTask?: (goal: string) => Promise<boolean> | boolean;
}

/**
 * ── Embeddable conversation view ─────────────────────────────────────
 *
 * The full conversation experience (message stream, thinking, tool-call
 * cards, streaming, retry, Composer) factored out of ``ConversationPage`` so
 * it can be reused verbatim by both the conversation ROUTE and a narrow
 * embedded panel. ``ConversationPage`` is now a thin shell: read the route
 * param, render ``<ConversationView variant="page" sessionId={id} />``.
 *
 * This component itself calls no hooks — it just picks which renderer
 * mounts, so ``variant`` never triggers a conditional-hooks violation (each
 * renderer below is a distinct component instance; switching ``variant`` on
 * a live view — which no caller does — would simply unmount one and mount
 * the other, which is ordinary React, not a hook-order violation).
 */
export function ConversationView(props: ConversationViewProps) {
  const variant = props.variant ?? "page";
  if (variant === "panel") return <ConversationViewPanel {...props} />;
  return <ConversationViewPage {...props} />;
}

function useOrchestration(
  props: ConversationViewProps,
  variant: ConversationViewVariant,
  directoryFieldMode: ConversationOrchestrationParams["directoryFieldMode"],
) {
  const {
    id,
    conversationInstanceKey,
    promotingSessionIdRef,
    onSessionPromoted,
  } = useConversationRouting({
    sessionId: props.sessionId,
    variant,
    onSessionCreated: props.onSessionCreated,
  });
  const core = useConversationOrchestration({
    variant,
    id,
    conversationInstanceKey,
    promotingSessionIdRef,
    onSessionPromoted,
    onSessionUnavailable: props.onSessionUnavailable,
    directoryFieldMode,
    hostRef: props.hostRef,
    createDefaults: props.createDefaults,
    prefillDraft: props.prefillDraft,
    onPrefillConsumed: props.onPrefillConsumed,
  });
  return core;
}

/** ``variant="page"`` — the conversation ROUTE. Adds the chrome
 *  (``useProjectOutlet`` / header / artifact split pane / project handoff /
 *  title rename+delete / context panel) around the shared orchestration. */
function ConversationViewPage(props: ConversationViewProps) {
  const { directoryFieldMode, setRightPanel, setHeader, setHideHeader } =
    useProjectOutlet();
  const composerSuppressed = useSurfaceSuppressed("conversation.composer");
  const core = useOrchestration(props, "page", directoryFieldMode);

  const { handleSend, hasPendingProjectSend } = useProjectHandoff({
    id: core.id,
    location: core.location,
    searchParams: core.searchParams,
    selectedProjectId: core.selectedProjectId,
    draft: core.draft,
    historyCursorRef: core.historyCursorRef,
    projectSendHandoffRef: core.projectSendHandoffRef,
    handoffSessionIdRef: core.handoffSessionIdRef,
    draftBootstrapSettled: core.draftBootstrapSettled,
    setPendingUserMessage: core.setPendingUserMessage,
    setTurnStartAnchor: core.setTurnStartAnchor,
    setSending: core.setSending,
    restageAttachments: core.restageAttachments,
    adoptAttachments: core.adoptAttachments,
    getDisplayBusy: () => core.displayBusy,
    performEnqueue: core.performEnqueue,
    performSend: core.performSend,
  });

  const {
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
  } = useTitleActions({ selectedSessionId: core.selectedSessionId });

  // Context panel — the JSX-producing ``contextPanelNode`` memo and the
  // layout-slot mount effect, moved into the hook with verbatim body and
  // dependency arrays (memoization semantics untouched).
  useContextPanel({
    id: core.id,
    isSkillCreatorMode: core.isSkillCreatorMode,
    stagingSlugs: core.stagingSlugs,
    stagingRefreshing: core.stagingRefreshing,
    stagingSyncing: core.stagingSyncing,
    refreshStaging: core.refreshStaging,
    handleSyncStaging: core.handleSyncStaging,
    activeProject: core.activeProject,
    activeProjectRootPath: core.activeProjectRootPath,
    activeWorktree: core.activeWorktree,
    selectedProjectId: core.selectedProjectId,
    selectedSession: core.selectedSession,
    selectedComposerSkill: core.selectedComposerSkill,
    availableSkills: core.availableSkills,
    sessionAttachments: core.sessionAttachments,
    sessionArtifacts: core.sessionArtifacts,
    fileTree: core.fileTree,
    projectKbTree: core.projectKbTree,
    projectKbBindings: core.projectKbBindings,
    handleExpandProjectKbFolder: core.handleExpandProjectKbFolder,
    handleLocalFilesAttach: core.handleLocalFilesAttach,
    handleRemoveSessionAttachment: core.handleRemoveSessionAttachment,
    openArtifactFile: core.openArtifactFile,
    refreshFileTree: core.refreshFileTree,
    panelCollapsed: core.panelCollapsed,
    panelSetCollapsed: core.panelSetCollapsed,
    todos: core.todos,
    setRightPanel,
    setHeader,
    setHideHeader,
  });

  return (
    <>
      {/* The pane is always mounted with the conversation as its first column,
          so opening or closing a document never remounts the message list. */}
      <ArtifactSplitPane
        file={core.artifactFile}
        onReload={core.handleArtifactReload}
        onClose={core.handleArtifactClose}
        onCopyContent={core.handleArtifactCopy}
        onOpenExternal={core.handleArtifactOpenExternal}
      >
        <div className="relative flex h-full min-h-0 flex-col bg-surface">
          <ConversationHeader
            fromTaskId={core.fromTaskId}
            isSkillCreatorMode={core.isSkillCreatorMode}
            headerTitle={core.headerTitle}
            titleRenaming={titleRenaming}
            titleRenameValue={titleRenameValue}
            setTitleRenameValue={setTitleRenameValue}
            selectedSession={core.selectedSession}
            selectedSessionId={core.selectedSessionId}
            refreshActiveSession={core.refreshActiveSession}
            setTitleRenaming={setTitleRenaming}
            titleRenameWidth={titleRenameWidth}
            setTitleRenameWidth={setTitleRenameWidth}
            titleTriggerRef={titleTriggerRef}
            setTitleDeleting={setTitleDeleting}
            draftSendInFlight={core.draftSendInFlight}
            effectiveTurns={core.effectiveTurns}
            scrollToTop={() => core.scrollToTurnIndex(0)}
            headerAgentSlug={core.headerAgentSlug}
            agentNameBySlug={core.agentNameBySlug}
            activeProject={core.activeProject}
            onFork={() => void handleFork()}
            forkInFlight={forkInFlight}
          />

          <ConversationBody
            id={core.id}
            loading={core.loading}
            providers={core.providers}
            providerChannelState={core.providerChannelState}
            scrollContainerRef={core.scrollContainerRef}
            hasMoreOlder={core.hasMoreOlder}
            loadingOlder={core.loadingOlder}
            topSentinelRef={core.topSentinelRef}
            userScrolledRef={core.userScrolledRef}
            loadOlderTurns={core.loadOlderTurns}
            conversationInstanceKey={core.conversationInstanceKey}
            effectiveTurns={core.effectiveTurns}
            displayBusy={core.displayBusy}
            postRunVerificationActive={core.postRunVerificationActive}
            error={core.error}
            handleRetry={core.handleRetry}
            retryCounts={core.retryCounts}
            containerHeight={core.containerHeight}
            skillsBySlug={core.skillsBySlug}
            handleTurnListVirtualApiReady={core.handleTurnListVirtualApiReady}
            scrollToTurnIndex={core.scrollToTurnIndex}
            renderToolCall={core.renderToolCall}
            isToolCardFoldable={core.isToolCardFoldable}
            revealInFinder={core.revealInFinder}
            localFileLinks={core.localFileLinks}
            selectedSessionId={core.selectedSessionId}
            openCitation={core.openCitation}
            setDraft={core.setDraft}
            hasPendingProjectSend={hasPendingProjectSend}
            startingRuntime={core.startingRuntime}
            selectedSessionMode={core.selectedSessionMode}
            setSelectedSessionMode={core.setSelectedSessionMode}
            performSend={core.performSend}
            emptyStateOverride={props.emptyState}
            canForkFromTurn={canForkSession(core.selectedSession)}
            forkInFlight={forkInFlight}
            forkingMessageId={forkingMessageId}
            onForkFromTurn={(messageId) => void handleFork(messageId)}
            activeTurnIndex={core.activeTurnIndex}
          />

          <ApprovalTray
            pendingApprovals={core.pendingApprovals}
            autoApprovedNotices={core.autoApprovedNotices}
            handleApprovalDecision={core.handleApprovalDecision}
          />

          {/* Background-task strip — the turn that LAUNCHES a run_in_background
            command ends normally while the process keeps running for minutes;
            without this the conversation reads as "finished" with no cue that
            work is still in flight. Derived from persisted session.bg_task.*
            events (deriveBackgroundTasks), so it also survives re-entering the
            page mid-run; hides itself once every task reaches a terminal
            state (finished / stopped-on-runtime-close). */}
          <BackgroundTaskStrip tasks={core.runningBgTasks} />

          {/* An overlay in a take-over mode (share selection) suppresses this:
              leaving the composer live invites sending a message in a mode
              where that makes no sense. */}
          {composerSuppressed ? null : (
            <ComposerPane
              onSendTask={props.onSendTask}
              showScrollBottom={core.showScrollBottom}
              handleScrollToBottom={core.handleScrollToBottom}
              displayBusy={core.displayBusy}
              selectedSession={core.selectedSession}
              rosterEmpty={core.rosterEmpty}
              channelLoaded={core.channelLoaded}
              hasChannel={core.hasChannel}
              channelsPending={core.channelsPending}
              agentPending={core.agentPending}
              setupPending={core.setupPending}
              refreshChannels={core.refreshChannels}
              refreshAgents={core.refreshAgents}
              createAgentOpen={core.createAgentOpen}
              setCreateAgentOpen={core.setCreateAgentOpen}
              setAgentLibraryRevision={core.setAgentLibraryRevision}
              setSelectedAgentSlug={core.setSelectedAgentSlug}
              setComposerTouched={core.setComposerTouched}
              selectedSessionId={core.selectedSessionId}
              queue={core.queue}
              isBusy={core.isBusy}
              queueDispatching={core.queueDispatching}
              queuePaused={core.queuePaused}
              handleEditQueued={core.handleEditQueued}
              handleDeleteQueued={core.handleDeleteQueued}
              handleResumeQueue={core.handleResumeQueue}
              handleSteerQueued={core.handleSteerQueued}
              conversationInstanceKey={core.conversationInstanceKey}
              draft={core.draft}
              setDraft={core.setDraft}
              isProjectProject={core.isProjectProject}
              effectiveAgentSlug={core.effectiveAgentSlug}
              handleSend={handleSend}
              interruptRef={core.interruptRef}
              sessionAttachments={core.stagedAttachments}
              handleRemoveSessionAttachment={core.handleRemoveSessionAttachment}
              composerAgents={core.composerAgents}
              sessionAgentSlug={core.sessionAgentSlug}
              selectedAgentSlug={core.selectedAgentSlug}
              execBarLocked={core.execBarLocked}
              sessionExecOrigin={core.sessionExecOrigin}
              execTargetId={core.execTargetId}
              setExecTargetId={core.setExecTargetId}
              setSelectedProviderId={core.setSelectedProviderId}
              setSelectedModelId={core.setSelectedModelId}
              projects={core.projects}
              selectedProjectId={core.selectedProjectId}
              setSelectedProjectId={core.setSelectedProjectId}
              setSelectedComposerSkill={core.setSelectedComposerSkill}
              execBarProjects={core.execBarProjects}
              providerTarget={core.providerTarget}
              panelSetCollapsed={core.panelSetCollapsed}
              composerProviders={core.composerProviders}
              selectedProviderId={core.selectedProviderId}
              selectedModelId={core.selectedModelId}
              composerRuntimes={core.composerRuntimes}
              selectedRuntimeId={core.selectedRuntimeId}
              setSelectedRuntimeId={core.setSelectedRuntimeId}
              selectedPermissionMode={core.selectedPermissionMode}
              setSelectedPermissionMode={core.setSelectedPermissionMode}
              isNewSession={core.isNewSession}
              id={core.id}
              selectedEffort={core.selectedEffort}
              setSelectedEffort={core.setSelectedEffort}
              selectedSessionMode={core.selectedSessionMode}
              setSelectedSessionMode={core.setSelectedSessionMode}
              selectedAgentSkillItems={core.selectedAgentSkillItems}
              composerMentionSkills={core.composerMentionSkills}
              availableSkills={core.availableSkills}
              handleOpenKbPicker={core.handleOpenKbPicker}
              handleLocalFilesAttach={core.handleLocalFilesAttach}
              connectorOptions={core.connectorOptions}
              selectedMcpSlugs={core.selectedMcpSlugs}
              toggleConnector={core.toggleConnector}
              performSend={core.performSend}
            />
          )}
        </div>
      </ArtifactSplitPane>

      <KbPickerOverlay
        kbPickerOpen={core.kbPickerOpen}
        pickerKbTree={core.pickerKbTree}
        pickerKbLoading={core.pickerKbLoading}
        pickerExpandFolder={core.pickerExpandFolder}
        sessionAttachments={core.sessionAttachments}
        handleKbPickerConfirm={core.handleKbPickerConfirm}
        setKbPickerOpen={core.setKbPickerOpen}
      />
      <DeleteConfirmDialog
        open={titleDeleting}
        onOpenChange={(open) => {
          if (!open && !titleDeleteInFlight) setTitleDeleting(false);
        }}
        itemName={
          core.selectedSession?.name ??
          (typeof core.headerTitle === "string" ? core.headerTitle : "")
        }
        loading={titleDeleteInFlight}
        onConfirm={handleTitleDeleteConfirm}
      />
    </>
  );
}

/** ``variant="panel"`` — the bare conversation, no chrome. Meant for a
 *  fixed-width embedding host (e.g. a 345px edition workbench panel), which
 *  owns its own header/新对话 affordances around this. */
function ConversationViewPanel(props: ConversationViewProps) {
  const core = useOrchestration(props, "panel", "input");

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-surface">
      <ConversationBody
        id={core.id}
        loading={core.loading}
        providers={core.providers}
        providerChannelState={core.providerChannelState}
        scrollContainerRef={core.scrollContainerRef}
        hasMoreOlder={core.hasMoreOlder}
        loadingOlder={core.loadingOlder}
        topSentinelRef={core.topSentinelRef}
        userScrolledRef={core.userScrolledRef}
        loadOlderTurns={core.loadOlderTurns}
        conversationInstanceKey={core.conversationInstanceKey}
        effectiveTurns={core.effectiveTurns}
        displayBusy={core.displayBusy}
        postRunVerificationActive={core.postRunVerificationActive}
        error={core.error}
        handleRetry={core.handleRetry}
        retryCounts={core.retryCounts}
        containerHeight={core.containerHeight}
        skillsBySlug={core.skillsBySlug}
        handleTurnListVirtualApiReady={core.handleTurnListVirtualApiReady}
        scrollToTurnIndex={core.scrollToTurnIndex}
        renderToolCall={core.renderToolCall}
        isToolCardFoldable={core.isToolCardFoldable}
        revealInFinder={core.revealInFinder}
        localFileLinks={core.localFileLinks}
        selectedSessionId={core.selectedSessionId}
        openCitation={core.openCitation}
        setDraft={core.setDraft}
        hasPendingProjectSend={core.hasPendingProjectSend}
        startingRuntime={core.startingRuntime}
        selectedSessionMode={core.selectedSessionMode}
        setSelectedSessionMode={core.setSelectedSessionMode}
        performSend={core.performSend}
        emptyStateOverride={props.emptyState}
      />

      <ApprovalTray
        pendingApprovals={core.pendingApprovals}
        autoApprovedNotices={core.autoApprovedNotices}
        handleApprovalDecision={core.handleApprovalDecision}
      />

      <BackgroundTaskStrip tasks={core.runningBgTasks} />

      <ComposerPane
              onSendTask={props.onSendTask}
        showScrollBottom={core.showScrollBottom}
        handleScrollToBottom={core.handleScrollToBottom}
        displayBusy={core.displayBusy}
        selectedSession={core.selectedSession}
        rosterEmpty={core.rosterEmpty}
        channelLoaded={core.channelLoaded}
        hasChannel={core.hasChannel}
        channelsPending={core.channelsPending}
        agentPending={core.agentPending}
        setupPending={core.setupPending}
        refreshChannels={core.refreshChannels}
        refreshAgents={core.refreshAgents}
        createAgentOpen={core.createAgentOpen}
        setCreateAgentOpen={core.setCreateAgentOpen}
        setAgentLibraryRevision={core.setAgentLibraryRevision}
        setSelectedAgentSlug={core.setSelectedAgentSlug}
        setComposerTouched={core.setComposerTouched}
        selectedSessionId={core.selectedSessionId}
        queue={core.queue}
        isBusy={core.isBusy}
        queueDispatching={core.queueDispatching}
        queuePaused={core.queuePaused}
        handleEditQueued={core.handleEditQueued}
        handleDeleteQueued={core.handleDeleteQueued}
        handleResumeQueue={core.handleResumeQueue}
        handleSteerQueued={core.handleSteerQueued}
        conversationInstanceKey={core.conversationInstanceKey}
        draft={core.draft}
        setDraft={core.setDraft}
        isProjectProject={core.isProjectProject}
        effectiveAgentSlug={core.effectiveAgentSlug}
        handleSend={core.handleSend}
        interruptRef={core.interruptRef}
        sessionAttachments={core.stagedAttachments}
        handleRemoveSessionAttachment={core.handleRemoveSessionAttachment}
        composerAgents={core.composerAgents}
        sessionAgentSlug={core.sessionAgentSlug}
        selectedAgentSlug={core.selectedAgentSlug}
        execBarLocked={core.execBarLocked}
        sessionExecOrigin={core.sessionExecOrigin}
        execTargetId={core.execTargetId}
        setExecTargetId={core.setExecTargetId}
        setSelectedProviderId={core.setSelectedProviderId}
        setSelectedModelId={core.setSelectedModelId}
        projects={core.projects}
        selectedProjectId={core.selectedProjectId}
        setSelectedProjectId={core.setSelectedProjectId}
        setSelectedComposerSkill={core.setSelectedComposerSkill}
        execBarProjects={core.execBarProjects}
        providerTarget={core.providerTarget}
        panelSetCollapsed={core.panelSetCollapsed}
        composerProviders={core.composerProviders}
        selectedProviderId={core.selectedProviderId}
        selectedModelId={core.selectedModelId}
        composerRuntimes={core.composerRuntimes}
        selectedRuntimeId={core.selectedRuntimeId}
        setSelectedRuntimeId={core.setSelectedRuntimeId}
        selectedPermissionMode={core.selectedPermissionMode}
        setSelectedPermissionMode={core.setSelectedPermissionMode}
        isNewSession={core.isNewSession}
        id={core.id}
        selectedEffort={core.selectedEffort}
        setSelectedEffort={core.setSelectedEffort}
        selectedSessionMode={core.selectedSessionMode}
        setSelectedSessionMode={core.setSelectedSessionMode}
        selectedAgentSkillItems={core.selectedAgentSkillItems}
        composerMentionSkills={core.composerMentionSkills}
        availableSkills={core.availableSkills}
        handleOpenKbPicker={core.handleOpenKbPicker}
        handleLocalFilesAttach={core.handleLocalFilesAttach}
        connectorOptions={core.connectorOptions}
        selectedMcpSlugs={core.selectedMcpSlugs}
        toggleConnector={core.toggleConnector}
        performSend={core.performSend}
      />

      <KbPickerOverlay
        kbPickerOpen={core.kbPickerOpen}
        pickerKbTree={core.pickerKbTree}
        pickerKbLoading={core.pickerKbLoading}
        pickerExpandFolder={core.pickerExpandFolder}
        sessionAttachments={core.sessionAttachments}
        handleKbPickerConfirm={core.handleKbPickerConfirm}
        setKbPickerOpen={core.setKbPickerOpen}
      />
    </div>
  );
}

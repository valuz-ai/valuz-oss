import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  useLocation,
  useParams,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import {
  agentsApi,
  getDefaultExecutionTarget,
  recordEntityOrigin,
  useExecutionTargets,
  useSessionStore,
  useProjectStore,
  skillsApi,
  usePanelStore,
  type SessionEventDTO,
  type SessionListItem,
  type TodoItem,
  type ProjectDetail,
  type ProjectListItem,
  type SkillView,
  type WorkflowState,
  useCapabilities,
  useSessionArtifacts,
  useSessionAttachments,
  type MemberWithAgent,
} from "@valuz/core";
import {
  DeleteConfirmDialog,
  type ApprovalCardSubject,
  type ApprovalResolvedDecision,
} from "@valuz/ui";
import { useProjectOutlet } from "@valuz/app/layout";
import {
  deriveBackgroundTasks,
  runningBackgroundTasks,
  useIncrementalTurns,
} from "@valuz/core";
import { BackgroundTaskStrip } from "@valuz/ui";
import { usePlatform } from "@valuz/app/platform";
import {
  useHasUsableChannel,
  useTranslation,
  markSessionNotificationsRead,
} from "@valuz/core";
import { computePlanAnchors } from "./conversation-plan-anchors";
import { useCitationDocumentPreview } from "../components/CitationDocumentPreviewProvider";
import { deriveTurnActive } from "./conversation-loading";
import { createConversationBootstrapGuard } from "./conversation-bootstrap";
import { ArtifactSplitPane } from "../components/ArtifactSplitPane";
import { getLastTempAgent } from "../lib/last-temp-agent";
import { NEW_SESSION_ID } from "./conversation/session-events";
import { useToolCallCards } from "./conversation/useToolCallCards";
import { useSessionSubscription } from "./conversation/useSessionSubscription";
import { useSessionLifecycle } from "./conversation/useSessionLifecycle";
import { useConversationHistory } from "./conversation/useConversationHistory";
import { useInputQueue } from "./conversation/useInputQueue";
import { useConversationSend } from "./conversation/useConversationSend";
import { useApprovalActions } from "./conversation/useApprovalActions";
import { useConversationScroll } from "./conversation/useConversationScroll";
import { useContextPanel } from "./conversation/useContextPanel";
import { useSkillStaging } from "./conversation/useSkillStaging";
import { useArtifactPane } from "./conversation/useArtifactPane";
import { useComposerConfig } from "./conversation/useComposerConfig";
import { useComposerSelection } from "./conversation/useComposerSelection";
import { useKbPickerState } from "./conversation/useKbPickerState";
import { useProjectHandoff } from "./conversation/useProjectHandoff";
import { useTitleActions } from "./conversation/useTitleActions";
import { ApprovalTray } from "./conversation/ApprovalTray";
import { ConversationBody } from "./conversation/ConversationBody";
import { ConversationHeader } from "./conversation/ConversationHeader";
import { ComposerPane } from "./conversation/ComposerPane";
import { KbPickerOverlay } from "./conversation/KbPickerOverlay";

export const ConversationPage = () => {
  const { t } = useTranslation();
  const { openCitation } = useCitationDocumentPreview();
  const platform = usePlatform();
  const { revealInFinder } = platform;
  const { id = NEW_SESSION_ID } = useParams<{ id: string }>();
  const location = useLocation();
  const promotingSessionIdRef = useRef<string | null>(null);
  const consumedPromoteSessionIdsRef = useRef<Set<string>>(new Set());
  const previousRouteSessionIdRef = useRef(id);
  const skipNextSessionStateResetRef = useRef(false);
  const [conversationInstanceKey, setConversationInstanceKey] = useState(
    () => `conversation:${id}`,
  );
  useEffect(() => {
    const previousRouteId = previousRouteSessionIdRef.current;
    previousRouteSessionIdRef.current = id;

    if (previousRouteId === id) return;

    const isPromote =
      previousRouteId === NEW_SESSION_ID &&
      promotingSessionIdRef.current === id;
    if (isPromote) {
      promotingSessionIdRef.current = null;
      return;
    }

    setConversationInstanceKey(`conversation:${id}`);
  }, [id]);
  // Opening a conversation clears the unread badge for any notification that
  // points at it (a question answered elsewhere, a run failure the user is now
  // looking at). Covers every entry path — direct link, notification card,
  // conversation list — since they all land here. Skips the "new" sentinel.
  useEffect(() => {
    if (id && id !== NEW_SESSION_ID) markSessionNotificationsRead(id);
  }, [id]);
  const { directoryFieldMode, setRightPanel, setHeader, setHideHeader } =
    useProjectOutlet();
  const panelCollapsed = usePanelStore((s) => s.collapsed);
  const panelSetCollapsed = usePanelStore((s) => s.setCollapsed);
  const [searchParams] = useSearchParams();
  // Mode hint persisted on the session row at creation time; lets us restore
  // the Skill-Creator banner / staging panel when the user re-enters the
  // session from history (where ?mode= is absent).
  const [sessionTriggerMode, setSessionTriggerMode] = useState<string | null>(
    null,
  );
  // Project-agent handle for the open session (Project Task lead/member).
  const [sessionAgentSlug, setSessionAgentSlug] = useState<string | null>(null);
  // Project conversations pick a configured project agent instead of a raw
  // model. ``projectAgents`` is the member roster for the active project;
  // ``selectedAgentSlug`` is the user's pick for the next (new) session.
  const [projectAgents, setProjectAgents] = useState<MemberWithAgent[]>([]);
  // 10-new-conversation-guidance: the 🤖 「+ Agent」 menu item opens a create
  // dialog for temp conversations (the project path navigates to the project).
  const [createAgentOpen, setCreateAgentOpen] = useState(false);
  // Invalidate the selected target's roster after an agent is created.
  const [agentLibraryRevision, setAgentLibraryRevision] = useState(0);
  // 10-new-conversation-guidance (slice 2): is there any usable model channel?
  // Drives the setup banner's "no channel" state.
  const {
    hasChannel,
    loaded: channelLoaded,
    refresh: refreshChannels,
  } = useHasUsableChannel();
  const { managedRuntimeSetup } = useCapabilities();
  // A managed install receives its channels and its built-in assistant, so
  // "none yet" is a delivery that has not landed — not a step the user skipped.
  // Both cases offer a retry instead of a setup screen they cannot act on.
  const channelsPending = managedRuntimeSetup && channelLoaded && !hasChannel;
  const [selectedAgentSlug, setSelectedAgentSlug] = useState<string | null>(
    null,
  );
  // 「去临时对话」after creating an agent navigates here with ?agent=<slug>;
  // honour it as the pre-selected agent for the next (new) chat. The roster
  // roster reload keys off the same param so the new agent is
  // actually present, and the defaulting effect below treats it as top priority.
  const agentParam = searchParams.get("agent");
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (agentParam) setSelectedAgentSlug(agentParam);
  }, [agentParam]);
  // Deep-link origin fast path (multi-target editions): a share/notification
  // link can carry ``?origin=cloud`` so the page routes to the owning backend
  // without a probe round-trip. The edition adapter validates the value;
  // single-target builds have no adapter → no-op.
  const originParam = searchParams.get("origin");
  useEffect(() => {
    if (originParam && id && id !== NEW_SESSION_ID) {
      recordEntityOrigin(id, originParam);
    }
  }, [originParam, id]);
  // Set when this conversation was opened from a task's "view session" link
  // (TaskDetailPage). Drives the breadcrumb back-to-task affordance in the
  // header so a subtask/lead session isn't a navigational dead end.
  const fromTaskId = searchParams.get("from_task");
  const isSkillCreatorMode =
    searchParams.get("mode") === "skill-creator" ||
    sessionTriggerMode === "skill-creator";
  // Draft-first skill creation (技能库 → 添加技能 → AI 创建): the entry no
  // longer pre-creates a session (that locked an empty agent binding — a
  // dead conversation). It lands here as a normal draft carrying the
  // creation context in the URL; ``ensureSession`` mints the session via
  // the skills launcher on the first send, with the agent the user picked.
  const skillKindParam = searchParams.get("skill_kind");
  const skillProjectParam = searchParams.get("skill_project");
  const shouldRevealPanelFromNavigation =
    (
      location.state as {
        revealSessionContext?: boolean;
      } | null
    )?.revealSessionContext === true;
  const revealPanelOnSessionChangeRef = useRef(false);

  // Staging panel (Skill Creator mode) — extracted to useSkillStaging.
  const {
    stagingSlugs,
    stagingRefreshing,
    stagingSyncing,
    refreshStaging,
    handleSyncStaging,
  } = useSkillStaging({ id, isSkillCreatorMode });

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<ProjectListItem[]>([]);
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(
    null,
  );
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    null,
  );
  // Title rename / delete cluster — extracted to useTitleActions (called
  // here, below ``selectedSessionId``, which the delete handler reads).
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
  } = useTitleActions({ selectedSessionId });
  // Mirror of ``selectedSessionId`` for callbacks that must read it
  // without declaring it in their useCallback deps (which would re-
  // create the callback on every session change). Used by
  // ``refreshActiveSession`` and ``bootstrap`` to decide whether to
  // re-fetch events: when the post-turn refresh lands on the same
  // session, refetching would clobber SSE-accumulated events with
  // the server's truncated list (``list_events_after`` caps at 500
  // rows ASC, so a refetch on a long session silently drops the most
  // recent turn — which is exactly what we just streamed).
  const selectedSessionIdRef = useRef<string | null>(null);
  const bootstrapGuardRef = useRef(createConversationBootstrapGuard());
  useEffect(() => {
    selectedSessionIdRef.current = selectedSessionId;
  }, [selectedSessionId]);
  const [events, setEvents] = useState<SessionEventDTO[]>([]);
  // Every ``event_uid`` this transcript has already consumed via the SSE
  // path. Reconnect gap-fills and the server's initial drain legitimately
  // redeliver frames (the history cursor only advances from REST reads; live
  // frames carry kernel-local seqs, a different space) — replays must stay
  // render-inert AND terminal-gate-inert (a redelivered old terminal frame
  // must not re-run turn-end handling). Uid-less legacy frames bypass the
  // guard. Bounded in ``appendEvent`` (a session-lifetime page keeps this
  // set for its whole stay); cleared wherever the transcript resets.
  const seenEventUidsRef = useRef<Set<string>>(new Set());
  // Live TODO list for the active session. Hydrated from
  // ``session.todos`` on session switch and from ``session.todos.update``
  // SSE frames during a turn. ``null`` means "agent has never emitted
  // a TodoWrite snapshot for this session"; an empty array means
  // "all done" (kernel preserves it as a meaningful state).
  const [todos, setTodos] = useState<TodoItem[] | null>(null);
  // Live progress of Claude dynamic-workflow (``Workflow`` tool) runs in this
  // session, keyed by the launch tool_use_id. Fed by ``session.workflow_progress``
  // SSE snapshots; ``renderToolCall`` reads it to render the progress card on the
  // matching Workflow tool block. Live-only (the kernel never persists these), so
  // it stays empty on history replay / reconnect — the persisted Workflow tool
  // call then renders as a plain tool card. Cleared on session switch.
  const [workflowStates, setWorkflowStates] = useState<
    Map<string, WorkflowState>
  >(() => new Map());
  // Optimistic pending user message — rendered IMMEDIATELY when the user
  // hits Send so they see their text in the conversation without waiting
  // for the (potentially multi-second) round-trip through ensureSession ⇒
  // attachment upload ⇒ POST /messages ⇒ Claude SDK init ⇒ first SSE
  // frame. Cleared when the live ``message.user`` event arrives (the
  // backend always fires this as the very first event of a turn) or when
  // send fails.
  const [pendingUserMessage, setPendingUserMessage] = useState<{
    text: string;
    attachments: Array<{ name: string; size: number }>;
    /**
     * HISTORY cursor (durable-store seq) at the moment ``handleSend`` set
     * the pending — used by ``effectiveTurns`` to disambiguate a server
     * echo for THIS send (history envelope seq > fromSeq) from a previous
     * turn that happens to share the exact same text. Only comparable to
     * HISTORY seqs; a LIVE echo (kernel-local seq) is recognized via the
     * ``sentAt`` timestamp instead. Without this, sending the identical
     * text twice in a row would falsely dedup the optimistic against the
     * prior turn and snap-to-top would land on the older turn's position.
     */
    fromSeq: number;
    /**
     * Local ISO timestamp captured when ``handleSend`` set the pending,
     * surfaced as the optimistic turn's ``userTimestamp`` so the user
     * message action bar can render its hover-only clock immediately
     * — without it, the freshly-sent message would have no timestamp
     * until the SSE ``message.user`` echo lands and replaces the
     * pending turn with the real one.
     */
    sentAt: number; // Unix epoch ms (UTC)
  } | null>(null);
  // Wall-clock anchor for the latest send, deliberately OUTLIVING
  // ``pendingUserMessage``: that one is cleared by the kernel's
  // ``message.user`` echo, and the echo is exactly the moment the anchor
  // becomes load-bearing. The real turn's ``userTimestamp`` is stamped when
  // the RUNTIME started — after local kernel warm-up or, in sandboxed / cloud
  // execution, after the whole instance boots — so handing the turn header
  // that stamp restarts its "已处理 X 秒" counter from zero mid-turn. Carrying
  // the send time onto the real turn as ``clientSentAtMs`` keeps ONE counter
  // across the optimistic → real handover. Cleared only where the send is
  // genuinely void: a failed send, or a session switch.
  const [turnStartAnchor, setTurnStartAnchor] = useState<{
    text: string;
    fromSeq: number;
    sentAt: number; // Unix epoch ms (UTC), client clock
  } | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  // Derived here — above its original position — because it is a
  // ``useComposerSelection`` param; the memo reads only ``sessions`` /
  // ``selectedSessionId``, both declared above.
  const selectedSession = useMemo(
    () => sessions.find((s) => s.id === selectedSessionId) ?? null,
    [selectedSessionId, sessions],
  );
  // Composer override state spine — runtime / provider / model / effort /
  // permission / connector / skill selections, their Settings-default and
  // runtime-availability seeding, the locked-session mirror effect and
  // ``handleSwitchModel`` — extracted to useComposerSelection. (The two
  // composer-override effects that read useComposerConfig outputs —
  // seed-from-brain and provider auto-pick — stay in this page, below.)
  const {
    selectedProviderId,
    setSelectedProviderId,
    selectedModelId,
    setSelectedModelId,
    selectedRuntimeId,
    setSelectedRuntimeId,
    composerTouched,
    setComposerTouched,
    defaultsLoading,
    runtimeList,
    retryCounts,
    setRetryCounts,
    modelSelectorUnlocked,
    selectedPermissionMode,
    setSelectedPermissionMode,
    selectedEffort,
    setSelectedEffort,
    selectedMcpSlugs,
    connectorOptions,
    toggleConnector,
    selectedComposerSkill,
    setSelectedComposerSkill,
    handleSwitchModel,
  } = useComposerSelection({
    selectedSessionId,
    selectedAgentSlug,
    selectedSession,
  });

  // Multi-target editions: where a NEW quick/temp chat runs. ``null`` follows
  // the registered default; single-target builds register nothing and the
  // picker renders nothing. Locked at session creation (ADR-006 semantics) —
  // project conversations don't get a choice, they follow the project's
  // origin. See docs (commercial): execution-location-per-entity.
  const executionTargets = useExecutionTargets();
  const [execTargetId, setExecTargetId] = useState<string | null>(null);
  const resolveExecTarget = useCallback(() => {
    if (executionTargets.length === 0) return undefined;
    return (
      executionTargets.find((target) => target.id === execTargetId) ??
      getDefaultExecutionTarget()
    );
  }, [executionTargets, execTargetId]);
  const isNewSession = id === NEW_SESSION_ID;
  // Local mirror of ``answers`` captured at submit time. Keyed by
  // ``tool_use_id`` (== renderer's ``tool.id``). Lets the renderer
  // swap to ``UserAnswerSummaryCard`` IMMEDIATELY on click — without
  // waiting for the paired ``session.action_resolved`` SSE frame to
  // round-trip. Once that frame lands ``askUserQuestionAnswersByToolId``
  // takes precedence (kernel is the authority); on submit failure the
  // entry is cleared and the interactive card returns.
  const [askUserQuestionLocalAnswers, setAskUserQuestionLocalAnswers] =
    useState<Record<string, Record<string, string | string[]>>>({});
  // ADR-013 cross-runtime approval contract: the runtime parks on
  // ``clarifying_questions`` when AskUserQuestion is invoked, emitting a
  // ``session.requires_action`` with a ``pending_id``. Resolving it
  // requires POST /v1/sessions/{id}/actions with decision=answer + the
  // structured answers map. Since the runtime parks (only one
  // clarifying pending open at a time per session), we just track the
  // current unresolved one. ``action_resolved`` for the same pending_id
  // clears it. The kernel doesn't echo back a tool_use_id in the
  // pending payload so we can't key this by ``tool.id`` directly — the
  // FIFO is "the only open clarifying pending".
  const currentClarifyingPendingRef = useRef<string | null>(null);
  // ADR-013 — pending approve/reject cards for the 4 non-clarifying
  // subjects (shell_command / file_change / mcp_tool_call /
  // tool_input). Rendered as a tray above the Composer. Each card
  // shows the subject payload + Approve / Reject; user click POSTs
  // /actions and the paired action_resolved SSE frame flips the card
  // into its answered state and eventually removes it from the tray.
  interface PendingApprovalEntry {
    pendingId: string;
    subject: ApprovalCardSubject;
    payload: Record<string, unknown>;
    // V5+d008b53 (v2 approval contract):
    availableDecisions: string[];
    sessionRulePreviewDisplay: string | null;
    originalInput: Record<string, unknown> | null;
    receivedAt?: number; // Unix epoch ms (UTC)
    submitting?: boolean;
    // Resolution state. Populated when the matching
    // ``action_resolved`` lands. Drives the swap from the full
    // ``ApprovalCard`` to the compact ``ApprovalResolvedStrip``.
    answered?: boolean;
    decision?: ApprovalResolvedDecision;
    rejectMessage?: string | null;
  }
  const [pendingApprovals, setPendingApprovals] = useState<
    PendingApprovalEntry[]
  >([]);
  // V5+d008b53 — cache-hit notices pushed when the kernel emits
  // ``action_resolved(decision="auto_approved")``. The kernel pairs
  // every cache-hit with a preceding ``requires_action`` (see
  // ``claude_agent/runtime.py``), so by the time the resolved frame
  // lands we already have the originating ``subject`` + ``payload``
  // sitting in ``pendingApprovals``. The handler harvests those into
  // the notice before removing the pending entry, so the strip can
  // render a one-line summary of WHAT was approved (e.g.
  // ``reportify-stock.stock_quote(symbol=000858)``) instead of a
  // generic "auto-approved" label.
  //
  // Lifecycle: each push is paired with a ``setTimeout`` filter so
  // the tray auto-clears 5s later (symmetric with
  // ``ApprovalResolvedStrip``'s 2s, longer here because the user
  // didn't initiate the action and needs more time to notice).
  // Dedup by pending_id prevents an SSE retransmit / replay from
  // double-inserting before the timer fires.
  interface AutoApprovedNotice {
    pendingId: string;
    receivedAtLabel?: string;
    rulePreviewDisplay: string | null;
    // Captured from the matching ``requires_action`` row in
    // ``pendingApprovals`` at resolve time. ``null`` when the
    // requires_action couldn't be matched (rare — replay edge case).
    subject: ApprovalCardSubject | null;
    payload: Record<string, unknown> | null;
  }
  const [autoApprovedNotices, setAutoApprovedNotices] = useState<
    AutoApprovedNotice[]
  >([]);
  // ``rule_id → preview.display`` learned at ``approve_for_session``
  // commit time. Lets the auto_approved strip show the rule that
  // fired even though the kernel only re-emits the rule_id, not the
  // preview, on subsequent cache hits.
  const ruleIdToPreviewRef = useRef<Map<string, string>>(new Map());
  // Ref so renderToolCall (declared before handleSend) can invoke the
  // submit handler without a block-scope ordering error.
  const askUserQuestionSubmitRef = useRef<
    (toolId: string, answers: Record<string, string>) => void
  >(() => {});
  // The session-lifetime stream's controller. Owned by the session-open
  // effect via ``subscribeToSession`` (which supersedes the previous one);
  // aborted on unmount. Nothing else opens or closes streams.
  const abortRef = useRef<AbortController | null>(null);
  // Set while ``handleSend`` is between ``ensureSession`` and its POST
  // settling. Remaining consumer: the bootstrap promote-fast-path, which
  // skips the history refetch on the ``/conversation/new`` → real-id
  // promotion so the optimistic transcript isn't clobbered mid-send.
  const isSendInFlightRef = useRef(false);
  // HISTORY cursor — the highest DURABLE-store seq confirmed via REST
  // history reads (``listEvents`` / ``listEventsWindow`` responses). Used as
  // ``after_seq`` for history reads and for the SSE subscribe/reconnect
  // cursor. NEVER advanced from live SSE frames: those carry the kernel's
  // LOCAL seq — an independent space — and feeding one into this cursor
  // would skip (or endlessly replay) history. Live-path dedup is uid-based
  // (``event_uid``), not cursor-based.
  const historyCursorRef = useRef(0);
  // Consecutive unexpected-close reconnect attempts for the live events
  // stream. Reset whenever a live frame is delivered (a healthy stream)
  // and on every session switch; capped so a session whose stream is
  // repeatedly cut (dead proxy, wedged server) degrades to the
  // status-reconcile path instead of reconnecting forever.
  const streamReconnectAttemptsRef = useRef(0);
  // Earliest seq currently in ``events``. Used as the cursor for the
  // upward "load older turns" pager. Starts at ``Infinity`` so the first
  // ``listEventsWindow`` call (which omits ``before_seq``) targets the
  // latest window — the second call uses this value verbatim.
  const minSeqRef = useRef<number>(Number.POSITIVE_INFINITY);
  // Whether the server reports older turns are still available beyond
  // the current window. Drives the IntersectionObserver gating + the
  // sentinel's visibility — both are kept in sync via ``setHasMoreOlder``
  // wherever the ref is mutated. The ref form lets ``loadOlderTurns``
  // read the current value without taking a re-render dependency.
  const hasMoreOlderRef = useRef(false);
  const [hasMoreOlder, setHasMoreOlder] = useState(false);
  // Concurrent-load guard. The IntersectionObserver can fire several
  // times in quick succession as the sentinel enters/leaves the
  // viewport; without this guard we'd issue duplicate requests.
  const loadingOlderRef = useRef(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  // Becomes true once the user has issued a real scroll on the
  // conversation scroller (mousewheel / trackpad / arrow-key scroll).
  // Programmatic scroll-to-bottom-on-load and ResizeObserver-driven
  // re-measures don't flip it. ``loadOlderTurns`` short-circuits while
  // this is false so the IntersectionObserver can't cascade-load every
  // page in a single tick during the initial mount race (sentinel
  // briefly visible at scrollTop=0 before auto-scroll-to-bottom fires).
  const userScrolledRef = useRef(false);
  // One-shot scroll anchor: when the upward pager prepends events to
  // the front of the list, the scroll container's existing items shift
  // downward by the new content's height. This ref captures the pre-
  // prepend ``scrollHeight``/``scrollTop`` so a useLayoutEffect can
  // re-pin the scrollTop to the same logical content position before
  // the user perceives a jump.
  const pendingScrollAnchorRef = useRef<{
    oldScrollHeight: number;
    oldScrollTop: number;
  } | null>(null);
  const interruptRef = useRef<() => void>(() => {});
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const topSentinelRef = useRef<HTMLButtonElement>(null);
  const pinNextTurnToTopRef = useRef(false);
  const keepCurrentTurnAtTopRef = useRef(false);
  // Open while confirming a send that would ship still-parsing attachments.
  const [parsingConfirmOpen, setParsingConfirmOpen] = useState(false);
  const sidebarSessions = useSessionStore((state) => state.sessions);
  const setSidebarSessions = useSessionStore((state) => state.setSessions);
  const fetchSidebarSessions = useSessionStore((state) => state.fetchSessions);
  const setStoreActiveProjectId = useSessionStore(
    (state) => state.setActiveProjectId,
  );
  const upsertProject = useProjectStore((s) => s.upsertProject);
  // Server-stored attachments + async parse status, owned by the shared hook:
  // upload-on-attach, poll ``parsing → ready|failed``, and live progress for
  // the composer chips + context panel. ``setSessionAttachments`` is the
  // hook's own setter (kept aliased so existing optimistic splices still
  // work). Local files now upload the moment they're attached, so there is no
  // separate not-yet-uploaded ``File[]`` queue for them anymore.
  const {
    attachments: sessionAttachments,
    setAttachments: setSessionAttachments,
    hasParsing: attachmentsParsing,
    attachLocalFiles,
    attachKbDocs,
    remove: removeSessionAttachmentRow,
    markPendingConsumed,
  } = useSessionAttachments(selectedSessionId);
  // Agent-delivered artifacts (the "生成文件" list) — recorded by the
  // ``deliver_artifacts`` MCP tool. Loads on session change; refreshed on
  // turn-end (below) so newly delivered files appear without a manual reload.
  const { artifacts: sessionArtifacts, refresh: refreshArtifacts } =
    useSessionArtifacts(selectedSessionId);
  const navigate = useNavigate();

  // Project-detail handoff + send entry point — the optimistic handed-over
  // turn, the draft-first project send, and ``handleSend`` — extracted to
  // useProjectHandoff. ``getDisplayBusy`` / ``performEnqueue`` /
  // ``performSend`` are declared below this call site (useInputQueue /
  // useConversationSend returns), so they travel as deferring lambdas that
  // are only invoked at send time.
  const {
    projectSendHandoffRef,
    handoffSessionIdRef,
    setDraftBootstrapSettled,
    handleSend,
    hasPendingProjectSend,
  } = useProjectHandoff({
    id,
    location,
    searchParams,
    selectedProjectId,
    draft,
    attachmentsParsing,
    historyCursorRef,
    setPendingUserMessage,
    setTurnStartAnchor,
    setSending,
    setParsingConfirmOpen,
    getDisplayBusy: () => displayBusy,
    performEnqueue: () => performEnqueue(),
    performSend: (overrideText?: string) => performSend(overrideText),
  });

  const [availableSkills, setAvailableSkills] = useState<SkillView[]>([]);
  const [projectSkills, setProjectSkills] = useState<SkillView[]>([]);

  // Residual-state sweep for in-place session transitions. The layout used
  // to remount this whole page on every pathname change, which incidentally
  // wiped composer/turn-scoped state; conversation routes now transition in
  // place (so the ``new`` → ``{id}`` promotion survives), so anything the
  // remount used to clean must be cleared explicitly. Keyed on
  // ``conversationInstanceKey``, which changes exactly on TRUE session
  // switches — stable across the promotion (where ``handleSend`` owns this
  // state) and a no-op on mount (everything is still at its initial value).
  useEffect(() => {
    setDraft("");
    setSelectedComposerSkill(null);
    setRetryCounts({});
    setAutoApprovedNotices([]);
    userScrolledRef.current = false;
  }, [conversationInstanceKey]);

  const activeProject = useMemo(
    () =>
      (projects.find((w) => w.id === selectedProjectId) as
        ProjectDetail | undefined) ?? null,
    [selectedProjectId, projects],
  );
  const activeProjectRootPath = useMemo(() => {
    const detail = activeProject as ProjectDetail | null;
    return detail?.cwd ?? detail?.root_path ?? "";
  }, [activeProject]);

  // The worktree the open session runs in (creation-time snapshot on the list
  // item). When present, the right panel's file tree + artifact reads scope to
  // the worktree checkout (design D7) instead of the shared project cwd.
  // Derived from ``sessions`` (not ``selectedSession``, declared below) so the
  // callbacks above it can depend on it without a temporal-dead-zone error.
  const activeWorktree = useMemo(
    () => sessions.find((s) => s.id === selectedSessionId)?.worktree ?? null,
    [sessions, selectedSessionId],
  );

  // Artifact split pane — extracted to useArtifactPane.
  const {
    artifactFile,
    closeArtifact,
    openArtifactFile,
    localFileLinks,
    handleArtifactReload,
    handleArtifactClose,
    handleArtifactCopy,
    handleArtifactOpenExternal,
  } = useArtifactPane({
    selectedProjectId,
    selectedSessionId,
    activeWorktree,
    activeProjectRootPath,
    directoryFieldMode,
  });

  // KB / file-picker state — the attachment picker's open flag + global KB
  // doc tree, the read-only project KB bindings, and the project file tree +
  // ``refreshFileTree`` — extracted to useKbPickerState. (The turn-end
  // file-tree refresh stays below: it couples to ``isBusy`` /
  // ``refreshArtifacts``.)
  const {
    kbPickerOpen,
    setKbPickerOpen,
    pickerKbTree,
    pickerKbLoading,
    pickerExpandFolder,
    projectKbTree,
    projectKbBindings,
    handleExpandProjectKbFolder,
    fileTree,
    setFileTree,
    refreshFileTree,
  } = useKbPickerState({
    selectedProjectId,
    activeProject,
    activeWorktree,
  });

  // The composer's loading / Stop state (and the streaming logo + "已处理 X 秒"
  // timer) is DERIVED (session-stream-lifetime §2.1): ``sending`` is only the
  // optimistic click → turn-start bridge (released by the turn's start /
  // terminal events or a send error); the reconciled session ``status``
  // carries busy for the turn itself — including turns started by the queue
  // drain, a schedule, or another client. The stream being open says nothing.
  const isBusy = deriveTurnActive(sending, selectedSession?.status);

  // The agent actually bound to this composer: an existing session is frozen to
  // its ``sessionAgentSlug`` (ADR-006), a fresh draft uses the picker's
  // ``selectedAgentSlug``. The Composer's ``selectedAgentSlug`` prop derives the
  // same value — skill resolution and the ``/`` gate must use it too, or they'd
  // read a null draft slug while viewing a live conversation.
  const effectiveAgentSlug = selectedSession
    ? sessionAgentSlug
    : selectedAgentSlug;

  // Publish the open conversation's project to the store so the sidebar keeps
  // that project's accordion expanded — authoritative and immediate (straight
  // from the loaded session detail), unlike the lagging runs list. Cleared when
  // the conversation is project-less or this page unmounts.
  const openConversationProjectId = selectedSession?.project_id ?? null;
  useEffect(() => {
    setStoreActiveProjectId(openConversationProjectId);
    return () => setStoreActiveProjectId(null);
  }, [openConversationProjectId, setStoreActiveProjectId]);
  // Incremental transcript build: folds only newly-appended events per render
  // (falls back to a full rebuild on any non-append change) and hands back
  // turns that already satisfy the stable-ref contract — so a long streamed
  // reply no longer re-walks the whole event history per token. Replaces the
  // old ``useStableTurns(buildTurns(events))`` O(N²) hot path.
  const turns = useIncrementalTurns(events);
  // Background tasks (run_in_background shell commands) — derived from the
  // same persisted event list, so the "still running" strip is correct on
  // live streams and after re-entering the page mid-run.
  const runningBgTasks = useMemo(
    () => runningBackgroundTasks(deriveBackgroundTasks(events)),
    [events],
  );
  // No idle-time bg-task poll anymore: the session-lifetime stream stays open
  // between turns (the server generator drains bus events between turns too),
  // so bg_task terminal frames and the CLI's spontaneous wake-up turn arrive
  // live on the same connection that carried the launching turn.

  // VALUZ-CHATPLAN — track the LATEST ``plan_task`` / ``modify_plan`` tool
  // result per task_id. That position in the conversation gets the rich
  // LiveTaskCard (subtask list + status + actions, SSE-subscribed). Earlier
  // plan writes for the same task degrade to compact history pills, so the
  // user always lands on the "current state" surface near the bottom of
  // the timeline. ``taskByRichTool`` also doubles as a "this is a plan
  // write for a known task" set for the pill renderer. The task id is
  // resolved from the lead session's ``metadata.valuz.task_id`` (the
  // authoritative binding) rather than the unreliable tool args — see
  // ``computePlanAnchors``.
  const planAnchors = useMemo(
    () => computePlanAnchors(turns, selectedSession?.task_id ?? null),
    [turns, selectedSession],
  );
  // Append the optimistic pending turn so the user sees their message +
  // a thinking hint immediately on Send. ``ConversationTurnList`` keys
  // off ``sending && isLatest`` to draw the waiting indicator on the
  // last turn — placing the optimistic one at the end gets that for
  // free without touching the renderer.
  const effectiveTurns = useMemo(() => {
    const lastTurn = turns[turns.length - 1];
    const lastTurnSeq = lastTurn?.userMessageSeq ?? 0;
    // "Is the latest real turn the kernel's echo of send S?" — two signals,
    // because the echo can arrive from either seq space: a HISTORY row
    // satisfies ``seq > fromSeq`` (fromSeq is the history cursor at send
    // time); a LIVE frame carries a kernel-local seq that can't be compared
    // to fromSeq, so fall back to the store-independent event timestamp vs
    // the moment of the send. A previous turn that happens to share the exact
    // same text fails both (its history seq <= fromSeq; its timestamp <
    // sentAt), which is what keeps re-sending identical text from collapsing
    // onto the older turn.
    const lastTurnIsEchoOf = (send: {
      text: string;
      fromSeq: number;
      sentAt: number;
    }): boolean =>
      lastTurn !== undefined &&
      lastTurn.userText === send.text &&
      (lastTurnSeq > send.fromSeq ||
        (lastTurn.userTimestamp !== undefined &&
          lastTurn.userTimestamp >= send.sentAt));

    // Re-stamp the echoed turn with the moment the user pressed Send, so the
    // turn header's elapsed counter spans the runtime-startup window instead
    // of restarting at the kernel's stamp (see ``turnStartAnchor``).
    const base =
      lastTurn && turnStartAnchor && lastTurnIsEchoOf(turnStartAnchor)
        ? [
            ...turns.slice(0, -1),
            { ...lastTurn, clientSentAtMs: turnStartAnchor.sentAt },
          ]
        : turns;

    if (!pendingUserMessage) return base;
    // Defensive dedup: if the kernel's ``message.user`` echo has already
    // been folded into ``turns`` but ``setPendingUserMessage(null)`` hasn't
    // landed yet (race between two batched setStates inside the SSE
    // callback), the latest real turn carries the same userText as the
    // optimistic — drop the optimistic so the user doesn't see two
    // identical bubbles in the same render.
    if (lastTurnIsEchoOf(pendingUserMessage)) return base;
    return [
      ...base,
      {
        id: "pending-turn",
        userMessageSeq: 0,
        userText: pendingUserMessage.text,
        blocks: [],
        failedMessage: null,
        attachments:
          pendingUserMessage.attachments.length > 0
            ? pendingUserMessage.attachments
            : undefined,
        userTimestamp: pendingUserMessage.sentAt,
        clientSentAtMs: pendingUserMessage.sentAt,
      },
    ];
  }, [turns, pendingUserMessage, turnStartAnchor]);

  // Retire the optimistic pending once its echo is VISIBLE, whichever path
  // delivered it.
  //
  // Clearing it only from the live SSE handler is not enough: the echo can
  // just as well arrive in the history refetch bootstrap runs on landing, and
  // then nothing releases the pending. ``effectiveTurns`` above still dedupes
  // the bubble, so the transcript looks right — but ``startingRuntime`` is
  // derived from the pending, so the header stays stuck on "正在启动…运行环境"
  // for the rest of the turn, counting up while the agent is plainly already
  // answering. ``refreshEventsInner``'s unconditional clear used to mask this;
  // the handoff guard added with the project-composer change removed that
  // accident for exactly the sessions most likely to hit it.
  useEffect(() => {
    if (!pendingUserMessage) return;
    const lastTurn = turns[turns.length - 1];
    if (!lastTurn || lastTurn.userText !== pendingUserMessage.text) return;
    const echoed =
      (lastTurn.userMessageSeq ?? 0) > pendingUserMessage.fromSeq ||
      (lastTurn.userTimestamp !== undefined &&
        lastTurn.userTimestamp >= pendingUserMessage.sentAt);
    if (!echoed) return;
    setPendingUserMessage(null);
    handoffSessionIdRef.current = null;
  }, [turns, pendingUserMessage]);

  // Special-cased tool-call cards (skill submissions, agent/automation
  // proposals, chatplan pills, AskUserQuestion, generative UI, workflow
  // progress) — state + renderers live in the hook.
  const { isToolCardFoldable, renderToolCall } = useToolCallCards({
    events,
    turns,
    isBusy,
    selectedSessionId,
    selectedSessionIdRef,
    selectedSessionName: selectedSession?.name ?? null,
    planAnchors,
    workflowStates,
    askUserQuestionLocalAnswers,
    askUserQuestionSubmitRef,
  });

  const firstUserText = turns[0]?.userText;
  // A draft page that has already accepted a send. The session does not exist
  // yet — it is minted behind the optimistic turn — but everything the header
  // displays is already decided: the project, the agent, the execution
  // origin, and the fact that no run has started. Without this the whole
  // header stayed blank for the entire mint + startup window and then popped
  // in, which on a cloud project is several seconds of a page that looks like
  // it lost the message.
  const draftSendInFlight = isNewSession && effectiveTurns.length > 0;
  // The agent this conversation runs as: bound on an existing session, and the
  // composer's pick while the session is still being minted (it is what
  // ``ensureSession`` will freeze into it).
  const headerAgentSlug =
    sessionAgentSlug ?? (draftSendInFlight ? selectedAgentSlug : null);
  const headerTitle =
    selectedSession?.name ||
    firstUserText?.slice(0, 40) ||
    (isNewSession && !draftSendInFlight
      ? null
      : t("conversation.newChat" as Parameters<typeof t>[0]));

  // Composer / provider / agent / skill derivations — extracted to
  // useComposerConfig. The two composer-override effects below stay here:
  // they write this page's selectedRuntimeId/ProviderId/ModelId/Effort state.
  const {
    sessionExecOrigin,
    selectedProjectOrigin,
    providerTarget,
    startingRuntime,
    providerChannelState,
    providers,
    myAgents,
    myAgentsLoaded,
    refreshAgents,
    composerProviders,
    composerRuntimes,
    rosterEmpty,
    agentPending,
    setupPending,
    execBarLocked,
    execBarProjects,
    composerAgents,
    selectedAgentBrain,
    agentNameBySlug,
    selectedAgentSkillItems,
    composerMentionSkills,
    skillsBySlug,
  } = useComposerConfig({
    id,
    isNewSession,
    projects,
    selectedProjectId,
    selectedSession,
    activeProject,
    executionTargets,
    execTargetId,
    pendingUserMessage,
    selectedRuntimeId,
    runtimeList,
    managedRuntimeSetup,
    channelsPending,
    projectAgents,
    agentParam,
    agentLibraryRevision,
    selectedAgentSlug,
    effectiveAgentSlug,
    availableSkills,
    projectSkills,
  });

  // Seed the override controls from the bound agent's brain for a NEW agent
  // conversation. Runs on first bind and whenever the agent changes (the
  // agent picker clears ``composerTouched``); a user override sets
  // ``composerTouched`` and is preserved until they switch agents. Existing
  // sessions are frozen (ADR-006), so this never runs for them.
  useEffect(() => {
    if (selectedSessionId) return;
    if (composerTouched) return;
    if (!selectedAgentBrain) return;
    if (selectedAgentBrain.runtime)
      setSelectedRuntimeId(selectedAgentBrain.runtime);
    setSelectedProviderId(selectedAgentBrain.providerId);
    if (selectedAgentBrain.model) setSelectedModelId(selectedAgentBrain.model);
    setSelectedEffort(selectedAgentBrain.effort);
  }, [selectedAgentBrain, composerTouched, selectedSessionId]);

  // Auto-pick the first available (provider, model) for the current
  // runtime so ``selectedProviderId`` / ``selectedModelId`` are never
  // null when the user hits Send. Without this, the backend falls back
  // to the seed-time ``is_default=ch-anthropic`` provider — which has no
  // API key configured — and every "send with default" attempt 422s.
  // Skips the auto-pick if a session already exists (its model is
  // frozen at creation per ADR-006) or if the user has explicitly
  // chosen a (provider, model) that's still in the filtered list.
  useEffect(() => {
    if (selectedSession) return;
    if (defaultsLoading) return;
    if (composerProviders.length === 0) return;
    const stillValid =
      selectedProviderId &&
      selectedModelId &&
      composerProviders.some(
        (m) =>
          m.providerId === selectedProviderId && m.modelId === selectedModelId,
      );
    if (stillValid) return;
    const preferred =
      composerProviders.find((m) => m.isDefault) ?? composerProviders[0];
    setSelectedProviderId(preferred.providerId);
    setSelectedModelId(preferred.modelId);
  }, [
    composerProviders,
    selectedProviderId,
    selectedModelId,
    selectedSession,
    defaultsLoading,
  ]);

  // Settles when the CURRENT history-window load has finished (success or
  // failure). ``refreshEvents`` resets the seq cursors synchronously and only
  // hydrates them (and ``events``) when its fetch lands — a stream opened in
  // that gap would capture ``afterSeq = 0`` and make the SSE replay the
  // session's whole history over the wire (pure waste; the uid dedup would
  // absorb it, but the transfer + churn are avoidable).
  // ``subscribeToSession`` awaits this before opening the stream.
  const historyHydrationRef = useRef<Promise<void>>(Promise.resolve());

  const { refreshEvents, loadOlderTurns, refreshActiveSession, bootstrap } =
    useConversationHistory({
      id,
      location,
      searchParams,
      panelSetCollapsed,
      selectedSessionIdRef,
      handoffSessionIdRef,
      currentClarifyingPendingRef,
      historyCursorRef,
      seenEventUidsRef,
      minSeqRef,
      hasMoreOlderRef,
      streamReconnectAttemptsRef,
      loadingOlderRef,
      userScrolledRef,
      scrollContainerRef,
      pendingScrollAnchorRef,
      isSendInFlightRef,
      promotingSessionIdRef,
      consumedPromoteSessionIdsRef,
      historyHydrationRef,
      setPendingUserMessage,
      setTurnStartAnchor,
      setEvents,
      setTodos,
      setWorkflowStates,
      setPendingApprovals,
      setHasMoreOlder,
      setLoadingOlder,
      setError,
      setLoading,
      // Owned by useProjectHandoff, destructured above this call — passed
      // directly (the old TDZ deferring wrapper is no longer needed).
      setDraftBootstrapSettled,
      setProjects,
      setSessionTriggerMode,
      setSessionAgentSlug,
      setSelectedProjectId,
      setSessions,
      setSelectedSessionId,
    });

  useEffect(() => {
    const request = bootstrapGuardRef.current.start();
    // Invalidate stale history/subscription writes as soon as the route effect
    // starts, rather than waiting for the new session detail request to land.
    selectedSessionIdRef.current = id === NEW_SESSION_ID ? null : id;
    void bootstrap(request.isCurrent);
    return request.cancel;
    // ``location.key`` changes even when the user clicks the currently-selected
    // history link again. Re-run bootstrap in that case so a failed/blank
    // hydration can recover without requiring a hard refresh.
  }, [bootstrap, id, location.key]);

  // Load all skills for composer autocomplete
  useEffect(() => {
    if (!selectedProjectId) return;
    skillsApi
      .list(selectedProjectId)
      .then((catalog) => {
        setAvailableSkills(catalog.skills);
      })
      .catch(() => {
        setAvailableSkills([]);
      });
  }, [selectedProjectId]);

  // Load the project's configured member agents. Project conversations
  // pick one of these (instead of a raw model); the composer renders them
  // as an Agent selector. Default the picker to the first agent.
  useEffect(() => {
    if (!selectedProjectId || activeProject?.kind !== "project") {
      setProjectAgents([]);
      return;
    }
    let cancelled = false;
    agentsApi
      .listMembers(selectedProjectId)
      .then((res) => {
        if (cancelled) return;
        setProjectAgents(res.agents);
        setSelectedAgentSlug((prev) => {
          if (prev && res.agents.some((a) => a.member.agent_slug === prev)) {
            return prev;
          }
          return res.agents[0]?.member.agent_slug ?? null;
        });
      })
      .catch(() => {
        if (!cancelled) setProjectAgents([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedProjectId, activeProject?.kind]);

  // 09-assistant: when the 📁 chip sits on 临时对话 (non-project project)
  // and no session exists yet, default the 🤖 chip to the first "我的" agent.
  // Empty library → null; handleSend then nudges the user to pick/create
  // (10-new-conversation-guidance). The project case is handled by the
  // listMembers effect above (first member).
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (selectedSession) return; // existing session is frozen (ADR-006)
    if (activeProject?.kind === "project") return; // project path elsewhere
    if (!myAgentsLoaded) return; // do not clear the old selection mid-switch
    setSelectedAgentSlug((prev) => {
      // A freshly-created agent handed off via ?agent= (「去临时对话」) wins as
      // soon as it appears in the reloaded roster.
      if (agentParam && myAgents.some((a) => a.slug === agentParam))
        return agentParam;
      if (prev && myAgents.some((a) => a.slug === prev)) return prev;
      // Skill-creator still needs an agent (its create flow binds one), so keep
      // the old defaulting there: last-used → Valurion → first library agent.
      if (isSkillCreatorMode) {
        const lastUsed = getLastTempAgent();
        if (lastUsed && myAgents.some((a) => a.slug === lastUsed))
          return lastUsed;
        if (myAgents.some((a) => a.slug === "valurion")) return "valurion";
        return myAgents[0]?.slug ?? null;
      }
      // A normal new conversation now defaults to NO agent — an agentless quick
      // chat on the global default model (the model-defaults effect seeds it).
      return null;
    });
    // Re-run only on the data that decides the default — existence of a
    // session (frozen), the project kind, the candidate roster, the explicit
    // ?agent= hand-off, and whether this is the skill-creator flow.
  }, [
    activeProject?.kind,
    myAgents,
    myAgentsLoaded,
    selectedSession,
    agentParam,
    isSkillCreatorMode,
  ]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // Load bound skills for project project context panel
  useEffect(() => {
    if (!selectedProjectId || activeProject?.kind !== "project") return;
    skillsApi
      .projectCatalog(selectedProjectId)
      .then((catalog) => {
        setProjectSkills(catalog.skills);
      })
      .catch(() => {
        setProjectSkills([]);
      });
  }, [selectedProjectId, activeProject?.kind]);

  // Auto-refresh on turn end: when the derived ``isBusy`` flips false, the
  // agent has just finished writing whatever artifacts it was going to. Pull
  // a fresh tree so the panel reflects new files without the user
  // having to switch projects or hit refresh manually. (``isBusy`` rather
  // than raw ``sending`` so a stuck flag can't suppress the refresh.)
  const prevBusyRef = useRef(isBusy);
  useEffect(() => {
    if (prevBusyRef.current && !isBusy) {
      refreshFileTree();
      // The agent may have called ``deliver_artifacts`` during the turn —
      // pull the fresh 生成文件 list alongside the file tree.
      void refreshArtifacts();
    }
    prevBusyRef.current = isBusy;
  }, [isBusy, refreshFileTree, refreshArtifacts]);

  // Loading server-side attachments on session change + polling parse status
  // is owned by ``useSessionAttachments`` above.

  const {
    handleOpenKbPicker,
    handleKbPickerConfirm,
    handleLocalFilesAttach,
    handleRemoveSessionAttachment,
    performSend,
    handleRetry,
  } = useConversationSend({
    id,
    selectedSession,
    selectedSessionId,
    selectedProjectId,
    activeProject,
    isSkillCreatorMode,
    skillKindParam,
    skillProjectParam,
    selectedAgentSlug,
    composerTouched,
    selectedProviderId,
    selectedModelId,
    selectedRuntimeId,
    selectedEffort,
    selectedPermissionMode,
    selectedMcpSlugs,
    selectedComposerSkill,
    draft,
    isBusy,
    turns,
    effectiveTurns,
    sessionAttachments,
    sidebarSessions,
    resolveExecTarget,
    attachKbDocs,
    attachLocalFiles,
    removeSessionAttachmentRow,
    markPendingConsumed,
    refreshEvents,
    refreshActiveSession,
    fetchSidebarSessions,
    setSidebarSessions,
    upsertProject,
    panelSetCollapsed,
    selectedSessionIdRef,
    skipNextSessionStateResetRef,
    projectSendHandoffRef,
    handoffSessionIdRef,
    promotingSessionIdRef,
    isSendInFlightRef,
    historyCursorRef,
    revealPanelOnSessionChangeRef,
    pinNextTurnToTopRef,
    keepCurrentTurnAtTopRef,
    interruptRef,
    setSelectedSessionId,
    setSessionAgentSlug,
    setSelectedProjectId,
    setSessions,
    setProjects,
    setEvents,
    setPendingUserMessage,
    setTurnStartAnchor,
    setSending,
    setError,
    setDraft,
    setSelectedComposerSkill,
    setRetryCounts,
    setKbPickerOpen,
    // Owned by useProjectHandoff, destructured above this call — passed
    // directly (the old TDZ deferring wrapper is no longer needed).
    handleSend,
  });

  const { subscribeToSession } = useSessionSubscription({
    abortRef,
    selectedSessionIdRef,
    seenEventUidsRef,
    historyCursorRef,
    streamReconnectAttemptsRef,
    historyHydrationRef,
    handoffSessionIdRef,
    currentClarifyingPendingRef,
    ruleIdToPreviewRef,
    // ``refreshQueueRef`` is declared further down this component (block-
    // scoped const) — a direct reference here would be a TDZ use-before-
    // declaration. The getter defers the read to call time, exactly as the
    // original in-component closure did.
    refreshQueueRef: {
      get current() {
        return refreshQueueRef.current;
      },
    },
    pendingApprovals,
    setEvents,
    setPendingUserMessage,
    setTodos,
    setWorkflowStates,
    setPendingApprovals,
    setAutoApprovedNotices,
    setSending,
    setSessions,
  });

  // (The send entry point itself — ``handleSend`` — lives in
  // useProjectHandoff, destructured above.)
  // ---- Session input queue (docs/design/session-input-queue.md) ----

  const {
    queue,
    queuePaused,
    queueDraining,
    queueDispatching,
    refreshQueueRef,
    performEnqueue,
    handleEditQueued,
    handleDeleteQueued,
    handleResumeQueue,
    handleSteerQueued,
  } = useInputQueue({
    selectedSessionId,
    selectedProviderId,
    selectedModelId,
    draft,
    isBusy,
    setDraft,
    setSelectedComposerSkill,
    refreshActiveSession,
    fetchSidebarSessions,
  });
  // Queue continuity for the LOADING AFFORDANCES only (shimmer / Stop /
  // timer): between two drained items the session is briefly idle — while the
  // host drain chain is in flight (``queueDraining``; authoritative, refreshed
  // at every boundary and by the backstop below) keep the affordances up
  // across those sub-second gaps. Display + send-routing only — the
  // turn-boundary effects (queue refetch, file-tree refresh, bookkeeping)
  // stay on raw ``isBusy`` because they must fire per drained turn.
  const displayBusy = isBusy || (queueDraining && !queuePaused);

  // ADR-013 approval / clarifying-question dispatchers — moved into the
  // hook (including the render-time ``askUserQuestionSubmitRef`` assignment).
  const { handleApprovalDecision } = useApprovalActions({
    selectedSessionId,
    currentClarifyingPendingRef,
    askUserQuestionSubmitRef,
    setPendingApprovals,
    setAskUserQuestionLocalAnswers,
  });

  // Scroll / virtualization cluster — the scroll-to-bottom affordance +
  // handler, the virtual-list API handoff, the send-time pin/anchor
  // effects, the upward pager's scroll restoration + top-sentinel
  // observer, streaming follow, the entry scroll, and the session-switch
  // ``sending`` release.
  const {
    showScrollBottom,
    containerHeight,
    handleScrollToBottom,
    handleTurnListVirtualApiReady,
  } = useConversationScroll({
    selectedSessionId,
    events,
    effectiveTurns,
    pendingUserMessage,
    hasMoreOlder,
    loadOlderTurns,
    scrollContainerRef,
    topSentinelRef,
    userScrolledRef,
    pendingScrollAnchorRef,
    pinNextTurnToTopRef,
    keepCurrentTurnAtTopRef,
    setSending,
  });

  // Session-open (todos hydration + stream open with the visibility /
  // connection-budget guard) and unmount stream teardown — extracted to
  // useSessionLifecycle.
  useSessionLifecycle({
    selectedSessionId,
    subscribeToSession,
    historyCursorRef,
    abortRef,
    selectedSessionIdRef,
    setTodos,
  });

  // Context panel — the JSX-producing ``contextPanelNode`` memo and the
  // layout-slot mount effect, moved into the hook with verbatim body and
  // dependency arrays (memoization semantics untouched).
  useContextPanel({
    id,
    isSkillCreatorMode,
    stagingSlugs,
    stagingRefreshing,
    stagingSyncing,
    refreshStaging,
    handleSyncStaging,
    activeProject,
    activeProjectRootPath,
    activeWorktree,
    selectedProjectId,
    selectedSession,
    selectedComposerSkill,
    availableSkills,
    sessionAttachments,
    sessionArtifacts,
    fileTree,
    projectKbTree,
    projectKbBindings,
    handleExpandProjectKbFolder,
    handleLocalFilesAttach,
    handleRemoveSessionAttachment,
    openArtifactFile,
    refreshFileTree,
    panelCollapsed,
    panelSetCollapsed,
    todos,
    setRightPanel,
    setHeader,
    setHideHeader,
  });

  // Synchronously reset all panel-driving data on session change.
  // ``fileTree`` / ``sessionAttachments`` / ``attachments`` are all
  // refreshed asynchronously (per-project fetch, per-session fetch,
  // user-queued composer state) so without this the right-panel
  // collapsed effect below would run a SECOND time with the previous
  // session's stale ``hasData = true`` and edge-trigger an unwanted
  // expand on the new empty session. Each downstream effect re-fetches
  // its own slice for the new session, so this only clears the visible
  // surface — no data is lost. ``todos`` is already cleared
  // synchronously by ``refreshEvents``.
  useEffect(() => {
    if (skipNextSessionStateResetRef.current) {
      skipNextSessionStateResetRef.current = false;
      return;
    }
    setSessionAttachments([]);
    setFileTree([]);
    closeArtifact();
  }, [closeArtifact, selectedSessionId, setSessionAttachments]);

  // Drive the right-panel collapsed state from per-session data:
  //   * Project projects always have meaningful panel content
  //     (instructions / skills / KB / file tree) — open by default,
  //     and re-open on every session switch within the project.
  //   * Chat projects start collapsed and auto-expand on the
  //     empty → has-data edge (todos / attachments / files). Manual
  //     collapses survive subsequent growth (we only edge-trigger).
  const isProjectProject = activeProject?.kind === "project";
  const prevSessionIdRef = useRef<string | null>(null);
  const prevHasDataRef = useRef(false);
  useEffect(() => {
    if (!shouldRevealPanelFromNavigation) return;
    revealPanelOnSessionChangeRef.current = true;
    panelSetCollapsed(false);
    navigate(`${location.pathname}${location.search}`, {
      replace: true,
      state: null,
    });
  }, [
    shouldRevealPanelFromNavigation,
    panelSetCollapsed,
    navigate,
    location.pathname,
    location.search,
  ]);

  useEffect(() => {
    const sid = selectedSessionId ?? "new";
    if (prevSessionIdRef.current !== sid) {
      const revealPanel = revealPanelOnSessionChangeRef.current;
      const waitingForRoutedSession =
        revealPanel && !selectedSessionId && id !== NEW_SESSION_ID;
      if (waitingForRoutedSession) {
        prevSessionIdRef.current = sid;
        prevHasDataRef.current = false;
        panelSetCollapsed(false);
        return;
      }
      revealPanelOnSessionChangeRef.current = false;
      prevSessionIdRef.current = sid;
      prevHasDataRef.current = false;
      panelSetCollapsed(!selectedSessionId);
      return;
    }
    if (isProjectProject) return;
    const hasData =
      (todos?.length ?? 0) > 0 ||
      sessionAttachments.length > 0 ||
      fileTree.length > 0;
    if (hasData && !prevHasDataRef.current) {
      panelSetCollapsed(false);
    }
    prevHasDataRef.current = hasData;
  }, [
    selectedSessionId,
    id,
    isProjectProject,
    todos,
    sessionAttachments,
    fileTree,
    panelSetCollapsed,
  ]);

  return (
    <>
      {/* The pane is always mounted with the conversation as its first column,
          so opening or closing a document never remounts the message list. */}
      <ArtifactSplitPane
        file={artifactFile}
        onReload={handleArtifactReload}
        onClose={handleArtifactClose}
        onCopyContent={handleArtifactCopy}
        onOpenExternal={handleArtifactOpenExternal}
      >
        <div className="relative flex h-full min-h-0 flex-col bg-surface">
          <ConversationHeader
            fromTaskId={fromTaskId}
            isSkillCreatorMode={isSkillCreatorMode}
            headerTitle={headerTitle}
            titleRenaming={titleRenaming}
            titleRenameValue={titleRenameValue}
            setTitleRenameValue={setTitleRenameValue}
            selectedSession={selectedSession}
            selectedSessionId={selectedSessionId}
            refreshActiveSession={refreshActiveSession}
            setTitleRenaming={setTitleRenaming}
            titleRenameWidth={titleRenameWidth}
            setTitleRenameWidth={setTitleRenameWidth}
            titleTriggerRef={titleTriggerRef}
            setTitleDeleting={setTitleDeleting}
            draftSendInFlight={draftSendInFlight}
            effectiveTurns={effectiveTurns}
            selectedProjectOrigin={selectedProjectOrigin}
            headerAgentSlug={headerAgentSlug}
            agentNameBySlug={agentNameBySlug}
            activeProject={activeProject}
          />

          <ConversationBody
            id={id}
            loading={loading}
            providers={providers}
            providerChannelState={providerChannelState}
            scrollContainerRef={scrollContainerRef}
            hasMoreOlder={hasMoreOlder}
            loadingOlder={loadingOlder}
            topSentinelRef={topSentinelRef}
            userScrolledRef={userScrolledRef}
            loadOlderTurns={loadOlderTurns}
            conversationInstanceKey={conversationInstanceKey}
            effectiveTurns={effectiveTurns}
            displayBusy={displayBusy}
            error={error}
            handleRetry={handleRetry}
            handleSwitchModel={handleSwitchModel}
            retryCounts={retryCounts}
            containerHeight={containerHeight}
            skillsBySlug={skillsBySlug}
            handleTurnListVirtualApiReady={handleTurnListVirtualApiReady}
            renderToolCall={renderToolCall}
            isToolCardFoldable={isToolCardFoldable}
            revealInFinder={revealInFinder}
            localFileLinks={localFileLinks}
            selectedSessionId={selectedSessionId}
            openCitation={openCitation}
            setDraft={setDraft}
            hasPendingProjectSend={hasPendingProjectSend}
            startingRuntime={startingRuntime}
          />

          <ApprovalTray
            pendingApprovals={pendingApprovals}
            autoApprovedNotices={autoApprovedNotices}
            handleApprovalDecision={handleApprovalDecision}
          />

          {/* Background-task strip — the turn that LAUNCHES a run_in_background
            command ends normally while the process keeps running for minutes;
            without this the conversation reads as "finished" with no cue that
            work is still in flight. Derived from persisted session.bg_task.*
            events (deriveBackgroundTasks), so it also survives re-entering the
            page mid-run; hides itself once every task reaches a terminal
            state (finished / stopped-on-runtime-close). */}
          <BackgroundTaskStrip tasks={runningBgTasks} />

          <ComposerPane
            showScrollBottom={showScrollBottom}
            handleScrollToBottom={handleScrollToBottom}
            displayBusy={displayBusy}
            selectedSession={selectedSession}
            rosterEmpty={rosterEmpty}
            channelLoaded={channelLoaded}
            hasChannel={hasChannel}
            channelsPending={channelsPending}
            agentPending={agentPending}
            setupPending={setupPending}
            refreshChannels={refreshChannels}
            refreshAgents={refreshAgents}
            createAgentOpen={createAgentOpen}
            setCreateAgentOpen={setCreateAgentOpen}
            setAgentLibraryRevision={setAgentLibraryRevision}
            setSelectedAgentSlug={setSelectedAgentSlug}
            setComposerTouched={setComposerTouched}
            selectedSessionId={selectedSessionId}
            queue={queue}
            isBusy={isBusy}
            queueDispatching={queueDispatching}
            queuePaused={queuePaused}
            handleEditQueued={handleEditQueued}
            handleDeleteQueued={handleDeleteQueued}
            handleResumeQueue={handleResumeQueue}
            handleSteerQueued={handleSteerQueued}
            conversationInstanceKey={conversationInstanceKey}
            draft={draft}
            setDraft={setDraft}
            isProjectProject={isProjectProject}
            effectiveAgentSlug={effectiveAgentSlug}
            handleSend={handleSend}
            interruptRef={interruptRef}
            sessionAttachments={sessionAttachments}
            handleRemoveSessionAttachment={handleRemoveSessionAttachment}
            composerAgents={composerAgents}
            sessionAgentSlug={sessionAgentSlug}
            selectedAgentSlug={selectedAgentSlug}
            execBarLocked={execBarLocked}
            sessionExecOrigin={sessionExecOrigin}
            execTargetId={execTargetId}
            setExecTargetId={setExecTargetId}
            setSelectedProviderId={setSelectedProviderId}
            setSelectedModelId={setSelectedModelId}
            projects={projects}
            selectedProjectId={selectedProjectId}
            setSelectedProjectId={setSelectedProjectId}
            setSelectedComposerSkill={setSelectedComposerSkill}
            execBarProjects={execBarProjects}
            providerTarget={providerTarget}
            panelSetCollapsed={panelSetCollapsed}
            composerProviders={composerProviders}
            selectedProviderId={selectedProviderId}
            selectedModelId={selectedModelId}
            composerRuntimes={composerRuntimes}
            selectedRuntimeId={selectedRuntimeId}
            setSelectedRuntimeId={setSelectedRuntimeId}
            selectedPermissionMode={selectedPermissionMode}
            setSelectedPermissionMode={setSelectedPermissionMode}
            isNewSession={isNewSession}
            id={id}
            selectedEffort={selectedEffort}
            setSelectedEffort={setSelectedEffort}
            modelSelectorUnlocked={modelSelectorUnlocked}
            selectedAgentSkillItems={selectedAgentSkillItems}
            composerMentionSkills={composerMentionSkills}
            availableSkills={availableSkills}
            handleOpenKbPicker={handleOpenKbPicker}
            handleLocalFilesAttach={handleLocalFilesAttach}
            connectorOptions={connectorOptions}
            selectedMcpSlugs={selectedMcpSlugs}
            toggleConnector={toggleConnector}
            parsingConfirmOpen={parsingConfirmOpen}
            setParsingConfirmOpen={setParsingConfirmOpen}
            performSend={performSend}
          />
        </div>
      </ArtifactSplitPane>

      <KbPickerOverlay
        kbPickerOpen={kbPickerOpen}
        pickerKbTree={pickerKbTree}
        pickerKbLoading={pickerKbLoading}
        pickerExpandFolder={pickerExpandFolder}
        sessionAttachments={sessionAttachments}
        handleKbPickerConfirm={handleKbPickerConfirm}
        setKbPickerOpen={setKbPickerOpen}
      />
      <DeleteConfirmDialog
        open={titleDeleting}
        onOpenChange={(open) => {
          if (!open && !titleDeleteInFlight) setTitleDeleting(false);
        }}
        itemName={
          selectedSession?.name ??
          (typeof headerTitle === "string" ? headerTitle : "")
        }
        loading={titleDeleteInFlight}
        onConfirm={handleTitleDeleteConfirm}
      />
    </>
  );
};

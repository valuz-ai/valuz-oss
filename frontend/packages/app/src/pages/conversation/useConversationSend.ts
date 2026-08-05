import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  ApiError,
  getEntityOrigin,
  projectsApi,
  recordEntityOrigin,
  refreshRunningRuns,
  resolveApiBase,
  sessionsApi,
  skillsApi,
  useTranslation,
  type ExecutionTarget,
  type ProjectDetail,
  type ProjectListItem,
  type RuntimeId,
  type SessionEventDTO,
  type SessionListItem,
  type SkillView,
  type UseSessionAttachmentsResult,
} from "@valuz/core";
import type { ConversationTurn } from "@valuz/shared";
import { t as _t } from "@valuz/shared/i18n";
import type { I18nKey } from "@valuz/shared";
import { resolveBrainOverride } from "../conversation-brain-override";
import { setLastTempAgent } from "../../lib/last-temp-agent";
import {
  NEW_SESSION_ID,
  appendUniqueEvents,
  isLocalUserInterruptEvent,
  makeLocalUserInterruptEvent,
  sessionDetailToListItem,
} from "./session-events";

type ConversationSendParams = {
  /** Route param (``/conversation/{id}``), defaulted to ``NEW_SESSION_ID``. */
  id: string;
  selectedSession: SessionListItem | null;
  selectedSessionId: string | null;
  selectedProjectId: string | null;
  activeProject: ProjectDetail | null;
  isSkillCreatorMode: boolean;
  skillKindParam: string | null;
  skillProjectParam: string | null;
  selectedAgentSlug: string | null;
  composerTouched: boolean;
  selectedProviderId: string | null;
  selectedModelId: string | null;
  selectedRuntimeId: RuntimeId | null;
  selectedEffort: "low" | "medium" | "high" | "xhigh" | "max" | null;
  selectedPermissionMode: "default" | "auto_review" | "full_access";
  selectedMcpSlugs: string[];
  selectedComposerSkill: SkillView | null;
  draft: string;
  /** Derived turn-activity flag (``deriveTurnActive``), computed in the page. */
  isBusy: boolean;
  turns: ConversationTurn[];
  /** Only ``.length`` is read (the draft-orphan check + its dep entry). */
  effectiveTurns: readonly unknown[];
  sessionAttachments: UseSessionAttachmentsResult["attachments"];
  sidebarSessions: SessionListItem[];
  resolveExecTarget: () => ExecutionTarget | undefined;
  attachKbDocs: UseSessionAttachmentsResult["attachKbDocs"];
  attachLocalFiles: UseSessionAttachmentsResult["attachLocalFiles"];
  removeSessionAttachmentRow: UseSessionAttachmentsResult["remove"];
  markPendingConsumed: UseSessionAttachmentsResult["markPendingConsumed"];
  refreshEvents: (sessionId: string | null) => Promise<void>;
  refreshActiveSession: (sessionId: string | null) => Promise<void>;
  fetchSidebarSessions: () => Promise<void>;
  setSidebarSessions: (sessions: SessionListItem[]) => void;
  upsertProject: (project: ProjectDetail) => void;
  panelSetCollapsed: (collapsed: boolean) => void;
  selectedSessionIdRef: { current: string | null };
  skipNextSessionStateResetRef: { current: boolean };
  projectSendHandoffRef: {
    current: {
      worktree?: { name?: string };
      permissionMode?: "default" | "auto_review" | "full_access";
    } | null;
  };
  handoffSessionIdRef: { current: string | null };
  promotingSessionIdRef: { current: string | null };
  isSendInFlightRef: { current: boolean };
  historyCursorRef: { current: number };
  revealPanelOnSessionChangeRef: { current: boolean };
  pinNextTurnToTopRef: { current: boolean };
  keepCurrentTurnAtTopRef: { current: boolean };
  interruptRef: { current: () => void };
  setSelectedSessionId: (id: string | null) => void;
  setSessionAgentSlug: (slug: string | null) => void;
  setSelectedProjectId: (id: string | null) => void;
  setSessions: Dispatch<SetStateAction<SessionListItem[]>>;
  setProjects: Dispatch<SetStateAction<ProjectListItem[]>>;
  setEvents: Dispatch<SetStateAction<SessionEventDTO[]>>;
  /** Narrower than the raw ``useState`` setters on purpose — the moved
   *  bodies only ever pass a full value (or ``null``), never an updater. */
  setPendingUserMessage: (
    value: {
      text: string;
      attachments: Array<{ name: string; size: number }>;
      fromSeq: number;
      sentAt: number; // Unix epoch ms (UTC)
    } | null,
  ) => void;
  setTurnStartAnchor: (
    value: {
      text: string;
      fromSeq: number;
      sentAt: number; // Unix epoch ms (UTC), client clock
    } | null,
  ) => void;
  setSending: (sending: boolean) => void;
  setError: (error: string | null) => void;
  setDraft: (draft: string) => void;
  setSelectedComposerSkill: (value: null) => void;
  setRetryCounts: Dispatch<SetStateAction<Record<string, number>>>;
  setKbPickerOpen: (open: boolean) => void;
  /** The send entry point (owned by useProjectHandoff). Only invoked from
   *  ``handleRetry``'s ``setTimeout``. */
  handleSend: () => void;
};

/**
 * ── Session mint + send pipeline ─────────────────────────────────────
 *
 * Owns the send-side verbs of the conversation page: ``ensureSession``
 * (mint-or-reuse, including the skill-creator launcher and multi-target
 * routing), the attachment/KB picker handlers, ``performSend`` (kept a
 * plain function so its per-render identity semantics stay identical),
 * ``handleInterrupt`` (plus the render-time ``interruptRef`` assignment),
 * and ``handleRetry``. Bodies are moved verbatim from ``ConversationPage``.
 */
export function useConversationSend({
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
  handleSend,
}: ConversationSendParams) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const ensureSession = useCallback(
    async (navigateOnCreate = false) => {
      if (selectedSession) return selectedSession;
      const sessionProjectId = selectedProjectId ?? "chat-default";
      // For quick-chat (kind="chat"), send the ``"chat-default"`` sentinel so
      // the backend allocates a fresh, isolated chat project + cwd for this
      // session. Project conversations keep passing their real project id.
      const isChat =
        sessionProjectId === "chat-default" || activeProject?.kind === "chat";
      // 09-assistant §2.1/§2.2: every session binds to an agent — project
      // conversations to the chosen 派驻 member, 临时对话 to the picked "我的"
      // agent. Skill-creator must bind an agent; a normal conversation may be
      // agentless (the create below sends ``agent_slug: undefined`` → backend
      // chat path).
      if (isSkillCreatorMode && !selectedAgentSlug) {
        throw new Error("No agent selected.");
      }
      // The bound agent OWNS the brain — provider/model/runtime/effort in this
      // create are ADR-006 overrides that beat it, so they travel only when the
      // user actually picked one here. See ``conversation-brain-override.ts``
      // for the two ways sending them unconditionally went wrong.
      const brainOverride = resolveBrainOverride({
        agentSlug: selectedAgentSlug,
        composerTouched,
        providerId: selectedProviderId,
        modelId: selectedModelId,
        runtimeId: selectedRuntimeId,
        effort: selectedEffort,
      });
      let created: Awaited<ReturnType<typeof sessionsApi.create>>;
      if (isSkillCreatorMode) {
        // Skill-creator draft: mint through the skills launcher so the
        // session carries trigger_meta + creation_context (the
        // ``submit_skill`` confirm flow reads them), bound to the agent
        // picked in the composer — same UX as 新对话.
        const start = await skillsApi.startCreate({
          context:
            skillKindParam === "project" && skillProjectParam
              ? { kind: "project", project_id: skillProjectParam }
              : { kind: skillKindParam === "chat" ? "chat" : "skills_library" },
          agent_slug: selectedAgentSlug,
          provider_id: brainOverride.provider_id,
          model_id: brainOverride.model_id,
        });
        created = await sessionsApi.get(start.session_id);
      } else {
        // Multi-target routing: a quick/temp chat runs on the target the
        // composer picker chose; a project conversation always follows its
        // project's observed origin. Single-target builds resolve both to
        // ``undefined`` → module-default base, unchanged behaviour.
        const chatTarget = isChat ? resolveExecTarget() : undefined;
        const projectOrigin = !isChat
          ? getEntityOrigin(sessionProjectId, "project")
          : undefined;
        const createBaseUrl = isChat
          ? chatTarget?.baseUrl
          : resolveApiBase({ projectId: sessionProjectId }, "") || undefined;
        // Connector picks still come from the local backend. The model list is
        // loaded from ``createBaseUrl`` above, so provider/model/runtime picks
        // are valid on the selected remote backend and must be preserved.
        const remoteCreate = chatTarget?.remote === true;
        created = await sessionsApi.create(
          {
            project_id: isChat ? "chat-default" : sessionProjectId,
            agent_slug: selectedAgentSlug ?? undefined,
            ...brainOverride,
            mcp_provider_slugs:
              !remoteCreate && selectedMcpSlugs.length > 0
                ? selectedMcpSlugs
                : undefined,
            permission_mode:
              projectSendHandoffRef.current?.permissionMode ??
              selectedPermissionMode,
            // What the project-detail composer had picked and this page would
            // otherwise answer with its OWN defaults: worktree (this page has
            // no such field at all) and permission mode (same name on both
            // pages, so dropping it raises no error — it just silently mints
            // the session with the wrong permission). Provider / model are
            // deliberately NOT carried: the project composer picks an AGENT,
            // not a model, so its ``selectedProviderId`` / ``selectedModelId``
            // are seeded from the project's last-used session, not from the
            // chosen agent. Forwarding them made the create override the
            // agent's own brain (backend ADR-006), so a project whose last
            // chat ran on another channel silently dragged every agent onto
            // it. The brain comes from ``agent_slug`` alone.
            // (Execution location is carried too, but through
            // ``recordEntityOrigin`` where the handoff is consumed, because
            // that is what ``getEntityOrigin`` / ``resolveApiBase`` read.)
            ...(projectSendHandoffRef.current?.worktree
              ? { worktree: projectSendHandoffRef.current.worktree }
              : {}),
          },
          createBaseUrl ? { baseUrl: createBaseUrl } : undefined,
        );
        const originTag = isChat ? chatTarget?.id : projectOrigin;
        if (originTag) {
          recordEntityOrigin(created.id, originTag);
          // A remote quick-chat mints a MANAGED project server-side. That
          // project appears in no local list (temp projects are list-hidden),
          // so no fan-out observation ever records its origin — without this
          // line every project-context fetch (detail / files / skills) routes
          // to the module-default backend and 404s, blanking the 云端对话 page.
          if (created.project_id && created.project_id !== "chat-default") {
            recordEntityOrigin(created.project_id, originTag);
          }
        }
      }
      // 10-new-conversation-guidance slice 3: remember which agent this 临时对话
      // used so the next new conversation pre-selects it.
      if (isChat && selectedAgentSlug) setLastTempAgent(selectedAgentSlug);
      // Update local state IMMEDIATELY (before navigate / sendMessage)
      // so the rest of ``handleSend`` and the SSE subscription closures
      // — which capture ``selectedProjectId`` and friends — see the
      // freshly minted ids. This is what the old ``refreshSessions``
      // race ultimately failed at.
      selectedSessionIdRef.current = created.id;
      if (id === NEW_SESSION_ID) {
        skipNextSessionStateResetRef.current = true;
      }
      setSelectedSessionId(created.id);
      // Seed the bound agent immediately. Attach-on-upload mints the session
      // WITHOUT navigating (``navigateOnCreate=false``), so the bootstrap effect
      // — which normally sets ``sessionAgentSlug`` from the fetched detail on a
      // URL change — never fires. Without this, the moment ``selectedSession``
      // turns truthy the composer flips ``selectedSession ? sessionAgentSlug :
      // selectedAgentSlug`` to a null agent and the picker looks deselected. The
      // session was just created with ``selectedAgentSlug``, so that is the
      // authoritative bound agent.
      setSessionAgentSlug(selectedAgentSlug);
      const createdItem = sessionDetailToListItem(created);
      setSessions([createdItem]);
      // Push the new session into the global session store IMMEDIATELY
      // so the sidebar's "New Chat" group renders it on this same render
      // tick — no waiting for the post-navigate ``fetchSidebarSessions``
      // round-trip. Same optimistic pattern the existing in-flight
      // message handler uses (around line 1450 below).
      setSidebarSessions(
        sidebarSessions.some((s) => s.id === createdItem.id)
          ? sidebarSessions.map((s) =>
              s.id === createdItem.id ? createdItem : s,
            )
          : [createdItem, ...sidebarSessions],
      );
      if (created.project_id !== selectedProjectId) {
        // Quick-chat: the backend just minted a fresh project + cwd
        // for this session. Pull the authoritative ``ProjectDetail``
        // (cwd lives on the backend — host writes flow through
        // ``fs_registry`` and we never derive it locally) and merge.
        try {
          const wsDetail = await projectsApi.get(created.project_id);
          setProjects((prev) => {
            const filtered = prev.filter((w) => w.id !== wsDetail.id);
            return [...filtered, wsDetail];
          });
          // Also push into the global project store so the layout's
          // "New Chat" filter (``allProjects.filter(kind === "chat")``)
          // immediately sees this row — without it, the new session
          // would still be filtered out of the chat group until the
          // path-change ``fetchProjects`` round-trip completes.
          upsertProject(wsDetail);
        } catch {
          /* non-fatal — file tree falls back gracefully */
        }
        setSelectedProjectId(created.project_id);
      }
      void fetchSidebarSessions();
      // Promote the URL from ``/conversation/new`` (or any other
      // fresh-entry path) to ``/conversation/{real-id}`` so the page
      // is no longer stuck on a sentinel. ``replace: true`` keeps the
      // back button from taking the user back to an empty draft.
      // Bootstrap re-fires under the new URL but its events refetch
      // is gated on ``sessionDetail.id !== selectedSessionIdRef.current``
      // — we just set the ref, so the in-flight SSE subscription is
      // not disturbed.
      // Attach-on-upload mints the session WITHOUT navigating (navigateOnCreate
      // = false): staying on ``/conversation/new`` until the user actually sends
      // mirrors the project-detail composer and avoids the navigate→bootstrap
      // churn that was dropping the freshly-attached file from the panel. The
      // send path navigates explicitly (see ``performSend``).
      if (id === NEW_SESSION_ID && navigateOnCreate) {
        navigate(
          `/conversation/${created.id}${isSkillCreatorMode ? "?mode=skill-creator" : ""}`,
          { replace: true },
        );
      }
      return created;
    },
    [
      selectedProjectId,
      activeProject?.kind,
      fetchSidebarSessions,
      sidebarSessions,
      setSidebarSessions,
      upsertProject,
      selectedSession,
      selectedProviderId,
      selectedModelId,
      selectedRuntimeId,
      selectedMcpSlugs,
      // ADR-013/ADR-006: ``selectedPermissionMode`` is read inside this
      // callback at session creation time. Without it in the deps array
      // the closure stays bound to the initial ``"full_access"`` value
      // and the chat-default session is silently created with the wrong
      // mode — the conversation page composer then reflects that wrong
      // value (locked) after navigate, which is the user-visible
      // "default permission picked, full access shown" regression.
      // Project-detail flow has no equivalent bug because its
      // ``handleSend`` is a plain function (no closure cache).
      selectedPermissionMode,
      selectedEffort,
      selectedAgentSlug,
      // Gates ``brainOverride``: a bound agent owns the brain until the user
      // picks a model for this conversation. Stale here = a user override
      // silently dropped (or an agent silently overridden).
      composerTouched,
      isSkillCreatorMode,
      skillKindParam,
      skillProjectParam,
      id,
      navigate,
      // Multi-target routing: the chosen execution target is read at
      // creation time — same closure-staleness trap as permission mode.
      resolveExecTarget,
    ],
  );

  const handleOpenKbPicker = useCallback(() => {
    // The ``useKbDocTree`` hook is gated on ``kbPickerOpen`` — opening
    // the picker triggers the tree fetch; no manual load needed here.
    setKbPickerOpen(true);
  }, []);

  const handleKbPickerConfirm = useCallback(
    async (ids: string[]) => {
      setKbPickerOpen(false);
      if (ids.length === 0) return;
      // KB picks land in the same session-attachment pipeline as local
      // uploads. The hook eager-mints the session if needed (new-conversation
      // entry), attaches each doc, re-reads the list, and starts polling the
      // async parse status — so the composer chips + panel show progress.
      try {
        // navigate:false — stay on /conversation/new while attaching.
        await attachKbDocs(ids, () => ensureSession(false));
      } catch {
        toast.error(t("common.failed" as Parameters<typeof t>[0]));
      }
    },
    [attachKbDocs, ensureSession, t],
  );

  // Local files upload the moment they're attached (drag-drop, file picker, or
  // the panel's upload button). The hook eager-mints the session if needed,
  // uploads each file, and polls its parse status for the progress UI.
  const handleLocalFilesAttach = useCallback(
    (files: File[]) => {
      // navigate:false — stay on /conversation/new while attaching.
      void attachLocalFiles(files, () => ensureSession(false));
    },
    [attachLocalFiles, ensureSession],
  );

  // Delete a persisted session attachment row (KB-sourced or local).
  // Hoisted so both the side panel's remove button and the composer's
  // pinned-chip remove button share it.
  const handleRemoveSessionAttachment = useCallback(
    async (attachmentId: string) => {
      await removeSessionAttachmentRow(attachmentId);
      // Attaching a file on a draft eagerly mints a session to hold the upload.
      // If the user removes the last file without ever sending a message, that
      // session is left behind as a statusless "New chat" orphan cluttering
      // Activity / recents (and, before the idempotent-delete fix, one the user
      // couldn't clear). Discard it: we're still on the draft URL, this was the
      // last attachment, and nothing was sent.
      const wasLastAttachment =
        sessionAttachments.filter((a) => a.id !== attachmentId).length === 0;
      if (
        id === NEW_SESSION_ID &&
        selectedSessionId &&
        effectiveTurns.length === 0 &&
        wasLastAttachment
      ) {
        const orphan = selectedSessionId;
        // Reset to a clean draft first so no per-session effect re-fetches the
        // session we're about to delete, then delete it best-effort.
        await refreshEvents(null);
        setSelectedSessionId(null);
        setSessions([]);
        void sessionsApi.delete(orphan).catch(() => {});
      }
    },
    [
      removeSessionAttachmentRow,
      sessionAttachments,
      id,
      selectedSessionId,
      effectiveTurns.length,
      refreshEvents,
    ],
  );

  // The actual send. Attachments are uploaded on attach, so this never
  // uploads — it just mints/reuses the session and posts the message.
  const performSend = async (overrideText?: string) => {
    // ``overrideText`` is the project-detail handoff: that page navigates here
    // before any session exists and lets this page mint + send, so its draft
    // arrives out of band rather than through the composer's state.
    const source = (overrideText ?? draft).trim();
    // Re-entrancy guard on the derived ``isBusy`` (not raw ``sending``): a
    // stuck ``sending`` on a reconciled-idle session must not swallow the send.
    if (!source || isBusy) return;
    // Skill-creator binds an agent (its create flow needs one) — nudge if none.
    // A normal new 临时对话 may now be agentless (a quick chat on the default
    // model), so it sends without an agent pick.
    if (!selectedSession && !selectedAgentSlug && isSkillCreatorMode) {
      toast.error(_t("conversation.selectAgentFirst" as I18nKey));
      return;
    }
    revealPanelOnSessionChangeRef.current = true;
    panelSetCollapsed(false);
    // ``draft`` already contains any inline ``/slug`` tokens because
    // Composer serializes its skill chips into the controlled value.
    // Don't prepend ``selectedComposerSkill`` again or the message
    // ships with ``/skill /skill ...``.
    const text = source;
    // Optimistic UI: clear the input and surface the message + a
    // "thinking" hint immediately so the user gets sub-frame feedback,
    // even while ensureSession + uploads + POST /messages are still
    // round-tripping. Without this the page sits idle for ~500-3000ms
    // depending on session creation + Claude SDK warm-up.
    // Attachments are already uploaded (on attach); the optimistic bubble
    // lists this turn's pending rows so chips show instantly.
    const queuedAttachmentMeta = sessionAttachments
      .filter((a) => !a.consumed_at)
      .map((a) => ({ name: a.filename, size: a.size_bytes }));
    pinNextTurnToTopRef.current = true;
    keepCurrentTurnAtTopRef.current = true;
    const sentAt = Date.now();
    setPendingUserMessage({
      text,
      attachments: queuedAttachmentMeta,
      fromSeq: historyCursorRef.current,
      sentAt,
    });
    setTurnStartAnchor({ text, fromSeq: historyCursorRef.current, sentAt });
    setSending(true);
    // Optimistically mark the active session running so the derived loading flag
    // shows immediately (an existing session re-send would otherwise read its
    // prior ``idle`` status for a beat and flicker). The real status is
    // reconciled from the POST response + SSE ``session.update`` right after.
    if (selectedSessionId) {
      setSessions((prev) =>
        prev.map((s) =>
          s.id === selectedSessionId ? { ...s, status: "running" } : s,
        ),
      );
    }
    setError(null);
    setDraft("");
    setSelectedComposerSkill(null);
    isSendInFlightRef.current = true;
    if (selectedComposerSkill) {
      console.debug(
        "[Conversation] skill selected:",
        selectedComposerSkill.name,
      );
    }
    try {
      const session = await ensureSession();
      // Protect the optimistic turn we just painted from the landing refresh.
      //
      // ``refreshEventsInner`` clears ``pendingUserMessage`` for any session it
      // is asked to load unless that session owns the pending, and bootstrap
      // runs it the moment we promote to /conversation/{id}. A plain 新对话
      // escapes it through bootstrap's promote fast-path; the project-detail
      // handoff waits for the project binding first, which shifts the timing
      // enough to miss that path — and the pending was wiped a beat after it
      // was set, taking the runtime-startup header down with it (the label
      // vanished and the row fell through to "已处理").
      //
      // Claiming the freshly minted id is what makes the guard recognise it.
      // The claim is released by the ``message.user`` echo, like any other.
      handoffSessionIdRef.current = session.id;
      if (!session?.id) throw new Error("Failed to create session.");
      // Land on the real session URL on SEND. ``ensureSession`` navigates
      // inline when it mints a brand-new session (no prior attach), but when
      // the session was pre-created by an attach (navigate:false — we stayed on
      // ``/conversation/new`` so the attachment panel/chips render without the
      // navigate→bootstrap churn) it returns cached without navigating, so do
      // the swap here. ``replace:true`` keeps Back from returning to the draft.
      if (id === NEW_SESSION_ID && session.id !== NEW_SESSION_ID) {
        promotingSessionIdRef.current = session.id;
        navigate(
          `/conversation/${session.id}${isSkillCreatorMode ? "?mode=skill-creator" : ""}`,
          {
            replace: true,
            state: {
              promotedFromNew: true,
              promotedSessionId: session.id,
            },
          },
        );
      }

      // Attachments were already uploaded (on attach) and the backend's
      // ``_run_agent_background`` reads this session's pending
      // SessionAttachmentRow rows, threading ``parsed_path`` (or the raw
      // ``stored_path`` when a parse hasn't finished) into kernel
      // ``UserMessage.attachments[]`` plus the ``additional-context`` hint —
      // so the prompt text stays clean. Nothing to upload here.

      // Prompt text is sent verbatim — no attachment hint appended.
      const outboundText = text;

      // No subscription here: the session-lifetime stream (opened by the
      // session-open effect, including right after the ``/conversation/new``
      // promotion above) carries this turn's events. ``sending`` bridges the
      // click → turn-start window; the turn's ``message.user`` echo on the
      // stream releases it.

      const detail = await sessionsApi.sendMessage(
        session.id,
        outboundText,
        selectedProviderId,
        selectedModelId,
      );
      if (!detail?.id) throw new Error("Failed to send message.");
      // The desktop sidebar's per-project session lists are derived from
      // ``/v1/runs`` (ProjectLayoutBase), NOT from the session store the
      // optimistic updates below write to — so without a poke here a brand-new
      // session only appears after the next 10s running poll (or a reload).
      // Force the shared poller now; the resulting ``liveRunIds`` transition
      // also triggers the layout's finished-runs refresh.
      refreshRunningRuns();
      // Attachments are per-turn: the backend ships this turn's pending
      // set with the message, then stamps those rows ``consumed_at`` once
      // the turn runs. Optimistically mark them consumed so they drop out
      // of the composer's staging chips immediately (they stay in the
      // panel's "uploaded files" history).
      markPendingConsumed();
      // ``send_message`` kicks the turn off in the BACKGROUND and returns
      // immediately — its status snapshot is stale-prone in BOTH directions:
      //  (a) taken before the kernel flips to "running" inside ``run_turn``
      //      → carries the PRE-turn "idle"/"created" (letting it through
      //      killed the whole loading UI for the turn: ``isBusy = sending &&
      //      !terminal(status)`` read a terminal status until a refresh);
      //  (b) taken mid-turn but delivered AFTER an ultra-fast turn already
      //      ended → carries a stale "running" that would resurrect the
      //      running pill on a finished turn (the terminal SSE frame that
      //      landed during the POST await already wrote "idle").
      // So the response never writes ``status`` for a row we already track:
      // the optimistic write at send start owns the turn's beginning and the
      // data-plane terminal frames (``session.update`` / ``session.idle``)
      // own its end. Only a row we DON'T have yet (fresh session) takes
      // "running" — a successful send means its turn is starting, and its
      // own terminal frames correct an instant failure.
      const startedSession: SessionListItem = {
        ...sessionDetailToListItem(detail),
        status: "running",
      };
      const keepLocalStatus = (s: SessionListItem): SessionListItem => ({
        ...startedSession,
        // Forward-upgrade only: a row still on the PRE-turn ``created`` takes
        // the send's ``running`` (a successful send means its turn is
        // starting — without this the header pill sat on 等待中 for the whole
        // first turn of a new session, since ``created`` isn't terminal and
        // nothing else wrote ``running`` mid-turn). Any other local status —
        // the optimistic ``running`` or a terminal frame that landed during
        // the POST await — stays authoritative, preserving both stale-status
        // protections documented above.
        status: s.status === "created" ? "running" : s.status,
      });
      setSessions((prev) =>
        prev.some((s) => s.id === startedSession.id)
          ? prev.map((s) =>
              s.id === startedSession.id ? keepLocalStatus(s) : s,
            )
          : [startedSession, ...prev],
      );
      setSidebarSessions(
        sidebarSessions.some((s) => s.id === startedSession.id)
          ? sidebarSessions.map((s) =>
              s.id === startedSession.id ? keepLocalStatus(s) : s,
            )
          : [startedSession, ...sidebarSessions],
      );
      setSelectedSessionId(detail.id);
    } catch (cause) {
      // The backend may attach an i18n key to a business error (structured
      // ``detail.key``) and let the client render it — e.g. a commercial
      // billing rejection. Prefer that key; otherwise show the raw message.
      const msg =
        cause instanceof ApiError && cause.i18nKey
          ? _t(
              cause.i18nKey as Parameters<typeof _t>[0],
              cause.i18nParams as Parameters<typeof _t>[1],
            )
          : cause instanceof Error
            ? cause.message
            : "Failed to send message.";
      setError(msg);
      toast.error(msg);
      setSending(false);
      pinNextTurnToTopRef.current = false;
      keepCurrentTurnAtTopRef.current = false;
      // Optimistic turn never got a real ``message.user`` echo because
      // the send failed — drop it so the user can retry without a
      // phantom card lingering. The session-lifetime stream stays up. The
      // anchor goes with it: there is no turn for it to stamp.
      setPendingUserMessage(null);
      setTurnStartAnchor(null);
      // The optimistic ``running`` status write above must be reconciled:
      // under the derived busy (``sendPending || status === "running"``) a
      // stale optimistic ``running`` would pin the loading state forever.
      if (selectedSessionId) void refreshActiveSession(selectedSessionId);
    } finally {
      isSendInFlightRef.current = false;
    }
  };

  const handleInterrupt = async () => {
    const sessionId = selectedSessionId;
    if (!sessionId) return;
    const afterSeq = historyCursorRef.current;
    // Do NOT tear the session-lifetime stream down: the cancel tail
    // (partial assistant text, ``session.idle`` with the interrupt stop
    // reason) arrives on it, and follow-up turns keep streaming. The
    // explicit gap-fill below just renders the tail without waiting on
    // stream latency; uid dedup absorbs the overlap.
    try {
      const detail = await sessionsApi.interrupt(sessionId);
      const missed = await sessionsApi
        .listEvents(sessionId, afterSeq)
        .catch(() => ({ items: [] as SessionEventDTO[] }));
      if (selectedSessionIdRef.current !== sessionId) return;
      if (
        missed.items.some((event) => event.event.event_type === "message.user")
      ) {
        setPendingUserMessage(null);
      }
      for (const event of missed.items) {
        if (event.seq > 0) {
          historyCursorRef.current = Math.max(
            historyCursorRef.current,
            event.seq,
          );
        }
      }
      setEvents((prev) => {
        const shouldAddLocalInterrupt = !prev.some(isLocalUserInterruptEvent);
        const incoming = shouldAddLocalInterrupt
          ? [...missed.items, makeLocalUserInterruptEvent()]
          : missed.items;
        return appendUniqueEvents(prev, incoming);
      });
      const updatedSession = sessionDetailToListItem(detail);
      setSessions((prev) =>
        prev.some((s) => s.id === updatedSession.id)
          ? prev.map((s) => (s.id === updatedSession.id ? updatedSession : s))
          : [updatedSession, ...prev],
      );
      void fetchSidebarSessions();
      setSending(false);
      toast.success(t("conversation.interrupted" as Parameters<typeof t>[0]));
    } catch (cause) {
      toast.error(
        cause instanceof Error
          ? cause.message
          : t("conversation.interruptFailed" as Parameters<typeof t>[0]),
      );
    }
  };

  interruptRef.current = handleInterrupt;

  const handleRetry = useCallback(
    (turnId: string) => {
      const turn = turns.find((t) => t.id === turnId);
      if (!turn) return;
      setRetryCounts((prev) => {
        const next = { ...prev, [turnId]: (prev[turnId] ?? 0) + 1 };
        return next;
      });
      setDraft(turn.userText);
      setTimeout(() => void handleSend(), 0);
    },
    [turns, handleSend],
  );

  return {
    ensureSession,
    handleOpenKbPicker,
    handleKbPickerConfirm,
    handleLocalFilesAttach,
    handleRemoveSessionAttachment,
    performSend,
    handleInterrupt,
    handleRetry,
  };
}

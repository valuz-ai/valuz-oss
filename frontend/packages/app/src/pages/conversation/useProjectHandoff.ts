import { useEffect, useRef, useState } from "react";
import { useNavigate, type Location } from "react-router-dom";
import { recordEntityOrigin } from "@valuz/core";
import { canSendProjectHandoff } from "../conversation-project-handoff";
import { dropHandoffFromHistory } from "../conversation-handoff-history";
import { NEW_SESSION_ID } from "./session-events";
import type { PermissionMode } from "./useComposerSelection";

type ProjectHandoffParams = {
  /** Route param (``/conversation/{id}``), defaulted to ``NEW_SESSION_ID``. */
  id: string;
  location: Location;
  searchParams: URLSearchParams;
  selectedProjectId: string | null;
  draft: string;
  /** True while any session attachment is still parsing. */
  attachmentsParsing: boolean;
  historyCursorRef: { current: number };
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
  setParsingConfirmOpen: (open: boolean) => void;
  /** ``displayBusy`` is derived below the hook call site in the page (it
   *  needs useInputQueue's returns), so it arrives as a deferring getter —
   *  read only inside ``handleSend``, at send time, exactly as the original
   *  in-component closure did. */
  getDisplayBusy: () => boolean;
  /** Declared below the hook call site in the page (useInputQueue return) —
   *  the page threads a deferring lambda, invoked only at send time. */
  performEnqueue: () => Promise<void>;
  /** Declared below the hook call site in the page (useConversationSend
   *  return) — the page threads a deferring lambda, invoked at send time. */
  performSend: (overrideText?: string) => Promise<void>;
};

/**
 * ── Project-detail handoff + send entry point ────────────────────────
 *
 * Owns the hand-over cluster of the conversation page: the optimistic
 * turn handed over by the project-detail composer (session-scoped
 * handoff refs + effect), the draft-first project send (its consumed
 * ref, the bootstrap-settled gate, the in-flight welcome suppression,
 * and the send-firing effect), and ``handleSend`` — the composer's
 * send entry point that routes between queueing and a direct send.
 * Bodies, comments and dependency arrays are moved verbatim from
 * ``ConversationPage``; the only body adaptations are
 * ``typeof selectedPermissionMode`` → the exported ``PermissionMode``
 * alias, and ``displayBusy`` → ``getDisplayBusy()``.
 */
export function useProjectHandoff({
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
  getDisplayBusy,
  performEnqueue,
  performSend,
}: ProjectHandoffParams) {
  const navigate = useNavigate();


  // Optimistic turn handed over by a page that minted the session itself (the
  // project-detail composer). That page navigates here the moment it has an id
  // and fires the send from its own closure, so all this does is SHOW the
  // message: it sets the same ``pendingUserMessage`` / ``turnStartAnchor``
  // pair a local send sets, which buys the bubble, the runtime-startup header
  // and the echo dedup for free. It must not send — see the note on
  // ``handoff`` in ProjectDetailPage.
  //
  // Consumed exactly once per session. The hash router restores
  // ``history.state`` across a reload, so without the seen-set a refresh would
  // resurrect a bubble for a turn that has long since landed in history.
  const consumedHandoffSessionIdsRef = useRef<Set<string>>(new Set());
  /** Oldest a handoff may be and still be the live one. See its use. */
  const HANDOFF_MAX_AGE_MS = 30_000;
  // Session-creation options the project-detail composer owns and this page
  // otherwise knows nothing about. Read by ``ensureSession`` while it mints
  // the handed-over session, so a project chat that asked for worktree
  // isolation still gets it. A ref, not state: ``ensureSession`` needs the
  // value in the same tick the send starts, before any re-render.
  const projectSendHandoffRef = useRef<{
    worktree?: { name?: string };
    permissionMode?: PermissionMode;
  } | null>(null);
  // Set while a handed-over pending is live, and read by ``refreshEventsInner``
  // so its unconditional "switching sessions invalidates the pending" clear
  // does not wipe a pending that belongs to the session being loaded. Bootstrap
  // runs that refresh on landing, i.e. always right after this seeds.
  const handoffSessionIdRef = useRef<string | null>(null);
  useEffect(() => {
    const handoff = (
      location.state as {
        handoff?: { text?: string; sentAt?: number };
      } | null
    )?.handoff;
    const text = handoff?.text?.trim();
    if (!text || id === NEW_SESSION_ID) return;
    if (consumedHandoffSessionIdsRef.current.has(id)) return;
    const sentAt = handoff?.sentAt ?? Date.now();
    // A reload restores ``history.state``, and the seen-set is a ref — it
    // resets with the page, so it cannot be what stops a replay. The state is
    // dropped from history below the moment it is consumed; this age check is
    // the belt to that braces, covering a restore that happens before the
    // replace lands. A genuine handoff is consumed within a frame or two of
    // the navigation, so anything older is a replay: re-seeding it re-showed
    // the startup label and re-armed ``sending`` on a turn that had long since
    // finished, leaving the stop button up on a settled session.
    if (Date.now() - sentAt > HANDOFF_MAX_AGE_MS) return;
    consumedHandoffSessionIdsRef.current.add(id);
    handoffSessionIdRef.current = id;
    setPendingUserMessage({
      text,
      attachments: [],
      fromSeq: historyCursorRef.current,
      sentAt,
    });
    setTurnStartAnchor({ text, fromSeq: historyCursorRef.current, sentAt });
    // The turn is genuinely in flight — the handing-over page has already
    // posted it. Released by the ``message.user`` echo like any other send.
    setSending(true);
    // Consume the state out of history so a reload cannot replay it. Only the
    // handing-over navigation sets ``handoff`` and it carries nothing else, so
    // clearing the whole entry is safe here.
    dropHandoffFromHistory();
  }, [id, location.state, location.pathname, location.search, navigate]);

  // Send entry point. While a turn is running, a follow-up is queued (drains
  // after the active turn). Otherwise it blocks on attachments still parsing —
  // the confirm dialog lets the user wait or submit with only the raw file.
  //
  // Routing MUST gate on the derived ``isBusy``, not the raw ``sending`` flag:
  // ``sending`` can stay stuck true after a missed terminal frame (the exact
  // case ``deriveTurnActive`` reconciles away for the DISPLAY). Gating here on
  // the raw flag made a follow-up on a visually-idle session silently detour
  // through the queue — the backend idle-kick drained it immediately (so it
  // ran), but a phantom queue bubble stayed pinned under the composer.
  const handleSend = () => {
    if (!draft.trim()) return;
    // Route on ``displayBusy`` (= isBusy OR an unpaused drain chain in
    // flight): between two drained items nothing is streaming (``isBusy``
    // false) but the backend still 409s a direct send (``is_draining_queue``
    // guards the gap) — a follow-up typed in that window must queue, exactly
    // as the Composer's queue affordance (also ``displayBusy``) advertises.
    if (getDisplayBusy()) {
      void performEnqueue();
      return;
    }
    if (attachmentsParsing) {
      setParsingConfirmOpen(true);
      return;
    }
    void performSend();
  };

  // Project-detail send handoff, draft-first form.
  //
  // That page used to await ``sessionsApi.create`` before it could navigate,
  // so a cloud project froze its composer for the whole round trip with no
  // feedback. 新对话 never had that problem because the user is ALREADY on
  // this page: ``performSend`` paints the optimistic turn first and mints the
  // session behind it. The project page now navigates to ``/conversation/new``
  // with nothing but the draft, which puts both entries on that same path.
  //
  // Placed after ``performSend`` so the reference is not a forward one.
  const consumedProjectSendRef = useRef(false);
  // Set when bootstrap's ``/conversation/new`` branch has run to completion.
  // The handoff waits on it because that branch binds the project BEFORE it
  // clears per-session state — sending in between created the optimistic turn
  // and then had it wiped, so the message went out with no bubble and no
  // runtime-startup header. Cleared whenever a fresh bootstrap starts.
  const [draftBootstrapSettled, setDraftBootstrapSettled] = useState(false);
  // True from the moment this page is entered by a project-detail send until
  // the send has produced its optimistic turn. Suppresses the new-chat
  // welcome for exactly that window.
  //
  // Two sources on purpose. The route state covers arrival and the wait for
  // bootstrap to bind the project; the explicit flag covers the handover
  // itself, because consuming the handoff CLEARS that state (it has to — a
  // reload must not replay the send) and the optimistic turn does not exist
  // yet at that instant. Deriving the flag from the state alone left exactly
  // one frame where neither held, and the welcome rendered in it.
  const [projectSendInFlight, setProjectSendInFlight] = useState(false);
  const hasPendingProjectSend =
    projectSendInFlight ||
    Boolean(
      (
        location.state as {
          projectSend?: { text?: string };
        } | null
      )?.projectSend?.text?.trim(),
    );
  useEffect(() => {
    if (id !== NEW_SESSION_ID) return;
    if (consumedProjectSendRef.current) return;
    const send = (
      location.state as {
        projectSend?: {
          text?: string;
          sentAt?: number;
          worktree?: { name?: string };
          permissionMode?: PermissionMode;
          projectId?: string;
          execOrigin?: string;
        };
      } | null
    )?.projectSend;
    const text = send?.text?.trim();
    if (!text) return;
    // Same reasoning as the session-scoped handoff: the hash router restores
    // ``history.state`` across a reload, and a ref cannot outlive one. The
    // state is dropped from history below; this bounds the window before that
    // lands, so a reload can never re-fire the send.
    if (Date.now() - (send?.sentAt ?? 0) > HANDOFF_MAX_AGE_MS) return;
    // WAIT for bootstrap to bind the project before sending.
    //
    // ``?project=`` is only turned into ``selectedProjectId`` after bootstrap
    // has fetched the project list and validated it, and ``ensureSession``
    // reads that state — not the URL. Firing the moment the route state
    // arrives therefore raced bootstrap and lost: ``selectedProjectId`` was
    // still null, so ``sessionProjectId`` fell back to ``"chat-default"``,
    // ``isChat`` went true, and the session was minted as a QUICK CHAT — not
    // bound to the project, and routed by the chat target picker (i.e. local)
    // rather than the project's own execution origin. Carrying the origin
    // observation could not help, because the lookup was keyed on
    // ``"chat-default"``.
    //
    // The ref is set below, at the point of no return, so an early bail here
    // leaves the handoff intact for the re-run that bootstrap triggers.
    if (
      !canSendProjectHandoff({
        projectParam: searchParams.get("project"),
        selectedProjectId,
        draftBootstrapSettled,
      })
    )
      return;
    consumedProjectSendRef.current = true;
    projectSendHandoffRef.current = {
      ...(send?.worktree ? { worktree: send.worktree } : {}),
      ...(send?.permissionMode ? { permissionMode: send.permissionMode } : {}),
    };
    // Execution location does not travel as a create field: ``ensureSession``
    // resolves a project conversation's target through ``getEntityOrigin`` /
    // ``resolveApiBase``. Seeding the observation is therefore what actually
    // routes the create at the right backend — without it a 云端 project
    // silently mints its session on the default one.
    if (send?.projectId && send?.execOrigin) {
      recordEntityOrigin(send.projectId, send.execOrigin);
    }
    dropHandoffFromHistory();
    // Held until the send settles, so the flag outlives the ``state: null``
    // navigation above; the ``finally`` also covers a failed send, which
    // would otherwise suppress the welcome on this page forever.
    setProjectSendInFlight(true);
    void performSend(text).finally(() => setProjectSendInFlight(false));
    // ``performSend`` is a plain function, so it changes identity every render;
    // listing it would re-run this effect on every frame. ``consumedProjectSendRef``
    // already makes re-entry impossible, and the effect only ever needs the
    // definition current at the moment the handoff lands.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    id,
    location.state,
    location.pathname,
    location.search,
    navigate,
    searchParams,
    selectedProjectId,
    draftBootstrapSettled,
  ]);

  return {
    projectSendHandoffRef,
    handoffSessionIdRef,
    setDraftBootstrapSettled,
    handleSend,
    hasPendingProjectSend,
  };
}

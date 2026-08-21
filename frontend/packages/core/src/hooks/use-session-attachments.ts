import { useCallback, useEffect, useRef, useState } from "react";

import { sessionsApi, type SessionAttachmentItem } from "../api/sessions-api";

/**
 * Mints (or returns) the session a freshly-attached file belongs to.
 *
 * Attachments upload **on attach**, not on send, so a session must already
 * exist before the first upload. For an ongoing conversation this just returns
 * the live session; for a brand-new / project conversation it eagerly creates
 * one (which, per ADR-006, freezes the model/agent/runtime — the composer
 * reflects that lock once a file is attached).
 */
export type EnsureSession = () => Promise<{ id: string }>;

export interface UseSessionAttachmentsResult {
  /** Every attachment row for the active session (pending + consumed). */
  attachments: SessionAttachmentItem[];
  /**
   * True while any *pending* (not-yet-sent) attachment is still being uploaded
   * or parsed. Callers gate Send on this to surface the "submit anyway?"
   * confirm. The upload window counts: a turn sent during it carries no
   * reference to the file at all, which is strictly worse than carrying an
   * unparsed one.
   */
  hasParsing: boolean;
  /** Upload local files immediately; eager-creates the session if needed. */
  attachLocalFiles: (
    files: File[],
    ensureSession: EnsureSession,
  ) => Promise<void>;
  /** Attach KB documents immediately; eager-creates the session if needed. */
  attachKbDocs: (
    docIds: string[],
    ensureSession: EnsureSession,
  ) => Promise<void>;
  /** Remove one attachment (optimistic drop + server delete). */
  remove: (attachmentId: string) => Promise<void>;
  /**
   * Optimistically stamp every pending row consumed — call right after a send
   * so the staging chips clear immediately (the turn marks them consumed
   * server-side only after it runs). Also records a consume watermark so rows
   * the server returns as still-pending AFTER this call land consumed too.
   *
   * ``sessionId``/``consumedAt`` override the watermark's session and moment
   * for callers consuming a send performed ELSEWHERE (the project-detail
   * handoff: that page POSTs the message itself and navigates; the landing
   * page's own ``sessionId`` may not have settled yet when it consumes the
   * handoff).
   */
  markPendingConsumed: (sessionId?: string, consumedAt?: number) => void;
  /** Escape hatch for callers that need to splice optimistic state directly. */
  setAttachments: React.Dispatch<React.SetStateAction<SessionAttachmentItem[]>>;
}

const POLL_INTERVAL_MS = 1000;

/** Marks a row that exists only in this hook — no server row behind it yet. */
const LOCAL_ID_PREFIX = "local:";
const isLocalPlaceholder = (a: SessionAttachmentItem): boolean =>
  a.id.startsWith(LOCAL_ID_PREFIX);

/** Unique within one page life, which is all a placeholder id has to be. */
let localSeq = 0;

/**
 * The row a file gets the instant it is attached, before anything is uploaded.
 *
 * ``parse_status: "parsing"`` is deliberate rather than a new ``"uploading"``
 * state. To the person watching, the two are one wait — the chip they want is
 * the spinner that already exists for parsing — and reusing it means the
 * upload window is covered by ``hasParsing`` too, so Send cannot slip a turn
 * out while the file is still going up. A distinct status would have to be
 * threaded through five call sites and a locale file to render the same chip
 * and would leave that hole open.
 */
const placeholderFor = (file: File): SessionAttachmentItem => ({
  id: `${LOCAL_ID_PREFIX}${(localSeq += 1)}`,
  // Unknown until ``ensureSession`` resolves; the merges below key off the id
  // prefix rather than this, so an empty session never drops the row.
  session_id: "",
  filename: file.name,
  stored_path: "",
  parse_status: "parsing",
  size_bytes: file.size,
  mime_type: file.type || null,
  created_at: Date.now(),
  source_kind: "local",
  consumed_at: null,
});

/**
 * Owns a session's attachment staging set: load-on-session-change, eager
 * upload-on-attach for local files + KB docs, and polling of the async parse
 * status (`parsing → ready | failed`).
 *
 * The backend parses uploads off the event loop in a background task and the
 * upload returns immediately as `parse_status="parsing"`; this hook polls
 * `GET /v1/sessions/{id}/attachments` until every row settles so the composer
 * and context panel can render live progress. A turn sent while a file is
 * still `parsing` ships only the raw file reference — the backend's path
 * picker / additional-context builder already degrade gracefully.
 *
 * Shared by the conversation, new-chat, and project-conversation composers.
 */
export function useSessionAttachments(
  sessionId: string | null,
): UseSessionAttachmentsResult {
  const [attachments, setAttachments] = useState<SessionAttachmentItem[]>([]);

  // Mirror of ``sessionId`` for callbacks that must read it at CALL time.
  // ``markPendingConsumed`` fires from a closure captured before the
  // project-send handoff promoted ``sessionId`` (null → real id), so reading
  // the prop directly would miss the session the send just consumed. (Synced
  // in an effect — the promote commits long before the send's POST resolves.)
  const sessionIdRef = useRef(sessionId);
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  // Last optimistic-consume moment, per session. ``markPendingConsumed`` can
  // only stamp rows that are ALREADY in local state — but on the project-send
  // handoff the send races the very first attachments load: the GET was
  // issued before the server stamped ``consumed_at`` (that happens once the
  // turn runs), so its still-pending rows land AFTER the local stamp — either
  // replacing stamped rows or arriving into a state the stamp never saw — and
  // the composer chips resurrect with nothing left to clear them. Recording
  // the consume moment lets every server-row application re-assert it.
  const consumeWatermarkRef = useRef<{ sessionId: string; ts: number } | null>(
    null,
  );

  // Placeholder ids the person removed while their upload was still in flight.
  // See ``remove`` — the upload can't be cancelled, so it is undone on arrival.
  const removedPlaceholdersRef = useRef<Set<string>>(new Set());

  // Re-assert the optimistic consume on a server row the server hasn't caught
  // up with yet: a still-pending row of the watermarked session uploaded
  // BEFORE the consume moment belongs to the already-sent turn (uploads
  // happen on attach, before send). A row attached after the send stays
  // pending — it ships with the next turn.
  const applyConsumeWatermark = useCallback(
    (row: SessionAttachmentItem): SessionAttachmentItem => {
      const wm = consumeWatermarkRef.current;
      if (!wm || row.consumed_at || row.session_id !== wm.sessionId) return row;
      if (row.created_at > wm.ts) return row;
      return { ...row, consumed_at: wm.ts };
    },
    [],
  );

  // Merge fresh server rows into local state WITHOUT clobbering an optimistic
  // ``consumed_at`` we stamped on send. The turn marks rows consumed
  // server-side only after it finishes running, so a poll fired between send
  // and turn-completion would otherwise un-consume the just-sent chips and
  // flash them back into the composer's staging row.
  const mergeServer = useCallback(
    (serverRows: SessionAttachmentItem[]) => {
      setAttachments((prev) => {
        const localById = new Map(prev.map((a) => [a.id, a]));
        const merged = serverRows.map((s) => {
          const local = localById.get(s.id);
          if (local?.consumed_at && !s.consumed_at) {
            return { ...s, consumed_at: local.consumed_at };
          }
          return applyConsumeWatermark(s);
        });
        // A file still uploading has no server row to be returned, and this
        // poll fires every second from the moment the session exists — so
        // taking the server's list verbatim would delete the chip the person
        // is watching, one tick after it appeared.
        return [...merged, ...prev.filter(isLocalPlaceholder)];
      });
    },
    [applyConsumeWatermark],
  );

  // Load on session change. CRITICAL: this races with ``attachLocalFiles`` in
  // the eager-create flow (the new-conversation composer sets ``sessionId`` to
  // the freshly-minted session, which fires this load *before* the upload has
  // committed its row — so the server returns an empty/stale list). We must NOT
  // let that stale result clobber a row the upload optimistically appended, or
  // the just-attached file silently vanishes from the composer + panel. So the
  // load MERGES: server rows win, but any optimistic row for THIS session that
  // the server hasn't returned yet is preserved. Rows from a previously-active
  // session (different ``session_id``) are dropped — this still behaves like a
  // replace across a real session switch.
  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<SessionAttachmentItem[]> => {
      if (!sessionId) return [];
      try {
        const res = await sessionsApi.listAttachments(sessionId);
        return res.items;
      } catch {
        return [];
      }
    };
    void load().then((items) => {
      if (cancelled) return;
      setAttachments((prev) => {
        // No session yet: nothing to load, but a file may already have been
        // attached — the new-conversation composer stages files precisely
        // while ``sessionId`` is still null, and the session is minted by the
        // upload that follows. Clearing here would erase the chip that was
        // just put up.
        if (!sessionId) return prev.filter(isLocalPlaceholder);
        const localById = new Map(prev.map((a) => [a.id, a]));
        const serverIds = new Set(items.map((r) => r.id));
        // Server rows win, but never backwards on ``consumed_at``: a send
        // that already stamped a row optimistically must survive a load that
        // was issued before the server caught up (project-send handoff), and
        // the consume watermark covers rows the stamp never saw.
        const merged = items.map((s) => {
          const local = localById.get(s.id);
          if (local?.consumed_at && !s.consumed_at) {
            return { ...s, consumed_at: local.consumed_at };
          }
          return applyConsumeWatermark(s);
        });
        // Placeholders are kept regardless of ``session_id``: this effect runs
        // the moment ``ensureSession`` promotes null → the new id, which is
        // BEFORE the upload that will tell the placeholder which session it
        // belongs to. Matching on session would drop it at exactly that
        // moment, and the chip would blink out mid-upload.
        const optimistic = prev.filter(
          (a) =>
            !serverIds.has(a.id) &&
            (a.session_id === sessionId || isLocalPlaceholder(a)),
        );
        return [...merged, ...optimistic];
      });
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId, applyConsumeWatermark]);

  const hasParsing = attachments.some(
    (a) => !a.consumed_at && a.parse_status === "parsing",
  );

  // Poll while ANY row is still parsing. The optimistic append (attachLocalFiles)
  // puts the freshly-uploaded ``parsing`` row into local state, so this fires
  // immediately on attach and keeps the composer/panel progress live until every
  // row settles. (No grace-poll needed: the new-conversation composer stays on
  // /conversation/new instead of navigating, so the optimistic row is never
  // dropped by a navigate→bootstrap reset.)
  const anyParsing = attachments.some((a) => a.parse_status === "parsing");
  useEffect(() => {
    if (!sessionId || !anyParsing) return;
    let cancelled = false;
    const handle = setInterval(() => {
      sessionsApi
        .listAttachments(sessionId)
        .then((res) => {
          if (!cancelled) mergeServer(res.items);
        })
        .catch(() => {
          /* transient — next tick retries */
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [sessionId, anyParsing, mergeServer]);

  const attachLocalFiles = useCallback(
    async (files: File[], ensureSession: EnsureSession) => {
      if (files.length === 0) return;

      // Show the files FIRST. ``ensureSession`` can take seconds — in cloud
      // mode it allocates a sandbox — and the upload seconds more, and until
      // this change every one of those seconds looked to the person like their
      // drop had been ignored: no chip, no spinner, nothing. The row they see
      // now is local; the upload below swaps in the server's.
      const staged = files.map((file) => [file, placeholderFor(file)] as const);
      setAttachments((prev) => [...prev, ...staged.map(([, row]) => row)]);

      const drop = (id: string) =>
        setAttachments((prev) => prev.filter((a) => a.id !== id));

      let session: { id: string };
      try {
        session = await ensureSession();
      } catch (cause) {
        // No session means no upload for ANY of them — leaving the chips up
        // would promise a turn that cannot carry them.
        for (const [, row] of staged) drop(row.id);
        throw cause;
      }

      for (const [file, row] of staged) {
        try {
          const item = await sessionsApi.uploadAttachment(session.id, file);
          if (removedPlaceholdersRef.current.delete(row.id)) {
            void sessionsApi.deleteAttachment(session.id, item.id).catch(() => {
              /* best-effort; the row is at worst an unreferenced file */
            });
            continue;
          }
          setAttachments((prev) => {
            if (prev.some((a) => a.id === item.id))
              return prev.filter((a) => a.id !== row.id);
            // Swap in place rather than drop-and-append: the chip keeps its
            // position in the row, so a multi-file attach doesn't reshuffle
            // itself as each upload lands.
            return prev.map((a) => (a.id === row.id ? item : a));
          });
        } catch {
          /* best-effort; the caller surfaces an upload-failed toast */
          drop(row.id);
        }
      }
    },
    [],
  );

  const attachKbDocs = useCallback(
    async (docIds: string[], ensureSession: EnsureSession) => {
      if (docIds.length === 0) return;
      const session = await ensureSession();
      await sessionsApi.addKbAttachments(session.id, docIds);
      // Re-read the full list (the KB endpoint returns pending-only); merge so
      // panel history + optimistic consume survive.
      const res = await sessionsApi.listAttachments(session.id);
      mergeServer(res.items);
    },
    [mergeServer],
  );

  const remove = useCallback(
    async (attachmentId: string) => {
      setAttachments((prev) => prev.filter((a) => a.id !== attachmentId));
      // A placeholder has no server row yet, so DELETE would 404 on an id the
      // server has never seen. The in-flight upload cannot be cancelled, so
      // record the intent instead: when it lands, ``attachLocalFiles`` deletes
      // the row it just created. Without that, a file the person visibly
      // removed would still ship with the turn.
      if (attachmentId.startsWith(LOCAL_ID_PREFIX)) {
        removedPlaceholdersRef.current.add(attachmentId);
        return;
      }
      if (!sessionId) return;
      try {
        await sessionsApi.deleteAttachment(sessionId, attachmentId);
      } catch {
        /* best-effort */
      }
    },
    [sessionId],
  );

  const markPendingConsumed = useCallback(
    (sessionIdOverride?: string, consumedAt?: number) => {
      const ts = consumedAt ?? Date.now();
      const sid = sessionIdOverride ?? sessionIdRef.current;
      if (sid) consumeWatermarkRef.current = { sessionId: sid, ts };
      setAttachments((prev) =>
        prev.map((a) => (a.consumed_at ? a : { ...a, consumed_at: ts })),
      );
    },
    [],
  );

  return {
    attachments,
    hasParsing,
    attachLocalFiles,
    attachKbDocs,
    remove,
    markPendingConsumed,
    setAttachments,
  };
}

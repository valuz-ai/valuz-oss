import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { sessionsApi, type SessionAttachmentItem } from "../api/sessions-api";


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
  /** Upload local files immediately. No session required, or created. */
  attachLocalFiles: (files: File[]) => Promise<void>;
  /** Attach KB documents immediately. No session required, or created. */
  attachKbDocs: (docIds: string[]) => Promise<void>;
  /**
   * Ids of the staged attachments, for callers that only need to look.
   *
   * NOT what a send should pass: this is a render value, and a send spans
   * several awaits during which an upload can land. Use the ids
   * ``markPendingConsumed`` returns — it reads the set at the moment it
   * consumes it.
   */
  pendingIds: string[];
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
  markPendingConsumed: (sessionId?: string, consumedAt?: number) => string[];
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
  // NULL until a turn claims it. The merges below key off the id prefix, so
  // an unbound row is never mistaken for a stale one.
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
  /**
   * Where staged reads and uploads go. There is no session to route on before
   * the first send, so the caller names the backend — the same one the eventual
   * session will be created on. Omitted → the module default.
   */
  baseUrl?: string,
): UseSessionAttachmentsResult {
  const readOpts = useMemo(() => (baseUrl ? { baseUrl } : undefined), [baseUrl]);
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
    // No session → this owner's STAGING set (uploaded, not yet claimed by a
    // turn), which is what a composer shows before its first send and what it
    // restores from after a reload. With a session → that conversation's own
    // attachments. Two questions, one hook, because a composer becomes the
    // second the moment it sends.
    const load = async (): Promise<SessionAttachmentItem[]> => {
      try {
        const res = sessionId
          ? await sessionsApi.listAttachments(sessionId)
          : await sessionsApi.listStagedAttachments(readOpts);
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
        // ``items`` is authoritative for whichever set was read.
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
        // Placeholders are kept regardless of ``session_id``: a chip goes up
        // before its upload lands, so a read that arrives in between must not
        // take it back down.
        // Optimistic rows the server has not returned yet: a placeholder put
        // up before its upload landed, and a server row appended by an upload
        // whose response beat a list read that was already in flight.
        //
        // Scoped to the set being READ, or a session switch would carry the
        // previous conversation's files into the next one — the load is also
        // what CLEARS on a switch, and a filter that keeps everything cannot
        // clear anything.
        const belongsHere = (a: SessionAttachmentItem) =>
          sessionId ? a.session_id === sessionId : !a.session_id;
        const optimistic = prev.filter(
          (a) => !serverIds.has(a.id) && (belongsHere(a) || isLocalPlaceholder(a)),
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
    if (!anyParsing) return;
    let cancelled = false;
    const handle = setInterval(() => {
      (sessionId
        ? sessionsApi.listAttachments(sessionId)
        : sessionsApi.listStagedAttachments(readOpts)
      )
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
    async (files: File[]) => {
      if (files.length === 0) return;

      // Show the files FIRST. The upload still takes a moment (it writes to
      // the owner's store), and until the chip exists a drop looks ignored.
      // The row they see is local; the upload below swaps in the server's.
      const staged = files.map((file) => [file, placeholderFor(file)] as const);
      setAttachments((prev) => [...prev, ...staged.map(([, row]) => row)]);

      const drop = (id: string) =>
        setAttachments((prev) => prev.filter((a) => a.id !== id));

      for (const [file, row] of staged) {
        try {
          const item = await sessionsApi.uploadAttachment(file, readOpts);
          if (removedPlaceholdersRef.current.delete(row.id)) {
            void sessionsApi.deleteAttachment(item.id, readOpts).catch(() => {
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
    [readOpts],
  );

  const attachKbDocs = useCallback(
    async (docIds: string[]) => {
      if (docIds.length === 0) return;
      // The endpoint returns the owner's staging set, which is what a composer
      // shows — no second read needed.
      const { items } = await sessionsApi.addKbAttachments(docIds, readOpts);
      mergeServer(items);
    },
    [mergeServer, readOpts],
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
      try {
        await sessionsApi.deleteAttachment(attachmentId, readOpts);
      } catch {
        /* best-effort */
      }
    },
    [readOpts],
  );

  const markPendingConsumed = useCallback(
    (sessionIdOverride?: string, consumedAt?: number): string[] => {
      const ts = consumedAt ?? Date.now();
      const sid = sessionIdOverride ?? sessionIdRef.current;
      if (sid) consumeWatermarkRef.current = { sessionId: sid, ts };
      // Returns what it stamped, and reads it from INSIDE the updater — the
      // only place with the current set.
      //
      // The caller needs these ids to bind the files to the turn, and deriving
      // them from a render value instead is a race it loses often: a send is
      // several awaits long (session create, navigation), and an upload that
      // lands in the middle is invisible to a list captured before it. Attach
      // a file and hit send immediately and the closure still holds the local
      // placeholder — filtered out, so the turn goes out claiming nothing.
      //
      // Consuming and knowing-what-you-consumed are the same act; splitting
      // them is what allowed them to disagree.
      const claimed: string[] = [];
      setAttachments((prev) => {
        claimed.length = 0;
        for (const a of prev) {
          if (!a.consumed_at && !isLocalPlaceholder(a)) claimed.push(a.id);
        }
        return prev.map((a) => (a.consumed_at ? a : { ...a, consumed_at: ts }));
      });
      return claimed;
    },
    [],
  );

  const pendingIds = attachments
    .filter((a) => !a.consumed_at && !isLocalPlaceholder(a))
    .map((a) => a.id);

  return {
    attachments,
    hasParsing,
    pendingIds,
    attachLocalFiles,
    attachKbDocs,
    remove,
    markPendingConsumed,
    setAttachments,
  };
}

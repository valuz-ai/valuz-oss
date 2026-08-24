import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { sessionsApi, type SessionAttachmentItem } from "../api/sessions-api";

const POLL_INTERVAL_MS = 1000;

/** Marks a row that exists only in this hook — no server row behind it yet. */
const LOCAL_ID_PREFIX = "local:";
const isPlaceholder = (a: SessionAttachmentItem): boolean =>
  a.id.startsWith(LOCAL_ID_PREFIX);

/** Unique within one page life, which is all a placeholder id has to be. */
let localSeq = 0;

/**
 * The row a file gets the instant it is attached, before anything is uploaded.
 *
 * ``parse_status: "parsing"`` rather than a distinct ``"uploading"``: to the
 * person watching, the two are one wait, and the spinner chip already exists
 * for parsing.
 */
const placeholderFor = (file: File): SessionAttachmentItem => ({
  id: `${LOCAL_ID_PREFIX}${(localSeq += 1)}`,
  session_id: null,
  filename: file.name,
  stored_path: "",
  parse_status: "parsing",
  size_bytes: file.size,
  mime_type: file.type || null,
  created_at: Date.now(),
  source_kind: "local",
  consumed_at: null,
});

export interface UseStagedAttachmentsResult {
  /** Files staged for the next turn: uploaded-but-unclaimed, plus in-flight. */
  attachments: SessionAttachmentItem[];
  /**
   * Files a turn has claimed but the server has not confirmed bound yet.
   *
   * A file's life is ``staged → in-flight → bound``, and this is the middle
   * one. It used to live only in a local variable inside the send, so the
   * conversation's file panel — which shows staged plus bound — could see
   * neither for the whole length of the send. On a cloud project that is a
   * session-create plus a message POST, and the panel sat empty for seconds
   * while the message bubble beside it already showed the file.
   *
   * Not part of ``attachments``: the composer must not offer to send a file
   * that is already on its way.
   */
  inFlight: SessionAttachmentItem[];
  /** True while any staged file is still uploading or parsing. */
  hasPending: boolean;
  attachLocalFiles: (files: File[]) => Promise<void>;
  attachKbDocs: (docIds: string[]) => Promise<void>;
  remove: (attachmentId: string) => Promise<void>;
  /**
   * Take the staged files out and hand them to a turn.
   *
   * Returns the rows so the caller can pass their ids to
   * ``sessionsApi.sendMessage`` and put them back with ``restage`` if the send
   * fails. Reads a ref, so the answer does not depend on when React last
   * rendered — a send is several awaits long and this is called in the middle
   * of it.
   *
   * Placeholders are left behind: an upload still in flight has no server row
   * to bind, and it stays staged for the next turn rather than being lost.
   */
  claim: () => SessionAttachmentItem[];
  /** Put claimed rows back — the send they were claimed for did not happen. */
  restage: (rows: SessionAttachmentItem[]) => void;
  /**
   * Take on files another page already sent, so this one can show them.
   *
   * The project composer posts and navigates without waiting, so the arriving
   * conversation has files it did not stage and cannot yet read back. Not
   * ``restage``: these are spent. They are in flight, and the conversation's
   * own list takes over the moment it can see them.
   */
  adopt: (rows: SessionAttachmentItem[]) => void;
  /**
   * Let claimed rows go: the bind is durable, so the conversation's own list
   * owns them now. Until this is called they stay in ``inFlight`` and the
   * panel keeps showing them.
   */
  settle: (rows: Array<{ id: string }>) => void;
}

/**
 * The composer's staging set: files uploaded but not yet claimed by a turn.
 *
 * **Owner-scoped, and deliberately blind to sessions.** An attachment uploads
 * with no session (``POST /v1/attachments``) and is bound by the turn that
 * ships it, so nothing here needs a session id — and giving it one is what
 * previously broke it: the composer's send creates the session, which used to
 * re-key this state mid-send and drop every staged row on the floor, so the
 * turn went out claiming nothing. The set a person is looking at does not
 * change because a session was born; this hook now cannot express that it did.
 *
 * The session's own attachment history is a different question with a
 * different owner — see ``useSessionAttachments``, which is read-only.
 */
export function useStagedAttachments(
  /**
   * Where uploads and reads go. There is no session to route on, so the caller
   * names the backend — the same one its turn will run on. Omitted → the
   * module default.
   */
  baseUrl?: string,
): UseStagedAttachmentsResult {
  const opts = useMemo(() => (baseUrl ? { baseUrl } : undefined), [baseUrl]);
  const [attachments, setState] = useState<SessionAttachmentItem[]>([]);
  // Claimed, sent, not yet confirmed bound. Plain state: unlike the staged set
  // nothing reads this mid-await — it exists to be rendered.
  const [inFlight, setInFlight] = useState<SessionAttachmentItem[]>([]);

  // The ref is the authority; the state is its render mirror. Every mutation
  // here happens in an async callback — an upload resolving, a poll tick, a
  // send claiming — and ``claim`` has to answer from the current set, not from
  // whatever the last render saw.
  const ref = useRef<SessionAttachmentItem[]>([]);
  const write = useCallback(
    (next: (prev: SessionAttachmentItem[]) => SessionAttachmentItem[]) => {
      ref.current = next(ref.current);
      setState(ref.current);
    },
    [],
  );

  /** Ids the person removed while their upload was still in flight. */
  const removedRef = useRef<Set<string>>(new Set());

  // The ids THIS composer put up.
  //
  // Staging is owner-scoped on the server — an attachment has no session and
  // no composer, which is what lets it be uploaded before either exists. But
  // "everything this owner has staged" is not what a composer is holding: with
  // a quick chat and a project chat open, each would show the other's files.
  // So the server answers per owner and this narrows it to its own, exactly
  // like the text draft beside it, which is component state and shared with
  // nobody.
  //
  // The consequence is deliberate: a reload loses the chips, as it loses the
  // typed text. The rows stay on the server as unclaimed staging and fall to
  // the per-owner cap. Restoring them instead is what leaked them sideways.
  const mineRef = useRef<Set<string>>(new Set());

  /** Server rows for files THIS composer staged; placeholders ride along. */
  const merge = useCallback(
    (serverRows: SessionAttachmentItem[]) => {
      write((prev) => [
        ...serverRows.filter((r) => mineRef.current.has(r.id)),
        ...prev.filter(isPlaceholder),
      ]);
    },
    [write],
  );

  const hasPending = attachments.some((a) => a.parse_status === "parsing");

  useEffect(() => {
    if (!hasPending) return;
    let cancelled = false;
    const handle = setInterval(() => {
      sessionsApi
        .listStagedAttachments(opts)
        .then(({ items }) => {
          if (!cancelled) merge(items);
        })
        .catch(() => {
          /* transient — next tick retries */
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [hasPending, opts, merge]);

  const attachLocalFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;

      // Show the files FIRST: the upload takes a moment and until the chip
      // exists a drop looks ignored. The upload below swaps in the server row.
      const staged = files.map((file) => [file, placeholderFor(file)] as const);
      write((prev) => [...prev, ...staged.map(([, row]) => row)]);

      for (const [file, row] of staged) {
        try {
          const item = await sessionsApi.uploadAttachment(file, opts);
          if (removedRef.current.delete(row.id)) {
            // Removed while in flight. The upload could not be cancelled, so
            // undo it — otherwise a file the person watched themselves remove
            // would still ship with the turn.
            void sessionsApi.deleteAttachment(item.id, opts).catch(() => {
              /* best-effort; at worst an unreferenced file */
            });
            continue;
          }
          mineRef.current.add(item.id);
          // Swap in place so a multi-file attach does not reshuffle itself as
          // each upload lands.
          write((prev) => prev.map((a) => (a.id === row.id ? item : a)));
        } catch {
          /* best-effort; the caller surfaces an upload-failed toast */
          write((prev) => prev.filter((a) => a.id !== row.id));
        }
      }
    },
    [opts, write],
  );

  const attachKbDocs = useCallback(
    async (docIds: string[]) => {
      if (docIds.length === 0) return;
      const { items } = await sessionsApi.addKbAttachments(docIds, opts);
      // The endpoint answers with the rows it just created, so every one of
      // them is this composer's.
      for (const r of items) mineRef.current.add(r.id);
      merge([...ref.current.filter((a) => !isPlaceholder(a)), ...items]);
    },
    [merge, opts],
  );

  const remove = useCallback(
    async (attachmentId: string) => {
      write((prev) => prev.filter((a) => a.id !== attachmentId));
      mineRef.current.delete(attachmentId);
      if (isPlaceholder({ id: attachmentId } as SessionAttachmentItem)) {
        // No server row yet — DELETE would 404 on an id it has never seen.
        // Recorded so the in-flight upload undoes itself on arrival.
        removedRef.current.add(attachmentId);
        return;
      }
      try {
        await sessionsApi.deleteAttachment(attachmentId, opts);
      } catch {
        /* best-effort */
      }
    },
    [opts, write],
  );

  const claim = useCallback((): SessionAttachmentItem[] => {
    const claimed = ref.current.filter((a) => !isPlaceholder(a));
    if (claimed.length === 0) return [];
    for (const a of claimed) mineRef.current.delete(a.id);
    write((prev) => prev.filter(isPlaceholder));
    // Out of the composer, but not yet the conversation's: hold them so the
    // panel has something to show across the send. ``settle`` or ``restage``
    // takes them from here.
    setInFlight((prev) => [...prev, ...claimed]);
    return claimed;
  }, [write]);

  const adopt = useCallback((rows: SessionAttachmentItem[]) => {
    if (rows.length === 0) return;
    setInFlight((prev) => {
      const have = new Set(prev.map((a) => a.id));
      return [...prev, ...rows.filter((r) => !have.has(r.id))];
    });
  }, []);

  const settle = useCallback((rows: Array<{ id: string }>) => {
    if (rows.length === 0) return;
    const done = new Set(rows.map((r) => r.id));
    setInFlight((prev) => prev.filter((a) => !done.has(a.id)));
  }, []);

  const restage = useCallback(
    (rows: SessionAttachmentItem[]) => {
      if (rows.length === 0) return;
      settle(rows); // no longer in flight — it never left
      for (const r of rows) mineRef.current.add(r.id);
      write((prev) => {
        const have = new Set(prev.map((a) => a.id));
        return [...rows.filter((r) => !have.has(r.id)), ...prev];
      });
    },
    [settle, write],
  );

  return {
    attachments,
    inFlight,
    hasPending,
    attachLocalFiles,
    attachKbDocs,
    remove,
    claim,
    restage,
    adopt,
    settle,
  };
}

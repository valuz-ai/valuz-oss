import { useCallback, useEffect, useRef, useState } from "react";

import { sessionsApi, type SessionAttachmentItem } from "../api/sessions-api";

const POLL_INTERVAL_MS = 1000;

/**
 * How long to keep waiting for a file someone told us to expect.
 *
 * Bounded because the promise can be broken: the page that sent the turn owns
 * the POST, and if it fails the bind never happens. Twenty seconds of polling
 * costs twenty reads and then stops for good, which beats either extreme —
 * giving up before a slow cloud round trip lands, or polling a conversation
 * forever over a turn that died on another page.
 */
const MAX_EXPECT_POLLS = 20;

export interface UseSessionAttachmentsResult {
  /** Every attachment this conversation has ever been sent, oldest first. */
  attachments: SessionAttachmentItem[];
  /** True while any of them is still parsing. */
  hasParsing: boolean;
  /**
   * Re-read now.
   *
   * Takes the session explicitly because the caller often knows a newer one
   * than this hook does: a send creates the session and then binds its files,
   * and the closure that runs afterwards was built when the id was still null.
   * Omitted → whatever the hook is currently pointed at.
   */
  refresh: (sessionId?: string) => Promise<void>;
  /** Detach a file from this conversation (and delete what it owns). */
  remove: (attachmentId: string) => Promise<void>;
}

/**
 * One conversation's attachment history — read-only, and only that.
 *
 * **Not the composer's set.** A file being staged for the next turn belongs to
 * the owner, not to a session: it uploads with no session and is bound by the
 * turn that ships it (``useStagedAttachments``). Serving both from one hook is
 * what made the composer's state re-key itself when its send created the
 * session, dropping the staged rows and sending a turn that claimed nothing.
 *
 * Keeping the two apart is the point. This one has no upload path, no
 * placeholders, and no notion of "pending" beyond a parse that has not
 * finished — so there is nothing here for a session change to get wrong.
 */
export function useSessionAttachments(
  sessionId: string | null,
  /**
   * Ids another page has already sent into this conversation, so this one can
   * wait for them instead of assuming its single mount-time read saw
   * everything.
   *
   * The project composer sends and navigates in that order: it claims the
   * file, posts, and hands the conversation over WITHOUT waiting for the POST
   * to return. This page's read therefore raced a bind that had not happened
   * yet, and — nothing else refreshing — the file was missing from the panel
   * until the person switched away and back.
   */
  expectIds: string[] = [],
): UseSessionAttachmentsResult {
  const [attachments, setAttachments] = useState<SessionAttachmentItem[]>([]);

  // ``null`` = could not read; keep what is on screen rather than flashing an
  // empty panel. No session is an empty list, not a failure.
  const read = useCallback(async (): Promise<
    SessionAttachmentItem[] | null
  > => {
    if (!sessionId) return [];
    try {
      return (await sessionsApi.listAttachments(sessionId)).items;
    } catch {
      return null;
    }
  }, [sessionId]);

  const load = useCallback(
    async (override?: string) => {
      const id = override ?? sessionId;
      if (!id) return;
      try {
        const { items } = await sessionsApi.listAttachments(id);
        setAttachments(items);
      } catch {
        /* keep what is on screen rather than flashing an empty panel */
      }
    },
    [sessionId],
  );

  useEffect(() => {
    let cancelled = false;
    // Through the promise on purpose: writing state synchronously inside an
    // effect cascades renders, and the no-session case would do exactly that.
    void read().then((items) => {
      if (!cancelled && items) setAttachments(items);
    });
    return () => {
      cancelled = true;
    };
  }, [read]);

  const hasParsing = attachments.some((a) => a.parse_status === "parsing");

  // Compared as a string so a caller that rebuilds the array every render —
  // all of them do — does not restart the wait on every frame.
  const have = new Set(attachments.map((a) => a.id));
  const awaiting = expectIds.filter((id) => !have.has(id)).join(",");

  // A turn can be sent mid-parse, so a bound row may still settle; and a turn
  // sent from another page may not be bound yet at all. Polls only while
  // something is outstanding, which for an ordinary conversation is never.
  const pollsRef = useRef(0);
  useEffect(() => {
    pollsRef.current = 0;
  }, [sessionId, awaiting]);
  useEffect(() => {
    if (!sessionId || (!hasParsing && !awaiting)) return;
    const handle = setInterval(() => {
      // The parse poll is self-limiting — a parse always reaches a terminal
      // status. Waiting on another page's POST is not, so it gets a deadline.
      if (
        awaiting &&
        !hasParsing &&
        (pollsRef.current += 1) > MAX_EXPECT_POLLS
      ) {
        clearInterval(handle);
        return;
      }
      void load();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(handle);
  }, [sessionId, hasParsing, awaiting, load]);

  const remove = useCallback(async (attachmentId: string) => {
    setAttachments((prev) => prev.filter((a) => a.id !== attachmentId));
    try {
      await sessionsApi.deleteAttachment(attachmentId);
    } catch {
      /* best-effort */
    }
  }, []);

  return { attachments, hasParsing, refresh: load, remove };
}

import { useCallback, useEffect, useState } from "react";

import {
  sessionsApi,
  type SessionAttachmentItem,
} from "../api/sessions-api";

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
   * True while any *pending* (not-yet-sent) attachment is still parsing.
   * Callers gate Send on this to surface the "submit anyway?" confirm.
   */
  hasParsing: boolean;
  /** Upload local files immediately; eager-creates the session if needed. */
  attachLocalFiles: (files: File[], ensureSession: EnsureSession) => Promise<void>;
  /** Attach KB documents immediately; eager-creates the session if needed. */
  attachKbDocs: (docIds: string[], ensureSession: EnsureSession) => Promise<void>;
  /** Remove one attachment (optimistic drop + server delete). */
  remove: (attachmentId: string) => Promise<void>;
  /**
   * Optimistically stamp every pending row consumed — call right after a send
   * so the staging chips clear immediately (the turn marks them consumed
   * server-side only after it runs).
   */
  markPendingConsumed: () => void;
  /** Escape hatch for callers that need to splice optimistic state directly. */
  setAttachments: React.Dispatch<React.SetStateAction<SessionAttachmentItem[]>>;
}

const POLL_INTERVAL_MS = 1000;

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

  // Merge fresh server rows into local state WITHOUT clobbering an optimistic
  // ``consumed_at`` we stamped on send. The turn marks rows consumed
  // server-side only after it finishes running, so a poll fired between send
  // and turn-completion would otherwise un-consume the just-sent chips and
  // flash them back into the composer's staging row.
  const mergeServer = useCallback((serverRows: SessionAttachmentItem[]) => {
    setAttachments((prev) => {
      const localById = new Map(prev.map((a) => [a.id, a]));
      return serverRows.map((s) => {
        const local = localById.get(s.id);
        if (local?.consumed_at && !s.consumed_at) {
          return { ...s, consumed_at: local.consumed_at };
        }
        return s;
      });
    });
  }, []);

  // Load on session change — a full replace (a fresh session has no optimistic
  // local state to preserve). State is set only inside the async resolution
  // (never synchronously in the effect body) — a null session resolves to an
  // empty list rather than an inline ``setAttachments([])``.
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
      if (!cancelled) setAttachments(items);
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const hasParsing = attachments.some(
    (a) => !a.consumed_at && a.parse_status === "parsing",
  );

  // Poll while ANY row (pending or already consumed) is still parsing, so the
  // panel history stays accurate too. Stops the moment everything settles.
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
      const session = await ensureSession();
      for (const file of files) {
        try {
          const item = await sessionsApi.uploadAttachment(session.id, file);
          setAttachments((prev) =>
            prev.some((a) => a.id === item.id) ? prev : [...prev, item],
          );
        } catch {
          /* best-effort; the caller surfaces an upload-failed toast */
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
      if (!sessionId) return;
      try {
        await sessionsApi.deleteAttachment(sessionId, attachmentId);
      } catch {
        /* best-effort */
      }
    },
    [sessionId],
  );

  const markPendingConsumed = useCallback(() => {
    const ts = Date.now();
    setAttachments((prev) =>
      prev.map((a) => (a.consumed_at ? a : { ...a, consumed_at: ts })),
    );
  }, []);

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

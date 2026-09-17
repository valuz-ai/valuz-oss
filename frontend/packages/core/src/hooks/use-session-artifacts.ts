import { useCallback, useEffect, useState } from "react";

import {
  sessionsApi,
  type SessionArtifactItem,
} from "../api/sessions-api";

export interface UseSessionArtifactsResult {
  /** The agent-delivered artifacts for the active session (oldest first). */
  artifacts: SessionArtifactItem[];
  /** Re-fetch from the server — call on turn-end so new deliveries appear. */
  refresh: () => Promise<void>;
}

/**
 * Owns a session's agent-delivered artifacts (the "产物" list).
 *
 * Unlike {@link useSessionAttachments} (user uploads, polled while parsing),
 * artifacts only change when the agent calls the ``deliver_artifacts`` tool,
 * i.e. mid/after a turn. So instead of interval polling we load on session
 * change and expose ``refresh()`` for the caller to fire on the same turn-end
 * signal that refreshes the file tree.
 */
export function useSessionArtifacts(
  sessionId: string | null,
): UseSessionArtifactsResult {
  const [artifacts, setArtifacts] = useState<SessionArtifactItem[]>([]);

  const fetchInto = useCallback(
    async (id: string | null): Promise<SessionArtifactItem[]> => {
      if (!id) return [];
      try {
        const res = await sessionsApi.listArtifacts(id);
        return res.items;
      } catch {
        return [];
      }
    },
    [],
  );

  // Load on session change. A stale in-flight load from a previous session is
  // discarded so it can't clobber the current session's list. ``fetchInto``
  // returns ``[]`` for a null session, so clearing on sign-out / no-session
  // flows through the same async path (no synchronous setState in the effect).
  useEffect(() => {
    let cancelled = false;
    void fetchInto(sessionId).then((items) => {
      if (!cancelled) setArtifacts(items);
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId, fetchInto]);

  const refresh = useCallback(async () => {
    const items = await fetchInto(sessionId);
    setArtifacts(items);
  }, [sessionId, fetchInto]);

  return { artifacts, refresh };
}

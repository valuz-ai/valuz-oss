import type {
  SessionDetail,
  SessionEventDTO,
  SessionListItem,
} from "@valuz/core";

/**
 * Sentinel ``id`` URL param used by the "fresh quick-chat" entry
 * (sidebar `+新对话`, ⌘N, home page fallback). The route is
 * ``/conversation/new``; when the user actually sends a message,
 * ``ensureSession`` mints a real session and ``navigate(replace:true)``
 * swaps the URL to ``/conversation/{real-id}``. Centralizing the
 * literal here keeps every check (``id === NEW_SESSION_ID``) in
 * lock-step with the route definition.
 */
export const NEW_SESSION_ID = "new";

export function sessionDetailToListItem(detail: SessionDetail): SessionListItem {
  return {
    id: detail.id,
    project_id: detail.project_id,
    name: detail.name,
    status: detail.status,
    origin: detail.origin,
    last_user_message_text: detail.last_user_message_text,
    locked_model_id: detail.locked_model_id,
    locked_provider_id: detail.locked_provider_id ?? null,
    runtime_provider: detail.runtime_provider,
    permission_mode: detail.permission_mode,
    effort: detail.effort ?? null,
    task_id: detail.task_id ?? null,
    // Carries ``exists`` (liveness) from the detail fetch — the header
    // worktree badge greys out on it.
    worktree: detail.worktree ?? null,
    updated_at: detail.updated_at,
  };
}

export function makeLocalUserInterruptEvent(): SessionEventDTO {
  return {
    seq: 0,
    event: {
      event_type: "session.idle",
      payload: { stop_reason: "user_interrupt" },
    },
    timestamp: Date.now(),
  };
}

export function isLocalUserInterruptEvent(event: SessionEventDTO): boolean {
  return (
    event.seq === 0 &&
    event.event.event_type === "session.idle" &&
    event.event.payload.stop_reason === "user_interrupt"
  );
}

export function appendUniqueEvents(
  current: SessionEventDTO[],
  incoming: SessionEventDTO[],
): SessionEventDTO[] {
  // Dedup keys on the store-independent ``event_uid`` when present — history
  // reads and live frames use INDEPENDENT seq spaces, so a bare seq match is
  // only trustworthy between uid-less (legacy) rows. uid-less incoming keeps
  // the historical seq-based check, but only against uid-less rows (a
  // uid-bearing row's seq may be kernel-local and collide by accident).
  const seenUids = new Set<string>();
  const seenLegacySeqs = new Set<number>();
  for (const event of current) {
    if (event.event_uid) seenUids.add(event.event_uid);
    else if (event.seq > 0) seenLegacySeqs.add(event.seq);
  }
  const fresh = incoming.filter((event) =>
    event.event_uid
      ? !seenUids.has(event.event_uid)
      : event.seq <= 0 || !seenLegacySeqs.has(event.seq),
  );
  if (fresh.length === 0) return current;
  return [...current, ...fresh];
}

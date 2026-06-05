import { useEffect, useState } from "react";
import { sessionsApi } from "../api/sessions-api";
import type { SessionEventDTO } from "@valuz/shared";

// The dashboard only renders the most recent ~5 *aggregated* lines, but the
// aggregation operates over the full buffer — so a small buffer truncates the
// per-batch counts (e.g. a ``Called harness 10 times`` batch becomes
// ``Called harness 2 times`` if 8 of the 10 calls fell out the front). Keep
// the cap generous so demo / replayed sessions show accurate totals; a long
// agent run rarely exceeds a few hundred milestone events.
const DEFAULT_MAX = 500;

// The buffer is meant for human-readable milestones (assistant_message,
// tool_call, subtask_*, plan_*, …). Drop:
//   • token-level deltas (would flood the buffer)
//   • runtime / session lifecycle plumbing (no signal for a watcher)
const SKIP_TYPES: ReadonlySet<string> = new Set([
  "text_delta",
  "thinking_delta",
  "todo_update_delta",
  "runtime.engine.usage",
  "session.idle",
  "session.update",
  "session.mode_changed",
  // ``tool.call.completed`` mirrors ``tool.call.started`` one-for-one — it
  // would double every tool entry on the dashboard without adding info.
  "tool.call.completed",
  "tool_call_completed",
]);

/**
 * Subscribe to a session's SSE event stream and return a rolling buffer of
 * the most recent events. Opens one connection per ``sessionId``; closes on
 * unmount or when ``sessionId`` changes. Pass ``null`` to disable.
 *
 * Token-level delta events (``text_delta`` etc.) are dropped — the buffer
 * surfaces milestones, not raw tokens.
 */
export function useSessionEvents(
  sessionId: string | null,
  options?: { max?: number },
): SessionEventDTO[] {
  const max = options?.max ?? DEFAULT_MAX;
  const [events, setEvents] = useState<SessionEventDTO[]>([]);

  useEffect(() => {
    setEvents([]);
    if (!sessionId) return;
    const controller = new AbortController();
    void sessionsApi
      .subscribeEvents(
        sessionId,
        (event) => {
          if (SKIP_TYPES.has(event.event.event_type)) return;
          setEvents((prev) => {
            const trimmed =
              prev.length >= max ? prev.slice(prev.length - max + 1) : prev;
            return [...trimmed, event];
          });
        },
        0,
        controller.signal,
      )
      .catch(() => undefined);
    return () => controller.abort();
  }, [sessionId, max]);

  return events;
}

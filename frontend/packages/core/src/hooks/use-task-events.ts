import { useEffect, useRef, useState } from "react";
import { tasksApi, type TaskEvent } from "../api/tasks-api";

/**
 * Subscribe to a task's event log (VALUZ-CHATPLAN S3 + S5).
 *
 * Connects to ``GET /v1/tasks/{taskId}/events/stream`` via EventSource and
 * surfaces each task event to the caller as it arrives. Events arrive
 * sequentially in append-only order, indexed by ``sequence`` — the hook
 * remembers the highest sequence it has seen so a reconnect resumes
 * exactly where it left off (the server polls the DB and emits anything
 * with ``sequence > after_seq``).
 *
 * Reconnect: ``EventSource`` reconnects natively on transient drops. When
 * it does, the hook hands the server the last seen sequence via
 * ``?after_seq=`` so no events are missed; the next read from the server
 * picks up newer rows.
 *
 * When ``taskId`` is ``null`` the hook is inert (no connection opened).
 * The hook does not currently emit error state — failures are silent and
 * EventSource retries on its own.
 */
export function useTaskEvents(
  taskId: string | null,
  onEvent: (event: TaskEvent) => void,
): { lastSeq: number } {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const [lastSeq, setLastSeq] = useState(0);
  const lastSeqRef = useRef(0);
  lastSeqRef.current = lastSeq;

  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;
    let source: EventSource | null = null;

    const open = (afterSeq: number) => {
      if (cancelled) return;
      source?.close();
      const url = tasksApi.eventsStreamUrl(taskId, afterSeq);
      source = new EventSource(url);

      // The backend uses sse-starlette with named events
      // (event:type / data:json / id:sequence). Browser EventSource
      // dispatches a separate event listener PER event name; ``message``
      // only fires for the default (unnamed) event, which the server
      // never sends. So we install a generic ``open + addEventListener``
      // for the specific task_event types we expect, plus a fallback
      // catch-all via ``onmessage`` for safety.
      const handleNamedEvent = (e: MessageEvent) => {
        try {
          const parsed = JSON.parse(e.data) as TaskEvent;
          onEventRef.current(parsed);
          const seq = parsed.sequence ?? 0;
          if (seq > lastSeqRef.current) {
            setLastSeq(seq);
            lastSeqRef.current = seq;
          }
        } catch {
          // Heartbeat / malformed — ignore.
        }
      };

      // Listed task_event types from backend models.py + valuz-chatplan S2/S4.
      const KNOWN_TYPES = [
        "kickoff",
        "kickoff_failed",
        "task_planned",
        "task_plan_update",
        "plan_revised",
        "subtask_spawned",
        "subtask_message",
        "subtask_reviewed",
        "subtask_completed",
        "subtask_failed",
        "session_error",
        "task_completed",
        "task_stopped",
        "task_blocked",
        "user_note",
        "goal_revised",
        "stopped",
        "resumed",
        "task_drafted",
        "committed",
        "abandoned",
        "user_inject",
        "user_inject_dropped",
      ];
      for (const t of KNOWN_TYPES) {
        source.addEventListener(t, handleNamedEvent as EventListener);
      }

      // Heartbeat events are no-ops (the data is empty by design); we
      // listen so the browser doesn't log "unhandled event" warnings.
      source.addEventListener("heartbeat", () => {
        /* keep-alive only */
      });

      // EventSource auto-reconnects with exponential backoff. We
      // override the reconnect to thread the latest seq cursor so no
      // events are missed across a drop.
      source.onerror = () => {
        if (cancelled) return;
        source?.close();
        // 500ms backoff before reconnect — matches the server poll cadence.
        setTimeout(() => {
          if (!cancelled) open(lastSeqRef.current);
        }, 500);
      };
    };

    open(0);

    return () => {
      cancelled = true;
      source?.close();
    };
  }, [taskId]);

  return { lastSeq };
}

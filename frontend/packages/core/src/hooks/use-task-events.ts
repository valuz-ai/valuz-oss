import { useEffect, useRef, useState } from "react";
import { fetchEventSource } from "../api/fetch-event-source";
import { tasksApi, type TaskEvent } from "../api/tasks-api";

/**
 * Subscribe to a task's event log (VALUZ-CHATPLAN S3 + S5).
 *
 * Reads ``GET /v1/tasks/{taskId}/events/stream`` as fetch-based SSE (not
 * ``EventSource``, so the request carries auth headers) and surfaces each task
 * event to the caller as it arrives. Events arrive sequentially in append-only
 * order, indexed by ``sequence`` — the hook remembers the highest sequence it
 * has seen so a reconnect resumes exactly where it left off (the server emits
 * anything with ``sequence > after_seq``).
 *
 * Reconnect: ``fetchEventSource`` reconnects on transient drops and re-reads
 * the URL each time, so the latest ``?after_seq=`` cursor is threaded and no
 * events are missed.
 *
 * When ``taskId`` is ``null`` the hook is inert (no connection opened).
 * The hook does not emit error state — failures are silent and the reader
 * retries on its own.
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
    return fetchEventSource(
      // Re-read on each (re)connect so the latest seq cursor is threaded.
      () => tasksApi.eventsStreamUrl(taskId, lastSeqRef.current),
      (frame) => {
        if (frame.event === "heartbeat") return; // keep-alive only
        try {
          const parsed = JSON.parse(frame.data) as TaskEvent;
          onEventRef.current(parsed);
          const seq = parsed.sequence ?? 0;
          if (seq > lastSeqRef.current) {
            setLastSeq(seq);
            lastSeqRef.current = seq;
          }
        } catch {
          // Malformed frame — ignore.
        }
      },
      { reconnectDelayMs: 500 }, // matches the server poll cadence
    );
  }, [taskId]);

  return { lastSeq };
}

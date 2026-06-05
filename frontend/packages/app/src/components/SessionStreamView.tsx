import { useEffect, useMemo, useRef, useState } from "react";
import {
  buildTurns,
  createSessionStreamController,
  sessionsApi,
  useStableTurns,
  type SessionEventDTO,
} from "@valuz/core";
import { ConversationTurnList } from "@valuz/ui";

export interface SessionStreamViewProps {
  sessionId: string;
  /** Tailwind height for the scroll container. */
  heightClass?: string;
  /**
   * Whether the underlying run/task is still active. When true, the view
   * auto-reconnects after the SSE stream closes on idle so a member's later
   * turns keep streaming live. When false (task done), it leaves the stream
   * closed — the hydrated history is already final.
   */
  active?: boolean;
}

/** Delay before resuming the SSE stream after it closes on idle. */
const RECONNECT_DELAY_MS = 2500;

/**
 * Read-only live view of a kernel session's event stream, reusing the
 * conversation turn renderer. Hydrates history via listEvents then opens an
 * SSE subscription for live deltas; dedupes by seq so the hydrate/live
 * boundary never double-counts. No composer — purely for observing a
 * lead/subtask run on the task page.
 *
 * The kernel closes the SSE stream when a session goes idle (e.g. a v2 member
 * between turns). While ``active`` we reconnect after a short delay so the
 * next turn's deltas resume without a manual reload.
 */
export const SessionStreamView = ({
  sessionId,
  heightClass = "h-[320px]",
  active = true,
}: SessionStreamViewProps) => {
  const [events, setEvents] = useState<SessionEventDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Mirror ``active`` into a ref so the SSE effect (keyed only on sessionId)
  // reads the latest value in its onStateChange closure without resubscribing.
  const activeRef = useRef(active);
  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  // Remounted via key={sessionId} by the parent, so state starts fresh per
  // session — no synchronous reset needed here (which would trip the
  // set-state-in-effect rule). All setState below runs in async callbacks.
  useEffect(() => {
    let cancelled = false;
    let maxSeq = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const append = (incoming: SessionEventDTO[]) => {
      if (cancelled || incoming.length === 0) return;
      setEvents((prev) => {
        const next = [...prev];
        for (const e of incoming) {
          if (e.seq > maxSeq) {
            maxSeq = e.seq;
            next.push(e);
          }
        }
        return next;
      });
    };

    const controller = createSessionStreamController({
      sessionId,
      // Resume from the latest hydrated seq once history is loaded; until
      // then onEvent still dedupes by seq so early live deltas are safe.
      startSeq: 0,
      onEvent: (e) => append([e]),
      onStateChange: (snap) => {
        // Stream closed on idle — resume while the run is still active so
        // subsequent turns keep streaming (the kernel ends the SSE per turn).
        if (snap.state === "disconnected" && activeRef.current && !cancelled) {
          if (reconnectTimer != null) return;
          reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            if (!cancelled && activeRef.current) controller.reconnect();
          }, RECONNECT_DELAY_MS);
        }
      },
    });

    void sessionsApi
      .listEvents(sessionId)
      .then((res) => {
        if (cancelled) return;
        append(res.items ?? []);
        setLoading(false);
        controller.start();
      })
      .catch(() => {
        if (cancelled) return;
        setLoading(false);
        controller.start();
      });

    return () => {
      cancelled = true;
      if (reconnectTimer != null) clearTimeout(reconnectTimer);
      controller.stop();
    };
  }, [sessionId]);

  const turns = useStableTurns(useMemo(() => buildTurns(events), [events]));

  return (
    <div
      ref={scrollRef}
      className={`${heightClass} overflow-y-auto rounded-[10px] border border-surface-border bg-card`}
    >
      <ConversationTurnList
        turns={turns}
        scrollContainerRef={scrollRef}
        sending={false}
        loading={loading}
        error={null}
      />
    </div>
  );
};
